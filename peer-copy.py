import Pyro5.api
import time
import random
import os
import threading
import sys
import base64

@Pyro5.api.expose
class Peer:
    def __init__(self, peer_id, total_peers=5):
        self.total_peers = total_peers
        self.peer_id = peer_id
        self.is_tracker = False
        self.epoca = 0
        self.current_tracker_uri = None
        self.last_heartbeat = 0
        self.election_timeout = random.uniform(0.2, 0.6)
        self.daemon = None
        self.shared_dir = f"peer_{peer_id}_shared"
        self.files = []  # List of files in the shared directory.
        self.file_registry = {}
        self.stop_threads = False
        self.setup_shared_directory()
        self.update_local_file_list()  # Ensure the file list is updated before notifying the tracker.
        self.voted_for = None
        self.received_votes = 0
        self.election_in_progress = threading.Lock() 

    def get_uri_name(self):
        return f"Peer_{self.peer_id}"

    def register_with_nameserver(self):
        if self.daemon is not None:
            with Pyro5.api.locate_ns() as ns:
                uri = self.daemon.register(self)
                ns.register(self.get_uri_name(), uri)
                print(f"Peer {self.peer_id}: Registrado no serviço de nomes como {self.get_uri_name()}")
        else:
            print(f"Peer {self.peer_id}: Erro ao registrar no Nameserver: ")

    def listar_peers(self):
        peers = []
        try:
            with Pyro5.api.locate_ns() as ns:
                return [
                    uri
                    for name, uri in ns.list().items()
                    if name.startswith("Peer_") and name != f"Peer_{self.peer_id}"
                ]
        except Pyro5.errors.NamingError as e:
            print(f"Peer {self.peer_id}: Erro ao listar peers devido a NamingError: {e}")
        except Exception as e:
            print(f"Peer {self.peer_id}: Erro ao listar peers: {e}")
        return peers

    def get_peer_uri(self):
        try:
            with Pyro5.api.locate_ns() as ns:
                uri = ns.lookup(self.get_uri_name())
                return uri
        except Pyro5.errors.NamingError:
            print(f"Peer {self.peer_id}: URI não encontrado no Nameserver.")
            return None
        except Exception as e:
            print(f"Peer {self.peer_id}: Erro ao buscar URI do próprio peer: {e}")
            return None

    def setup_shared_directory(self):
        os.makedirs(self.shared_dir, exist_ok=True)
        print(f"Peer {self.peer_id}: Created shared directory: {self.shared_dir}")

   
    def count(self):
        """Returns the number of name registrations."""
        return len(self.storage)
    
    def select_initial_tracker(self):
     if self.peer_id == 1:
      print(f"Peer {self.peer_id} é o tracker inicial")
      self.epoca = 0
      self.is_tracker = True
      self.current_tracker_uri = self.get_peer_uri()
      self.update_file_registry(self.peer_id, self.files)
      self.start_heartbeat()
     else:
         try:
              with Pyro5.api.locate_ns() as ns:
                 self.current_tracker_uri = ns.lookup("Peer_1")
                 print("Conectado ao tracker inicial")
                 self.notify_tracker_files()
         except Exception:
            print("Tracker nao encontrado, iniciando eleição")
            self.start_election(5)  # Inicia a eleição com 5 peers
    
    @Pyro5.api.expose
    def send_heartbeat(self,epoca):
        if self.current_tracker_uri:
            try:
                tracker = Pyro5.api.Proxy(self.current_tracker_uri)
                return tracker.checa_heartbeat(epoca, tracker)
            except Pyro5.errors.CommunicationError as e:
                print(f"Peer {self.peer_id}: Erro de comunicação com o tracker (URI: {self.current_tracker_uri}) durante o envio de heartbeat: {e}")
        else:
            print(f"Peer {self.peer_id}: Tracker não definido.")
        return None
    @Pyro5.api.expose
    def checa_heartbeat(self, epoca, tracker):
        if epoca > self.epoca:
            self.epoca = epoca
            # marcar que vc nao eh tracker, buscar a referencia no tracker no serv nomes, cadastrar nele os seus arquivos
            self.is_tracker = False
            tracker_uri = tracker.get_uri_name()
            self.notify_tracker_files(tracker_uri)    
           #resetar o temporizador
        self.last_heartbeat = time.time()
        if(time.time() - self.last_heartbeat > self.election_timeout):
            print(f"Peer {self.peer_id}: Timeout de eleição atingido, iniciando nova eleição.")
            self.iniciar_eleicao()
            self.election_timeout = random.uniform(0.2, 0.6)
        else:
            return ("Heartbeat recebido com sucesso.", self.epoca)
        
    @Pyro5.api.expose
    def iniciar_eleicao(self, total_peers):
        self.epoca += 1
        print(f"Peer {self.peer_id}: Iniciando eleição na época {self.epoca}.")
        self.received_votes = 1  
        self.voted_for = self.peer_id
        peers = self.listar_peers()
        if not peers:
            print(f"Peer {self.peer_id}: Nenhum peer disponível para participar da eleição.")
            return 
        for peer_uri in peers:
                peer = Pyro5.api.Proxy(peer_uri)
                if peer.request_vote(self.peer_id):
                    self.received_votes += 1
                    print(f"Peer {self.peer_id}: Voto recebido do peer {peer_uri}.")
             
        if( self.received_votes > total_peers // 2):
            self.become_tracker()
            time.sleep(0.3)
            self.send_heartbeat(self.epoca)
            print(f"Peer {self.peer_id}: Eleição concluída, agora é o tracker.")
            
        if (self.received_votes < total_peers):
            total_peers = self.received_votes
            
    @Pyro5.api.expose
    def request_vote(self, peer_id):
        if self.voted_for is None or self.voted_for == peer_id:
            self.voted_for = peer_id
            print(f"Peer {self.peer_id}: Votou no peer {peer_id}.")
            return True
        else:
            print(f"Peer {self.peer_id}: Já votou em outro peer ({self.voted_for}).")
            return False 
    @Pyro5.api.expose
    def become_tracker(self):
        self.is_tracker = True
        with Pyro5.api.locate_ns() as ns:
            try:
                ns.remove(self.get_uri_name())
            except Exception:
                pass
        
        tracker_name = f"Tracker_Epoca_{self.epoca}"
        uri = self.daemon.register(self)
        with Pyro5.api.locate_ns() as ns:
            ns.register(tracker_name, uri)
        
        self.current_tracker_uri = uri
        if self.current_tracker_uri is None:
            print(f"Peer {self.peer_id}: Falha ao se tornar tracker, URI não encontrada.")
            self.is_tracker = False
            return
            
        print(f"Peer {self.peer_id}: Agora é o tracker. URI: {self.current_tracker_uri}")
        self.notify_tracker_files()
        self.send_heartbeat(self.epoca)
   
   
    @Pyro5.api.expose
    def local_tracker_files(self):
  
      if self.is_tracker:
        self.update_file_registry(self.peer_id, self.files)  # Notify the tracker with the updated file list.
        return True
      try:
        self.update_local_file_list()
        # Notifica o tracker atual
        tracker = Pyro5.api.Proxy(self.current_tracker_uri)
        try:
            tracker._pyroTimeout = 1
        except AttributeError:
            print(f"Peer {self.peer_id}: Tracker proxy does not support '_pyroTimeout' attribute.")
        tracker.update_file_registry(self.peer_id, self.files)
        print(f"Peer {self.peer_id}: Arquivos notificados ao tracker com sucesso")
        return True
      except Exception as e:
        print(f"Peer {self.peer_id}: Erro ao notificar arquivos ao tracker: {e}")
        return False
    
    @Pyro5.api.expose
    def notify_tracker_files(self):
        if not self.is_tracker:
                with Pyro5.api.locate_ns() as ns:
                    tracker = ns.lookup(f"Tracker_Epoca_{self.epoca}")
                    self.current_tracker_uri = tracker
                    print(f"Peer {self.peer_id}: Tracker encontrado: {self.current_tracker_uri}")
                
                return tracker.update_file_registry(self.peer_id, self.files)
        
    @Pyro5.api.expose
    def update_file_registry(self, peer_id=None, files=None):
        if peer_id and files:
            self.update_registry(peer_id, files)
        else:
            self.update_local_file_list()
            if self.is_tracker:
                self.update_registry(self.peer_id, self.files)
            elif self.current_tracker_uri:
                self.notify_tracker_files()
        return self.files

    def update_local_file_list(self):
        self.files = [f for f in os.listdir(self.shared_dir)
                      if os.path.isfile(os.path.join(self.shared_dir, f))]
        print(f"Peer {self.peer_id}: Lista de arquivos local atualizada.")

    def update_registry(self, peer_id, files):
        self.file_registry[peer_id] = files
        print(f"Peer {self.peer_id}: Registro de arquivos atualizado para o peer {peer_id}.")
        try:
            with Pyro5.api.Proxy(self.current_tracker_uri) as tracker:
                tracker.update_file_registry(self.peer_id, self.files)
                print(f"Peer {self.peer_id}: Arquivos notificados ao tracker com sucesso.")
        except Exception as e:
            print(f"Peer {self.peer_id}: Erro ao notificar arquivos ao tracker: {e}")
        return self.files
    
    @Pyro5.api.expose
    def search_file(self, filename):
        if self.is_tracker:
            if not filename:
                return self.file_registry
            return self.file_registry.get(filename, [])

        if not self.current_tracker_uri:
            print(f"Peer {self.peer_id}: Não há tracker conhecido para procurar arquivo. Tentando localizar.")
            try:
                with Pyro5.api.locate_ns() as ns:
                    tracker_candidates = [
                        (name, uri) for name, uri in ns.list().items()
                        if name.startswith("Tracker_Epoca_")
                    ]
                    best_tracker_uri = None
                    best_epoca = -1
                    for name, uri in tracker_candidates:
                        tracker_epoca_str = name.split('_')[-1]
                        try:
                            tracker_epoca = int(tracker_epoca_str)
                            if tracker_epoca > best_epoca:
                                best_epoca = tracker_epoca
                                best_tracker_uri = uri
                        except ValueError:
                            continue
                    if best_tracker_uri:
                        self.current_tracker_uri = best_tracker_uri
                        self.epoca = best_epoca
                        print(f"Peer {self.peer_id}: Tracker {best_tracker_uri} encontrado para busca.")
                    else:
                        print(f"Peer {self.peer_id}: Nenhum tracker ativo encontrado no Nameserver para procurar arquivo.")
                        return []
            except Exception as e:
                print(f"Peer {self.peer_id}: Erro ao localizar tracker para busca: {e}")
                return []
        try:
            tracker = Pyro5.api.Proxy(self.current_tracker_uri)
            tracker._pyroTimeout = 0.5
            return tracker.search_file(filename)
        except Exception as e:
            print(f"Peer {self.peer_id}: Falha ao procurar arquivo no tracker ({self.current_tracker_uri}): {e}")
            return []

    @Pyro5.api.expose
    def download_file(self, filename, source_peer_id):
     try:
        with Pyro5.api.locate_ns() as ns:
            source_uri = ns.lookup(f"Peer_{source_peer_id}")
            source_peer = Pyro5.api.Proxy(source_uri)
            source_peer._pyroTimeout = 2.0
            encoded_content = source_peer.send_file(filename)
            
            if encoded_content:
                file_content = base64.b64decode(encoded_content)
                filepath = os.path.join(self.shared_dir, filename)
                with open(filepath, 'wb') as f:
                    f.write(file_content)
                self.update_local_file_list()
                print(f"Arquivo {filename} baixado com sucesso do peer {source_peer_id}")
                return True
            else:
                print(f"Conteúdo do arquivo está vazio ou nulo para {filename}")
                return False
     except Exception as e:
        print(f"Falha ao baixar arquivo: {e}")
        return False

    @Pyro5.api.expose
    def send_file(self, filename):
        try:
            filepath = os.path.join(self.shared_dir, filename)
            if not os.path.exists(filepath):
                print(f"Arquivo não encontrado: {filepath}")
                return None
            with open(filepath, 'rb') as f:
                content = f.read()
                encoded_content = base64.b64encode(content)
                return encoded_content
        except Exception as e:
            print(f"Falha ao enviar arquivo: {e}")
            return None
def main():
    if len(sys.argv) < 2:
        print("Uso: python peer.py <peer_id>")
        print("Exemplo: python peer.py 1")
        sys.exit(1)

    peer_id = int(sys.argv[1])
    if peer_id < 1 or peer_id > 5:
        print("Peer ID inválido. Deve ser entre 1 e 5.")
        sys.exit(1)

    peer = Peer(peer_id)
    daemon = Pyro5.api.Daemon()
    peer.daemon = daemon

    peer.register_with_nameserver()


    daemon_thread = threading.Thread(target=daemon.requestLoop, daemon=True)
    daemon_thread.start()
    time.sleep(random.uniform(0.1, 0.3))
    
  

  
    threading.Thread(target=lambda: peer.checa_heartbeat(peer.epoca), daemon=True).start()
    
    print(f"Peer {peer_id} iniciado e aguardando requisições...")
    
    time.sleep(2)  
    if not peer.is_tracker:
        peer.notify_tracker_files()

    try:
        while True:
            print("\n=== Menu Peer {} ===".format(peer_id))
            print("1. Listar arquivos na rede")
            print("2. Procurar e Baixar arquivo")
            print("3. Sair")
            
            escolha = input("\nEscolha uma opção: ")

            if escolha == "1":
                if peer.is_tracker:
                    print("\nRegistro de arquivos do Tracker:")
                    for filename, peers in peer.file_registry.items():
                        print(f"- {filename} em Peer(s): {peers}")
                else:
                    try:
                        registry = peer.search_file("")
                        if registry:
                            print("\nRegistro de arquivos na rede:")
                            for filename, peers in registry.items():
                                print(f"- {filename} em Peer(s): {peers}")
                        else:
                            print("Nenhum arquivo registrado.")
                    except Exception as e:
                        print(f"Erro ao acessar registro: {e}")

            elif escolha == "2":
                filename = input("Digite o nome do arquivo: ")
                peers_with_file = peer.search_file(filename)
                if peers_with_file:
                    print(f"Arquivo encontrado nos peers: {peers_with_file}")
                    source_peer = int(input("Digite o ID do peer de onde deseja fazer o download: "))
                    if source_peer in peers_with_file:
                        if peer.download_file(filename, source_peer):
                            print("✅ Download concluído!")
                            peer.notify_tracker_files()
                        else:
                            print("❌ Falha no download")
                    else:
                        print("❌ Peer ID inválido")
                else:
                    print("Arquivo não encontrado na rede")

            elif escolha == "3":
                print("Encerrando...")
                break

    except KeyboardInterrupt:
        print("\nEncerrando peer...")
    finally:
        peer.stop_threads = True
        daemon.shutdown()
        print("Peer encerrado.")

if __name__ == "__main__":
    main()
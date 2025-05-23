import Pyro5.api
import time
import random
import os
import threading


@Pyro5.api.expose
class Peer:
    def __init__(self, peer_id, total_peers=5):
        self.total_peers = total_peers
        self.peer_id = peer_id
        self.is_tracker = False
        self.epoca = 0
        self.current_tracker_uri = None
        self.last_heartbeat = 0
        self.election_timeout = random.uniform(0.15,0.3)
        self.daemon = None
        self.shared_dir = f"peer_{peer_id}_shared"
        self.files = []
        self.file_registry = {}
        self.stop_threads = False
        self.setup_shared_directory()
        self.update_file_list()
        self.voted_for = None  
        self.received_votes = 0 
        self.election_in_progress = threading.Lock()
        
    def get_uri_name(self):
        return f"Peer_{self.peer_id}"

    def register_with_nameserver(self):
        with Pyro5.api.locate_ns() as ns:
            uri = self.daemon.register(self)
            ns.register(self.get_uri_name(), uri)
            print(f"Registrado no serviço de nomes como {self.get_uri_name()}")

    def listar_peers(self):
            with Pyro5.api.locate_ns() as ns:
                return [
                    uri
                    for name, uri in ns.list().items()
                    if name.startswith("Peer_") and name != f"Peer_{self.peer_id}"
                ]

    def registered(self):
        """returns a list of all object names registered in this daemon"""
        return list(self.daemon.objectsById.keys())
 
    def get_peer_uri(self):
        with Pyro5.api.locate_ns() as ns:
            uri = ns.lookup(self.get_uri_name())
            return uri

    def start_peer(self):
        self.daemon = Pyro5.api.Daemon() 
        self.register_with_nameserver()
        time.sleep(1)
        # self.select_initial_tracker()  # Commented out as select_initial_tracker is not defined
        threading.Thread(target=self.monitora_tracker_heartbeat, daemon=True).start()
        print(f"Peer {self.peer_id} iniciado e aguardando requisições...")
        
    def setup_shared_directory(self):
        #Cria o diretório compartilhado para o peer, na primeira vez que é iniciado
        if not os.path.exists(self.shared_dir):
            os.makedirs(self.shared_dir)
            print(f"Created shared directory: {self.shared_dir}")

    def update_file_list(self):
        self.files = [f for f in os.listdir(self.shared_dir) 
                     if os.path.isfile(os.path.join(self.shared_dir, f))]
        if self.is_tracker:
            # Se for o tracker, atualiza o próprio registro com seus arquivos
            self.update_file_registry(self.peer_id, self.files) 
        elif self.current_tracker_uri:
            # Se não for o tracker, notifica o tracker atual
            self.notify_tracker_files()
        return self.files
    
    def start_heartbeat(self):
        def heartbeat_loop():
            while self.is_tracker and not self.stop_threads:
                self.send_heartbeat()
                time.sleep(0.1)

        threading.Thread(target=heartbeat_loop, daemon=True).start()


    @Pyro5.api.expose
    def send_heartbeat(self):
      if not self.is_tracker:
        return
      with Pyro5.api.locate_ns() as ns:
        peers = self.listar_peers()
        for peer_uri in peers:
            try:
                peer = Pyro5.api.Proxy(peer_uri)
                peer._pyroTimeout = 0.1
                peer.receive_heartbeat(self.epoca)
            except Exception:
                pass

    @Pyro5.api.expose
    def receive_heartbeat(self, epoca):

        # Se receber um heartbeat de uma época mais recente, atualiza sua própria época.
        # Se for a mesma época, apenas atualiza last_heartbeat para indicar que o tracker está vivo.
        if epoca >= self.epoca:
            self.epoca = epoca
            # Se for um peer e não um tracker, e a época do heartbeat é mais recente ou igual,
            # significa que um novo tracker foi eleito ou o tracker existente está ativo
            if not self.is_tracker:
                try:
                        with Pyro5.api.locate_ns() as ns:
                        # Busca por qualquer tracker registrado com epoca igual ou maior
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
                                if tracker_epoca >= best_epoca: # Pega o mais recente ou igual
                                    best_epoca = tracker_epoca
                                    best_tracker_uri = uri
                            except ValueError:
                                continue 

                        if best_tracker_uri and best_tracker_uri != self.current_tracker_uri:
                            self.current_tracker_uri = best_tracker_uri
                            print(f"Peer {self.peer_id}: Conectado ao novo tracker com época {best_epoca}.")
                            self.notify_tracker_files() 
                except Pyro5.errors.NamingError:
                    pass 
                except Exception as e:
                    print(f"Peer {self.peer_id}: Erro ao localizar novo tracker no receive_heartbeat: {e}")

        self.last_heartbeat = time.time()
        return True

    def monitora_tracker_heartbeat(self):
        #monitora o heartbeat do tracker e caso dê timeout, inicia uma eleição
        while not self.stop_threads:
            time.sleep(self.election_timeout)

            if not self.is_tracker:
                tracker_is_alive = False
                try:
                    # Tenta acessar o tracker atual
                    tracker = Pyro5.api.Proxy(self.current_tracker_uri)
                    tracker._pyroTimeout = 0.1
                    tracker.get_uri_name() 
                    tracker_is_alive = True
                except Exception:
                    pass

                if not tracker_is_alive:
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
                                self.last_heartbeat = time.time() 
                                tracker_is_alive = True
                                print(f"Peer {self.peer_id}: Encontrou tracker ativo {best_tracker_uri} com época {best_epoca} no Nameserver.")
                                self.notify_tracker_files() 

                    except Exception as e:
                        print(f"Peer {self.peer_id}: Erro ao procurar tracker no Nameserver: {e}")

                if not tracker_is_alive or (time.time() - self.last_heartbeat > self.election_timeout):
                    if self.election_in_progress.acquire(blocking=False): 
                        try:
                            print(f"Peer {self.peer_id}: Tracker timeout ou não encontrado. Iniciando eleição.")
                            self.election_timeout = random.uniform(0.15, 0.3) 
                            self.start_election(self.total_peers)
                        finally:
                            self.election_in_progress.release()
                    
def start_election(self, total_peers):
    self.epoca += 1
    self.voted_for = self.peer_id
    self.received_votes = 1
    peers_uris = self.listar_peers()
    num_responsive_peers = 1 
    
    if not peers_uris:
        print(f"Peer {self.peer_id} é o único peer ativo. Tornando-se tracker.")
        self.become_tracker()
        return
    
    for peer_uri in peers_uris:
        try:
            peer_proxy = Pyro5.api.Proxy(peer_uri)
            peer_proxy._pyroTimeout = 0.1 # Timeout curto para a requisição de voto
            if peer_proxy.request_vote(self.epoca, self.peer_id):
                self.received_votes += 1
            num_responsive_peers += 1
        except Exception:
            pass
    time.sleep(0.1)  
    if self.received_votes > total_peers / 2:
        self.become_tracker()
        print(f"Peer {self.peer_id} se tornou o tracker")
        print("epoca atual:", self.epoca)
    time.sleep(0.05) 
    
    print(f"Peer {self.peer_id} recebeu {self.received_votes} votos de {total_peers} peers")

@Pyro5.api.expose
def request_vote(self, epoca, candidate_id):
    if epoca > self.epoca or self.voted_for is None:
        self.epoca = epoca
        self.voted_for = candidate_id
        return True
    return False

def become_tracker(self):
    try:
        self.is_tracker = True
        self.current_tracker_uri = self.get_peer_uri()
        self.last_heartbeat = time.time()

        with Pyro5.api.locate_ns() as ns:
            for name, _ in list(ns.list().items()):
                if name.startswith("Tracker_Epoca_"):
                    try:
                        ns.remove(name)
                    except Pyro5.errors.NamingError:
                        pass
            ns.register(f"Tracker_Epoca_{self.epoca}", self.current_tracker_uri)
            print(f"Peer {self.peer_id} tornou-se tracker")

        self.file_registry = {}
        self.update_file_registry(self.peer_id, self.files)

        all_peers_uris = [self.current_tracker_uri] + self.listar_peers()
        for peer_uri in all_peers_uris:
            try:
                peer_proxy = Pyro5.api.Proxy(peer_uri)
                peer_proxy._pyroTimeout = 0.5
                if hasattr(peer_proxy, "notify_tracker_files"):
                    peer_proxy.notify_tracker_files()
            except Exception as e:
                print(f"Peer {self.peer_id}: Erro ao notificar peer {peer_uri} para atualizar arquivos: {e}")

        self.start_heartbeat()

    except Exception as e:
        print(f"Peer {self.peer_id}: Oops! Erro ao se tornar tracker: {e}")
        self.is_tracker = False
        self.current_tracker_uri = None

def stop(self):
    self.stop_threads = True
    if self.is_tracker:
        self.is_tracker = False

@Pyro5.api.expose
def notify_tracker_files(self):
    if self.is_tracker:
        return
    try:
        tracker = Pyro5.api.Proxy(self.current_tracker_uri)
        tracker.update_file_registry(self.peer_id, self.files)
    except Exception as e:
        print(f"Failed to notify tracker: {e}")

@Pyro5.api.expose
def update_file_registry(self, peer_id, files):
    #atualiza o registro de arquivos do tracker, se necessário
    if not self.is_tracker:
        return
    for file in files:
        if file not in self.file_registry:
            self.file_registry[file] = []
        if peer_id not in self.file_registry[file]:
            self.file_registry[file].append(peer_id)
    print(f"Registro de arquivos atualizado: {self.file_registry}")

@Pyro5.api.expose
def search_file(self, filename):
    if self.is_tracker:
        if not filename:
            return self.file_registry 
        return self.file_registry.get(filename, [])
    try:
        tracker = Pyro5.api.Proxy(self.current_tracker_uri)
        return tracker.search_file(filename)
    except Exception as e:
        print(f"Falha ao procurar arquivo: {e}")
        return []

@Pyro5.api.expose
def download_file(self, filename, source_peer_id):
    try:
        with Pyro5.api.locate_ns() as ns:
            source_uri = ns.lookup(f"Peer_{source_peer_id}")
            source_peer = Pyro5.api.Proxy(source_uri)
            file_content = source_peer.send_file(filename)
            if file_content:
                filepath = os.path.join(self.shared_dir, filename) 
                with open(filepath, 'wb') as f:
                    f.write(file_content)
                self.update_file_list()
                print(f"Arquivo {filename} baixado com sucesso do peer {source_peer_id}")  
                self.notify_tracker_files()  
                return True
            else:
                print(f"Conteúdo do arquivo está vazio ou nulo para {filename} do peer {source_peer_id}")  # Log
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
            return f.read()
    except Exception as e:
        print(f"Falha ao enviar arquivo: {e}")
        return None

def main():
    peer_id = int(input(" Digite o peer ID (1-5): "))
    if peer_id < 1 or peer_id > 5:
        print("Peer ID inválido. Deve ser entre 1 e 5.")
        return
    
    
    peer = Peer(peer_id)
    daemon = Pyro5.api.Daemon()
    peer.daemon = daemon 
    
    peer.register_with_nameserver() 
    threading.Thread(target=peer.monitora_tracker_heartbeat, daemon=True).start()
    
    print(f"Peer {peer_id} iniciado e aguardando requisições...")

    daemon_thread = threading.Thread(target=daemon.requestLoop, daemon=True)
    daemon_thread.start()
    # Aguarda um momento para a eleição inicial acontecer
    time.sleep(2) 
    if not peer.is_tracker:
        peer.notify_tracker_files()
    def menu_loop():
        def show_menu():
            print("\n1. Listar arquivos locais")
            print("2. Procurar e Baixar arquivo")
            print("3. Sair")
        
        while True:
            show_menu()
            escolha = input("Escolha uma opção: ")
            
            if escolha == "1":
                if peer.is_tracker:
                    print("\nRegistro de arquivos do Tracker:")
                    for filename, peers in peer.file_registry.items():
                        print(f"- {filename} em Peer(s): {peers}")
                else:
                    try:
                        tracker = Pyro5.api.Proxy(peer.current_tracker_uri)
                        registry = tracker.file_registry
                        print("\nRegistro de arquivos na rede (do Tracker):")
                        for filename, peers in registry.items():
                            print(f"- {filename} em Peer(s): {peers}")
                    except Exception as e:
                        print("\nNão foi possível acessar o registro do tracker:", e)
            elif escolha == "2":
                filename = input("Digite o nome do arquivo: ")
                peers_with_file = peer.search_file(filename)
                if peers_with_file:
                    print(f"Arquivo encontrado nos peers: {peers_with_file}")
                    source_peer = int(input("Digite o ID do peer de onde deseja fazer o download: "))
                    if source_peer in peers_with_file:
                        if peer.download_file(filename, source_peer):
                            print("Download concluído com sucesso!")
                            peer.notify_tracker_files()
                        else:
                            print("Falha no download")
                    else:
                        print("Peer ID inválido")
                else:
                    print("Arquivo não encontrado na rede")
            elif escolha == "3":
                peer.stop()
                break

    menu_loop()
    print("Encerrando peer...")
    daemon.shutdown()
    
if __name__ == "__main__":
    main()
    main()

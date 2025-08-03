import sys
import os
import Pyro5.api
import random
import threading
import time
import base64

class ElectionManager:
    def __init__(self, peer):    
        self.peer = peer
        self.election_in_progress = False
        self.am_candidate = False
        self.votes_received = set()
        self.epoca = 0
        self.voted_in_epoch = -1 
        self.election_lock = threading.Lock()
        
# Em teste-final.py, na classe ElectionManager

    def inicia_election(self, epoca):
        """
        Inicia o processo de eleição do tracker de forma estável.
        """
        # Usar um lock garante que apenas uma eleição seja iniciada por vez neste peer.
        with self.election_lock:
            if self.election_in_progress:
                return
            # Incrementa a época do gerenciador de forma atômica.
            self.epoca += 1 
            print(f"Peer {self.peer.peer_id} iniciando eleição para a ÉPOCA {self.epoca}.")
            
            self.election_in_progress = True
            self.am_candidate = True
            
            # Garante que o peer vote em si mesmo para a nova época correta.
            self.voted_in_epoch = self.epoca
            self.votes_received = {self.peer.peer_id}
        
        # O resto do método continua a partir daqui...
        # Precisa da maioria dos votos (quórum) para ser eleito 
        active_peers = self.peer.listar_peers_ativos()
        votes_needed = (len(active_peers) + 1) // 2 + 1

        # Solicita votos de outros peers
        for name, uri in active_peers:
            try:
                with Pyro5.api.Proxy(uri) as new_remote_peer:
                    # Envia o pedido de voto com a NOVA época.
                    voted = new_remote_peer.request_vote(self.peer.peer_id, self.epoca)
                    if voted:
                        peer_id_str = name.split('_')[-1]
                        self.votes_received.add(peer_id_str)
            except Exception as e:
                print(f"Peer {self.peer.peer_id}: Falha ao pedir voto para {name}: {e}")
        
        # Verifica se recebeu votos suficientes
        with self.peer.lock:
            if len(self.votes_received) >= votes_needed:
                print(f"Peer {self.peer.peer_id} ganhou a eleição para a época {self.epoca} com votos de: {self.votes_received}")
                self.peer.become_tracker()
            else:
                print(f"Peer {self.peer.peer_id} perdeu a eleição para a época {self.epoca}. Votos recebidos: {self.votes_received}")
            
            self.election_in_progress = False
            self.am_candidate = False
            
    @Pyro5.api.expose        
    def request_vote(self, candidate_id, epoca):
        """
        Recebe um pedido de voto de outro peer, com lógica de defesa do tracker.
        """
        with self.peer.heartbeat_lock: # Usando o lock para garantir consistência
            if epoca > self.epoca:
                self.epoca = epoca
                self.voted_in_epoch = -1 # Reseta o voto para a nova época.
            if epoca == self.epoca and self.voted_in_epoch < epoca:
                self.voted_in_epoch = epoca # Marca que já votou nesta época.
                print(f"Peer {self.peer.peer_id}: Votou em {candidate_id} para a época {epoca}.")
                return True
            
            print(f"Peer {self.peer.peer_id}: Voto negado para {candidate_id} (época: {epoca}, já votou ou época inválida).")
            return False
        
        print(f"Peer {self.peer.peer_id}: Voto negado para {candidate_id} (época: {epoca}, já votou na época: {self.voted_in_epoch}).")
        return False    
    @Pyro5.api.expose
    def set_epoca(self, epoca):
        """
        Define a época atual do ElectionManager.
        """
        self.epoca = epoca
        print(f"Peer {self.peer.peer_id}: Época atual definida para {self.epoca}.")
class Peer:
    def __init__(self, peer_id):
        self.daemon = Pyro5.api.Daemon()
        self.peer_id = peer_id
        self.other_peers = {}
        self.election_manager = ElectionManager(self)
        self.file_registry = {}
        self.files = []
        self.shared_dir = f"peer_{self.peer_id}_shared"
        self.configurar_diretorio_compartilhado()
        self.is_tracker = False
        self.last_heartbeat = time.time()
        self.heartbeat_timeout = random.uniform(0.8, 1.5)
        self.heartbeat_lock = threading.Lock()
        self.lock = threading.Lock()
        self.stop_threads = False
        self.current_tracker_uri = None
        self.epoca = 0
    @Pyro5.api.expose
    def configurar_diretorio_compartilhado(self):
        os.makedirs(self.shared_dir, exist_ok=True)
        print(f"Peer {self.peer_id}: Created shared directory: {self.shared_dir}")  
         
    def registrar_no_servico_nomes(self):
     try:
        with Pyro5.api.locate_ns() as ns:
            try:
                ns.remove(self.get_uri_name())
            except Exception:
                pass
            if not hasattr(self, '_pyroId'):
                uri = self.daemon.register(self)
            else:
                uri = self.daemon.uriFor(self)
                
            ns.register(self.get_uri_name(), uri)
            print(f"Peer {self.peer_id}: Registrado no serviço de nomes como {self.get_uri_name()}")
            return True
     except Exception as e:
        print(f"Peer {self.peer_id}: Erro ao registrar no Nameserver: {e}")
        return False
    
    def get_uri_name(self):
        """
        Retorna o nome do URI para este peer.
        """
        return f"Peer_{self.peer_id}"
    
    def listar_peers_ativos(self):
        peers = []
        try:
            with Pyro5.api.locate_ns() as ns:
                for name, uri in ns.list(prefix="Peer_").items():
                    if name != self.get_uri_name():
                        peers.append((name, uri))
        except Exception:
            pass
        return peers
    
    def atualizar_lista_arquivos_local(self):
        self.files = os.listdir(self.shared_dir)
    #funcoes do tracker 

    def become_tracker(self):
        """
        Torna este peer o tracker e limpa registros antigos.
        """
        try:
           with Pyro5.api.locate_ns() as ns:
            self.is_tracker = True
            self.file_registry = {}
            self.epoca = self.election_manager.epoca 
            tracker_name = f"Tracker_Epoca_{self.epoca}" 
            
            if not hasattr(self, '_pyroId'):
             uri = self.daemon.register(self)
            else:
             uri = self.daemon.uriFor(self)
             
            with Pyro5.api.locate_ns() as ns:
              # --- LÓGICA DE LIMPEZA ---
              # Remove registros de trackers de épocas anteriores.
              for name, u in ns.list(prefix="Tracker_Epoca_").items():
                  try:
                      old_epoca = int(name.split('_')[-1])
                      if old_epoca < self.epoca:
                          ns.remove(name)
                  except Exception:
                      pass
              
              ns.register(tracker_name, uri)
              self.current_tracker_uri = uri

            print(f"Peer {self.peer_id}: AGORA SOU O TRACKER para a Época {self.epoca}.")
            self.file_registry[self.peer_id] = self.files.copy() 
            
            threading.Thread(target=self.loop_heartbeat, daemon=True).start()
            threading.Thread(target=self.solicitar_todos_arquivos, daemon=True).start()
        except Exception as e:
            print(f"Peer {self.peer_id}: Erro ao se tornar tracker: {e}")
            self.is_tracker = False
            
    def loop_heartbeat(self):
        while self.is_tracker:
            try:
                active_peers = self.listar_peers_ativos()
                for peer_uri in active_peers:
                    try:
                        peer = Pyro5.api.Proxy(peer_uri)
                        peer._pyroTimeout = 0.5
                        peer.receber_heartbeat(self.epoca,self.peer_id)
                    except Exception:
                        pass                
                time.sleep(0.1) 
            except Exception as e:
                print(f"Tracker {self.peer_id}: Erro no loop de heartbeat: {e}")
                break  
    @Pyro5.api.expose
    def solicitar_todos_arquivos(self):
        if not self.is_tracker:
            return

        print(f"Tracker (Peer {self.peer_id}): Puxando listas de arquivos dos outros peers...")
        time.sleep(1) 
        
        active_peers = self.listar_peers_ativos()
        for peer_name, peer_uri in active_peers:
            try:
                with Pyro5.api.Proxy(peer_uri) as remote_peer:
                    peer_id_str = peer_name.split('_')[-1]
                    peer_id = int(peer_id_str)
                    files = remote_peer.get_lista_arquivos() 
                    # O Tracker atualiza seu próprio registro com os dados recebidos
                    self.atualizar_registro_arquivos(peer_id, files)
            except Exception as e:
                print(f"Tracker (Peer {self.peer_id}): Falha ao solicitar arquivos de {peer_name}. Erro: {e}")
                
    @Pyro5.api.expose        
    def iniciar_eleicao(self):
        self.election_manager.inicia_election(self.epoca)
        return self.is_tracker      
    @Pyro5.api.expose
    def request_vote(self, candidate_id, epoca):
     return self.election_manager.request_vote(candidate_id, epoca)
 
    @Pyro5.api.expose
    def notificar_novo_tracker(self, tracker_uri, epoca):
        """
        Recebe a notificação de um novo tracker eleito e se registra com ele.
        """
        if epoca >= self.epoca:
            print(f"Peer {self.peer_id}: Notificado sobre o novo tracker (Época {epoca}). Registrando arquivos.")
            self.epoca = epoca
            self.election_manager.set_epoca(epoca)
            self.current_tracker_uri = tracker_uri
            self.is_tracker = False
            self.last_heartbeat = time.time() # Reseta o timer de timeout!
            self.notificar_arquivos_tracker() 
                
    @Pyro5.api.expose
    def atualizar_registro_arquivos(self, peer_id, files):
        if self.is_tracker:
            self.file_registry[peer_id] = files
            return True
        return 
    @Pyro5.api.expose
    def notificar_arquivos_tracker(self):
        try:
         if self.current_tracker_uri:
            tracker = Pyro5.api.Proxy(self.current_tracker_uri)
            tracker._pyroTimeout = 2.0
            tracker.atualizar_registro_arquivos(self.peer_id, self.files)
            self.last_heartbeat = time.time()
            print(f"Peer {self.peer_id}: Arquivos registrados com o tracker.")
        except Exception as e:
         print(f"Peer {self.peer_id}: Falha ao registrar arquivos com o tracker: {e}")

    def buscar_tracker(self):
        """
        Busca o tracker com a ÉPOCA MAIS ALTA no serviço de nomes para garantir
        que sempre siga o líder mais recente e legítimo.
        """
        try:
            with Pyro5.api.locate_ns() as ns:

                all_trackers = {name: uri for name, uri in ns.list(prefix="Tracker_Epoca_").items()}
            
                if not all_trackers:
                    return False

                latest_epoca = -1
                latest_tracker_uri = None
            
                for name, uri in all_trackers.items():
                    try:
                        epoca = int(name.split('_')[-1])
                        if epoca > latest_epoca:
                            latest_epoca = epoca
                            latest_tracker_uri = str(uri)
                    except (ValueError, IndexError):
                        continue
                
                if latest_tracker_uri and latest_epoca >= self.epoca:
                    self.current_tracker_uri = latest_tracker_uri
                    # Sincroniza a época no Peer e no seu ElectionManager
                    self.epoca = latest_epoca
                    self.election_manager.set_epoca(latest_epoca)
                    print(f"Peer {self.peer_id}: Tracker MAIS RECENTE encontrado: Tracker_Epoca_{latest_epoca}")
                    self.last_heartbeat = time.time() # Reinicia o temporizador!
                    return True
        except Exception as e:
            print(f"Peer {self.peer_id}: Erro ao buscar tracker: {e}")
        return False
    
        """recebe heartbeat do tracker"""
    @Pyro5.api.expose
    def receber_heartbeat(self, epoca, tracker_uri):
        with self.heartbeat_lock:
            if epoca >= self.epoca:
                self.last_heartbeat = time.time()
                self.epoca = epoca
                self.current_tracker_uri = tracker_uri
                print(f"Peer {self.peer_id}: Heartbeat recebido do tracker (Época {epoca})")
                return True
            return False
    @Pyro5.api.expose
    def monitorar_tracker(self):
        while not self.stop_threads:
            try:
                if not self.is_tracker and self.current_tracker_uri:
                    current_time = time.time()
                    tempo_desde_ultimo_heartbeat = current_time - self.last_heartbeat
                    print(f"Peer {self.peer_id}: Tempo desde último heartbeat: {tempo_desde_ultimo_heartbeat:.3f}s")

                    if tempo_desde_ultimo_heartbeat > self.heartbeat_timeout:
                        print(f"Peer {self.peer_id}: Timeout de eleição atingido (> {self.heartbeat_timeout:.3f}s). Iniciando nova eleição.")
                        if self.iniciar_eleicao():
                            print(f"Peer {self.peer_id}: Venci a eleição e me tornei o tracker. Saindo do modo de monitoramento.")
                            break
                        else:
                            print(f"Peer {self.peer_id}: Eleição não vencida. Buscando o novo tracker...")
                            time.sleep(random.uniform(0.2, 0.5)) 
                            if self.buscar_tracker():
                                print(f"Peer {self.peer_id}: Novo tracker encontrado ({self.current_tracker_uri}). Estado sincronizado.")
                                self.last_heartbeat = time.time()
                            else:
                                print(f"Peer {self.peer_id}: Nenhum tracker encontrado. Reiniciando timeout.")
                                self.last_heartbeat = time.time()
                                self.heartbeat_timeout = random.uniform(0.150, 0.300)
                time.sleep(0.05)
            except Exception as e:
                print(f"Peer {self.peer_id}: Erro no monitoramento: {e}")
                time.sleep(1)
    
    @Pyro5.api.expose
    def get_lista_arquivos(self):
        """ Retorna a lista de arquivos que este peer está compartilhando. """
        self.atualizar_lista_arquivos_local() 
        return self.files           
    @Pyro5.api.expose
    def obter_todos_arquivos(self):
        if self.is_tracker:
            return self.file_registry
        return {}
                
    @Pyro5.api.expose
    def buscar_arquivo(self, filename):
        if self.is_tracker:
            peers_with_file = [pid for pid, flist in self.file_registry.items() if filename in flist]
            return peers_with_file
        return []
    @Pyro5.api.expose
    def enviar_arquivo(self, filename):
        try:
            filepath = os.path.join(self.shared_dir, filename)
            if not os.path.exists(filepath):
                print(f"Arquivo não encontrado: {filepath}")
                return None    
            with open(filepath, 'rb') as f:
                content = f.read()
                encoded_content = base64.b64encode(content)
                return encoded_content.decode('utf-8')
        except Exception as e:
            print(f"Falha ao enviar arquivo: {e}")
            return None

    def baixar_arquivo(self, filename, source_peer_id):
        try:
            with Pyro5.api.locate_ns() as ns:
                source_uri = ns.lookup(f"Peer_{source_peer_id}")
                source_peer = Pyro5.api.Proxy(source_uri)
                source_peer._pyroTimeout = 5.0
                
                encoded_content = source_peer.enviar_arquivo(filename)
                if not encoded_content:
                    print(f"Conteúdo do arquivo está vazio ou nulo para {filename}")
                    return False
                file_content = base64.b64decode(encoded_content)
                filepath = os.path.join(self.shared_dir, filename)
                
                with open(filepath, 'wb') as f:
                    f.write(file_content)
                self.atualizar_lista_arquivos_local()
                self.notificar_arquivos_tracker()
                print(f"Arquivo {filename} baixado com sucesso do peer {source_peer_id}")
                return True
        except Exception as e:
            print(f"Falha ao baixar arquivo: {e}")
            return False
        
    def inicializar(self):
     self.other_peers = {name: uri for name, uri in self.listar_peers_ativos()}
     self.atualizar_lista_arquivos_local()

      # Se for o Peer 1, automaticamente se torna tracker
     if self.peer_id == 1 and not self.buscar_tracker():
        print(f"Peer {self.peer_id}: Nenhum tracker encontrado. Atuando como tracker inicial.")
        self.become_tracker()
     else:
        self.buscar_tracker()

     threading.Thread(target=self.monitorar_tracker, daemon=True).start()
   
def main():
    if len(sys.argv) < 2:
        print("Uso: python peer.py <peer_id>")
        print("Exemplo: python peer.py 1")
        sys.exit(1)
    
    global peer_id, peer
    peer_id = int(sys.argv[1])
    peer = Peer(peer_id)
    daemon = Pyro5.api.Daemon()
    peer.daemon = daemon
    peer.registrar_no_servico_nomes()

    daemon_thread = threading.Thread(target=daemon.requestLoop, daemon=True)
    daemon_thread.start()

    time.sleep(1)
    peer.inicializar()
    print(f"Peer {peer_id} iniciado e aguardando requisições...")

    try:
        while True:
            if peer.is_tracker:
                print("(TRACKER ATIVO)")
            print("1. Listar arquivos na rede")
            print("2. Procurar e Baixar arquivo")
            print("3. Listar meus arquivos")
            print("4. Sair")

            try:
                escolha = input("\nEscolha uma opção: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if escolha == "1":
                if peer.is_tracker:
                    registry = peer.file_registry
                    if registry:
                        print("\nRegistro de arquivos do Tracker:")
                        for peer_id, files in registry.items():
                            if files:
                                print(f"- Peer {peer_id}: {', '.join(files)}")
                
                else:
                    try:
                        tracker = Pyro5.api.Proxy(peer.current_tracker_uri)
                        registry = tracker.obter_todos_arquivos()
                        if registry:
                            print("\nRegistro de arquivos na rede:")
                            for peer_id, files in registry.items():
                                if files:
                                    print(f"- Peer {peer_id}: {', '.join(files)}")
                        else:
                            print("Nenhum arquivo registrado.")
                    except Exception as e:
                        print(f"Erro ao acessar registro: {e}")

            elif escolha == "2":
                filename = input("Digite o nome do arquivo: ").strip()
                if not filename:
                    continue

                try:
                    if peer.is_tracker:
                        peers_with_file = peer.buscar_arquivo(filename)
                    else:
                        tracker = Pyro5.api.Proxy(peer.current_tracker_uri)
                        peers_with_file = tracker.buscar_arquivo(filename)

                    if peers_with_file:
                        print(f"Arquivo encontrado nos peers: {peers_with_file}")
                        try:
                            source_peer = int(input("Digite o ID do peer de onde deseja fazer o download: "))
                            if source_peer in peers_with_file:
                                if peer.baixar_arquivo(filename, source_peer):
                                    print("✅ Download concluído!")
                                else:
                                    print("❌ Falha no download")
                            else:
                                print("❌ Peer ID inválido")
                        except ValueError:
                            print("❌ ID inválido")
                    else:
                        print("Arquivo não encontrado na rede")
                except Exception as e:
                    print(f"Erro na busca: {e}")

            elif escolha == "3":
                print("\n=== Meus Arquivos ===")
                if peer.files:
                    for i, filename in enumerate(peer.files, 1):
                        print(f"{i}. {filename}")
                else:
                    print("Nenhum arquivo local")

            elif escolha == "4":
                print("Encerrando...")
                break

    except KeyboardInterrupt:
        print("\nEncerrando peer...")
    finally:
        peer.stop_threads = True
        peer.daemon.shutdown()
        print("Peer encerrado.")
    
if __name__ == "__main__":
    main()

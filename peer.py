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
        self.epoca = 0
        self.voted_in_epoch = -1
        self.lock = threading.Lock()

    def set_epoca(self, epoca):
        with self.lock:
            self.epoca = epoca

    def inicia_election(self):
        with self.lock:
            if self.election_in_progress:
                return
            self.election_in_progress = True
            self.epoca += 1
            print(f"Peer {self.peer.peer_id}: Iniciando eleição para a ÉPOCA {self.epoca}.")
            self.voted_in_epoch = self.epoca
            self.votes_received = {str(self.peer.peer_id)}

        active_peers = self.peer.listar_peers_ativos()
        votes_needed = (len(active_peers) + 1) // 2 + 1

        for peer_name, peer_uri in active_peers:
            try:
                with Pyro5.api.Proxy(peer_uri) as remote_peer:
                    if remote_peer.request_vote(self.peer.peer_id, self.epoca):
                        self.votes_received.add(peer_name.split('_')[-1])
            except Exception as e:
                print(f"Peer {self.peer.peer_id}: Falha ao pedir voto para {peer_name}: {e}")

        with self.lock:
            if len(self.votes_received) >= votes_needed:
                print(f"Peer {self.peer.peer_id}: Ganhou a eleição para a época {self.epoca} com votos de: {self.votes_received}")
                self.peer.become_tracker()
            else:
                print(f"Peer {self.peer.peer_id}: Perdeu a eleição para a época {self.epoca}. Votos recebidos: {self.votes_received}")
            self.election_in_progress = False

    def request_vote(self, candidate_id, epoca):
        with self.lock:
            if self.peer.is_tracker:
                if epoca > self.epoca:
                    print(f"Tracker (Peer {self.peer.peer_id}): Recebeu pedido para época superior ({epoca}). Aceitando e renunciando.")
                    self.peer.is_tracker = False
                else:
                    return False

            if epoca > self.epoca:
                self.epoca = epoca
                self.voted_in_epoch = -1

            if epoca == self.epoca and self.voted_in_epoch < epoca:
                self.voted_in_epoch = epoca
                print(f"Peer {self.peer.peer_id}: Votou em {candidate_id} para a época {epoca}.")
                return True
            
            return False

# --- CLASSE PEER PRINCIPAL ---
@Pyro5.api.expose
class Peer:
    def __init__(self, peer_id):
        self.peer_id = peer_id
        self.is_tracker = False
        self.epoca = 0
        self.current_tracker_uri = None
        self.last_heartbeat = time.time()
        self.heartbeat_timeout = random.uniform(3.0, 5.0)
        self.daemon = None
        self.shared_dir = f"peer_{self.peer_id}_shared"
        self.files = []
        self.file_registry = {}
        self.stop_threads = False
        self.election_manager = ElectionManager(self)
        self.heartbeat_lock = threading.Lock()
        self.lock = threading.Lock()
        
        self._setup_local_files()

    def _setup_local_files(self):
        os.makedirs(self.shared_dir, exist_ok=True)
        if not os.listdir(self.shared_dir):
            filename = f"arquivo_peer_{self.peer_id}.txt"
            with open(os.path.join(self.shared_dir, filename), "w") as f:
                f.write(f"Conteúdo de teste do peer {self.peer_id}")
        self.files = [f for f in os.listdir(self.shared_dir) if os.path.isfile(os.path.join(self.shared_dir, f))]

    def get_uri_name(self):
        return f"Peer_{self.peer_id}"

    def registrar_no_servico_nomes(self):
        try:
            with Pyro5.api.locate_ns() as ns:
                uri = self.daemon.register(self)
                ns.register(self.get_uri_name(), uri)
                print(f"Peer {self.peer_id}: Registrado como {self.get_uri_name()}")
        except Exception as e:
            print(f"Peer {self.peer_id}: Erro ao registrar: {e}")

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

    def buscar_tracker(self):
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
                    self.epoca = latest_epoca
                    self.election_manager.set_epoca(latest_epoca)
                    print(f"Peer {self.peer_id}: Tracker MAIS RECENTE encontrado: Tracker_Epoca_{latest_epoca}")
                    self.last_heartbeat = time.time()
                    return True
        except Exception as e:
            print(f"Peer {self.peer_id}: Erro ao buscar tracker: {e}")
        return False
            
    def become_tracker(self):
        with self.lock:
            self.is_tracker = True
            self.epoca = self.election_manager.epoca
            self.file_registry = {}
            self.file_registry[self.peer_id] = self.files
            try:
                with Pyro5.api.locate_ns() as ns:
                    uri = self.daemon.uriFor(self)
                    tracker_name = f"Tracker_Epoca_{self.epoca}"
                    ns.register(tracker_name, uri)
                print(f"Peer {self.peer_id}: Tornou-se o tracker para a época {self.epoca}.")
                threading.Thread(target=self.loop_heartbeat, daemon=True).start()
                threading.Thread(target=self.solicitar_todos_arquivos, daemon=True).start()
            except Exception as e:
                print(f"Peer {self.peer_id}: Erro ao se tornar tracker: {e}")
                self.is_tracker = False

    def solicitar_todos_arquivos(self):
        if not self.is_tracker: return
        time.sleep(1)
        print(f"Tracker (Peer {self.peer_id}): Puxando listas de arquivos dos outros peers...")
        active_peers = self.listar_peers_ativos()
        for peer_name, peer_uri in active_peers:
            try:
                with Pyro5.api.Proxy(peer_uri) as remote_peer:
                    peer_id_str = peer_name.split('_')[-1]
                    peer_id = int(peer_id_str)
                    files = remote_peer.get_lista_arquivos()
                    self.atualizar_registro_arquivos(peer_id, files)
            except Exception as e:
                print(f"Tracker (Peer {self.peer_id}): Falha ao solicitar arquivos de {peer_name}. Erro: {e}")

    @Pyro5.api.expose
    def get_lista_arquivos(self):
        return self.files

    def loop_heartbeat(self):
        while self.is_tracker:
            time.sleep(1.5)
            active_peers = self.listar_peers_ativos()
            for peer_name, peer_uri in active_peers:
                try:
                    with Pyro5.api.Proxy(peer_uri) as peer:
                        peer.receber_heartbeat(self.epoca)
                except Exception:
                    pass

    @Pyro5.api.expose
    def receber_heartbeat(self, epoca):
        with self.heartbeat_lock:
            if epoca >= self.epoca:
                self.last_heartbeat = time.time()
                self.epoca = epoca
                self.election_manager.set_epoca(epoca)
                return True
            return False

    def monitorar_tracker(self):
        while not self.stop_threads:
            time.sleep(0.5)
            if not self.is_tracker and self.current_tracker_uri:
                if time.time() - self.last_heartbeat > self.heartbeat_timeout:
                    print(f"Peer {self.peer_id}: Timeout do tracker detectado. Iniciando eleição.")
                    self.current_tracker_uri = None
                    self.election_manager.inicia_election()

    @Pyro5.api.expose
    def request_vote(self, candidate_id, epoca):
        return self.election_manager.request_vote(candidate_id, epoca)
    
    def notificar_arquivos_tracker(self):
        if not self.is_tracker and self.current_tracker_uri:
            try:
                with Pyro5.api.Proxy(self.current_tracker_uri) as tracker:
                    tracker.atualizar_registro_arquivos(self.peer_id, self.files)
            except Exception as e:
                print(f"Peer {self.peer_id}: Erro ao notificar arquivos ao tracker: {e}")

    @Pyro5.api.expose
    def atualizar_registro_arquivos(self, peer_id, files):
        if self.is_tracker:
            self.file_registry[peer_id] = files
            print(f"Tracker: Registro do Peer {peer_id} atualizado com os arquivos: {files}")
            return True
        return False

    @Pyro5.api.expose
    def obter_todos_arquivos(self):
        if self.is_tracker:
            return self.file_registry
        return {}
    
    @Pyro5.api.expose
    def buscar_arquivo(self, filename):
        if self.is_tracker:
            return [pid for pid, flist in self.file_registry.items() if filename in flist]
        return []

    @Pyro5.api.expose
    def enviar_arquivo(self, filename):
        filepath = os.path.join(self.shared_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, 'rb') as f:
                return base64.b64encode(f.read()).decode('utf-8')
        return None

    def baixar_arquivo(self, filename, source_peer_id):
        try:
            with Pyro5.api.locate_ns() as ns:
                source_uri = ns.lookup(f"Peer_{source_peer_id}")
                with Pyro5.api.Proxy(source_uri) as source_peer:
                    encoded_content = source_peer.enviar_arquivo(filename)
                    if encoded_content:
                        file_content = base64.b64decode(encoded_content)
                        with open(os.path.join(self.shared_dir, filename), 'wb') as f:
                            f.write(file_content)
                        self.files.append(filename)
                        self.notificar_arquivos_tracker()
                        print(f"Arquivo {filename} baixado com sucesso do peer {source_peer_id}")
                        return True
        except Exception as e:
            print(f"Falha ao baixar arquivo: {e}")
        return False

    def inicializar(self):
        threading.Thread(target=self.monitorar_tracker, daemon=True).start()
        time.sleep(random.uniform(0.1, 1.0))
        if not self.buscar_tracker():
            self.election_manager.inicia_election()
        else:
            self.notificar_arquivos_tracker()

# --- FUNÇÃO MAIN E LÓGICA DE INTERFACE ---
def main():
    if len(sys.argv) < 2:
        print("Uso: python seu_arquivo.py <id_do_peer>")
        sys.exit(1)

    peer_id = int(sys.argv[1])
    peer = Peer(peer_id)
    daemon = Pyro5.api.Daemon()
    peer.daemon = daemon
    peer.registrar_no_servico_nomes()

    daemon_thread = threading.Thread(target=daemon.requestLoop, daemon=True)
    daemon_thread.start()

    time.sleep(1)
    peer.inicializar()

    try:
        while True:
            print("\n" + "="*20)
            print(f"Peer {peer.peer_id} | Época: {peer.election_manager.epoca} | {'TRACKER' if peer.is_tracker else 'PEER'}")
            print("1. Listar arquivos na rede")
            print("2. Baixar arquivo")
            print("3. Listar meus arquivos")
            print("4. Sair")
            
            escolha = input("> ").strip()

            if escolha == '1':
                registry = {}
                try:
                    if peer.is_tracker:
                        registry = peer.obter_todos_arquivos()
                    elif peer.current_tracker_uri:
                        with Pyro5.api.Proxy(peer.current_tracker_uri) as tracker:
                            registry = tracker.obter_todos_arquivos()
                    
                    if not registry:
                        print("Nenhum arquivo na rede.")
                    else:
                        print("\n--- Arquivos na Rede ---")
                        for pid, files in registry.items():
                            print(f"  Peer {pid}: {', '.join(files) if files else 'Nenhum arquivo'}")
                except Exception as e:
                    print(f"Erro ao listar arquivos da rede: {e}")

            elif escolha == '2':
                filename = input("Nome do arquivo para baixar: ").strip()
                peers_with_file = []
                try:
                    if peer.is_tracker:
                        peers_with_file = peer.buscar_arquivo(filename)
                    elif peer.current_tracker_uri:
                        with Pyro5.api.Proxy(peer.current_tracker_uri) as tracker:
                            peers_with_file = tracker.buscar_arquivo(filename)
                    
                    if not peers_with_file:
                        print("Arquivo não encontrado na rede.")
                    else:
                        print(f"Arquivo encontrado nos peers: {peers_with_file}")
                        source_id_str = input(f"ID do peer para baixar (ex: {peers_with_file[0]}): ").strip()
                        if source_id_str.isdigit():
                            source_id = int(source_id_str)
                            if source_id in peers_with_file:
                                peer.baixar_arquivo(filename, source_id)
                            else:
                                print("ID de peer inválido.")
                        else:
                            print("ID inválido, por favor insira um número.")
                except Exception as e:
                    print(f"Erro ao buscar/baixar arquivo: {e}")

            elif escolha == '3':
                print("\n--- Meus Arquivos ---")
                if not peer.files:
                    print("Você não está compartilhando nenhum arquivo.")
                else:
                    for f in peer.files:
                        print(f"  - {f}")

            elif escolha == '4':
                break
    finally:
        print("\nEncerrando...")
        peer.stop_threads = True
        daemon.shutdown()

if __name__ == "__main__":
    main()
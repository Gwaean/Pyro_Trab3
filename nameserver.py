import Pyro5.api
import uuid
try:
    ns = Pyro5.api.locate_ns()
except Pyro5.errors.NamingError:
    print("Não foi possível encontrar o servidor de nomes")
    exit(1)








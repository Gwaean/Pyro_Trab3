import Pyro5.server
import Pyro5.api

obj = Pyro5.client.Proxy("PYRONAME:musicserver")  
nameserver = Pyro5.api.locate_ns()
uri = nameserver.lookup("musicserver")
uri_string = "PYRONAME:musicserver"
musicserver = Pyro5.api.Proxy(uri)
try:
    musicserver.load_playlist("90s rock")
    musicserver.play()
    print("Currently playing:", musicserver.current_song())
except Exception:
    print("Couldn't select playlist or start playing")


import Pyro5.api
obj = Pyro5.client.Proxy("PYRONAME:objectname")
ns = Pyro5.core.locate_ns()
uri = ns.lookup("objectname")
# uri now is the resolved 'objectname'
obj = Pyro5.client.Proxy(uri)
obj.method()
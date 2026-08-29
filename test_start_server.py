import sys
sys.path.insert(0, 'src')
print("Importing start_server")
from web.server import start_server
print("Calling start_server")
start_server()
print("Done")

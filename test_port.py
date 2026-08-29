import sys
sys.path.insert(0, 'src')
from core.config import load_config
config = load_config()
print(f"Port from config: {config.get('port')} (type: {type(config.get('port'))})")

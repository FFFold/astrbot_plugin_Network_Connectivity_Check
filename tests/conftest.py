import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Star:
    def __init__(self, context):
        self.context = context


class _Context:
    async def send_message(self, umo, chain):
        return None


class _StarTools:
    @staticmethod
    def get_data_dir(name):
        path = ROOT / ".pytest-plugin-data" / name
        path.mkdir(parents=True, exist_ok=True)
        return path


def _register(*args, **kwargs):
    def decorator(cls):
        return cls

    return decorator


def _identity_decorator(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


class _CommandGroup:
    def __call__(self, func):
        return self

    def command(self, *args, **kwargs):
        return _identity_decorator(*args, **kwargs)


astrbot_module = types.ModuleType("astrbot")
api_module = types.ModuleType("astrbot.api")
event_module = types.ModuleType("astrbot.api.event")
star_module = types.ModuleType("astrbot.api.star")
message_module = types.ModuleType("astrbot.api.message_components")

api_module.logger = _Logger()
event_module.AstrMessageEvent = object
event_module.filter = types.SimpleNamespace(
    command=_identity_decorator,
    command_group=lambda *args, **kwargs: _CommandGroup(),
)
star_module.Context = _Context
star_module.Star = _Star
star_module.register = _register
star_module.StarTools = _StarTools
message_module.Plain = lambda text: text

sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", api_module)
sys.modules.setdefault("astrbot.api.event", event_module)
sys.modules.setdefault("astrbot.api.star", star_module)
sys.modules.setdefault("astrbot.api.message_components", message_module)

"""Echo web dashboard / control panel (Stage 7).

Importing this package never requires flask — it's imported lazily inside server.create_app
/ start_webui, so a missing flask degrades to "dashboard off" rather than an import error.
"""

from .control import EchoControl
from .server import start_webui, load_webui_config, create_app

__all__ = ["EchoControl", "start_webui", "load_webui_config", "create_app"]

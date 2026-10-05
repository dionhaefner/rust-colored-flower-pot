"""WSGI entry point configured with TC_* settings."""

from .. import get_settings
from ..wsgi import Application

_settings = get_settings()
app = Application(_settings.DRIVER_PATH, _settings._asdict())

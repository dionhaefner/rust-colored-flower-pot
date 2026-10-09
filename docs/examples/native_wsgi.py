"""Set TC_DRIVER_PATH before starting this WSGI host."""

from rcfp.wsgi import create_app

application = create_app()

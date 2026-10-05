"""WSGI entry point; set TC_DRIVER_PATH before starting the host."""

from rcfp.wsgi import create_app

application = create_app()

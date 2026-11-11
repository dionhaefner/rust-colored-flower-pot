"""Serve the unmodified Terracotta WSGI application through Lambda Function URLs."""

import json
from importlib.metadata import version

import rasterio
import serverless_wsgi
import terracotta
from rasterio.env import get_gdal_config
from terracotta.server import create_app

app = create_app()
with rasterio.Env():
    print(
        json.dumps(
            {
                "terracotta": terracotta.__version__,
                "rasterio": rasterio.__version__,
                "gdal": rasterio.__gdal_version__,
                "serverless_wsgi": version("serverless-wsgi"),
                "gdal_cache_bytes": get_gdal_config("GDAL_CACHEMAX"),
                "raster_cache_bytes": terracotta.get_settings().RASTER_CACHE_SIZE,
                "multiprocessing": terracotta.get_settings().USE_MULTIPROCESSING,
            }
        )
    )


def handler(event, context):
    return serverless_wsgi.handle_request(app, event, context)

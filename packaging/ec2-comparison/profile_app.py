"""Wall-time call profiles for separate, instrumented Terracotta requests."""

import cProfile
import os
from pathlib import Path

from terracotta.server import create_app

application = create_app()
profile = cProfile.Profile()


def app(environ, start_response):
    profile.enable()
    try:
        result = application(environ, start_response)
        try:
            return list(result)
        finally:
            if hasattr(result, "close"):
                result.close()
    finally:
        profile.disable()
        profile.dump_stats(str(Path("/profiles") / f"{os.getpid()}.pstats"))

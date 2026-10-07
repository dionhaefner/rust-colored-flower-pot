"""Serve a scalar satellite model through the installed RCFP Python API."""

import argparse
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

import numpy as np
import rcfp
from rcfp.wsgi import Application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--red", required=True)
    parser.add_argument("--nir", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--bounds",
        type=float,
        nargs=4,
        required=True,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
    )
    parser.add_argument("--weather", type=float, required=True)
    parser.add_argument("--weather-revision", required=True)
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    store = rcfp.LayerStore(args.database)
    store.create()
    store.put(
        "satellite",
        {
            "observations": [
                {
                    "id": args.date,
                    "attributes": {"date": args.date},
                    "assets": {
                        "red": {"location": args.red},
                        "nir": {"location": args.nir},
                    },
                }
            ],
            "overlap": "first_valid",
        },
    )

    def stress(values, valid, context):
        red, nir = values["red"], values["nir"]
        coverage = valid["red"] & valid["nir"] & (red + nir != 0)
        result = np.zeros(red.shape)
        np.divide(nir - red, nir + red, out=result, where=coverage)
        return result * context.parameters["gain"] + args.weather, coverage

    class Server(ThreadingMixIn, WSGIServer):
        # Stop and join HTTP callers before releasing the compute host.
        daemon_threads = False

    with rcfp.ComputeApplication(args.database) as host:
        host.register(
            "stress",
            stress,
            source="satellite",
            observation=args.date,
            inputs={
                name: {"asset": name, "band": 1, "values": "raw"}
                for name in ("red", "nir")
            },
            version="stress-model-1",
            preview_grid={
                "crs": "EPSG:3857",
                "bounds": args.bounds,
                "size": [256, 256],
                "resampling": "nearest",
            },
            parameters={
                "gain": {"type": "number", "default": 1, "minimum": 0, "maximum": 4}
            },
            dependency_revisions={"weather": args.weather_revision},
            display={"stretch_range": [-1, 2], "colormap": "viridis"},
        )
        with make_server(
            "127.0.0.1", args.port, Application(application=host), server_class=Server
        ) as server:
            print(
                f"Python map host listening on http://127.0.0.1:{server.server_port}",
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()

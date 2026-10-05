"""Installed-wheel compatibility checks and optional Terracotta differential replay."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"
PROFILE = "terracotta-0.10.1"


def invoke(command, *, profile=PROFILE, check=True, **kwargs):
    env = dict(os.environ, RCFP_COMPAT_PROFILE=profile)
    return subprocess.run(
        command, env=env, check=check, capture_output=True, text=True, **kwargs
    )


def wsgi_request(app, url):
    from io import BytesIO, StringIO
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    status = []
    body = b"".join(
        app(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": parts.path,
                "QUERY_STRING": parts.query,
                "SERVER_NAME": "localhost",
                "SERVER_PORT": "80",
                "SERVER_PROTOCOL": "HTTP/1.1",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": BytesIO(),
                "wsgi.errors": StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            },
            lambda value, headers, exc_info=None: status.append(value),
        )
    )
    return int(status[0].split()[0]), body


def worker(package, database, output, create=False):
    import numpy as np
    from PIL import Image

    tc = importlib.import_module(package)
    driver = tc.get_driver(database)
    if create:
        driver.create(["scene", "band"], key_descriptions={"scene": "Scene"})
        for band in ("red", "green", "blue", "nir"):
            driver.insert(["lagoon", band], str((CORPUS / f"{band}.tif").resolve()))
    tc.update_settings(DRIVER_PATH=str(database), USE_MULTIPROCESSING=False)

    def array(value):
        return {
            "dtype": str(value.dtype),
            "shape": list(value.shape),
            "values": value.filled(0).tolist(),
            "mask": np.ma.getmaskarray(value).tolist(),
        }

    def png(value):
        image = np.asarray(Image.open(value).convert("RGBA"))
        return {
            "shape": list(image.shape),
            "sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        }

    result = {}
    result["settings_fields"] = list(tc.get_settings()._fields)
    result["keys"] = list(driver.get_keys().items())
    result["version"] = driver.db_version
    result["datasets"] = [
        list(keys) for keys in driver.get_datasets(order_by=["band"], ascending=False)
    ]
    metadata = driver.get_metadata({"scene": "lagoon", "band": "red"})
    result["metadata"] = metadata
    result["metadata_types"] = {
        key: type(metadata[key]).__name__ for key in ("range", "bounds", "percentiles")
    }
    result["raster"] = array(
        driver.get_raster_tile(["lagoon", "red"], tile_size=(32, 48))
    )
    future = driver.get_raster_tile(
        ["lagoon", "red"], tile_size=(32, 48), asynchronous=True
    )
    result["future"] = {
        "type": type(future).__name__,
        "array": array(future.result(timeout=20)),
    }
    for label, keys in (("missing", ["lagoon", "missing"]), ("invalid", ["lagoon"])):
        try:
            driver.get_metadata(keys)
        except Exception as error:  # noqa: BLE001
            result[label] = type(error).__name__
        else:
            raise AssertionError(f"{label} unexpectedly succeeded")
    with driver.connect():
        driver.insert(["written", "red"], "stored-original-path.tif", metadata=metadata)
        result["written_metadata"] = driver.get_metadata(["written", "red"])
    driver.delete(["written", "red"])
    result["delete"] = not driver.get_datasets({"scene": "written"})

    from concurrent.futures import Future

    assert isinstance(future, Future)
    assert isinstance(driver.get_raster_tile(["lagoon", "red"]), np.ma.MaskedArray)
    from io import BytesIO

    app = importlib.import_module(f"{package}.server.app").app
    status, body = wsgi_request(app, "/keys")
    assert status == 200
    result["http_keys"] = json.loads(body)
    for name, url in {
        "singleband": "/singleband/lagoon/red/preview.png?stretch_range=[0,3000]&tile_size=[48,32]",
        "rgb": "/rgb/lagoon/preview.png?r=red&g=green&b=blue&r_range=[0,3000]&g_range=[0,3000]&b_range=[0,3000]&tile_size=[48,32]",
        "compute": "/compute/lagoon/preview.png?expression=(v1-v2)/(v1%2Bv2)&v1=nir&v2=red&stretch_range=[-1,1]&tile_size=[48,32]",
    }.items():
        status, body = wsgi_request(app, url)
        assert status == 200, (name, body)
        result[name] = png(BytesIO(body))
    result["colormap"] = json.loads(
        wsgi_request(
            app, "/colormap?stretch_range=[0,3000]&colormap=viridis&num_values=7"
        )[1]
    )
    result["http_missing"] = wsgi_request(app, "/metadata/lagoon/missing")[0]
    output.write_text(
        json.dumps(
            result,
            default=lambda value: (
                value.tolist() if hasattr(value, "tolist") else float(value)
            ),
        )
    )


def run(args):
    import numpy as np
    import rcfp
    from PIL import Image

    assert Path(rcfp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "test requires an installed wheel"
    )
    args.out.mkdir(parents=True, exist_ok=True)
    cases = []

    def case(name, operation):
        try:
            operation()
        except Exception as error:  # noqa: BLE001
            cases.append(
                {
                    "id": name,
                    "status": "failed",
                    "error": repr(error),
                    "traceback": traceback.format_exc(),
                }
            )
        else:
            cases.append({"id": name, "status": "passed"})

    executable = Path(sys.executable).with_name(
        "rcfp.exe" if os.name == "nt" else "rcfp"
    )
    database = args.out / "catalog.sqlite"
    if database.exists():
        raise ValueError(f"Output catalog already exists: {database}")
    driver = rcfp.get_driver(database)
    driver.create(["scene", "band"])
    source = (CORPUS / "red.tif").resolve()
    driver.insert(["lagoon", "red"], str(source))
    metadata = driver.get_metadata(["lagoon", "red"])

    def flag_selection():
        assert rcfp.compatibility_profile == PROFILE
        assert rcfp.__protocol_version__ == "0.10.1"
        result = invoke([str(executable), "--version-json"])
        assert json.loads(result.stdout)["compatibility_profile"] == PROFILE
        invoke(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys, rcfp_cli; sys.argv=['rcfp','--version']; rcfp_cli.main(); assert 'rcfp' not in sys.modules",
            ]
        )
        for name in (
            "config",
            "drivers",
            "drivers.sqlite_meta_store",
            "drivers.sqlite_remote_meta_store",
            "drivers.mysql_meta_store",
            "drivers.postgresql_meta_store",
            "exceptions",
            "server.app",
        ):
            if name == "server.app":
                rcfp.update_settings(DRIVER_PATH=str(database))
            importlib.import_module(f"rcfp.{name}")
        for name in (
            "image",
            "expressions",
            "xyz",
            "raster",
            "handlers",
            "cmaps",
            "logs",
            "profile",
            "drivers.relational_meta_store",
            "server.flask_api",
            "scripts.cli",
        ):
            try:
                importlib.import_module(f"rcfp.{name}")
            except ModuleNotFoundError:
                pass
            else:
                raise AssertionError(f"internal facade still shipped: {name}")

    case("profile.public-imports", flag_selection)

    def invalid_profile():
        result = invoke(
            [sys.executable, "-I", "-c", "import rcfp"], profile="unknown", check=False
        )
        assert (
            result.returncode
            and "unknown compatibility profile" in result.stderr.lower()
        )
        result = invoke(
            [str(executable), "--compat-profile", "unknown", "--help"], check=False
        )
        assert (
            result.returncode
            and "unknown compatibility profile" in result.stderr.lower()
        )

    case("profile.invalid-values", invalid_profile)

    def precedence():
        config = args.out / "settings.toml"
        config.write_text('COMPAT_PROFILE = "terracotta-0.10.1"\n')
        result = invoke(
            [str(executable), "-c", str(config), "--version-json"], profile="native"
        )
        assert json.loads(result.stdout)["compatibility_profile"] == PROFILE
        result = invoke(
            [
                str(executable),
                "--compat-profile",
                "native",
                "-c",
                str(config),
                "--version-json",
            ],
            profile="unknown",
        )
        assert json.loads(result.stdout)["compatibility_profile"] == "native"

    case("profile.cli-precedence", precedence)

    def immutable():
        app = rcfp.NativeApplication(database)
        previous = os.environ.get("RCFP_COMPAT_PROFILE")
        os.environ["RCFP_COMPAT_PROFILE"] = "native"
        try:
            assert rcfp.compatibility_profile == PROFILE
            assert app.request("GET", "/keys")[0] == 200
            assert driver.get_metadata(["lagoon", "red"])["range"] == metadata["range"]
            try:
                rcfp.NativeApplication(database, {"COMPAT_PROFILE": "native"})
            except ValueError:
                pass
            else:
                raise AssertionError("profile changed within process")
        finally:
            os.environ["RCFP_COMPAT_PROFILE"] = previous or PROFILE

    case("profile.immutable-app-driver", immutable)

    def sql_driver_cache():
        for provider in ("mysql", "postgresql"):
            path = (
                f"{provider}://reader:password@127.0.0.1/cache_contract?sslmode=disable"
            )
            first = rcfp.get_driver(path)
            assert first is rcfp.get_driver(path, provider=provider)
            for other in (
                path.replace("sslmode=disable", "sslmode=verify-full"),
                path + "&sslrootcert=untrusted.pem",
                path.replace("reader:password", "another:password"),
                path.replace("reader:password", "reader:another"),
            ):
                assert rcfp.get_driver(other) is not first

    case("python.sql-driver-cache-configuration", sql_driver_cache)

    def api_types():
        assert rcfp.get_driver(database) is rcfp.get_driver(
            database.parent / "unused/.." / database.name
        )
        assert isinstance(driver.key_names, tuple)
        assert isinstance(metadata["bounds"], tuple)
        array = driver.get_raster_tile(["lagoon", "red"], tile_size=(32, 48))
        assert isinstance(array, np.ma.MaskedArray) and array.shape == (32, 48)
        future = driver.get_raster_tile(
            {"scene": "lagoon", "band": "red"}, tile_size=(32, 48), asynchronous=True
        )
        np.testing.assert_array_equal(future.result(timeout=30), array)

    case("python.driver-array-future-types", api_types)

    def lazy_and_context():
        driver.insert(["lazy", "red"], str(source), skip_metadata=True)
        assert driver.get_metadata(["lazy", "red"])["range"] == metadata["range"]
        with driver.connect():
            with driver.connect():
                driver.insert(["context", "red"], str(source), metadata=metadata)
            assert driver.get_metadata(["context", "red"])["range"] == metadata["range"]
        try:
            with driver.connect():
                driver.insert(["committed", "red"], str(source), metadata=metadata)
                raise RuntimeError("caller failed")
        except RuntimeError:
            pass
        assert driver.get_datasets({"scene": "committed"})
        driver.delete(["committed", "red"])
        driver.delete(["context", "red"])
        driver.delete(["lazy", "red"])

    case("python.lazy-metadata-connection-context", lazy_and_context)

    def exceptions():
        for values, error_type in (
            (["lagoon"], rcfp.exceptions.InvalidKeyError),
            (["lagoon", "missing"], rcfp.exceptions.DatasetNotFoundError),
        ):
            try:
                driver.get_metadata(values)
            except error_type:
                pass
            else:
                raise AssertionError("wrong exception behavior")
        try:
            driver.compute_metadata(source, max_shape=[1])
        except ValueError:
            pass
        else:
            raise AssertionError("invalid metadata dimensions accepted")

    case("python.exception-types", exceptions)

    def wsgi_entrypoint():
        from rcfp.wsgi import Application

        rcfp.update_settings(DRIVER_PATH=str(database))
        app = importlib.import_module("rcfp.server.app").app
        status, body = wsgi_request(app, "/keys")
        assert status == 200 and json.loads(body)["keys"][0]["key"] == "scene"
        status, body = wsgi_request(Application(str(database)), "/keys")
        assert status == 200 and json.loads(body)["keys"][0]["key"] == "scene"

    case("python.wsgi-entrypoint", wsgi_entrypoint)

    def unchanged_data():
        raster_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        with sqlite3.connect(database) as connection:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
            ).fetchall()
        image = rcfp.render_preview(
            database, ["lagoon", "red"], stretch_range=[0, 3000]
        )
        assert Image.open(__import__("io").BytesIO(image)).size == (256, 256)
        with sqlite3.connect(database) as connection:
            assert (
                schema
                == connection.execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
                ).fetchall()
            )
            assert (
                connection.execute("SELECT version FROM terracotta").fetchone()[0]
                == "0.10.1"
            )
        assert hashlib.sha256(source.read_bytes()).hexdigest() == raster_hash

    case("storage.unchanged-format-and-rasters", unchanged_data)

    def incompatible_catalog():
        unsupported = args.out / "unsupported.sqlite"
        shutil.copy2(database, unsupported)
        with sqlite3.connect(unsupported) as connection:
            connection.execute("UPDATE terracotta SET version='1.0.0'")
        before = unsupported.read_bytes()
        try:
            rcfp.get_driver(unsupported).insert(
                ["bad", "red"], str(source), metadata=metadata
            )
        except rcfp.exceptions.InvalidDatabaseError as error:
            assert "Version conflict" in str(error)
        else:
            raise AssertionError("unsupported catalog was writable")
        assert unsupported.read_bytes() == before

    case("storage.unsupported-format-write-fence", incompatible_catalog)

    def remote_catalog():
        remote_source = args.out / "remote-source.sqlite"
        shutil.copy2(database, remote_source)
        rcfp.get_driver(remote_source).insert(
            ["remote-lazy", "red"], str(source), skip_metadata=True
        )
        payload = remote_source.read_bytes()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                pass

            def do_GET(self):
                requests.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        variables = {
            "AWS_ENDPOINT_URL_S3": f"http://127.0.0.1:{server.server_port}",
            "AWS_ACCESS_KEY_ID": "compatibility-test-key",
            "AWS_SECRET_ACCESS_KEY": "compatibility-test-secret",
            "AWS_REGION": "eu-west-1",
        }
        previous = {name: os.environ.get(name) for name in variables}
        os.environ.update(variables)
        try:
            remote = rcfp.get_driver("s3://compatibility/catalog.sqlite")
            assert remote.get_keys() == driver.get_keys()
            assert remote.get_metadata(["lagoon", "red"]) == metadata
            with remote.connect():
                np.testing.assert_array_equal(
                    remote.get_raster_tile(["lagoon", "red"], tile_size=(32, 48)),
                    driver.get_raster_tile(["lagoon", "red"], tile_size=(32, 48)),
                )
            try:
                remote.delete(["lagoon", "red"])
            except rcfp.exceptions.DatabaseNotWritableError:
                pass
            else:
                raise AssertionError("remote catalog was writable")
            try:
                remote.get_metadata(["remote-lazy", "red"])
            except rcfp.exceptions.DatabaseNotWritableError as error:
                assert "Lazy loading" in str(error)
            else:
                raise AssertionError("remote lazy metadata was writable")
            assert remote_source.read_bytes() == payload
            assert requests and any(
                "authorization" in {name.lower() for name in row} for row in requests
            ), "remote request was unsigned"
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    case("storage.remote-catalog-readonly", remote_catalog)

    def native_mode():
        program = "import rcfp; assert rcfp.compatibility_profile=='native'; assert not hasattr(rcfp,'get_driver'); assert rcfp.DataStore"
        invoke([sys.executable, "-I", "-c", program], profile="native")
        result = invoke(
            [str(executable), "--compat-profile", "native", "serve", "--help"]
        )
        assert "rcfp" in result.stdout

    case("profile.native-api-and-cli", native_mode)

    def cli_workflows():
        output = args.out / "ingested.sqlite"
        invoke(
            [
                str(executable),
                "ingest",
                str(CORPUS / "{band}.tif"),
                "-o",
                str(output),
                "--skip-metadata",
                "-q",
            ]
        )
        assert rcfp.get_driver(output).get_datasets()
        for command in ("serve", "ingest", "optimize-rasters", "connect", "migrate"):
            assert "Usage:" in invoke([str(executable), command, "--help"]).stdout
        invoke([str(executable), "migrate", str(output), "--yes"])
        legacy = args.out / "legacy.sqlite"
        shutil.copy2(database, legacy)
        with sqlite3.connect(legacy) as connection:
            connection.execute("UPDATE terracotta SET version='0.9.0'")
        before = legacy.read_bytes()
        invoke([str(executable), "migrate", str(legacy), "--dry-run"])
        assert legacy.read_bytes() == before
        invoke([str(executable), "migrate", str(legacy), "--yes"])
        assert rcfp.get_driver(legacy).db_version == "0.10.0"
        env = dict(os.environ, RCFP_COMPAT_PROFILE=PROFILE, TC_TESTING="1")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        serving_env = dict(env, TC_TESTING="")
        with (args.out / "serve.log").open("w") as log:
            process = subprocess.Popen(
                [str(executable), "serve", "-d", str(database), "--port", str(port)],
                env=serving_env,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with urlopen(
                            f"http://127.0.0.1:{port}/keys", timeout=1
                        ) as response:
                            assert json.load(response)["keys"][0]["key"] == "scene"
                        break
                    except URLError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError(
                                "compatibility CLI server did not start"
                            ) from None
                        time.sleep(0.05)
                subprocess.run(
                    [str(executable), "connect", f"127.0.0.1:{port}", "--no-browser"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=15,
                )
            finally:
                process.terminate()
                process.wait(timeout=10)
        optimized = args.out / "optimized"
        invoke(
            [
                str(executable),
                "optimize-rasters",
                str(source),
                "-o",
                str(optimized),
                "-q",
            ]
        )
        assert (optimized / source.name).is_file()

    case("cli.all-workflows-and-migration-dry-run", cli_workflows)

    def reference_roundtrip():
        reference_database = args.out / "reference.sqlite"
        baseline = args.out / "reference.json"
        candidate = args.out / "candidate.json"
        script = str(Path(__file__).resolve())
        invoke(
            [
                str(args.reference_python),
                script,
                "--worker",
                "terracotta",
                "--create",
                "--database",
                str(reference_database),
                "--output",
                str(baseline),
            ]
        )
        invoke(
            [
                sys.executable,
                script,
                "--worker",
                "rcfp",
                "--database",
                str(reference_database),
                "--output",
                str(candidate),
            ]
        )
        expected, actual = (
            json.loads(baseline.read_text()),
            json.loads(candidate.read_text()),
        )
        assert actual == expected, (
            f"reference mismatch in {[key for key in expected if expected[key] != actual.get(key)]}"
        )
        # A catalog written by RCFP must be usable by the unchanged reference too.
        other = args.out / "candidate-created.sqlite"
        invoke(
            [
                sys.executable,
                script,
                "--worker",
                "rcfp",
                "--create",
                "--database",
                str(other),
                "--output",
                str(candidate),
            ]
        )
        invoke(
            [
                str(args.reference_python),
                script,
                "--worker",
                "terracotta",
                "--database",
                str(other),
                "--output",
                str(baseline),
            ]
        )
        expected, actual = (
            json.loads(baseline.read_text()),
            json.loads(candidate.read_text()),
        )
        assert actual == expected, (
            f"write/readback mismatch in {[key for key in expected if expected[key] != actual.get(key)]}"
        )

    if args.reference_python:
        case("reference.python-cli-http-storage-roundtrip", reference_roundtrip)

    required = json.loads(
        (ROOT / "tests/fixtures/compatibility-cases.json").read_text()
    )
    expected = required["required"] + (
        required["reference"] if args.reference_python else []
    )
    assert [row["id"] for row in cases] == expected, (
        "required compatibility cases missing"
    )
    report = {
        "profile": PROFILE,
        "version": rcfp.__version__,
        "python": sys.version,
        "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
        "cases": cases,
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    suite = ET.Element("testsuite", name="compatibility", tests=str(len(cases)))
    for row in cases:
        node = ET.SubElement(suite, "testcase", name=row["id"])
        if row["status"] == "failed":
            ET.SubElement(node, "failure").text = row["error"]
    ET.ElementTree(suite).write(
        args.out / "junit.xml", encoding="utf-8", xml_declaration=True
    )
    print(json.dumps(report, indent=2))
    if any(row["status"] != "passed" for row in cases):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--reference-python", type=Path)
    parser.add_argument("--worker", choices=["rcfp", "terracotta"])
    parser.add_argument("--database")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.database, args.output, args.create)
    else:
        run(args)


if __name__ == "__main__":
    main()

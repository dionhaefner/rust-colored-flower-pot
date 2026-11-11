# Serve through WSGI

Use an installed wheel and a WSGI host appropriate to your operating system.
The native adapter creates one owned application for its workers and caches.
Keep that application alive across requests. `TC_DRIVER_PATH` accepts a SQL
catalog location, STAC document or local [source configuration](stac.md) in native
mode. All use the same WSGI application and tile routes.

```sh
export RCFP_COMPAT_PROFILE=native
export TC_DRIVER_PATH=/absolute/path/catalog.sqlite
python -m pip install gunicorn
python -m gunicorn --bind 127.0.0.1:5000 --workers 1 rcfp.wsgi:app
```

Gunicorn runs on Unix. Choose a supported WSGI host on Windows. With
[Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/), for example,
`waitress-serve --listen=127.0.0.1:5000 rcfp.wsgi:app` uses the same application.
Install your chosen host separately. `TC_DRIVER_PATH` must be set before module
import, or `rcfp.wsgi:app` is not configured. Test `/keys` and a preview before
routing traffic to it.

To configure an explicit module, use `docs/examples/native_wsgi.py` as a starting
point, or construct `Application(path, settings=...)`. Each host process has its
own native worker pool and cache budget. Multiplying host workers multiplies
those resources. A single persistent process can already use several raster
workers. See [resource tuning](resources.md).

For the documented Terracotta WSGI entry point, select the named profile before
the host imports anything:

```sh
export RCFP_COMPAT_PROFILE=terracotta
export TC_DRIVER_PATH=/absolute/path/catalog.sqlite
python -m gunicorn --bind 127.0.0.1:5000 --workers 1 rcfp.server.app:app
```

`rcfp.server.app:app` uses the native WSGI adapter. It does not expose a Flask
application or extension hooks. Native `rcfp.wsgi.create_app(database)` creates
the same kind of adapter with an explicit catalog. Follow
[native migration](../native-migration.md) when changing entry points. Shutdown
waits for outstanding native work and can outlast an HTTP response deadline.
Configure your host's graceful timeout.

Run [read-only diagnostics](diagnostics.md) from this installed environment with
the same catalog, TOML and environment settings before starting WSGI. Use
`--render-probe` for a bounded local check and apply configuration before Python
imports when selecting the compatibility profile.

For trusted Python models, pass a persistent `ComputeApplication` as
`Application(application=host)`. Register layers before serving and call
`host.close()` in the host shutdown hook. See [Python compute layers](python-compute.md).

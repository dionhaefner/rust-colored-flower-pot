# Run the native server in a container

Use a matching Linux native bundle with a catalog and rasters reachable inside
the container. Keep the bundle directories together. The supplied Dockerfile
expects a build context containing `bundle/` and `fixture/`.

```sh
mkdir -p container-context/fixture
cp -a dist/bundle container-context/bundle
cp packaging/container/Dockerfile container-context/Dockerfile
```

Place a catalog and its data in `container-context/fixture`, or use a volume at
runtime. Stored raster paths must use container locations, such as
`/data/red.tif`; a host absolute path does not become a container path by itself.
Prepare a deployment catalog deliberately rather than rewriting the only copy
of your production catalog.

```sh
docker build -t rcfp-local container-context
docker run --rm -p 127.0.0.1:5000:5000 -v "$PWD/deployment-data:/data" rcfp-local
```

The default command serves `/data/catalog.sqlite` and binds `0.0.0.0:5000`
inside the container. The host port above accepts local connections only.
Check `http://127.0.0.1:5000/keys` and a known image. Set worker/cache configuration
with Docker `-e` arguments. Use a writable catalog if metadata may be filled
lazily, or precompute metadata before mounting a read-only data directory.

For an existing current catalog there is no format migration. Verify the chosen
bundle's native-library baseline against the container base image, and see
[artifact requirements](../reference/artifacts.md) for architecture and ABI limits.

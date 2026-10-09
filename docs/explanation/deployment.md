# Choose a deployment model

The right host depends on application integration, data access, and operational
ownership. Native HTTP and Python WSGI are adapters over the same Rust core.

| Model                 | Useful when                                                           | Responsibilities                                                                                        |
| --------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Native executable     | You want a raster service or a local exploration server               | Bundle compatibility, service management, public routing/HTTPS, data paths and resource limits          |
| Native WSGI           | An existing Python host needs raster request handling                 | Persistent Application, per-process budgets, host grace periods and environment setup                   |
| Container             | You already operate container workloads                               | Native bundle/base-image ABI, mounted paths, writable versus read-only metadata, ingress                |
| Lambda custom runtime | Your infrastructure uses API Gateway and S3-backed/serverless serving | Matching runtime bundle, role access, binary responses, invocation limits and real-service verification |

A catalog and its rasters must be reachable from the chosen runtime. A serverless
host does not automatically make source I/O fast or authorize access, and a local
container test does not validate a cloud deployment. WSGI process counts multiply
native worker pools and caches. Resource sizing should include the whole service.

Use [native configuration](../how-to/configure.md), [WSGI](../how-to/wsgi.md),
[container](../how-to/container.md), or [Lambda](../how-to/lambda.md) for concrete
steps. Review the [support matrix](../reference/support.md) and rehearse rollback
before changing a public deployment.

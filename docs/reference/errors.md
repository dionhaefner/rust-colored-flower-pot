# Errors and operational limits

| Condition                          | HTTP behavior                                | Operator consequence                                                   |
| ---------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------- |
| Invalid keys/arguments/query sizes | 400                                          | Correct the request; inspect the endpoint specification.               |
| Dataset missing                    | 404                                          | Verify catalog selection and key order.                                |
| Write to a read-only store         | Forbidden/error for the applicable operation | Use a writable catalog copy or precompute metadata.                    |
| Raster queue saturated             | 503                                          | Bound arrival concurrency and tune queue/workers to measured capacity. |
| Response wait deadline             | 504                                          | Native work may still run and drains normally.                         |
| Missing metrics permission         | Metrics route hidden                         | Set EXPOSE_METRICS only for the intended monitoring deployment.        |

Native HTTP limits include 16 KiB headers, 1 MiB request bodies, a five-second
arrival deadline, and two concurrent render requests in the socket adapter.
WSGI body reads are bounded separately. Tile dimensions must be positive and
total at most 1,048,576 pixels. `/colormap` permits at most 4096 samples;
`/datasets` permits at most 1000 items per page. Bulk metadata selection is bounded
by `MAX_POST_METADATA_KEYS`. Oversized/malformed input is rejected before large
render allocations.

Native Python operations raise `RcfpError`; HTTP helpers return status/body.
Compatibility Python uses its named exception hierarchy, preserving supported
legacy return/exception behavior. Raster errors are sanitized for clients;
retain private diagnostic logs when investigating local access failures.

Disconnects/deadlines do not forcibly cancel GDAL. Application teardown joins
workers after submitted work finishes. Use persistent applications and bounded
remote I/O. See [troubleshooting](../how-to/troubleshoot.md) and
[resource controls](../how-to/resources.md).

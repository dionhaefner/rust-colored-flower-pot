# Terracotta compatibility Python API

Set `RCFP_COMPAT_PROFILE=terracotta` before importing `rcfp` or starting
Python/WSGI workers. Change documented `terracotta.*` import prefixes to `rcfp.*`.
The profile is frozen on import and cannot be changed with `update_settings`.

The contract consists of the three top-level functions, the documented
`TerracottaDriver` methods/properties, four provider catalog types, the
`TerracottaSettings` result and `server.app:app`. Raster I/O, statistics,
lazy metadata, queries and writes execute in Rust. Python wraps ordered keys,
dataset mappings, metadata tuples/lists, masked NumPy arrays, Futures and
`rcfp.exceptions`. NumPy is the only Python runtime dependency.

`driver.connect()` retains a native session and preserves legacy per-operation
commits. It is not an atomic batch and does not yield a SQLAlchemy connection.
The WSGI entry point uses the same native application as HTTP. Flask factories,
Click groups and internal image/expression/handler/backend helpers are unsupported.
See the complete [contract and exclusions](../compatibility.md#compatibility-scope).

## Extracted signatures

```{include} ../_generated/python-terracotta.md

```

See [the trial](../compatibility.md) and [native migration](../native-migration.md).

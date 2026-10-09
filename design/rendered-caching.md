# Rendered caching and publication

The shared application finalizes delivery before adapters strip HEAD bodies or
write responses. Native HTTP, Python/FFI, WSGI and Lambda pass the same headers.
Native successful retrievals use strong validators and revalidation by default.
The named Terracotta profile preserves its transport headers and conditional
behavior. Operator contracts are in [rendered caching](../docs/how-to/rendered-caching.md).

Rendered publication uses a digest of canonical request parameters, selected
source/default metadata, logical plans, file generations, effective render
settings and encoded bytes. Including bytes guards renderer/encoder changes
even when the declared rendering contract version is unchanged. Effective
defaults must be identical across adapter settings snapshots. Revision queries
do not participate in render-cache identity. The origin compares a supplied
revision before conditional evaluation and rejects changes with 409. This
guards identity reuse without retaining old catalog/source snapshots.

Source access belongs to the application and worker settings scope. Internal
cache keys retain private transport locations. Public validators expose digests.
Explicit asset revisions exclude signed access locations from logical identity.
Requests with authorization/cookies receive no-store, and shared publication
requires an explicit application setting. Vary includes denied CORS origins,
authorization and cookies to prevent a proxy from reusing policy across requests.
Deployment authentication remains outside RCFP.

Singleband, RGB, compute and layer rendering share the bounded encoded cache and
in-flight results. The GET layer route uses persisted defaults or a parsed render
specification, while POST responses remain no-store. Cache admission detects
changes to direct local inputs during rendering and releases followers without
retaining the result. Logical source revisions include their input generations.
Unix stamps include inode/change time to detect preserved-mtime replacement or
writes. Other platforms retain their existing size/mtime boundary. These stamps
do not replace checksum-pinned or versioned publication inputs.

Remote source/array/image TTLs retain their existing limits. Conditional HTTP
requests validate the application's visible representation, rather than forcing
an upstream refresh. STAC's checksum-verified staging supplies an immutable
input. Unversioned remote mutation and credential refresh retain provider-specific
qualification requirements. Already cached immutable URLs may retain a previous
snapshot after origin changes. New publications use new URLs. Removal from
external caches requires their purge controls.

The maintained delivery runner verifies adapters, revision failures, independent
pixels, transparent images, filesystem/catalog mutation, encoded compute/layer
reuse, nginx and Chromium. It records three alternating paired nine-tile
viewport trials, payload bytes, raster jobs, cache accounting, RSS and duplicate
work at zero and 4 MiB retention. Outputs and failed setup runs belong in ignored
`build/rendered-caching/`. Final qualification is recorded separately in the
[validation handoff](human-validation.md).

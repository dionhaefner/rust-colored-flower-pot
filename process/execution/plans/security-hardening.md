# Security hardening

Scope: bound HTTP connection parsing and render memory, require authenticated database transport, prevent operational data and path disclosure, and harden native buffer and panic boundaries. Preserve existing API responses for ordinary requests.

Checks: Rust unit suite, focused malformed/slow HTTP checks, Python adapter checks, and source review of all FFI and GDAL read call sites. Record any dependency or integration validation gap in the handoff.

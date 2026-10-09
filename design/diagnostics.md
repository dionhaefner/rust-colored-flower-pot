# Read-only inspection and supplied first use

`rcfp inspect` uses the existing settings parser, ingestion filename matcher,
read-only SQLite store and GDAL raster reader. It provides a versioned JSON report
and a text view, with issues carrying severity, code and next step. CLI parsing
retains setting origins before environment and profile-dependent defaults are
filled. Explicit CLI profile/loglevel selectors apply after TOML loading,
regardless of flag order.

Physical TIFF headers and optional 256×256 grayscale probes run in child
processes. The parent suppresses raw GDAL stderr, captures private output under a one-MiB
JSON read limit, terminates children after ten seconds and reaps them. This bounds probe time
without cancelling GDAL inside persistent serving workers. It does not cap every
GDAL allocation or source decode. VRTs and other dependency formats are rejected
before opening. A synthetic shared-library initializer checks blocked native
startup under the production deadline.

Catalog inspection uses read-only, non-lazy SQLite access. Native band bindings
identify the physical asset and one-based selected band. Expression and stored
source bindings remain explicit skipped logical checks. Remote catalogs,
advanced layers and direct STAC configuration need serving/deployment checks.
Inspection does not call lazy descriptor/metadata resolution. Existing discovery
routines supply datasets. Raster probes are limited to 100 datasets.

Remote access requires an explicit HTTP(S) range GET, with a five-second client
deadline, no redirects and at most 1024 retained body bytes. Ignored or malformed
ranges do not trigger whole-file reads. Access denial yields a sanitized issue
without inferring provider credential-chain state. Remote GDAL rendering is
excluded. URLs omit user info, query and fragment. Secret/string configuration
and extra credential/GDAL environment settings are redacted. Local paths and
catalog keys remain operator data.

`rcfp sample` embeds the existing checksum-pinned tutorial TIFFs, manifest and
catalog helper in the executable. It creates a new directory and refuses
replacement. Wheels, native bundles and Lambda build inputs retain the same
sample bytes and attribution, allowing offline first use after installation.
The raster inputs add roughly 1.4 MiB. Source archives already include them.

The viewer's Leaflet adapter shares admission across replaced layers, with three
tile requests in flight. RGB tiles can submit three independent raster reads,
so pacing avoids viewport bursts exhausting the default raster queue. Removed
tiles abort fetches and drop queued requests. Admitted GDAL jobs still finish
under the serving execution contract. HTTP 503 receives at most two retries.
Permanent errors remain visible. The API-docs renderer uses system fonts and a
text footer label, with no external font or logo fetch. Native specifications
use an RCFP service description. The named compatibility profile preserves its
reference specification.

The [diagnostic contract](../docs/how-to/diagnostics.md) documents output and
unsupported checks. Tests live in `tests/diagnostic_contracts.py`. Generated
reports stay in ignored storage. The first-use trial runs from a fresh directory,
compares installed-artifact output with independent Terracotta reference pixels
and optionally checks offline Chromium. Automated completion time and obstacles
are separate from a human-participant usability trial.

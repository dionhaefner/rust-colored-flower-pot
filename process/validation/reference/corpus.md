# Corpus coverage matrix

The machine-readable source and derivative records are `assets.json`,
`subsets.json`, and `process/validation/evidence/p00/corpus.json`.

| Dimension | Evidence | Remaining gap |
| --- | --- | --- |
| Optical / RGB / compute | Sentinel-2 B02/B03/B04/B08, uint16, Venice lagoon and recognizable coastline | None for initial corpus |
| Continuous native float | Copernicus DEM, float32, Dolomites | No native invalid float pixels in this selection |
| Categorical | WorldCover 2021 v200, uint8 classes 10–100 with published product semantics | None for initial corpus |
| Native projected CRS | Sentinel-2 EPSG:32632 at 10 m | None |
| Native geographic CRS | DEM and WorldCover EPSG:4326 at different resolutions | None |
| Validity / footprint | Actual Sentinel-2 nodata footprint edge and a derived internal-mask copy of the same real pixels | Native alpha is absent; derived mask is explicitly labeled |
| Valid zero | DEM subset has valid values and no nodata, but does not happen to contain zero | A retained valid-zero raster case is still needed before full mask qualification |
| Parent storage | Three unaltered tiled DEFLATE COG products with 1024 blocks and publisher overviews | Additional native codecs and alpha are later coverage |
| Fast tier | Seven 1024–2048 pixel windows; 30.2 MB combined | Derived ordinary TIFFs do not prove COG layout |
| Stress tier | Full WorldCover 36,000 × 36,000 COG; 85,040,674 file bytes; 1.296 GB values; 5.184 GB float32 work estimate | Classic TIFF, no offsets beyond 4 GiB; physical multi-GiB BigTIFF remains open |
| Access | Full local cached parents; pinned HTTPS URLs and immutable identity metadata | Local range server and real remote range measurements are P02 |

The source support matrix found in the frozen Python project is Linux through
pip CI plus macOS and Windows through conda CI, on Python 3.10 and 3.14; its
package classifiers also name Unix, macOS, and Windows 10. For the rewrite,
P00 establishes Linux x86_64 as locally built. P07 must qualify concrete
Linux/macOS/Windows architectures and packaging before C32 is verified.

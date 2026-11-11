# Reproduce the README animations

The navigation animation captures RCFP's installed, checksum-pinned samples in
its bundled Leaflet viewer, including real RGB and NDVI tile requests. Playback
uses edited screenshot durations and does not measure latency or browser paint.
The NDVI request explicitly selects NIR and red and promotes the UInt16 samples
with `1.0*v1` and `1.0*v2` before arithmetic. It uses a −1 to 1 stretch and the
viridis colormap.

Use a current installed RCFP wheel, Pillow, Playwright, and its Chromium browser.
The capture was made with Playwright 1.55.0 and Pillow 12.3.0 on Linux. The default
font directory contains the DejaVu Sans and Sans Mono TrueType files; pass
`--fonts` to use another directory containing those files.

From the repository root, in the environment containing RCFP and the capture
dependencies:

```sh
python -m playwright install chromium
python docs/tools/readme_media.py \
  --binary "$(command -v rcfp)" \
  --work build/readme-capture \
  --assets docs/static/readme
```

`--binary` must point to the native executable, or the installed `rcfp` launcher.
Use a fresh `--work` directory. The script starts two loopback services on available ports, blocks
external browser requests, checks source checksums and HTTP/JavaScript errors,
and stops both services when finished. It replaces the two README media files
in `--assets`. Review the animation and its PNG still before committing.

The work directory retains the command transcript, service logs, full viewer
screenshot, and a JSON report with source attribution/checksums, binary hash,
request count, navigation stages, and output hashes/sizes. Generated reports
and intermediate captures belong in ignored storage. These images are product
demonstrations, not independent rendering reference outputs.

The imagery contains modified Copernicus Sentinel data 2021. The sample manifest
also documents the ESA WorldCover layer supplied with the quickstart; it is not
shown in these animations. Source URLs, acquisition details, and attribution are
in [the sample manifest](../examples/data/manifest.json).

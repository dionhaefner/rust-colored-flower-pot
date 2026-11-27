# Reproduce the README and feature screenshots

The README walkthrough uses a full 10 m Sentinel-2 scene around Venice. It
captures wheel zooms centered on the pointer, short drag gestures, and the
switch from RGB to an NDVI expression. Playback timing is edited. The feature
images come from the runnable native demos, the Leaflet SDK example, the
rendered-publication example, and the native metrics endpoint.

Use a current RCFP wheel and matching native bundle with Pillow and Playwright.
The capture runs on Linux with Chromium. From the repository root:

```sh
python -m playwright install chromium
python docs/tools/readme_media.py \
  --binary dist/bundle/bin/rcfp \
  --work build/readme-capture \
  --assets docs/static/readme \
  --features docs/_static/native-demos
```

Use a fresh work directory. The script verifies the rasters against their
pinned checksums and starts two loopback servers. Pass `--sources PATH` to
reuse downloaded rasters named as in `tests/fixtures/navigation.json`.
Browser requests stay on loopback. Review the still, animation, and screenshots
before committing. The work directory contains sample frames, service logs,
and a source and response report.

Contains modified Copernicus Sentinel data 2021.

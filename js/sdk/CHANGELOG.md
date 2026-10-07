# Changelog

## Unreleased

- Type Python compute declarations, optional source inspection/statistics and
  declared model parameters in TileJSON requests.

- Pace Leaflet tile requests across view changes, discard removed tiles and retry
  temporary busy responses within a fixed bound.
- Add native tileset discovery, TileJSON manifests and source point values.
- Add TileJSON adapters for Leaflet, MapLibre and OpenLayers.

## 0.1.0

- Add typed discovery, rendering, metadata, legends, explicit time selection,
  display-pixel inspection, errors, cancellation and revisioned shared state.
- Add Leaflet, MapLibre and OpenLayers integrations without runtime dependencies.

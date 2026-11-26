// Bundled browser assets and MIME types.
pub fn asset(path: &str) -> Option<(&'static [u8], &'static str)> {
    match path {
        "/" => Some((
            include_bytes!("assets/client/templates/index.html"),
            "text/html; charset=utf-8",
        )),
        "/natural-earth-land.geojson" => Some((
            include_bytes!("assets/client/natural-earth-land.geojson"),
            "application/geo+json",
        )),
        "/examples/leaflet.html" => Some((
            include_bytes!("assets/client/examples/leaflet.html"),
            "text/html; charset=utf-8",
        )),
        "/examples/maplibre.html" => Some((
            include_bytes!("assets/client/examples/maplibre.html"),
            "text/html; charset=utf-8",
        )),
        "/examples/openlayers.html" => Some((
            include_bytes!("assets/client/examples/openlayers.html"),
            "text/html; charset=utf-8",
        )),
        "/sdk/CHANGELOG.md" => Some((include_bytes!("../js/sdk/CHANGELOG.md"), "text/plain")),
        "/sdk/LICENSE" => Some((include_bytes!("../js/sdk/LICENSE"), "text/plain")),
        "/sdk/README.md" => Some((include_bytes!("../js/sdk/README.md"), "text/plain")),
        "/sdk/index.d.ts" => Some((include_bytes!("../js/sdk/index.d.ts"), "text/plain")),
        "/sdk/index.js" => Some((include_bytes!("../js/sdk/index.js"), "text/javascript")),
        "/sdk/integrations.d.ts" => {
            Some((include_bytes!("../js/sdk/integrations.d.ts"), "text/plain"))
        }
        "/sdk/integrations.js" => Some((
            include_bytes!("../js/sdk/integrations.js"),
            "text/javascript",
        )),
        "/sdk/package.json" => Some((include_bytes!("../js/sdk/package.json"), "application/json")),
        "/vendor/leaflet-LICENSE.txt" => Some((
            include_bytes!("assets/client/vendor/leaflet-LICENSE.txt"),
            "text/plain",
        )),
        "/vendor/leaflet.css" => Some((
            include_bytes!("assets/client/vendor/leaflet.css"),
            "text/css",
        )),
        "/vendor/leaflet.js" => Some((
            include_bytes!("assets/client/vendor/leaflet.js"),
            "text/javascript",
        )),
        "/vendor/manifest.json" => Some((
            include_bytes!("assets/client/vendor/manifest.json"),
            "application/json",
        )),
        "/vendor/maplibre-LICENSE.txt" => Some((
            include_bytes!("assets/client/vendor/maplibre-LICENSE.txt"),
            "text/plain",
        )),
        "/vendor/maplibre-gl.css" => Some((
            include_bytes!("assets/client/vendor/maplibre-gl.css"),
            "text/css",
        )),
        "/vendor/maplibre-gl.js" => Some((
            include_bytes!("assets/client/vendor/maplibre-gl.js"),
            "text/javascript",
        )),
        "/vendor/ol-LICENSE.txt" => Some((
            include_bytes!("assets/client/vendor/ol-LICENSE.txt"),
            "text/plain",
        )),
        "/vendor/ol.css" => Some((include_bytes!("assets/client/vendor/ol.css"), "text/css")),
        "/vendor/ol.js" => Some((
            include_bytes!("assets/client/vendor/ol.js"),
            "text/javascript",
        )),
        "/viewer.css" => Some((include_bytes!("assets/client/viewer.css"), "text/css")),
        "/viewer.js" => Some((include_bytes!("assets/client/viewer.js"), "text/javascript")),
        "/viewer-connection.js" => Some((
            include_bytes!("assets/client/viewer-connection.js"),
            "text/javascript",
        )),
        _ => None,
    }
}

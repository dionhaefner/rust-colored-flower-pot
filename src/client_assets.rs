// Bundled browser assets and MIME types.
pub fn asset(path: &str) -> Option<(&'static [u8], &'static str)> {
    match path {
        "/static/react/asset-manifest.json" => Some((
            include_bytes!("assets/client/static/react/asset-manifest.json"),
            "application/json",
        )),
        "/static/react/favicon.ico" => Some((
            include_bytes!("assets/client/static/react/favicon.ico"),
            "image/x-icon",
        )),
        "/static/react/logo192.png" => Some((
            include_bytes!("assets/client/static/react/logo192.png"),
            "image/png",
        )),
        "/static/react/logo512.png" => Some((
            include_bytes!("assets/client/static/react/logo512.png"),
            "image/png",
        )),
        "/static/react/manifest.json" => Some((
            include_bytes!("assets/client/static/react/manifest.json"),
            "application/json",
        )),
        "/static/react/robots.txt" => Some((
            include_bytes!("assets/client/static/react/robots.txt"),
            "application/octet-stream",
        )),
        "/static/react/static/css/2.19072978.chunk.css" => Some((
            include_bytes!("assets/client/static/react/static/css/2.19072978.chunk.css"),
            "text/css",
        )),
        "/static/react/static/js/2.83c1f4db.chunk.js" => Some((
            include_bytes!("assets/client/static/react/static/js/2.83c1f4db.chunk.js"),
            "application/javascript",
        )),
        "/static/react/static/js/2.83c1f4db.chunk.js.LICENSE.txt" => Some((
            include_bytes!("assets/client/static/react/static/js/2.83c1f4db.chunk.js.LICENSE.txt"),
            "application/octet-stream",
        )),
        "/static/react/static/js/main.9ed63b1f.chunk.js" => Some((
            include_bytes!("assets/client/static/react/static/js/main.9ed63b1f.chunk.js"),
            "application/javascript",
        )),
        "/static/react/static/js/runtime-main.b2f07bb0.js" => Some((
            include_bytes!("assets/client/static/react/static/js/runtime-main.b2f07bb0.js"),
            "application/javascript",
        )),
        "/static/react/static/media/header.87193822.svg" => Some((
            include_bytes!("assets/client/static/react/static/media/header.87193822.svg"),
            "image/svg+xml",
        )),
        _ => None,
    }
}

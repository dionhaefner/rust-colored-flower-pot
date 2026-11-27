//! Open the browser explorer or serve its assets on loopback.
use crate::Result;
use std::process::Stdio;
use tiny_http::{Header, Response, Server, StatusCode};
#[path = "client_assets.rs"]
mod assets;

pub const HOSTED_ORIGIN: &str = "https://dionhaefner.github.io";
const HOSTED_VIEWER: &str = "https://dionhaefner.github.io/rust-colored-flower-pot/explore/";
pub fn command(args: &[String]) -> Result<()> {
    let mut host = None;
    let mut port = None;
    let mut local = false;
    let mut browser = true;
    let mut source_revision = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--help" | "-h" => {
                println!(
                    "Usage: rcfp connect [HOSTNAME] [--local] [--no-browser] [--port PORT] [--source-revision REVISION]"
                );
                return Ok(());
            }
            "--no-browser" => browser = false,
            "--local" => local = true,
            "--source-revision" if i + 1 < args.len() => {
                i += 1;
                let revision = &args[i];
                if revision.trim().is_empty() || revision.len() > 256 {
                    return Err("source revision must contain 1 to 256 bytes".into());
                }
                source_revision = Some(revision.as_str());
            }
            "--port" if i + 1 < args.len() => {
                i += 1;
                port = Some(args[i].parse::<u16>().map_err(|_| "invalid port")?);
            }
            s if !s.starts_with('-') && host.is_none() => host = Some(s.to_string()),
            s => return Err(format!("unsupported connect option: {s}")),
        }
        i += 1;
    }
    let Some(host) = host else {
        if local || port.is_some() {
            return Err("a bundled viewer requires a tile server hostname".into());
        }
        launch(hosted_viewer()?, source_revision, browser);
        return Ok(());
    };
    let explicit_scheme = host.contains("://");
    let mut url = clean_url(&if explicit_scheme {
        host
    } else {
        format!("http://{host}")
    })?;
    let loopback = match url.host() {
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        Some(url::Host::Domain(name)) => {
            let name = name.trim_end_matches('.');
            name == "localhost" || name.ends_with(".localhost")
        }
        None => false,
    };
    if !explicit_scheme && !loopback && port.is_none() {
        url.set_scheme("https")
            .map_err(|_| "invalid service scheme")?;
    }
    let host = url.as_str().trim_end_matches('/');
    let port = port.or_else(|| (local && !loopback).then_some(5100));
    let Some(port) = port else {
        let viewer = if local {
            let mut viewer = url.clone();
            viewer.set_path(&format!("{}/explore/", url.path().trim_end_matches('/')));
            viewer
        } else {
            let mut viewer = hosted_viewer()?;
            viewer.query_pairs_mut().append_pair("service", host);
            viewer
        };
        launch(viewer, source_revision, browser);
        return Ok(());
    };
    let server = Server::http(("127.0.0.1", port))
        .map_err(|error| format!("Could not bind local viewer on port {port}: {error}"))?;
    if std::env::var_os("TC_TESTING").is_some() {
        return Ok(());
    }
    let viewer = format!("http://{}/", server.server_addr());
    eprintln!("RCFP browser listening on {viewer}");
    if browser {
        open_browser(&viewer);
    }
    for request in server.incoming_requests() {
        let path = request.url().split('?').next().unwrap_or("/");
        let (status, mime, data) =
            if request.method().as_str() != "GET" && request.method().as_str() != "HEAD" {
                (405, "text/plain", Vec::new())
            } else if let Some((data, mime)) = viewer_asset(path, host, "", source_revision) {
                (200, mime, data)
            } else {
                (404, "text/plain", b"Not Found".to_vec())
            };
        let mut response = Response::from_data(data)
            .with_status_code(StatusCode(status))
            .with_header(Header::from_bytes("Content-Type", mime).unwrap());
        if path.starts_with("/sdk/") && status == 200 {
            response.add_header(Header::from_bytes("Access-Control-Allow-Origin", "*").unwrap());
        }
        let _ = request.respond(response);
    }
    Ok(())
}

fn hosted_viewer() -> Result<url::Url> {
    let override_url = std::env::var("RCFP_VIEWER_URL").ok();
    clean_url(override_url.as_deref().unwrap_or(HOSTED_VIEWER))
}

fn launch(mut viewer: url::Url, source_revision: Option<&str>, browser: bool) {
    if let Some(revision) = source_revision {
        viewer
            .query_pairs_mut()
            .append_pair("source-revision", revision);
    }
    println!("{viewer}");
    if browser && std::env::var_os("TC_TESTING").is_none() {
        open_browser(viewer.as_str());
    }
}

fn clean_url(value: &str) -> Result<url::Url> {
    let url = url::Url::parse(value).map_err(|_| "invalid HTTP or HTTPS URL")?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("connect requires HTTP or HTTPS".into());
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("connect URL must not contain credentials, a query or fragment".into());
    }
    Ok(url)
}

fn open_browser(url: &str) {
    #[cfg(target_os = "linux")]
    let command = "xdg-open";
    #[cfg(target_os = "macos")]
    let command = "open";
    #[cfg(target_os = "windows")]
    let command = "explorer";
    let _ = std::process::Command::new(command)
        .arg(url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

pub(crate) fn viewer_asset(
    path: &str,
    service: &str,
    profile: &str,
    source_revision: Option<&str>,
) -> Option<(Vec<u8>, &'static str)> {
    let (asset, mime) = assets::asset(path)?;
    let data = if mime.starts_with("text/html") {
        let escape = |value: &str| {
            value
                .replace('&', "&amp;")
                .replace('<', "&lt;")
                .replace('>', "&gt;")
                .replace('"', "&quot;")
                .replace('\'', "&#39;")
        };
        let service = if service.is_empty() && path.starts_with("/examples/") {
            "../../"
        } else if service.is_empty() {
            "../"
        } else {
            service
        };
        std::str::from_utf8(asset)
            .expect("viewer template is UTF-8")
            .replace("{{ hostname }}", &escape(service))
            .replace("{{ profile }}", profile)
            .replace(
                "{{ source_revision }}",
                &escape(source_revision.unwrap_or("unversioned")),
            )
            .into_bytes()
    } else {
        asset.to_vec()
    };
    Some((data, mime))
}

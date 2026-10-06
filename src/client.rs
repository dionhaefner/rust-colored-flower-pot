//! Serve the bundled browser client and verify its backend protocol.
use crate::Result;
use std::time::Duration;
use tiny_http::{Header, Response, Server, StatusCode};
#[path = "client_assets.rs"]
mod assets;
pub fn command(args: &[String]) -> Result<()> {
    let mut host = None;
    let mut port = None;
    let mut browser = true;
    let mut source_revision = "unversioned".to_string();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--help" | "-h" => {
                println!(
                    "Usage: rcfp connect HOSTNAME [--no-browser] [--port PORT] [--source-revision REVISION]"
                );
                return Ok(());
            }
            "--no-browser" => browser = false,
            "--source-revision" if i + 1 < args.len() => {
                i += 1;
                source_revision = args[i].clone();
                if source_revision.trim().is_empty() || source_revision.len() > 256 {
                    return Err("source revision must contain 1 to 256 bytes".into());
                }
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
    let host = host.ok_or("connect requires a hostname")?;
    let mut url = url::Url::parse(&if host.contains("://") {
        host
    } else {
        format!("http://{host}")
    })
    .map_err(|_| "invalid hostname")?;
    if !["http", "https"].contains(&url.scheme()) {
        return Err("connect requires HTTP or HTTPS".into());
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("connect URL must not contain credentials, a query or fragment".into());
    }
    url.set_query(None);
    url.set_fragment(None);
    let host = url.to_string().trim_end_matches('/').to_string();
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(5))
        .build();
    let identity = agent
        .get(&format!("{host}/__terracotta/identity"))
        .call()
        .map_err(|_| format!("Could not connect to {host}, check hostname and ensure that RCFP is running on the server"))?
        .into_string()
        .map_err(|e| e.to_string())?;
    let identity: serde_json::Value = serde_json::from_str(&identity).map_err(|e| e.to_string())?;
    let profile = identity["compatibility_profile"]
        .as_str()
        .ok_or("server does not advertise its compatibility profile")?;
    crate::compatibility::CompatibilityProfile::parse(profile)?;
    let spec = agent
        .get(&format!("{host}/swagger.json"))
        .call()
        .map_err(|e| e.to_string())?
        .into_string()
        .map_err(|e| e.to_string())?;
    let spec: serde_json::Value = serde_json::from_str(&spec).map_err(|e| e.to_string())?;
    let version = spec["info"]["version"]
        .as_str()
        .ok_or("invalid server specification")?;
    if version
        .split('.')
        .take(2)
        .ne(crate::TERRACOTTA_COMPATIBILITY_VERSION.split('.').take(2))
    {
        return Err(format!(
            "Incompatible version: The server running at {host} is using Terracotta v{version}, but this is v{}",
            crate::TERRACOTTA_COMPATIBILITY_VERSION
        ));
    }
    let ports: Vec<_> = port
        .map(|p| vec![p])
        .unwrap_or_else(|| (5100..5200).collect());
    let (server, port) = ports
        .iter()
        .find_map(|p| Server::http(("127.0.0.1", *p)).ok().map(|s| (s, *p)))
        .ok_or_else(|| format!("Could not find open port to bind to (ports tried: {ports:?})"))?;
    if std::env::var_os("TC_TESTING").is_some() {
        return Ok(());
    }
    eprintln!("RCFP browser listening on http://127.0.0.1:{port}/");
    if browser {
        let url = format!("http://127.0.0.1:{port}/");
        #[cfg(target_os = "linux")]
        let command = "xdg-open";
        #[cfg(target_os = "macos")]
        let command = "open";
        #[cfg(target_os = "windows")]
        let command = "explorer";
        let _ = std::process::Command::new(command).arg(url).spawn();
    }
    let escape = |value: &str| {
        value
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
            .replace('"', "&quot;")
            .replace('\'', "&#39;")
    };
    let index = include_str!("assets/client/templates/index.html")
        .replace("{{ hostname }}", &escape(&host))
        .replace("{{ profile }}", &escape(profile))
        .replace("{{ source_revision }}", &escape(&source_revision));
    for request in server.incoming_requests() {
        let path = request.url().split('?').next().unwrap_or("/");
        let (status, mime, data) =
            if request.method().as_str() != "GET" && request.method().as_str() != "HEAD" {
                (405, "text/plain", Vec::new())
            } else if path == "/" {
                (200, "text/html; charset=utf-8", index.as_bytes().to_vec())
            } else if let Some((data, mime)) = assets::asset(path) {
                let data = if mime.starts_with("text/html") {
                    String::from_utf8_lossy(data)
                        .replace("{{ hostname }}", &escape(&host))
                        .replace("{{ profile }}", &escape(profile))
                        .into_bytes()
                } else {
                    data.to_vec()
                };
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

//! Bounded HTTP/1.1 socket admission and request parsing.
use super::{AppResponse, Application, json_error};
use crate::Result;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::mpsc::{self, TrySendError};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const MAX_HEADERS: usize = 16 * 1024;
const MAX_BODY: usize = 1024 * 1024;
const READ_DEADLINE: Duration = Duration::from_secs(5);

struct ParsedRequest {
    method: String,
    url: String,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

pub(super) fn serve(application: Application, address: &str) -> Result<()> {
    let listener =
        TcpListener::bind(address).map_err(|error| format!("could not bind {address}: {error}"))?;
    let workers = application.http_workers;
    let queue = application.http_queue_capacity;
    let application = Arc::new(application);
    let (sender, receiver) = mpsc::sync_channel(queue);
    let receiver = Arc::new(Mutex::new(receiver));
    for worker_id in 0..workers {
        let application = application.clone();
        let receiver = receiver.clone();
        std::thread::Builder::new()
            .name(format!("terracotta-http-{worker_id}"))
            .spawn(move || {
                loop {
                    let socket = {
                        let receiver = receiver.lock().expect("HTTP receiver lock");
                        receiver.recv()
                    };
                    let Ok(socket) = socket else { return };
                    handle(socket, &application);
                }
            })
            .map_err(|error| format!("could not start HTTP worker: {error}"))?;
    }
    eprintln!(
        "RCFP listening on http://{address} (HTTP workers {}, raster workers {}, queue {})",
        workers,
        application.executor.metrics().workers,
        queue
    );
    for socket in listener.incoming() {
        let socket = socket.map_err(|error| format!("HTTP accept failed: {error}"))?;
        match sender.try_send(socket) {
            Ok(()) => {}
            Err(TrySendError::Full(mut socket)) => {
                let _ = socket.set_write_timeout(Some(Duration::from_secs(1)));
                let _ = write_response(&mut socket, json_error(503, "HTTP request queue is full"));
            }
            Err(TrySendError::Disconnected(mut socket)) => {
                let _ =
                    write_response(&mut socket, json_error(503, "HTTP workers are unavailable"));
                return Err("HTTP worker pool stopped".into());
            }
        }
    }
    Ok(())
}

fn handle(mut socket: TcpStream, application: &Application) {
    let _ = socket.set_write_timeout(Some(Duration::from_secs(5)));
    let response = match read_request(&mut socket) {
        Ok(request) => {
            let tile = request.url.starts_with("/singleband/") || request.url.starts_with("/rgb/");
            let response = application.request_full(
                &request.method,
                &request.url,
                &request.headers,
                &request.body,
            );
            if tile && response.status == 200 {
                application.executor.wake_prefetch();
            }
            response
        }
        Err(status) => json_error(
            status,
            match status {
                408 => "Request timed out",
                413 => "Request body or headers are too large",
                417 => "Unsupported expectation",
                501 => "Unsupported transfer encoding",
                _ => "Malformed HTTP request",
            },
        ),
    };
    let _ = write_response(&mut socket, response);
}

fn read_request(socket: &mut TcpStream) -> std::result::Result<ParsedRequest, u16> {
    let deadline = Instant::now() + READ_DEADLINE;
    let mut bytes = Vec::with_capacity(1024);
    let end = loop {
        if let Some(end) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
            break end + 4;
        }
        if bytes.len() >= MAX_HEADERS {
            return Err(413);
        }
        read_more(socket, &mut bytes, deadline, MAX_HEADERS + MAX_BODY)?;
    };
    if end > MAX_HEADERS {
        return Err(413);
    }
    let text = std::str::from_utf8(&bytes[..end]).map_err(|_| 400u16)?;
    let mut lines = text[..end - 4].split("\r\n");
    let mut parts = lines.next().ok_or(400u16)?.split(' ');
    let method = parts.next().ok_or(400u16)?;
    let url = parts.next().ok_or(400u16)?;
    let version = parts.next().ok_or(400u16)?;
    if parts.next().is_some()
        || method.is_empty()
        || url.is_empty()
        || url.len() > 8192
        || !url.starts_with('/')
        || !matches!(version, "HTTP/1.0" | "HTTP/1.1")
        || !method.bytes().all(|c| c.is_ascii_uppercase())
        || url.bytes().any(|c| c.is_ascii_control())
    {
        return Err(400);
    }
    let mut headers = BTreeMap::new();
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(400u16)?;
        if name.is_empty()
            || !name
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"!#$%&'*+-.^_|~".contains(&c))
            || value.bytes().any(|c| c.is_ascii_control() && c != b'\t')
        {
            return Err(400);
        }
        if headers
            .insert(name.to_ascii_lowercase(), value.trim().to_owned())
            .is_some()
        {
            return Err(400);
        }
    }
    if version == "HTTP/1.1" && !headers.contains_key("host") {
        return Err(400);
    }
    if headers.contains_key("transfer-encoding") {
        return Err(501);
    }
    if headers.contains_key("expect") {
        return Err(417);
    }
    let body_len = match headers.get("content-length") {
        Some(value) if !value.is_empty() && value.bytes().all(|c| c.is_ascii_digit()) => {
            value.parse::<usize>().map_err(|_| 413u16)?
        }
        Some(_) => return Err(400),
        None => 0,
    };
    if body_len > MAX_BODY {
        return Err(413);
    }
    let method = method.to_owned();
    let url = url.to_owned();
    while bytes.len() - end < body_len {
        read_more(socket, &mut bytes, deadline, MAX_HEADERS + MAX_BODY)?;
    }
    Ok(ParsedRequest {
        method,
        url,
        headers,
        body: bytes[end..end + body_len].to_vec(),
    })
}

fn read_more(
    socket: &mut TcpStream,
    bytes: &mut Vec<u8>,
    deadline: Instant,
    cap: usize,
) -> std::result::Result<(), u16> {
    let remaining = deadline
        .checked_duration_since(Instant::now())
        .ok_or(408u16)?;
    socket
        .set_read_timeout(Some(remaining.min(Duration::from_secs(1))))
        .map_err(|_| 400u16)?;
    let mut chunk = [0u8; 4096];
    match socket.read(&mut chunk) {
        Ok(0) => Err(400),
        Ok(n) => {
            if bytes.len().saturating_add(n) > cap {
                return Err(413);
            }
            bytes.extend_from_slice(&chunk[..n]);
            Ok(())
        }
        Err(error)
            if error.kind() == std::io::ErrorKind::TimedOut
                || error.kind() == std::io::ErrorKind::WouldBlock =>
        {
            if Instant::now() >= deadline {
                Err(408)
            } else {
                Ok(())
            }
        }
        Err(_) => Err(400),
    }
}

fn write_response(socket: &mut TcpStream, response: AppResponse) -> std::io::Result<()> {
    let reason = match response.status {
        200 => "OK",
        201 => "Created",
        204 => "No Content",
        301 => "Moved Permanently",
        302 => "Found",
        304 => "Not Modified",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        413 => "Payload Too Large",
        415 => "Unsupported Media Type",
        417 => "Expectation Failed",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "Response",
    };
    let content_length = response
        .headers
        .get("Content-Length")
        .and_then(|length| length.parse::<usize>().ok())
        .unwrap_or(response.body.len());
    let mut head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n",
        response.status, reason, response.content_type, content_length
    );
    if response.cache {
        head.push_str("Cache-Control: no-cache\r\n");
    }
    for (name, value) in response.headers {
        if [
            "content-length",
            "connection",
            "content-type",
            "cache-control",
        ]
        .iter()
        .any(|reserved| name.eq_ignore_ascii_case(reserved))
        {
            continue;
        }
        if name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
            && !value.bytes().any(|c| c == b'\r' || c == b'\n' || c == 0)
        {
            head.push_str(&format!("{name}: {value}\r\n"));
        }
    }
    head.push_str("\r\n");
    socket.write_all(head.as_bytes())?;
    socket.write_all(&response.body)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_oversized_headers_before_parsing() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let sender = std::thread::spawn(move || {
            let mut socket = TcpStream::connect(address).unwrap();
            let mut request = b"GET /keys HTTP/1.1\r\nHost: localhost\r\nX-Big: ".to_vec();
            request.extend(vec![b'x'; MAX_HEADERS]);
            let _ = socket.write_all(&request);
        });
        let (mut socket, _) = listener.accept().unwrap();
        assert!(matches!(read_request(&mut socket), Err(413)));
        sender.join().unwrap();
    }
    #[test]
    fn rejects_duplicate_content_length() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let sender = std::thread::spawn(move || {
            let mut socket = TcpStream::connect(address).unwrap();
            socket.write_all(b"POST /metadata HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nContent-Length: 4\r\n\r\nxx").unwrap();
        });
        let (mut socket, _) = listener.accept().unwrap();
        assert!(matches!(read_request(&mut socket), Err(400)));
        sender.join().unwrap();
    }
}

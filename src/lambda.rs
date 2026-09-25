//! AWS Lambda custom-runtime and API Gateway event adapter.

use crate::Result;
use crate::http::Application;
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;

const RUNTIME_VERSION: &str = "2018-06-01";

pub fn handle_event(database: &Path, event: &Value) -> Result<Value> {
    let application = Application::open(database)?;
    handle_event_with_application(&application, event)
}

pub fn handle_event_with_application(application: &Application, event: &Value) -> Result<Value> {
    let method = event
        .pointer("/requestContext/http/method")
        .or_else(|| event.get("httpMethod"))
        .and_then(Value::as_str)
        .unwrap_or("GET");
    let path = event
        .get("rawPath")
        .or_else(|| event.get("path"))
        .and_then(Value::as_str)
        .unwrap_or("/");
    let query = event
        .get("rawQueryString")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .unwrap_or_else(|| v1_query_string(event));
    let url = if query.is_empty() {
        path.to_owned()
    } else {
        format!("{path}?{query}")
    };
    let incoming_headers: BTreeMap<String, String> = event
        .get("headers")
        .and_then(Value::as_object)
        .map(|h| {
            h.iter()
                .filter_map(|(k, v)| Some((k.to_ascii_lowercase(), v.as_str()?.to_owned())))
                .collect()
        })
        .unwrap_or_default();
    let body = event.get("body").and_then(Value::as_str).unwrap_or("");
    let body = if event.get("isBase64Encoded").and_then(Value::as_bool) == Some(true) {
        base64_decode(body)?
    } else {
        body.as_bytes().to_vec()
    };
    let response = application.request_full(method, &url, &incoming_headers, &body);
    let binary = response.content_type == "image/png";
    let body = if binary {
        base64_encode(&response.body)
    } else {
        String::from_utf8(response.body)
            .map_err(|_| "non-image adapter response was not UTF-8".to_string())?
    };
    let mut headers: Map<String, Value> = response
        .headers
        .into_iter()
        .map(|(k, v)| (k.to_ascii_lowercase(), json!(v)))
        .collect();
    headers.insert(
        "content-type".into(),
        Value::String(response.content_type.into()),
    );
    if response.cache {
        headers.insert("cache-control".into(), Value::String("no-cache".into()));
    }
    Ok(json!({
        "statusCode": response.status,
        "headers": headers,
        "body": body,
        "isBase64Encoded": binary
    }))
}

pub fn run_runtime(database: &Path, runtime_api: &str) -> Result<()> {
    let application = Application::open(database)?;
    loop {
        let invocation = runtime_request(
            runtime_api,
            "GET",
            &format!("/{RUNTIME_VERSION}/runtime/invocation/next"),
            &[],
        )?;
        if invocation.status != 200 {
            return Err(format!(
                "Lambda runtime next returned {}",
                invocation.status
            ));
        }
        let request_id = invocation
            .headers
            .get("lambda-runtime-aws-request-id")
            .ok_or_else(|| "Lambda runtime response omitted request id".to_string())?;
        let result = serde_json::from_slice::<Value>(&invocation.body)
            .map_err(|error| format!("invalid Lambda event JSON: {error}"))
            .and_then(|event| handle_event_with_application(&application, &event));
        let (suffix, body) = match result {
            Ok(response) => (
                "response",
                serde_json::to_vec(&response).map_err(|error| error.to_string())?,
            ),
            Err(error) => (
                "error",
                serde_json::to_vec(&json!({
                    "errorMessage": error,
                    "errorType": "TerracottaRuntimeError"
                }))
                .map_err(|encode_error| encode_error.to_string())?,
            ),
        };
        let response = runtime_request(
            runtime_api,
            "POST",
            &format!("/{RUNTIME_VERSION}/runtime/invocation/{request_id}/{suffix}"),
            &body,
        )?;
        if !(200..300).contains(&response.status) {
            return Err(format!(
                "Lambda runtime result returned {}",
                response.status
            ));
        }
    }
}

fn v1_query_string(event: &Value) -> String {
    if let Some(values) = event
        .get("multiValueQueryStringParameters")
        .and_then(Value::as_object)
    {
        let mut pairs = Vec::new();
        let ordered = values.iter().collect::<BTreeMap<_, _>>();
        for (key, values) in ordered {
            if let Some(values) = values.as_array() {
                for value in values.iter().filter_map(Value::as_str) {
                    pairs.push(format!("{}={}", url_encode(key), url_encode(value)));
                }
            }
        }
        return pairs.join("&");
    }
    let Some(values) = event
        .get("queryStringParameters")
        .and_then(Value::as_object)
    else {
        return String::new();
    };
    values
        .iter()
        .collect::<BTreeMap<_, _>>()
        .into_iter()
        .filter_map(|(key, value)| {
            value
                .as_str()
                .map(|value| format!("{}={}", url_encode(key), url_encode(value)))
        })
        .collect::<Vec<_>>()
        .join("&")
}

fn url_encode(value: &str) -> String {
    let mut output = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            output.push(byte as char);
        } else {
            output.push_str(&format!("%{byte:02X}"));
        }
    }
    output
}

fn base64_encode(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let value = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        output.push(TABLE[((value >> 18) & 63) as usize] as char);
        output.push(TABLE[((value >> 12) & 63) as usize] as char);
        output.push(if chunk.len() > 1 {
            TABLE[((value >> 6) & 63) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(value & 63) as usize] as char
        } else {
            '='
        });
    }
    output
}

struct RuntimeResponse {
    status: u16,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

fn runtime_request(host: &str, method: &str, path: &str, body: &[u8]) -> Result<RuntimeResponse> {
    let mut stream = TcpStream::connect(host)
        .map_err(|error| format!("could not connect to Lambda runtime API {host}: {error}"))?;
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(request.as_bytes())
        .and_then(|_| stream.write_all(body))
        .map_err(|error| format!("could not write Lambda runtime request: {error}"))?;
    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| format!("could not read Lambda runtime response: {error}"))?;
    let boundary = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| "invalid Lambda runtime HTTP response".to_string())?;
    let head = std::str::from_utf8(&response[..boundary])
        .map_err(|_| "Lambda runtime response headers were not UTF-8".to_string())?;
    let mut lines = head.split("\r\n");
    let status = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|status| status.parse().ok())
        .ok_or_else(|| "Lambda runtime response had no status".to_string())?;
    let headers = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_owned()))
        .collect();
    Ok(RuntimeResponse {
        status,
        headers,
        body: response[boundary + 4..].to_vec(),
    })
}

fn base64_decode(input: &str) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut buffer = 0u32;
    let mut bits = 0;
    for b in input.bytes().take_while(|b| *b != b'=') {
        let value = match b {
            b'A'..=b'Z' => b - b'A',
            b'a'..=b'z' => b - b'a' + 26,
            b'0'..=b'9' => b - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return Err("invalid base64 event body".into()),
        };
        buffer = (buffer << 6) | value as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            output.push((buffer >> bits) as u8);
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_rfc_examples() {
        assert_eq!(base64_encode(b""), "");
        assert_eq!(base64_encode(b"f"), "Zg==");
        assert_eq!(base64_encode(b"fo"), "Zm8=");
        assert_eq!(base64_encode(b"foo"), "Zm9v");
    }

    #[test]
    fn builds_stable_v1_queries() {
        let event = json!({"queryStringParameters": {"b": "two words", "a": "1"}});
        assert_eq!(v1_query_string(&event), "a=1&b=two%20words");
    }
}

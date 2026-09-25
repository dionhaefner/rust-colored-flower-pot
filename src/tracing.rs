//! X-Ray daemon subsegments for native requests. No AWS service API calls.
use serde_json::json;
use std::{
    collections::BTreeMap,
    sync::atomic::{AtomicU64, Ordering},
    time::SystemTime,
};
static NEXT: AtomicU64 = AtomicU64::new(1);
pub fn request(
    headers: &BTreeMap<String, String>,
    method: &str,
    path: &str,
    status: u16,
    start: SystemTime,
) {
    if !crate::settings::var("TC_XRAY_PROFILE").is_ok_and(|s| s == "true") {
        return;
    }
    let header = headers
        .get("x-amzn-trace-id")
        .cloned()
        .or_else(|| std::env::var("_X_AMZN_TRACE_ID").ok())
        .unwrap_or_default();
    let fields: BTreeMap<_, _> = header
        .split(';')
        .filter_map(|s| s.trim().split_once('='))
        .collect();
    if fields.get("Sampled") != Some(&"1") {
        return;
    }
    let (Some(root), Some(parent)) = (fields.get("Root"), fields.get("Parent")) else {
        return;
    };
    if root.len() != 35 || parent.len() != 16 || !parent.chars().all(|c| c.is_ascii_hexdigit()) {
        return;
    }
    let seconds = |t: SystemTime| {
        t.duration_since(SystemTime::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64()
    };
    let now = SystemTime::now();
    let id = now
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
        ^ NEXT.fetch_add(1, Ordering::Relaxed);
    let segment = json!({"name":"terracotta", "type":"subsegment", "id":format!("{id:016x}"), "trace_id":root,"parent_id":parent,
        "start_time":seconds(start),"end_time":seconds(now),"http":{"request":{"method":method,"url":path},"response":{"status":status}},"fault":status>=500,"error":(400..500).contains(&status)});
    let address =
        std::env::var("AWS_XRAY_DAEMON_ADDRESS").unwrap_or_else(|_| "127.0.0.1:2000".into());
    let address = address
        .split_whitespace()
        .find_map(|s| s.strip_prefix("udp:"))
        .unwrap_or(&address);
    match std::net::UdpSocket::bind("0.0.0.0:0").and_then(|s| {
        s.send_to(
            format!("{{\"format\":\"json\",\"version\":1}}\n{segment}").as_bytes(),
            address,
        )
    }) {
        Ok(_) => {}
        Err(error) => eprintln!("warning: X-Ray daemon send failed: {error}"),
    }
}

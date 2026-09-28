//! Socket-based integration executable: run separately with local socket permission.
use rcfp_core::storage::Store;
use std::{
    io::{Read, Write},
    net::TcpListener,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::Duration,
};
#[test]
fn leases_failed_refresh_and_signing() {
    let root = std::env::temp_dir().join(format!("tc-p06-lease-{}", std::process::id()));
    std::fs::create_dir_all(&root).unwrap();
    let cache = root.join("cache");
    std::fs::create_dir(&cache).unwrap();
    let credentials = root.join("credentials");
    std::fs::write(&credentials,"[lease]\naws_access_key_id=lease-key\naws_secret_access_key=lease-secret\naws_session_token=lease-token\n").unwrap();
    let config = root.join("config");
    std::fs::write(&config, "[profile lease]\nregion=eu-west-1\n").unwrap();
    // Isolated integration executable; configure before spawning threads.
    unsafe {
        for key in [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
        ] {
            std::env::remove_var(key);
        }
        std::env::set_var("AWS_PROFILE", "lease");
        std::env::set_var("AWS_SHARED_CREDENTIALS_FILE", &credentials);
        std::env::set_var("AWS_CONFIG_FILE", &config);
        std::env::set_var("TC_REMOTE_DB_CACHE_TTL", "1");
        std::env::set_var("TC_REMOTE_DB_CACHE_DIR", &cache);
    }
    let bytes =
        std::fs::read("validation/artifacts/capture-reproduction/reference.sqlite").unwrap();
    let response = Arc::new(Mutex::new(bytes));
    let server = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}", server.local_addr().unwrap());
    unsafe {
        std::env::set_var("AWS_ENDPOINT_URL_S3", &endpoint);
    }
    server.set_nonblocking(true).unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let count = Arc::new(AtomicUsize::new(0));
    let headers = Arc::new(Mutex::new(Vec::new()));
    let handle = {
        let response = response.clone();
        let stop = stop.clone();
        let count = count.clone();
        let headers = headers.clone();
        thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                match server.accept() {
                    Ok((mut stream, _)) => {
                        stream
                            .set_read_timeout(Some(Duration::from_secs(3)))
                            .unwrap();
                        let mut data = Vec::new();
                        let mut b = [0; 1024];
                        while !data.ends_with(b"\r\n\r\n") {
                            let n = stream.read(&mut b).unwrap();
                            if n == 0 {
                                break;
                            }
                            data.extend_from_slice(&b[..n]);
                        }
                        headers
                            .lock()
                            .unwrap()
                            .push(String::from_utf8(data).unwrap());
                        count.fetch_add(1, Ordering::Relaxed);
                        let bytes = response.lock().unwrap().clone();
                        write!(
                            stream,
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            bytes.len()
                        )
                        .unwrap();
                        stream.write_all(&bytes).unwrap();
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2))
                    }
                    Err(e) => panic!("{e}"),
                }
            }
        })
    };
    let mut a = Store::open("s3://lease-bucket/data.sqlite").unwrap();
    let b = Store::open("s3://lease-bucket/data.sqlite").unwrap();
    assert_eq!(count.load(Ordering::Relaxed), 1);
    let old = std::fs::read_dir(&cache)
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    std::fs::write(&credentials,"[lease]\naws_access_key_id=rotated-key\naws_secret_access_key=rotated-secret\naws_session_token=rotated-token\n").unwrap();
    thread::sleep(Duration::from_millis(1100));
    let mut callers = Vec::new();
    for _ in 0..8 {
        callers.push(thread::spawn(|| {
            let s = Store::open("s3://lease-bucket/data.sqlite").unwrap();
            assert_eq!(s.datasets().unwrap().len(), 7);
        }));
    }
    for h in callers {
        h.join().unwrap();
    }
    assert_eq!(count.load(Ordering::Relaxed), 2);
    assert!(old.exists());
    assert_eq!(b.datasets().unwrap().len(), 7);
    a.refresh().unwrap();
    drop(b);
    assert!(!old.exists());
    assert_eq!(std::fs::read_dir(&cache).unwrap().count(), 1);
    *response.lock().unwrap() = b"truncated sqlite".to_vec();
    thread::sleep(Duration::from_millis(1100));
    assert!(a.refresh().is_err());
    assert_eq!(a.datasets().unwrap().len(), 7);
    assert_eq!(std::fs::read_dir(&cache).unwrap().count(), 1);
    let h = headers.lock().unwrap();
    assert!(
        h.iter()
            .all(|s| s.contains("AWS4-HMAC-SHA256") && s.contains("/eu-west-1/s3/aws4_request"))
    );
    assert!(
        h[0].contains("Credential=lease-key/")
            && h[0]
                .to_lowercase()
                .contains("x-amz-security-token: lease-token")
    );
    assert!(h[1..].iter().all(|s| {
        s.contains("Credential=rotated-key/")
            && s.to_lowercase()
                .contains("x-amz-security-token: rotated-token")
    }));
    drop(h);
    drop(a);
    assert_eq!(std::fs::read_dir(&cache).unwrap().count(), 0);
    stop.store(true, Ordering::Relaxed);
    handle.join().unwrap();
    std::fs::remove_dir_all(root).unwrap();
}

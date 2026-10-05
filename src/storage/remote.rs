//! Immutable S3 SQLite generations. Readers hold leases; refresh never overwrites
//! an open file. Failed downloads/validation retain the old generation and TTL.
use super::SqliteStore;
use crate::Result;
use std::{
    collections::HashMap,
    fs::File,
    path::PathBuf,
    sync::{Arc, Mutex, OnceLock, Weak},
    time::{Duration, Instant},
};

pub struct Generation {
    pub path: PathBuf,
}
impl Drop for Generation {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}
struct State {
    generation: Option<Arc<Generation>>,
    updated: Option<Instant>,
}
pub struct Remote {
    url: String,
    state: Mutex<State>,
    ttl: Duration,
    directory: PathBuf,
}
static REGISTRY: OnceLock<Mutex<HashMap<String, Weak<Remote>>>> = OnceLock::new();
impl Remote {
    pub fn open(path: &str) -> Result<Arc<Self>> {
        let url = url::Url::parse(path).map_err(|_| "Expected s3:// URL")?;
        if url.scheme() != "s3"
            || url.host_str().is_none()
            || url.path().trim_matches('/').is_empty()
        {
            return Err("Expected s3:// URL".into());
        }
        let mut registry = REGISTRY
            .get_or_init(Default::default)
            .lock()
            .map_err(|_| "remote registry poisoned")?;
        if let Some(remote) = registry.get(path).and_then(Weak::upgrade) {
            return Ok(remote);
        }
        registry.retain(|_, v| v.strong_count() > 0);
        let directory = crate::settings::var_os("TC_REMOTE_DB_CACHE_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| std::env::temp_dir().join("terracotta"));
        std::fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
        let ttl = crate::settings::var("TC_REMOTE_DB_CACHE_TTL")
            .unwrap_or_else(|_| "600".into())
            .parse::<u64>()
            .map_err(|_| "invalid remote database TTL")?;
        let remote = Arc::new(Self {
            url: path.into(),
            state: Mutex::new(State {
                generation: None,
                updated: None,
            }),
            ttl: Duration::from_secs(ttl),
            directory,
        });
        registry.insert(path.into(), Arc::downgrade(&remote));
        Ok(remote)
    }
    pub fn snapshot(&self) -> Result<Arc<Generation>> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "remote refresh lock poisoned")?;
        if state.updated.is_some_and(|t| t.elapsed() < self.ttl) {
            return Ok(state.generation.as_ref().unwrap().clone());
        }
        static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let path = self.directory.join(format!(
            "tc_s3_db_{}_{}_{}.sqlite",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let generation = Arc::new(Generation { path });
        let mut options = File::options();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&generation.path).map_err(|e| e.to_string())?;
        self.download(&mut file)?;
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        let store = SqliteStore::open_read_only(&generation.path)?;
        store.integrity_check()?;
        drop(store);
        state.generation = Some(generation.clone());
        state.updated = Some(Instant::now());
        Ok(generation)
    }
    fn download(&self, file: &mut File) -> Result<()> {
        let url = url::Url::parse(&self.url).map_err(|_| "Expected s3:// URL")?;
        let endpoint = crate::settings::var("AWS_ENDPOINT_URL_S3")
            .or_else(|_| crate::settings::var("AWS_ENDPOINT_URL"))
            .ok();
        // Resolve on each refresh so rotated environment/profile/container/instance
        // credentials are not frozen for the lifetime of the application.
        use aws_credential_types::provider::ProvideCredentials;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| e.to_string())?;
        let (aws, profile_region) = runtime
            .block_on(async {
                let config = aws_config::load_defaults(aws_config::BehaviorVersion::latest()).await;
                let credentials = config
                    .credentials_provider()
                    .ok_or("credentials unavailable")?
                    .provide_credentials()
                    .await
                    .map_err(|_| "credentials unavailable")?;
                Ok::<_, &str>((credentials, config.region().map(|r| r.as_ref().to_owned())))
            })
            .map_err(|_| "Could not retrieve database from S3: credentials unavailable")?;
        let region = crate::settings::var("AWS_REGION")
            .or_else(|_| crate::settings::var("AWS_DEFAULT_REGION"))
            .ok()
            .or(profile_region)
            .unwrap_or_else(|| "us-east-1".into());
        let region = if let Some(endpoint) = &endpoint {
            s3::Region::Custom {
                region,
                endpoint: endpoint.clone(),
            }
        } else {
            region.parse().map_err(|_| "invalid AWS region")?
        };
        let credentials = s3::creds::Credentials::new(
            Some(aws.access_key_id()),
            Some(aws.secret_access_key()),
            None,
            aws.session_token(),
            None,
        )
        .map_err(|_| "invalid S3 credentials")?;
        let bucket = s3::Bucket::new(url.host_str().unwrap(), region, credentials)
            .map_err(|_| "invalid S3 configuration")?;
        let mut bucket = if endpoint.is_some() {
            bucket.with_path_style()
        } else {
            bucket
        };
        bucket.set_request_timeout(Some(super::provider::timeout()?));
        let status = bucket
            .get_object_to_writer(url.path().trim_matches('/'), file)
            .map_err(|_| "Could not retrieve database from S3")?;
        if status != 200 {
            return Err(format!(
                "Could not retrieve database from S3 (HTTP {status})"
            ));
        }
        Ok(())
    }
}

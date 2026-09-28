//! Bounded raster execution, caching, and resource instrumentation.

use crate::Result;
use crate::raster::{Gdal, Raster, ReadRequest};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

pub const OVERLOADED: &str = "raster execution queue is full";
pub const TIMED_OUT: &str = "raster execution timed out while native work continues";

#[derive(Clone, Debug)]
pub struct ExecutionConfig {
    pub workers: usize,
    pub queue_capacity: usize,
    pub wait_timeout: Duration,
    pub cache_bytes: usize,
    pub remote_cache_ttl: Duration,
}

impl ExecutionConfig {
    pub fn from_env() -> Result<Self> {
        let maximum_cache_bytes =
            usize::try_from(16_u64 * 1024 * 1024 * 1024).unwrap_or(usize::MAX);
        Ok(Self {
            workers: env_usize(
                "TC_RASTER_WORKERS",
                if crate::settings::var("TC_USE_MULTIPROCESSING").is_ok_and(|s| s == "false") {
                    1
                } else {
                    3
                },
                1,
                64,
            )?,
            queue_capacity: env_usize("TC_RASTER_QUEUE_CAPACITY", 8, 1, 1024)?,
            wait_timeout: Duration::from_millis(env_u64(
                "TC_RASTER_TIMEOUT_MS",
                30_000,
                1,
                3_600_000,
            )?),
            cache_bytes: env_usize(
                "TC_RASTER_CACHE_BYTES",
                env_usize(
                    "TC_RASTER_CACHE_SIZE",
                    64 * 1024 * 1024,
                    0,
                    maximum_cache_bytes,
                )?,
                0,
                maximum_cache_bytes,
            )?,
            remote_cache_ttl: Duration::from_secs(env_u64(
                "TC_REMOTE_RASTER_CACHE_TTL_SECONDS",
                60,
                0,
                86_400,
            )?),
        })
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct ExecutionMetrics {
    pub workers: usize,
    pub queue_capacity: usize,
    pub wait_timeout_ms: u64,
    pub cache_capacity_bytes: usize,
    pub cache_bytes: usize,
    pub cache_entries: usize,
    pub submitted: u64,
    pub completed: u64,
    pub failed: u64,
    pub overloaded: u64,
    pub timed_out: u64,
    pub cache_hits: u64,
    pub cache_misses: u64,
    pub remote_jobs: u64,
    pub in_flight: usize,
    pub peak_in_flight: usize,
}

struct Counters {
    submitted: AtomicU64,
    completed: AtomicU64,
    failed: AtomicU64,
    overloaded: AtomicU64,
    timed_out: AtomicU64,
    cache_hits: AtomicU64,
    cache_misses: AtomicU64,
    remote_jobs: AtomicU64,
    in_flight: AtomicUsize,
    peak_in_flight: AtomicUsize,
}

impl Counters {
    fn new() -> Self {
        Self {
            submitted: AtomicU64::new(0),
            completed: AtomicU64::new(0),
            failed: AtomicU64::new(0),
            overloaded: AtomicU64::new(0),
            timed_out: AtomicU64::new(0),
            cache_hits: AtomicU64::new(0),
            cache_misses: AtomicU64::new(0),
            remote_jobs: AtomicU64::new(0),
            in_flight: AtomicUsize::new(0),
            peak_in_flight: AtomicUsize::new(0),
        }
    }

    fn begin(&self) {
        let current = self.in_flight.fetch_add(1, Ordering::Relaxed) + 1;
        self.peak_in_flight.fetch_max(current, Ordering::Relaxed);
    }

    fn end(&self) {
        self.in_flight.fetch_sub(1, Ordering::Relaxed);
    }
}

struct Job {
    path: PathBuf,
    request: ReadRequest,
    result: SyncSender<Result<Arc<Raster>>>,
}

enum Work {
    Read(Job),
    Wake,
}

#[derive(Clone, Eq)]
struct CacheKey {
    path: String,
    source_size: u64,
    source_modified_ns: u128,
    bounds: Option<[u64; 4]>,
    size: [usize; 2],
    preserve_values: bool,
}

impl PartialEq for CacheKey {
    fn eq(&self, other: &Self) -> bool {
        self.path == other.path
            && self.source_size == other.source_size
            && self.source_modified_ns == other.source_modified_ns
            && self.bounds == other.bounds
            && self.size == other.size
            && self.preserve_values == other.preserve_values
    }
}

impl Hash for CacheKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.path.hash(state);
        self.source_size.hash(state);
        self.source_modified_ns.hash(state);
        self.bounds.hash(state);
        self.size.hash(state);
        self.preserve_values.hash(state);
    }
}

struct CacheEntry {
    raster: Arc<Raster>,
    inserted: Instant,
    bytes: usize,
    remote: bool,
}

struct RasterCache {
    entries: HashMap<CacheKey, CacheEntry>,
    order: VecDeque<CacheKey>,
    bytes: usize,
    capacity: usize,
    remote_ttl: Duration,
}

impl RasterCache {
    fn new(capacity: usize, remote_ttl: Duration) -> Self {
        Self {
            entries: HashMap::new(),
            order: VecDeque::new(),
            bytes: 0,
            capacity,
            remote_ttl,
        }
    }

    fn get(&mut self, key: &CacheKey) -> Option<Arc<Raster>> {
        let expired = self
            .entries
            .get(key)
            .map(|entry| {
                entry.remote
                    && (self.remote_ttl.is_zero() || entry.inserted.elapsed() > self.remote_ttl)
            })
            .unwrap_or(false);
        if expired {
            self.remove(key);
            return None;
        }
        let raster = self.entries.get(key)?.raster.clone();
        self.order.retain(|candidate| candidate != key);
        self.order.push_back(key.clone());
        Some(raster)
    }

    fn insert(&mut self, key: CacheKey, raster: Arc<Raster>, remote: bool) {
        let bytes = raster.values.len() * std::mem::size_of::<f64>() + raster.mask.len();
        if self.capacity == 0 || bytes > self.capacity {
            return;
        }
        self.remove(&key);
        while self.bytes.saturating_add(bytes) > self.capacity {
            let Some(oldest) = self.order.pop_front() else {
                break;
            };
            if let Some(entry) = self.entries.remove(&oldest) {
                self.bytes = self.bytes.saturating_sub(entry.bytes);
            }
        }
        self.bytes += bytes;
        self.order.push_back(key.clone());
        self.entries.insert(
            key,
            CacheEntry {
                raster,
                inserted: Instant::now(),
                bytes,
                remote,
            },
        );
    }

    fn remove(&mut self, key: &CacheKey) {
        self.order.retain(|candidate| candidate != key);
        if let Some(entry) = self.entries.remove(key) {
            self.bytes = self.bytes.saturating_sub(entry.bytes);
        }
    }
}

struct Neighbor {
    key: CacheKey,
    path: PathBuf,
    request: ReadRequest,
}

struct NeighborQueue {
    pending: VecDeque<Neighbor>,
    keys: std::collections::HashSet<CacheKey>,
}

impl NeighborQueue {
    fn new() -> Self {
        Self {
            pending: VecDeque::new(),
            keys: std::collections::HashSet::new(),
        }
    }

    fn schedule(&mut self, path: &Path, request: ReadRequest, cache: &RasterCache) {
        if is_remote(path) || request.preserve_values || request.size != [256, 256] {
            return;
        }
        let Some(bounds) = request.bounds_mercator else {
            return;
        };
        let width = bounds[2] - bounds[0];
        if !width.is_finite() || width <= 0.0 {
            return;
        }
        let half = std::f64::consts::PI * 6_378_137.0;
        let zoom = (2.0 * half / width).log2().round() as u32;
        if zoom > 30 {
            return;
        }
        let count = 2.0f64.powi(zoom as i32);
        let span = 2.0 * half / count;
        let x = ((bounds[0] + half) / span).round();
        let y = ((half - bounds[3]) / span).round();
        if crate::raster::mercator_tile_bounds(x, y, zoom) != bounds {
            return;
        }
        // All directions are treated equally; the browser may pan in any direction.
        for (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
            let nx = x + dx as f64;
            let ny = y + dy as f64;
            if nx < 0.0 || ny < 0.0 || nx >= count || ny >= count {
                continue;
            }
            let shifted = ReadRequest {
                bounds_mercator: Some(crate::raster::mercator_tile_bounds(nx, ny, zoom)),
                ..request
            };
            let key = cache_key(path, shifted, false);
            if cache.entries.contains_key(&key) || !self.keys.insert(key.clone()) {
                continue;
            }
            if self.pending.len() >= 128 {
                if let Some(oldest) = self.pending.pop_back() {
                    self.keys.remove(&oldest.key);
                }
            }
            self.pending.push_front(Neighbor {
                key,
                path: path.to_path_buf(),
                request: shifted,
            });
        }
    }

    fn pop(&mut self) -> Option<Neighbor> {
        let neighbor = self.pending.pop_front()?;
        self.keys.remove(&neighbor.key);
        Some(neighbor)
    }
}

pub struct RasterExecutor {
    pool: WorkerPool,
    cache: Arc<Mutex<RasterCache>>,
    neighbors: Arc<Mutex<NeighborQueue>>,
    counters: Arc<Counters>,
    config: ExecutionConfig,
    gdal_version: String,
}

// Keep threads owned until their GDAL thread-local destructors have finished.
// Detaching them allows process/library finalization to race native cleanup.
struct WorkerPool {
    sender: Option<SyncSender<Work>>,
    workers: Vec<thread::JoinHandle<()>>,
}

impl Drop for WorkerPool {
    fn drop(&mut self) {
        // Closing admission lets accepted jobs drain before recv returns Err.
        self.sender.take();
        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

impl RasterExecutor {
    pub fn start(config: ExecutionConfig) -> Result<Self> {
        let (sender, receiver) = mpsc::sync_channel::<Work>(config.queue_capacity);
        let mut pool = WorkerPool {
            sender: Some(sender),
            workers: Vec::with_capacity(config.workers),
        };
        let receiver = Arc::new(Mutex::new(receiver));
        let cache = Arc::new(Mutex::new(RasterCache::new(
            config.cache_bytes,
            config.remote_cache_ttl,
        )));
        let counters = Arc::new(Counters::new());
        let neighbors = Arc::new(Mutex::new(NeighborQueue::new()));
        let source_blocks = Gdal::new_shared_blocks();
        let (ready_sender, ready_receiver) = mpsc::channel();
        let mut gdal_version = None;
        for worker_id in 0..config.workers {
            let receiver = receiver.clone();
            let cache = cache.clone();
            let counters = counters.clone();
            let neighbors = neighbors.clone();
            let source_blocks = source_blocks.clone();
            let ready_sender = ready_sender.clone();
            let settings = crate::settings::snapshot();
            let worker = thread::Builder::new()
                .name(format!("terracotta-gdal-{worker_id}"))
                .spawn(move || {
                    crate::settings::scope(settings, || {
                        worker_loop(
                            receiver,
                            cache,
                            neighbors,
                            source_blocks,
                            counters,
                            ready_sender,
                        )
                    })
                })
                .map_err(|error| format!("could not start raster worker: {error}"))?;
            pool.workers.push(worker);
            // GDAL registration and dynamic loading are process-global. Start
            // workers serially, then allow their request loops to run in parallel.
            match ready_receiver.recv() {
                Ok(Ok(version)) => gdal_version.get_or_insert(version),
                Ok(Err(error)) => return Err(error),
                Err(_) => return Err("raster worker exited during startup".into()),
            };
        }
        drop(ready_sender);
        Ok(Self {
            pool,
            cache,
            neighbors,
            counters,
            config,
            gdal_version: gdal_version.unwrap_or_else(|| "unknown".into()),
        })
    }

    pub fn wake_prefetch(&self) {
        if std::env::var_os("TC_PREFETCH_TILE_RING").is_some() {
            let _ = self
                .pool
                .sender
                .as_ref()
                .expect("live executor")
                .try_send(Work::Wake);
        }
    }

    pub fn version(&self) -> &str {
        &self.gdal_version
    }

    pub fn read(&self, path: &Path, request: ReadRequest) -> Result<Arc<Raster>> {
        let remote = is_remote(path);
        let key = cache_key(path, request, remote);
        let cached = self.cache.lock().expect("cache lock").get(&key);
        if let Some(raster) = cached {
            self.counters.cache_hits.fetch_add(1, Ordering::Relaxed);
            if std::env::var_os("TC_PREFETCH_TILE_RING").is_some() {
                let cache = self.cache.lock().expect("cache lock");
                self.neighbors
                    .lock()
                    .expect("neighbor queue")
                    .schedule(path, request, &cache);
            }
            return Ok(raster);
        }
        self.counters.cache_misses.fetch_add(1, Ordering::Relaxed);
        let (result_sender, result_receiver) = mpsc::sync_channel(1);
        let job = Job {
            path: path.to_path_buf(),
            request,
            result: result_sender,
        };
        match self
            .pool
            .sender
            .as_ref()
            .expect("live executor")
            .try_send(Work::Read(job))
        {
            Ok(()) => {
                self.counters.submitted.fetch_add(1, Ordering::Relaxed);
                if remote {
                    self.counters.remote_jobs.fetch_add(1, Ordering::Relaxed);
                }
            }
            Err(TrySendError::Full(_)) => {
                self.counters.overloaded.fetch_add(1, Ordering::Relaxed);
                return Err(OVERLOADED.into());
            }
            Err(TrySendError::Disconnected(_)) => {
                return Err("raster execution workers are unavailable".into());
            }
        }
        match result_receiver.recv_timeout(self.config.wait_timeout) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.counters.timed_out.fetch_add(1, Ordering::Relaxed);
                Err(TIMED_OUT.into())
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err("raster worker exited without a result".into())
            }
        }
    }

    pub fn metrics(&self) -> ExecutionMetrics {
        let cache = self.cache.lock().expect("cache lock");
        ExecutionMetrics {
            workers: self.config.workers,
            queue_capacity: self.config.queue_capacity,
            wait_timeout_ms: self.config.wait_timeout.as_millis() as u64,
            cache_capacity_bytes: self.config.cache_bytes,
            cache_bytes: cache.bytes,
            cache_entries: cache.entries.len(),
            submitted: self.counters.submitted.load(Ordering::Relaxed),
            completed: self.counters.completed.load(Ordering::Relaxed),
            failed: self.counters.failed.load(Ordering::Relaxed),
            overloaded: self.counters.overloaded.load(Ordering::Relaxed),
            timed_out: self.counters.timed_out.load(Ordering::Relaxed),
            cache_hits: self.counters.cache_hits.load(Ordering::Relaxed),
            cache_misses: self.counters.cache_misses.load(Ordering::Relaxed),
            remote_jobs: self.counters.remote_jobs.load(Ordering::Relaxed),
            in_flight: self.counters.in_flight.load(Ordering::Relaxed),
            peak_in_flight: self.counters.peak_in_flight.load(Ordering::Relaxed),
        }
    }
}

fn worker_loop(
    receiver: Arc<Mutex<Receiver<Work>>>,
    cache: Arc<Mutex<RasterCache>>,
    neighbors: Arc<Mutex<NeighborQueue>>,
    source_blocks: Arc<Mutex<crate::raster::SharedBlocks>>,
    counters: Arc<Counters>,
    ready: mpsc::Sender<Result<String>>,
) {
    let gdal = match Gdal::load_with_shared_blocks(source_blocks) {
        Ok(gdal) => {
            let _ = ready.send(Ok(gdal.version()));
            gdal
        }
        Err(error) => {
            let _ = ready.send(Err(error));
            return;
        }
    };
    let mut next_job = None;
    loop {
        let work = if let Some(work) = next_job.take() {
            work
        } else {
            let receiver = receiver.lock().expect("raster receiver lock");
            let Ok(work) = receiver.recv() else { return };
            work
        };
        let foreground = matches!(work, Work::Read(_));
        if let Work::Read(job) = work {
            counters.begin();
            let remote = is_remote(&job.path);
            let key = cache_key(&job.path, job.request, remote);
            let result = gdal.read(&job.path, job.request).map(Arc::new);
            match &result {
                Ok(raster) => {
                    cache
                        .lock()
                        .expect("cache lock")
                        .insert(key, raster.clone(), remote);
                    counters.completed.fetch_add(1, Ordering::Relaxed);
                }
                Err(_) => {
                    counters.failed.fetch_add(1, Ordering::Relaxed);
                }
            }
            counters.end();
            let successful = result.is_ok();
            let _ = job.result.send(result);
            if successful && std::env::var_os("TC_PREFETCH_TILE_RING").is_some() {
                let cache_guard = cache.lock().expect("cache lock");
                neighbors.lock().expect("neighbor queue").schedule(
                    &job.path,
                    job.request,
                    &cache_guard,
                );
            }
        }
        // Serving requests always takes priority. Idle workers may decode
        // neighboring source blocks after a response is delivered.
        loop {
            let pending = receiver.try_lock().ok().map(|receiver| receiver.try_recv());
            match pending {
                Some(Ok(work)) => {
                    next_job = Some(work);
                    break;
                }
                Some(Err(mpsc::TryRecvError::Disconnected)) => return,
                _ => {
                    if foreground && std::env::var_os("TC_PREFETCH_TILE_RING").is_some() {
                        break;
                    }
                    if std::env::var_os("TC_PREFETCH_TILE_RING").is_some() {
                        let candidate = neighbors.lock().expect("neighbor queue").pop();
                        if let Some(candidate) = candidate {
                            if candidate.key == cache_key(&candidate.path, candidate.request, false)
                                && !cache
                                    .lock()
                                    .expect("cache lock")
                                    .entries
                                    .contains_key(&candidate.key)
                            {
                                if let Ok(raster) = gdal.read(&candidate.path, candidate.request) {
                                    cache.lock().expect("cache lock").insert(
                                        candidate.key,
                                        Arc::new(raster),
                                        false,
                                    );
                                }
                            }
                            continue;
                        }
                    }
                    if gdal.prefetch_idle_block() {
                        continue;
                    }
                    break;
                }
            }
        }
    }
}

fn cache_key(path: &Path, request: ReadRequest, remote: bool) -> CacheKey {
    let (source_size, source_modified_ns) = if remote {
        (0, 0)
    } else {
        std::fs::metadata(path)
            .ok()
            .map(|metadata| {
                let modified = metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH);
                let nanos = modified
                    .duration_since(SystemTime::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos();
                (metadata.len(), nanos)
            })
            .unwrap_or((0, 0))
    };
    CacheKey {
        path: path.to_string_lossy().into_owned(),
        source_size,
        source_modified_ns,
        bounds: request
            .bounds_mercator
            .map(|bounds| bounds.map(f64::to_bits)),
        size: request.size,
        preserve_values: request.preserve_values,
    }
}

pub fn is_remote(path: &Path) -> bool {
    let path = path.to_string_lossy();
    path.starts_with("http://")
        || path.starts_with("https://")
        || path.starts_with("s3://")
        || path.starts_with("/vsicurl/")
        || path.starts_with("/vsis3/")
}

fn env_usize(name: &str, default: usize, minimum: usize, maximum: usize) -> Result<usize> {
    let Some(raw) = crate::settings::var_os(name) else {
        return Ok(default);
    };
    let value = raw
        .to_string_lossy()
        .parse::<usize>()
        .map_err(|_| format!("{name} must be an integer"))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(format!("{name} must be between {minimum} and {maximum}"));
    }
    Ok(value)
}

fn env_u64(name: &str, default: u64, minimum: u64, maximum: u64) -> Result<u64> {
    let Some(raw) = crate::settings::var_os(name) else {
        return Ok(default);
    };
    let value = raw
        .to_string_lossy()
        .parse::<u64>()
        .map_err(|_| format!("{name} must be an integer"))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(format!("{name} must be between {minimum} and {maximum}"));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pool_drop_drains_jobs_and_waits_for_thread_local_cleanup() {
        struct Cleanup(Arc<AtomicUsize>);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                self.0.fetch_add(1, Ordering::SeqCst);
            }
        }
        thread_local! {
            static CLEANUP: std::cell::RefCell<Option<Cleanup>> = const {
                std::cell::RefCell::new(None)
            };
        }
        let cleaned = Arc::new(AtomicUsize::new(0));
        let completed = Arc::new(AtomicUsize::new(0));
        let (sender, receiver) = mpsc::sync_channel::<Work>(4);
        let worker_cleaned = cleaned.clone();
        let worker_completed = completed.clone();
        let worker = thread::spawn(move || {
            CLEANUP.with(|slot| *slot.borrow_mut() = Some(Cleanup(worker_cleaned)));
            while receiver.recv().is_ok() {
                thread::sleep(Duration::from_millis(5));
                worker_completed.fetch_add(1, Ordering::SeqCst);
            }
        });
        let pool = WorkerPool {
            sender: Some(sender),
            workers: vec![worker],
        };
        for _ in 0..3 {
            let (result, _receiver) = mpsc::sync_channel(1);
            pool.sender
                .as_ref()
                .unwrap()
                .send(Work::Read(Job {
                    path: PathBuf::new(),
                    request: ReadRequest::default(),
                    result,
                }))
                .unwrap();
        }
        drop(pool);
        assert_eq!(completed.load(Ordering::SeqCst), 3);
        assert_eq!(cleaned.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn cache_identity_covers_rendering_inputs_and_file_generation() {
        let path = Path::new("validation/reference/capture/images/rgb.png");
        let request = ReadRequest::default();
        let base = cache_key(path, request, false);
        let changed_size = cache_key(
            path,
            ReadRequest {
                size: [128, 256],
                ..request
            },
            false,
        );
        let categorical = cache_key(
            path,
            ReadRequest {
                preserve_values: true,
                ..request
            },
            false,
        );
        assert_ne!(base.size, changed_size.size);
        assert_ne!(base.preserve_values, categorical.preserve_values);
    }

    #[test]
    fn recognizes_supported_remote_paths() {
        assert!(is_remote(Path::new("https://example.test/a.tif")));
        assert!(is_remote(Path::new("s3://bucket/a.tif")));
        assert!(!is_remote(Path::new("/tmp/a.tif")));
    }
}

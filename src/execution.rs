//! Bounded raster execution, caching, and resource instrumentation.

use crate::Result;
use crate::raster::{Gdal, Raster, ReadRequest};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

mod compact;
use compact::CachedRaster;

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
    pub cancelled_queued: u64,
    pub overloaded: u64,
    pub timed_out: u64,
    pub cache_hits: u64,
    pub cache_misses: u64,
    pub remote_jobs: u64,
    pub in_flight: usize,
    pub peak_in_flight: usize,
    pub queued: usize,
    pub prefetch_in_flight: usize,
    pub prefetch_completed: u64,
    pub prefetch_failed: u64,
    pub cost: serde_json::Value,
}

struct Counters {
    submitted: AtomicU64,
    completed: AtomicU64,
    failed: AtomicU64,
    cancelled_queued: AtomicU64,
    overloaded: AtomicU64,
    timed_out: AtomicU64,
    cache_hits: AtomicU64,
    cache_misses: AtomicU64,
    remote_jobs: AtomicU64,
    in_flight: AtomicUsize,
    peak_in_flight: AtomicUsize,
    queued: AtomicUsize,
    prefetch_in_flight: AtomicUsize,
    prefetch_completed: AtomicU64,
    prefetch_failed: AtomicU64,
}

impl Counters {
    fn new() -> Self {
        Self {
            submitted: AtomicU64::new(0),
            completed: AtomicU64::new(0),
            failed: AtomicU64::new(0),
            cancelled_queued: AtomicU64::new(0),
            overloaded: AtomicU64::new(0),
            timed_out: AtomicU64::new(0),
            cache_hits: AtomicU64::new(0),
            cache_misses: AtomicU64::new(0),
            remote_jobs: AtomicU64::new(0),
            in_flight: AtomicUsize::new(0),
            peak_in_flight: AtomicUsize::new(0),
            queued: AtomicUsize::new(0),
            prefetch_in_flight: AtomicUsize::new(0),
            prefetch_completed: AtomicU64::new(0),
            prefetch_failed: AtomicU64::new(0),
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
    enqueued: Instant,
    context: Option<crate::telemetry::Context>,
    path: PathBuf,
    request: ReadRequest,
    result: SyncSender<Result<Arc<Raster>>>,
    interested: Arc<AtomicBool>,
}
struct BandJob {
    enqueued: Instant,
    context: Option<crate::telemetry::Context>,
    path: PathBuf,
    requests: Vec<ReadRequest>,
    result: SyncSender<Result<Vec<Arc<Raster>>>>,
    interested: Arc<AtomicBool>,
}
struct Interest(Arc<AtomicBool>);
impl Drop for Interest {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

enum PendingRead {
    Ready(Arc<Raster>),
    Queued {
        receiver: Receiver<Result<Arc<Raster>>>,
        interested: Arc<AtomicBool>,
    },
}
impl Drop for PendingRead {
    fn drop(&mut self) {
        if let Self::Queued { interested, .. } = self {
            interested.store(false, Ordering::Release);
        }
    }
}
impl PendingRead {
    fn wait(&self, counters: &Counters, timeout: Duration) -> Result<Arc<Raster>> {
        let Self::Queued { receiver, .. } = self else {
            let Self::Ready(raster) = self else {
                unreachable!()
            };
            return Ok(raster.clone());
        };
        match receiver.recv_timeout(timeout) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                counters.timed_out.fetch_add(1, Ordering::Relaxed);
                Err(TIMED_OUT.into())
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err("raster worker exited without a result".into())
            }
        }
    }
}

enum Work {
    Read(Job),
    Bands(BandJob),
    Wake,
}

#[derive(Clone, Eq, PartialEq, Hash)]
struct CacheKey {
    grid: Option<crate::layers::GridRead>,
    band: usize,
    path: String,
    generation: Vec<(u64, u128)>,
    bounds: Option<[u64; 4]>,
    size: [usize; 2],
    preserve_values: bool,
}

struct CacheEntry {
    raster: CachedRaster,
    hot: Option<Arc<Raster>>,
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
    compact: bool,
}

impl RasterCache {
    fn new(capacity: usize, remote_ttl: Duration, compact: bool) -> Self {
        Self {
            entries: HashMap::new(),
            order: VecDeque::new(),
            bytes: 0,
            capacity,
            remote_ttl,
            compact,
        }
    }

    fn get(&mut self, key: &CacheKey) -> Option<CachedRaster> {
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
        let entry = self.entries.get(key)?;
        let raster = entry.hot.as_ref().map_or_else(
            || entry.raster.clone(),
            |hot| CachedRaster::Full(hot.clone()),
        );
        self.order.retain(|candidate| candidate != key);
        self.order.push_back(key.clone());
        Some(raster)
    }

    fn insert(&mut self, key: CacheKey, raster: Arc<Raster>, remote: bool) {
        if self.capacity == 0 {
            return;
        }
        let hot = self.compact.then(|| raster.clone());
        let raster = CachedRaster::new(raster, self.compact);
        let bytes = raster.bytes();
        if bytes > self.capacity {
            return;
        }
        self.remove(&key);
        while self.bytes.saturating_add(bytes) > self.capacity {
            if self.compact && self.drop_oldest_hot() {
                continue;
            }
            let Some(oldest) = self.order.pop_front() else {
                break;
            };
            if let Some(entry) = self.entries.remove(&oldest) {
                self.bytes = self.bytes.saturating_sub(entry.bytes);
            }
        }
        let hot = if matches!(raster, CachedRaster::Compact(_)) {
            hot
        } else {
            None
        };
        let hot = hot.filter(|hot| {
            let hot_bytes = hot.values.capacity() * 8 + hot.mask.capacity();
            while self.bytes.saturating_add(bytes).saturating_add(hot_bytes) > self.capacity {
                if !self.drop_oldest_hot() {
                    return false;
                }
            }
            true
        });
        let bytes = bytes
            + hot.as_ref().map_or(0, |raster| {
                raster.values.capacity() * 8 + raster.mask.capacity()
            });
        self.bytes += bytes;
        self.order.push_back(key.clone());
        self.entries.insert(
            key,
            CacheEntry {
                raster,
                hot,
                inserted: Instant::now(),
                bytes,
                remote,
            },
        );
    }

    fn promote(&mut self, key: &CacheKey, snapshot: &CachedRaster, raster: Arc<Raster>) {
        if !matches!(snapshot, CachedRaster::Compact(_))
            || !self
                .entries
                .get(key)
                .is_some_and(|entry| entry.hot.is_none() && entry.raster.same(snapshot))
        {
            return;
        }
        let bytes = raster.values.capacity() * 8 + raster.mask.capacity();
        while self.bytes.saturating_add(bytes) > self.capacity {
            if !self.drop_oldest_hot() {
                return;
            }
        }
        let entry = self.entries.get_mut(key).expect("cached raster promotion");
        entry.hot = Some(raster);
        entry.bytes += bytes;
        self.bytes += bytes;
    }

    fn drop_oldest_hot(&mut self) -> bool {
        for key in &self.order {
            let entry = self.entries.get_mut(key).expect("cached raster LRU entry");
            if let Some(hot) = entry.hot.take() {
                let bytes = hot.values.capacity() * 8 + hot.mask.capacity();
                entry.bytes -= bytes;
                self.bytes -= bytes;
                return true;
            }
        }
        false
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
        if request.grid.is_some() {
            return;
        }
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
        // The browser may pan in any direction.
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
            let key = cache_key(path, shifted);
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
    cost: Arc<crate::telemetry::Metrics>,
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
            crate::settings::flag("TC_COMPACT_RASTER_CACHE", false)?,
        )));
        let counters = Arc::new(Counters::new());
        let cost = Arc::new(crate::telemetry::Metrics::default());
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
            let cost = cost.clone();
            let worker = thread::Builder::new()
                .name(format!("terracotta-gdal-{worker_id}"))
                .spawn(move || {
                    crate::settings::scope(settings, || {
                        crate::telemetry::scope(Some(cost.context(false)), || {
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
            cost,
            cache,
            neighbors,
            counters,
            config,
            gdal_version: gdal_version.unwrap_or_else(|| "unknown".into()),
        })
    }

    pub(crate) fn cost(&self) -> &Arc<crate::telemetry::Metrics> {
        &self.cost
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
        let context = crate::telemetry::current().or_else(|| Some(self.cost.context(false)));
        crate::telemetry::scope(context, || {
            self.submit(path, request)?
                .wait(&self.counters, self.config.wait_timeout)
        })
    }

    pub fn read_many(&self, requests: &[(PathBuf, ReadRequest)]) -> Result<Vec<Arc<Raster>>> {
        let context = crate::telemetry::current().or_else(|| Some(self.cost.context(false)));
        crate::telemetry::scope(context, || self.read_many_scoped(requests))
    }

    fn read_many_scoped(&self, requests: &[(PathBuf, ReadRequest)]) -> Result<Vec<Arc<Raster>>> {
        if requests.len() > 1
            && requests.iter().all(|(path, request)| {
                let (first_path, first) = &requests[0];
                path == first_path
                    && request.grid == first.grid
                    && request.bounds_mercator == first.bounds_mercator
                    && request.size == first.size
                    && request.preserve_values == first.preserve_values
            })
        {
            let remote = is_remote(&requests[0].0);
            let keys = requests
                .iter()
                .map(|(path, request)| cache_key(path, *request))
                .collect::<Vec<_>>();
            let cached = {
                let mut cache = self.cache.lock().expect("cache lock");
                keys.iter()
                    .map(|key| cache.get(key))
                    .collect::<Option<Vec<_>>>()
            };
            if let Some(cached) = cached {
                self.counters
                    .cache_hits
                    .fetch_add(requests.len() as u64, Ordering::Relaxed);
                return Ok(cached
                    .iter()
                    .zip(&keys)
                    .map(|(snapshot, key)| {
                        let raster = snapshot.raster();
                        if matches!(snapshot, CachedRaster::Compact(_)) {
                            self.cache.lock().expect("cache lock").promote(
                                key,
                                snapshot,
                                raster.clone(),
                            );
                        }
                        raster
                    })
                    .collect());
            }
            self.counters
                .cache_misses
                .fetch_add(requests.len() as u64, Ordering::Relaxed);
            let (result, receiver) = mpsc::sync_channel(1);
            let interest = Interest(Arc::new(AtomicBool::new(true)));
            let job = BandJob {
                enqueued: Instant::now(),
                context: crate::telemetry::current(),
                path: requests[0].0.clone(),
                requests: requests.iter().map(|(_, r)| *r).collect(),
                result,
                interested: interest.0.clone(),
            };
            self.counters.queued.fetch_add(1, Ordering::Relaxed);
            match self
                .pool
                .sender
                .as_ref()
                .expect("live executor")
                .try_send(Work::Bands(job))
            {
                Ok(()) => {
                    self.counters.submitted.fetch_add(1, Ordering::Relaxed);
                    if remote {
                        self.counters.remote_jobs.fetch_add(1, Ordering::Relaxed);
                    }
                }
                Err(TrySendError::Full(_)) => {
                    self.counters.queued.fetch_sub(1, Ordering::Relaxed);
                    self.counters.overloaded.fetch_add(1, Ordering::Relaxed);
                    return Err(OVERLOADED.into());
                }
                Err(TrySendError::Disconnected(_)) => {
                    self.counters.queued.fetch_sub(1, Ordering::Relaxed);
                    return Err("raster execution workers are unavailable".into());
                }
            }
            return match receiver.recv_timeout(self.config.wait_timeout) {
                Ok(result) => result,
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    self.counters.timed_out.fetch_add(1, Ordering::Relaxed);
                    Err(TIMED_OUT.into())
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    Err("raster worker exited without a result".into())
                }
            };
        }
        let started = Instant::now();
        let pending = requests
            .iter()
            .map(|(path, request)| self.submit(path, *request))
            .collect::<Result<Vec<_>>>()?;
        pending
            .iter()
            .map(|read| {
                read.wait(
                    &self.counters,
                    self.config.wait_timeout.saturating_sub(started.elapsed()),
                )
            })
            .collect()
    }

    fn submit(&self, path: &Path, request: ReadRequest) -> Result<PendingRead> {
        let remote = is_remote(path);
        let key = cache_key(path, request);
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
            let result = raster.raster();
            if matches!(raster, CachedRaster::Compact(_)) {
                self.cache
                    .lock()
                    .expect("cache lock")
                    .promote(&key, &raster, result.clone());
            }
            return Ok(PendingRead::Ready(result));
        }
        self.counters.cache_misses.fetch_add(1, Ordering::Relaxed);
        let (result_sender, result_receiver) = mpsc::sync_channel(1);
        let interested = Arc::new(AtomicBool::new(true));
        let job = Job {
            enqueued: Instant::now(),
            context: crate::telemetry::current(),
            path: path.to_path_buf(),
            request,
            result: result_sender,
            interested: interested.clone(),
        };
        self.counters.queued.fetch_add(1, Ordering::Relaxed);
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
                self.counters.queued.fetch_sub(1, Ordering::Relaxed);
                self.counters.overloaded.fetch_add(1, Ordering::Relaxed);
                return Err(OVERLOADED.into());
            }
            Err(TrySendError::Disconnected(_)) => {
                self.counters.queued.fetch_sub(1, Ordering::Relaxed);
                return Err("raster execution workers are unavailable".into());
            }
        }
        Ok(PendingRead::Queued {
            receiver: result_receiver,
            interested,
        })
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
            cancelled_queued: self.counters.cancelled_queued.load(Ordering::Relaxed),
            overloaded: self.counters.overloaded.load(Ordering::Relaxed),
            timed_out: self.counters.timed_out.load(Ordering::Relaxed),
            cache_hits: self.counters.cache_hits.load(Ordering::Relaxed),
            cache_misses: self.counters.cache_misses.load(Ordering::Relaxed),
            remote_jobs: self.counters.remote_jobs.load(Ordering::Relaxed),
            in_flight: self.counters.in_flight.load(Ordering::Relaxed),
            peak_in_flight: self.counters.peak_in_flight.load(Ordering::Relaxed),
            queued: self.counters.queued.load(Ordering::Relaxed),
            prefetch_in_flight: self.counters.prefetch_in_flight.load(Ordering::Relaxed),
            prefetch_completed: self.counters.prefetch_completed.load(Ordering::Relaxed),
            prefetch_failed: self.counters.prefetch_failed.load(Ordering::Relaxed),
            cost: self.cost.snapshot(),
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
        let foreground = matches!(work, Work::Read(_) | Work::Bands(_));
        if let Work::Bands(job) = &work {
            counters.queued.fetch_sub(1, Ordering::Relaxed);
            if let Some(context) = &job.context {
                context.record(crate::telemetry::Stage::QueueWait, job.enqueued.elapsed());
            }
            if !job.interested.load(Ordering::Acquire) {
                counters.cancelled_queued.fetch_add(1, Ordering::Relaxed);
                continue;
            }
            counters.begin();
            let remote = is_remote(&job.path);
            let result = crate::telemetry::scope(job.context.clone(), || {
                gdal.read_bands(&job.path, &job.requests)
                    .map(|rasters| rasters.into_iter().map(Arc::new).collect::<Vec<_>>())
            });
            if let Ok(rasters) = &result {
                let mut cache = cache.lock().expect("cache lock");
                for (request, raster) in job.requests.iter().zip(rasters) {
                    cache.insert(cache_key(&job.path, *request), raster.clone(), remote);
                }
                counters.completed.fetch_add(1, Ordering::Relaxed);
            } else {
                counters.failed.fetch_add(1, Ordering::Relaxed);
            }
            counters.end();
            let _ = job.result.send(result);
        } else if let Work::Read(job) = work {
            counters.queued.fetch_sub(1, Ordering::Relaxed);
            if let Some(context) = &job.context {
                context.record(crate::telemetry::Stage::QueueWait, job.enqueued.elapsed());
            }
            if !job.interested.load(Ordering::Acquire) {
                counters.cancelled_queued.fetch_add(1, Ordering::Relaxed);
                continue;
            }
            counters.begin();
            let remote = is_remote(&job.path);
            let key = cache_key(&job.path, job.request);
            let result = crate::telemetry::scope(job.context.clone(), || {
                gdal.read(&job.path, job.request).map(Arc::new)
            });
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
                    if counters.queued.load(Ordering::Relaxed) != 0 {
                        break;
                    }
                    if std::env::var_os("TC_PREFETCH_TILE_RING").is_some() {
                        let candidate = neighbors.lock().expect("neighbor queue").pop();
                        if let Some(candidate) = candidate {
                            if candidate.key == cache_key(&candidate.path, candidate.request)
                                && !cache
                                    .lock()
                                    .expect("cache lock")
                                    .entries
                                    .contains_key(&candidate.key)
                            {
                                counters.prefetch_in_flight.fetch_add(1, Ordering::Relaxed);
                                let _timer =
                                    crate::telemetry::timer(crate::telemetry::Stage::Prefetch);
                                if let Ok(raster) = gdal.read(&candidate.path, candidate.request) {
                                    counters.prefetch_completed.fetch_add(1, Ordering::Relaxed);
                                    cache.lock().expect("cache lock").insert(
                                        candidate.key,
                                        Arc::new(raster),
                                        false,
                                    );
                                } else {
                                    counters.prefetch_failed.fetch_add(1, Ordering::Relaxed);
                                }
                                counters.prefetch_in_flight.fetch_sub(1, Ordering::Relaxed);
                            }
                            continue;
                        }
                    }
                    counters.prefetch_in_flight.fetch_add(1, Ordering::Relaxed);
                    let prefetched = gdal.prefetch_idle_block();
                    counters.prefetch_in_flight.fetch_sub(1, Ordering::Relaxed);
                    if let Some(successful) = prefetched {
                        let counter = if successful {
                            &counters.prefetch_completed
                        } else {
                            &counters.prefetch_failed
                        };
                        counter.fetch_add(1, Ordering::Relaxed);
                        continue;
                    }
                    break;
                }
            }
        }
    }
}

fn cache_key(path: &Path, request: ReadRequest) -> CacheKey {
    CacheKey {
        grid: request.grid,
        band: request.band,
        path: path.to_string_lossy().into_owned(),
        generation: source_generation(path),
        bounds: request
            .bounds_mercator
            .map(|bounds| bounds.map(f64::to_bits)),
        size: request.size,
        preserve_values: request.preserve_values,
    }
}

pub(crate) fn source_generation(path: &Path) -> Vec<(u64, u128)> {
    if is_remote(path) {
        return Vec::new();
    }
    ["", ".msk", ".ovr", ".aux.xml"]
        .iter()
        .map(|suffix| {
            let mut filename = path.as_os_str().to_owned();
            filename.push(suffix);
            std::fs::metadata(Path::new(&filename))
                .ok()
                .map(|metadata| {
                    let modified = metadata
                        .modified()
                        .unwrap_or(SystemTime::UNIX_EPOCH)
                        .duration_since(SystemTime::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_nanos();
                    #[cfg(unix)]
                    let stamp = {
                        use sha2::{Digest, Sha256};
                        use std::os::unix::fs::MetadataExt;
                        let mut hash = Sha256::new();
                        hash.update(modified.to_le_bytes());
                        hash.update(metadata.dev().to_le_bytes());
                        hash.update(metadata.ino().to_le_bytes());
                        hash.update(metadata.ctime().to_le_bytes());
                        hash.update(metadata.ctime_nsec().to_le_bytes());
                        u128::from(u64::from_le_bytes(
                            hash.finalize()[..8].try_into().expect("file stamp"),
                        ))
                    };
                    #[cfg(not(unix))]
                    let stamp = modified;
                    (metadata.len(), stamp)
                })
                .unwrap_or((0, 0))
        })
        .collect()
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
    fn compact_cache_drops_hot_copies_before_samples_and_rejects_stale_promotion() {
        let raster = Arc::new(Raster {
            width: 4,
            height: 4,
            data_type: 1,
            values: (0..16).map(|i| i as f64).collect(),
            mask: vec![false; 16],
        });
        let mut cache = RasterCache::new(168, Duration::ZERO, true);
        let mut keys = Vec::new();
        for band in 1..=3 {
            let key = cache_key(
                Path::new("cache-input"),
                ReadRequest {
                    band,
                    ..ReadRequest::default()
                },
            );
            cache.insert(key.clone(), raster.clone(), false);
            keys.push(key);
            assert!(cache.bytes <= cache.capacity);
            assert_eq!(
                cache.bytes,
                cache
                    .entries
                    .values()
                    .map(|entry| entry.bytes)
                    .sum::<usize>()
            );
        }
        assert_eq!(cache.entries.len(), 3);
        for key in &keys {
            assert_eq!(cache.get(key).unwrap().raster().values, raster.values);
        }
        let stale = cache.get(&keys[0]).unwrap();
        cache.insert(
            keys[0].clone(),
            Arc::new(Raster {
                values: vec![2.; 16],
                ..(*raster).clone()
            }),
            false,
        );
        cache.promote(&keys[0], &stale, raster.clone());
        assert_eq!(cache.get(&keys[0]).unwrap().raster().values, vec![2.; 16]);
        assert!(cache.bytes <= cache.capacity);
        assert_eq!(
            cache.bytes,
            cache
                .entries
                .values()
                .map(|entry| entry.bytes)
                .sum::<usize>()
        );
    }

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
                    enqueued: Instant::now(),
                    context: None,
                    path: PathBuf::new(),
                    request: ReadRequest::default(),
                    result,
                    interested: Arc::new(AtomicBool::new(true)),
                }))
                .unwrap();
        }
        drop(pool);
        assert_eq!(completed.load(Ordering::SeqCst), 3);
        assert_eq!(cleaned.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn native_grid_cache_separates_crs_resampling_revision_and_legacy_validity() {
        use crate::layers::{GridRead, Resampling};
        let path = Path::new("tests/fixtures/corpus/red.tif");
        let mut request = ReadRequest {
            grid: Some(GridRead {
                matrix: None,
                point: None,
                epsg: 32632,
                resampling: Resampling::Nearest,
                revision: [1; 32],
            }),
            ..Default::default()
        };
        let base = cache_key(path, request);
        request.grid.as_mut().unwrap().epsg = 3857;
        assert!(base != cache_key(path, request));
        request.grid.as_mut().unwrap().epsg = 32632;
        request.grid.as_mut().unwrap().resampling = Resampling::Bilinear;
        assert!(base != cache_key(path, request));
        request.grid.as_mut().unwrap().resampling = Resampling::Nearest;
        request.grid.as_mut().unwrap().revision = [2; 32];
        assert!(base != cache_key(path, request));
        request.grid.as_mut().unwrap().revision = [1; 32];
        request.grid.as_mut().unwrap().matrix =
            Some(crate::tile_matrix::TileMatrixSet::WebMercatorQuad);
        assert!(base != cache_key(path, request));
        request.grid.as_mut().unwrap().matrix = None;
        request.grid.as_mut().unwrap().point = Some([12.0f64.to_bits(), 45.0f64.to_bits()]);
        assert!(base != cache_key(path, request));
        let point = cache_key(path, request);
        request.grid.as_mut().unwrap().point = Some([13.0f64.to_bits(), 45.0f64.to_bits()]);
        assert!(point != cache_key(path, request));
        request.grid = None;
        assert!(base != cache_key(path, request));
    }

    #[test]
    fn cache_identity_covers_rendering_inputs_and_file_generation() {
        let path = Path::new("tests/fixtures/lagoon-red-preview.png");
        let request = ReadRequest::default();
        let base = cache_key(path, request);
        let changed_size = cache_key(
            path,
            ReadRequest {
                size: [128, 256],
                ..request
            },
        );
        let categorical = cache_key(
            path,
            ReadRequest {
                preserve_values: true,
                ..request
            },
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

    #[test]
    fn abandoning_a_pending_read_revokes_queued_interest() {
        let (_sender, receiver) = mpsc::sync_channel(1);
        let interested = Arc::new(AtomicBool::new(true));
        let pending = PendingRead::Queued {
            receiver,
            interested: interested.clone(),
        };
        drop(pending);
        assert!(!interested.load(Ordering::Acquire));
    }
}

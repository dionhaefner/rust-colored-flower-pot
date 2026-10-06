mod gdal;
mod projection;
mod reader;
mod source;

use futures::{StreamExt, stream};
use rcfp_core::raster::Raster;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicU64, Ordering},
    mpsc,
};
use std::time::Instant;
use tokio::sync::{OnceCell, Semaphore};

#[derive(Clone, Deserialize)]
pub struct Job {
    id: String,
    source: String,
    mode: String,
    #[serde(default = "default_bands")]
    bands: Vec<usize>,
    #[serde(default = "default_size")]
    size: [usize; 2],
    bounds: Option<[f64; 4]>,
    window: Option<[usize; 4]>,
    #[serde(default)]
    overview: usize,
    #[serde(default = "default_bias")]
    overview_bias: f64,
    #[serde(default = "default_method")]
    resampling: String,
    #[serde(default = "default_ranges")]
    ranges: Vec<[f64; 2]>,
}
fn default_bands() -> Vec<usize> {
    vec![1]
}
fn default_size() -> [usize; 2] {
    [256, 256]
}
fn default_bias() -> f64 {
    1.
}
fn default_method() -> String {
    "bilinear".into()
}
fn default_ranges() -> Vec<[f64; 2]> {
    vec![[0., 3000.]]
}

#[derive(Deserialize)]
struct Group {
    name: String,
    jobs: Vec<Job>,
}
#[derive(Deserialize)]
struct Config {
    engine: String,
    workers: usize,
    inflight: usize,
    io_concurrency: usize,
    cache_bytes: usize,
    merge_gap: u64,
    out: PathBuf,
    groups: Vec<Group>,
}

#[derive(Default, Serialize)]
struct Timings {
    source_ms: f64,
    plan_ms: f64,
    fetch_decode_ms: f64,
    sample_ms: f64,
    encode_ms: f64,
    total_ms: f64,
}
struct Outcome {
    metadata: Option<Value>,
    rasters: Vec<Raster>,
    png: Vec<u8>,
    timings: Timings,
    selected_ifd: Option<usize>,
}
fn ms(started: Instant) -> f64 {
    started.elapsed().as_secs_f64() * 1000.
}

fn encode(job: &Job, rasters: &[Raster]) -> Result<Vec<u8>, String> {
    if job.mode == "metadata" {
        return Ok(vec![]);
    }
    if rasters.len() == 3 {
        if job.ranges.len() != 3 {
            return Err("RGB needs three ranges".into());
        }
        rcfp_core::image::stretched_rgb_png(
            [&rasters[0], &rasters[1], &rasters[2]],
            [job.ranges[0], job.ranges[1], job.ranges[2]],
        )
    } else if rasters.len() == 1 {
        let first = &rasters[0];
        let pixels = rcfp_core::image::normalized_pixels(first, job.ranges[0]);
        rcfp_core::image::grayscale_png(first.width, first.height, &pixels)
    } else {
        Err("probe supports one or three selected bands".into())
    }
}

type SourceCell = Arc<OnceCell<Result<Arc<source::Source>, String>>>;
struct AsyncEngine {
    sources: Mutex<HashMap<String, SourceCell>>,
    client: reqwest::Client,
    io: Arc<Semaphore>,
    cpu: Arc<Semaphore>,
    cache: Arc<Mutex<source::Cache>>,
    traffic: Arc<reader::Traffic>,
    decoded: Arc<AtomicU64>,
    merge_gap: u64,
    hybrid: bool,
}

impl AsyncEngine {
    async fn source(&self, path: &str) -> Result<Arc<source::Source>, String> {
        let cell = self
            .sources
            .lock()
            .unwrap()
            .entry(path.into())
            .or_insert_with(|| Arc::new(OnceCell::new()))
            .clone();
        cell.get_or_init(|| async {
            let reader = reader::Reader::new(
                path,
                self.client.clone(),
                self.io.clone(),
                self.traffic.clone(),
                self.merge_gap,
            )?;
            source::Source::open(reader).await.map(Arc::new)
        })
        .await
        .clone()
    }

    async fn blocking<T: Send + 'static>(
        &self,
        function: impl FnOnce() -> Result<T, String> + Send + 'static,
    ) -> Result<T, String> {
        let permit = self
            .cpu
            .clone()
            .acquire_owned()
            .await
            .map_err(|e| e.to_string())?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            function()
        })
        .await
        .map_err(|e| e.to_string())?
    }

    async fn run(&self, job: Job) -> Result<Outcome, String> {
        let start = Instant::now();
        let mut timings = Timings::default();
        let stage = Instant::now();
        let source = self.source(&job.source).await?;
        timings.source_ms = ms(stage);
        if job.mode == "metadata" {
            timings.total_ms = ms(start);
            return Ok(Outcome {
                metadata: Some(source.metadata()),
                rasters: vec![],
                png: vec![],
                timings,
                selected_ifd: None,
            });
        }
        let stage = Instant::now();
        let grid = {
            let source = source.clone();
            let job = job.clone();
            self.blocking(move || source.grid(&job)).await?
        };
        let selected_ifd = Some(grid.ifd);
        let hybrid = self.hybrid && job.mode == "tile";
        let (grid, sample_job, offset) = if hybrid {
            let ifd = &source.ifds[grid.ifd];
            let valid = grid
                .positions
                .iter()
                .filter(|p| p.iter().all(|v| v.is_finite()));
            let (mut minx, mut miny, mut maxx, mut maxy) = (
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            );
            for p in valid {
                minx = minx.min(p[0]);
                miny = miny.min(p[1]);
                maxx = maxx.max(p[0]);
                maxy = maxy.max(p[1]);
            }
            let x = (minx.floor() - 2.).max(0.).min(ifd.image_width() as f64) as usize;
            let y = (miny.floor() - 2.).max(0.).min(ifd.image_height() as f64) as usize;
            let right = (maxx.ceil() + 2.).max(0.).min(ifd.image_width() as f64) as usize;
            let bottom = (maxy.ceil() + 2.).max(0.).min(ifd.image_height() as f64) as usize;
            let (w, h) = (right.saturating_sub(x), bottom.saturating_sub(y));
            if w == 0 || h == 0 {
                let data_type = source.data_type(grid.ifd)?;
                let rasters: Vec<_> = job
                    .bands
                    .iter()
                    .map(|_| Raster {
                        width: job.size[1],
                        height: job.size[0],
                        data_type,
                        values: vec![0.; job.size[0] * job.size[1]],
                        mask: vec![true; job.size[0] * job.size[1]],
                    })
                    .collect();
                timings.plan_ms = ms(stage);
                let encode_started = Instant::now();
                let png = encode(&job, &rasters)?;
                timings.encode_ms = ms(encode_started);
                timings.total_ms = ms(start);
                return Ok(Outcome {
                    metadata: None,
                    rasters,
                    png,
                    timings,
                    selected_ifd,
                });
            }
            if w * h > 4 * 1024 * 1024 {
                return Err("hybrid source window exceeds four million pixels".into());
            }
            let mut sample_job = job.clone();
            sample_job.mode = "window".into();
            sample_job.resampling = "nearest".into();
            let positions = (0..h)
                .flat_map(|row| {
                    (0..w).map(move |col| [(x + col) as f64 + 0.5, (y + row) as f64 + 0.5])
                })
                .collect();
            (
                source::Grid {
                    positions,
                    size: [h, w],
                    ..grid
                },
                sample_job,
                [x, y],
            )
        } else {
            (grid, job.clone(), [0, 0])
        };
        let linear = sample_job.resampling == "bilinear" && sample_job.mode != "window";
        let keys = source.tile_keys(&grid, linear)?;
        timings.plan_ms = ms(stage);
        let stage = Instant::now();
        let blocks = source
            .blocks(
                &job.source,
                grid.ifd,
                &keys,
                self.cache.clone(),
                self.cpu.clone(),
                self.decoded.clone(),
            )
            .await?;
        let masks = if let Some(mask_ifd) = source.mask_ifd(grid.ifd) {
            let mask_grid = source::Grid {
                ifd: mask_ifd,
                positions: grid.positions.clone(),
                size: grid.size,
                transform: grid.transform,
                epsg: grid.epsg,
            };
            let mask_keys = source.tile_keys(&mask_grid, linear)?;
            source
                .blocks(
                    &job.source,
                    mask_ifd,
                    &mask_keys,
                    self.cache.clone(),
                    self.cpu.clone(),
                    self.decoded.clone(),
                )
                .await?
        } else {
            HashMap::new()
        };
        timings.fetch_decode_ms = ms(stage);
        let stage = Instant::now();
        let rasters = {
            let source = source.clone();
            let job = job.clone();
            self.blocking(move || {
                let rasters = source.sample(&sample_job, &grid, &blocks, &masks)?;
                if hybrid {
                    let mut gt = grid.transform;
                    gt[0] += offset[0] as f64 * gt[1] + offset[1] as f64 * gt[2];
                    gt[3] += offset[0] as f64 * gt[4] + offset[1] as f64 * gt[5];
                    let nodata = source.ifds[grid.ifd]
                        .gdal_nodata()
                        .map(|v| v.trim_end_matches('\0').parse::<f64>())
                        .transpose()
                        .map_err(|e| e.to_string())?;
                    gdal::api()?.warp_memory(&rasters, gt, grid.epsg, nodata, &job)
                } else {
                    Ok(rasters)
                }
            })
            .await?
        };
        timings.sample_ms = ms(stage);
        let stage = Instant::now();
        let (rasters, png) = {
            let job = job.clone();
            self.blocking(move || {
                let png = encode(&job, &rasters)?;
                Ok((rasters, png))
            })
            .await?
        };
        timings.encode_ms = ms(stage);
        timings.total_ms = ms(start);
        Ok(Outcome {
            metadata: None,
            rasters,
            png,
            timings,
            selected_ifd,
        })
    }
}

type Work = (Job, tokio::sync::oneshot::Sender<Result<Outcome, String>>);
struct GdalPool {
    senders: Vec<mpsc::SyncSender<Work>>,
    threads: Vec<std::thread::JoinHandle<()>>,
}
impl GdalPool {
    fn new(workers: usize) -> Result<Self, String> {
        gdal::api()?;
        let mut senders = Vec::new();
        let mut threads = Vec::new();
        for _ in 0..workers {
            let (tx, rx) = mpsc::sync_channel::<Work>(128);
            senders.push(tx);
            threads.push(std::thread::spawn(move || {
                let mut worker = gdal::Worker::new();
                while let Ok((job, tx)) = rx.recv() {
                    let start = Instant::now();
                    let result = worker.run(&job).and_then(|(metadata, rasters)| {
                        let read_ms = ms(start);
                        let stage = Instant::now();
                        let png = encode(&job, &rasters)?;
                        Ok(Outcome {
                            metadata,
                            rasters,
                            png,
                            selected_ifd: None,
                            timings: Timings {
                                fetch_decode_ms: read_ms,
                                encode_ms: ms(stage),
                                total_ms: ms(start),
                                ..Default::default()
                            },
                        })
                    });
                    let _ = tx.send(result);
                }
            }));
        }
        Ok(Self { senders, threads })
    }
}
impl Drop for GdalPool {
    fn drop(&mut self) {
        self.senders.clear();
        for thread in self.threads.drain(..) {
            let _ = thread.join();
        }
    }
}

fn save(
    out: &std::path::Path,
    index: usize,
    job: &Job,
    outcome: Result<Outcome, String>,
    submitted_ms: f64,
) -> Result<Value, String> {
    let Ok(outcome) = outcome else {
        return Ok(
            json!({"id": job.id, "status": "error", "error": outcome.err(), "submitted_ms": submitted_ms}),
        );
    };
    let mut row = json!({"id": job.id, "status": "ok", "timings": outcome.timings, "submitted_ms": submitted_ms,
        "selected_ifd": outcome.selected_ifd, "metadata": outcome.metadata});
    let prefix = format!("{index:04}");
    if !outcome.rasters.is_empty() {
        let mut samples = Vec::new();
        let mut masks = Vec::new();
        for raster in &outcome.rasters {
            for &value in &raster.values {
                samples.extend_from_slice(&value.to_le_bytes());
            }
            masks.extend(raster.mask.iter().map(|v| u8::from(*v)));
        }
        fs::write(out.join(format!("{prefix}.f64")), &samples).map_err(|e| e.to_string())?;
        fs::write(out.join(format!("{prefix}.mask")), &masks).map_err(|e| e.to_string())?;
        fs::write(out.join(format!("{prefix}.png")), &outcome.png).map_err(|e| e.to_string())?;
        row["artifact"] = json!(prefix);
        row["shape"] = json!([
            outcome.rasters.len(),
            outcome.rasters[0].height,
            outcome.rasters[0].width
        ]);
        row["data_types"] = json!(
            outcome
                .rasters
                .iter()
                .map(|r| r.data_type)
                .collect::<Vec<_>>()
        );
        row["sample_sha256"] = json!(format!("{:x}", Sha256::digest(&samples)));
        row["mask_sha256"] = json!(format!("{:x}", Sha256::digest(&masks)));
        row["png_bytes"] = json!(outcome.png.len());
    }
    Ok(row)
}

fn memory() -> Value {
    let status = fs::read_to_string("/proc/self/status").unwrap_or_default();
    json!(
        status
            .lines()
            .filter(|line| line.starts_with("VmHWM:") || line.starts_with("VmRSS:"))
            .collect::<Vec<_>>()
    )
}

async fn execute(config: Config) -> Result<Value, String> {
    let started = Instant::now();
    if !matches!(
        config.engine.as_str(),
        "rcfp" | "async-native" | "async-gdal"
    ) {
        return Err("unknown engine".into());
    }
    if config.workers == 0
        || config.workers > 64
        || config.inflight == 0
        || config.inflight > 128
        || config.io_concurrency == 0
        || config.io_concurrency > 128
    {
        return Err("invalid concurrency limits".into());
    }
    for group in &config.groups {
        for job in &group.jobs {
            if !matches!(job.mode.as_str(), "metadata" | "window" | "tile")
                || !matches!(job.resampling.as_str(), "nearest" | "bilinear")
                || job.size.iter().any(|v| *v < 2 || *v > 1024)
                || job.ranges.is_empty()
            {
                return Err("invalid job".into());
            }
            if job
                .window
                .is_some_and(|w| w[2] == 0 || w[3] == 0 || w[2] * w[3] > 4 * 1024 * 1024)
            {
                return Err("invalid window size".into());
            }
        }
    }
    fs::create_dir_all(&config.out).map_err(|e| e.to_string())?;
    let pool = (config.engine == "rcfp")
        .then(|| GdalPool::new(config.workers))
        .transpose()?;
    let engine = AsyncEngine {
        sources: Mutex::new(HashMap::new()),
        client: reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .map_err(|e| e.to_string())?,
        io: Arc::new(Semaphore::new(config.io_concurrency)),
        cpu: Arc::new(Semaphore::new(config.workers)),
        cache: Arc::new(Mutex::new(source::Cache::new(config.cache_bytes))),
        traffic: Arc::new(reader::Traffic::default()),
        decoded: Arc::new(AtomicU64::new(0)),
        merge_gap: config.merge_gap,
        hybrid: config.engine == "async-gdal",
    };
    let startup_ms = ms(started);
    let mut groups = Vec::new();
    let mut sequence = 0;
    for group in &config.groups {
        let phase = Instant::now();
        let outcomes: Vec<_> =
            stream::iter(group.jobs.iter().cloned().enumerate().map(|(index, job)| {
                let engine = &engine;
                let pool = &pool;
                async move {
                    let submitted_ms = ms(phase);
                    let result = if let Some(pool) = pool {
                        let (tx, rx) = tokio::sync::oneshot::channel();
                        pool.senders[index % pool.senders.len()]
                            .send((job.clone(), tx))
                            .map_err(|e| e.to_string())?;
                        rx.await.map_err(|e| e.to_string())?
                    } else {
                        engine.run(job.clone()).await
                    };
                    Ok::<_, String>((index, job, result, submitted_ms, ms(phase)))
                }
            }))
            .buffer_unordered(config.inflight)
            .collect()
            .await;
        let completion_ms = ms(phase);
        let mut outcomes = outcomes.into_iter().collect::<Result<Vec<_>, _>>()?;
        outcomes.sort_by_key(|r| r.0);
        let mut rows = Vec::new();
        for (_, job, result, submitted_ms, completed_ms) in outcomes {
            let mut row = save(&config.out, sequence, &job, result, submitted_ms)?;
            row["completed_ms"] = json!(completed_ms);
            rows.push(row);
            sequence += 1;
        }
        let summary = json!({"name": group.name, "completion_ms": completion_ms, "jobs": rows,
            "reader_requests_cumulative": engine.traffic.requests.load(Ordering::Relaxed), "reader_bytes_cumulative": engine.traffic.bytes.load(Ordering::Relaxed),
            "decoded_blocks_cumulative": engine.decoded.load(Ordering::Relaxed), "cache": engine.cache.lock().unwrap().report(), "memory": memory()});
        groups.push(summary);
    }
    drop(pool);
    Ok(
        json!({"engine": config.engine, "async_tiff": "0.4.0", "startup_ms": startup_ms,
        "process_ms": ms(started), "groups": groups, "memory": memory()}),
    )
}

fn main() {
    let result = (|| {
        let path = std::env::args()
            .nth(1)
            .ok_or("usage: rcfp-raster-probe CONFIG.json")?;
        let config: Config = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .max_blocking_threads(config.workers.max(config.io_concurrency) + 4)
            .enable_all()
            .build()
            .map_err(|e| e.to_string())?;
        runtime.block_on(execute(config))
    })();
    match result {
        Ok(result) => println!("{}", serde_json::to_string(&result).unwrap()),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

//! Fixed-cardinality application timings, shared by all deployment adapters.
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

#[derive(Clone, Copy)]
pub(crate) enum Stage {
    Selection,
    QueueWait,
    SourceAccess,
    RasterRead,
    Coloring,
    Encoding,
    RenderWait,
    Request,
    Delivery,
    Prefetch,
    HttpQueueWait,
    RemoteSourceAccess,
    RemoteRasterRead,
}
const STAGES: [&str; 13] = [
    "selection",
    "queue_wait",
    "source_access",
    "raster_read",
    "coloring",
    "encoding",
    "render_wait",
    "request",
    "delivery",
    "prefetch",
    "http_queue_wait",
    "remote_source_access",
    "remote_raster_read",
];
const BOUNDS_US: [u64; 11] = [
    100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000, 5000000, 30000000,
];
pub(crate) enum RasterBackend {
    Gdal,
    Tiff,
}
#[derive(Default)]
struct Histogram {
    sum_us: AtomicU64,
    bins: [AtomicU64; 12],
}
struct HistogramSnapshot {
    count: u64,
    sum_us: u64,
    buckets: [u64; 11],
}
impl Histogram {
    fn snapshot(&self) -> HistogramSnapshot {
        // Disjoint bins keep cumulative buckets monotone even during a live scrape.
        let bins = self.bins.each_ref().map(|bin| bin.load(Ordering::Relaxed));
        let mut count = 0;
        let buckets = std::array::from_fn(|i| {
            count += bins[i];
            count
        });
        HistogramSnapshot {
            count: count + bins[11],
            sum_us: self.sum_us.load(Ordering::Relaxed),
            buckets,
        }
    }
}
#[derive(Default)]
pub(crate) struct Metrics {
    timings: [Histogram; 13],
    gdal_reads: AtomicU64,
    tiff_reads: AtomicU64,
    source_hits: AtomicU64,
    source_misses: AtomicU64,
    delivery_failed: AtomicU64,
    delivery_body_bytes: AtomicU64,
    pub(crate) http_overloaded: AtomicU64,
    pub(crate) render_overloaded: AtomicU64,
}
#[derive(Clone)]
pub(crate) struct Context {
    metrics: Arc<Metrics>,
    request_us: Option<Arc<[AtomicU64; 13]>>,
}
thread_local! { static CURRENT: RefCell<Option<Context>> = const { RefCell::new(None) }; }
pub(crate) fn current() -> Option<Context> {
    CURRENT.with(|slot| slot.borrow().clone())
}
pub(crate) fn scope<T>(context: Option<Context>, f: impl FnOnce() -> T) -> T {
    struct Restore(Option<Context>);
    impl Drop for Restore {
        fn drop(&mut self) {
            CURRENT.with(|slot| *slot.borrow_mut() = self.0.take());
        }
    }
    let _restore = Restore(CURRENT.with(|slot| slot.replace(context)));
    f()
}
impl Metrics {
    pub(crate) fn context(self: &Arc<Self>, request: bool) -> Context {
        Context {
            metrics: self.clone(),
            request_us: request.then(|| Arc::new(std::array::from_fn(|_| AtomicU64::new(0)))),
        }
    }
    pub(crate) fn record(&self, stage: Stage, elapsed: Duration) {
        let us = elapsed.as_micros().min(u64::MAX as u128) as u64;
        let h = &self.timings[stage as usize];
        h.sum_us.fetch_add(us, Ordering::Relaxed);
        let bin = BOUNDS_US
            .iter()
            .position(|bound| us <= *bound)
            .unwrap_or(11);
        h.bins[bin].fetch_add(1, Ordering::Relaxed);
    }
    pub(crate) fn delivery(&self, elapsed: Duration, failed: bool, body_bytes: usize) {
        self.record(Stage::Delivery, elapsed);
        if failed {
            self.delivery_failed.fetch_add(1, Ordering::Relaxed);
        } else {
            self.delivery_body_bytes
                .fetch_add(body_bytes as u64, Ordering::Relaxed);
        }
    }
    pub(crate) fn snapshot(&self) -> Value {
        let mut stages = serde_json::Map::new();
        for (name, h) in STAGES.iter().zip(&self.timings) {
            let h = h.snapshot();
            stages.insert((*name).into(), json!({"count":h.count, "sum_seconds":h.sum_us as f64 / 1e6, "buckets":BOUNDS_US.iter().zip(&h.buckets).map(|(bound, count)| json!({"le_seconds":*bound as f64 / 1e6,"count":*count})).collect::<Vec<_>>() }));
        }
        json!({"stages":stages,"raster_backend_reads":{"gdal":self.gdal_reads.load(Ordering::Relaxed),"tiff":self.tiff_reads.load(Ordering::Relaxed)},"source_handle_hits":self.source_hits.load(Ordering::Relaxed),"source_handle_misses":self.source_misses.load(Ordering::Relaxed),"delivery_failed":self.delivery_failed.load(Ordering::Relaxed), "delivery_body_bytes":self.delivery_body_bytes.load(Ordering::Relaxed), "http_overloaded":self.http_overloaded.load(Ordering::Relaxed), "render_overloaded":self.render_overloaded.load(Ordering::Relaxed)})
    }
    pub(crate) fn prometheus(&self) -> String {
        use std::fmt::Write;
        let mut output = String::from(
            "# HELP rcfp_stage_duration_seconds Observed application stage wall time.\n# TYPE rcfp_stage_duration_seconds histogram\n",
        );
        for (name, h) in STAGES.iter().zip(&self.timings) {
            let h = h.snapshot();
            for (bound, bucket) in BOUNDS_US.iter().zip(&h.buckets) {
                let _ = writeln!(
                    output,
                    "rcfp_stage_duration_seconds_bucket{{stage=\"{name}\",le=\"{}\"}} {}",
                    *bound as f64 / 1e6,
                    *bucket
                );
            }
            let count = h.count;
            let _ = writeln!(
                output,
                "rcfp_stage_duration_seconds_bucket{{stage=\"{name}\",le=\"+Inf\"}} {count}\nrcfp_stage_duration_seconds_count{{stage=\"{name}\"}} {count}\nrcfp_stage_duration_seconds_sum{{stage=\"{name}\"}} {}",
                h.sum_us as f64 / 1e6
            );
        }
        output.push_str("# HELP rcfp_raster_backend_reads_total Pixel reader invocations by backend.\n# TYPE rcfp_raster_backend_reads_total counter\n");
        for (backend, counter) in [("gdal", &self.gdal_reads), ("tiff", &self.tiff_reads)] {
            let _ = writeln!(
                output,
                "rcfp_raster_backend_reads_total{{backend=\"{backend}\"}} {}",
                counter.load(Ordering::Relaxed)
            );
        }
        for (name, value) in [
            ("source_handle_hits", &self.source_hits),
            ("source_handle_misses", &self.source_misses),
            ("delivery_failed", &self.delivery_failed),
            ("delivery_body_bytes", &self.delivery_body_bytes),
            ("http_overloaded", &self.http_overloaded),
            ("render_overloaded", &self.render_overloaded),
        ] {
            let _ = writeln!(
                output,
                "# TYPE rcfp_{name}_total counter\nrcfp_{name}_total {}",
                value.load(Ordering::Relaxed)
            );
        }
        output
    }
}
impl Context {
    pub(crate) fn record(&self, stage: Stage, elapsed: Duration) {
        self.metrics.record(stage, elapsed);
        if let Some(request) = &self.request_us {
            request[stage as usize].fetch_add(
                elapsed.as_micros().min(u64::MAX as u128) as u64,
                Ordering::Relaxed,
            );
        }
    }
    pub(crate) fn trace(&self) -> Value {
        let mut stages = serde_json::Map::new();
        if let Some(request) = &self.request_us {
            for (name, us) in STAGES.iter().zip(request.iter()) {
                stages.insert(
                    (*name).into(),
                    json!(us.load(Ordering::Relaxed) as f64 / 1e6),
                );
            }
        }
        json!(stages)
    }
}
pub(crate) struct Timer {
    stage: Stage,
    started: Instant,
    context: Option<Context>,
}
pub(crate) fn timer(stage: Stage) -> Timer {
    Timer {
        stage,
        started: Instant::now(),
        context: current(),
    }
}
impl Drop for Timer {
    fn drop(&mut self) {
        if let Some(context) = &self.context {
            context.record(self.stage, self.started.elapsed());
        }
    }
}
pub(crate) fn raster_backend_read(backend: RasterBackend) {
    if let Some(context) = current() {
        let counter = match backend {
            RasterBackend::Gdal => &context.metrics.gdal_reads,
            RasterBackend::Tiff => &context.metrics.tiff_reads,
        };
        counter.fetch_add(1, Ordering::Relaxed);
    }
}

pub(crate) fn source_hit(hit: bool) {
    if let Some(context) = current() {
        let counter = if hit {
            &context.metrics.source_hits
        } else {
            &context.metrics.source_misses
        };
        counter.fetch_add(1, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn live_scrapes_keep_histogram_buckets_monotone() {
        let metrics = Arc::new(Metrics::default());
        let workers = (0..4)
            .map(|_| {
                let metrics = metrics.clone();
                std::thread::spawn(move || {
                    for i in 0..50_000 {
                        metrics.record(Stage::QueueWait, Duration::from_micros(i * 1000));
                    }
                })
            })
            .collect::<Vec<_>>();
        for _ in 0..1000 {
            let h = metrics.timings[Stage::QueueWait as usize].snapshot();
            assert!(h.buckets.windows(2).all(|pair| pair[0] <= pair[1]));
            assert!(h.buckets[10] <= h.count);
        }
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(
            metrics.timings[Stage::QueueWait as usize].snapshot().count,
            200_000
        );
    }

    #[test]
    fn fixed_buckets_and_scope_restore_include_errors() {
        let metrics = Arc::new(Metrics::default());
        scope(Some(metrics.context(true)), || {
            let _timer = timer(Stage::Encoding);
            raster_backend_read(RasterBackend::Gdal);
            raster_backend_read(RasterBackend::Tiff);
            scope(None, || {
                assert!(current().is_none());
            });
            assert!(current().is_some());
        });
        assert!(current().is_none());
        metrics.record(Stage::QueueWait, Duration::from_millis(2));
        let v = metrics.snapshot();
        assert_eq!(v["raster_backend_reads"]["gdal"], 1);
        assert_eq!(v["raster_backend_reads"]["tiff"], 1);
        assert!(metrics.prometheus().contains("backend=\"tiff\"} 1"));
        assert_eq!(v["stages"]["encoding"]["count"], 1);
        assert_eq!(v["stages"]["queue_wait"]["buckets"][2]["count"], 0);
        assert_eq!(v["stages"]["queue_wait"]["buckets"][3]["count"], 1);
        assert!(metrics.prometheus().contains("le=\"+Inf\"} 1"));
    }
}

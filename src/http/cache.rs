//! Bounded encoded-image reuse with shared results for identical in-flight renders.

use crate::{Result, storage::Metadata};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

fn public_location(path: &str) -> String {
    let path = path.strip_prefix("/vsicurl/").unwrap_or(path);
    if let Ok(mut url) = url::Url::parse(path) {
        let _ = url.set_username("");
        let _ = url.set_password(None);
        url.set_query(None);
        url.set_fragment(None);
        url.to_string()
    } else {
        path.to_owned()
    }
}

#[derive(Clone, Hash, Eq, PartialEq, Serialize)]
struct SourceKey {
    path: String,
    generation: Vec<(u64, u128)>,
    metadata: Vec<u64>,
    policy: String,
}
#[derive(Clone, Hash, Eq, PartialEq, Serialize)]
pub(super) struct RenderKey {
    route: String,
    parameters: Vec<(String, String)>,
    sources: Vec<SourceKey>,
    semantics: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn key(name: &str) -> RenderKey {
        RenderKey {
            route: name.into(),
            parameters: vec![],
            sources: vec![],
            semantics: String::new(),
        }
    }

    #[test]
    fn validator_accepts_full_width_file_generation_stamps() {
        let mut key = key("raster");
        key.sources.push(SourceKey {
            path: "raster.tif".into(),
            generation: vec![(123, u128::MAX)],
            metadata: vec![],
            policy: String::new(),
        });
        assert_eq!(key.validator("image/png", &[1]).len(), 66);
    }

    #[test]
    fn signed_access_is_private_but_explicit_revision_is_stable() {
        let metadata = Metadata {
            keys: BTreeMap::new(),
            bounds: [0.; 4],
            convex_hull: serde_json::Value::Null,
            valid_percentage: 100.,
            range: [0., 1.],
            mean: 0.,
            stdev: 0.,
            percentiles: vec![],
            metadata: serde_json::json!({"revision":"verified-content"}),
        };
        let a = RenderKey::new(
            "/singleband/scene/red/preview.png",
            &BTreeMap::new(),
            [(
                Path::new("https://user:secret@example.com/a.tif?signature=one"),
                &metadata,
            )],
        );
        let b = RenderKey::new(
            "/singleband/scene/red/preview.png",
            &BTreeMap::new(),
            [(
                Path::new("https://user:renewed@example.com/a.tif?signature=two"),
                &metadata,
            )],
        );
        assert!(a != b);
        assert_eq!(
            a.validator("image/png", &[1]),
            b.validator("image/png", &[1])
        );
        assert_ne!(
            a.validator("image/png", &[1]),
            b.validator("image/png", &[2])
        );
        let mut changed = metadata.clone();
        changed.metadata["revision"] = "new-content".into();
        let c = RenderKey::new(
            "/singleband/scene/red/preview.png",
            &BTreeMap::new(),
            [(Path::new("https://example.com/a.tif"), &changed)],
        );
        assert_ne!(
            a.validator("image/png", &[1]),
            c.validator("image/png", &[1])
        );
    }

    #[test]
    fn changing_a_source_during_render_never_retains_the_result() {
        let folder = tempfile::tempdir().unwrap();
        let path = folder.path().join("scene.tif");
        std::fs::write(&path, [1]).unwrap();
        let mut key = key("a");
        key.sources.push(SourceKey {
            path: path.to_string_lossy().into(),
            generation: crate::execution::source_generation(&path),
            metadata: vec![],
            policy: String::new(),
        });
        let cache = RenderCache::new(4096, Duration::ZERO, Duration::from_secs(1));
        assert!(
            cache
                .render(key, false, || {
                    std::fs::write(&path, [2, 3]).unwrap();
                    Ok(vec![1])
                })
                .is_err()
        );
        assert_eq!(cache.metrics()["entries"], 0);
        assert_eq!(cache.metrics()["in_flight"], 0);
    }

    #[test]
    fn evicts_within_budget_and_never_caches_errors_or_oversized_images() {
        let capacity = key("a").weight(40) * 2;
        let cache = RenderCache::new(capacity, Duration::ZERO, Duration::from_secs(1));
        for name in ["a", "b", "c"] {
            cache.render(key(name), false, || Ok(vec![1; 40])).unwrap();
        }
        assert_eq!(cache.metrics()["entries"], 2);
        assert_eq!(cache.metrics()["bytes"], capacity);
        assert!(
            cache
                .render(key("error"), false, || Err("failure".into()))
                .is_err()
        );
        assert_eq!(
            cache.render(key("error"), false, || Ok(vec![2])).unwrap(),
            vec![2]
        );
        cache
            .render(key("large"), false, || Ok(vec![3; capacity + 1]))
            .unwrap();
        assert!(cache.metrics()["bytes"].as_u64().unwrap() <= capacity as u64);
        assert!(
            !cache
                .state
                .lock()
                .unwrap()
                .entries
                .contains_key(&key("large"))
        );
    }

    #[test]
    fn remote_ttl_expires_and_zero_ttl_disables_retention() {
        let cache = RenderCache::new(4096, Duration::ZERO, Duration::from_secs(1));
        cache.render(key("a"), true, || Ok(vec![1])).unwrap();
        assert_eq!(cache.metrics()["entries"], 0);
        let cache = RenderCache::new(4096, Duration::from_secs(1), Duration::from_secs(1));
        cache.render(key("a"), true, || Ok(vec![1])).unwrap();
        cache
            .state
            .lock()
            .unwrap()
            .entries
            .get_mut(&key("a"))
            .unwrap()
            .inserted = Instant::now() - Duration::from_secs(2);
        assert_eq!(
            cache.render(key("a"), true, || Ok(vec![2])).unwrap(),
            vec![2]
        );
    }

    #[test]
    fn identical_concurrent_requests_share_one_render_without_retention() {
        let cache = Arc::new(RenderCache::new(0, Duration::ZERO, Duration::from_secs(5)));
        let calls = Arc::new(AtomicUsize::new(0));
        let (started, ready) = std::sync::mpsc::channel();
        let (finish, proceed) = std::sync::mpsc::channel();
        let leader_cache = cache.clone();
        let leader_calls = calls.clone();
        let leader = std::thread::spawn(move || {
            leader_cache
                .render(key("a"), false, || {
                    leader_calls.fetch_add(1, Ordering::SeqCst);
                    started.send(()).unwrap();
                    proceed.recv().unwrap();
                    Ok(vec![7])
                })
                .unwrap()
        });
        ready.recv_timeout(Duration::from_secs(5)).unwrap();
        let followers = (0..3)
            .map(|_| {
                let cache = cache.clone();
                let calls = calls.clone();
                std::thread::spawn(move || {
                    cache
                        .render(key("a"), false, || {
                            calls.fetch_add(1, Ordering::SeqCst);
                            Ok(vec![8])
                        })
                        .unwrap()
                })
            })
            .collect::<Vec<_>>();
        let deadline = Instant::now() + Duration::from_secs(5);
        while cache.metrics()["coalesced"] != 3 {
            assert!(Instant::now() < deadline);
            std::thread::yield_now();
        }
        finish.send(()).unwrap();
        assert_eq!(leader.join().unwrap(), vec![7]);
        for follower in followers {
            assert_eq!(follower.join().unwrap(), vec![7]);
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert_eq!(cache.metrics()["in_flight"], 0);
        assert_eq!(cache.metrics()["entries"], 0);
        assert_eq!(cache.metrics()["bytes"], 0);
        assert_eq!(
            cache.render(key("a"), false, || Ok(vec![9])).unwrap(),
            vec![9]
        );
    }

    #[test]
    fn eviction_releases_allocated_capacity() {
        let key = key("a");
        let cache = RenderCache::new(key.weight(4096), Duration::ZERO, Duration::from_secs(1));
        cache
            .render(key.clone(), false, || {
                let mut bytes = Vec::with_capacity(4096);
                bytes.push(1);
                Ok(bytes)
            })
            .unwrap();
        let mut state = cache.state.lock().unwrap();
        RenderCache::remove(&mut state, &key);
        assert_eq!(state.bytes, 0);
        assert!(state.entries.is_empty());
    }

    #[test]
    fn panicking_leader_releases_followers_and_allows_retry() {
        let cache = RenderCache::new(100, Duration::ZERO, Duration::from_secs(1));
        assert!(
            std::panic::catch_unwind(|| cache.render(key("a"), false, || panic!("render failed")))
                .is_err()
        );
        assert_eq!(cache.metrics()["in_flight"], 0);
        assert_eq!(
            cache.render(key("a"), false, || Ok(vec![9])).unwrap(),
            vec![9]
        );
    }
}

impl RenderKey {
    fn current(&self) -> bool {
        self.sources.iter().all(|source| {
            source.generation == crate::execution::source_generation(Path::new(&source.path))
        })
    }

    pub fn layer(
        route: &str,
        parameters: &BTreeMap<String, String>,
        layer: &crate::layers::Layer,
        spec: &crate::layers::RenderSpec,
    ) -> Self {
        let mut key = Self::new(route, parameters, []);
        key.sources = layer
            .observations
            .iter()
            .flat_map(|o| o.assets.values())
            .map(|a| SourceKey {
                path: a.location.to_string_lossy().into_owned(),
                generation: crate::execution::source_generation(&a.location),
                metadata: vec![],
                policy: String::new(),
            })
            .collect();
        key.semantics
            .push_str(&serde_json::to_string(&(layer.identity(), spec)).expect("layer identity"));
        key
    }

    pub fn validator(&self, content_type: &str, bytes: &[u8]) -> String {
        let mut hash = Sha256::new();
        let sources = self
            .sources
            .iter()
            .map(|s| {
                let revisioned = serde_json::from_str::<serde_json::Value>(&s.policy)
                    .ok()
                    .is_some_and(|v| !v[0].is_null());
                let path = if revisioned || self.route.starts_with("/layers/") {
                    String::new()
                } else {
                    public_location(&s.path)
                };
                let generation = s
                    .generation
                    .iter()
                    .map(|(size, stamp)| (*size, stamp.to_string()))
                    .collect::<Vec<_>>();
                serde_json::json!([path, generation, s.metadata, s.policy])
            })
            .collect::<Vec<_>>();
        hash.update(
            serde_json::to_vec(&(
                self.route.as_str(),
                &self.parameters,
                &self.semantics,
                sources,
            ))
            .expect("render identity"),
        );
        hash.update(content_type);
        hash.update(bytes);
        format!("\"{:x}\"", hash.finalize())
    }

    pub fn new<'a>(
        route: &str,
        parameters: &BTreeMap<String, String>,
        sources: impl IntoIterator<Item = (&'a Path, &'a Metadata)>,
    ) -> Self {
        let defaults = crate::settings::defaults();
        Self {
            route: route.into(),
            semantics: serde_json::json!({
                "contract": "rcfp-render-1",
                "version": crate::IMPLEMENTATION_VERSION,
                "settings": (["TC_REPROJECTION_METHOD", "TC_RESAMPLING_METHOD", "TC_NATIVE_DIRECT_READ", "TC_RUST_UTM_FAST", "TC_RUST_MERCATOR_FAST", "TC_PNG_COMPRESS_LEVEL", "TC_DEFAULT_TILE_SIZE"].map(|k| {
                    let default = defaults.get(k.trim_start_matches("TC_"));
                    let value = crate::settings::var(k).ok().or_else(|| default.map(|v| v.as_str().map(str::to_owned).unwrap_or_else(|| v.to_string())));
                    let value = if let Some(value) = default.and_then(serde_json::Value::as_bool) {
                        crate::settings::flag(k, value).ok().map(|v| v.to_string())
                    } else { value.map(|v| serde_json::from_str::<serde_json::Value>(&v).map(|v| v.to_string()).unwrap_or(v)) };
                    (k, value)
                }))
            }).to_string(),
            parameters: parameters
                .iter()
                .filter(|(k, _)| ["tile_size", "color_transform", "colormap", "explicit_color_map", "stretch_range", "band", "r", "g", "b", "r_band", "g_band", "b_band", "r_range", "g_range", "b_range", "expression", "v1", "v2", "v3", "v4", "v5", "format", "quality"].contains(&k.as_str()))
                .map(|(k, v)| (k.clone(), serde_json::from_str::<serde_json::Value>(v).map(|v| v.to_string()).unwrap_or_else(|_| v.clone())))
                .collect(),
            sources: sources
                .into_iter()
                .map(|(path, metadata)| {
                    let generation = crate::execution::source_generation(path);
                    let numbers = metadata
                        .bounds
                        .iter()
                        .chain(&metadata.range)
                        .chain(&metadata.percentiles)
                        .map(|v| v.to_bits())
                        .collect();
                    SourceKey {
                        path: path.to_string_lossy().into_owned(),
                        generation,
                        metadata: numbers,
                        policy: serde_json::json!([
                            metadata.metadata.get("revision"),
                            metadata.metadata.get("defaults")
                        ])
                        .to_string(),
                    }
                })
                .collect(),
        }
    }

    fn bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.route.len()
            + self.semantics.len()
            + self
                .parameters
                .iter()
                .map(|(k, v)| k.len() + v.len() + std::mem::size_of::<(String, String)>())
                .sum::<usize>()
            + self
                .sources
                .iter()
                .map(|source| {
                    source.path.len()
                        + source.policy.len()
                        + source.generation.len() * std::mem::size_of::<(u64, u128)>()
                        + source.metadata.len() * 8
                        + std::mem::size_of::<SourceKey>()
                })
                .sum::<usize>()
    }
    fn weight(&self, bytes: usize) -> usize {
        bytes + self.bytes() * 2 + std::mem::size_of::<Entry>()
    }
}

struct Entry {
    bytes: Arc<Vec<u8>>,
    inserted: Instant,
    remote: bool,
    tick: u64,
}
#[derive(Default)]
struct State {
    entries: HashMap<RenderKey, Entry>,
    order: BTreeMap<u64, RenderKey>,
    flights: HashMap<RenderKey, Arc<Flight>>,
    bytes: usize,
    tick: u64,
    hits: u64,
    misses: u64,
    coalesced: u64,
}
struct Flight {
    result: Mutex<Option<Result<Arc<Vec<u8>>>>>,
    ready: Condvar,
}
struct Leader<'a> {
    flight: Arc<Flight>,
    state: &'a Mutex<State>,
    key: RenderKey,
}
impl Drop for Leader<'_> {
    fn drop(&mut self) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let mut result = self.flight.result.lock().unwrap_or_else(|e| e.into_inner());
        if result.is_none() {
            *result = Some(Err("render worker did not finish".into()));
        }
        if state
            .flights
            .get(&self.key)
            .is_some_and(|f| Arc::ptr_eq(f, &self.flight))
        {
            state.flights.remove(&self.key);
        }
        self.flight.ready.notify_all();
    }
}

pub(super) struct RenderCache {
    state: Mutex<State>,
    capacity: usize,
    remote_ttl: Duration,
    wait_timeout: Duration,
}
impl RenderCache {
    pub fn key<'a>(
        &self,
        route: &str,
        parameters: &BTreeMap<String, String>,
        sources: impl IntoIterator<Item = (&'a Path, &'a Metadata)>,
    ) -> RenderKey {
        RenderKey::new(route, parameters, sources)
    }
    pub fn new(capacity: usize, remote_ttl: Duration, wait_timeout: Duration) -> Self {
        Self {
            state: Mutex::new(State::default()),
            capacity,
            remote_ttl,
            wait_timeout,
        }
    }

    pub fn render(
        &self,
        key: RenderKey,
        remote: bool,
        render: impl FnOnce() -> Result<Vec<u8>>,
    ) -> Result<Vec<u8>> {
        if !key.current() {
            return Err("source changed during rendering; retry".into());
        }
        let (flight, leader) =
            {
                let mut state = self.state.lock().expect("render cache");
                if state.entries.get(&key).is_some_and(|entry| {
                    entry.remote && entry.inserted.elapsed() >= self.remote_ttl
                }) {
                    Self::remove(&mut state, &key);
                }
                if let Some(entry) = state.entries.get(&key) {
                    let bytes = entry.bytes.clone();
                    let tick = entry.tick;
                    state.order.remove(&tick);
                    state.tick = state.tick.wrapping_add(1);
                    let next = state.tick;
                    state.entries.get_mut(&key).expect("cache entry").tick = next;
                    state.order.insert(next, key);
                    state.hits += 1;
                    return Ok(bytes.as_ref().clone());
                }
                if let Some(flight) = state.flights.get(&key).cloned() {
                    state.coalesced += 1;
                    (flight, false)
                } else {
                    state.misses += 1;
                    let flight = Arc::new(Flight {
                        result: Mutex::new(None),
                        ready: Condvar::new(),
                    });
                    state.flights.insert(key.clone(), flight.clone());
                    (flight, true)
                }
            };
        if !leader {
            let _timer = crate::telemetry::timer(crate::telemetry::Stage::RenderWait);
            let result = flight.result.lock().expect("render result");
            let (result, _) = flight
                .ready
                .wait_timeout_while(result, self.wait_timeout, |value| value.is_none())
                .expect("render wait");
            return result
                .as_ref()
                .ok_or_else(|| crate::execution::TIMED_OUT.to_owned())?
                .as_ref()
                .map(|bytes| bytes.as_ref().clone())
                .map_err(Clone::clone);
        }
        let lease = Leader {
            flight: flight.clone(),
            state: &self.state,
            key: key.clone(),
        };
        let result = render().and_then(|bytes| {
            if key.current() {
                Ok(Arc::new(bytes))
            } else {
                Err("source changed during rendering; retry".into())
            }
        });
        {
            let mut state = self.state.lock().expect("render cache");
            if let Ok(bytes) = &result {
                let weight = key.weight(bytes.capacity());
                if weight <= self.capacity && (!remote || !self.remote_ttl.is_zero()) {
                    while state.bytes.saturating_add(weight) > self.capacity
                        || state.entries.len() >= 4096
                    {
                        let Some((_, oldest)) = state.order.first_key_value() else {
                            break;
                        };
                        let oldest = oldest.clone();
                        Self::remove(&mut state, &oldest);
                    }
                    state.tick = state.tick.wrapping_add(1);
                    let tick = state.tick;
                    state.bytes += weight;
                    state.entries.insert(
                        key.clone(),
                        Entry {
                            bytes: bytes.clone(),
                            inserted: Instant::now(),
                            remote,
                            tick,
                        },
                    );
                    state.order.insert(tick, key.clone());
                }
            }
            *flight.result.lock().expect("render result") = Some(result.clone());
            state.flights.remove(&key);
            flight.ready.notify_all();
        }
        drop(lease);
        result.map(|bytes| bytes.as_ref().clone())
    }

    fn remove(state: &mut State, key: &RenderKey) {
        if let Some(entry) = state.entries.remove(key) {
            state.order.remove(&entry.tick);
            state.bytes -= key.weight(entry.bytes.capacity());
        }
    }

    pub fn metrics(&self) -> serde_json::Value {
        let state = self.state.lock().expect("render cache");
        serde_json::json!({"capacity_bytes":self.capacity,"bytes":state.bytes,"entries":state.entries.len(),"hits":state.hits,"misses":state.misses,"coalesced":state.coalesced,"in_flight":state.flights.len()})
    }
}

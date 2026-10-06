//! Bounded range access for worker-owned TIFF sources.
use async_tiff::error::{AsyncTiffError, AsyncTiffResult};
use async_tiff::reader::AsyncFileReader;
use async_trait::async_trait;
use bytes::Bytes;
use futures::{StreamExt, TryStreamExt, stream};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::ops::Range;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

const MAX_RANGE: u64 = 8 * 1024 * 1024;

pub(super) struct Reader {
    file: Option<Mutex<File>>,
    length: Option<u64>,
    url: String,
    client: reqwest::Client,
    etag: Mutex<Option<reqwest::header::HeaderValue>>,
    pub bytes: AtomicU64,
    pub header: AtomicBool,
    ranges: Arc<Mutex<super::ranges::Ranges>>,
    owner: Arc<()>,
}

impl std::fmt::Debug for Reader {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Reader")
            .field("remote", &self.is_remote())
            .finish_non_exhaustive()
    }
}

fn error(message: &str) -> AsyncTiffError {
    AsyncTiffError::General(message.into())
}

impl Reader {
    pub fn versioned(&self) -> bool {
        self.file.is_some()
            || self.etag.lock().ok().is_some_and(|etag| {
                etag.as_ref().is_some_and(|v| {
                    let bytes = v.as_bytes();
                    bytes.len() >= 2 && bytes.first() == Some(&b'"') && bytes.last() == Some(&b'"')
                })
            })
    }
    pub fn is_remote(&self) -> bool {
        self.file.is_none()
    }
    pub fn new(
        path: &std::path::Path,
        client: reqwest::Client,
        ranges: Arc<Mutex<super::ranges::Ranges>>,
    ) -> Option<Self> {
        let remote = crate::execution::is_remote(path);
        let file = if remote {
            None
        } else {
            Some(File::open(path).ok()?)
        };
        let length = file
            .as_ref()
            .map(|f| f.metadata().map(|m| m.len()))
            .transpose()
            .ok()?;
        Some(Self {
            file: file.map(Mutex::new),
            length,
            url: path.to_string_lossy().into_owned(),
            client,
            etag: Mutex::new(None),
            bytes: AtomicU64::new(0),
            header: AtomicBool::new(true),
            ranges,
            owner: Arc::new(()),
        })
    }
}

#[async_trait]
impl AsyncFileReader for Reader {
    async fn get_bytes(&self, range: Range<u64>) -> AsyncTiffResult<Bytes> {
        if range.is_empty() || range.end - range.start > MAX_RANGE {
            return Err(error("invalid TIFF range"));
        }
        if self.is_remote() && self.versioned() {
            if let Some(data) = self
                .ranges
                .lock()
                .map_err(|_| error("TIFF range lock poisoned"))?
                .get(&self.owner, &range)
            {
                return Ok(data);
            }
        }
        if self.header.load(Ordering::Relaxed)
            && self
                .bytes
                .load(Ordering::Relaxed)
                .saturating_add(range.end - range.start)
                > super::HEADER_BUDGET as u64
        {
            return Err(error("TIFF metadata limit"));
        }
        let data = if let Some(file) = &self.file {
            let stop = range.end.min(self.length.unwrap());
            if stop <= range.start {
                return Err(error("TIFF range beyond EOF"));
            }
            let mut file = file.lock().map_err(|_| error("TIFF file lock poisoned"))?;
            file.seek(SeekFrom::Start(range.start))?;
            let mut bytes = vec![0; (stop - range.start) as usize];
            file.read_exact(&mut bytes)?;
            Bytes::from(bytes)
        } else {
            let mut request = self
                .client
                .get(&self.url)
                .header("Range", format!("bytes={}-{}", range.start, range.end - 1));
            let etag = self
                .etag
                .lock()
                .map_err(|_| error("TIFF version lock poisoned"))?
                .clone();
            if let Some(etag) = etag {
                request = request.header("If-Match", etag);
            }
            let response = request
                .send()
                .await
                .map_err(|_| error("TIFF range transport failed"))?;
            if response.status() != reqwest::StatusCode::PARTIAL_CONTENT {
                return Err(error("TIFF server did not return partial content"));
            }
            let content_range = response
                .headers()
                .get("content-range")
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.strip_prefix("bytes "))
                .and_then(|v| v.split_once('/'))
                .and_then(|(interval, total)| interval.split_once('-').map(|(a, b)| (a, b, total)))
                .and_then(|(a, b, total)| {
                    Some((
                        a.parse::<u64>().ok()?,
                        b.parse::<u64>().ok()?,
                        total.parse::<u64>().ok()?,
                    ))
                });
            let Some((start, end, total)) = content_range else {
                return Err(error("invalid TIFF Content-Range"));
            };
            if start != range.start
                || end < start
                || end.checked_add(1) != Some(range.end.min(total))
            {
                return Err(error("incorrect TIFF Content-Range"));
            }
            if let Some(version) = response.headers().get("etag") {
                let mut etag = self
                    .etag
                    .lock()
                    .map_err(|_| error("TIFF version lock poisoned"))?;
                if etag.as_ref().is_some_and(|old| old != version) {
                    return Err(error("TIFF source version changed"));
                }
                *etag = Some(version.clone());
            } else if self
                .etag
                .lock()
                .map_err(|_| error("TIFF version lock poisoned"))?
                .is_some()
            {
                return Err(error("TIFF response lost source version"));
            }
            let expected = (end - start + 1) as usize;
            // Stream into a bounded buffer even when an origin lies about its length.
            let mut response = response;
            let mut data = Vec::with_capacity(expected);
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| error("TIFF response body failed"))?
            {
                if data.len().saturating_add(chunk.len()) > expected {
                    return Err(error("oversized TIFF response"));
                }
                data.extend_from_slice(&chunk);
            }
            if data.len() != expected {
                return Err(error("short TIFF response"));
            }
            Bytes::from(data)
        };
        self.bytes.fetch_add(data.len() as u64, Ordering::Relaxed);
        if self.is_remote() && self.versioned() {
            self.ranges
                .lock()
                .map_err(|_| error("TIFF range lock poisoned"))?
                .insert(
                    self.owner.clone(),
                    range.start..range.start + data.len() as u64,
                    data.clone(),
                );
        }
        Ok(data)
    }

    async fn get_byte_ranges(&self, ranges: Vec<Range<u64>>) -> AsyncTiffResult<Vec<Bytes>> {
        let mut sorted = ranges.clone();
        sorted.sort_by_key(|r| r.start);
        let mut merged: Vec<Range<u64>> = Vec::new();
        for range in sorted {
            if let Some(last) = merged.last_mut() {
                if range.start <= last.end.saturating_add(65536)
                    && range.end.saturating_sub(last.start) <= 2 * 1024 * 1024
                {
                    last.end = last.end.max(range.end);
                    continue;
                }
            }
            merged.push(range);
        }
        let fetched: Vec<_> = stream::iter(merged.into_iter().map(|r| async move {
            let data = self.get_bytes(r.clone()).await?;
            Ok::<_, AsyncTiffError>((r, data))
        }))
        .buffer_unordered(4)
        .try_collect()
        .await?;
        ranges
            .iter()
            .map(|r| {
                let (outer, data) = fetched
                    .iter()
                    .find(|(outer, _)| outer.start <= r.start && outer.end >= r.end)
                    .ok_or_else(|| error("missing TIFF range"))?;
                let end = (r.end - outer.start) as usize;
                if end > data.len() {
                    return Err(error("TIFF tile extends beyond EOF"));
                }
                Ok(data.slice((r.start - outer.start) as usize..end))
            })
            .collect()
    }
}

impl Drop for Reader {
    fn drop(&mut self) {
        if let Ok(mut ranges) = self.ranges.lock() {
            ranges.remove(&self.owner);
        }
    }
}

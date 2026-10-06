use async_tiff::error::{AsyncTiffError, AsyncTiffResult};
use async_tiff::reader::AsyncFileReader;
use async_trait::async_trait;
use bytes::Bytes;
use futures::{StreamExt, TryStreamExt, stream};
use std::fs::File;
use std::ops::Range;
use std::os::unix::fs::FileExt;
use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};
use tokio::sync::Semaphore;

#[derive(Debug, Default)]
pub struct Traffic {
    pub requests: AtomicU64,
    pub bytes: AtomicU64,
}

#[derive(Debug)]
pub struct Reader {
    file: Option<Arc<File>>,
    file_size: Option<u64>,
    url: String,
    client: reqwest::Client,
    limit: Arc<Semaphore>,
    pub traffic: Arc<Traffic>,
    pub merge_gap: u64,
}

fn error(message: impl ToString) -> AsyncTiffError {
    AsyncTiffError::General(message.to_string())
}

impl Reader {
    pub fn new(
        url: &str,
        client: reqwest::Client,
        limit: Arc<Semaphore>,
        traffic: Arc<Traffic>,
        merge_gap: u64,
    ) -> Result<Self, String> {
        let file = if url.starts_with("http://") || url.starts_with("https://") {
            None
        } else {
            Some(Arc::new(File::open(url).map_err(|e| e.to_string())?))
        };
        let file_size = file
            .as_ref()
            .map(|f| f.metadata().map(|m| m.len()))
            .transpose()
            .map_err(|e| e.to_string())?;
        Ok(Self {
            file,
            file_size,
            url: url.into(),
            client,
            limit,
            traffic,
            merge_gap,
        })
    }
}

#[async_trait]
impl AsyncFileReader for Reader {
    async fn get_bytes(&self, range: Range<u64>) -> AsyncTiffResult<Bytes> {
        if range.is_empty() || range.end - range.start > 64 * 1024 * 1024 {
            return Err(error("empty or oversized range"));
        }
        let _permit = self.limit.acquire().await.map_err(error)?;
        let (data, expected_len) = if let Some(file) = &self.file {
            let file = file.clone();
            let offset = range.start;
            let stop = range.end.min(self.file_size.unwrap());
            if stop <= offset {
                return Err(error("range starts beyond end of file"));
            }
            let len = (stop - offset) as usize;
            let data = tokio::task::spawn_blocking(move || {
                let mut bytes = vec![0; len];
                file.read_exact_at(&mut bytes, offset).map_err(error)?;
                Ok::<_, AsyncTiffError>(Bytes::from(bytes))
            })
            .await
            .map_err(error)??;
            (data, len)
        } else {
            let response = self
                .client
                .get(&self.url)
                .header("Range", format!("bytes={}-{}", range.start, range.end - 1))
                .send()
                .await
                .map_err(error)?;
            if response.status() != reqwest::StatusCode::PARTIAL_CONTENT {
                return Err(error(format!(
                    "range request returned {}",
                    response.status()
                )));
            }
            let header = response
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
            let Some((start, stop, total)) = header else {
                return Err(error("invalid Content-Range"));
            };
            if start != range.start
                || stop.checked_add(1) != Some(range.end.min(total))
                || stop < start
            {
                return Err(error("incorrect Content-Range"));
            }
            (
                response.bytes().await.map_err(error)?,
                (stop - start + 1) as usize,
            )
        };
        if data.len() != expected_len {
            return Err(error("short range response"));
        }
        self.traffic.requests.fetch_add(1, Ordering::Relaxed);
        self.traffic
            .bytes
            .fetch_add(data.len() as u64, Ordering::Relaxed);
        Ok(data)
    }

    async fn get_byte_ranges(&self, ranges: Vec<Range<u64>>) -> AsyncTiffResult<Vec<Bytes>> {
        let mut ordered = ranges.clone();
        ordered.sort_by_key(|r| r.start);
        let mut merged: Vec<Range<u64>> = Vec::new();
        for range in ordered {
            if let Some(last) = merged.last_mut() {
                if range.start <= last.end.saturating_add(self.merge_gap)
                    && range.end.saturating_sub(last.start) <= 2 * 1024 * 1024
                {
                    last.end = last.end.max(range.end);
                    continue;
                }
            }
            merged.push(range);
        }
        let fetched: Vec<_> = stream::iter(merged.into_iter().map(|range| async move {
            let bytes = self.get_bytes(range.clone()).await?;
            Ok::<_, AsyncTiffError>((range, bytes))
        }))
        .buffer_unordered(16)
        .try_collect()
        .await?;
        ranges
            .iter()
            .map(|range| {
                let (outer, bytes) = fetched
                    .iter()
                    .find(|(outer, _)| outer.start <= range.start && outer.end >= range.end)
                    .ok_or_else(|| error("range merge lost an input"))?;
                let end = (range.end - outer.start) as usize;
                if end > bytes.len() {
                    return Err(error("range extends beyond end of file"));
                }
                Ok(bytes.slice((range.start - outer.start) as usize..end))
            })
            .collect()
    }
}

//! Exact stored-pixel TIFF reads with GDAL fallback.
use super::{Raster, ReadRequest};
use async_tiff::metadata::MetadataFetch;
use async_tiff::metadata::{TiffMetadataReader, cache::ReadaheadMetadataCache};
use async_tiff::tags::{
    Compression, ExtraSamples, PhotometricInterpretation, PlanarConfiguration, SampleFormat,
};
use async_tiff::{Array, ImageFileDirectory, TypedArray};
use futures::FutureExt;
use std::cell::RefCell;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

mod decoder;
mod ranges;
mod reader;
use reader::Reader;

const HEADER_BUDGET: usize = 4 * 1024 * 1024;
const MAX_BLOCK_BYTES: usize = 16 * 1024 * 1024;

pub(super) struct Source {
    reader: Arc<Reader>,
    ifds: Vec<ImageFileDirectory>,
    levels: Vec<usize>,
    header_bytes: usize,
}

struct Entry {
    path: PathBuf,
    revision: Option<[u8; 32]>,
    generation: Vec<(u64, u128)>,
    opened: Instant,
    source: Option<Arc<Source>>,
}

struct State {
    runtime: tokio::runtime::Runtime,
    client: reqwest::Client,
    entries: VecDeque<Entry>,
    ranges: Arc<std::sync::Mutex<ranges::Ranges>>,
}

pub(super) struct Backend {
    enabled: bool,
    ttl: Duration,
    state: RefCell<Option<State>>,
}

#[derive(Clone, Copy)]
pub(super) enum Validity {
    Native,
    Asset,
}

impl Backend {
    pub fn new(enabled: bool, ttl: Duration) -> Self {
        Self {
            enabled,
            ttl,
            state: RefCell::new(None),
        }
    }

    pub fn source(&self, path: &Path) -> Option<Arc<Source>> {
        self.source_revision(path, None)
    }

    fn source_revision(&self, path: &Path, revision: Option<[u8; 32]>) -> Option<Arc<Source>> {
        if !self.enabled || tokio::runtime::Handle::try_current().is_ok() || !eligible(path) {
            return None;
        }
        let _timer = crate::telemetry::timer(crate::telemetry::Stage::SourceAccess);
        let remote = crate::execution::is_remote(path);
        let _remote_timer =
            remote.then(|| crate::telemetry::timer(crate::telemetry::Stage::RemoteSourceAccess));
        let generation = crate::execution::source_generation(path);
        let mut cell = self.state.borrow_mut();
        if cell.is_none() {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(4)
                .build()
                .ok()?;
            let timeout = crate::settings::var("TC_RASTER_REMOTE_TIMEOUT_SECONDS")
                .ok()
                .map(|s| s.parse::<f64>().ok())
                .unwrap_or(Some(30.))?;
            if !timeout.is_finite() || timeout <= 0. {
                return None;
            }
            let client = reqwest::Client::builder()
                .timeout(Duration::from_secs_f64(timeout.min(86400.)))
                .build()
                .ok()?;
            *cell = Some(State {
                runtime,
                client,
                entries: VecDeque::new(),
                ranges: Arc::new(std::sync::Mutex::new(ranges::Ranges::new())),
            });
        }
        let state = cell.as_mut()?;
        state.entries.retain(|entry| {
            !crate::execution::is_remote(&entry.path) || entry.opened.elapsed() < self.ttl
        });
        if let Some(index) = state.entries.iter().position(|entry| {
            entry.path == path && entry.generation == generation && entry.revision == revision
        }) {
            let entry = state.entries.remove(index)?;
            let result = entry.source.clone();
            state.entries.push_front(entry);
            crate::telemetry::source_hit(true);
            return result;
        }
        crate::telemetry::source_hit(false);
        let reader = Reader::new(path, state.client.clone(), state.ranges.clone())?;
        let opened = state
            .runtime
            .block_on(std::panic::AssertUnwindSafe(Source::open(reader)).catch_unwind());
        let source = opened.ok().and_then(Result::ok).map(Arc::new);
        state.entries.retain(|entry| entry.path != path);
        if !remote || !self.ttl.is_zero() {
            state.entries.push_front(Entry {
                path: path.to_owned(),
                revision,
                generation,
                opened: Instant::now(),
                source: source.clone(),
            });
            while state.entries.len() > 12
                || state
                    .entries
                    .iter()
                    .map(|e| e.source.as_ref().map_or(0, |s| s.header_bytes))
                    .sum::<usize>()
                    > HEADER_BUDGET
            {
                state.entries.pop_back();
            }
        }
        let headers = state
            .entries
            .iter()
            .map(|e| e.source.as_ref().map_or(0, |s| s.header_bytes))
            .sum::<usize>();
        state
            .ranges
            .lock()
            .ok()?
            .limit(HEADER_BUDGET.saturating_sub(headers));
        source
    }

    pub fn window(
        &self,
        source: &Source,
        level: usize,
        window: [usize; 4],
        bands: &[usize],
        mode: Validity,
    ) -> Option<Vec<Raster>> {
        if tokio::runtime::Handle::try_current().is_ok() {
            return None;
        }
        crate::telemetry::raster_backend_read(crate::telemetry::RasterBackend::Tiff);
        let _timer = crate::telemetry::timer(crate::telemetry::Stage::RasterRead);
        let _remote_timer = source
            .reader_remote()
            .then(|| crate::telemetry::timer(crate::telemetry::Stage::RemoteRasterRead));
        self.state
            .borrow()
            .as_ref()?
            .runtime
            .block_on(
                std::panic::AssertUnwindSafe(source.window(level, window, bands, mode))
                    .catch_unwind(),
            )
            .ok()?
            .ok()
    }

    pub fn read(
        &self,
        path: &Path,
        requests: &[ReadRequest],
        algorithm: i32,
    ) -> Option<Vec<Raster>> {
        let first = requests.first()?;
        first.bounds_mercator?;
        if requests.iter().any(|r| {
            r.bounds_mercator != first.bounds_mercator
                || r.size != first.size
                || r.preserve_values != first.preserve_values
                || r.grid != first.grid
        }) {
            return None;
        }
        if first.size.contains(&0) || first.size.iter().any(|n| *n > 2048) {
            return None;
        }
        let mode = if let Some(grid) = first.grid {
            if grid.point.is_some()
                || grid.epsg != 3857
                || grid.resampling != crate::layers::Resampling::Nearest
            {
                return None;
            }
            Validity::Asset
        } else {
            Validity::Native
        };
        let source = self.source_revision(path, first.grid.map(|grid| grid.revision))?;
        if matches!(mode, Validity::Asset) && source.nodata()?.is_some_and(|n| !n.is_finite()) {
            return None;
        }
        let (level, window) = source.exact_grid(first, algorithm)?;
        if matches!(mode, Validity::Asset) && level != 0 {
            return None;
        }
        self.window(
            &source,
            level,
            window,
            &requests.iter().map(|r| r.band).collect::<Vec<_>>(),
            mode,
        )
    }
}

fn eligible(path: &Path) -> bool {
    let text = path.to_string_lossy();
    if [
        "TC_GDAL_OPTIONS",
        "GTIFF_POINT_GEO_IGNORE",
        "GDAL_GEOREF_SOURCES",
        "GTIFF_SRS_SOURCE",
    ]
    .iter()
    .any(|key| crate::settings::var(key).is_ok())
    {
        return false;
    }
    if crate::execution::is_remote(path) {
        let url = url::Url::parse(&text).ok();
        if !url.is_some_and(|url| url.username().is_empty() && url.password().is_none())
            || std::env::vars_os().any(|(key, _)| {
                key.to_str()
                    .is_some_and(|key| key.starts_with("GDAL_HTTP_"))
            })
        {
            return false;
        }
        if text.starts_with("https://")
            && ["CURL_CA_BUNDLE", "SSL_CERT_FILE"]
                .iter()
                .any(|key| crate::settings::var(key).is_ok())
        {
            return false;
        }
        return (text.starts_with("http://") || text.starts_with("https://"))
            && [
                "TC_RASTER_AWS_ACCESS_KEY",
                "TC_RASTER_AWS_SECRET_KEY",
                "TC_RASTER_AWS_SESSION_TOKEN",
                "GDAL_HTTP_HEADERS",
                "GDAL_HTTP_HEADER_FILE",
                "GDAL_HTTP_COOKIEFILE",
                "GDAL_HTTP_COOKIEJAR",
                "GDAL_HTTP_USERPWD",
                "GDAL_HTTP_AUTH",
            ]
            .iter()
            .all(|key| crate::settings::var(key).is_err());
    }
    if !path
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("tif") || e.eq_ignore_ascii_case("tiff"))
    {
        return false;
    }
    for suffix in [".msk", ".ovr", ".aux.xml", ".aux"] {
        let mut sidecar = path.as_os_str().to_owned();
        sidecar.push(suffix);
        if Path::new(&sidecar).exists() {
            return false;
        }
    }
    !["tfw", "tifw", "tiffw", "wld", "tab", "rpb", "rpc", "aux"]
        .iter()
        .any(|extension| {
            path.with_extension(extension).exists()
                || path.with_extension(extension.to_uppercase()).exists()
        })
}

impl Source {
    async fn open(reader: Reader) -> crate::Result<Self> {
        let reader = Arc::new(reader);
        let fetch: Arc<dyn async_tiff::reader::AsyncFileReader> = reader.clone();
        let cache = ReadaheadMetadataCache::new(fetch);
        let mut parser = TiffMetadataReader::try_open(&cache)
            .await
            .map_err(|e| e.to_string())?;
        let mut ifds = Vec::new();
        let mut allocations = 0usize;
        while parser.has_next_ifd() {
            if ifds.len() >= 64 || reader.bytes.load(Ordering::Relaxed) > HEADER_BUDGET as u64 {
                return Err("TIFF metadata limit".into());
            }
            // Validate directory counts before the parser reserves tag/value vectors.
            let offset = parser.next_ifd_offset().ok_or("missing TIFF directory")?;
            let big = parser.bigtiff();
            let count_size = if big { 8 } else { 2 };
            let entry_size = if big { 20 } else { 12 };
            let integer = |bytes: &[u8]| -> u64 {
                match parser.endianness() {
                    async_tiff::reader::Endianness::LittleEndian => {
                        bytes.iter().rev().fold(0, |n, b| (n << 8) | *b as u64)
                    }
                    async_tiff::reader::Endianness::BigEndian => {
                        bytes.iter().fold(0, |n, b| (n << 8) | *b as u64)
                    }
                }
            };
            let stop = offset
                .checked_add(count_size)
                .ok_or("TIFF directory overflow")?;
            let count = cache.fetch(offset..stop).await.map_err(|e| e.to_string())?;
            if count.len() != count_size as usize {
                return Err("short TIFF directory".into());
            }
            let count = integer(&count);
            if count > 4096 {
                return Err("TIFF directory limit".into());
            }
            let end = stop
                .checked_add(count * entry_size)
                .ok_or("TIFF directory overflow")?;
            let entries = cache.fetch(stop..end).await.map_err(|e| e.to_string())?;
            if entries.len() != (count * entry_size) as usize {
                return Err("short TIFF directory".into());
            }
            for entry in entries.chunks_exact(entry_size as usize) {
                let kind = integer(&entry[2..4]) as u16;
                if async_tiff::tags::Type::from_u16(kind).is_none() {
                    return Err("unsupported TIFF tag type".into());
                }
                let count = integer(&entry[4..if big { 12 } else { 8 }]);
                let size = count
                    .checked_mul(std::mem::size_of::<async_tiff::TagValue>() as u64)
                    .and_then(|n| usize::try_from(n).ok())
                    .ok_or("TIFF tag overflow")?;
                allocations = allocations
                    .checked_add(size)
                    .ok_or("TIFF metadata overflow")?;
                if allocations > HEADER_BUDGET {
                    return Err("TIFF metadata limit".into());
                }
            }
            ifds.push(
                parser
                    .read_next_ifd(&cache)
                    .await
                    .map_err(|e| e.to_string())?
                    .ok_or("missing TIFF directory")?,
            );
        }
        let base = ifds.first().ok_or("empty TIFF")?;
        if base.new_subfile_type().unwrap_or(0) != 0
            || base.image_width() == 0
            || base.image_height() == 0
            || base.samples_per_pixel() == 0
            || base.orientation().is_some_and(|o| o != 1)
            || !safe_metadata(base)
            || !matches!(
                base.photometric_interpretation(),
                PhotometricInterpretation::BlackIsZero
                    | PhotometricInterpretation::WhiteIsZero
                    | PhotometricInterpretation::RGB
                    | PhotometricInterpretation::RGBPalette
            )
            || base
                .other_tags()
                .contains_key(&async_tiff::tags::Tag::Unknown(330))
        {
            return Err("unsupported TIFF base directory".into());
        }
        let mut levels = vec![0];
        for (index, ifd) in ifds.iter().enumerate().skip(1) {
            let flags = ifd.new_subfile_type().unwrap_or(0);
            if flags & 4 != 0 {
                continue;
            }
            if flags != 1
                || ifd.image_width() == 0
                || ifd.image_height() == 0
                || ifd.image_width() >= base.image_width()
                || ifd.image_height() >= base.image_height()
                || ifd.samples_per_pixel() != base.samples_per_pixel()
            {
                return Err("TIFF pages require GDAL".into());
            }
            levels.push(index);
        }
        let header_bytes = (reader.bytes.load(Ordering::Relaxed) as usize).max(allocations);
        reader.header.store(false, Ordering::Relaxed);
        Ok(Self {
            reader,
            ifds,
            levels,
            header_bytes,
        })
    }

    pub fn dimensions(&self) -> (usize, usize, i32) {
        let base = &self.ifds[0];
        (
            base.image_width() as usize,
            base.image_height() as usize,
            self.levels.len() as i32 - 1,
        )
    }
    pub fn bands(&self) -> usize {
        self.ifds[0].samples_per_pixel() as usize
    }
    pub fn nodata(&self) -> Option<Option<f64>> {
        self.ifds[0]
            .gdal_nodata()
            .map(|s| s.trim_end_matches('\0').trim().parse().map(Some).ok())
            .unwrap_or(Some(None))
    }
    fn reader_remote(&self) -> bool {
        self.reader.is_remote()
    }

    fn mask(&self, index: usize) -> Option<usize> {
        let data = &self.ifds[index];
        self.ifds.iter().position(|ifd| {
            ifd.new_subfile_type().unwrap_or(0) & 4 != 0
                && ifd.image_width() == data.image_width()
                && ifd.image_height() == data.image_height()
        })
    }

    fn exact_grid(&self, request: &ReadRequest, algorithm: i32) -> Option<(usize, [usize; 4])> {
        let base = &self.ifds[0];
        let geo = base.geo_key_directory()?;
        if geo.epsg_code() != Some(3857)
            || geo.vertical.is_some()
            || geo.vertical_datum.is_some()
            || geo.vertical_units.is_some()
            || geo.proj_linear_units.is_some_and(|u| u != 9001)
            || geo.proj_linear_unit_size.is_some()
            || geo.projection.is_some()
            || geo.proj_coord_trans.is_some()
            || [
                geo.proj_std_parallel1,
                geo.proj_std_parallel2,
                geo.proj_nat_origin_long,
                geo.proj_nat_origin_lat,
                geo.proj_false_easting,
                geo.proj_false_northing,
                geo.proj_false_origin_long,
                geo.proj_false_origin_lat,
                geo.proj_false_origin_easting,
                geo.proj_false_origin_northing,
                geo.proj_center_long,
                geo.proj_center_lat,
                geo.proj_center_easting,
                geo.proj_center_northing,
                geo.proj_scale_at_nat_origin,
                geo.proj_scale_at_center,
                geo.proj_azimuth_angle,
                geo.proj_straight_vert_pole_long,
            ]
            .iter()
            .any(Option::is_some)
        {
            return None;
        }
        let mut gt = if let Some(m) = base.model_transformation() {
            if m.len() != 16 || m[2] != 0. || m[6] != 0. || m[12..] != [0., 0., 0., 1.] {
                return None;
            }
            [m[3], m[0], m[1], m[7], m[4], m[5]]
        } else {
            let scale = base.model_pixel_scale()?;
            let tie = base.model_tiepoint()?;
            if scale.len() != 3 || tie.len() != 6 {
                return None;
            }
            [
                tie[3] - tie[0] * scale[0],
                scale[0],
                0.,
                tie[4] + tie[1] * scale[1],
                0.,
                -scale[1],
            ]
        };
        if geo.raster_type == Some(2) {
            gt[0] -= gt[1] * 0.5;
            gt[3] -= gt[5] * 0.5;
        }
        if gt.iter().any(|v| !v.is_finite())
            || gt[1] <= 0.
            || gt[5] >= 0.
            || gt[2] != 0.
            || gt[4] != 0.
        {
            return None;
        }
        let [west, south, east, north] = request.bounds_mercator?;
        let base_window = [
            (west - gt[0]) / gt[1],
            (north - gt[3]) / gt[5],
            (east - west) / gt[1],
            (south - north) / gt[5],
        ];
        let [height, width] = request.size;
        let scale = (base_window[2] / width as f64).min(base_window[3] / height as f64);
        let mut candidates = self
            .levels
            .iter()
            .enumerate()
            .map(|(level, index)| {
                let ifd = &self.ifds[*index];
                (
                    level,
                    base.image_width() as f64 / ifd.image_width() as f64,
                    base.image_height() as f64 / ifd.image_height() as f64,
                )
            })
            .collect::<Vec<_>>();
        candidates.sort_by(|a, b| a.1.total_cmp(&b.1));
        if candidates.windows(2).any(|pair| pair[0].1 == pair[1].1) {
            return None;
        }
        // A larger overview within GDAL's nearest oversampling threshold could
        // change which stored pixels RasterIO selects.
        if candidates
            .iter()
            .any(|(_, x, _)| *x > scale && *x <= scale * 1.2)
        {
            return None;
        }
        for (level, sx, sy) in candidates.into_iter().rev() {
            let floats = [
                base_window[0] / sx,
                base_window[1] / sy,
                base_window[2] / sx,
                base_window[3] / sy,
            ];
            let nearest = request.preserve_values || request.grid.is_some() || algorithm == 0;
            if floats.iter().any(|v| {
                !v.is_finite()
                    || *v < 0.
                    || (*v - v.round()).abs() > if nearest { 1e-8 } else { 0. }
            }) {
                continue;
            }
            let w = floats.map(|v| v.round() as usize);
            let ifd = &self.ifds[self.levels[level]];
            if w[2] == width
                && w[3] == height
                && w[0].checked_add(width)? <= ifd.image_width() as usize
                && w[1].checked_add(height)? <= ifd.image_height() as usize
            {
                return Some((level, w));
            }
        }
        None
    }

    async fn pixels(
        &self,
        index: usize,
        window: [usize; 4],
        bands: &[usize],
    ) -> crate::Result<Vec<Vec<f64>>> {
        let ifd = &self.ifds[index];
        if !self.reader.versioned() {
            return Err("unversioned remote TIFF requires GDAL".into());
        }
        check_pixels(ifd)?;
        let [x, y, width, height] = window;
        let count = width.checked_mul(height).ok_or("TIFF window overflow")?;
        if count == 0
            || count > 2048 * 2048
            || x.checked_add(width).ok_or("TIFF window overflow")? > ifd.image_width() as usize
            || y.checked_add(height).ok_or("TIFF window overflow")? > ifd.image_height() as usize
            || bands
                .iter()
                .any(|b| *b == 0 || *b > ifd.samples_per_pixel() as usize)
        {
            return Err("invalid TIFF pixel window".into());
        }
        let tw = ifd.tile_width().ok_or("stripped TIFF")? as usize;
        let th = ifd.tile_height().ok_or("stripped TIFF")? as usize;
        let samples = if ifd.planar_configuration() == PlanarConfiguration::Planar {
            1
        } else {
            ifd.samples_per_pixel() as usize
        };
        let limit = (tw * th * samples * ifd.bits_per_sample()[0] as usize).div_ceil(8);
        let registry = decoder::registry(ifd.compression(), limit);
        let keys = (y / th..=(y + height - 1) / th)
            .flat_map(|ty| (x / tw..=(x + width - 1) / tw).map(move |tx| (tx, ty)))
            .collect::<Vec<_>>();
        let mut output = bands.iter().map(|_| vec![0.; count]).collect::<Vec<_>>();
        for batch in keys.chunks(4) {
            let tiles = ifd
                .fetch_tiles(batch, self.reader.as_ref())
                .await
                .map_err(|e| e.to_string())?;
            for tile in tiles {
                let (tx, ty) = (tile.x(), tile.y());
                let array = tile.decode(&registry).map_err(|e| e.to_string())?;
                let expected = if ifd.planar_configuration() == PlanarConfiguration::Planar {
                    [ifd.samples_per_pixel() as usize, th, tw]
                } else {
                    [th, tw, ifd.samples_per_pixel() as usize]
                };
                if array.shape() != expected {
                    return Err("unexpected TIFF tile shape".into());
                }
                for py in y.max(ty * th)..(y + height).min((ty + 1) * th) {
                    for px in x.max(tx * tw)..(x + width).min((tx + 1) * tw) {
                        let dst = (py - y) * width + px - x;
                        for (channel, band) in bands.iter().enumerate() {
                            let row = py - ty * th;
                            let col = px - tx * tw;
                            let src = if ifd.planar_configuration() == PlanarConfiguration::Planar {
                                (band - 1) * tw * th + row * tw + col
                            } else {
                                (row * tw + col) * ifd.samples_per_pixel() as usize + band - 1
                            };
                            output[channel][dst] = value(&array, src);
                        }
                    }
                }
            }
        }
        Ok(output)
    }

    async fn window(
        &self,
        level: usize,
        window: [usize; 4],
        bands: &[usize],
        mode: Validity,
    ) -> crate::Result<Vec<Raster>> {
        let index = *self.levels.get(level).ok_or("missing TIFF level")?;
        let ifd = &self.ifds[index];
        check_pixels(ifd)?;
        if index != 0 && self.mask(0).is_some() && self.mask(index).is_none() {
            return Err("missing TIFF overview mask".into());
        }
        let data_type = dtype(ifd).ok_or("unsupported TIFF dtype")?;
        let nodata = self.nodata().ok_or("invalid TIFF nodata")?;
        let alpha = ifd.extra_samples().and_then(|extra| {
            extra
                .iter()
                .position(|e| {
                    matches!(
                        e,
                        ExtraSamples::AssociatedAlpha | ExtraSamples::UnassociatedAlpha
                    )
                })
                .map(|i| ifd.samples_per_pixel() as usize - extra.len() + i + 1)
        });
        let mut requested = bands.to_vec();
        if let Some(alpha) = alpha {
            if !requested.contains(&alpha) {
                requested.push(alpha);
            }
        }
        let data = self.pixels(index, window, &requested).await?;
        let alpha_data = alpha
            .and_then(|a| requested.iter().position(|b| *b == a))
            .map(|i| {
                data[i]
                    .iter()
                    .map(|a| !a.is_finite() || *a <= 0.)
                    .collect::<Vec<_>>()
            });
        let internal_mask = if let Some(index) = self
            .mask(index)
            .filter(|_| !matches!(mode, Validity::Native) || alpha.is_none())
        {
            if self.ifds[index].samples_per_pixel() != 1 {
                return Err("multiband TIFF mask requires GDAL".into());
            }
            Some(
                self.pixels(index, window, &[1])
                    .await?
                    .remove(0)
                    .into_iter()
                    .map(|m| m <= 0.)
                    .collect::<Vec<_>>(),
            )
        } else {
            None
        };
        let mut rasters = Vec::new();
        for (band, mut values) in bands.iter().zip(data) {
            let mask: Vec<bool> = values
                .iter()
                .enumerate()
                .map(|(i, v)| {
                    let missing = nodata.is_some_and(|n| *v == n || n.is_nan() && v.is_nan());
                    let alpha_invalid = alpha_data.as_ref().is_some_and(|a| a[i]);
                    let masked = internal_mask.as_ref().is_some_and(|m| m[i]);
                    !v.is_finite()
                        || match mode {
                            Validity::Native => {
                                missing
                                    || if alpha.is_some() {
                                        alpha_invalid
                                    } else {
                                        masked
                                    }
                            }
                            Validity::Asset => {
                                missing || masked || (alpha != Some(*band) && alpha_invalid)
                            }
                        }
                })
                .collect();
            if matches!(mode, Validity::Asset) {
                for (value, invalid) in values.iter_mut().zip(&mask) {
                    if *invalid {
                        *value = nodata.unwrap_or(0.);
                    }
                }
            }
            rasters.push(Raster {
                width: window[2],
                height: window[3],
                data_type,
                values,
                mask,
            });
        }
        Ok(rasters)
    }
}

fn check_pixels(ifd: &ImageFileDirectory) -> crate::Result<()> {
    // async-tiff 0.4 applies predictors with chunky sample spacing to planar data.
    if ifd.planar_configuration() == PlanarConfiguration::Planar
        && ifd
            .predictor()
            .is_some_and(|p| p != async_tiff::tags::Predictor::None)
    {
        return Err("predicted planar TIFF requires GDAL".into());
    }
    if !matches!(
        ifd.compression(),
        Compression::None
            | Compression::LZW
            | Compression::Deflate
            | Compression::OldDeflate
            | Compression::ZSTD
    ) || !matches!(
        ifd.photometric_interpretation(),
        PhotometricInterpretation::BlackIsZero
            | PhotometricInterpretation::RGB
            | PhotometricInterpretation::RGBPalette
            | PhotometricInterpretation::TransparencyMask
    ) || ifd.orientation().is_some_and(|o| o != 1)
        || !safe_metadata(ifd)
        || !ifd.other_tags().is_empty()
        || ifd.samples_per_pixel() == 0
        || ifd.bits_per_sample().is_empty()
        || ifd.samples_per_pixel() > 8
        || ifd
            .extra_samples()
            .is_some_and(|extra| extra.len() > ifd.samples_per_pixel() as usize)
    {
        return Err("TIFF pixels require GDAL".into());
    }
    let width = ifd.tile_width().ok_or("stripped TIFF")? as usize;
    let height = ifd.tile_height().ok_or("stripped TIFF")? as usize;
    if width == 0 || height == 0 || width % 16 != 0 || height % 16 != 0 {
        return Err("invalid TIFF block dimensions".into());
    }
    let planes = if ifd.planar_configuration() == PlanarConfiguration::Planar {
        ifd.samples_per_pixel() as usize
    } else {
        1
    };
    let count = (ifd.image_width() as usize)
        .div_ceil(width)
        .checked_mul((ifd.image_height() as usize).div_ceil(height))
        .and_then(|n| n.checked_mul(planes))
        .ok_or("TIFF block count overflow")?;
    if ifd.tile_offsets().is_none_or(|v| v.len() != count)
        || ifd.tile_byte_counts().is_none_or(|v| v.len() != count)
    {
        return Err("invalid TIFF block tables".into());
    }
    let size = width;
    let size = size
        .checked_mul(height)
        .and_then(|n| n.checked_mul(ifd.samples_per_pixel() as usize))
        .and_then(|n| {
            n.checked_mul(ifd.bits_per_sample().first().copied().unwrap_or(64).max(8) as usize / 8)
        })
        .ok_or("TIFF block overflow")?;
    if size > MAX_BLOCK_BYTES
        || dtype(ifd).is_none()
            && ifd.photometric_interpretation() != PhotometricInterpretation::TransparencyMask
    {
        return Err("unsupported TIFF block".into());
    }
    Ok(())
}

fn safe_metadata(ifd: &ImageFileDirectory) -> bool {
    let Some(xml) = ifd.gdal_metadata() else {
        return true;
    };
    static RESAMPLING: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RESAMPLING.get_or_init(|| regex::Regex::new(r#"^\s*<GDALMetadata>\s*(?:<Item name="RESAMPLING" sample="[0-7]">(?:NEAREST|AVERAGE|BILINEAR|CUBIC|LANCZOS|MODE|RMS)</Item>\s*)+</GDALMetadata>\s*$"#).expect("constant TIFF metadata expression")).is_match(xml)
}

fn dtype(ifd: &ImageFileDirectory) -> Option<i32> {
    let bits = ifd.bits_per_sample();
    let formats = ifd.sample_format();
    if bits.is_empty()
        || formats.is_empty()
        || bits.iter().any(|b| *b != bits[0])
        || formats.iter().any(|f| *f != formats[0])
    {
        return None;
    }
    Some(match (formats[0], bits[0]) {
        (SampleFormat::Uint, 8) => 1,
        (SampleFormat::Uint, 16) => 2,
        (SampleFormat::Int, 16) => 3,
        (SampleFormat::Uint, 32) => 4,
        (SampleFormat::Int, 32) => 5,
        (SampleFormat::Float, 32) => 6,
        (SampleFormat::Float, 64) => 7,
        (SampleFormat::Uint, 64) => 12,
        (SampleFormat::Int, 64) => 13,
        (SampleFormat::Int, 8) => 14,
        _ => return None,
    })
}

fn value(array: &Array, index: usize) -> f64 {
    match array.data() {
        TypedArray::Bool(v) => f64::from(v[index]),
        TypedArray::UInt8(v) => v[index] as f64,
        TypedArray::UInt16(v) => v[index] as f64,
        TypedArray::UInt32(v) => v[index] as f64,
        TypedArray::UInt64(v) => v[index] as f64,
        TypedArray::Int8(v) => v[index] as f64,
        TypedArray::Int16(v) => v[index] as f64,
        TypedArray::Int32(v) => v[index] as f64,
        TypedArray::Int64(v) => v[index] as f64,
        TypedArray::Float32(v) => v[index] as f64,
        TypedArray::Float64(v) => v[index],
    }
}

#[cfg(test)]
#[path = "../../tests/raster/tiff_backend.rs"]
mod tests;

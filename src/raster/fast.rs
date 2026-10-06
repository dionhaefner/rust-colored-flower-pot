//! Optional affine Web Mercator path for fully valid uint16 interiors.

use super::*;
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::SystemTime;

pub(super) const GDT_UINT16: c_int = 2;
pub(super) const GF_WRITE: c_int = 1;

#[derive(Clone, Hash, PartialEq, Eq)]
struct BlockKey {
    path: PathBuf,
    stamp: (u64, SystemTime),
    block_x: i32,
    block_y: i32,
}

struct DecodedBlock {
    width: usize,
    height: usize,
    pixels: Vec<u16>,
}

pub(crate) struct SharedBlocks {
    capacity: usize,
    bytes: usize,
    entries: HashMap<BlockKey, Arc<DecodedBlock>>,
    order: VecDeque<BlockKey>,
    pending: VecDeque<BlockKey>,
    pending_set: HashSet<BlockKey>,
    prefetch: bool,
}

impl SharedBlocks {
    fn new(capacity: usize) -> Self {
        Self {
            capacity,
            bytes: 0,
            entries: HashMap::new(),
            order: VecDeque::new(),
            pending: VecDeque::new(),
            pending_set: HashSet::new(),
            prefetch: std::env::var_os("TC_PREFETCH_BLOCK_RING").is_some(),
        }
    }

    fn get(&mut self, key: &BlockKey) -> Option<Arc<DecodedBlock>> {
        let block = self.entries.get(key)?.clone();
        self.order.retain(|candidate| candidate != key);
        self.order.push_back(key.clone());
        Some(block)
    }

    fn insert(&mut self, key: BlockKey, block: Arc<DecodedBlock>) -> Arc<DecodedBlock> {
        if let Some(existing) = self.get(&key) {
            return existing;
        }
        let bytes = block.pixels.len() * std::mem::size_of::<u16>();
        if bytes > self.capacity {
            return block;
        }
        while self.bytes.saturating_add(bytes) > self.capacity {
            let Some(oldest) = self.order.pop_front() else {
                break;
            };
            if let Some(removed) = self.entries.remove(&oldest) {
                self.bytes -= removed.pixels.len() * std::mem::size_of::<u16>();
            }
        }
        self.bytes += bytes;
        self.order.push_back(key.clone());
        self.entries.insert(key, block.clone());
        block
    }

    fn schedule_ring(
        &mut self,
        path: &Path,
        stamp: (u64, SystemTime),
        touched: [i32; 4],
        maximum: [i32; 2],
    ) {
        if !self.prefetch {
            return;
        }
        let [x0, y0, x1, y1] = touched;
        for by in (y0 - 1).max(0)..=(y1 + 1).min(maximum[1]) {
            for bx in (x0 - 1).max(0)..=(x1 + 1).min(maximum[0]) {
                if bx >= x0 && bx <= x1 && by >= y0 && by <= y1 {
                    continue;
                }
                let key = BlockKey {
                    path: path.to_path_buf(),
                    stamp,
                    block_x: bx,
                    block_y: by,
                };
                if self.entries.contains_key(&key) || self.pending_set.contains(&key) {
                    continue;
                }
                if self.pending.len() >= 128 {
                    return;
                }
                self.pending_set.insert(key.clone());
                self.pending.push_back(key);
            }
        }
    }

    fn next_prefetch(&mut self) -> Option<BlockKey> {
        while let Some(key) = self.pending.pop_front() {
            self.pending_set.remove(&key);
            if !self.entries.contains_key(&key) {
                return Some(key);
            }
        }
        None
    }
}

impl Gdal {
    pub(crate) fn new_shared_blocks() -> Arc<Mutex<SharedBlocks>> {
        let capacity = std::env::var("TC_SOURCE_BLOCK_CACHE_BYTES")
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(0);
        Arc::new(Mutex::new(SharedBlocks::new(capacity)))
    }

    pub(super) fn cached_native_window(
        &self,
        path: &Path,
        stamp: (u64, SystemTime),
        source: DatasetHandle,
        band: BandHandle,
        window: [i32; 4],
    ) -> Result<Option<Vec<u16>>> {
        let Some(cache) = &self.source_blocks else {
            return Ok(None);
        };
        if cache.lock().expect("source blocks").capacity == 0 {
            return Ok(None);
        }
        unsafe {
            let get_size: unsafe extern "C" fn(BandHandle, *mut c_int, *mut c_int) =
                self.symbol(b"GDALGetBlockSize\0")?;
            let (mut bw, mut bh) = (0, 0);
            get_size(band, &mut bw, &mut bh);
            if bw <= 0 || bh <= 0 || bw > 2048 || bh > 2048 {
                return Ok(None);
            }
            let [rx0, ry0, rx1, ry1] = window;
            let sw = (rx1 - rx0) as usize;
            let sh = (ry1 - ry0) as usize;
            let mut output = vec![0u16; sw * sh];
            for block_y in ry0 / bh..=(ry1 - 1) / bh {
                for block_x in rx0 / bw..=(rx1 - 1) / bw {
                    let key = BlockKey {
                        path: path.to_path_buf(),
                        stamp,
                        block_x,
                        block_y,
                    };
                    let cached = cache.lock().expect("source blocks").get(&key);
                    let block = if let Some(cached) = cached {
                        cached
                    } else {
                        let x = block_x * bw;
                        let y = block_y * bh;
                        let width = bw.min((self.raster_x_size)(source) - x) as usize;
                        let height = bh.min((self.raster_y_size)(source) - y) as usize;
                        let mut pixels = vec![0u16; width * height];
                        read_window(
                            self.raster_io_ex,
                            band,
                            &mut pixels,
                            ReadSpec {
                                width,
                                height,
                                data_type: GDT_UINT16,
                                algorithm: GRIORA_NEAREST,
                                window: [x as f64, y as f64, width as f64, height as f64],
                            },
                        )?;
                        let decoded = Arc::new(DecodedBlock {
                            width,
                            height,
                            pixels,
                        });
                        cache.lock().expect("source blocks").insert(key, decoded)
                    };
                    let x0 = rx0.max(block_x * bw);
                    let y0 = ry0.max(block_y * bh);
                    let x1 = rx1.min(block_x * bw + block.width as i32);
                    let y1 = ry1.min(block_y * bh + block.height as i32);
                    for y in y0..y1 {
                        let from = (y - block_y * bh) as usize * block.width
                            + (x0 - block_x * bw) as usize;
                        let to = (y - ry0) as usize * sw + (x0 - rx0) as usize;
                        let length = (x1 - x0) as usize;
                        output[to..to + length].copy_from_slice(&block.pixels[from..from + length]);
                    }
                }
            }
            let source_width = (self.raster_x_size)(source);
            let source_height = (self.raster_y_size)(source);
            cache.lock().expect("source blocks").schedule_ring(
                path,
                stamp,
                [rx0 / bw, ry0 / bh, (rx1 - 1) / bw, (ry1 - 1) / bh],
                [(source_width - 1) / bw, (source_height - 1) / bh],
            );
            Ok(Some(output))
        }
    }

    pub(crate) fn prefetch_idle_block(&self) -> Option<bool> {
        let cache = self.source_blocks.as_ref()?;
        let key = cache.lock().expect("source blocks").next_prefetch()?;
        let _timer = crate::telemetry::timer(crate::telemetry::Stage::Prefetch);
        let started = Instant::now();
        let result: Result<()> = (|| {
            let metadata = std::fs::metadata(&key.path).map_err(|e| e.to_string())?;
            if (
                metadata.len(),
                metadata.modified().map_err(|e| e.to_string())?,
            ) != key.stamp
            {
                return Ok(());
            }
            let cached_source = self
                .sources
                .borrow()
                .iter()
                .find(|entry| entry.path == key.path && entry.stamp == key.stamp)
                .map(|entry| entry.dataset.handle);
            let opened = if cached_source.is_none() {
                Some(self.open_dataset(&key.path)?)
            } else {
                None
            };
            let source =
                cached_source.unwrap_or_else(|| opened.as_ref().expect("source opened").handle);
            unsafe {
                let band = (self.raster_band)(source, 1);
                let get_size: unsafe extern "C" fn(BandHandle, *mut c_int, *mut c_int) =
                    self.symbol(b"GDALGetBlockSize\0")?;
                let (mut bw, mut bh) = (0, 0);
                get_size(band, &mut bw, &mut bh);
                if bw <= 0 || bh <= 0 {
                    return Ok(());
                }
                let x = key.block_x * bw;
                let y = key.block_y * bh;
                let width = bw.min((self.raster_x_size)(source) - x) as usize;
                let height = bh.min((self.raster_y_size)(source) - y) as usize;
                let mut pixels = vec![0u16; width * height];
                read_window(
                    self.raster_io_ex,
                    band,
                    &mut pixels,
                    ReadSpec {
                        width,
                        height,
                        data_type: GDT_UINT16,
                        algorithm: GRIORA_NEAREST,
                        window: [x as f64, y as f64, width as f64, height as f64],
                    },
                )?;
                let block = Arc::new(DecodedBlock {
                    width,
                    height,
                    pixels,
                });
                cache.lock().expect("source blocks").insert(key, block);
            }
            Ok(())
        })();
        if self.profile_reads {
            eprintln!(
                "prefetch_profile elapsed_us={} ok={}",
                started.elapsed().as_micros(),
                result.is_ok()
            );
        }
        Some(result.is_ok())
    }

    pub(super) fn read_mercator_fast(
        &self,
        path: &Path,
        stamp: Option<(u64, SystemTime)>,
        source: DatasetHandle,
        request: ReadRequest,
    ) -> Result<Option<Raster>> {
        if std::env::var_os("TC_RUST_MERCATOR_FAST").is_none()
            || !self.target_crs.eq_ignore_ascii_case("EPSG:3857")
            || self.reprojection.0 != 1
            || self.resampling != 5
            || request.preserve_values
        {
            return Ok(None);
        }
        let Some(bounds) = request.bounds_mercator else {
            return Ok(None);
        };
        unsafe {
            let get_spatial_ref: unsafe extern "C" fn(DatasetHandle) -> *mut c_void =
                self.symbol(b"GDALGetSpatialRef\0")?;
            let new_spatial_ref: unsafe extern "C" fn(*const c_char) -> *mut c_void =
                self.symbol(b"OSRNewSpatialReference\0")?;
            let set_from_user_input: unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int =
                self.symbol(b"OSRSetFromUserInput\0")?;
            let is_same: unsafe extern "C" fn(*mut c_void, *mut c_void) -> c_int =
                self.symbol(b"OSRIsSame\0")?;
            let destroy: unsafe extern "C" fn(*mut c_void) =
                self.symbol(b"OSRDestroySpatialReference\0")?;
            let source_ref = get_spatial_ref(source);
            if source_ref.is_null() {
                return Ok(None);
            }
            let target_ref = new_spatial_ref(ptr::null());
            if target_ref.is_null() {
                return Err("GDAL could not allocate target CRS".into());
            }
            let target_valid = set_from_user_input(target_ref, c"EPSG:3857".as_ptr()) == 0;
            let matches = target_valid && is_same(source_ref, target_ref) == 1;
            destroy(target_ref);
            if !matches {
                return Ok(None);
            }
            let band = (self.raster_band)(source, 1);
            if band.is_null() || (self.band_data_type)(band) != GDT_UINT16 {
                return Ok(None);
            }
            let flags: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetMaskFlags\0")?;
            if ![1, 8].contains(&flags(band)) {
                return Ok(None);
            }
            let mut has_nodata = 0;
            let nodata = (self.nodata)(band, &mut has_nodata);
            if has_nodata != 0 && nodata != 0.0 {
                return Ok(None);
            }
            let mut t = [0.0; 6];
            if (self.geo_transform)(source, t.as_mut_ptr()) != 0
                || t[1] <= 0.0
                || t[5] >= 0.0
                || t[2] != 0.0
                || t[4] != 0.0
            {
                return Ok(None);
            }
            let width = request.size[1];
            let height = request.size[0];
            let tile_x_res = (bounds[2] - bounds[0]) / width as f64;
            let tile_y_res = (bounds[3] - bounds[1]) / height as f64;
            if tile_x_res < t[1] || tile_y_res < -t[5] {
                return Ok(None);
            }
            let iw = ((bounds[2] - bounds[0]) / t[1]).round().max(1.0) as usize;
            let ih = ((bounds[3] - bounds[1]) / -t[5]).round().max(1.0) as usize;
            if iw > 4096 || ih > 4096 {
                return Ok(None);
            }
            let dx = (bounds[2] - bounds[0]) / iw as f64;
            let dy = (bounds[3] - bounds[1]) / ih as f64;
            let padded = [
                bounds[0] - 2.0 * dx,
                bounds[1] - 2.0 * dy,
                bounds[2] + 2.0 * dx,
                bounds[3] + 2.0 * dy,
            ];
            let rx0 = ((padded[0] - t[0]) / t[1]).floor() as i32 - 3;
            let rx1 = ((padded[2] - t[0]) / t[1]).ceil() as i32 + 3;
            let ry0 = ((padded[3] - t[3]) / t[5]).floor() as i32 - 3;
            let ry1 = ((padded[1] - t[3]) / t[5]).ceil() as i32 + 3;
            if rx0 < 0
                || ry0 < 0
                || rx1 > (self.raster_x_size)(source)
                || ry1 > (self.raster_y_size)(source)
            {
                return Ok(None);
            }
            let sw = (rx1 - rx0) as usize;
            let sh = (ry1 - ry0) as usize;
            let started = Instant::now();
            let cached = if let Some(stamp) = stamp {
                self.cached_native_window(path, stamp, source, band, [rx0, ry0, rx1, ry1])?
            } else {
                None
            };
            let native = if let Some(cached) = cached {
                cached
            } else {
                let mut native = vec![0u16; sw * sh];
                read_window(
                    self.raster_io_ex,
                    band,
                    &mut native,
                    ReadSpec {
                        width: sw,
                        height: sh,
                        data_type: GDT_UINT16,
                        algorithm: GRIORA_NEAREST,
                        window: [rx0 as f64, ry0 as f64, sw as f64, sh as f64],
                    },
                )?;
                native
            };
            if has_nodata != 0 && native.contains(&0) {
                return Ok(None);
            }
            let native_at = Instant::now();
            let vw = iw + 4;
            let vh = ih + 4;
            let mut intermediate = vec![0u16; vw * vh];
            let x_samples = (0..vw)
                .map(|col| {
                    let merc_x = padded[0] + (col as f64 + 0.5) * dx;
                    let sx = (merc_x - t[0]) / t[1] - 0.5 - rx0 as f64;
                    let x0 = sx.floor() as usize;
                    (x0, sx - x0 as f64)
                })
                .collect::<Vec<_>>();
            if x_samples.iter().any(|(x0, _)| x0 + 1 >= sw) {
                return Ok(None);
            }
            for row in 0..vh {
                let merc_y = padded[3] - (row as f64 + 0.5) * dy;
                let sy = (merc_y - t[3]) / t[5] - 0.5 - ry0 as f64;
                let y0 = sy.floor() as usize;
                let fy = sy - y0 as f64;
                if y0 + 1 >= sh {
                    return Ok(None);
                }
                let top_row = &native[y0 * sw..(y0 + 1) * sw];
                let bottom_row = &native[(y0 + 1) * sw..(y0 + 2) * sw];
                let output_row = &mut intermediate[row * vw..(row + 1) * vw];
                for (col, &(x0, fx)) in x_samples.iter().enumerate() {
                    let p00 = top_row[x0] as f64;
                    let p01 = top_row[x0 + 1] as f64;
                    let p10 = bottom_row[x0] as f64;
                    let p11 = bottom_row[x0 + 1] as f64;
                    let top = p00 + fx * (p01 - p00);
                    let bottom = p10 + fx * (p11 - p10);
                    output_row[col] = (top + fy * (bottom - top)).round() as u16;
                }
            }
            let interpolated_at = Instant::now();
            let driver_by_name: unsafe extern "C" fn(*const c_char) -> *mut c_void =
                self.symbol(b"GDALGetDriverByName\0")?;
            let create: unsafe extern "C" fn(
                *mut c_void,
                *const c_char,
                c_int,
                c_int,
                c_int,
                c_int,
                *mut *mut c_char,
            ) -> DatasetHandle = self.symbol(b"GDALCreate\0")?;
            let driver = driver_by_name(c"MEM".as_ptr());
            let mem = create(
                driver,
                c"".as_ptr(),
                vw as c_int,
                vh as c_int,
                1,
                GDT_UINT16,
                ptr::null_mut(),
            );
            if mem.is_null() {
                return Err("GDAL could not create memory raster".into());
            }
            let mem = DatasetGuard {
                handle: mem,
                close: self.close,
            };
            let mem_band = (self.raster_band)(mem.handle, 1);
            let write_error = (self.raster_io_ex)(
                mem_band,
                GF_WRITE,
                0,
                0,
                vw as c_int,
                vh as c_int,
                intermediate.as_mut_ptr().cast(),
                vw as c_int,
                vh as c_int,
                GDT_UINT16,
                0,
                0,
                ptr::null_mut(),
            );
            if write_error != 0 {
                return Err("GDAL could not fill memory raster".into());
            }
            let mut values = vec![0.0f64; width * height];
            read_window(
                self.raster_io_ex,
                mem_band,
                &mut values,
                ReadSpec {
                    width,
                    height,
                    data_type: GDT_FLOAT64,
                    algorithm: self.resampling,
                    window: [2.0, 2.0, iw as f64, ih as f64],
                },
            )?;
            if self.profile_reads {
                eprintln!(
                    "fast_profile native_us={} interpolate_us={} average_us={}",
                    (native_at - started).as_micros(),
                    (interpolated_at - native_at).as_micros(),
                    interpolated_at.elapsed().as_micros(),
                );
            }
            Ok(Some(Raster {
                data_type: GDT_UINT16,
                width,
                height,
                values,
                mask: vec![false; width * height],
            }))
        }
    }
}

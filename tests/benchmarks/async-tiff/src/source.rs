use crate::{Job, projection, reader::Reader};
use async_tiff::metadata::{TiffMetadataReader, cache::ReadaheadMetadataCache};
use async_tiff::tags::{
    ExtraSamples, PhotometricInterpretation, PlanarConfiguration, SampleFormat,
};
use async_tiff::{Array, DataType, ImageFileDirectory, TypedArray};
use futures::{StreamExt, TryStreamExt, stream};
use rcfp_core::raster::Raster;
use serde_json::{Value, json};
use std::collections::{HashMap, VecDeque};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicU64, Ordering},
};
use tokio::sync::Semaphore;

pub struct Source {
    pub reader: Arc<Reader>,
    pub ifds: Vec<ImageFileDirectory>,
    pub data: Vec<usize>,
}

pub struct Cache {
    budget: usize,
    bytes: usize,
    entries: HashMap<String, (Arc<Array>, usize)>,
    order: VecDeque<String>,
    pub hits: u64,
    pub misses: u64,
    pub peak: usize,
}

impl Cache {
    pub fn new(budget: usize) -> Self {
        Self {
            budget,
            bytes: 0,
            entries: HashMap::new(),
            order: VecDeque::new(),
            hits: 0,
            misses: 0,
            peak: 0,
        }
    }
    fn get(&mut self, key: &str) -> Option<Arc<Array>> {
        if let Some((array, _)) = self.entries.get(key) {
            self.hits += 1;
            self.order.retain(|k| k != key);
            self.order.push_back(key.into());
            Some(array.clone())
        } else {
            self.misses += 1;
            None
        }
    }
    fn insert(&mut self, key: String, array: Arc<Array>) {
        let bytes =
            array.data().len() * array.data_type().map_or(1, |v| v.size()) + key.len() + 256;
        if bytes > self.budget || self.entries.contains_key(&key) {
            return;
        }
        while self.bytes + bytes > self.budget || self.entries.len() >= 4096 {
            let Some(old) = self.order.pop_front() else {
                break;
            };
            if let Some((_, size)) = self.entries.remove(&old) {
                self.bytes -= size;
            }
        }
        self.bytes += bytes;
        self.peak = self.peak.max(self.bytes);
        self.order.push_back(key.clone());
        self.entries.insert(key, (array, bytes));
    }
    pub fn report(&self) -> Value {
        json!({"retained_bytes": self.bytes, "peak_retained_bytes": self.peak, "hits": self.hits, "misses": self.misses})
    }
}

fn affine(ifd: &ImageFileDirectory) -> Result<[f64; 6], String> {
    let mut out = if let Some(m) = ifd.model_transformation() {
        if m.len() != 16 {
            return Err("invalid model transformation".into());
        }
        [m[3], m[0], m[1], m[7], m[4], m[5]]
    } else {
        let scale = ifd
            .model_pixel_scale()
            .ok_or("missing affine pixel scale")?;
        let tie = ifd.model_tiepoint().ok_or("missing affine tiepoint")?;
        if scale.len() < 2 || tie.len() != 6 {
            return Err("non-affine georeferencing is unsupported".into());
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
    if ifd
        .geo_key_directory()
        .is_some_and(|g| g.raster_type == Some(2))
    {
        out[0] -= (out[1] + out[2]) * 0.5;
        out[3] -= (out[4] + out[5]) * 0.5;
    }
    if !out.iter().all(|v| v.is_finite()) || (out[1] * out[5] - out[2] * out[4]).abs() < 1e-30 {
        return Err("invalid affine".into());
    }
    Ok(out)
}

fn pixel(gt: [f64; 6], x: f64, y: f64) -> [f64; 2] {
    let det = gt[1] * gt[5] - gt[2] * gt[4];
    [
        (gt[5] * (x - gt[0]) - gt[2] * (y - gt[3])) / det,
        (-gt[4] * (x - gt[0]) + gt[1] * (y - gt[3])) / det,
    ]
}

pub fn value(array: &Array, index: usize) -> f64 {
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

fn gdal_type(data: DataType) -> Result<i32, String> {
    Ok(match data {
        DataType::UInt8 => 1,
        DataType::UInt16 => 2,
        DataType::Int16 => 3,
        DataType::UInt32 => 4,
        DataType::Int32 => 5,
        DataType::Float32 => 6,
        DataType::Float64 => 7,
        DataType::UInt64 => 12,
        DataType::Int64 => 13,
        DataType::Int8 => 14,
        DataType::Bool => return Err("boolean data bands unsupported".into()),
    })
}

fn rounded(value: f64, data_type: i32) -> f64 {
    match data_type {
        1 => value.round().clamp(0., 255.),
        2 => value.round().clamp(0., 65535.),
        3 => value.round().clamp(-32768., 32767.),
        4 => value.round().clamp(0., u32::MAX as f64),
        5 => value.round().clamp(i32::MIN as f64, i32::MAX as f64),
        6 => (value as f32) as f64,
        _ => value,
    }
}

pub struct Grid {
    pub ifd: usize,
    pub positions: Vec<[f64; 2]>,
    pub size: [usize; 2],
    pub transform: [f64; 6],
    pub epsg: u16,
}

impl Source {
    pub fn data_type(&self, index: usize) -> Result<i32, String> {
        let ifd = &self.ifds[index];
        let bits = ifd.bits_per_sample();
        let formats = ifd.sample_format();
        if bits.is_empty()
            || formats.is_empty()
            || bits.iter().any(|b| *b != bits[0])
            || formats.iter().any(|f| *f != formats[0])
        {
            return Err("mixed sample types need GDAL fallback".into());
        }
        match (formats[0], bits[0]) {
            (SampleFormat::Uint, 8) => Ok(1),
            (SampleFormat::Uint, 16) => Ok(2),
            (SampleFormat::Int, 16) => Ok(3),
            (SampleFormat::Uint, 32) => Ok(4),
            (SampleFormat::Int, 32) => Ok(5),
            (SampleFormat::Float, 32) => Ok(6),
            (SampleFormat::Float, 64) => Ok(7),
            (SampleFormat::Uint, 64) => Ok(12),
            (SampleFormat::Int, 64) => Ok(13),
            (SampleFormat::Int, 8) => Ok(14),
            _ => Err("unsupported sample type".into()),
        }
    }
    pub async fn open(reader: Reader) -> Result<Self, String> {
        let reader = Arc::new(reader);
        let metadata_reader: Arc<dyn async_tiff::reader::AsyncFileReader> = reader.clone();
        let cache = ReadaheadMetadataCache::new(metadata_reader);
        let mut metadata = TiffMetadataReader::try_open(&cache)
            .await
            .map_err(|e| e.to_string())?;
        let ifds = metadata
            .read_all_ifds(&cache)
            .await
            .map_err(|e| e.to_string())?;
        let data = ifds
            .iter()
            .enumerate()
            .filter(|(_, d)| d.new_subfile_type().unwrap_or(0) & 4 == 0)
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        if data.is_empty() {
            return Err("no data IFDs".into());
        }
        Ok(Self { reader, ifds, data })
    }

    pub fn metadata(&self) -> Value {
        let first = &self.ifds[self.data[0]];
        json!({"width": first.image_width(), "height": first.image_height(), "bands": first.samples_per_pixel(),
            "epsg": first.geo_key_directory().and_then(|g| g.epsg_code()),
            "transform": affine(first).ok(), "nodata": first.gdal_nodata(),
            "ifds": self.ifds.iter().map(|d| json!({"width": d.image_width(), "height": d.image_height(),
                "subfile_type": d.new_subfile_type(), "compression": format!("{:?}", d.compression()),
                "tiled": d.tile_width().is_some(), "bands": d.samples_per_pixel()})).collect::<Vec<_>>()})
    }

    pub fn grid(&self, job: &Job) -> Result<Grid, String> {
        let base = &self.ifds[self.data[0]];
        let geo = base.geo_key_directory().ok_or("missing CRS")?;
        if geo.vertical.is_some() {
            return Err("vertical CRS needs GDAL fallback".into());
        }
        let epsg = geo
            .epsg_code()
            .filter(|code| *code != 32767)
            .ok_or("custom CRS needs GDAL fallback")?;
        let gt = affine(base)?;
        let mut positions;
        let level;
        let size;
        if job.mode == "window" {
            level = job.overview;
            let [x, y, w, h] = job.window.ok_or("window is required")?;
            size = [h, w];
            let ifd = self
                .ifds
                .get(*self.data.get(level).ok_or("overview not found")?)
                .ok_or("IFD not found")?;
            if x + w > ifd.image_width() as usize || y + h > ifd.image_height() as usize {
                return Err("window extends outside image".into());
            }
            positions = (0..h)
                .flat_map(|row| {
                    (0..w).map(move |col| [(x + col) as f64 + 0.5, (y + row) as f64 + 0.5])
                })
                .collect();
        } else {
            size = job.size;
            let coords =
                projection::coordinates(epsg, job.bounds.ok_or("tile bounds required")?, size)?;
            positions = coords
                .into_iter()
                .map(|p| pixel(gt, p[0], p[1]))
                .collect::<Vec<_>>();
            let center = (size[0] / 2).min(size[0] - 2) * size[1] + (size[1] / 2).min(size[1] - 2);
            let p = positions[center];
            let right = positions[center + 1];
            let down = positions[center + size[1]];
            let footprint = ((right[0] - p[0]).hypot(right[1] - p[1]))
                .min((down[0] - p[0]).hypot(down[1] - p[1]));
            level = self
                .data
                .iter()
                .enumerate()
                .filter(|(_, index)| {
                    let ifd = &self.ifds[**index];
                    let scale = (base.image_width() as f64 / ifd.image_width() as f64)
                        .max(base.image_height() as f64 / ifd.image_height() as f64);
                    scale <= footprint.max(1.) * job.overview_bias
                })
                .map(|(level, _)| level)
                .next_back()
                .unwrap_or(0);
            let ifd = &self.ifds[self.data[level]];
            let sx = ifd.image_width() as f64 / base.image_width() as f64;
            let sy = ifd.image_height() as f64 / base.image_height() as f64;
            for p in &mut positions {
                p[0] *= sx;
                p[1] *= sy;
            }
        }
        let ifd_index = self.data[level];
        let ifd = &self.ifds[ifd_index];
        let mut transform = gt;
        let sx = base.image_width() as f64 / ifd.image_width() as f64;
        let sy = base.image_height() as f64 / ifd.image_height() as f64;
        transform[1] *= sx;
        transform[4] *= sx;
        transform[2] *= sy;
        transform[5] *= sy;
        Ok(Grid {
            ifd: ifd_index,
            positions,
            size,
            transform,
            epsg,
        })
    }

    pub fn tile_keys(&self, grid: &Grid, linear: bool) -> Result<Vec<(usize, usize)>, String> {
        let ifd = &self.ifds[grid.ifd];
        let tw = ifd
            .tile_width()
            .ok_or("stripped TIFF needs GDAL fallback")? as usize;
        let th = ifd
            .tile_height()
            .ok_or("stripped TIFF needs GDAL fallback")? as usize;
        let mut keys = std::collections::BTreeSet::new();
        for p in &grid.positions {
            let x = if linear { p[0] - 0.5 } else { p[0] };
            let y = if linear { p[1] - 0.5 } else { p[1] };
            if !x.is_finite() || !y.is_finite() {
                continue;
            }
            for dy in 0..=usize::from(linear) {
                for dx in 0..=usize::from(linear) {
                    let ix = x.floor() as i64 + dx as i64;
                    let iy = y.floor() as i64 + dy as i64;
                    if ix >= 0
                        && iy >= 0
                        && ix < ifd.image_width() as i64
                        && iy < ifd.image_height() as i64
                    {
                        keys.insert((ix as usize / tw, iy as usize / th));
                    }
                }
            }
        }
        if keys.len() > 256 {
            return Err("requested grid exceeds 256 source blocks".into());
        }
        Ok(keys.into_iter().collect())
    }

    pub async fn blocks(
        &self,
        source: &str,
        ifd: usize,
        keys: &[(usize, usize)],
        cache: Arc<Mutex<Cache>>,
        cpu: Arc<Semaphore>,
        decoded: Arc<AtomicU64>,
    ) -> Result<HashMap<(usize, usize), Arc<Array>>, String> {
        let mut blocks = HashMap::new();
        let mut missing = Vec::new();
        for &(x, y) in keys {
            let key = format!("{source}:{ifd}:{x}:{y}");
            if let Some(block) = cache.lock().unwrap().get(&key) {
                blocks.insert((x, y), block);
            } else {
                missing.push((x, y));
            }
        }
        for batch in missing.chunks(32) {
            let tiles = self.ifds[ifd]
                .fetch_tiles(batch, self.reader.as_ref())
                .await
                .map_err(|e| e.to_string())?;
            let results: Vec<_> = stream::iter(tiles.into_iter().map(|tile| {
                let cache = cache.clone();
                let cpu = cpu.clone();
                let decoded = decoded.clone();
                async move {
                    let permit = cpu.acquire_owned().await.map_err(|e| e.to_string())?;
                    let x = tile.x();
                    let y = tile.y();
                    let array = tokio::task::spawn_blocking(move || {
                        let _permit = permit;
                        tile.decode(&Default::default())
                            .map(Arc::new)
                            .map_err(|e| e.to_string())
                    })
                    .await
                    .map_err(|e| e.to_string())??;
                    decoded.fetch_add(1, Ordering::Relaxed);
                    cache
                        .lock()
                        .unwrap()
                        .insert(format!("{source}:{ifd}:{x}:{y}"), array.clone());
                    Ok::<_, String>(((x, y), array))
                }
            }))
            .buffer_unordered(16)
            .try_collect()
            .await?;
            blocks.extend(results);
        }
        Ok(blocks)
    }

    pub fn mask_ifd(&self, index: usize) -> Option<usize> {
        let data = &self.ifds[index];
        self.ifds.iter().position(|mask| {
            mask.new_subfile_type().unwrap_or(0) & 4 != 0
                && mask.image_width() == data.image_width()
                && mask.image_height() == data.image_height()
        })
    }

    pub fn sample(
        &self,
        job: &Job,
        grid: &Grid,
        blocks: &HashMap<(usize, usize), Arc<Array>>,
        masks: &HashMap<(usize, usize), Arc<Array>>,
    ) -> Result<Vec<Raster>, String> {
        let ifd = &self.ifds[grid.ifd];
        if !matches!(
            ifd.photometric_interpretation(),
            PhotometricInterpretation::BlackIsZero
                | PhotometricInterpretation::RGB
                | PhotometricInterpretation::RGBPalette
        ) {
            return Err("photometric interpretation needs GDAL fallback".into());
        }
        let band_count = ifd.samples_per_pixel() as usize;
        if job
            .bands
            .iter()
            .any(|band| *band == 0 || *band > band_count)
        {
            return Err("invalid band".into());
        }
        let tw = ifd.tile_width().ok_or("not tiled")? as usize;
        let th = ifd.tile_height().ok_or("not tiled")? as usize;
        let planar = ifd.planar_configuration() == PlanarConfiguration::Planar;
        let nodata = ifd
            .gdal_nodata()
            .map(|s| s.trim_end_matches('\0').parse::<f64>())
            .transpose()
            .map_err(|e| e.to_string())?;
        let alpha = ifd.extra_samples().and_then(|extra| {
            extra
                .iter()
                .position(|e| {
                    matches!(
                        e,
                        ExtraSamples::AssociatedAlpha | ExtraSamples::UnassociatedAlpha
                    )
                })
                .map(|i| band_count - extra.len() + i)
        });
        let mask_ifd = self.mask_ifd(grid.ifd).map(|i| &self.ifds[i]);
        let get = |x: i64, y: i64, band: usize| -> Option<(f64, i32)> {
            if x < 0 || y < 0 || x >= ifd.image_width() as i64 || y >= ifd.image_height() as i64 {
                return None;
            }
            let (x, y) = (x as usize, y as usize);
            let array = blocks.get(&(x / tw, y / th))?;
            let row = y % th;
            let col = x % tw;
            let index = |band| {
                if planar {
                    band * tw * th + row * tw + col
                } else {
                    (row * tw + col) * band_count + band
                }
            };
            let v = value(array, index(band));
            let check_nodata = job.mode != "window" || (mask_ifd.is_none() && alpha.is_none());
            if !v.is_finite()
                || (check_nodata && nodata.is_some_and(|n| n == v || (n.is_nan() && v.is_nan())))
            {
                return None;
            }
            if alpha.is_some_and(|a| value(array, index(a)) <= 0.) {
                return None;
            }
            if let Some(mask) = mask_ifd {
                let mw = mask.tile_width()? as usize;
                let mh = mask.tile_height()? as usize;
                let array = masks.get(&(x / mw, y / mh))?;
                if value(array, (y % mh) * mw + x % mw) == 0. {
                    return None;
                }
            }
            Some((v, gdal_type(array.data_type()?).ok()?))
        };
        let data_type = blocks
            .values()
            .next()
            .map(|a| {
                a.data_type()
                    .ok_or_else(|| "unknown sample dtype".to_string())
                    .and_then(gdal_type)
            })
            .transpose()?
            .unwrap_or(self.data_type(grid.ifd)?);
        let mut rasters = Vec::new();
        for &band in &job.bands {
            let mut raster = Raster {
                width: grid.size[1],
                height: grid.size[0],
                data_type,
                values: Vec::with_capacity(grid.positions.len()),
                mask: Vec::with_capacity(grid.positions.len()),
            };
            for p in &grid.positions {
                let sample = if !p.iter().all(|v| v.is_finite()) {
                    None
                } else if job.resampling != "bilinear" || job.mode == "window" {
                    get(p[0].floor() as i64, p[1].floor() as i64, band - 1).map(|v| v.0)
                } else {
                    let x = p[0] - 0.5;
                    let y = p[1] - 0.5;
                    let ix = x.floor() as i64;
                    let iy = y.floor() as i64;
                    let fx = x - x.floor();
                    let fy = y - y.floor();
                    // Keep center validity separate from normalization of valid neighbors.
                    if get(p[0].floor() as i64, p[1].floor() as i64, band - 1).is_none() {
                        None
                    } else {
                        let mut sum = 0.;
                        let mut weight = 0.;
                        for (dx, dy, w) in [
                            (0, 0, (1. - fx) * (1. - fy)),
                            (1, 0, fx * (1. - fy)),
                            (0, 1, (1. - fx) * fy),
                            (1, 1, fx * fy),
                        ] {
                            if let Some((v, _)) = get(ix + dx, iy + dy, band - 1) {
                                sum += v * w;
                                weight += w;
                            }
                        }
                        (weight > 0.).then(|| rounded(sum / weight, data_type))
                    }
                };
                raster.mask.push(sample.is_none());
                raster.values.push(sample.unwrap_or(0.));
            }
            rasters.push(raster);
        }
        Ok(rasters)
    }
}

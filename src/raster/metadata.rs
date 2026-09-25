//! Metadata uses native source blocks, masks, and GDAL coordinate transforms.
use super::*;
use crate::statistics::{Digest, Moments};
use crate::storage::Metadata;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct MetadataOptions {
    pub use_chunks: Option<bool>,
    pub max_shape: Option<[usize; 2]>,
    pub large_raster_threshold: usize,
    pub extra_metadata: Value,
}
impl Default for MetadataOptions {
    fn default() -> Self {
        Self {
            use_chunks: None,
            max_shape: None,
            large_raster_threshold: 10980 * 10980,
            extra_metadata: json!({}),
        }
    }
}

impl Gdal {
    pub(super) unsafe fn source_mercator_bounds(&self, source: DatasetHandle) -> Result<[f64; 4]> {
        unsafe {
            let projection: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                self.symbol(b"GDALGetProjectionRef\0")?;
            let mut transform = [0.; 6];
            if (self.geo_transform)(source, transform.as_mut_ptr()) != 0 {
                return Err("source has no transform".into());
            }
            let width = (self.raster_x_size)(source) as f64;
            let height = (self.raster_y_size)(source) as f64;
            let corners = [[0., 0.], [width, 0.], [0., height], [width, height]].map(|[x, y]| {
                [
                    transform[0] + x * transform[1] + y * transform[2],
                    transform[3] + x * transform[4] + y * transform[5],
                ]
            });
            Coordinates::new_to(
                self,
                projection(source),
                CString::new(self.target_crs.as_str())
                    .map_err(|_| "invalid target CRS")?
                    .as_ptr(),
            )?
            .bounds(extent(&corners))
        }
    }
    // SAFETY: all callers give the documented C signature for the named symbol;
    // the process-lifetime library owns every returned function pointer.
    pub(super) unsafe fn symbol<T: Copy>(&self, name: &[u8]) -> Result<T> {
        unsafe {
            self._library
                .get::<T>(name)
                .map(|s| *s)
                .map_err(symbol_error)
        }
    }

    unsafe fn metadata_hull(&self, points: &[[f64; 2]]) -> Result<Vec<[f64; 2]>> {
        if points.is_empty() {
            return Ok(Vec::new());
        }
        unsafe {
            let create: unsafe extern "C" fn(c_int) -> *mut c_void =
                self.symbol(b"OGR_G_CreateGeometry\0")?;
            let destroy: unsafe extern "C" fn(*mut c_void) =
                self.symbol(b"OGR_G_DestroyGeometry\0")?;
            let point: unsafe extern "C" fn(*mut c_void, f64, f64) =
                self.symbol(b"OGR_G_AddPoint_2D\0")?;
            let add: unsafe extern "C" fn(*mut c_void, *mut c_void) -> c_int =
                self.symbol(b"OGR_G_AddGeometryDirectly\0")?;
            let hull: unsafe extern "C" fn(*mut c_void) -> *mut c_void =
                self.symbol(b"OGR_G_ConvexHull\0")?;
            let ring: unsafe extern "C" fn(*mut c_void, c_int) -> *mut c_void =
                self.symbol(b"OGR_G_GetGeometryRef\0")?;
            let count: unsafe extern "C" fn(*mut c_void) -> c_int =
                self.symbol(b"OGR_G_GetPointCount\0")?;
            let get_x: unsafe extern "C" fn(*mut c_void, c_int) -> f64 =
                self.symbol(b"OGR_G_GetX\0")?;
            let get_y: unsafe extern "C" fn(*mut c_void, c_int) -> f64 =
                self.symbol(b"OGR_G_GetY\0")?;
            let collection = create(4);
            if collection.is_null() {
                return Err("cannot allocate hull geometry".into());
            }
            for p in points {
                let g = create(1);
                if g.is_null() {
                    destroy(collection);
                    return Err("cannot allocate hull point".into());
                }
                point(g, p[0], p[1]);
                if add(collection, g) != 0 {
                    destroy(g);
                    destroy(collection);
                    return Err("cannot append hull point".into());
                }
            }
            let polygon = hull(collection);
            destroy(collection);
            if polygon.is_null() {
                return Err("cannot compute coverage hull (GEOS required)".into());
            }
            let r = ring(polygon, 0);
            if r.is_null() {
                destroy(polygon);
                return Err("coverage hull has no polygon ring".into());
            }
            let output = (0..count(r) - 1)
                .map(|i| [get_x(r, i), get_y(r, i)])
                .collect();
            destroy(polygon);
            Ok(output)
        }
    }

    pub fn compute_metadata(&self, path: &Path, options: &MetadataOptions) -> Result<Metadata> {
        if options.use_chunks == Some(true) && options.max_shape.is_some() {
            return Err("Cannot use both use_chunks and max_shape arguments".into());
        }
        if options.max_shape.is_some_and(|s| s.contains(&0)) {
            return Err("max_shape must be positive".into());
        }
        let src = self.open_dataset(path)?;
        unsafe {
            let width = (self.raster_x_size)(src.handle) as usize;
            let height = (self.raster_y_size)(src.handle) as usize;
            let band = (self.raster_band)(src.handle, 1);
            let dtype = (self.band_data_type)(band);
            let get_mask: unsafe extern "C" fn(BandHandle) -> BandHandle =
                self.symbol(b"GDALGetMaskBand\0")?;
            let get_block: unsafe extern "C" fn(BandHandle, *mut c_int, *mut c_int) =
                self.symbol(b"GDALGetBlockSize\0")?;
            let mut transform = [0.; 6];
            if (self.geo_transform)(src.handle, transform.as_mut_ptr()) != 0 {
                return Err("raster has no geotransform".into());
            }
            let corners = [
                [0., 0.],
                [width as f64, 0.],
                [width as f64, height as f64],
                [0., height as f64],
            ]
            .map(|p| affine(transform, p));
            let bounds = extent(&corners);
            let projection: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                self.symbol(b"GDALGetProjectionRef\0")?;
            let coord = Coordinates::new(self, projection(src.handle))?;
            let wgs_bounds = coord.bounds(bounds)?;
            let chunked = options.use_chunks.unwrap_or(
                options.max_shape.is_none() && width * height > options.large_raster_threshold,
            );
            let shape = options
                .max_shape
                .map(|s| [s[0].min(height), s[1].min(width)])
                .unwrap_or([height, width]);
            let (mut bx, mut by) = (width as c_int, height as c_int);
            if chunked {
                get_block(band, &mut bx, &mut by);
            }
            if bx <= 0 || by <= 0 {
                return Err("invalid raster block dimensions".into());
            }
            let mut hull = Vec::new();
            let mut values = Vec::new();
            let mut moments = Moments::default();
            let mut digest = Digest::default();
            let mut min = f64::MAX;
            // Crick initializes max to -DBL_MIN, observable for all-negative chunks.
            let mut max = if chunked {
                -f64::MIN_POSITIVE
            } else {
                -f64::MAX
            };
            let mut total = 0usize;
            let mut nodata_valid = 0;
            let nodata = (self.nodata)(band, &mut nodata_valid);
            for y in (0..height).step_by(by as usize) {
                for x in (0..width).step_by(bx as usize) {
                    let ww = (bx as usize).min(width - x);
                    let hh = (by as usize).min(height - y);
                    let [oh, ow] = if chunked { [hh, ww] } else { shape };
                    let window = [x as f64, y as f64, ww as f64, hh as f64];
                    let mut data = vec![0f64; ow * oh];
                    let mut mask = vec![0u8; ow * oh];
                    read_window(
                        self.raster_io_ex,
                        band,
                        &mut data,
                        ReadSpec {
                            width: ow,
                            height: oh,
                            data_type: GDT_FLOAT64,
                            algorithm: 0,
                            window,
                        },
                    )?;
                    read_window(
                        self.raster_io_ex,
                        get_mask(band),
                        &mut mask,
                        ReadSpec {
                            width: ow,
                            height: oh,
                            data_type: GDT_BYTE,
                            algorithm: 0,
                            window,
                        },
                    )?;
                    total += data.len();
                    let mut points = Vec::new();
                    let count_before = moments.count;
                    // Row extremes suffice for the hull of pixel squares; avoid polygonizing every pixel.
                    for row in 0..oh {
                        let (mut first, mut last) = (None, None);
                        for col in 0..ow {
                            let i = row * ow + col;
                            let v = data[i];
                            let valid = mask[i] != 0
                                && v.is_finite()
                                && (chunked || nodata_valid == 0 || v != nodata);
                            if !valid {
                                continue;
                            }
                            first.get_or_insert(col);
                            last = Some(col);
                            min = min.min(v);
                            max = max.max(v);
                            moments.add(v);
                            if chunked {
                                digest.add(v);
                            } else {
                                values.push(v);
                            }
                        }
                        if let (Some(a), Some(b)) = (first, last) {
                            for p in [
                                [a as f64, row as f64],
                                [(b + 1) as f64, row as f64],
                                [(b + 1) as f64, (row + 1) as f64],
                                [a as f64, (row + 1) as f64],
                            ] {
                                points.push(if chunked {
                                    let origin = affine(transform, [x as f64, y as f64]);
                                    let mut block_transform = transform;
                                    block_transform[0] = origin[0];
                                    block_transform[3] = origin[1];
                                    affine(block_transform, p)
                                } else {
                                    [
                                        bounds[0] + p[0] * (bounds[2] - bounds[0]) / ow as f64,
                                        bounds[3] - p[1] * (bounds[3] - bounds[1]) / oh as f64,
                                    ]
                                });
                            }
                        }
                    }
                    if chunked && moments.count - count_before == (ow * oh) as u64 {
                        // Rasterio windows.bounds uses these two corners; legacy
                        // all-valid chunks use that rectangle even for rotated grids.
                        let [w, s] = affine(transform, [x as f64, (y + hh) as f64]);
                        let [e, n] = affine(transform, [(x + ww) as f64, y as f64]);
                        points = vec![[w, s], [e, s], [e, n], [w, n]];
                    }
                    points.extend_from_slice(&hull);
                    hull = self.metadata_hull(&points)?;
                }
            }
            if moments.count == 0 {
                return Err(format!(
                    "Raster file {} does not contain any valid data",
                    path.display()
                ));
            }
            let (mean, stdev, percentiles) = if chunked {
                (
                    moments.sum / moments.count as f64,
                    (moments.m2 / moments.count as f64).sqrt(),
                    digest.percentiles(),
                )
            } else {
                let n = values.len() as f64;
                let mean = if dtype == 6 {
                    pairwise_f32(&values) as f64 / n
                } else {
                    pairwise(&values) / n
                };
                let mean = if dtype == 6 { mean as f32 as f64 } else { mean };
                let squares: Vec<f64> = values
                    .iter()
                    .map(|v| {
                        if dtype == 6 {
                            let d = (*v as f32) - (mean as f32);
                            (d * d) as f64
                        } else {
                            (v - mean).powi(2)
                        }
                    })
                    .collect();
                let stdev = if dtype == 6 {
                    ((pairwise_f32(&squares) / (n as f32)).sqrt()) as f64
                } else {
                    (pairwise(&squares) / n).sqrt()
                };
                values.sort_unstable_by(f64::total_cmp);
                let ps = (1..100)
                    .map(|p| {
                        let i = (values.len() - 1) as f64 * (p as f64 / 100.);
                        let a = i.floor() as usize;
                        let b = i.ceil() as usize;
                        values[a] + (values[b] - values[a]) * (i - a as f64)
                    })
                    .collect();
                (mean, stdev, ps)
            };
            // Match GEOS clockwise hull orientation. Ring order is not geometrical meaning.
            hull.reverse();
            for p in &mut hull {
                *p = coord.point(*p)?;
            }
            hull.push(hull[0]);
            Ok(Metadata {
                keys: Default::default(),
                bounds: wgs_bounds,
                convex_hull: json!({"type":"Polygon","coordinates":[hull]}),
                valid_percentage: moments.count as f64 / total as f64 * 100.,
                range: [min, max],
                mean,
                stdev,
                percentiles,
                metadata: if !json_truthy(&options.extra_metadata) {
                    json!({})
                } else {
                    options.extra_metadata.clone()
                },
            })
        }
    }
}
fn affine(t: [f64; 6], p: [f64; 2]) -> [f64; 2] {
    [
        p[0] * t[1] + p[1] * t[2] + t[0],
        p[0] * t[4] + p[1] * t[5] + t[3],
    ]
}
fn extent(points: &[[f64; 2]]) -> [f64; 4] {
    points.iter().fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |b, p| {
            [
                b[0].min(p[0]),
                b[1].min(p[1]),
                b[2].max(p[0]),
                b[3].max(p[1]),
            ]
        },
    )
}
// NumPy's contiguous pairwise reduction (eight accumulators, 128-element blocks).
macro_rules! pairwise_impl {
    ($name:ident,$t:ty) => {
        fn $name(v: &[f64]) -> $t {
            if v.len() < 8 {
                return v.iter().fold(-0.0 as $t, |a, b| a + *b as $t);
            }
            if v.len() <= 128 {
                let mut r = [0. as $t; 8];
                for i in 0..8 {
                    r[i] = v[i] as $t;
                }
                let end = v.len() / 8 * 8;
                for block in v[8..end].chunks_exact(8) {
                    for i in 0..8 {
                        r[i] += block[i] as $t;
                    }
                }
                let mut sum = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
                for x in &v[end..] {
                    sum += *x as $t;
                }
                sum
            } else {
                let n = (v.len() / 2) / 8 * 8;
                $name(&v[..n]) + $name(&v[n..])
            }
        }
    };
}
pairwise_impl!(pairwise, f64);
pairwise_impl!(pairwise_f32, f32);

struct Coordinates {
    source: *mut c_void,
    target: *mut c_void,
    handle: *mut c_void,
    destroy_srs: unsafe extern "C" fn(*mut c_void),
    destroy: unsafe extern "C" fn(*mut c_void),
    transform: unsafe extern "C" fn(*mut c_void, c_int, *mut f64, *mut f64, *mut f64) -> c_int,
    transform_bounds: unsafe extern "C" fn(
        *mut c_void,
        f64,
        f64,
        f64,
        f64,
        *mut f64,
        *mut f64,
        *mut f64,
        *mut f64,
        c_int,
    ) -> c_int,
}
impl Coordinates {
    unsafe fn new(g: &Gdal, projection: *const c_char) -> Result<Self> {
        unsafe { Self::new_to(g, projection, c"EPSG:4326".as_ptr()) }
    }
    unsafe fn new_to(g: &Gdal, projection: *const c_char, target: *const c_char) -> Result<Self> {
        unsafe {
            let new: unsafe extern "C" fn(*const c_char) -> *mut c_void =
                g.symbol(b"OSRNewSpatialReference\0")?;
            let set: unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int =
                g.symbol(b"OSRSetFromUserInput\0")?;
            let axis: unsafe extern "C" fn(*mut c_void, c_int) =
                g.symbol(b"OSRSetAxisMappingStrategy\0")?;
            let make: unsafe extern "C" fn(*mut c_void, *mut c_void) -> *mut c_void =
                g.symbol(b"OCTNewCoordinateTransformation\0")?;
            let mut result = Self {
                source: ptr::null_mut(),
                target: ptr::null_mut(),
                handle: ptr::null_mut(),
                destroy_srs: g.symbol(b"OSRDestroySpatialReference\0")?,
                destroy: g.symbol(b"OCTDestroyCoordinateTransformation\0")?,
                transform: g.symbol(b"OCTTransform\0")?,
                transform_bounds: g.symbol(b"OCTTransformBounds\0")?,
            };
            result.source = new(projection);
            result.target = new(ptr::null());
            if result.source.is_null() || result.target.is_null() || set(result.target, target) != 0
            {
                return Err("invalid coordinate system".into());
            }
            axis(result.source, 0);
            axis(result.target, 0);
            result.handle = make(result.source, result.target);
            if result.handle.is_null() {
                return Err("cannot transform coordinate system".into());
            }
            Ok(result)
        }
    }
    fn point(&self, p: [f64; 2]) -> Result<[f64; 2]> {
        let [mut x, mut y] = p;
        if unsafe { (self.transform)(self.handle, 1, &mut x, &mut y, ptr::null_mut()) } == 0 {
            return Err("coordinate transformation failed".into());
        }
        Ok([x, y])
    }
    fn bounds(&self, b: [f64; 4]) -> Result<[f64; 4]> {
        let (mut a, mut c, mut d, mut e) = (0., 0., 0., 0.);
        if unsafe {
            (self.transform_bounds)(
                self.handle,
                b[0],
                b[1],
                b[2],
                b[3],
                &mut a,
                &mut c,
                &mut d,
                &mut e,
                21,
            )
        } == 0
        {
            return Err("bounds transformation failed".into());
        }
        Ok([a, c, d, e])
    }
}
impl Drop for Coordinates {
    fn drop(&mut self) {
        unsafe {
            if !self.handle.is_null() {
                (self.destroy)(self.handle);
            }
            if !self.source.is_null() {
                (self.destroy_srs)(self.source);
            }
            if !self.target.is_null() {
                (self.destroy_srs)(self.target);
            }
        }
    }
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

//! Worker-owned GDAL boundary for local and remote raster reads.

use crate::Result;
use libloading::Library;
use std::cell::RefCell;
use std::collections::VecDeque;
use std::ffi::{CString, c_char, c_double, c_int, c_void};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Instant, SystemTime};

mod fast;
pub(crate) use fast::SharedBlocks;
mod assets;
mod diagnostics;
mod metadata;
mod native;
mod optimize;
mod plan;
mod tiff;
mod utm;
mod warped;
pub use metadata::MetadataOptions;
pub use optimize::OptimizeOptions;

type DatasetHandle = *mut c_void;
type BandHandle = *mut c_void;

const GA_READ_ONLY: c_int = 0;
const GF_READ: c_int = 0;
const GDT_BYTE: c_int = 1;
const GDT_FLOAT64: c_int = 7;
const GRA_NEAREST: c_int = 0;

const GRIORA_NEAREST: c_int = 0;

static VRT_SEQUENCE: AtomicU64 = AtomicU64::new(0);
static GDAL_INITIALIZATION: std::sync::Mutex<()> = std::sync::Mutex::new(());
// GDAL installs pthread TLS destructors. Keep code mapped through thread exit.
static GDAL_LIBRARY: std::sync::OnceLock<Library> = std::sync::OnceLock::new();
static GDAL_REGISTERED: std::sync::Once = std::sync::Once::new();

#[derive(Clone, Debug, PartialEq)]
pub struct Raster {
    /// GDAL sample type retained for NumPy-compatible compute arithmetic.
    pub data_type: i32,
    pub width: usize,
    pub height: usize,
    pub values: Vec<f64>,
    pub mask: Vec<bool>,
}

#[derive(Clone, Copy, Debug)]
pub struct ReadRequest {
    /// Explicit native grid, per-band validity and asset revision identity.
    pub grid: Option<crate::layers::GridRead>,
    /// One-based source band. Catalog metadata remains associated with band one.
    pub band: usize,
    pub bounds_mercator: Option<[f64; 4]>,
    /// Terracotta's public tuple is `(height, width)`.
    pub size: [usize; 2],
    pub preserve_values: bool,
}

impl Default for ReadRequest {
    fn default() -> Self {
        Self {
            grid: None,
            band: 1,
            bounds_mercator: None,
            size: [256, 256],
            preserve_values: false,
        }
    }
}

pub struct Gdal {
    tiff: tiff::Backend,
    profile_reads: bool,
    native_direct: bool,
    native_plan: bool,
    native_target: SpatialReference,
    sources: RefCell<VecDeque<SourceEntry>>,
    remote_sources: RefCell<VecDeque<RemoteSource>>,
    remote_source_ttl: std::time::Duration,
    source_blocks: Option<Arc<Mutex<fast::SharedBlocks>>>,
    target_crs: String,
    reprojection: (c_int, &'static str),
    resampling: c_int,
    band_data_type: unsafe extern "C" fn(BandHandle) -> c_int,
    color_interpretation: unsafe extern "C" fn(BandHandle) -> c_int,
    open: unsafe extern "C" fn(*const c_char, c_int) -> DatasetHandle,
    close: unsafe extern "C" fn(DatasetHandle),
    auto_warp: unsafe extern "C" fn(
        DatasetHandle,
        *const c_char,
        *const c_char,
        c_int,
        c_double,
        *const c_void,
    ) -> DatasetHandle,
    raster_x_size: unsafe extern "C" fn(DatasetHandle) -> c_int,
    raster_y_size: unsafe extern "C" fn(DatasetHandle) -> c_int,
    raster_count: unsafe extern "C" fn(DatasetHandle) -> c_int,
    geo_transform: unsafe extern "C" fn(DatasetHandle, *mut c_double) -> c_int,
    raster_band: unsafe extern "C" fn(DatasetHandle, c_int) -> BandHandle,
    nodata: unsafe extern "C" fn(BandHandle, *mut c_int) -> c_double,
    raster_io_ex: unsafe extern "C" fn(
        BandHandle,
        c_int,
        c_int,
        c_int,
        c_int,
        c_int,
        *mut c_void,
        c_int,
        c_int,
        c_int,
        isize,
        isize,
        *mut RasterIoExtraArg,
    ) -> c_int,
    overview_count: unsafe extern "C" fn(BandHandle) -> c_int,
    version_info: unsafe extern "C" fn(*const c_char) -> *const c_char,
    vsi_unlink: unsafe extern "C" fn(*const c_char) -> c_int,
    warp_options_new: unsafe extern "C" fn(*mut *mut c_char, *mut c_void) -> *mut c_void,
    warp_options_free: unsafe extern "C" fn(*mut c_void),
    warp: unsafe extern "C" fn(
        *const c_char,
        DatasetHandle,
        c_int,
        *mut DatasetHandle,
        *const c_void,
        *mut c_int,
    ) -> DatasetHandle,
    _thread_config: ThreadConfig,
    _library: &'static Library,
}

#[repr(C)]
struct RasterIoExtraArg {
    version: c_int,
    resample_algorithm: c_int,
    progress: *mut c_void,
    progress_data: *mut c_void,
    floating_window_valid: c_int,
    x_offset: c_double,
    y_offset: c_double,
    x_size: c_double,
    y_size: c_double,
}

impl Gdal {
    pub fn load() -> Result<Self> {
        let _initialization = GDAL_INITIALIZATION
            .lock()
            .map_err(|_| "GDAL initialization lock poisoned")?;
        let path = find_gdal_library()?;
        let reprojection = resampling_method(
            &crate::settings::var("TC_REPROJECTION_METHOD").unwrap_or_else(|_| "linear".into()),
        )?;
        let resampling = resampling_method(
            &crate::settings::var("TC_RESAMPLING_METHOD").unwrap_or_else(|_| "average".into()),
        )?
        .0;
        // SAFETY: symbols are copied function pointers and the owning library is
        // retained for the complete lifetime of this object.
        unsafe {
            let library = if let Some(library) = GDAL_LIBRARY.get() {
                library
            } else {
                let library = Library::new(&path).map_err(|error| {
                    format!("could not load GDAL runtime {}: {error}", path.display())
                })?;
                let _ = GDAL_LIBRARY.set(library);
                GDAL_LIBRARY
                    .get()
                    .expect("serialized GDAL library initialization")
            };
            let all_register: unsafe extern "C" fn() =
                *library.get(b"GDALAllRegister\0").map_err(symbol_error)?;
            let open: unsafe extern "C" fn(*const c_char, c_int) -> DatasetHandle =
                *library.get(b"GDALOpen\0").map_err(symbol_error)?;
            let close: unsafe extern "C" fn(DatasetHandle) =
                *library.get(b"GDALClose\0").map_err(symbol_error)?;
            let auto_warp: unsafe extern "C" fn(
                DatasetHandle,
                *const c_char,
                *const c_char,
                c_int,
                c_double,
                *const c_void,
            ) -> DatasetHandle = *library
                .get(b"GDALAutoCreateWarpedVRT\0")
                .map_err(symbol_error)?;
            let raster_x_size: unsafe extern "C" fn(DatasetHandle) -> c_int =
                *library.get(b"GDALGetRasterXSize\0").map_err(symbol_error)?;
            let raster_y_size: unsafe extern "C" fn(DatasetHandle) -> c_int =
                *library.get(b"GDALGetRasterYSize\0").map_err(symbol_error)?;
            let raster_count: unsafe extern "C" fn(DatasetHandle) -> c_int =
                *library.get(b"GDALGetRasterCount\0").map_err(symbol_error)?;
            let geo_transform: unsafe extern "C" fn(DatasetHandle, *mut c_double) -> c_int =
                *library
                    .get(b"GDALGetGeoTransform\0")
                    .map_err(symbol_error)?;
            let raster_band: unsafe extern "C" fn(DatasetHandle, c_int) -> BandHandle =
                *library.get(b"GDALGetRasterBand\0").map_err(symbol_error)?;
            let nodata: unsafe extern "C" fn(BandHandle, *mut c_int) -> c_double = *library
                .get(b"GDALGetRasterNoDataValue\0")
                .map_err(symbol_error)?;
            let raster_io_ex: unsafe extern "C" fn(
                BandHandle,
                c_int,
                c_int,
                c_int,
                c_int,
                c_int,
                *mut c_void,
                c_int,
                c_int,
                c_int,
                isize,
                isize,
                *mut RasterIoExtraArg,
            ) -> c_int = *library.get(b"GDALRasterIOEx\0").map_err(symbol_error)?;
            let overview_count: unsafe extern "C" fn(BandHandle) -> c_int = *library
                .get(b"GDALGetOverviewCount\0")
                .map_err(symbol_error)?;
            let version_info: unsafe extern "C" fn(*const c_char) -> *const c_char =
                *library.get(b"GDALVersionInfo\0").map_err(symbol_error)?;
            let vsi_unlink: unsafe extern "C" fn(*const c_char) -> c_int =
                *library.get(b"VSIUnlink\0").map_err(symbol_error)?;
            let warp_options_new: unsafe extern "C" fn(
                *mut *mut c_char,
                *mut c_void,
            ) -> *mut c_void = *library
                .get(b"GDALWarpAppOptionsNew\0")
                .map_err(symbol_error)?;
            let warp_options_free: unsafe extern "C" fn(*mut c_void) = *library
                .get(b"GDALWarpAppOptionsFree\0")
                .map_err(symbol_error)?;
            let warp: unsafe extern "C" fn(
                *const c_char,
                DatasetHandle,
                c_int,
                *mut DatasetHandle,
                *const c_void,
                *mut c_int,
            ) -> DatasetHandle = *library.get(b"GDALWarp\0").map_err(symbol_error)?;
            GDAL_REGISTERED.call_once(|| all_register());
            let new_ref: unsafe extern "C" fn(*const c_char) -> *mut c_void = *library
                .get(b"OSRNewSpatialReference\0")
                .map_err(symbol_error)?;
            let set_ref: unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int = *library
                .get(b"OSRSetFromUserInput\0")
                .map_err(symbol_error)?;
            let native_target = SpatialReference {
                handle: new_ref(ptr::null()),
                destroy: *library
                    .get(b"OSRDestroySpatialReference\0")
                    .map_err(symbol_error)?,
            };
            if native_target.handle.is_null()
                || set_ref(native_target.handle, c"EPSG:3857".as_ptr()) != 0
            {
                return Err("could not initialize rendering CRS".into());
            }
            let remote_source_ttl = crate::settings::var("TC_REMOTE_SOURCE_CACHE_TTL_SECONDS")
                .unwrap_or_else(|_| {
                    if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
                        "0".into()
                    } else {
                        "60".into()
                    }
                })
                .parse::<u64>()
                .map_err(|_| "invalid remote source cache TTL")?;
            if remote_source_ttl > 86400 {
                return Err("remote source cache TTL exceeds 86400 seconds".into());
            }
            Ok(Self {
                tiff: tiff::Backend::new(
                    !crate::compatibility::CompatibilityProfile::current().is_terracotta()
                        && crate::settings::flag("TC_ASYNC_TIFF", true)?,
                    std::time::Duration::from_secs(remote_source_ttl),
                ),
                profile_reads: std::env::var_os("TC_PROFILE_RASTER").is_some(),
                native_direct: !crate::compatibility::CompatibilityProfile::current()
                    .is_terracotta()
                    && crate::settings::flag("TC_NATIVE_DIRECT_READ", true)?,
                native_plan: crate::settings::flag("TC_NATIVE_WARP_PLAN", true)?,
                native_target,
                sources: RefCell::new(VecDeque::new()),
                remote_sources: RefCell::new(VecDeque::new()),
                remote_source_ttl: std::time::Duration::from_secs(remote_source_ttl),
                source_blocks: None,
                _thread_config: ThreadConfig::new(library, &path)?,
                target_crs: crate::settings::var("TC_TARGET_CRS")
                    .unwrap_or_else(|_| "EPSG:3857".into()),
                reprojection,
                resampling,
                band_data_type: *library
                    .get(b"GDALGetRasterDataType\0")
                    .map_err(symbol_error)?,
                color_interpretation: *library
                    .get(b"GDALGetRasterColorInterpretation\0")
                    .map_err(symbol_error)?,
                open,
                close,
                auto_warp,
                raster_x_size,
                raster_y_size,
                raster_count,
                geo_transform,
                raster_band,
                nodata,
                raster_io_ex,
                overview_count,
                version_info,
                vsi_unlink,
                warp_options_new,
                warp_options_free,
                warp,
                _library: library,
            })
        }
    }

    pub(crate) fn load_with_shared_blocks(blocks: Arc<Mutex<fast::SharedBlocks>>) -> Result<Self> {
        let mut gdal = Self::load()?;
        gdal.source_blocks = Some(blocks);
        Ok(gdal)
    }

    pub fn version(&self) -> String {
        let request = CString::new("RELEASE_NAME").expect("literal");
        unsafe {
            let pointer = (self.version_info)(request.as_ptr());
            if pointer.is_null() {
                return "unknown".into();
            }
            std::ffi::CStr::from_ptr(pointer)
                .to_string_lossy()
                .into_owned()
        }
    }

    pub fn band_count(&self, path: &Path) -> Result<i32> {
        if let Some(source) = self.tiff.source(path) {
            return Ok(source.bands() as i32);
        }
        let source = self.open_dataset(path)?;
        Ok(unsafe { (self.raster_count)(source.handle) })
    }

    /// Embedded nodata is authoritative before interpolation in STAC renders.
    pub fn band_nodata(&self, path: &Path, band: usize) -> Result<Option<f64>> {
        if let Some(source) = self.tiff.source(path) {
            if band == 0 || band > source.bands() {
                return Err("STAC band out of range".into());
            }
            if let Some(nodata) = source.nodata() {
                return Ok(nodata);
            }
        }
        let source = self.open_dataset(path)?;
        // SAFETY: the checked band belongs to the live dataset guard.
        unsafe {
            if band == 0 || band > (self.raster_count)(source.handle) as usize {
                return Err("STAC band out of range".into());
            }
            let mut present = 0;
            let value = (self.nodata)(
                (self.raster_band)(source.handle, band as c_int),
                &mut present,
            );
            Ok((present != 0).then_some(value))
        }
    }

    pub fn inspect(&self, path: &Path) -> Result<(usize, usize, i32)> {
        if let Some(source) = self.tiff.source(path) {
            return Ok(source.dimensions());
        }
        let source = self.open_dataset(path)?;
        unsafe {
            let width = (self.raster_x_size)(source.handle) as usize;
            let height = (self.raster_y_size)(source.handle) as usize;
            let band = (self.raster_band)(source.handle, 1);
            let overviews = if band.is_null() {
                0
            } else {
                (self.overview_count)(band)
            };
            Ok((width, height, overviews))
        }
    }

    fn suggested_geometry(
        &self,
        source: DatasetHandle,
        algorithm: c_int,
    ) -> Result<SuggestedGeometry> {
        // calculate_default_transform uses the source bounding rectangle and
        // dimensions, rather than the source's rotated affine coefficients.
        let proxy = unsafe {
            let mut transform = [0.; 6];
            if (self.geo_transform)(source, transform.as_mut_ptr()) != 0 {
                return Err("source has no transform".into());
            }
            let width = (self.raster_x_size)(source);
            let height = (self.raster_y_size)(source);
            let corners = [
                [0., 0.],
                [width as f64, 0.],
                [0., height as f64],
                [width as f64, height as f64],
            ]
            .map(|[x, y]| {
                [
                    transform[0] + x * transform[1] + y * transform[2],
                    transform[3] + x * transform[4] + y * transform[5],
                ]
            });
            let bounds = metadata::extent(&corners);
            let driver: unsafe extern "C" fn(*const c_char) -> *mut c_void =
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
            let set_transform: unsafe extern "C" fn(DatasetHandle, *mut f64) -> c_int =
                self.symbol(b"GDALSetGeoTransform\0")?;
            let projection: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                self.symbol(b"GDALGetProjectionRef\0")?;
            let set_projection: unsafe extern "C" fn(DatasetHandle, *const c_char) -> c_int =
                self.symbol(b"GDALSetProjection\0")?;
            let handle = create(
                driver(c"VRT".as_ptr()),
                c"".as_ptr(),
                width,
                height,
                1,
                GDT_BYTE,
                ptr::null_mut(),
            );
            if handle.is_null() {
                return Err("cannot create suggested-grid proxy".into());
            }
            let proxy = DatasetGuard {
                handle,
                close: self.close,
            };
            let mut axis_aligned = [
                bounds[0],
                (bounds[2] - bounds[0]) / width as f64,
                0.,
                bounds[3],
                0.,
                (bounds[1] - bounds[3]) / height as f64,
            ];
            if set_transform(handle, axis_aligned.as_mut_ptr()) != 0
                || set_projection(handle, projection(source)) != 0
            {
                return Err("cannot set suggested-grid georeferencing".into());
            }
            proxy
        };
        let target = CString::new(self.target_crs.as_str()).map_err(|_| "invalid target CRS")?;
        let warped = unsafe {
            (self.auto_warp)(
                proxy.handle,
                ptr::null(),
                target.as_ptr(),
                algorithm,
                0.0,
                ptr::null(),
            )
        };
        if warped.is_null() {
            return Err("GDAL could not create a Web Mercator view".into());
        }
        let warped = DatasetGuard {
            handle: warped,
            close: self.close,
        };
        unsafe {
            let mut transform = [0.0; 6];
            if (self.geo_transform)(warped.handle, transform.as_mut_ptr()) != 0 {
                return Err("GDAL warped dataset has no transform".into());
            }
            if transform[2] != 0.0 || transform[4] != 0.0 {
                return Err(
                    "rotated warped datasets are not supported by this raster slice".into(),
                );
            }
            Ok(SuggestedGeometry { transform })
        }
    }

    fn cached_suggested_geometry(
        &self,
        source: DatasetHandle,
        algorithm: c_int,
        stamp: Option<(u64, std::time::SystemTime)>,
        suggested_index: usize,
    ) -> Result<SuggestedGeometry> {
        if stamp.is_some() {
            let cached = self
                .sources
                .borrow()
                .front()
                .expect("source cached")
                .suggested[suggested_index];
            if let Some(cached) = cached {
                return Ok(cached);
            }
            let geometry = self.suggested_geometry(source, algorithm)?;
            self.sources
                .borrow_mut()
                .front_mut()
                .expect("source cached")
                .suggested[suggested_index] = Some(geometry);
            Ok(geometry)
        } else {
            self.suggested_geometry(source, algorithm)
        }
    }

    pub fn read(&self, path: &Path, request: ReadRequest) -> Result<Raster> {
        if request.size[0] == 0 || request.size[1] == 0 {
            return Err("tile dimensions must be positive".into());
        }
        if self.native_direct && self.target_crs.eq_ignore_ascii_case("EPSG:3857") {
            if let Some(mut rasters) = self.tiff.read(path, &[request], self.reprojection.0) {
                return Ok(rasters.remove(0));
            }
        }
        let started = Instant::now();
        crate::telemetry::raster_backend_read(crate::telemetry::RasterBackend::Gdal);
        let lease = self.source(path, request.grid.map(|grid| grid.revision))?;
        let source_handle = lease.handle;
        let stamp = lease.stamp;
        let opened = Instant::now();
        let _timer = crate::telemetry::timer(crate::telemetry::Stage::RasterRead);
        let _remote_timer = crate::execution::is_remote(path)
            .then(|| crate::telemetry::timer(crate::telemetry::Stage::RemoteRasterRead));
        if let Some(grid) = request.grid {
            return self.read_asset_grid(source_handle, request, grid);
        }
        if let Some(raster) = self.read_native(source_handle, request)? {
            return Ok(raster);
        }
        if request.band != 1 {
            return Err("source band selection requires native direct tile reads".into());
        }
        if let Some(raster) = self.read_utm_fast(path, stamp, source_handle, request)? {
            return Ok(raster);
        }
        if let Some(raster) = self.read_mercator_fast(path, stamp, source_handle, request)? {
            return Ok(raster);
        }
        self.read_general(request, source_handle, stamp, started, opened)
    }

    pub fn read_bands(&self, path: &Path, requests: &[ReadRequest]) -> Result<Vec<Raster>> {
        if self.native_direct && self.target_crs.eq_ignore_ascii_case("EPSG:3857") {
            if let Some(rasters) = self.tiff.read(path, requests, self.reprojection.0) {
                return Ok(rasters);
            }
        }
        if requests.iter().any(|request| request.grid.is_some()) {
            return requests
                .iter()
                .map(|request| self.read(path, *request))
                .collect();
        }
        crate::telemetry::raster_backend_read(crate::telemetry::RasterBackend::Gdal);
        let lease = self.source(path, None)?;
        let timer = crate::telemetry::timer(crate::telemetry::Stage::RasterRead);
        let remote_timer = crate::execution::is_remote(path)
            .then(|| crate::telemetry::timer(crate::telemetry::Stage::RemoteRasterRead));
        if let Some(rasters) = self.read_native_bands(lease.handle, requests)? {
            return Ok(rasters);
        }
        drop(timer);
        drop(remote_timer);
        requests
            .iter()
            .map(|request| self.read(path, *request))
            .collect()
    }

    fn source(&self, path: &Path, revision: Option<[u8; 32]>) -> Result<SourceLease> {
        let _timer = crate::telemetry::timer(crate::telemetry::Stage::SourceAccess);
        let _remote_timer = crate::execution::is_remote(path)
            .then(|| crate::telemetry::timer(crate::telemetry::Stage::RemoteSourceAccess));
        // A GDAL block cache entry belongs to its open dataset. Keep local
        // sources worker-owned so adjacent map tiles can reuse decoded blocks.
        // Remote sources use the configured bounded snapshot lease.
        let local = !crate::execution::is_remote(path);
        let stamp = if local {
            std::fs::metadata(path)
                .ok()
                .and_then(|metadata| Some((metadata.len(), metadata.modified().ok()?)))
        } else {
            None
        };
        let cache_remote = !local && !self.remote_source_ttl.is_zero();
        let uncached_source = if stamp.is_none() && !cache_remote {
            crate::telemetry::source_hit(false);
            Some(self.open_dataset(path)?)
        } else {
            None
        };
        let source_handle = if let Some(source) = &uncached_source {
            source.handle
        } else if cache_remote {
            let mut sources = self.remote_sources.borrow_mut();
            sources.retain(|entry| entry.opened.elapsed() < self.remote_source_ttl);
            if let Some(position) = sources
                .iter()
                .position(|entry| entry.path == path && entry.revision == revision)
            {
                crate::telemetry::source_hit(true);
                let entry = sources.remove(position).expect("existing remote source");
                sources.push_front(entry);
            } else {
                crate::telemetry::source_hit(false);
                sources.retain(|entry| entry.path != path);
                sources.push_front(RemoteSource {
                    revision,
                    path: path.to_path_buf(),
                    native: RefCell::new(plan::NativeSource::default()),
                    dataset: self.open_dataset(path)?,
                    opened: Instant::now(),
                });
                sources.truncate(12);
            }
            sources
                .front()
                .expect("remote source just cached")
                .dataset
                .handle
        } else {
            let mut sources = self.sources.borrow_mut();
            let stamp = stamp.expect("cached local file has metadata");
            let generation = crate::execution::source_generation(path);
            let position = sources.iter().position(|entry| {
                entry.path == path
                    && entry.revision == revision
                    && entry.stamp == stamp
                    && entry.generation == generation
            });
            if let Some(position) = position {
                crate::telemetry::source_hit(true);
                let entry = sources.remove(position).expect("existing source entry");
                sources.push_front(entry);
            } else {
                crate::telemetry::source_hit(false);
                let dataset = self.open_dataset(path)?;
                sources.retain(|entry| entry.path != path);
                sources.push_front(SourceEntry {
                    revision,
                    generation,
                    path: path.to_path_buf(),
                    stamp,
                    suggested: [None, None],
                    native: RefCell::new(plan::NativeSource::default()),
                    dataset,
                });
                sources.truncate(12);
            }
            sources.front().expect("source just cached").dataset.handle
        };
        Ok(SourceLease {
            _opened: uncached_source,
            handle: source_handle,
            stamp,
        })
    }

    fn read_general(
        &self,
        request: ReadRequest,
        source_handle: DatasetHandle,
        stamp: Option<(u64, SystemTime)>,
        started: Instant,
        opened: Instant,
    ) -> Result<Raster> {
        let algorithm = if request.preserve_values {
            GRA_NEAREST
        } else {
            self.reprojection.0
        };
        let geometry = self.cached_suggested_geometry(
            source_handle,
            algorithm,
            stamp,
            usize::from(request.preserve_values),
        )?;
        let suggested = Instant::now();

        unsafe {
            let transform = geometry.transform;
            let requested = match request.bounds_mercator {
                Some(bounds) => bounds,
                None => self.source_mercator_bounds(source_handle)?,
            };
            if requested[2] <= requested[0] || requested[3] <= requested[1] {
                return Err("legacy internal error".into());
            }
            let width = request.size[1];
            let height = request.size[0];
            let source_bounds = self.source_mercator_bounds(source_handle)?;
            let coverage = ((source_bounds[2] - source_bounds[0]) / (requested[2] - requested[0]))
                * ((source_bounds[3] - source_bounds[1]) / (requested[3] - requested[1]));
            if coverage < 0.01 {
                return Err("tile out of bounds".into());
            }
            // Legacy Affine.from_bounds receives (height,width) at this step.
            let requested_x_resolution = (requested[2] - requested[0]) / height as f64;
            let requested_y_resolution = (requested[3] - requested[1]) / width as f64;
            let finer_than_native = requested_x_resolution < transform[1].abs()
                || requested_y_resolution < transform[5].abs();
            let (x_resolution, y_resolution) = if finer_than_native {
                (requested_x_resolution, requested_y_resolution)
            } else {
                (transform[1].abs(), transform[5].abs())
            };
            let read_algorithm = if request.preserve_values || finer_than_native {
                GRIORA_NEAREST
            } else {
                self.resampling
            };
            let intermediate_width = ((requested[2] - requested[0]) / x_resolution)
                .round_ties_even()
                .max(1.0) as usize;
            let intermediate_height = ((requested[3] - requested[1]) / y_resolution)
                .round_ties_even()
                .max(1.0) as usize;
            let actual_x_resolution = (requested[2] - requested[0]) / intermediate_width as f64;
            let actual_y_resolution = (requested[3] - requested[1]) / intermediate_height as f64;
            let padding = 2.0;
            let padded_transform = [
                requested[0] - padding * actual_x_resolution,
                actual_x_resolution,
                0.,
                requested[3] + padding * actual_y_resolution,
                0.,
                -actual_y_resolution,
            ];
            let custom = self.create_padded_vrt(
                source_handle,
                padded_transform,
                [intermediate_height + 4, intermediate_width + 4],
                algorithm,
            )?;
            let warped_at = Instant::now();
            let band = (self.raster_band)(custom.handle, 1);
            if band.is_null() {
                return Err("GDAL warped dataset has no first band".into());
            }
            let values = self.read_typed_window(
                band,
                ReadSpec {
                    width,
                    height,
                    data_type: GDT_FLOAT64,
                    algorithm: read_algorithm,
                    window: [
                        padding,
                        padding,
                        intermediate_width as f64,
                        intermediate_height as f64,
                    ],
                },
            )?;
            let values_at = Instant::now();

            let alpha_index = (self.raster_count)(custom.handle);
            let mask_band = (self.raster_band)(custom.handle, alpha_index);
            let mut validity = vec![255.0; width * height];
            if !mask_band.is_null() {
                validity = self.read_typed_window(
                    mask_band,
                    ReadSpec {
                        width,
                        height,
                        data_type: GDT_BYTE,
                        algorithm: GRIORA_NEAREST,
                        window: [
                            padding,
                            padding,
                            intermediate_width as f64,
                            intermediate_height as f64,
                        ],
                    },
                )?;
            }
            let validity_at = Instant::now();
            let mut has_nodata = 0;
            let nodata = (self.nodata)((self.raster_band)(source_handle, 1), &mut has_nodata);
            let mask = values
                .iter()
                .zip(validity)
                .map(|(&value, valid)| {
                    valid == 0.0 || (has_nodata != 0 && value == nodata) || !value.is_finite()
                })
                .collect();
            let raster = Raster {
                data_type: (self.band_data_type)(band),
                width,
                height,
                values,
                mask,
            };
            if self.profile_reads {
                eprintln!(
                    "raster_profile open_us={} suggested_us={} warp_us={} values_us={} validity_us={} finish_us={}",
                    (opened - started).as_micros(),
                    (suggested - opened).as_micros(),
                    (warped_at - suggested).as_micros(),
                    (values_at - warped_at).as_micros(),
                    (validity_at - values_at).as_micros(),
                    validity_at.elapsed().as_micros(),
                );
            }
            Ok(raster)
        }
    }

    unsafe fn read_typed_window(&self, band: BandHandle, mut spec: ReadSpec) -> Result<Vec<f64>> {
        // GDAL resampling must write the source dtype before widening the
        // transfer buffer because Float64 output changes integer and Float32 rounding.
        let dtype = unsafe { (self.band_data_type)(band) };
        macro_rules! read {
            ($ty:ty) => {{
                spec.data_type = <$ty as GdalPixel>::DATA_TYPE;
                let mut values = vec![0 as $ty; spec.width * spec.height];
                unsafe {
                    read_window(self.raster_io_ex, band, &mut values, spec)?;
                }
                Ok(values.into_iter().map(|v| v as f64).collect())
            }};
        }
        match dtype {
            1 => read!(u8),
            2 => read!(u16),
            3 => read!(i16),
            4 => read!(u32),
            5 => read!(i32),
            6 => read!(f32),
            7 => read!(f64),
            12 => read!(u64),
            13 => read!(i64),
            14 => read!(i8),
            _ => read!(f64),
        }
    }

    fn create_warped_dataset(
        &self,
        source: DatasetHandle,
        bounds: [f64; 4],
        size: [usize; 2],
        resampling: &str,
    ) -> Result<VsiDatasetGuard> {
        self.create_grid_warp(source, bounds, size, resampling, &self.target_crs, false)
    }

    fn create_grid_warp(
        &self,
        source: DatasetHandle,
        bounds: [f64; 4],
        size: [usize; 2],
        resampling: &str,
        target_crs: &str,
        native_values: bool,
    ) -> Result<VsiDatasetGuard> {
        let destination = CString::new(format!(
            "/vsimem/rcfp-warp-{}-{}.vrt",
            std::process::id(),
            VRT_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ))
        .expect("generated path");
        let mut raw_arguments = vec![
            "-of".to_owned(),
            "VRT".to_owned(),
            "-t_srs".to_owned(),
            target_crs.to_owned(),
            // Rasterio's WarpedVRT transformer does not insert CENTER_LONG.
            // GDALWarp's default otherwise changes antimeridian mask coverage.
            "-to".to_owned(),
            "INSERT_CENTER_LONG=FALSE".to_owned(),
            "-te".to_owned(),
            bounds[0].to_string(),
            bounds[1].to_string(),
            bounds[2].to_string(),
            bounds[3].to_string(),
            "-ts".to_owned(),
            size[1].to_string(),
            size[0].to_string(),
            "-r".to_owned(),
            resampling.to_owned(),
        ];
        if native_values {
            // Physical conversions are explicit scale/offset operations in native layers.
            raw_arguments.extend(["-novshift".into(), "-dstnodata".into(), "None".into()]);
        }
        let has_alpha = unsafe {
            (1..=(self.raster_count)(source))
                .any(|i| (self.color_interpretation)((self.raster_band)(source, i)) == 6)
        };
        if !has_alpha {
            raw_arguments.push("-dstalpha".into());
        } else if native_values {
            raw_arguments.extend(["-wo".into(), "SRC_ALPHA_MAX=1".into()]);
        }
        let arguments = raw_arguments
            .into_iter()
            .map(|argument| CString::new(argument).expect("generated argument"))
            .collect::<Vec<_>>();
        let mut pointers = arguments
            .iter()
            .map(|argument| argument.as_ptr().cast_mut())
            .chain(std::iter::once(ptr::null_mut()))
            .collect::<Vec<_>>();
        unsafe {
            let options = (self.warp_options_new)(pointers.as_mut_ptr(), ptr::null_mut());
            if options.is_null() {
                return Err("GDAL could not construct warp options".into());
            }
            let mut source_handles = [source];
            let mut usage_error = 0;
            let handle = (self.warp)(
                destination.as_ptr(),
                ptr::null_mut(),
                1,
                source_handles.as_mut_ptr(),
                options,
                &mut usage_error,
            );
            (self.warp_options_free)(options);
            if handle.is_null() || usage_error != 0 {
                return Err("GDAL could not create the padded warped dataset".into());
            }
            Ok(VsiDatasetGuard {
                dataset: DatasetGuard {
                    handle,
                    close: self.close,
                },
                path: destination,
                unlink: self.vsi_unlink,
            })
        }
    }

    fn open_dataset(&self, path: &Path) -> Result<DatasetGuard> {
        self.open_dataset_options(path, None)
    }

    fn open_dataset_options(&self, path: &Path, overview: Option<i32>) -> Result<DatasetGuard> {
        // Probe known local auxiliary filenames without listing directories.
        // Remote COGs keep the no-sibling-probes default.
        let explicit_directory_policy = crate::settings::var("TC_GDAL_OPTIONS")
            .ok()
            .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
            .is_some_and(|options| options.get("GDAL_DISABLE_READDIR_ON_OPEN").is_some());
        let directory = if !crate::execution::is_remote(path)
            && !explicit_directory_policy
            && !crate::compatibility::CompatibilityProfile::current().is_terracotta()
        {
            unsafe {
                let get: unsafe extern "C" fn(*const c_char, *const c_char) -> *const c_char =
                    self.symbol(b"CPLGetThreadLocalConfigOption\0")?;
                let set: unsafe extern "C" fn(*const c_char, *const c_char) =
                    self.symbol(b"CPLSetThreadLocalConfigOption\0")?;
                let previous = get(c"GDAL_DISABLE_READDIR_ON_OPEN".as_ptr(), ptr::null());
                let previous = if previous.is_null() {
                    None
                } else {
                    Some(std::ffi::CStr::from_ptr(previous).to_owned())
                };
                set(c"GDAL_DISABLE_READDIR_ON_OPEN".as_ptr(), c"TRUE".as_ptr());
                Some(DirectoryPolicy { previous, set })
            }
        } else {
            None
        };
        let original = path.to_string_lossy();
        let path_string = if original.starts_with("http://") || original.starts_with("https://") {
            format!("/vsicurl/{original}")
        } else if let Some(remainder) = original.strip_prefix("s3://") {
            format!("/vsis3/{remainder}")
        } else {
            original.into_owned()
        };
        let c_path = CString::new(path_string.as_bytes())
            .map_err(|_| "raster path contains a NUL byte".to_string())?;
        let handle = unsafe {
            if let Some(level) = overview {
                let open: unsafe extern "C" fn(
                    *const c_char,
                    u32,
                    *const *const c_char,
                    *const *const c_char,
                    *const *const c_char,
                ) -> DatasetHandle = self.symbol(b"GDALOpenEx\0")?;
                let option =
                    CString::new(format!("OVERVIEW_LEVEL={level}")).expect("overview option");
                let options = [option.as_ptr(), ptr::null()];
                open(
                    c_path.as_ptr(),
                    2,
                    ptr::null(),
                    options.as_ptr(),
                    ptr::null(),
                )
            } else {
                (self.open)(c_path.as_ptr(), GA_READ_ONLY)
            }
        };
        if handle.is_null() {
            return Err("error while reading raster file".into());
        }
        let dataset = DatasetGuard {
            handle,
            close: self.close,
        };
        if directory.is_some() {
            unsafe {
                let mask: unsafe extern "C" fn(BandHandle) -> BandHandle =
                    self.symbol(b"GDALGetMaskBand\0")?;
                for index in 1..=(self.raster_count)(handle) {
                    let band = (self.raster_band)(handle, index);
                    mask(band);
                    (self.overview_count)(band);
                }
            }
        }
        Ok(dataset)
    }
}

struct DirectoryPolicy {
    previous: Option<CString>,
    set: unsafe extern "C" fn(*const c_char, *const c_char),
}
impl Drop for DirectoryPolicy {
    fn drop(&mut self) {
        unsafe {
            (self.set)(
                c"GDAL_DISABLE_READDIR_ON_OPEN".as_ptr(),
                self.previous
                    .as_ref()
                    .map_or(ptr::null(), |value| value.as_ptr()),
            );
        }
    }
}

struct DatasetGuard {
    handle: DatasetHandle,
    close: unsafe extern "C" fn(DatasetHandle),
}
struct SourceLease {
    _opened: Option<DatasetGuard>,
    handle: DatasetHandle,
    stamp: Option<(u64, SystemTime)>,
}
#[derive(Clone, Copy)]
struct SuggestedGeometry {
    transform: [f64; 6],
}

struct RemoteSource {
    revision: Option<[u8; 32]>,
    path: PathBuf,
    native: RefCell<plan::NativeSource>,
    dataset: DatasetGuard,
    opened: Instant,
}

struct SourceEntry {
    revision: Option<[u8; 32]>,
    generation: Vec<(u64, u128)>,
    path: PathBuf,
    stamp: (u64, std::time::SystemTime),
    suggested: [Option<SuggestedGeometry>; 2],
    native: RefCell<plan::NativeSource>,
    dataset: DatasetGuard,
}

impl Drop for DatasetGuard {
    fn drop(&mut self) {
        unsafe { (self.close)(self.handle) }
    }
}

struct VsiDatasetGuard {
    dataset: DatasetGuard,
    path: CString,
    unlink: unsafe extern "C" fn(*const c_char) -> c_int,
}
impl std::ops::Deref for VsiDatasetGuard {
    type Target = DatasetGuard;
    fn deref(&self) -> &Self::Target {
        &self.dataset
    }
}
impl Drop for VsiDatasetGuard {
    fn drop(&mut self) {
        // /vsimem keeps the already-open VRT alive until GDALClose.
        unsafe {
            (self.unlink)(self.path.as_ptr());
        }
    }
}

struct SpatialReference {
    handle: *mut c_void,
    destroy: unsafe extern "C" fn(*mut c_void),
}
impl Drop for SpatialReference {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe {
                (self.destroy)(self.handle);
            }
        }
    }
}

struct ReadSpec {
    width: usize,
    height: usize,
    data_type: c_int,
    algorithm: c_int,
    window: [f64; 4],
}

trait GdalPixel {
    const DATA_TYPE: c_int;
}
impl GdalPixel for u8 {
    const DATA_TYPE: c_int = GDT_BYTE;
}
impl GdalPixel for u16 {
    const DATA_TYPE: c_int = 2;
}
impl GdalPixel for f64 {
    const DATA_TYPE: c_int = GDT_FLOAT64;
}
impl GdalPixel for i16 {
    const DATA_TYPE: c_int = 3;
}
impl GdalPixel for u32 {
    const DATA_TYPE: c_int = 4;
}
impl GdalPixel for i32 {
    const DATA_TYPE: c_int = 5;
}
impl GdalPixel for f32 {
    const DATA_TYPE: c_int = 6;
}
impl GdalPixel for u64 {
    const DATA_TYPE: c_int = 12;
}
impl GdalPixel for i64 {
    const DATA_TYPE: c_int = 13;
}
impl GdalPixel for i8 {
    const DATA_TYPE: c_int = 14;
}

unsafe fn read_window<T: GdalPixel>(
    function: unsafe extern "C" fn(
        BandHandle,
        c_int,
        c_int,
        c_int,
        c_int,
        c_int,
        *mut c_void,
        c_int,
        c_int,
        c_int,
        isize,
        isize,
        *mut RasterIoExtraArg,
    ) -> c_int,
    band: BandHandle,
    output: &mut [T],
    spec: ReadSpec,
) -> Result<()> {
    if band.is_null()
        || spec.width == 0
        || spec.height == 0
        || spec.data_type != T::DATA_TYPE
        || spec.width.checked_mul(spec.height) != Some(output.len())
        || c_int::try_from(spec.width).is_err()
        || c_int::try_from(spec.height).is_err()
        || spec.window.iter().any(|value| !value.is_finite())
    {
        return Err("invalid GDAL raster buffer or dimensions".into());
    }
    let [x_offset, y_offset, x_size, y_size] = spec.window;
    let mut extra = RasterIoExtraArg {
        version: 1,
        resample_algorithm: spec.algorithm,
        progress: ptr::null_mut(),
        progress_data: ptr::null_mut(),
        floating_window_valid: 1,
        x_offset,
        y_offset,
        x_size,
        y_size,
    };
    let error = unsafe {
        function(
            band,
            GF_READ,
            x_offset.floor() as c_int,
            y_offset.floor() as c_int,
            x_size.round().max(1.0) as c_int,
            y_size.round().max(1.0) as c_int,
            output.as_mut_ptr().cast(),
            spec.width as c_int,
            spec.height as c_int,
            spec.data_type,
            0,
            0,
            &mut extra,
        )
    };
    if error != 0 {
        return Err(format!("GDAL raster read failed with error {error}"));
    }
    Ok(())
}

pub(crate) fn find_gdal_library() -> Result<PathBuf> {
    if let Some(path) = crate::settings::var_os("TERRACOTTA_GDAL_LIBRARY") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!(
            "TERRACOTTA_GDAL_LIBRARY does not exist: {}",
            path.display()
        ));
    }
    let roots = [PathBuf::from("/usr/lib"), PathBuf::from("/usr/local/lib")];
    for root in roots {
        if let Some(found) = find_named_library(&root, 5) {
            return Ok(found);
        }
    }
    Err("could not find libgdal; set TERRACOTTA_GDAL_LIBRARY to the GDAL shared library".into())
}

fn find_named_library(root: &Path, depth: usize) -> Option<PathBuf> {
    if depth == 0 {
        return None;
    }
    for entry in std::fs::read_dir(root).ok()?.flatten() {
        let path = entry.path();
        if path.is_file()
            && path.file_name()?.to_string_lossy().starts_with("libgdal")
            && path.to_string_lossy().contains(".so")
        {
            return Some(path);
        }
        if path.is_dir() {
            if let Some(found) = find_named_library(&path, depth - 1) {
                return Some(found);
            }
        }
    }
    None
}

fn symbol_error(error: libloading::Error) -> String {
    format!("GDAL runtime is missing a required C API symbol: {error}")
}

pub fn tile_exists(bounds: [f64; 4], x: f64, y: f64, z: u32) -> bool {
    if z > 1023 {
        return false;
    }
    let northwest = lon_lat_tile(bounds[0], bounds[3], z);
    let southeast = lon_lat_tile(bounds[2], bounds[1], z);
    northwest.0 <= x && x <= southeast.0 && northwest.1 <= y && y <= southeast.1
}

fn lon_lat_tile(longitude: f64, latitude: f64, zoom: u32) -> (f64, f64) {
    let count = 2.0f64.powi(zoom as i32);
    let x = ((longitude + 180.0) / 360.0 * count)
        .floor()
        .clamp(0.0, count - 1.0);
    let latitude = latitude
        .clamp(-85.0511287798066, 85.0511287798066)
        .to_radians();
    let y = ((1.0 - latitude.tan().asinh() / std::f64::consts::PI) / 2.0 * count)
        .floor()
        .clamp(0.0, count - 1.0);
    (x, y)
}

pub fn mercator_tile_bounds(x: f64, y: f64, z: u32) -> [f64; 4] {
    let half = std::f64::consts::PI * 6_378_137.0;
    let count = 2.0f64.powi(z as i32);
    let span = 2.0 * half / count;
    [
        -half + x * span,
        half - (y + 1.0) * span,
        -half + (x + 1.0) * span,
        half - y * span,
    ]
}

pub fn wgs84_bounds_to_mercator(bounds: [f64; 4]) -> [f64; 4] {
    fn x(longitude: f64) -> f64 {
        6_378_137.0 * longitude.to_radians()
    }
    fn y(latitude: f64) -> f64 {
        let latitude = latitude.clamp(-85.0511287798066, 85.0511287798066);
        6_378_137.0
            * (std::f64::consts::FRAC_PI_4 + latitude.to_radians() / 2.0)
                .tan()
                .ln()
    }
    [x(bounds[0]), y(bounds[1]), x(bounds[2]), y(bounds[3])]
}

fn resampling_method(name: &str) -> Result<(c_int, &'static str)> {
    match name {
        "nearest" => Ok((0, "near")),
        "linear" => Ok((1, "bilinear")),
        "cubic" => Ok((2, "cubic")),
        "average" => Ok((5, "average")),
        _ => Err(format!("unknown resampling method {name}")),
    }
}

/// GDAL options are scoped to the owning thread and restored on drop.
struct ThreadConfig {
    set: unsafe extern "C" fn(*const c_char, *const c_char),
    previous: Vec<(CString, Option<CString>)>,
}
impl ThreadConfig {
    unsafe fn new(library: &Library, library_path: &Path) -> Result<Self> {
        let set = unsafe {
            *library
                .get::<unsafe extern "C" fn(*const c_char, *const c_char)>(
                    b"CPLSetThreadLocalConfigOption\0",
                )
                .map_err(symbol_error)?
        };
        let get = unsafe {
            *library
                .get::<unsafe extern "C" fn(*const c_char, *const c_char) -> *const c_char>(
                    b"CPLGetThreadLocalConfigOption\0",
                )
                .map_err(symbol_error)?
        };
        let mut options = std::collections::BTreeMap::new();
        if crate::settings::var("CURL_CA_BUNDLE").is_err() {
            if let Ok(certificates) = crate::settings::var("SSL_CERT_FILE") {
                options.insert("CURL_CA_BUNDLE".into(), Some(certificates));
            } else {
                let certificates = library_path
                    .parent()
                    .and_then(Path::parent)
                    .map(|root| root.join("share/cacert.pem"));
                if let Some(certificates) = certificates.filter(|path| path.is_file()) {
                    options.insert(
                        "CURL_CA_BUNDLE".into(),
                        Some(certificates.to_string_lossy().into_owned()),
                    );
                }
            }
        }
        for (source, target) in [
            ("TC_RASTER_AWS_ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
            ("TC_RASTER_AWS_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"),
            ("TC_RASTER_AWS_SESSION_TOKEN", "AWS_SESSION_TOKEN"),
            ("TC_RASTER_REMOTE_TIMEOUT_SECONDS", "GDAL_HTTP_TIMEOUT"),
        ] {
            options.insert(target.to_string(), crate::settings::var(source).ok());
        }
        options.insert(
            "GDAL_DISABLE_READDIR_ON_OPEN".into(),
            Some("EMPTY_DIR".into()),
        );
        let endpoint = crate::settings::var("TC_RASTER_AWS_S3_ENDPOINT").ok();
        options.insert(
            "AWS_S3_ENDPOINT".into(),
            endpoint.as_ref().map(|e| {
                e.trim_start_matches("http://")
                    .trim_start_matches("https://")
                    .to_string()
            }),
        );
        options.insert("AWS_HTTPS".into(), endpoint.as_ref().map(|_| "NO".into()));
        options.insert(
            "AWS_VIRTUAL_HOSTING".into(),
            endpoint.as_ref().map(|_| "FALSE".into()),
        );
        if let Ok(raw) = crate::settings::var("TC_GDAL_OPTIONS") {
            let extra: serde_json::Map<String, serde_json::Value> =
                serde_json::from_str(&raw).map_err(|e| e.to_string())?;
            for (key, value) in extra {
                options.insert(
                    key,
                    if value.is_null() {
                        None
                    } else {
                        Some(
                            value
                                .as_str()
                                .map(str::to_string)
                                .unwrap_or_else(|| value.to_string()),
                        )
                    },
                );
            }
        }
        let mut guard = Self {
            set,
            previous: Vec::new(),
        };
        for (key, value) in options {
            let key = CString::new(key).map_err(|_| "GDAL option contains NUL")?;
            let old = unsafe { get(key.as_ptr(), ptr::null()) };
            let old = if old.is_null() {
                None
            } else {
                Some(unsafe { std::ffi::CStr::from_ptr(old) }.to_owned())
            };
            let value = value
                .map(CString::new)
                .transpose()
                .map_err(|_| "GDAL value contains NUL")?;
            unsafe {
                set(
                    key.as_ptr(),
                    value.as_ref().map_or(ptr::null(), |s| s.as_ptr()),
                );
            }
            guard.previous.push((key, old));
        }
        Ok(guard)
    }
}
impl Drop for ThreadConfig {
    fn drop(&mut self) {
        for (key, value) in self.previous.iter().rev() {
            unsafe {
                (self.set)(
                    key.as_ptr(),
                    value.as_ref().map_or(ptr::null(), |s| s.as_ptr()),
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matches_known_venice_tile() {
        let bounds = [
            12.254818102684911,
            45.34255438818542,
            12.52724757518983,
            45.53434456898156,
        ];
        assert!(tile_exists(bounds, 2188.0, 1466.0, 12));
        assert!(tile_exists(bounds, 2189.0, 1466.0, 12));
        assert!(!tile_exists(bounds, 0.0, 0.0, 20));
    }
    #[test]
    fn web_mercator_world_is_symmetric() {
        let bounds = mercator_tile_bounds(0.0, 0.0, 0);
        assert!((bounds[0] + bounds[2]).abs() < 1e-8);
        assert!((bounds[1] + bounds[3]).abs() < 1e-8);
    }
}

use crate::Job;
use libloading::Library;
use rcfp_core::raster::{Gdal, Raster, ReadRequest};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::ffi::{CString, c_char, c_int, c_void};
use std::ptr;
use std::sync::OnceLock;

type Handle = *mut c_void;
pub struct Api {
    _library: Library,
    open: unsafe extern "C" fn(*const c_char, c_int) -> Handle,
    close: unsafe extern "C" fn(Handle),
    band: unsafe extern "C" fn(Handle, c_int) -> Handle,
    width: unsafe extern "C" fn(Handle) -> c_int,
    height: unsafe extern "C" fn(Handle) -> c_int,
    count: unsafe extern "C" fn(Handle) -> c_int,
    band_width: unsafe extern "C" fn(Handle) -> c_int,
    band_height: unsafe extern "C" fn(Handle) -> c_int,
    data_type: unsafe extern "C" fn(Handle) -> c_int,
    overview: unsafe extern "C" fn(Handle, c_int) -> Handle,
    overview_count: unsafe extern "C" fn(Handle) -> c_int,
    mask: unsafe extern "C" fn(Handle) -> Handle,
    create_mask: unsafe extern "C" fn(Handle, c_int) -> c_int,
    io: unsafe extern "C" fn(
        Handle,
        c_int,
        c_int,
        c_int,
        c_int,
        c_int,
        Handle,
        c_int,
        c_int,
        c_int,
        c_int,
        c_int,
    ) -> c_int,
    transform: unsafe extern "C" fn(Handle, *mut f64) -> c_int,
    driver: unsafe extern "C" fn(*const c_char) -> Handle,
    create: unsafe extern "C" fn(
        Handle,
        *const c_char,
        c_int,
        c_int,
        c_int,
        c_int,
        *const *const c_char,
    ) -> Handle,
    set_transform: unsafe extern "C" fn(Handle, *mut f64) -> c_int,
    set_projection: unsafe extern "C" fn(Handle, *const c_char) -> c_int,
    set_nodata: unsafe extern "C" fn(Handle, f64) -> c_int,
    options: unsafe extern "C" fn(*mut *mut c_char, Handle) -> Handle,
    options_free: unsafe extern "C" fn(Handle),
    warp: unsafe extern "C" fn(
        *const c_char,
        Handle,
        c_int,
        *mut Handle,
        Handle,
        *mut c_int,
    ) -> Handle,
    srs_create: unsafe extern "C" fn(*const c_char) -> Handle,
    srs_input: unsafe extern "C" fn(Handle, *const c_char) -> c_int,
    srs_export: unsafe extern "C" fn(Handle, *mut *mut c_char) -> c_int,
    srs_destroy: unsafe extern "C" fn(Handle),
    free: unsafe extern "C" fn(Handle),
}

static API: OnceLock<Result<Api, String>> = OnceLock::new();
pub fn api() -> Result<&'static Api, String> {
    API.get_or_init(|| unsafe {
        let path = std::env::var("TERRACOTTA_GDAL_LIBRARY")
            .map_err(|_| "TERRACOTTA_GDAL_LIBRARY is required")?;
        let library = Library::new(path).map_err(|e| e.to_string())?;
        macro_rules! symbol {
            ($name:literal) => {
                *library
                    .get(concat!($name, "\0").as_bytes())
                    .map_err(|e| e.to_string())?
            };
        }
        let register: unsafe extern "C" fn() = symbol!("GDALAllRegister");
        register();
        Ok(Api {
            open: symbol!("GDALOpen"),
            close: symbol!("GDALClose"),
            band: symbol!("GDALGetRasterBand"),
            width: symbol!("GDALGetRasterXSize"),
            height: symbol!("GDALGetRasterYSize"),
            count: symbol!("GDALGetRasterCount"),
            band_width: symbol!("GDALGetRasterBandXSize"),
            band_height: symbol!("GDALGetRasterBandYSize"),
            data_type: symbol!("GDALGetRasterDataType"),
            overview: symbol!("GDALGetOverview"),
            overview_count: symbol!("GDALGetOverviewCount"),
            mask: symbol!("GDALGetMaskBand"),
            create_mask: symbol!("GDALCreateDatasetMaskBand"),
            io: symbol!("GDALRasterIO"),
            transform: symbol!("GDALGetGeoTransform"),
            driver: symbol!("GDALGetDriverByName"),
            create: symbol!("GDALCreate"),
            set_transform: symbol!("GDALSetGeoTransform"),
            set_projection: symbol!("GDALSetProjection"),
            set_nodata: symbol!("GDALSetRasterNoDataValue"),
            options: symbol!("GDALWarpAppOptionsNew"),
            options_free: symbol!("GDALWarpAppOptionsFree"),
            warp: symbol!("GDALWarp"),
            srs_create: symbol!("OSRNewSpatialReference"),
            srs_input: symbol!("OSRSetFromUserInput"),
            srs_export: symbol!("OSRExportToWkt"),
            srs_destroy: symbol!("OSRDestroySpatialReference"),
            free: symbol!("VSIFree"),
            _library: library,
        })
    })
    .as_ref()
    .map_err(Clone::clone)
}

struct Dataset {
    handle: Handle,
    api: &'static Api,
}
impl Drop for Dataset {
    fn drop(&mut self) {
        unsafe {
            (self.api.close)(self.handle);
        }
    }
}

impl Api {
    fn open_dataset(&'static self, source: &str) -> Result<Dataset, String> {
        let source = if source.starts_with("http") {
            format!("/vsicurl/{source}")
        } else {
            source.into()
        };
        let source = CString::new(source).map_err(|e| e.to_string())?;
        let handle = unsafe { (self.open)(source.as_ptr(), 0) };
        if handle.is_null() {
            return Err("GDAL could not open source".into());
        }
        Ok(Dataset { handle, api: self })
    }

    fn read_band(&self, band: Handle, window: [usize; 4]) -> Result<Raster, String> {
        let [x, y, width, height] = window;
        let mut values = vec![0.; width * height];
        let mut mask = vec![0_u8; values.len()];
        unsafe {
            if (self.io)(
                band,
                0,
                x as c_int,
                y as c_int,
                width as c_int,
                height as c_int,
                values.as_mut_ptr().cast(),
                width as c_int,
                height as c_int,
                7,
                0,
                0,
            ) != 0
            {
                return Err("GDAL read failed".into());
            }
            let mask_band = (self.mask)(band);
            if mask_band.is_null()
                || (self.io)(
                    mask_band,
                    0,
                    x as c_int,
                    y as c_int,
                    width as c_int,
                    height as c_int,
                    mask.as_mut_ptr().cast(),
                    width as c_int,
                    height as c_int,
                    1,
                    0,
                    0,
                ) != 0
            {
                return Err("GDAL mask read failed".into());
            }
            Ok(Raster {
                width,
                height,
                values,
                mask: mask.into_iter().map(|m| m == 0).collect(),
                data_type: (self.data_type)(band),
            })
        }
    }

    pub fn warp_memory(
        &'static self,
        rasters: &[Raster],
        mut gt: [f64; 6],
        epsg: u16,
        nodata: Option<f64>,
        job: &Job,
    ) -> Result<Vec<Raster>, String> {
        let first = rasters.first().ok_or("empty source window")?;
        unsafe {
            let handle = (self.create)(
                (self.driver)(c"MEM".as_ptr()),
                c"".as_ptr(),
                first.width as c_int,
                first.height as c_int,
                rasters.len() as c_int,
                first.data_type,
                ptr::null(),
            );
            if handle.is_null() {
                return Err("GDAL MEM allocation failed".into());
            }
            let source = Dataset { handle, api: self };
            let srs = (self.srs_create)(ptr::null());
            if srs.is_null() {
                return Err("GDAL CRS allocation failed".into());
            }
            let crs = CString::new(format!("EPSG:{epsg}")).unwrap();
            let mut wkt = ptr::null_mut();
            let success =
                (self.srs_input)(srs, crs.as_ptr()) == 0 && (self.srs_export)(srs, &mut wkt) == 0;
            (self.srs_destroy)(srs);
            if !success {
                return Err("GDAL CRS setup failed".into());
            }
            let status = (self.set_projection)(handle, wkt);
            (self.free)(wkt.cast());
            if status != 0 || (self.set_transform)(handle, gt.as_mut_ptr()) != 0 {
                return Err("GDAL MEM geometry setup failed".into());
            }
            for (index, raster) in rasters.iter().enumerate() {
                let band = (self.band)(handle, index as c_int + 1);
                let mut values: Vec<_> = raster
                    .values
                    .iter()
                    .zip(&raster.mask)
                    .map(|(&v, &m)| if m { nodata.unwrap_or(0.) } else { v })
                    .collect();
                if nodata.is_some_and(|value| (self.set_nodata)(band, value) != 0)
                    || (self.io)(
                        band,
                        1,
                        0,
                        0,
                        first.width as c_int,
                        first.height as c_int,
                        values.as_mut_ptr().cast(),
                        first.width as c_int,
                        first.height as c_int,
                        7,
                        0,
                        0,
                    ) != 0
                {
                    return Err("GDAL MEM data setup failed".into());
                }
            }
            if nodata.is_none() {
                if (self.create_mask)(handle, 2) != 0 {
                    return Err("GDAL MEM mask allocation failed".into());
                }
                let mut validity: Vec<u8> = (0..first.values.len())
                    .map(|i| {
                        if rasters.iter().all(|r| r.mask[i]) {
                            0
                        } else {
                            255
                        }
                    })
                    .collect();
                let mask = (self.mask)((self.band)(handle, 1));
                if mask.is_null()
                    || (self.io)(
                        mask,
                        1,
                        0,
                        0,
                        first.width as c_int,
                        first.height as c_int,
                        validity.as_mut_ptr().cast(),
                        first.width as c_int,
                        first.height as c_int,
                        1,
                        0,
                        0,
                    ) != 0
                {
                    return Err("GDAL MEM mask setup failed".into());
                }
            }
            let bounds = job.bounds.ok_or("missing target bounds")?;
            let mut args = vec![
                "-of".into(),
                "MEM".into(),
                "-t_srs".into(),
                "EPSG:3857".into(),
                "-te".into(),
            ];
            args.extend(bounds.iter().map(ToString::to_string));
            args.extend([
                "-ts".into(),
                job.size[1].to_string(),
                job.size[0].to_string(),
                "-r".into(),
                job.resampling.clone(),
                "-dstalpha".into(),
                "-wo".into(),
                "UNIFIED_SRC_NODATA=YES".into(),
            ]);
            let strings: Vec<_> = args
                .iter()
                .map(|s| CString::new(s.as_str()).unwrap())
                .collect();
            let mut pointers: Vec<_> = strings.iter().map(|s| s.as_ptr().cast_mut()).collect();
            pointers.push(ptr::null_mut());
            let options = (self.options)(pointers.as_mut_ptr(), ptr::null_mut());
            if options.is_null() {
                return Err("GDAL warp options failed".into());
            }
            let mut source_handle = source.handle;
            let mut usage = 0;
            let output = (self.warp)(
                c"".as_ptr(),
                ptr::null_mut(),
                1,
                &mut source_handle,
                options,
                &mut usage,
            );
            (self.options_free)(options);
            if output.is_null() {
                return Err("GDAL memory warp failed".into());
            }
            let output = Dataset {
                handle: output,
                api: self,
            };
            let alpha = self.read_band(
                (self.band)(output.handle, rasters.len() as c_int + 1),
                [0, 0, job.size[1], job.size[0]],
            )?;
            let mut result = Vec::new();
            for index in 0..rasters.len() {
                let mut raster = self.read_band(
                    (self.band)(output.handle, index as c_int + 1),
                    [0, 0, job.size[1], job.size[0]],
                )?;
                for (mask, alpha) in raster.mask.iter_mut().zip(&alpha.values) {
                    *mask |= *alpha <= 0.;
                }
                result.push(raster);
            }
            Ok(result)
        }
    }
}

pub struct Worker {
    datasets: HashMap<String, Dataset>,
    readers: HashMap<String, Gdal>,
}

impl Worker {
    pub fn new() -> Self {
        Self {
            datasets: HashMap::new(),
            readers: HashMap::new(),
        }
    }
    pub fn run(&mut self, job: &Job) -> Result<(Option<Value>, Vec<Raster>), String> {
        if job.mode == "tile" {
            if !self.readers.contains_key(&job.resampling) {
                let method = if job.resampling == "bilinear" {
                    "linear"
                } else {
                    "nearest"
                };
                let mut settings = serde_json::Map::new();
                settings.insert("REPROJECTION_METHOD".into(), json!(method));
                settings.insert("ASYNC_TIFF".into(), json!(false));
                let reader = rcfp_core::settings::scope(settings, Gdal::load)?;
                self.readers.insert(job.resampling.clone(), reader);
            }
            let requests: Vec<_> = job
                .bands
                .iter()
                .map(|&band| ReadRequest {
                    band,
                    bounds_mercator: job.bounds,
                    size: job.size,
                    preserve_values: job.resampling == "nearest",
                    ..Default::default()
                })
                .collect();
            return self.readers[&job.resampling]
                .read_bands(std::path::Path::new(&job.source), &requests)
                .map(|rasters| (None, rasters));
        }
        let api = api()?;
        if !self.datasets.contains_key(&job.source) {
            self.datasets
                .insert(job.source.clone(), api.open_dataset(&job.source)?);
        }
        let dataset = &self.datasets[&job.source];
        unsafe {
            if job.mode == "metadata" {
                let mut gt = [0.; 6];
                let first = (api.band)(dataset.handle, 1);
                let overview_count = (api.overview_count)(first);
                let geometry =
                    ((api.transform)(dataset.handle, gt.as_mut_ptr()) == 0).then_some(gt);
                return Ok((
                    Some(
                        json!({"width": (api.width)(dataset.handle), "height": (api.height)(dataset.handle), "bands": (api.count)(dataset.handle), "transform": geometry,
                    "overview_shapes": (0..overview_count).map(|i| { let b = (api.overview)(first, i); [(api.band_width)(b), (api.band_height)(b)] }).collect::<Vec<_>>() }),
                    ),
                    vec![],
                ));
            }
            let mut rasters = Vec::new();
            for &index in &job.bands {
                let base = (api.band)(dataset.handle, index as c_int);
                if base.is_null() {
                    return Err("invalid GDAL band".into());
                }
                let band = if job.overview == 0 {
                    base
                } else {
                    (api.overview)(base, job.overview as c_int - 1)
                };
                if band.is_null() {
                    return Err("missing GDAL overview".into());
                }
                let window = job.window.ok_or("missing window")?;
                if window[0] + window[2] > (api.band_width)(band) as usize
                    || window[1] + window[3] > (api.band_height)(band) as usize
                {
                    return Err("window exceeds GDAL band".into());
                }
                rasters.push(api.read_band(band, window)?);
            }
            Ok((None, rasters))
        }
    }
}

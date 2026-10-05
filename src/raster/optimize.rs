//! Two-pass GTiff preparation: first-band data + dataset mask, internal
//! overviews, then compressed CreateCopy with IFDs before imagery.
use super::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct OptimizeOptions {
    pub reproject: bool,
    pub web_mercator_grid: bool,
    pub all_bands: bool,
    pub block_size: usize,
    pub resampling_method: String,
    pub in_memory: Option<bool>,
    pub compression: String,
}
impl Default for OptimizeOptions {
    fn default() -> Self {
        Self {
            reproject: false,
            web_mercator_grid: false,
            all_bands: false,
            block_size: 256,
            resampling_method: "average".into(),
            in_memory: None,
            compression: "auto".into(),
        }
    }
}
struct TempPath<'a> {
    gdal: &'a Gdal,
    path: CString,
}
impl Drop for TempPath<'_> {
    fn drop(&mut self) {
        unsafe {
            (self.gdal.vsi_unlink)(self.path.as_ptr());
        }
    }
}
impl Gdal {
    pub fn optimize(&self, input: &Path, output: &Path, options: &OptimizeOptions) -> Result<()> {
        if options.all_bands && !options.web_mercator_grid {
            return Err("all-band preparation requires a Web Mercator grid".into());
        }
        if ![256, 512, 1024].contains(&options.block_size) {
            return Err("block size must be 256, 512 or 1024".into());
        }
        if options.block_size != 256 && !options.web_mercator_grid {
            return Err("custom block size requires a Web Mercator grid".into());
        }
        if options.reproject && options.web_mercator_grid {
            return Err("reproject and Web Mercator grid options are mutually exclusive".into());
        }
        let method = match options.resampling_method.as_str() {
            "nearest" => 0,
            "bilinear" => 1,
            "cubic" => 2,
            "average" => 5,
            _ => return Err("invalid resampling method".into()),
        };
        let compression = match options.compression.to_ascii_lowercase().as_str() {
            "auto" => unsafe {
                let driver: unsafe extern "C" fn(*const c_char) -> *mut c_void =
                    self.symbol(b"GDALGetDriverByName\0")?;
                let metadata: unsafe extern "C" fn(
                    *mut c_void,
                    *const c_char,
                    *const c_char,
                ) -> *const c_char = self.symbol(b"GDALGetMetadataItem\0")?;
                let value = metadata(
                    driver(c"GTiff".as_ptr()),
                    c"DMD_CREATIONOPTIONLIST".as_ptr(),
                    ptr::null(),
                );
                if !value.is_null()
                    && std::ffi::CStr::from_ptr(value)
                        .to_string_lossy()
                        .contains("<Value>ZSTD</Value>")
                {
                    "ZSTD"
                } else {
                    "DEFLATE"
                }
            },
            "zstd" => "ZSTD",
            "deflate" => "DEFLATE",
            "lzw" => "LZW",
            "none" => "NONE",
            _ => return Err("invalid compression".into()),
        };
        let src = self.open_dataset(input)?;
        if options.web_mercator_grid {
            if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
                return Err("map-grid preparation requires the native profile".into());
            }
            let path = CString::new(output.to_string_lossy().as_bytes())
                .map_err(|_| "invalid output path")?;
            // A nodata-free float source needs an unambiguous invalid padding
            // value. Keep genuine zero elevations valid when aligning the grid.
            let virtual_path = TempPath {
                gdal: self,
                path: CString::new(format!(
                    "/vsimem/rcfp-grid-{}.vrt",
                    VRT_SEQUENCE.fetch_add(1, Ordering::Relaxed)
                ))
                .expect("generated path"),
            };
            let floating_source = unsafe {
                let band = (self.raster_band)(src.handle, 1);
                let mut has_nodata = 0;
                (self.nodata)(band, &mut has_nodata);
                let alpha = (1..=(self.raster_count)(src.handle))
                    .any(|i| (self.color_interpretation)((self.raster_band)(src.handle, i)) == 6);
                if [6, 7].contains(&(self.band_data_type)(band)) && has_nodata == 0 && !alpha {
                    Some(self.translate(
                        src.handle,
                        &virtual_path.path,
                        &["-of".into(), "VRT".into(), "-a_nodata".into(), "nan".into()],
                    )?)
                } else {
                    None
                }
            };
            let source = floating_source
                .as_ref()
                .map_or(src.handle, |source| source.handle);
            let mut arguments = vec![
                "-of".into(),
                "COG".into(),
                "-co".into(),
                "TILING_SCHEME=GoogleMapsCompatible".into(),
                "-co".into(),
                format!("BLOCKSIZE={}", options.block_size),
                "-co".into(),
                "ALIGNED_LEVELS=4".into(),
                "-co".into(),
                format!("COMPRESS={compression}"),
                "-co".into(),
                "PREDICTOR=YES".into(),
                "-co".into(),
                format!("RESAMPLING={}", options.resampling_method.to_uppercase()),
                "-co".into(),
                "NUM_THREADS=1".into(),
            ];
            if !options.all_bands {
                arguments.extend(["-b".into(), "1".into()]);
            }
            unsafe {
                self.translate(source, &path, &arguments)?;
            }
            return Ok(());
        }
        unsafe {
            let config: unsafe extern "C" fn(*const c_char, *const c_char) =
                self.symbol(b"CPLSetThreadLocalConfigOption\0")?;
            config(c"GDAL_TIFF_INTERNAL_MASK".as_ptr(), c"YES".as_ptr());
            config(c"GDAL_TIFF_OVR_BLOCKSIZE".as_ptr(), c"256".as_ptr());
            let mut warped = if options.reproject {
                let new: unsafe extern "C" fn() -> *mut c_void =
                    self.symbol(b"GDALCreateWarpOptions\0")?;
                let free: unsafe extern "C" fn(*mut c_void) =
                    self.symbol(b"GDALDestroyWarpOptions\0")?;
                let set: unsafe extern "C" fn(
                    *mut *mut c_char,
                    *const c_char,
                    *const c_char,
                ) -> *mut *mut c_char = self.symbol(b"CSLSetNameValue\0")?;
                let warp_options = new();
                if warp_options.is_null() {
                    return Err("cannot allocate warp options".into());
                }
                // GDALWarpOptions' first public C ABI field is char **papszWarpOptions.
                // GDAL owns allocation/destruction; AutoCreateWarpedVRT clones it.
                let list = warp_options.cast::<*mut *mut c_char>();
                *list = set(*list, c"UNIFIED_SRC_NODATA".as_ptr(), c"YES".as_ptr());
                *list = set(*list, c"INIT_DEST".as_ptr(), c"NO_DATA".as_ptr());
                let handle = (self.auto_warp)(
                    src.handle,
                    ptr::null(),
                    c"EPSG:3857".as_ptr(),
                    method,
                    0.125,
                    warp_options,
                );
                free(warp_options);
                if handle.is_null() {
                    return Err("cannot reproject raster".into());
                }
                Some(DatasetGuard {
                    handle,
                    close: self.close,
                })
            } else {
                None
            };
            let mut original_transform = [0.; 6];
            if (self.geo_transform)(src.handle, original_transform.as_mut_ptr()) != 0 {
                return Err("raster has no geotransform".into());
            }
            if options.reproject && (original_transform[2] != 0. || original_transform[4] != 0.) {
                // calculate_default_transform receives dataset.bounds, not its
                // rotated affine. Suggest on an axis-aligned proxy, then warp
                // the real source onto that explicitly selected output grid.
                let w = (self.raster_x_size)(src.handle) as f64;
                let h = (self.raster_y_size)(src.handle) as f64;
                let t = original_transform;
                let corners = [[0., 0.], [w, 0.], [w, h], [0., h]].map(|p| {
                    [
                        t[0] + p[0] * t[1] + p[1] * t[2],
                        t[3] + p[0] * t[4] + p[1] * t[5],
                    ]
                });
                let b = corners.iter().fold(
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
                );
                let proxy_path = TempPath {
                    gdal: self,
                    path: CString::new(format!(
                        "/vsimem/terracotta-proxy-{}-{}.vrt",
                        std::process::id(),
                        VRT_SEQUENCE.fetch_add(1, Ordering::Relaxed)
                    ))
                    .unwrap(),
                };
                let proxy = self.translate(
                    src.handle,
                    &proxy_path.path,
                    &[
                        "-of".into(),
                        "VRT".into(),
                        "-a_ullr".into(),
                        b[0].to_string(),
                        b[3].to_string(),
                        b[2].to_string(),
                        b[1].to_string(),
                    ],
                )?;
                let handle = (self.auto_warp)(
                    proxy.handle,
                    ptr::null(),
                    c"EPSG:3857".as_ptr(),
                    0,
                    0.125,
                    ptr::null(),
                );
                if handle.is_null() {
                    return Err("cannot suggest rotated raster grid".into());
                }
                let suggestion = DatasetGuard {
                    handle,
                    close: self.close,
                };
                let mut t = [0.; 6];
                if (self.geo_transform)(handle, t.as_mut_ptr()) != 0 {
                    return Err("cannot read suggested grid".into());
                }
                let w = (self.raster_x_size)(handle);
                let h = (self.raster_y_size)(handle);
                let args = [
                    "-of".into(),
                    "VRT".into(),
                    "-t_srs".into(),
                    "EPSG:3857".into(),
                    "-r".into(),
                    options.resampling_method.clone(),
                    "-wo".into(),
                    "UNIFIED_SRC_NODATA=YES".into(),
                    "-wo".into(),
                    "INIT_DEST=NO_DATA".into(),
                    "-te".into(),
                    t[0].to_string(),
                    (t[3] + h as f64 * t[5]).to_string(),
                    (t[0] + w as f64 * t[1]).to_string(),
                    t[3].to_string(),
                    "-ts".into(),
                    w.to_string(),
                    h.to_string(),
                ];
                let cargs = args
                    .iter()
                    .map(|s: &String| CString::new(s.as_bytes()).unwrap())
                    .collect::<Vec<_>>();
                let mut pointers = cargs
                    .iter()
                    .map(|s| s.as_ptr().cast_mut())
                    .chain(std::iter::once(ptr::null_mut()))
                    .collect::<Vec<_>>();
                let opts = (self.warp_options_new)(pointers.as_mut_ptr(), ptr::null_mut());
                if opts.is_null() {
                    return Err("cannot construct rotated warp".into());
                }
                let dest = TempPath {
                    gdal: self,
                    path: CString::new(format!(
                        "/vsimem/terracotta-rotated-{}-{}.vrt",
                        std::process::id(),
                        VRT_SEQUENCE.fetch_add(1, Ordering::Relaxed)
                    ))
                    .unwrap(),
                };
                let mut source = src.handle;
                let mut usage = 0;
                let handle = (self.warp)(
                    dest.path.as_ptr(),
                    ptr::null_mut(),
                    1,
                    &mut source,
                    opts,
                    &mut usage,
                );
                (self.warp_options_free)(opts);
                if handle.is_null() {
                    return Err("cannot create rotated warp".into());
                }
                warped = Some(DatasetGuard {
                    handle,
                    close: self.close,
                });
                drop(suggestion);
            }
            let source = warped.as_ref().unwrap_or(&src);
            let width = (self.raster_x_size)(source.handle);
            let height = (self.raster_y_size)(source.handle);
            let seq = VRT_SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let name = format!(".terracotta-{}-{seq}.tif", std::process::id());
            let temp = if options
                .in_memory
                .unwrap_or((width as u64) * (height as u64) < 10980 * 10980)
            {
                format!("/vsimem/{name}")
            } else {
                output
                    .parent()
                    .unwrap_or(Path::new("."))
                    .join(name)
                    .to_string_lossy()
                    .into_owned()
            };
            let temp = TempPath {
                gdal: self,
                path: CString::new(temp).map_err(|e| e.to_string())?,
            };
            let common = [
                "-of",
                "GTiff",
                "-co",
                "TILED=YES",
                "-co",
                "BLOCKXSIZE=256",
                "-co",
                "BLOCKYSIZE=256",
                "-co",
                "INTERLEAVE=PIXEL",
                "-co",
                "PHOTOMETRIC=MINISBLACK",
                "-co",
                "BIGTIFF=IF_SAFER",
            ];
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
            let set_nodata: unsafe extern "C" fn(BandHandle, f64) -> c_int =
                self.symbol(b"GDALSetRasterNoDataValue\0")?;
            let source_band = (self.raster_band)(source.handle, 1);
            let mut creation = [
                c"TILED=YES".as_ptr().cast_mut(),
                c"BLOCKXSIZE=256".as_ptr().cast_mut(),
                c"BLOCKYSIZE=256".as_ptr().cast_mut(),
                c"BIGTIFF=IF_SAFER".as_ptr().cast_mut(),
                ptr::null_mut(),
            ];
            let handle = create(
                driver(c"GTiff".as_ptr()),
                temp.path.as_ptr(),
                width,
                height,
                1,
                (self.band_data_type)(source_band),
                creation.as_mut_ptr(),
            );
            if handle.is_null() {
                return Err("cannot create intermediate raster".into());
            }
            let dst = DatasetGuard {
                handle,
                close: self.close,
            };
            let mut transform = [0.; 6];
            if (self.geo_transform)(source.handle, transform.as_mut_ptr()) != 0
                || set_transform(handle, transform.as_mut_ptr()) != 0
                || set_projection(handle, projection(source.handle)) != 0
            {
                return Err("cannot copy raster georeferencing".into());
            }
            let mut valid = 0;
            let nodata = (self.nodata)(source_band, &mut valid);
            let dst_band = (self.raster_band)(handle, 1);
            if valid != 0 && set_nodata(dst_band, nodata) != 0 {
                return Err("cannot copy nodata".into());
            }
            let get_mask: unsafe extern "C" fn(BandHandle) -> BandHandle =
                self.symbol(b"GDALGetMaskBand\0")?;
            let flags: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetMaskFlags\0")?;
            let create_mask: unsafe extern "C" fn(DatasetHandle, c_int) -> c_int =
                self.symbol(b"GDALCreateDatasetMaskBand\0")?;
            if create_mask(dst.handle, 2) != 0 {
                return Err("cannot create internal mask".into());
            }
            let dst_mask = get_mask((self.raster_band)(dst.handle, 1));
            let count = (self.raster_count)(source.handle);
            let band = (self.raster_band)(source.handle, 1);
            let mask_flags = flags(band);
            // Rasterio dataset_mask: dataset/alpha mask, RGBA fourth band, otherwise OR band masks.
            let alpha = (1..=count)
                .find(|&i| (self.color_interpretation)((self.raster_band)(source.handle, i)) == 6);
            for y in (0..height).step_by(256) {
                for x in (0..width).step_by(256) {
                    let w = (width - x).min(256) as usize;
                    let h = (height - y).min(256) as usize;
                    let mut data = vec![0f64; w * h];
                    read_window(
                        self.raster_io_ex,
                        source_band,
                        &mut data,
                        ReadSpec {
                            width: w,
                            height: h,
                            data_type: GDT_FLOAT64,
                            algorithm: 0,
                            window: [x as f64, y as f64, w as f64, h as f64],
                        },
                    )?;
                    if (self.raster_io_ex)(
                        dst_band,
                        1,
                        x,
                        y,
                        w as c_int,
                        h as c_int,
                        data.as_mut_ptr().cast(),
                        w as c_int,
                        h as c_int,
                        GDT_FLOAT64,
                        0,
                        0,
                        ptr::null_mut(),
                    ) != 0
                    {
                        return Err("cannot write data".into());
                    }
                    let mut mask = vec![0u8; w * h];
                    let bands = if mask_flags & 2 != 0 {
                        vec![get_mask(band)]
                    } else if let Some(i) = alpha {
                        vec![(self.raster_band)(source.handle, i)]
                    } else {
                        (1..=count)
                            .map(|i| get_mask((self.raster_band)(source.handle, i)))
                            .collect()
                    };
                    for b in bands {
                        let mut m = vec![0u8; w * h];
                        read_window(
                            self.raster_io_ex,
                            b,
                            &mut m,
                            ReadSpec {
                                width: w,
                                height: h,
                                data_type: GDT_BYTE,
                                algorithm: 0,
                                window: [x as f64, y as f64, w as f64, h as f64],
                            },
                        )?;
                        for (a, b) in mask.iter_mut().zip(m) {
                            *a |= b;
                        }
                    }
                    if (self.raster_io_ex)(
                        dst_mask,
                        1,
                        x,
                        y,
                        w as c_int,
                        h as c_int,
                        mask.as_mut_ptr().cast(),
                        w as c_int,
                        h as c_int,
                        GDT_BYTE,
                        0,
                        0,
                        ptr::null_mut(),
                    ) != 0
                    {
                        return Err("cannot write mask".into());
                    }
                }
            }
            let level = ((height / 256).max(width / 256).max(1) as f64)
                .log2()
                .ceil() as u32;
            let overviews = (1..=level).map(|j| 1 << j).collect::<Vec<c_int>>();
            if !overviews.is_empty() {
                let build: unsafe extern "C" fn(
                    DatasetHandle,
                    *const c_char,
                    c_int,
                    *const c_int,
                    c_int,
                    *const c_int,
                    *mut c_void,
                    *mut c_void,
                ) -> c_int = self.symbol(b"GDALBuildOverviews\0")?;
                let resampling = CString::new(options.resampling_method.to_uppercase()).unwrap();
                if build(
                    dst.handle,
                    resampling.as_ptr(),
                    overviews.len() as c_int,
                    overviews.as_ptr(),
                    0,
                    ptr::null(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                ) != 0
                {
                    return Err("cannot build overviews".into());
                }
                let tag: unsafe extern "C" fn(
                    *mut c_void,
                    *const c_char,
                    *const c_char,
                    *const c_char,
                ) -> c_int = self.symbol(b"GDALSetMetadataItem\0")?;
                let value = CString::new(method.to_string()).unwrap();
                tag(
                    dst.handle,
                    c"resampling".as_ptr(),
                    value.as_ptr(),
                    c"rio_overview".as_ptr(),
                );
            }
            let mut args = common.iter().map(|v| v.to_string()).collect::<Vec<_>>();
            args.extend([
                "-co".into(),
                format!("COMPRESS={compression}"),
                "-co".into(),
                "ZLEVEL=1".into(),
                "-co".into(),
                "ZSTD_LEVEL=9".into(),
                "-co".into(),
                "COPY_SRC_OVERVIEWS=YES".into(),
            ]);
            let output =
                CString::new(output.to_string_lossy().as_bytes()).map_err(|e| e.to_string())?;
            let result = self.translate(dst.handle, &output, &args)?;
            drop(result);
            drop(dst);
            Ok(())
        }
    }
    unsafe fn translate(
        &self,
        source: DatasetHandle,
        path: &CString,
        args: &[String],
    ) -> Result<DatasetGuard> {
        unsafe {
            let new: unsafe extern "C" fn(*mut *mut c_char, *mut c_void) -> *mut c_void =
                self.symbol(b"GDALTranslateOptionsNew\0")?;
            let free: unsafe extern "C" fn(*mut c_void) =
                self.symbol(b"GDALTranslateOptionsFree\0")?;
            let translate: unsafe extern "C" fn(
                *const c_char,
                DatasetHandle,
                *const c_void,
                *mut c_int,
            ) -> DatasetHandle = self.symbol(b"GDALTranslate\0")?;
            let cargs = args
                .iter()
                .map(|a| CString::new(a.as_bytes()).map_err(|e| e.to_string()))
                .collect::<Result<Vec<_>>>()?;
            let mut pointers = cargs
                .iter()
                .map(|a| a.as_ptr().cast_mut())
                .collect::<Vec<_>>();
            pointers.push(ptr::null_mut());
            let opts = new(pointers.as_mut_ptr(), ptr::null_mut());
            if opts.is_null() {
                return Err("invalid raster translation options".into());
            }
            let mut usage = 0;
            let handle = translate(path.as_ptr(), source, opts, &mut usage);
            free(opts);
            if handle.is_null() {
                return Err("raster translation failed".into());
            }
            Ok(DatasetGuard {
                handle,
                close: self.close,
            })
        }
    }
}

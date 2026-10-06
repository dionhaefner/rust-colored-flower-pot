//! Padded general-reader VRTs retain the caller's affine grid exactly.
use super::*;

pub(super) type Transformer = unsafe extern "C" fn(
    *mut c_void,
    c_int,
    c_int,
    *mut f64,
    *mut f64,
    *mut f64,
    *mut c_int,
) -> c_int;

// Prefix of GDALWarpOptions' public C ABI. GDAL allocates the complete struct;
// only these documented fields are accessed here, never its unused tail.
#[repr(C)]
pub(super) struct WarpOptionsPrefix {
    pub(super) options: *mut *mut c_char,
    pub(super) memory_limit: f64,
    pub(super) algorithm: c_int,
    pub(super) working_type: c_int,
    pub(super) source: DatasetHandle,
    pub(super) destination: DatasetHandle,
    pub(super) band_count: c_int,
    pub(super) source_bands: *mut c_int,
    pub(super) destination_bands: *mut c_int,
    pub(super) source_alpha: c_int,
    pub(super) destination_alpha: c_int,
    pub(super) source_nodata: *mut f64,
    pub(super) source_nodata_imaginary: *mut f64,
    pub(super) destination_nodata: *mut f64,
    pub(super) destination_nodata_imaginary: *mut f64,
    pub(super) progress: *mut c_void,
    pub(super) progress_data: *mut c_void,
    pub(super) transformer: Option<Transformer>,
    pub(super) transformer_data: *mut c_void,
}

pub(super) struct WarpOptionsGuard {
    pub(super) handle: *mut WarpOptionsPrefix,
    pub(super) destroy: unsafe extern "C" fn(*mut WarpOptionsPrefix),
}
impl Drop for WarpOptionsGuard {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe { (self.destroy)(self.handle) }
        }
    }
}

pub(super) struct TransformerGuard {
    pub(super) handle: *mut c_void,
    pub(super) destroy: unsafe extern "C" fn(*mut c_void),
}
impl Drop for TransformerGuard {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe { (self.destroy)(self.handle) }
        }
    }
}

impl Gdal {
    pub(super) fn create_padded_vrt(
        &self,
        source: DatasetHandle,
        mut transform: [f64; 6],
        size: [usize; 2],
        algorithm: c_int,
    ) -> Result<DatasetGuard> {
        unsafe {
            let new_options: unsafe extern "C" fn() -> *mut WarpOptionsPrefix =
                self.symbol(b"GDALCreateWarpOptions\0")?;
            let destroy_options = self.symbol(b"GDALDestroyWarpOptions\0")?;
            let set: unsafe extern "C" fn(
                *mut *mut c_char,
                *const c_char,
                *const c_char,
            ) -> *mut *mut c_char = self.symbol(b"CSLSetNameValue\0")?;
            let map_bands: unsafe extern "C" fn(*mut WarpOptionsPrefix, c_int) =
                self.symbol(b"GDALWarpInitDefaultBandMapping\0")?;
            let init_nodata: unsafe extern "C" fn(*mut WarpOptionsPrefix, f64) =
                self.symbol(b"GDALWarpInitSrcNoDataReal\0")?;
            let init_dst_nodata: unsafe extern "C" fn(*mut WarpOptionsPrefix, f64) =
                self.symbol(b"GDALWarpInitDstNoDataReal\0")?;
            let projection: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                self.symbol(b"GDALGetProjectionRef\0")?;
            let new_ref: unsafe extern "C" fn(*const c_char) -> *mut c_void =
                self.symbol(b"OSRNewSpatialReference\0")?;
            let set_ref: unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int =
                self.symbol(b"OSRSetFromUserInput\0")?;
            let export: unsafe extern "C" fn(*mut c_void, *mut *mut c_char) -> c_int =
                self.symbol(b"OSRExportToWkt\0")?;
            let free: unsafe extern "C" fn(*mut c_void) = self.symbol(b"VSIFree\0")?;
            let new_transform: unsafe extern "C" fn(
                *const c_char,
                *const f64,
                *const c_char,
                *const f64,
            ) -> *mut c_void = self.symbol(b"GDALCreateGenImgProjTransformer3\0")?;
            let gen_transform: Transformer = self.symbol(b"GDALGenImgProjTransform\0")?;
            let new_approx: unsafe extern "C" fn(Transformer, *mut c_void, f64) -> *mut c_void =
                self.symbol(b"GDALCreateApproxTransformer\0")?;
            let approx_transform: Transformer = self.symbol(b"GDALApproxTransform\0")?;
            let owns: unsafe extern "C" fn(*mut c_void, c_int) =
                self.symbol(b"GDALApproxTransformerOwnsSubtransformer\0")?;
            let destroy_transform = self.symbol(b"GDALDestroyGenImgProjTransformer\0")?;
            let destroy_approx = self.symbol(b"GDALDestroyApproxTransformer\0")?;
            let create: unsafe extern "C" fn(
                DatasetHandle,
                c_int,
                c_int,
                *mut f64,
                *mut WarpOptionsPrefix,
            ) -> DatasetHandle = self.symbol(b"GDALCreateWarpedVRT\0")?;
            let set_projection: unsafe extern "C" fn(DatasetHandle, *const c_char) -> c_int =
                self.symbol(b"GDALSetProjection\0")?;
            let delete_nodata: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALDeleteRasterNoDataValue\0")?;
            let set_nodata: unsafe extern "C" fn(BandHandle, f64) -> c_int =
                self.symbol(b"GDALSetRasterNoDataValue\0")?;
            let target = SpatialReference {
                handle: new_ref(ptr::null()),
                destroy: self.symbol(b"OSRDestroySpatialReference\0")?,
            };
            let target_name =
                CString::new(self.target_crs.as_str()).map_err(|_| "invalid target CRS")?;
            if target.handle.is_null() || set_ref(target.handle, target_name.as_ptr()) != 0 {
                return Err("cannot initialize padded-grid CRS".into());
            }
            let mut wkt = ptr::null_mut();
            if export(target.handle, &mut wkt) != 0 {
                return Err("cannot export padded-grid CRS".into());
            }
            let target_wkt = std::ffi::CStr::from_ptr(wkt).to_owned();
            free(wkt.cast());
            let mut source_transform = [0.; 6];
            if (self.geo_transform)(source, source_transform.as_mut_ptr()) != 0 {
                return Err("source has no transform".into());
            }
            let mut transformer = TransformerGuard {
                handle: new_transform(
                    projection(source),
                    source_transform.as_ptr(),
                    target_wkt.as_ptr(),
                    transform.as_ptr(),
                ),
                destroy: destroy_transform,
            };
            if transformer.handle.is_null() {
                return Err("cannot create padded-grid transformer".into());
            }
            let mut approximate = TransformerGuard {
                handle: new_approx(gen_transform, transformer.handle, 0.125),
                destroy: destroy_approx,
            };
            if approximate.handle.is_null() {
                return Err("cannot create approximate padded-grid transformer".into());
            }
            owns(approximate.handle, 1);
            transformer.handle = ptr::null_mut();
            let options = WarpOptionsGuard {
                handle: new_options(),
                destroy: destroy_options,
            };
            if options.handle.is_null() {
                return Err("cannot allocate padded-grid warp options".into());
            }
            let count = (self.raster_count)(source);
            let alpha = (1..=count)
                .find(|&i| (self.color_interpretation)((self.raster_band)(source, i)) == 6);
            let mut has_nodata = 0;
            let nodata = (self.nodata)((self.raster_band)(source, 1), &mut has_nodata);
            let o = &mut *options.handle;
            o.source = source;
            o.algorithm = algorithm;
            o.source_alpha = alpha.unwrap_or(0);
            o.destination_alpha = alpha.unwrap_or(count + 1);
            o.options = set(o.options, c"UNIFIED_SRC_NODATA".as_ptr(), c"YES".as_ptr());
            o.options = set(
                o.options,
                c"INIT_DEST".as_ptr(),
                if has_nodata != 0 {
                    c"NO_DATA".as_ptr()
                } else {
                    c"0".as_ptr()
                },
            );
            map_bands(options.handle, count);
            if has_nodata != 0 {
                init_nodata(options.handle, nodata);
                if alpha.is_some() {
                    init_dst_nodata(options.handle, nodata);
                }
            }
            o.transformer = Some(approx_transform);
            o.transformer_data = approximate.handle;
            let handle = create(
                source,
                size[1] as c_int,
                size[0] as c_int,
                transform.as_mut_ptr(),
                options.handle,
            );
            if handle.is_null() {
                return Err("cannot create padded warped dataset".into());
            }
            // The VRT now owns the approximate transformer and its subtransformer.
            approximate.handle = ptr::null_mut();
            let vrt = DatasetGuard {
                handle,
                close: self.close,
            };
            if set_projection(handle, target_wkt.as_ptr()) != 0 {
                return Err("cannot set padded-grid projection".into());
            }
            for i in 1..=(self.raster_count)(handle) {
                let band = (self.raster_band)(handle, i);
                let error = if alpha.is_some() && has_nodata != 0 {
                    set_nodata(band, nodata)
                } else {
                    delete_nodata(band)
                };
                if error != 0 {
                    return Err("cannot set padded-grid nodata".into());
                }
            }
            Ok(vrt)
        }
    }
}

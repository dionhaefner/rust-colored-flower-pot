//! Scoped UTM interior path: GDAL coordinates and COG decoding, Rust bilinear sampling.

use super::fast::{GDT_UINT16, GF_WRITE};
use super::warped::{Transformer, TransformerGuard};
use super::*;

impl Gdal {
    pub(super) fn read_utm_fast(
        &self,
        path: &Path,
        stamp: Option<(u64, std::time::SystemTime)>,
        source: DatasetHandle,
        request: ReadRequest,
    ) -> Result<Option<Raster>> {
        if std::env::var_os("TC_RUST_UTM_FAST").is_none()
            || !self.target_crs.eq_ignore_ascii_case("EPSG:3857")
            || self.reprojection.0 != 1
            || self.resampling != 5
            || request.preserve_values
            || request.size != [256, 256]
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
            let destroy_srs: unsafe extern "C" fn(*mut c_void) =
                self.symbol(b"OSRDestroySpatialReference\0")?;
            let source_srs = get_spatial_ref(source);
            if source_srs.is_null() {
                return Ok(None);
            }
            let utm_srs = new_spatial_ref(ptr::null());
            if utm_srs.is_null() {
                return Err("GDAL could not allocate UTM SRS".into());
            }
            let utm_srs = SpatialReference {
                handle: utm_srs,
                destroy: destroy_srs,
            };
            if set_from_user_input(utm_srs.handle, c"EPSG:32632".as_ptr()) != 0
                || is_same(source_srs, utm_srs.handle) != 1
            {
                return Ok(None);
            }
            let target_srs = new_spatial_ref(ptr::null());
            if target_srs.is_null() {
                return Err("GDAL could not allocate Mercator SRS".into());
            }
            let target_srs = SpatialReference {
                handle: target_srs,
                destroy: destroy_srs,
            };
            if set_from_user_input(target_srs.handle, c"EPSG:3857".as_ptr()) != 0 {
                return Err("GDAL could not parse Mercator SRS".into());
            }
            let band = (self.raster_band)(source, 1);
            if band.is_null() || (self.band_data_type)(band) != GDT_UINT16 {
                return Ok(None);
            }
            let get_mask_flags: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetMaskFlags\0")?;
            if ![1, 8].contains(&get_mask_flags(band)) {
                return Ok(None);
            }
            let mut has_nodata = 0;
            let nodata = (self.nodata)(band, &mut has_nodata);
            if has_nodata != 0 && nodata != 0.0 {
                return Ok(None);
            }
            let mut src_t = [0.0; 6];
            if (self.geo_transform)(source, src_t.as_mut_ptr()) != 0
                || src_t[1] <= 0.0
                || src_t[5] >= 0.0
                || src_t[2] != 0.0
                || src_t[4] != 0.0
            {
                return Ok(None);
            }
            let geometry = self.cached_suggested_geometry(source, 1, stamp, 0)?;
            let requested_x_res = (bounds[2] - bounds[0]) / request.size[0] as f64;
            let requested_y_res = (bounds[3] - bounds[1]) / request.size[1] as f64;
            // Leave magnification and overview selection to the general GDAL path.
            // Use this four-sample kernel only near native resolution.
            if requested_x_res < geometry.transform[1].abs()
                || requested_y_res < geometry.transform[5].abs()
                || requested_x_res > 1.5 * geometry.transform[1].abs()
                || requested_y_res > 1.5 * geometry.transform[5].abs()
            {
                return Ok(None);
            }
            let iw = ((bounds[2] - bounds[0]) / geometry.transform[1].abs())
                .round()
                .max(1.0) as usize;
            let ih = ((bounds[3] - bounds[1]) / geometry.transform[5].abs())
                .round()
                .max(1.0) as usize;
            if iw > 2048 || ih > 2048 {
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
            let vw = iw + 4;
            let vh = ih + 4;
            let dst_t = [padded[0], dx, 0.0, padded[3], 0.0, -dy];
            let create_transform: unsafe extern "C" fn(
                *mut c_void,
                *const f64,
                *mut c_void,
                *const f64,
                *const *const c_char,
            ) -> *mut c_void = self.symbol(b"GDALCreateGenImgProjTransformer4\0")?;
            let destroy_transform: unsafe extern "C" fn(*mut c_void) =
                self.symbol(b"GDALDestroyGenImgProjTransformer\0")?;
            let transform_fn: Transformer = self.symbol(b"GDALGenImgProjTransform\0")?;
            let create_approx: unsafe extern "C" fn(Transformer, *mut c_void, f64) -> *mut c_void =
                self.symbol(b"GDALCreateApproxTransformer\0")?;
            let destroy_approx: unsafe extern "C" fn(*mut c_void) =
                self.symbol(b"GDALDestroyApproxTransformer\0")?;
            let approx_fn: Transformer = self.symbol(b"GDALApproxTransform\0")?;
            let base = create_transform(
                source_srs,
                src_t.as_ptr(),
                target_srs.handle,
                dst_t.as_ptr(),
                ptr::null(),
            );
            if base.is_null() {
                return Ok(None);
            }
            let base = TransformerGuard {
                handle: base,
                destroy: destroy_transform,
            };
            let approx = create_approx(transform_fn, base.handle, 0.125);
            if approx.is_null() {
                return Ok(None);
            }
            let approx = TransformerGuard {
                handle: approx,
                destroy: destroy_approx,
            };
            let mut coords_x = vec![0.0; vw * vh];
            let mut coords_y = vec![0.0; vw * vh];
            let mut z = vec![0.0; vw];
            let mut success = vec![0; vw];
            for row in 0..vh {
                let off = row * vw;
                let xs = &mut coords_x[off..off + vw];
                let ys = &mut coords_y[off..off + vw];
                for col in 0..vw {
                    xs[col] = col as f64 + 0.5;
                    ys[col] = row as f64 + 0.5;
                }
                if approx_fn(
                    approx.handle,
                    1,
                    vw as c_int,
                    xs.as_mut_ptr(),
                    ys.as_mut_ptr(),
                    z.as_mut_ptr(),
                    success.as_mut_ptr(),
                ) == 0
                    || success.contains(&0)
                {
                    return Ok(None);
                }
            }
            let min_x = coords_x.iter().copied().fold(f64::INFINITY, f64::min);
            let max_x = coords_x.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let min_y = coords_y.iter().copied().fold(f64::INFINITY, f64::min);
            let max_y = coords_y.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            if ![min_x, max_x, min_y, max_y].iter().all(|x| x.is_finite()) {
                return Ok(None);
            }
            let rx0 = min_x.floor() as i32 - 4;
            let rx1 = max_x.ceil() as i32 + 4;
            let ry0 = min_y.floor() as i32 - 4;
            let ry1 = max_y.ceil() as i32 + 4;
            if rx0 < 0
                || ry0 < 0
                || rx1 > (self.raster_x_size)(source)
                || ry1 > (self.raster_y_size)(source)
            {
                return Ok(None);
            }
            let sw = (rx1 - rx0) as usize;
            let sh = (ry1 - ry0) as usize;
            let cached = if stamp.is_some() {
                self.cached_native_window(path, source, band, [rx0, ry0, rx1, ry1])?
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
            if native.contains(&0) {
                return Ok(None);
            }
            // GDAL expands its bilinear kernel using chunk source-window scales.
            // Fixed four-sample interpolation differs slightly at UTM rotations.

            let mut intermediate = vec![0u16; vw * vh];
            for index in 0..vw * vh {
                let sx = coords_x[index] - 0.5 - rx0 as f64;
                let sy = coords_y[index] - 0.5 - ry0 as f64;
                if sx < 0.0 || sy < 0.0 {
                    return Ok(None);
                }
                let x0 = sx.floor() as usize;
                let y0 = sy.floor() as usize;
                if x0 + 1 >= sw || y0 + 1 >= sh {
                    return Ok(None);
                }
                let fx = sx - x0 as f64;
                let fy = sy - y0 as f64;
                let p00 = native[y0 * sw + x0] as f64;
                let p01 = native[y0 * sw + x0 + 1] as f64;
                let p10 = native[(y0 + 1) * sw + x0] as f64;
                let p11 = native[(y0 + 1) * sw + x0 + 1] as f64;
                let top = p00 + fx * (p01 - p00);
                let bottom = p10 + fx * (p11 - p10);
                intermediate[index] = (top + fy * (bottom - top)).round() as u16;
            }
            let driver_by_name: unsafe extern "C" fn(*const c_char) -> *mut c_void =
                self.symbol(b"GDALGetDriverByName\0")?;
            let create_mem: unsafe extern "C" fn(
                *mut c_void,
                *const c_char,
                c_int,
                c_int,
                c_int,
                c_int,
                *mut *mut c_char,
            ) -> DatasetHandle = self.symbol(b"GDALCreate\0")?;
            let mem_handle = create_mem(
                driver_by_name(c"MEM".as_ptr()),
                c"".as_ptr(),
                vw as c_int,
                vh as c_int,
                1,
                GDT_UINT16,
                ptr::null_mut(),
            );
            if mem_handle.is_null() {
                return Err("GDAL could not create manual UTM raster".into());
            }
            let mem = DatasetGuard {
                handle: mem_handle,
                close: self.close,
            };
            let mem_band = (self.raster_band)(mem.handle, 1);
            if (self.raster_io_ex)(
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
            ) != 0
            {
                return Err("GDAL could not fill manual UTM raster".into());
            }
            let mut values = vec![0.0f64; request.size[0] * request.size[1]];
            read_window(
                self.raster_io_ex,
                mem_band,
                &mut values,
                ReadSpec {
                    width: request.size[1],
                    height: request.size[0],
                    data_type: GDT_FLOAT64,
                    algorithm: self.resampling,
                    window: [2.0, 2.0, iw as f64, ih as f64],
                },
            )?;
            Ok(Some(Raster {
                data_type: GDT_UINT16,
                width: request.size[1],
                height: request.size[0],
                mask: vec![false; values.len()],
                values,
            }))
        }
    }
}

//! Worker-owned affine projection plans for direct native tile grids.

use super::warped::{Transformer, TransformerGuard, WarpOptionsGuard, WarpOptionsPrefix};
use super::*;

#[derive(Default)]
pub(super) struct NativeSource {
    eligible: Option<bool>,
    prunable: Option<bool>,
    plans: VecDeque<Plan>,
}

struct Plan {
    level: i32,
    transformer: TransformerGuard,
    // Overview datasets opened through the public API own their source handle.
    _overview: Option<DatasetGuard>,
    source: DatasetHandle,
}

impl Gdal {
    pub(super) fn read_planned_native(
        &self,
        source: DatasetHandle,
        requests: &[ReadRequest],
        bounds: [f64; 4],
        algorithm: c_int,
    ) -> Result<Option<Vec<Raster>>> {
        let sources = self.sources.borrow();
        if let Some(entry) = sources.iter().find(|entry| entry.dataset.handle == source) {
            return self.read_with_plan(
                source,
                requests,
                bounds,
                algorithm,
                &mut entry.native.borrow_mut(),
            );
        }
        let remote = self.remote_sources.borrow();
        if let Some(entry) = remote.iter().find(|entry| entry.dataset.handle == source) {
            return self.read_with_plan(
                source,
                requests,
                bounds,
                algorithm,
                &mut entry.native.borrow_mut(),
            );
        }
        Ok(None)
    }

    fn read_with_plan(
        &self,
        source: DatasetHandle,
        requests: &[ReadRequest],
        bounds: [f64; 4],
        algorithm: c_int,
        state: &mut NativeSource,
    ) -> Result<Option<Vec<Raster>>> {
        unsafe {
            if state.eligible.is_none() {
                state.eligible = Some(self.plan_eligible(source)?);
            }
            if state.eligible == Some(false) {
                return Ok(None);
            }
            let [height, width] = requests[0].size;
            let mut grid = [
                bounds[0],
                (bounds[2] - bounds[0]) / width as f64,
                0.,
                bounds[3],
                0.,
                (bounds[1] - bounds[3]) / height as f64,
            ];
            let set_grid: unsafe extern "C" fn(*mut c_void, *const f64) =
                self.symbol(b"GDALSetGenImgProjTransformerDstGeoTransform\0")?;
            let transform: Transformer = self.symbol(b"GDALGenImgProjTransform\0")?;
            let Ok(base) = self.plan(source, -1, state) else {
                return Ok(None);
            };
            set_grid(base.transformer.handle, grid.as_ptr());
            let level =
                self.plan_overview(source, base.transformer.handle, transform, width, height)?;
            if level < -1 {
                return Ok(None);
            }
            let Ok(plan) = self.plan(source, level, state) else {
                return Ok(None);
            };
            set_grid(plan.transformer.handle, grid.as_ptr());
            let count = (self.raster_count)(plan.source);
            let alpha = (self.color_interpretation)((self.raster_band)(plan.source, count)) == 6;
            let bands = count - i32::from(alpha);
            if state.prunable.is_none() {
                state.prunable = Some(self.plan_prunable(source)?);
            }
            let mut indexes = if state.prunable == Some(true)
                && requests.iter().all(|r| r.band <= bands as usize)
            {
                requests.iter().map(|r| r.band as c_int).collect::<Vec<_>>()
            } else {
                (1..=bands).collect()
            };
            indexes.sort_unstable();
            indexes.dedup();
            let plan = self.plan(source, level, state)?;
            let Some(warped) = self.planned_vrt(
                plan.source,
                plan.transformer.handle,
                &mut grid,
                [height, width],
                algorithm,
                &indexes,
            )?
            else {
                return Ok(None);
            };
            requests
                .iter()
                .map(|request| {
                    self.native_samples(
                        warped.handle,
                        indexes
                            .iter()
                            .position(|&index| index == request.band as c_int)
                            .map_or(indexes.len() + 1, |index| index + 1)
                            as c_int,
                        [0., 0., width as f64, height as f64],
                        request.size,
                        0,
                        true,
                    )
                })
                .collect::<Result<Vec<_>>>()
                .map(Some)
        }
    }

    unsafe fn plan_prunable(&self, source: DatasetHandle) -> Result<bool> {
        unsafe {
            let flags: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetMaskFlags\0")?;
            let mask: unsafe extern "C" fn(BandHandle) -> BandHandle =
                self.symbol(b"GDALGetMaskBand\0")?;
            let count = (self.raster_count)(source);
            let alpha = (self.color_interpretation)((self.raster_band)(source, count)) == 6;
            let first = (self.raster_band)(source, 1);
            let first_flags = flags(first);
            let first_mask = mask(first);
            for index in 1..=count - i32::from(alpha) {
                let band = (self.raster_band)(source, index);
                let mut present = 0;
                (self.nodata)(band, &mut present);
                let f = flags(band);
                if present != 0
                    || !(first_flags == 1 && f == 1
                        || first_flags == f && f & 8 == 0 && f & 6 != 0 && mask(band) == first_mask)
                {
                    return Ok(false);
                }
            }
            Ok(true)
        }
    }

    unsafe fn plan_eligible(&self, source: DatasetHandle) -> Result<bool> {
        unsafe {
            let gcps: unsafe extern "C" fn(DatasetHandle) -> c_int =
                self.symbol(b"GDALGetGCPCount\0")?;
            let metadata: unsafe extern "C" fn(DatasetHandle, *const c_char) -> *mut *mut c_char =
                self.symbol(b"GDALGetMetadata\0")?;
            let spatial: unsafe extern "C" fn(DatasetHandle) -> *mut c_void =
                self.symbol(b"GDALGetSpatialRef\0")?;
            let compound: unsafe extern "C" fn(*mut c_void) -> c_int =
                self.symbol(b"OSRIsCompound\0")?;
            let axes: unsafe extern "C" fn(*mut c_void) -> c_int =
                self.symbol(b"OSRGetAxesCount\0")?;
            let mut affine = [0.; 6];
            let reference = spatial(source);
            let count = (self.raster_count)(source);
            if count == 0
                || reference.is_null()
                || compound(reference) != 0
                || axes(reference) != 2
                || gcps(source) != 0
                || (self.geo_transform)(source, affine.as_mut_ptr()) != 0
                || !affine.iter().all(|value| value.is_finite())
                || affine[1] * affine[5] - affine[2] * affine[4] == 0.
            {
                return Ok(false);
            }
            for domain in [c"RPC", c"GEOLOCATION"] {
                let values = metadata(source, domain.as_ptr());
                if !values.is_null() && !(*values).is_null() {
                    return Ok(false);
                }
            }
            let dtype = (self.band_data_type)((self.raster_band)(source, 1));
            if ![1, 2, 3, 4, 5, 6, 7, 12, 13, 14].contains(&dtype) {
                return Ok(false);
            }
            Ok((1..=count).all(|index| {
                let band = (self.raster_band)(source, index);
                (self.band_data_type)(band) == dtype
                    && ((self.color_interpretation)(band) != 6 || index == count)
            }))
        }
    }

    unsafe fn plan<'a>(
        &self,
        source: DatasetHandle,
        level: i32,
        state: &'a mut NativeSource,
    ) -> Result<&'a Plan> {
        unsafe {
            if let Some(position) = state.plans.iter().position(|plan| plan.level == level) {
                let plan = state
                    .plans
                    .remove(position)
                    .expect("existing projection plan");
                state.plans.push_front(plan);
            } else {
                let overview = if level >= 0 {
                    let description: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                        self.symbol(b"GDALGetDescription\0")?;
                    let path = std::ffi::CStr::from_ptr(description(source))
                        .to_str()
                        .map_err(|_| "source path is not UTF-8")?;
                    Some(self.open_dataset_options(Path::new(path), Some(level))?)
                } else {
                    None
                };
                let handle = overview.as_ref().map_or(source, |dataset| dataset.handle);
                let create: unsafe extern "C" fn(
                    DatasetHandle,
                    DatasetHandle,
                    *const *const c_char,
                ) -> *mut c_void = self.symbol(b"GDALCreateGenImgProjTransformer2\0")?;
                let target = CString::new(format!("DST_SRS={}", self.target_crs))
                    .map_err(|_| "invalid target CRS")?;
                let options = [
                    target.as_ptr(),
                    c"INSERT_CENTER_LONG=FALSE".as_ptr(),
                    ptr::null(),
                ];
                let transformer = TransformerGuard {
                    handle: create(handle, ptr::null_mut(), options.as_ptr()),
                    destroy: self.symbol(b"GDALDestroyGenImgProjTransformer\0")?,
                };
                if transformer.handle.is_null() {
                    return Err("cannot create native projection plan".into());
                }
                state.plans.push_front(Plan {
                    level,
                    transformer,
                    _overview: overview,
                    source: handle,
                });
                // One base plan and at most four overview plans per source/worker.
                while state.plans.len() > 5 {
                    let index = state
                        .plans
                        .iter()
                        .rposition(|plan| plan.level >= 0)
                        .expect("overview plan");
                    state.plans.remove(index);
                }
            }
            Ok(state.plans.front().expect("projection plan just prepared"))
        }
    }

    unsafe fn plan_overview(
        &self,
        source: DatasetHandle,
        handle: *mut c_void,
        transform: Transformer,
        width: usize,
        height: usize,
    ) -> Result<i32> {
        unsafe {
            let band = (self.raster_band)(source, 1);
            let count = (self.overview_count)(band);
            if count == 0 {
                return Ok(-1);
            }
            let mut xs = [0.; 100];
            let mut ys = [0.; 100];
            let mut zs = [0.; 100];
            let mut valid = [0; 100];
            for x in 0..10 {
                for y in 0..10 {
                    xs[x * 10 + y] = width as f64 * x as f64 / 9.;
                    ys[x * 10 + y] = height as f64 * y as f64 / 9.;
                }
            }
            if transform(
                handle,
                1,
                100,
                xs.as_mut_ptr(),
                ys.as_mut_ptr(),
                zs.as_mut_ptr(),
                valid.as_mut_ptr(),
            ) == 0
            {
                return Ok(-2);
            }
            let mut limits = [
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
            ];
            for i in 0..100 {
                if valid[i] != 0 {
                    limits[0] = limits[0].min(xs[i]);
                    limits[1] = limits[1].max(xs[i]);
                    limits[2] = limits[2].min(ys[i]);
                    limits[3] = limits[3].max(ys[i]);
                }
            }
            let ratio = ((limits[1] - limits[0]) / width as f64)
                .min((limits[3] - limits[2]) / height as f64);
            if !ratio.is_finite() {
                return Ok(-2);
            }
            if ratio <= 1. {
                return Ok(-1);
            }
            let config: unsafe extern "C" fn(*const c_char, *const c_char) -> *const c_char =
                self.symbol(b"CPLGetConfigOption\0")?;
            let threshold = std::ffi::CStr::from_ptr(config(
                c"GDALWARP_OVERSAMPLING_THRESHOLD".as_ptr(),
                c"1".as_ptr(),
            ))
            .to_str()
            .ok()
            .and_then(|v| v.parse::<f64>().ok())
            .unwrap_or(1.);
            let overview: unsafe extern "C" fn(BandHandle, c_int) -> BandHandle =
                self.symbol(b"GDALGetOverview\0")?;
            let band_width: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetRasterBandXSize\0")?;
            let mut best = -1;
            let mut best_ratio = 0.;
            for level in -1..count {
                let factor = if level < 0 {
                    1.
                } else {
                    (self.raster_x_size)(source) as f64 / band_width(overview(band, level)) as f64
                };
                if factor >= ratio * threshold + 0.1 || factor <= best_ratio {
                    continue;
                }
                best = level;
                best_ratio = factor;
                if (ratio - factor).abs() < 0.1 {
                    break;
                }
            }
            Ok(best)
        }
    }

    unsafe fn planned_vrt(
        &self,
        source: DatasetHandle,
        transformer: *mut c_void,
        grid: &mut [f64; 6],
        size: [usize; 2],
        algorithm: c_int,
        indexes: &[c_int],
    ) -> Result<Option<DatasetGuard>> {
        unsafe {
            let create_options: unsafe extern "C" fn() -> *mut WarpOptionsPrefix =
                self.symbol(b"GDALCreateWarpOptions\0")?;
            let options = WarpOptionsGuard {
                handle: create_options(),
                destroy: self.symbol(b"GDALDestroyWarpOptions\0")?,
            };
            if options.handle.is_null() {
                return Err("cannot allocate native warp options".into());
            }
            let map: unsafe extern "C" fn(*mut WarpOptionsPrefix, c_int) =
                self.symbol(b"GDALWarpInitDefaultBandMapping\0")?;
            let set: unsafe extern "C" fn(
                *mut *mut c_char,
                *const c_char,
                *const c_char,
            ) -> *mut *mut c_char = self.symbol(b"CSLSetNameValue\0")?;
            let init_nodata: unsafe extern "C" fn(*mut WarpOptionsPrefix, f64) =
                self.symbol(b"GDALWarpInitSrcNoDataReal\0")?;
            let count = (self.raster_count)(source);
            let alpha = (self.color_interpretation)((self.raster_band)(source, count)) == 6;
            let bands = indexes.len() as c_int;
            let o = &mut *options.handle;
            o.source = source;
            o.algorithm = algorithm;
            o.working_type = 0;
            o.source_alpha = if alpha { count } else { 0 };
            o.destination_alpha = bands + 1;
            o.options = set(o.options, c"INIT_DEST".as_ptr(), c"0".as_ptr());
            map(options.handle, bands);
            for (i, &index) in indexes.iter().enumerate() {
                *o.source_bands.add(i) = index;
            }
            let nodata = indexes
                .iter()
                .copied()
                .map(|i| {
                    let mut present = 0;
                    let value = (self.nodata)((self.raster_band)(source, i), &mut present);
                    (present != 0).then_some(value)
                })
                .collect::<Vec<_>>();
            if nodata.iter().any(Option::is_some) {
                init_nodata(options.handle, -123456.789);
                for (i, value) in nodata.iter().enumerate() {
                    if let Some(value) = value {
                        *o.source_nodata.add(i) = *value;
                    }
                }
            }
            let resolve: unsafe extern "C" fn(*mut WarpOptionsPrefix) =
                self.symbol(b"GDALWarpResolveWorkingDataType\0")?;
            resolve(options.handle);
            if o.working_type != (self.band_data_type)((self.raster_band)(source, 1)) {
                return Ok(None);
            }
            let new_approx: unsafe extern "C" fn(Transformer, *mut c_void, f64) -> *mut c_void =
                self.symbol(b"GDALCreateApproxTransformer\0")?;
            let mut approximate = TransformerGuard {
                handle: new_approx(
                    self.symbol(b"GDALGenImgProjTransform\0")?,
                    transformer,
                    0.125,
                ),
                destroy: self.symbol(b"GDALDestroyApproxTransformer\0")?,
            };
            if approximate.handle.is_null() {
                return Err("cannot create native approximation".into());
            }
            // The tile owns the approximation, and the source owns the base plan.
            o.transformer = Some(self.symbol(b"GDALApproxTransform\0")?);
            o.transformer_data = approximate.handle;
            let create: unsafe extern "C" fn(
                DatasetHandle,
                c_int,
                c_int,
                *mut f64,
                *mut WarpOptionsPrefix,
            ) -> DatasetHandle = self.symbol(b"GDALCreateWarpedVRT\0")?;
            let handle = create(
                source,
                size[1] as c_int,
                size[0] as c_int,
                grid.as_mut_ptr(),
                options.handle,
            );
            if handle.is_null() {
                return Err("cannot create native warped grid".into());
            }
            approximate.handle = ptr::null_mut();
            let vrt = DatasetGuard {
                handle,
                close: self.close,
            };
            let delete: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALDeleteRasterNoDataValue\0")?;
            // Coverage comes from alpha because the GDAL utility does not install destination nodata.
            for band in 1..=(self.raster_count)(handle) {
                delete((self.raster_band)(handle, band));
            }
            Ok(Some(vrt))
        }
    }
}

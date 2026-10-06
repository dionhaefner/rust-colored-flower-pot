//! Direct output-grid reads for the native rendering policy.

use super::*;

impl Gdal {
    pub(super) fn read_native(
        &self,
        source: DatasetHandle,
        request: ReadRequest,
    ) -> Result<Option<Raster>> {
        self.read_native_bands(source, &[request])
            .map(|result| result.map(|mut rasters| rasters.remove(0)))
    }

    pub(super) fn read_native_bands(
        &self,
        source: DatasetHandle,
        requests: &[ReadRequest],
    ) -> Result<Option<Vec<Raster>>> {
        if !self.native_direct || !self.target_crs.eq_ignore_ascii_case("EPSG:3857") {
            return Ok(None);
        }
        let Some(request) = requests.first() else {
            return Ok(Some(vec![]));
        };
        let Some(bounds) = request.bounds_mercator else {
            return Ok(None);
        };
        if requests.iter().any(|other| {
            other.bounds_mercator != request.bounds_mercator
                || other.size != request.size
                || other.preserve_values != request.preserve_values
        }) {
            return Err("shared band reads require one output grid and resampling policy".into());
        }
        let [height, width] = request.size;
        let algorithm = if request.preserve_values {
            0
        } else {
            self.reprojection.0
        };
        let started = Instant::now();
        unsafe {
            if requests.iter().any(|request| {
                request.band == 0 || request.band > (self.raster_count)(source) as usize
            }) {
                return Err("source band is outside the raster's band count".into());
            }
            let spatial_ref: unsafe extern "C" fn(DatasetHandle) -> *mut c_void =
                self.symbol(b"GDALGetSpatialRef\0")?;
            let same: unsafe extern "C" fn(*mut c_void, *mut c_void) -> c_int =
                self.symbol(b"OSRIsSame\0")?;
            let mut transform = [0.; 6];
            let source_ref = spatial_ref(source);
            let aligned = !source_ref.is_null()
                && same(source_ref, self.native_target.handle) == 1
                && (self.geo_transform)(source, transform.as_mut_ptr()) == 0
                && transform[1] > 0.
                && transform[5] < 0.
                && transform[2] == 0.
                && transform[4] == 0.;
            if aligned {
                let window = [
                    (bounds[0] - transform[0]) / transform[1],
                    (bounds[3] - transform[3]) / transform[5],
                    (bounds[2] - bounds[0]) / transform[1],
                    (bounds[1] - bounds[3]) / transform[5],
                ];
                // Partial source coverage uses the warper's alpha handling.
                if window[0] >= 0.
                    && window[1] >= 0.
                    && window[0] + window[2] <= (self.raster_x_size)(source) as f64
                    && window[1] + window[3] <= (self.raster_y_size)(source) as f64
                {
                    return requests
                        .iter()
                        .map(|request| {
                            self.native_samples(
                                source,
                                request.band as c_int,
                                window,
                                request.size,
                                algorithm,
                                false,
                            )
                        })
                        .collect::<Result<Vec<_>>>()
                        .map(Some);
                }
            }
            if self.native_plan {
                if let Some(rasters) =
                    self.read_planned_native(source, requests, bounds, algorithm)?
                {
                    return Ok(Some(rasters));
                }
            }
            let warped = self.create_warped_dataset(
                source,
                bounds,
                request.size,
                if request.preserve_values {
                    "near"
                } else {
                    self.reprojection.1
                },
            )?;
            if self.profile_reads {
                eprintln!(
                    "raster_profile native_warp_us={}",
                    started.elapsed().as_micros()
                );
            }
            requests
                .iter()
                .map(|request| {
                    self.native_samples(
                        warped.handle,
                        request.band as c_int,
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

    pub(super) unsafe fn native_samples(
        &self,
        source: DatasetHandle,
        index: c_int,
        window: [f64; 4],
        size: [usize; 2],
        algorithm: c_int,
        warped: bool,
    ) -> Result<Raster> {
        unsafe {
            let [height, width] = size;
            let band = (self.raster_band)(source, index);
            if band.is_null() {
                return Err("raster has no requested band".into());
            }
            let mut values = vec![0.0f64; width * height];
            read_window(
                self.raster_io_ex,
                band,
                &mut values,
                ReadSpec {
                    width,
                    height,
                    data_type: GDT_FLOAT64,
                    algorithm,
                    window,
                },
            )?;
            let get_mask: unsafe extern "C" fn(BandHandle) -> BandHandle =
                self.symbol(b"GDALGetMaskBand\0")?;
            let flags: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetMaskFlags\0")?;
            let alpha = (1..=(self.raster_count)(source))
                .find(|&band| (self.color_interpretation)((self.raster_band)(source, band)) == 6);
            if warped && alpha.is_none() {
                return Err("warped raster has no alpha band".into());
            }
            let mask_band = if let Some(alpha) = alpha {
                (self.raster_band)(source, alpha)
            } else {
                get_mask(band)
            };
            let validity = if alpha.is_some() || flags(band) & 1 == 0 {
                let mut mask = vec![0.0f64; width * height];
                read_window(
                    self.raster_io_ex,
                    mask_band,
                    &mut mask,
                    ReadSpec {
                        width,
                        height,
                        data_type: GDT_FLOAT64,
                        algorithm: 0,
                        window,
                    },
                )?;
                Some(mask)
            } else {
                None
            };
            let mut has_nodata = 0;
            let nodata = (self.nodata)(band, &mut has_nodata);
            let mask = values
                .iter()
                .enumerate()
                .map(|(i, &v)| {
                    !v.is_finite()
                        || has_nodata != 0 && v == nodata
                        || validity
                            .as_ref()
                            .is_some_and(|m| !m[i].is_finite() || m[i] <= 0.)
                })
                .collect();
            Ok(Raster {
                width,
                height,
                values,
                mask,
                data_type: (self.band_data_type)(band),
            })
        }
    }
}

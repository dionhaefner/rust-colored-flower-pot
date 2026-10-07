//! Source band inspection and isolated-band alignment for native layers.
use super::metadata::Coordinates;
use super::*;
use crate::layers::{AssetMetadata, BandMetadata, GridRead};
use serde_json::json;
use std::ffi::CStr;

unsafe fn text(pointer: *const c_char) -> String {
    if pointer.is_null() {
        String::new()
    } else {
        unsafe { CStr::from_ptr(pointer).to_string_lossy().into_owned() }
    }
}
impl Gdal {
    pub fn asset_map_bounds(&self, path: &Path) -> Result<[f64; 4]> {
        let metadata = self.inspect_asset(path)?;
        unsafe {
            Coordinates::new_to(
                self,
                CString::new(metadata.crs)
                    .map_err(|_| "invalid asset CRS")?
                    .as_ptr(),
                c"EPSG:3857".as_ptr(),
            )?
            .bounds(metadata::extent(&metadata.footprint))
        }
    }

    pub fn inspect_asset(&self, path: &Path) -> Result<AssetMetadata> {
        let source = self.open_dataset(path)?;
        unsafe {
            let description: unsafe extern "C" fn(*mut c_void) -> *const c_char =
                self.symbol(b"GDALGetDescription\0")?;
            let units: unsafe extern "C" fn(BandHandle) -> *const c_char =
                self.symbol(b"GDALGetRasterUnitType\0")?;
            let scale: unsafe extern "C" fn(BandHandle, *mut c_int) -> f64 =
                self.symbol(b"GDALGetRasterScale\0")?;
            let offset: unsafe extern "C" fn(BandHandle, *mut c_int) -> f64 =
                self.symbol(b"GDALGetRasterOffset\0")?;
            let categories: unsafe extern "C" fn(BandHandle) -> *mut *mut c_char =
                self.symbol(b"GDALGetRasterCategoryNames\0")?;
            let type_name: unsafe extern "C" fn(c_int) -> *const c_char =
                self.symbol(b"GDALGetDataTypeName\0")?;
            let color_name: unsafe extern "C" fn(c_int) -> *const c_char =
                self.symbol(b"GDALGetColorInterpretationName\0")?;
            let projection: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                self.symbol(b"GDALGetProjectionRef\0")?;
            let mut transform = [0.; 6];
            if (self.geo_transform)(source.handle, transform.as_mut_ptr()) != 0 {
                return Err("asset has no affine geotransform".into());
            }
            let crs = text(projection(source.handle));
            if crs.is_empty() {
                return Err("asset has no CRS".into());
            }
            let width = (self.raster_x_size)(source.handle) as usize;
            let height = (self.raster_y_size)(source.handle) as usize;
            let footprint = [
                [0., 0.],
                [width as f64, 0.],
                [width as f64, height as f64],
                [0., height as f64],
            ]
            .map(|[x, y]| {
                [
                    transform[0] + x * transform[1] + y * transform[2],
                    transform[3] + x * transform[4] + y * transform[5],
                ]
            });
            let flags: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetMaskFlags\0")?;
            let color_table: unsafe extern "C" fn(BandHandle) -> *mut c_void =
                self.symbol(b"GDALGetRasterColorTable\0")?;
            let color_count: unsafe extern "C" fn(*mut c_void) -> c_int =
                self.symbol(b"GDALGetColorEntryCount\0")?;
            let color_entry: unsafe extern "C" fn(*mut c_void, c_int, *mut [i16; 4]) -> c_int =
                self.symbol(b"GDALGetColorEntryAsRGB\0")?;
            let mut bands = Vec::new();
            for index in 1..=(self.raster_count)(source.handle) {
                let band = (self.raster_band)(source.handle, index);
                let data_type = (self.band_data_type)(band);
                if ![1, 2, 3, 4, 5, 6, 7, 12, 13, 14].contains(&data_type) {
                    return Err("complex or unsupported band dtype".into());
                }
                let mut valid = 0;
                let nodata = (self.nodata)(band, &mut valid);
                let nodata = if valid == 0 {
                    None
                } else if nodata.is_finite() {
                    Some(json!(nodata))
                } else {
                    Some(json!(nodata.to_string()))
                };
                let mut valid = 0;
                let s = scale(band, &mut valid);
                let scale = if valid != 0 { s } else { 1. };
                let mut valid = 0;
                let o = offset(band, &mut valid);
                let offset = if valid != 0 { o } else { 0. };
                let mut names = Vec::new();
                let table = categories(band);
                if !table.is_null() {
                    for i in 0..65536 {
                        let name = *table.add(i);
                        if name.is_null() {
                            break;
                        }
                        names.push(text(name));
                    }
                }
                let table = color_table(band);
                let mut colors = Vec::new();
                if !table.is_null() {
                    for i in 0..color_count(table).min(65536) {
                        let mut rgba = [0i16; 4];
                        if color_entry(table, i, &mut rgba) != 0 {
                            colors.push(rgba.map(i32::from));
                        }
                    }
                }
                bands.push(BandMetadata {
                    index: index as usize,
                    name: text(description(band)),
                    dtype: text(type_name(data_type)),
                    data_type,
                    units: text(units(band)),
                    scale,
                    offset,
                    nodata,
                    color_interpretation: text(color_name((self.color_interpretation)(band))),
                    categories: names,
                    mask_flags: flags(band),
                    color_table: colors,
                });
            }
            if bands.is_empty() {
                return Err("asset has no raster bands".into());
            }
            Ok(AssetMetadata {
                generation: None,
                crs,
                size: [height, width],
                transform,
                footprint,
                bands,
            })
        }
    }
    unsafe fn asset_coverage_vrt(
        &self,
        selected: &VsiDatasetGuard,
        source: DatasetHandle,
        mask_flags: c_int,
        alpha: bool,
    ) -> Result<VsiDatasetGuard> {
        unsafe {
            static REGISTERED: std::sync::OnceLock<Result<()>> = std::sync::OnceLock::new();
            REGISTERED
                .get_or_init(|| {
                    let _lock = GDAL_INITIALIZATION
                        .lock()
                        .map_err(|_| "GDAL initialization lock poisoned")?;
                    let register: unsafe extern "C" fn(
                        *const c_char,
                        Option<ValidityFunction>,
                    ) -> c_int = self.symbol(b"GDALAddDerivedBandPixelFunc\0")?;
                    if register(c"rcfp_validity".as_ptr(), Some(validity_pixels)) != 0 {
                        return Err("cannot register native validity function".into());
                    }
                    Ok(())
                })
                .clone()?;
            let mut transform = [0.; 6];
            if (self.geo_transform)(source, transform.as_mut_ptr()) != 0 {
                return Err("asset has no affine transform".into());
            }
            let projection: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                self.symbol(b"GDALGetProjectionRef\0")?;
            let width = (self.raster_x_size)(source);
            let height = (self.raster_y_size)(source);
            let filename = xml(&text(selected.path.as_ptr()));
            let source_xml = |band: &str| {
                format!(
                    "<SimpleSource><SourceFilename relativeToVRT=\"0\">{filename}</SourceFilename><SourceBand>{band}</SourceBand><SrcRect xOff=\"0\" yOff=\"0\" xSize=\"{width}\" ySize=\"{height}\"/><DstRect xOff=\"0\" yOff=\"0\" xSize=\"{width}\" ySize=\"{height}\"/></SimpleSource>"
                )
            };
            let mut sources = source_xml("1");
            if mask_flags & 1 == 0 && mask_flags & 4 == 0 {
                sources.push_str(&source_xml("mask,1"));
            }
            if alpha {
                sources.push_str(&source_xml("2"));
            }
            let band = (self.raster_band)(selected.handle, 1);
            let mut valid = 0;
            let nodata = (self.nodata)(band, &mut valid);
            let nodata = if valid != 0 {
                format!("<NoDataValue>{nodata}</NoDataValue>")
            } else {
                String::new()
            };
            let dtype: unsafe extern "C" fn(c_int) -> *const c_char =
                self.symbol(b"GDALGetDataTypeName\0")?;
            let document = format!(
                "<VRTDataset rasterXSize=\"{width}\" rasterYSize=\"{height}\"><SRS>{}</SRS><GeoTransform>{}</GeoTransform><VRTRasterBand dataType=\"{}\" band=\"1\">{nodata}{}</VRTRasterBand><VRTRasterBand dataType=\"Float64\" band=\"2\" subClass=\"VRTDerivedRasterBand\"><ColorInterp>Alpha</ColorInterp><PixelFunctionType>rcfp_validity</PixelFunctionType><SourceTransferType>Float64</SourceTransferType>{sources}</VRTRasterBand></VRTDataset>",
                xml(&text(projection(source))),
                transform.map(|v| v.to_string()).join(","),
                text(dtype((self.band_data_type)(band))),
                source_xml("1")
            );
            let path = CString::new(format!(
                "/vsimem/rcfp-coverage-{}-{}.vrt",
                std::process::id(),
                VRT_SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ))
            .expect("generated path");
            let open: unsafe extern "C" fn(*const c_char, *const c_char) -> *mut c_void =
                self.symbol(b"VSIFOpenL\0")?;
            let write: unsafe extern "C" fn(*const c_void, usize, usize, *mut c_void) -> usize =
                self.symbol(b"VSIFWriteL\0")?;
            let close: unsafe extern "C" fn(*mut c_void) -> c_int = self.symbol(b"VSIFCloseL\0")?;
            let file = open(path.as_ptr(), c"wb".as_ptr());
            if file.is_null() {
                return Err("cannot allocate coverage VRT".into());
            }
            let count = write(document.as_ptr().cast(), 1, document.len(), file);
            close(file);
            if count != document.len() {
                (self.vsi_unlink)(path.as_ptr());
                return Err("cannot write coverage VRT".into());
            }
            match self.open_dataset(Path::new(&text(path.as_ptr()))) {
                Ok(dataset) => Ok(VsiDatasetGuard {
                    dataset,
                    path,
                    unlink: self.vsi_unlink,
                }),
                Err(error) => {
                    (self.vsi_unlink)(path.as_ptr());
                    Err(error)
                }
            }
        }
    }

    fn select_asset_band(
        &self,
        source: DatasetHandle,
        band_index: usize,
    ) -> Result<(VsiDatasetGuard, VsiDatasetGuard)> {
        unsafe {
            if band_index == 0 || band_index > (self.raster_count)(source) as usize {
                return Err("source band is outside the raster's band count".into());
            }
            let translate_options: unsafe extern "C" fn(
                *mut *mut c_char,
                *mut c_void,
            ) -> *mut c_void = self.symbol(b"GDALTranslateOptionsNew\0")?;
            let options_free: unsafe extern "C" fn(*mut c_void) =
                self.symbol(b"GDALTranslateOptionsFree\0")?;
            let translate: unsafe extern "C" fn(
                *const c_char,
                DatasetHandle,
                *const c_void,
                *mut c_int,
            ) -> DatasetHandle = self.symbol(b"GDALTranslate\0")?;
            let set_color: unsafe extern "C" fn(BandHandle, c_int) -> c_int =
                self.symbol(b"GDALSetRasterColorInterpretation\0")?;
            let alpha = (1..=(self.raster_count)(source))
                .find(|i| (self.color_interpretation)((self.raster_band)(source, *i)) == 6);
            let mut arguments = vec![
                "-of".to_owned(),
                "VRT".to_owned(),
                "-b".to_owned(),
                band_index.to_string(),
            ];
            let mask_flags: unsafe extern "C" fn(BandHandle) -> c_int =
                self.symbol(b"GDALGetMaskFlags\0")?;
            let flags = mask_flags((self.raster_band)(source, band_index as c_int));
            // GDALTranslate's automatic mask only copies dataset-wide masks.
            // Select channel masks explicitly, retaining native nodata as well.
            // Alpha masks stay explicit alpha to avoid Byte quantization.
            if flags & 1 == 0 && flags & 4 == 0 {
                arguments.extend(["-mask".into(), format!("mask,{band_index}")]);
            }
            if let Some(alpha) = alpha.filter(|i| *i as usize != band_index) {
                arguments.extend(["-b".into(), alpha.to_string()]);
            }
            let arguments = arguments
                .into_iter()
                .map(|s| CString::new(s).expect("generated argument"))
                .collect::<Vec<_>>();
            let mut pointers = arguments
                .iter()
                .map(|s| s.as_ptr().cast_mut())
                .chain(std::iter::once(ptr::null_mut()))
                .collect::<Vec<_>>();
            let options = translate_options(pointers.as_mut_ptr(), ptr::null_mut());
            if options.is_null() {
                return Err("cannot select source band".into());
            }
            let name = CString::new(format!(
                "/vsimem/rcfp-band-{}-{}.vrt",
                std::process::id(),
                VRT_SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ))
            .expect("generated path");
            let mut error = 0;
            let handle = translate(name.as_ptr(), source, options, &mut error);
            options_free(options);
            if handle.is_null() {
                return Err("cannot select source band".into());
            }
            let selected = VsiDatasetGuard {
                dataset: DatasetGuard {
                    handle,
                    close: self.close,
                },
                path: name,
                unlink: self.vsi_unlink,
            };
            if error != 0 {
                return Err("cannot select source band".into());
            }
            // A selected alpha band is data here; only the extra alpha remains coverage.
            if set_color((self.raster_band)(selected.handle, 1), 0) != 0 {
                return Err("cannot set selected band interpretation".into());
            }
            let covered = self.asset_coverage_vrt(
                &selected,
                source,
                flags,
                alpha.is_some_and(|i| i as usize != band_index),
            )?;
            Ok((selected, covered))
        }
    }

    pub fn compute_band_metadata(
        &self,
        path: &Path,
        band: usize,
        options: &super::MetadataOptions,
    ) -> Result<crate::storage::Metadata> {
        crate::layers::require_native()?;
        let source = self.open_dataset(path)?;
        let (_selected, covered) = self.select_asset_band(source.handle, band)?;
        self.compute_metadata(
            Path::new(covered.path.to_str().map_err(|_| "invalid coverage path")?),
            options,
        )
    }

    fn read_asset_point(
        &self,
        source: DatasetHandle,
        band: usize,
        point: [f64; 2],
    ) -> Result<Raster> {
        unsafe {
            let projection: unsafe extern "C" fn(DatasetHandle) -> *const c_char =
                self.symbol(b"GDALGetProjectionRef\0")?;
            let coordinates = Coordinates::new_to(self, c"EPSG:4326".as_ptr(), projection(source))?;
            let [x, y] = coordinates.point(point)?;
            let mut affine = [0.; 6];
            if (self.geo_transform)(source, affine.as_mut_ptr()) != 0 {
                return Err("point inspection requires an affine source".into());
            }
            let determinant = affine[1] * affine[5] - affine[2] * affine[4];
            if determinant == 0. || !determinant.is_finite() {
                return Err("source has an invalid affine transform".into());
            }
            let cell = |a: f64, b: f64| {
                let coordinate = (a * (x - affine[0]) - b * (y - affine[3])) / determinant;
                let tolerance = 8.
                    * f64::EPSILON
                    * (a.abs() * (x.abs() + affine[0].abs())
                        + b.abs() * (y.abs() + affine[3].abs()))
                    / determinant.abs();
                // Affine subtraction can place an exact cell edge just below its integer.
                let integer = coordinate.round();
                if (coordinate - integer).abs() <= tolerance {
                    integer
                } else {
                    coordinate.floor()
                }
            };
            let column = cell(affine[5], affine[2]);
            let row = cell(-affine[4], -affine[1]);
            if !column.is_finite()
                || !row.is_finite()
                || column < 0.
                || row < 0.
                || column >= (self.raster_x_size)(source) as f64
                || row >= (self.raster_y_size)(source) as f64
            {
                return Ok(Raster {
                    data_type: 7,
                    width: 1,
                    height: 1,
                    values: vec![f64::NAN],
                    mask: vec![true],
                });
            }
            let (_selected, covered) = self.select_asset_band(source, band)?;
            let mut sample = self.native_samples(
                covered.handle,
                1,
                [column, row, 1., 1.],
                [1, 1],
                GRIORA_NEAREST,
                true,
            )?;
            sample.data_type = (self.band_data_type)((self.raster_band)(source, band as c_int));
            Ok(sample)
        }
    }

    pub(super) fn read_asset_grid(
        &self,
        source: DatasetHandle,
        request: ReadRequest,
        grid: GridRead,
    ) -> Result<Raster> {
        crate::layers::require_native()?;
        if let Some(point) = grid.point {
            return self.read_asset_point(source, request.band, point.map(f64::from_bits));
        }
        let bounds = request
            .bounds_mercator
            .ok_or("native asset read requires explicit grid bounds")?;
        if request.size.contains(&0) || request.size.iter().any(|s| *s > 2048) {
            return Err("native grid dimensions must be between 1 and 2048".into());
        }
        unsafe {
            if request.band == 0 || request.band > (self.raster_count)(source) as usize {
                return Err("source band is outside the raster's band count".into());
            }
            let (_selected, covered) = self.select_asset_band(source, request.band)?;
            let warped = self.create_grid_warp(
                covered.handle,
                bounds,
                request.size,
                grid.resampling.gdal(),
                &format!("EPSG:{}", grid.epsg),
                true,
            )?;
            let [height, width] = request.size;
            let mut raster = self.native_samples(
                warped.handle,
                1,
                [0., 0., width as f64, height as f64],
                request.size,
                0,
                true,
            )?;
            // Warping may promote working samples; raw arithmetic uses source dtype.
            raster.data_type =
                (self.band_data_type)((self.raster_band)(source, request.band as c_int));
            Ok(raster)
        }
    }
}

fn xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('\"', "&quot;")
}

type ValidityFunction = unsafe extern "C" fn(
    *mut *mut c_void,
    c_int,
    *mut c_void,
    c_int,
    c_int,
    c_int,
    c_int,
    c_int,
    c_int,
) -> c_int;

// GDAL supplies contiguous source buffers in SourceTransferType Float64 and a
// destination buffer with its requested dtype and byte strides. The first
// source holds data; subsequent sources hold channel masks and explicit alpha.
unsafe extern "C" fn validity_pixels(
    sources: *mut *mut c_void,
    count: c_int,
    destination: *mut c_void,
    width: c_int,
    height: c_int,
    source_type: c_int,
    output_type: c_int,
    pixel_stride: c_int,
    row_stride: c_int,
) -> c_int {
    if sources.is_null()
        || destination.is_null()
        || count < 1
        || source_type != 7
        || width < 0
        || height < 0
        || pixel_stride < 1
        || row_stride < 1
    {
        return 3;
    }
    let Some(length) = (width as usize).checked_mul(height as usize) else {
        return 3;
    };
    let sources = unsafe { std::slice::from_raw_parts(sources, count as usize) };
    if sources.iter().any(|s| s.is_null()) {
        return 3;
    }
    for i in 0..length {
        let data = unsafe { (sources[0] as *const f64).add(i).read_unaligned() };
        let valid = data.is_finite()
            && sources[1..].iter().all(|p| {
                let value = unsafe { (*p as *const f64).add(i).read_unaligned() };
                value.is_finite() && value > 0.
            });
        let offset = (i / width as usize) * row_stride as usize
            + (i % width as usize) * pixel_stride as usize;
        let pointer = unsafe { destination.cast::<u8>().add(offset) };
        macro_rules! write {
            ($t:ty) => {
                unsafe { pointer.cast::<$t>().write_unaligned(u8::from(valid) as $t) }
            };
        }
        match output_type {
            1 => write!(u8),
            2 => write!(u16),
            3 => write!(i16),
            4 => write!(u32),
            5 => write!(i32),
            6 => write!(f32),
            7 => write!(f64),
            12 => write!(u64),
            13 => write!(i64),
            14 => write!(i8),
            _ => return 3,
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_validity_combines_finite_data_masks_and_fractional_alpha_with_strides() {
        let mut values = [1., 2., f64::NAN, 0., -4., 5.];
        let mut masks = [255., 0., 255., 255., 255., 255.];
        let mut alpha = [0.25, 1., 1., 0.01, f64::NAN, 0.];
        let mut sources = [
            values.as_mut_ptr().cast(),
            masks.as_mut_ptr().cast(),
            alpha.as_mut_ptr().cast(),
        ];
        let mut output = [42f64; 12];
        assert_eq!(
            unsafe {
                validity_pixels(
                    sources.as_mut_ptr(),
                    3,
                    output.as_mut_ptr().cast(),
                    3,
                    2,
                    7,
                    7,
                    16,
                    48,
                )
            },
            0
        );
        assert_eq!(
            output,
            [1., 42., 0., 42., 0., 42., 1., 42., 0., 42., 0., 42.]
        );
        let mut floats = [0f32; 6];
        assert_eq!(
            unsafe {
                validity_pixels(
                    sources.as_mut_ptr(),
                    3,
                    floats.as_mut_ptr().cast(),
                    3,
                    2,
                    7,
                    6,
                    4,
                    12,
                )
            },
            0
        );
        assert_eq!(floats, [1., 0., 0., 1., 0., 0.]);
    }
}

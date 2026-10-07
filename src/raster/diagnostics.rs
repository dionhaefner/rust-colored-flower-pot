use super::*;
use serde_json::{Value, json};

impl Gdal {
    /// Read layout declarations without computing statistics or sampling pixels.
    pub fn inspect_layout(&self, path: &Path) -> Result<Value> {
        let source = self.open_dataset(path)?;
        unsafe {
            let block_size: unsafe extern "C" fn(BandHandle, *mut c_int, *mut c_int) =
                self.symbol(b"GDALGetBlockSize\0")?;
            let overview: unsafe extern "C" fn(BandHandle, c_int) -> BandHandle =
                self.symbol(b"GDALGetOverview\0")?;
            let count = (self.raster_count)(source.handle);
            if !(1..=256).contains(&count) {
                return Err("inspection supports at most 256 bands".into());
            }
            let mut bands = Vec::new();
            for index in 1..=count {
                let band = (self.raster_band)(source.handle, index);
                let (mut x, mut y) = (0, 0);
                block_size(band, &mut x, &mut y);
                let mut sizes = Vec::new();
                for level in 0..(self.overview_count)(band).min(32) {
                    let band = overview(band, level);
                    if !band.is_null() {
                        let width: unsafe extern "C" fn(BandHandle) -> c_int =
                            self.symbol(b"GDALGetRasterBandXSize\0")?;
                        let height: unsafe extern "C" fn(BandHandle) -> c_int =
                            self.symbol(b"GDALGetRasterBandYSize\0")?;
                        sizes.push(json!([height(band), width(band)]));
                    }
                }
                bands.push(json!({"index":index, "block_size":[y,x], "overviews":sizes}));
            }
            Ok(json!(bands))
        }
    }
}

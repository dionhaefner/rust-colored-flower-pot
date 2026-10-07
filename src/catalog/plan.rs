//! Source-independent band semantics and logical dataset reads.
use super::*;
use crate::layers::{
    self, Asset, BandSelector, Binding, Channel, Coverage, Encoding, Grid, Layer, Observation,
    Overlap, RenderSpec, Values,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::Digest;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BandInfo {
    pub band: usize,
    pub name: Option<String>,
    pub common_name: Option<String>,
    pub scale: f64,
    pub offset: f64,
    pub nodata: serde_json::Value,
}

#[derive(Clone)]
pub(crate) enum Plan {
    Band {
        asset: Asset,
        band: usize,
        values: Values,
    },
    Expression {
        expression: String,
        bindings: BTreeMap<String, Source>,
    },
    Layer {
        layer: Layer,
        spec: RenderSpec,
    },
}

impl Source {
    pub fn band(asset: Asset, band: usize, values: Values) -> Result<Self> {
        let info = asset.band(&BandSelector::Index(band))?;
        if (asset.categorical_bands.contains(&band)
            || !info.categories.is_empty()
            || info.color_interpretation == "Palette")
            && matches!(values, Values::Scaled)
        {
            return Err("categorical bands require raw values".into());
        }
        if !info.scale.is_finite() || !info.offset.is_finite() {
            return Err("band scale and offset must be finite".into());
        }
        let path = asset.location.clone();
        let bounds = crate::raster::Gdal::load()?.asset_map_bounds(&path)?;
        Ok(Self {
            path,
            plan: Some(Plan::Band {
                asset,
                band,
                values,
            }),
            bounds: Some(bounds),
        })
    }
    pub fn expression(expression: String, bindings: BTreeMap<String, Source>) -> Result<Self> {
        if bindings.is_empty() || bindings.len() > 16 {
            return Err("expression requires 1 to 16 dataset bindings".into());
        }
        let dummies = bindings
            .iter()
            .map(|(name, source)| Ok((name.clone(), source.validation_sample()?)))
            .collect::<Result<BTreeMap<_, _>>>()?;
        let refs = dummies
            .iter()
            .map(|(name, sample)| (name.clone(), sample))
            .collect();
        crate::expressions::evaluate_typed(&expression, &refs)?;
        let bounds = bindings.values().try_fold(
            [
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ],
            |b, s| {
                let a = s.preview_bounds()?;
                Ok::<_, String>([
                    b[0].min(a[0]),
                    b[1].min(a[1]),
                    b[2].max(a[2]),
                    b[3].max(a[3]),
                ])
            },
        )?;
        Ok(Self {
            path: PathBuf::new(),
            plan: Some(Plan::Expression {
                expression,
                bindings,
            }),
            bounds: Some(bounds),
        })
    }
    fn validation_sample(&self) -> Result<Raster> {
        let dtype = match &self.plan {
            Some(Plan::Band {
                asset,
                band,
                values: Values::Raw,
            }) => asset.band(&BandSelector::Index(*band))?.data_type,
            Some(Plan::Expression {
                expression,
                bindings,
            }) => {
                let samples = bindings
                    .iter()
                    .map(|(name, source)| Ok((name.clone(), source.validation_sample()?)))
                    .collect::<Result<BTreeMap<_, _>>>()?;
                let refs = samples
                    .iter()
                    .map(|(name, sample)| (name.clone(), sample))
                    .collect();
                return crate::expressions::evaluate_typed(expression, &refs);
            }
            _ => 7,
        };
        Ok(Raster {
            data_type: dtype,
            width: 1,
            height: 1,
            values: vec![1.],
            mask: vec![false],
        })
    }
    pub(crate) fn preview_bounds(&self) -> Result<[f64; 4]> {
        self.bounds
            .map(Ok)
            .unwrap_or_else(|| crate::raster::Gdal::load()?.asset_map_bounds(&self.path))
    }
    pub fn mapped(&self) -> bool {
        self.plan.is_some()
    }
    pub fn planned_read(
        &self,
        executor: &RasterExecutor,
        mut request: ReadRequest,
    ) -> Result<Arc<Raster>> {
        let Some(plan) = &self.plan else {
            return executor.read(&self.path, request);
        };
        if request.band != 1 {
            return Err(
                "logical datasets select their stored band; omit physical band parameters".into(),
            );
        }
        let bounds = request.bounds_mercator.unwrap_or(self.preview_bounds()?);
        request.bounds_mercator = Some(bounds);
        match plan {
            Plan::Band {
                asset,
                band,
                values,
            } => {
                if let Some(mut grid) = request.grid {
                    grid.revision = sha2::Sha256::digest(asset.revision.as_bytes()).into();
                    let info = asset.band(&BandSelector::Index(*band))?;
                    if asset.categorical_bands.contains(band)
                        || !info.categories.is_empty()
                        || info.color_interpretation == "Palette"
                    {
                        grid.resampling = layers::Resampling::Nearest;
                    }
                    request.grid = Some(grid);
                    request.band = *band;
                    let raw = executor.read(&asset.location, request)?;
                    if matches!(values, Values::Raw) {
                        return Ok(raw);
                    }
                    let mut scaled = (*raw).clone();
                    scaled.data_type = 7;
                    for (value, mask) in scaled.values.iter_mut().zip(&mut scaled.mask) {
                        *value = *value * info.scale + info.offset;
                        *mask |= !value.is_finite();
                    }
                    return Ok(Arc::new(scaled));
                }
                let layer = Layer {
                    observations: vec![Observation {
                        id: "dataset".into(),
                        assets: BTreeMap::from([("image".into(), asset.clone())]),
                        attributes: BTreeMap::new(),
                    }],
                    overlap: Overlap::FirstValid,
                    selection: BTreeMap::new(),
                    default_render: None,
                    mosaic: None,
                };
                let spec = RenderSpec {
                    selection: BTreeMap::new(),
                    bindings: BTreeMap::from([(
                        "v".into(),
                        Binding {
                            asset: "image".into(),
                            band: BandSelector::Index(*band),
                        },
                    )]),
                    values: *values,
                    channels: vec![Channel {
                        expression: "v".into(),
                        stretch: Some([0., 1.]),
                        categories: vec![],
                    }],
                    coverage: Coverage::AnyValid,
                    grid: Grid {
                        crs: "EPSG:3857".into(),
                        bounds,
                        size: request.size,
                        resampling: resampling()?,
                    },
                    encoding: Encoding::Png,
                };
                let mut raster = layer.samples(executor, &spec)?.remove(0);
                if matches!(values, Values::Raw) {
                    raster.data_type = asset.band(&BandSelector::Index(*band))?.data_type;
                }
                Ok(Arc::new(raster))
            }
            Plan::Expression {
                expression,
                bindings,
            } => {
                let inputs = bindings
                    .iter()
                    .map(|(name, source)| Ok((name.clone(), source.read(executor, request)?)))
                    .collect::<Result<BTreeMap<_, _>>>()?;
                let refs = inputs
                    .iter()
                    .map(|(name, r)| (name.clone(), r.as_ref()))
                    .collect();
                Ok(Arc::new(crate::expressions::evaluate_typed(
                    expression, &refs,
                )?))
            }
            Plan::Layer { layer, spec } => {
                let mut spec = spec.clone();
                spec.grid = Grid {
                    crs: format!("EPSG:{}", request.grid.map_or(3857, |g| g.epsg)),
                    bounds,
                    size: request.size,
                    resampling: spec.grid.resampling,
                };
                Ok(Arc::new(
                    layer.samples_on(executor, &spec, request.grid)?.remove(0),
                ))
            }
        }
    }
    pub fn metadata(&self) -> Result<Metadata> {
        if let Some(Plan::Band {
            asset,
            band,
            values,
        }) = &self.plan
        {
            let mut meta = crate::raster::Gdal::load()?.compute_band_metadata(
                &asset.location,
                *band,
                &crate::raster::MetadataOptions::default(),
            )?;
            let info = asset.band(&BandSelector::Index(*band))?;
            if matches!(values, Values::Scaled) {
                calibrate_metadata(&mut meta, info.scale, info.offset)?;
            }
            meta.metadata = json!({"band":{"name":info.name,"dtype":info.dtype,"units":info.units,"scale":info.scale,"offset":info.offset,"values":values}});
            return Ok(meta);
        }
        let executor = RasterExecutor::start(crate::execution::ExecutionConfig::from_env()?)?;
        let max_shape = crate::settings::var("TC_LAZY_LOADING_MAX_SHAPE")
            .ok()
            .map(|s| serde_json::from_str(&s).map_err(|e| e.to_string()))
            .transpose()?
            .unwrap_or([1024, 1024]);
        let raster = self.read(
            &executor,
            ReadRequest {
                size: max_shape,
                ..Default::default()
            },
        )?;
        let mut values = raster
            .values
            .iter()
            .zip(&raster.mask)
            .filter_map(|(v, m)| (!m && v.is_finite()).then_some(*v))
            .collect::<Vec<_>>();
        if values.is_empty() {
            return Err("dataset contains no valid data".into());
        }
        values.sort_by(f64::total_cmp);
        let n = values.len();
        let mean = values.iter().sum::<f64>() / n as f64;
        let bounds = to_wgs84(self.preview_bounds()?);
        Ok(Metadata {
            keys: BTreeMap::new(),
            bounds,
            convex_hull: json!({"type":"Polygon","coordinates":[[[bounds[0],bounds[1]],[bounds[2],bounds[1]],[bounds[2],bounds[3]],[bounds[0],bounds[3]],[bounds[0],bounds[1]]]]}),
            valid_percentage: n as f64 / raster.values.len() as f64 * 100.,
            range: [values[0], values[n - 1]],
            mean,
            stdev: (values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n as f64).sqrt(),
            percentiles: (1..100)
                .map(|p| quantile(&values, p as f64 / 100.))
                .collect(),
            metadata: json!({"statistics":"sampled","shape":max_shape}),
        })
    }
}

pub(crate) fn calibrate_metadata(metadata: &mut Metadata, scale: f64, offset: f64) -> Result<()> {
    metadata.range = metadata.range.map(|v| v * scale + offset);
    metadata.mean = metadata.mean * scale + offset;
    metadata.stdev *= scale.abs();
    for v in &mut metadata.percentiles {
        *v = *v * scale + offset;
    }
    if scale < 0. {
        metadata.range.reverse();
        metadata.percentiles.reverse();
    }
    if metadata
        .range
        .iter()
        .chain([&metadata.mean, &metadata.stdev])
        .chain(&metadata.percentiles)
        .any(|v| !v.is_finite())
    {
        return Err("band calibration produces nonfinite metadata".into());
    }
    Ok(())
}
fn resampling() -> Result<layers::Resampling> {
    match crate::settings::var("TC_REPROJECTION_METHOD")
        .unwrap_or_else(|_| "linear".into())
        .as_str()
    {
        "nearest" => Ok(layers::Resampling::Nearest),
        "linear" | "bilinear" => Ok(layers::Resampling::Bilinear),
        "cubic" => Ok(layers::Resampling::Cubic),
        "average" => Ok(layers::Resampling::Average),
        _ => Err("logical dataset resampling supports nearest, bilinear, cubic or average".into()),
    }
}

fn quantile(values: &[f64], q: f64) -> f64 {
    let p = q * (values.len() - 1) as f64;
    let lo = p.floor() as usize;
    let hi = p.ceil() as usize;
    values[lo] + (values[hi] - values[lo]) * (p - lo as f64)
}
fn to_wgs84(b: [f64; 4]) -> [f64; 4] {
    let lon = |x: f64| (x / 6378137.).to_degrees();
    let lat =
        |y: f64| (2. * (y / 6378137.).exp().atan() - std::f64::consts::FRAC_PI_2).to_degrees();
    [lon(b[0]), lat(b[1]), lon(b[2]), lat(b[3])]
}

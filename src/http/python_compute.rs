//! Binary array and display boundaries for trusted Python hosts.
use super::*;
use crate::layers::{Asset, BandSelector, Grid, GridRead, Values};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Input {
    asset: String,
    band: BandSelector,
    values: Values,
}
#[derive(Deserialize, Serialize)]
struct Plan {
    assets: BTreeMap<String, Asset>,
    inputs: BTreeMap<String, Input>,
    grid: Grid,
    context: Value,
    identity: String,
    cacheable: bool,
    generation: Value,
}
fn generation(assets: &BTreeMap<String, Asset>) -> Value {
    json!(
        assets
            .iter()
            .map(|(name, asset)| (
                name,
                (
                    asset.identity(),
                    crate::execution::source_generation(&asset.location)
                )
            ))
            .collect::<BTreeMap<_, _>>()
    )
}
impl Application {
    pub(crate) fn compute_url(
        &self,
        headers: &BTreeMap<String, String>,
        absolute: bool,
    ) -> Result<String> {
        crate::settings::scope(self.settings.clone(), || {
            crate::layers::require_native()?;
            if absolute {
                super::native::public_url(headers)
            } else {
                super::native::discovery_base(headers)
            }
        })
    }

    pub(crate) fn compute_prepare(&self, r: &Value) -> Result<Value> {
        crate::settings::scope(self.settings.clone(), || {
            crate::layers::require_native()?;
            let grid: Grid =
                serde_json::from_value(r["grid"].clone()).map_err(|e| e.to_string())?;
            grid.epsg()?;
            if grid.size[0] * grid.size[1] > 1024 * 1024 {
                return Err("compute grid exceeds 1048576 pixels".into());
            }
            let inputs: BTreeMap<String, Input> =
                serde_json::from_value(r["inputs"].clone()).map_err(|e| e.to_string())?;
            if inputs.is_empty() || inputs.len() > 16 {
                return Err("compute requires between 1 and 16 named inputs".into());
            }
            let layer = self
                .layers
                .as_ref()
                .ok_or("compute inputs require a native layer catalog")?
                .lock()
                .map_err(|_| "layer catalog lock poisoned")?
                .get(r["source"].as_str().ok_or("source layer required")?)?
                .ok_or("source layer not found")?;
            if layer.mosaic.is_some() {
                return Err("compute inputs require an explicit non-mosaic observation".into());
            }
            let observation = layer
                .observations
                .iter()
                .find(|o| Some(o.id.as_str()) == r["observation"].as_str())
                .ok_or("observation not found")?;
            let mut assets = BTreeMap::new();
            for input in inputs.values() {
                let asset = observation
                    .assets
                    .get(&input.asset)
                    .ok_or("input asset not found")?;
                assets.insert(input.asset.clone(), asset.clone());
            }
            // Capture stamps before inspection so changes cannot publish a mixed plan.
            let before = generation(&assets);
            let gdal = crate::raster::Gdal::load()?;
            for asset in assets.values_mut() {
                asset.inspect(&gdal)?;
            }
            let stamps = assets
                .iter()
                .map(|(name, a)| (name, crate::execution::source_generation(&a.location)))
                .collect::<BTreeMap<_, _>>();
            for name in assets.keys() {
                if before[name][1] != json!(stamps[name]) {
                    return Err("source changed during rendering; retry".into());
                }
            }
            let bands = inputs.iter().map(|(name, input)| {
                let asset = &assets[&input.asset];
                let band = asset.band(&input.band)?;
                if !band.scale.is_finite() || !band.offset.is_finite() { return Err("band calibration must be finite".into()); }
                let categorical = asset.categorical_bands.contains(&band.index) || !band.categories.is_empty() || band.color_interpretation == "Palette";
                if categorical && (matches!(input.values, Values::Scaled) || grid.resampling != crate::layers::Resampling::Nearest) { return Err("categorical compute inputs require raw values and nearest resampling".into()); }
                Ok((name.clone(), json!({"asset":input.asset,"band":band.index,"values":input.values,"scale":band.scale,"offset":band.offset,"units":band.units,"revision":asset.revision})))
            }).collect::<Result<BTreeMap<_, _>>>()?;
            let cacheable = assets
                .values()
                .all(|a| !crate::execution::is_remote(&a.location) || !a.revision.is_empty());
            let identity = format!(
                "{:x}",
                Sha256::digest(
                    serde_json::to_vec(&json!([
                        generation(&assets),
                        inputs,
                        grid,
                        observation.id,
                        observation.attributes
                    ]))
                    .map_err(|e| e.to_string())?
                )
            );
            let context = json!({"grid":grid,"observation":{"id":observation.id,"attributes":observation.attributes},"inputs":bands,"source_revision":identity});
            let generation = generation(&assets);
            Ok(json!(Plan {
                assets,
                inputs,
                grid,
                context,
                identity,
                cacheable,
                generation
            }))
        })
    }

    pub(crate) fn compute_style(&self, parameters: &BTreeMap<String, String>) -> Result<()> {
        crate::settings::scope(self.settings.clone(), || {
            self.validate_render_options("/singleband/host/preview.png", parameters)?;
            if parameters.get("colormap").is_none_or(|v| v != "explicit") {
                let range = parse_numeric_range(
                    parameters
                        .get("stretch_range")
                        .ok_or("numeric stretch_range required")?,
                )?;
                if range[0] >= range[1] {
                    return Err("stretch requires increasing endpoints".into());
                }
            }
            Ok(())
        })
    }

    pub(crate) fn compute_binary(&self, r: &Value, body: &[u8]) -> Result<AppResponse> {
        let _permit = self.layer_permit()?;
        crate::settings::scope(self.settings.clone(), || {
            crate::layers::require_native()?;
            match r["operation"].as_str() {
                Some("inputs") => {
                    let plan: Plan =
                        serde_json::from_value(r["plan"].clone()).map_err(|e| e.to_string())?;
                    let epsg = plan.grid.epsg()?;
                    if plan.inputs.is_empty()
                        || plan.inputs.len() > 16
                        || plan.grid.size[0] * plan.grid.size[1] > 1024 * 1024
                    {
                        return Err("invalid compute input dimensions".into());
                    }
                    let before = generation(&plan.assets);
                    if before != plan.generation {
                        return Err("source changed during rendering; retry".into());
                    }
                    let requests = plan
                        .inputs
                        .values()
                        .map(|input| {
                            let asset = plan
                                .assets
                                .get(&input.asset)
                                .ok_or("input asset not found")?;
                            let band = asset.band(&input.band)?;
                            Ok((
                                asset.location.clone(),
                                ReadRequest {
                                    grid: Some(GridRead {
                                        matrix: None,
                                        point: None,
                                        epsg,
                                        resampling: plan.grid.resampling,
                                        revision: Sha256::digest(asset.revision.as_bytes()).into(),
                                    }),
                                    band: band.index,
                                    bounds_mercator: Some(plan.grid.bounds),
                                    size: plan.grid.size,
                                    preserve_values: false,
                                },
                            ))
                        })
                        .collect::<Result<Vec<_>>>()?;
                    let rasters = self.executor.read_many(&requests)?;
                    if before != generation(&plan.assets) {
                        return Err("source changed during rendering; retry".into());
                    }
                    let mut bytes = Vec::new();
                    for ((_, input), raster) in plan.inputs.iter().zip(rasters) {
                        let band = plan.assets[&input.asset].band(&input.band)?;
                        for value in &raster.values {
                            let value = if matches!(input.values, Values::Scaled) {
                                *value * band.scale + band.offset
                            } else {
                                *value
                            };
                            bytes.extend_from_slice(&value.to_le_bytes());
                        }
                        bytes.extend(raster.values.iter().zip(&raster.mask).map(|(v, mask)| {
                            u8::from(
                                !mask
                                    && v.is_finite()
                                    && (!matches!(input.values, Values::Scaled)
                                        || (*v * band.scale + band.offset).is_finite()),
                            )
                        }));
                    }
                    Ok(bytes_response("application/octet-stream", bytes))
                }
                Some("render") => {
                    let size: [usize; 2] =
                        serde_json::from_value(r["size"].clone()).map_err(|e| e.to_string())?;
                    if size.contains(&0)
                        || size.iter().any(|n| *n > 2048)
                        || size[0] * size[1] > 1024 * 1024
                        || body.len() != size[0] * size[1] * 9
                    {
                        return Err("invalid compute output buffers".into());
                    }
                    let count = size[0] * size[1];
                    let values = body[..count * 8]
                        .chunks_exact(8)
                        .map(|v| f64::from_le_bytes(v.try_into().expect("eight bytes")))
                        .collect::<Vec<_>>();
                    let valid = &body[count * 8..];
                    if valid.iter().any(|v| *v > 1)
                        || values
                            .iter()
                            .zip(valid)
                            .any(|(v, valid)| *valid == 1 && !v.is_finite())
                    {
                        return Err("valid compute values must be finite and masks boolean".into());
                    }
                    let raster = Raster {
                        height: size[0],
                        width: size[1],
                        data_type: 7,
                        values,
                        mask: valid.iter().map(|v| *v == 0).collect(),
                    };
                    let parameters: BTreeMap<String, String> =
                        serde_json::from_value(r["style"].clone()).map_err(|e| e.to_string())?;
                    self.compute_style(&parameters)?;
                    let explicit = parameters
                        .get("explicit_color_map")
                        .map(|v| parse_palette(v))
                        .transpose()?;
                    let range = if explicit.is_some() {
                        [0., 1.]
                    } else {
                        parse_numeric_range(&parameters["stretch_range"])?
                    };
                    let operations = parameters
                        .get("color_transform")
                        .map(|v| color::parse(v, 1))
                        .transpose()?
                        .unwrap_or_default();
                    let png = self.scalar_image(
                        &raster,
                        range,
                        &operations,
                        explicit.as_deref(),
                        parameters.get("colormap").map(String::as_str),
                    )?;
                    Ok(bytes_response("image/png", png))
                }
                _ => Err("unknown compute buffer operation".into()),
            }
        })
    }
}

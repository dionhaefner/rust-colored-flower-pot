//! Nearest source-cell inspection, independent of display and output tile grids.
use super::{Plan, Source};
use crate::{
    Result,
    execution::RasterExecutor,
    layers::{Asset, BandSelector, GridRead, Resampling, Values},
    raster::ReadRequest,
};
use serde_json::{Value, json};

impl Source {
    pub(crate) fn point(&self, executor: &RasterExecutor, coordinate: [f64; 2]) -> Result<Value> {
        let request = ReadRequest {
            grid: Some(GridRead {
                epsg: 4326,
                resampling: Resampling::Nearest,
                revision: [0; 32],
                matrix: None,
                point: Some(coordinate.map(f64::to_bits)),
            }),
            size: [1, 1],
            // Point reads use the source affine and ignore output bounds.
            bounds_mercator: Some([
                coordinate[0] - 0.5,
                coordinate[1] - 0.5,
                coordinate[0] + 0.5,
                coordinate[1] + 0.5,
            ]),
            ..Default::default()
        };
        match &self.plan {
            None => {
                let asset = Asset {
                    location: self.path.clone(),
                    revision: String::new(),
                    band_names: Default::default(),
                    categorical_bands: Default::default(),
                    metadata: Some(crate::layers::inspect(&self.path)?),
                };
                let mut value = Self {
                    path: self.path.clone(),
                    plan: Some(Plan::Band {
                        asset,
                        band: 1,
                        values: Values::Raw,
                    }),
                    bounds: None,
                }
                .point(executor, coordinate)?;
                value["provenance"]["revision"] = json!(self.identity());
                Ok(value)
            }
            Some(Plan::Band {
                asset,
                band,
                values,
            }) => {
                let info = asset.band(&BandSelector::Index(*band))?;
                let mut raw_source = self.clone();
                raw_source.plan = Some(Plan::Band {
                    asset: asset.clone(),
                    band: *band,
                    values: Values::Raw,
                });
                let raw = raw_source.read(executor, request)?;
                let valid = !raw.mask[0] && raw.values[0].is_finite();
                let scaled = raw.values[0] * info.scale + info.offset;
                let value = if matches!(values, Values::Scaled) {
                    scaled
                } else {
                    raw.values[0]
                };
                Ok(
                    json!({"value":if valid && value.is_finite() {Some(value)} else {None},"valid":valid && value.is_finite(),"raw":if valid {Some(raw.values[0])} else {None},"scaled":if valid && scaled.is_finite() {Some(scaled)} else {None},"values":values,"units":info.units,"scale":info.scale,"offset":info.offset,"provenance":{"revision":self.identity(),"band":band,"name":info.name,"source_crs":asset.metadata.as_ref().map(|m| &m.crs)}}),
                )
            }
            Some(Plan::Expression {
                expression,
                bindings,
            }) => {
                let raster = self.read(executor, request)?;
                let operands = bindings
                    .iter()
                    .map(|(name, source)| Ok((name.clone(), source.point(executor, coordinate)?)))
                    .collect::<Result<serde_json::Map<_, _>>>()?;
                Ok(
                    json!({"value":if !raster.mask[0] && raster.values[0].is_finite() {Some(raster.values[0])} else {None},"valid":!raster.mask[0] && raster.values[0].is_finite(),"raw":null,"scaled":null,"units":null,"values":"expression","provenance":{"revision":self.identity(),"expression":expression,"operands":operands}}),
                )
            }
            Some(Plan::Layer { layer, spec }) => {
                let mut spec = spec.clone();
                spec.grid.size = [1, 1];
                spec.grid.resampling = Resampling::Nearest;
                let samples = layer.samples_on(executor, &spec, request.grid)?;
                let raster = &samples[0];
                Ok(
                    json!({"value":if !raster.mask[0] && raster.values[0].is_finite() {Some(raster.values[0])} else {None},"valid":!raster.mask[0] && raster.values[0].is_finite(),"raw":null,"scaled":null,"units":null,"values":spec.values,"provenance":{"revision":self.identity(),"expression":spec.channels[0].expression,"observations":layer.observations.iter().map(|o| &o.id).collect::<Vec<_>>()}}),
                )
            }
        }
    }
}

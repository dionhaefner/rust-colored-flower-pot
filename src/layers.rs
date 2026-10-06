//! Native database-backed assets, observations, layers and render specifications.
use crate::{
    Result,
    execution::RasterExecutor,
    expressions, image,
    raster::{Gdal, Raster, ReadRequest},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub mod catalog;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum Resampling {
    Nearest,
    Bilinear,
    Cubic,
    Average,
}
impl Resampling {
    pub(crate) fn gdal(self) -> &'static str {
        match self {
            Self::Nearest => "near",
            Self::Bilinear => "bilinear",
            Self::Cubic => "cubic",
            Self::Average => "average",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct GridRead {
    pub epsg: u32,
    pub resampling: Resampling,
    pub revision: [u8; 32],
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BandMetadata {
    pub index: usize,
    pub name: String,
    pub dtype: String,
    pub data_type: i32,
    pub units: String,
    pub scale: f64,
    pub offset: f64,
    pub nodata: Option<Value>,
    pub color_interpretation: String,
    pub mask_flags: i32,
    pub color_table: Vec<[i32; 4]>,
    pub categories: Vec<String>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AssetMetadata {
    pub crs: String,
    pub size: [usize; 2],
    pub transform: [f64; 6],
    /// Four affine corners in the source CRS; the footprint is not a validity hull.
    pub footprint: [[f64; 2]; 4],
    pub bands: Vec<BandMetadata>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Asset {
    pub location: PathBuf,
    #[serde(default)]
    pub revision: String,
    #[serde(default)]
    pub band_names: BTreeMap<String, usize>,
    #[serde(default)]
    pub categorical_bands: BTreeSet<usize>,
    #[serde(default)]
    pub metadata: Option<AssetMetadata>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub id: String,
    pub assets: BTreeMap<String, Asset>,
    #[serde(default)]
    pub attributes: BTreeMap<String, Value>,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Overlap {
    FirstValid,
    LastValid,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Layer {
    pub observations: Vec<Observation>,
    pub overlap: Overlap,
    #[serde(default)]
    pub selection: BTreeMap<String, Value>,
    #[serde(default)]
    pub default_render: Option<RenderSpec>,
}
#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum BandSelector {
    Index(usize),
    Name(String),
}
impl<'de> Deserialize<'de> for BandSelector {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        let value = Value::deserialize(deserializer)?;
        if let Some(index) = value
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .filter(|n| *n > 0)
        {
            return Ok(Self::Index(index));
        }
        if let Some(name) = value.as_str().filter(|s| !s.is_empty()) {
            return Ok(Self::Name(name.into()));
        }
        Err(serde::de::Error::custom(
            "source band must be a positive one-based integer or a nonempty name",
        ))
    }
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub asset: String,
    pub band: BandSelector,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Values {
    Raw,
    Scaled,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Coverage {
    AnyValid,
    AllValid,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Grid {
    pub crs: String,
    pub bounds: [f64; 4],
    pub size: [usize; 2],
    pub resampling: Resampling,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Category {
    pub value: f64,
    pub color: image::Rgba,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Channel {
    pub expression: String,
    pub stretch: Option<[f64; 2]>,
    #[serde(default)]
    pub categories: Vec<Category>,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Encoding {
    Png,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RenderSpec {
    pub bindings: BTreeMap<String, Binding>,
    pub values: Values,
    pub channels: Vec<Channel>,
    pub coverage: Coverage,
    pub grid: Grid,
    pub encoding: Encoding,
}

pub fn require_native() -> Result<()> {
    if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
        return Err("native layers require the native profile".into());
    }
    Ok(())
}
impl Grid {
    pub fn epsg(&self) -> Result<u32> {
        let epsg = self
            .crs
            .strip_prefix("EPSG:")
            .and_then(|s| s.parse::<u32>().ok())
            .filter(|n| *n > 0)
            .ok_or("grid CRS must be an EPSG code, for example EPSG:3857")?;
        if self.size.contains(&0)
            || self.size.iter().any(|n| *n > 2048)
            || self.bounds.iter().any(|n| !n.is_finite())
            || self.bounds[0] >= self.bounds[2]
            || self.bounds[1] >= self.bounds[3]
        {
            return Err(
                "grid requires finite increasing bounds and dimensions between 1 and 2048".into(),
            );
        }
        Ok(epsg)
    }
}
impl Asset {
    pub fn inspect(&mut self, gdal: &Gdal) -> Result<()> {
        if !crate::execution::is_remote(&self.location) {
            self.location = std::fs::canonicalize(&self.location)
                .map_err(|e| format!("cannot resolve raster location: {e}"))?;
        }
        self.metadata = Some(gdal.inspect_asset(&self.location)?);
        let bands = &self.metadata.as_ref().expect("just inspected").bands;
        if self.band_names.keys().any(|name| name.is_empty())
            || self
                .band_names
                .values()
                .chain(&self.categorical_bands)
                .any(|i| *i == 0 || *i > bands.len())
        {
            return Err(
                "asset band aliases/categories must refer to existing one-based bands".into(),
            );
        }
        Ok(())
    }
    pub(crate) fn band(&self, selector: &BandSelector) -> Result<&BandMetadata> {
        let metadata = self
            .metadata
            .as_ref()
            .ok_or("asset must be registered before rendering")?;
        let index = match selector {
            BandSelector::Index(index) => *index,
            BandSelector::Name(name) => {
                if let Some(index) = self.band_names.get(name) {
                    *index
                } else {
                    let matches = metadata
                        .bands
                        .iter()
                        .filter(|band| &band.name == name && !name.is_empty())
                        .collect::<Vec<_>>();
                    if matches.len() != 1 {
                        return Err(format!("band name '{name}' is missing or ambiguous"));
                    }
                    matches[0].index
                }
            }
        };
        metadata
            .bands
            .iter()
            .find(|band| band.index == index)
            .ok_or_else(|| format!("source band {index} is outside the asset's band count"))
    }
}
impl Layer {
    pub fn validate(&self) -> Result<()> {
        require_native()?;
        if self.observations.is_empty() || self.observations.len() > 64 {
            return Err("layer requires between 1 and 64 observations".into());
        }
        let mut ids = BTreeSet::new();
        for observation in &self.observations {
            if observation.id.is_empty() || !ids.insert(&observation.id) {
                return Err("observation IDs must be nonempty and unique".into());
            }
            if observation.assets.is_empty()
                || observation.assets.len() > 32
                || observation.assets.keys().any(|n| n.is_empty())
            {
                return Err("observation requires between 1 and 32 named assets".into());
            }
        }
        Ok(())
    }
    pub fn register(&mut self) -> Result<()> {
        self.validate()?;
        let gdal = Gdal::load()?;
        for observation in &mut self.observations {
            for asset in observation.assets.values_mut() {
                asset.inspect(&gdal)?;
            }
        }
        if let Some(spec) = &self.default_render {
            self.validate_spec(spec)?;
        }
        Ok(())
    }
    fn selected(&self) -> impl Iterator<Item = &Observation> {
        self.observations.iter().filter(|o| {
            self.selection
                .iter()
                .all(|(k, v)| o.attributes.get(k) == Some(v))
        })
    }
    fn validate_spec(&self, spec: &RenderSpec) -> Result<()> {
        self.validate()?;
        spec.grid.epsg()?;
        if spec.bindings.is_empty() || spec.bindings.len() > 16 {
            return Err("render requires between 1 and 16 bindings".into());
        }
        for name in spec.bindings.keys() {
            let mut chars = name.chars();
            if !chars
                .next()
                .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
                || !chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
            {
                return Err("binding names must be expression identifiers".into());
            }
        }
        if ![1, 3].contains(&spec.channels.len()) {
            return Err("render requires one or three channels".into());
        }
        for channel in &spec.channels {
            if channel.expression.is_empty() {
                return Err("channel expression is required".into());
            }
            if channel.categories.is_empty() {
                let range = channel
                    .stretch
                    .ok_or("continuous channels require a numeric stretch")?;
                if !range.iter().all(|v| v.is_finite()) || range[0] >= range[1] {
                    return Err("stretch requires finite increasing endpoints".into());
                }
            } else if spec.channels.len() != 1
                || channel.stretch.is_some()
                || channel.categories.len() > 255
                || channel.categories.iter().any(|c| !c.value.is_finite())
            {
                return Err("category colors require one channel, at most 255 finite values, and no stretch".into());
            }
            for (index, category) in channel.categories.iter().enumerate() {
                if channel.categories[..index]
                    .iter()
                    .any(|other| other.value == category.value)
                {
                    return Err("category values must be unique".into());
                }
            }
        }
        let mut selected = false;
        for observation in self.selected() {
            selected = true;
            for binding in spec.bindings.values() {
                let asset = observation.assets.get(&binding.asset).ok_or_else(|| {
                    format!(
                        "unknown asset '{}' in observation '{}'",
                        binding.asset, observation.id
                    )
                })?;
                let band = asset.band(&binding.band)?;
                if (asset.categorical_bands.contains(&band.index)
                    || !band.categories.is_empty()
                    || band.color_interpretation == "Palette")
                    && matches!(spec.values, Values::Scaled)
                {
                    return Err("categorical bands require raw values".into());
                }
                if !band.scale.is_finite() || !band.offset.is_finite() {
                    return Err("band scale and offset must be finite".into());
                }
            }
        }
        if !selected {
            return Err("layer selection matches no observations".into());
        }
        // Compile and validate arithmetic with source dtypes before allocating grids or reading data.
        for observation in self.selected() {
            let rasters = spec
                .bindings
                .iter()
                .map(|(name, binding)| {
                    let band = observation.assets[&binding.asset].band(&binding.band)?;
                    Ok((
                        name.clone(),
                        Raster {
                            data_type: if matches!(spec.values, Values::Scaled) {
                                7
                            } else {
                                band.data_type
                            },
                            width: 1,
                            height: 1,
                            values: vec![1.],
                            mask: vec![false],
                        },
                    ))
                })
                .collect::<Result<BTreeMap<_, _>>>()?;
            let refs = rasters.iter().map(|(k, v)| (k.clone(), v)).collect();
            for channel in &spec.channels {
                expressions::evaluate(&channel.expression, &refs)?;
            }
        }
        Ok(())
    }
    pub fn samples(&self, executor: &RasterExecutor, spec: &RenderSpec) -> Result<Vec<Raster>> {
        self.validate_spec(spec)?;
        let [height, width] = spec.grid.size;
        let epsg = spec.grid.epsg()?;
        let mut outputs = spec
            .channels
            .iter()
            .map(|_| Raster {
                data_type: 7,
                width,
                height,
                values: vec![0.; width * height],
                mask: vec![true; width * height],
            })
            .collect::<Vec<_>>();
        for observation in self.selected() {
            let requests = spec
                .bindings
                .values()
                .map(|binding| {
                    let asset = &observation.assets[&binding.asset];
                    let band = asset.band(&binding.band)?;
                    use sha2::Digest;
                    let revision: [u8; 32] = sha2::Sha256::digest(asset.revision.as_bytes()).into();
                    Ok((
                        asset.location.clone(),
                        ReadRequest {
                            grid: Some(GridRead {
                                epsg,
                                revision,
                                resampling: if asset.categorical_bands.contains(&band.index)
                                    || !band.categories.is_empty()
                                    || band.color_interpretation == "Palette"
                                {
                                    Resampling::Nearest
                                } else {
                                    spec.grid.resampling
                                },
                            }),
                            band: band.index,
                            bounds_mercator: Some(spec.grid.bounds),
                            size: spec.grid.size,
                            preserve_values: false,
                        },
                    ))
                })
                .collect::<Result<Vec<_>>>()?;
            let reads = executor.read_many(&requests)?;
            let mut rasters = BTreeMap::new();
            for ((name, binding), raster) in spec.bindings.iter().zip(reads) {
                let mut raster = (*raster).clone();
                if matches!(spec.values, Values::Scaled) {
                    let band = observation.assets[&binding.asset].band(&binding.band)?;
                    for value in &mut raster.values {
                        *value = *value * band.scale + band.offset;
                    }
                    raster.data_type = 7;
                }
                rasters.insert(name.clone(), raster);
            }
            let refs = rasters.iter().map(|(k, v)| (k.clone(), v)).collect();
            for (channel, output) in spec.channels.iter().zip(&mut outputs) {
                let computed = expressions::evaluate(&channel.expression, &refs)?;
                for i in 0..width * height {
                    if !computed.mask[i]
                        && (output.mask[i] || matches!(self.overlap, Overlap::LastValid))
                    {
                        output.values[i] = computed.values[i];
                        output.mask[i] = false;
                    }
                }
            }
        }
        Ok(outputs)
    }
    pub fn render(&self, executor: &RasterExecutor, spec: &RenderSpec) -> Result<Vec<u8>> {
        let rasters = self.samples(executor, spec)?;
        let [height, width] = spec.grid.size;
        if !spec.channels[0].categories.is_empty() {
            let colors = spec.channels[0]
                .categories
                .iter()
                .map(|c| (c.value, c.color))
                .collect::<Vec<_>>();
            return image::palette_png(
                width,
                height,
                &image::label_values(&rasters[0], &colors),
                &colors.iter().map(|(_, c)| *c).collect::<Vec<_>>(),
            );
        }
        let pixels = rasters
            .iter()
            .zip(&spec.channels)
            .map(|(r, c)| image::native_normalized_pixels(r, c.stretch.expect("validated stretch")))
            .collect::<Vec<_>>();
        let mut rgba = Vec::with_capacity(width * height * 4);
        for i in 0..width * height {
            let valid = match spec.coverage {
                Coverage::AnyValid => rasters.iter().any(|r| !r.mask[i]),
                Coverage::AllValid => rasters.iter().all(|r| !r.mask[i]),
            };
            if pixels.len() == 1 {
                rgba.extend_from_slice(&[
                    pixels[0][i],
                    pixels[0][i],
                    pixels[0][i],
                    if valid { 255 } else { 0 },
                ]);
            } else {
                rgba.extend_from_slice(&[
                    pixels[0][i],
                    pixels[1][i],
                    pixels[2][i],
                    if valid { 255 } else { 0 },
                ]);
            }
        }
        image::rgba_png(width, height, &rgba)
    }
    pub fn from_catalog(
        store: &mut crate::storage::Store,
        bindings: &BTreeMap<String, Vec<String>>,
    ) -> Result<Self> {
        require_native()?;
        store.refresh()?;
        let mut assets = BTreeMap::new();
        for (name, keys) in bindings {
            let dataset = store.dataset(keys)?.ok_or("No dataset found")?;
            assets.insert(
                name.clone(),
                Asset {
                    location: dataset.path,
                    revision: String::new(),
                    band_names: BTreeMap::new(),
                    categorical_bands: BTreeSet::new(),
                    metadata: None,
                },
            );
        }
        let mut layer = Self {
            observations: vec![Observation {
                id: "catalog".into(),
                assets,
                attributes: BTreeMap::new(),
            }],
            overlap: Overlap::FirstValid,
            selection: BTreeMap::new(),
            default_render: None,
        };
        layer.register()?;
        Ok(layer)
    }
}

pub fn inspect(path: &Path) -> Result<AssetMetadata> {
    require_native()?;
    Gdal::load()?.inspect_asset(path)
}

/// Register an ordinary one-file layer without duplicating raster files.
pub fn raster_layer(
    path: &Path,
    bands: Option<Vec<usize>>,
    stretch: Option<[f64; 2]>,
) -> Result<Layer> {
    require_native()?;
    let gdal = Gdal::load()?;
    let mut asset = Asset {
        location: path.to_path_buf(),
        revision: String::new(),
        band_names: BTreeMap::new(),
        categorical_bands: BTreeSet::new(),
        metadata: None,
    };
    asset.inspect(&gdal)?;
    let metadata = asset.metadata.as_ref().expect("inspected");
    let bands = bands.unwrap_or_else(|| {
        let rgb = ["Red", "Green", "Blue"].map(|color| {
            metadata
                .bands
                .iter()
                .find(|b| b.color_interpretation == color)
                .map(|b| b.index)
        });
        if rgb.iter().all(Option::is_some) {
            rgb.into_iter().flatten().collect()
        } else if metadata.bands.len() >= 3 {
            vec![1, 2, 3]
        } else {
            vec![1]
        }
    });
    if ![1, 3].contains(&bands.len()) || bands.iter().any(|i| *i == 0 || *i > metadata.bands.len())
    {
        return Err("register requires one or three existing one-based bands".into());
    }
    let range = match stretch {
        Some(range) => range,
        None if bands.iter().all(|i| metadata.bands[*i - 1].data_type == 1) => [0., 255.],
        None => return Err("non-Byte bands require an explicit stretch".into()),
    };
    let bounds = gdal.asset_map_bounds(path)?;
    let bindings = bands
        .iter()
        .enumerate()
        .map(|(i, b)| {
            (
                format!("v{}", i + 1),
                Binding {
                    asset: "image".into(),
                    band: BandSelector::Index(*b),
                },
            )
        })
        .collect();
    let channels = bands
        .iter()
        .enumerate()
        .map(|(i, _)| Channel {
            expression: format!("v{}", i + 1),
            stretch: Some(range),
            categories: Vec::new(),
        })
        .collect();
    let spec = RenderSpec {
        bindings,
        channels,
        values: Values::Raw,
        coverage: Coverage::AnyValid,
        grid: Grid {
            crs: "EPSG:3857".into(),
            bounds,
            size: [256, 256],
            resampling: Resampling::Bilinear,
        },
        encoding: Encoding::Png,
    };
    let layer = Layer {
        observations: vec![Observation {
            id: "image".into(),
            assets: BTreeMap::from([("image".into(), asset)]),
            attributes: BTreeMap::new(),
        }],
        overlap: Overlap::FirstValid,
        selection: BTreeMap::new(),
        default_render: Some(spec),
    };
    layer.validate_spec(layer.default_render.as_ref().expect("default render"))?;
    Ok(layer)
}

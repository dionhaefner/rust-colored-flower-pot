//! STAC source discovery for the native HTTP catalog.
use super::*;
use std::path::Path;

#[derive(Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ServeOptions {
    #[serde(skip)]
    pub item: Option<String>,
    #[serde(skip)]
    pub catalog: Option<String>,
    #[serde(skip)]
    pub search: Option<String>,
    assets: Vec<String>,
    #[serde(skip)]
    query: serde_json::Map<String, Value>,
    revisions: Option<PathBuf>,
    headers: Option<PathBuf>,
    asset_urls: Option<PathBuf>,
    max_items: Option<usize>,
    max_documents: Option<usize>,
    max_staged_bytes: Option<u64>,
}

impl ServeOptions {
    pub fn parse_option(&mut self, args: &[String], index: &mut usize) -> Result<()> {
        let option = args[*index].as_str();
        let value = args.get(*index + 1).ok_or("missing STAC option value")?;
        match option {
            "--stac-item" => self.item = Some(value.clone()),
            "--stac-catalog" => self.catalog = Some(value.clone()),
            "--stac-search" => self.search = Some(value.clone()),
            "--stac-assets" => self.assets = value.split(',').map(str::to_owned).collect(),
            "--stac-bbox" => {
                let bounds = value
                    .split(',')
                    .map(str::parse::<f64>)
                    .collect::<std::result::Result<Vec<_>, _>>()
                    .map_err(|_| "invalid STAC bbox")?;
                self.query.insert("bbox".into(), json!(bounds));
            }
            "--stac-datetime" => {
                self.query.insert("datetime".into(), json!(value));
            }
            "--stac-collections" => {
                self.query.insert(
                    "collections".into(),
                    json!(value.split(',').collect::<Vec<_>>()),
                );
            }
            "--stac-revisions" => self.revisions = Some(value.into()),
            "--stac-headers" => self.headers = Some(value.into()),
            "--stac-asset-urls" => self.asset_urls = Some(value.into()),
            "--stac-max-items" => {
                self.max_items = Some(value.parse().map_err(|_| "invalid STAC item limit")?)
            }
            "--stac-max-documents" => {
                self.max_documents = Some(value.parse().map_err(|_| "invalid STAC document limit")?)
            }
            "--stac-max-staged-bytes" => {
                self.max_staged_bytes =
                    Some(value.parse().map_err(|_| "invalid STAC staging budget")?)
            }
            _ => return Err("unknown STAC serve option; use rcfp serve --help".into()),
        }
        *index += 2;
        Ok(())
    }

    pub fn has_options(&self) -> bool {
        self.item.is_some()
            || self.catalog.is_some()
            || self.search.is_some()
            || !self.assets.is_empty()
            || !self.query.is_empty()
            || self.revisions.is_some()
            || self.headers.is_some()
            || self.asset_urls.is_some()
            || self.max_items.is_some()
            || self.max_documents.is_some()
            || self.max_staged_bytes.is_some()
    }

    pub(crate) fn resolve_location(
        mut self,
        location: &str,
        search: Option<Value>,
    ) -> Result<Service> {
        crate::compatibility::CompatibilityProfile::current().require_native()?;
        if let Some(query) = search {
            self.query = query
                .as_object()
                .ok_or("source search must be a JSON object")?
                .clone();
            self.search = Some(location.to_owned());
            return self.resolve();
        }
        let options = self.selection_options()?;
        let selection = with_refresh(self.headers.as_deref(), |headers| {
            Selection::from_source(
                location,
                headers,
                options.clone(),
                self.max_documents.unwrap_or(10000),
            )
        })?;
        Ok(Service::new(selection, self.asset_urls))
    }

    fn selection_options(&self) -> Result<Options> {
        Ok(Options {
            discover_assets: self.assets.is_empty(),
            assets: self.assets.clone(),
            revisions: read_mapping(self.revisions.as_deref())?,
            max_items: self.max_items.unwrap_or(1000),
            max_staged_bytes: self.max_staged_bytes.unwrap_or(512 * 1024 * 1024),
            ..Default::default()
        })
    }

    pub(crate) fn relative_paths(&mut self, base: &Path) {
        for path in [&mut self.revisions, &mut self.headers, &mut self.asset_urls]
            .into_iter()
            .flatten()
        {
            if path.is_relative() {
                *path = base.join(&*path);
            }
        }
    }

    pub fn resolve(self) -> Result<Service> {
        crate::compatibility::CompatibilityProfile::current().require_native()?;
        if self.search.is_none() && !self.query.is_empty() {
            return Err("STAC bbox/time/collection filters require --stac-search".into());
        }
        if self.catalog.is_none() && self.max_documents.is_some() {
            return Err("STAC document limit requires --stac-catalog".into());
        }
        let options = self.selection_options()?;
        let selection = with_refresh(self.headers.as_deref(), |headers| {
            match (&self.item, &self.catalog, &self.search) {
                (Some(item), None, None) => {
                    Selection::from_document(item, headers, options.clone())
                }
                (None, Some(root), None) => Selection::from_catalog(
                    root,
                    headers,
                    options.clone(),
                    self.max_documents.unwrap_or(10000),
                ),
                (None, None, Some(endpoint)) => Selection::search(
                    endpoint,
                    Value::Object(self.query.clone()),
                    headers,
                    options.clone(),
                ),
                _ => Err("choose one of --stac-item, --stac-catalog or --stac-search".into()),
            }
        })?;
        Ok(Service::new(selection, self.asset_urls))
    }
}

fn read_mapping(path: Option<&Path>) -> Result<BTreeMap<String, String>> {
    let Some(path) = path else {
        return Ok(BTreeMap::new());
    };
    let file = std::fs::File::open(path).map_err(|_| "STAC mapping file unavailable")?;
    let mut bytes = Vec::new();
    file.take(PAGE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "STAC mapping file read failure")?;
    if bytes.len() as u64 > PAGE_BYTES {
        return Err("STAC mapping file exceeds byte limit".into());
    }
    serde_json::from_slice(&bytes)
        .map_err(|_| "STAC mapping file must be a JSON object of strings".into())
}

fn with_refresh<T>(
    path: Option<&Path>,
    mut operation: impl FnMut(&BTreeMap<String, String>) -> Result<T>,
) -> Result<T> {
    for attempt in 0..2 {
        match operation(&read_mapping(path)?) {
            Err(e)
                if attempt == 0
                    && path.is_some()
                    && matches!(
                        e.as_str(),
                        "STAC upstream HTTP 401" | "STAC upstream HTTP 403"
                    ) => {}
            result => return result,
        }
    }
    unreachable!("the second attempt returns")
}

pub struct Service {
    pub(crate) selection: Selection,
    asset_urls: Option<PathBuf>,
    metadata: Mutex<BTreeMap<(usize, usize), (PathBuf, crate::storage::Metadata)>>,
}
impl Service {
    pub(crate) fn new(selection: Selection, asset_urls: Option<PathBuf>) -> Self {
        Self {
            selection,
            asset_urls,
            metadata: Mutex::new(BTreeMap::new()),
        }
    }

    pub(crate) fn assets(&self) -> &[Asset] {
        &self.selection.assets
    }

    pub(crate) fn descriptor_band(
        &self,
        index: usize,
        band_index: usize,
    ) -> Result<(PathBuf, crate::storage::Metadata)> {
        let mut entries = self
            .metadata
            .lock()
            .map_err(|_| "STAC metadata lock poisoned")?;
        if let Some(descriptor) = entries.get(&(index, band_index)) {
            return Ok(descriptor.clone());
        }
        let asset = &self.selection.assets[index];
        let key = format!("{}/{}/{}", asset.collection, asset.item, asset.asset);
        let path = with_refresh(self.asset_urls.as_deref(), |locations| {
            let signed = if self.asset_urls.is_some() {
                Some(
                    locations
                        .get(&key)
                        .ok_or("STAC signed asset mapping is missing an asset")?
                        .as_str(),
                )
            } else {
                None
            };
            self.selection.materialize(asset, signed)
        })?;
        let band = asset
            .bands
            .get(band_index.wrapping_sub(1))
            .ok_or("source band out of range")?;
        validate_band(&path, band)?;
        let mut metadata = crate::raster::Gdal::load()?.compute_band_metadata(
            &path,
            band_index,
            &crate::raster::MetadataOptions {
                max_shape: Some(
                    crate::settings::var("TC_LAZY_LOADING_MAX_SHAPE")
                        .map(|value| {
                            serde_json::from_str::<[usize; 2]>(&value)
                                .map_err(|_| "invalid lazy metadata shape")
                        })
                        .unwrap_or(Ok([1024, 1024]))?,
                ),
                extra_metadata: json!({"stac": asset,
                "selection_id": self.selection.id, "search_id": self.selection.search_id,
                "selection_metrics": self.selection.metrics}),
                ..Default::default()
            },
        )?;
        crate::catalog::plan::calibrate_metadata(&mut metadata, band.scale, band.offset)?;
        metadata.metadata["band"] = json!({"index": band_index,"name": band.name,"common_name":band.common_name,"scale":band.scale,"offset":band.offset,"values":"scaled"});
        // Bound retained statistics separately from discovery and staging budgets.
        if entries.len() >= 256 {
            entries.pop_first();
        }
        entries.insert((index, band_index), (path.clone(), metadata.clone()));
        Ok((path, metadata))
    }
}

impl Service {
    pub(crate) fn source_band(
        &self,
        index: usize,
        band_index: usize,
        path: &Path,
    ) -> Result<crate::catalog::Source> {
        let mut asset = crate::layers::Asset {
            location: path.into(),
            revision: self.selection.assets[index].revision.clone(),
            band_names: Default::default(),
            categorical_bands: Default::default(),
            metadata: Some(crate::raster::Gdal::load()?.inspect_asset(path)?),
        };
        let declared = &self.selection.assets[index].bands[band_index - 1];
        let band = asset
            .metadata
            .as_mut()
            .unwrap()
            .bands
            .get_mut(band_index - 1)
            .ok_or("source band out of range")?;
        band.scale = declared.scale;
        band.offset = declared.offset;
        crate::catalog::Source::band(asset, band_index, crate::layers::Values::Scaled)
    }
    pub(crate) fn snapshot(&self) -> Value {
        let mut items = BTreeMap::<(String, String), Value>::new();
        for asset in self.assets() {
            let key = (asset.collection.clone(), asset.item.clone());
            let item=items.entry(key).or_insert_with(||json!({"type":"Feature","stac_version":"1.0.0","id":asset.item,"collection":asset.collection,"properties":{"datetime":asset.datetime},"geometry":asset.geometry,"bbox":asset.bbox,"assets":{},"links":[]}));
            item["properties"]["proj:code"] = asset.crs.clone();
            if asset.end_datetime != asset.datetime {
                item["properties"]["datetime"] = Value::Null;
                item["properties"]["start_datetime"] = json!(asset.datetime);
                item["properties"]["end_datetime"] = json!(asset.end_datetime);
            }
            item["assets"][&asset.asset] = json!({"href":asset.href,"file:checksum":format!("1220{}",asset.revision),"raster:bands":asset.bands.iter().map(|b|json!({"scale":b.scale,"offset":b.offset,"nodata":b.nodata})).collect::<Vec<_>>(),"eo:bands":asset.bands.iter().map(|b|json!({"name":b.name,"common_name":b.common_name})).collect::<Vec<_>>()});
        }
        json!({"items":items.into_values().collect::<Vec<_>>(),"max_staged_bytes":self.selection.options.max_staged_bytes,"asset_urls":self.asset_urls,"selection_id":self.selection.id,"search_id":self.selection.search_id})
    }
    pub(crate) fn from_snapshot(value: &Value) -> Result<Self> {
        let items = value["items"]
            .as_array()
            .ok_or("stored source has no Items")?;
        let options = Options {
            discover_assets: true,
            max_items: items.len().max(1),
            max_staged_bytes: value["max_staged_bytes"]
                .as_u64()
                .ok_or("stored source has no staging limit")?,
            ..Default::default()
        };
        Ok(Self::new(
            Selection::from_items(items, options)?,
            value["asset_urls"].as_str().map(PathBuf::from),
        ))
    }
}

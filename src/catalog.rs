//! Keyed catalogs and logical dataset plans shared by application entry points.
use crate::{
    Result,
    execution::RasterExecutor,
    raster::{Raster, ReadRequest},
    storage::{Dataset, KeyDefinition, Metadata, Store},
};
use serde_json::{Value, json};
use sha2::Digest;
use std::{cell::RefCell, collections::BTreeMap, path::PathBuf, sync::Arc};

pub(crate) mod dataset;
pub(crate) mod plan;
mod source;
mod write;
use dataset::Definition;
use plan::Plan;
pub(crate) use source::Opened;

pub(crate) enum Catalog {
    Sql(Store),
    Native {
        store: Store,
        sources: RefCell<BTreeMap<String, Box<Catalog>>>,
        derived: RefCell<BTreeMap<String, Metadata>>,
    },
    Layers {
        store: crate::layers::catalog::Catalog,
        keys: Vec<KeyDefinition>,
    },
    Stac {
        service: crate::stac::serve::Service,
        keys: Vec<KeyDefinition>,
        entries: BTreeMap<Vec<String>, (usize, usize)>,
    },
}
impl Catalog {
    pub fn open(location: &std::path::Path) -> Result<Opened> {
        source::open(location)
    }
    pub fn sql(store: Store) -> Result<Self> {
        Ok(if store.db_version()? == dataset::FORMAT {
            Self::Native {
                store,
                sources: RefCell::new(BTreeMap::new()),
                derived: RefCell::new(BTreeMap::new()),
            }
        } else {
            Self::Sql(store)
        })
    }
    pub fn layers(path: &std::path::Path) -> Result<Self> {
        Ok(Self::Layers {
            store: crate::layers::catalog::Catalog::open(path, false)?,
            keys: ["layer", "observation", "channel"]
                .map(|key| KeyDefinition {
                    key: key.into(),
                    description: String::new(),
                })
                .to_vec(),
        })
    }
    pub fn stac(service: crate::stac::serve::Service) -> Result<Self> {
        let keys = ["collection", "datetime", "item", "asset"]
            .map(|key| KeyDefinition {
                key: key.into(),
                description: String::new(),
            })
            .to_vec();
        let mut entries = BTreeMap::new();
        for (index, asset) in service.assets().iter().enumerate() {
            let collection = if asset.collection.is_empty() {
                "~".into()
            } else if asset.collection.starts_with('~') {
                format!("~{}", asset.collection)
            } else {
                asset.collection.clone()
            };
            for band in &asset.bands {
                let name = band
                    .common_name
                    .as_ref()
                    .or(band.name.as_ref())
                    .filter(|s| !s.is_empty())
                    .cloned()
                    .or_else(|| (asset.bands.len() == 1).then(|| asset.asset.clone()))
                    .ok_or("multiband STAC assets require unique logical band names")?;
                let values = vec![
                    collection.clone(),
                    asset.datetime.clone(),
                    asset.item.clone(),
                    name,
                ];
                if entries.insert(values, (index, band.band)).is_some() {
                    return Err(
                        "duplicate STAC logical dataset keys; supply distinct band names".into(),
                    );
                }
            }
        }
        Ok(Self::Stac {
            service,
            keys,
            entries,
        })
    }
    pub fn refresh(&mut self) -> Result<()> {
        match self {
            Self::Sql(s) => s.refresh(),
            Self::Native { store, .. } => store.refresh(),
            _ => Ok(()),
        }
    }
    pub fn keys(&self) -> &[KeyDefinition] {
        match self {
            Self::Sql(s) => s.keys(),
            Self::Native { store, .. } => store.keys(),
            Self::Stac { keys, .. } | Self::Layers { keys, .. } => keys,
        }
    }
    pub fn key_names(&self) -> Vec<&str> {
        self.keys().iter().map(|key| key.key.as_str()).collect()
    }
    pub fn revision(&self) -> Option<&str> {
        match self {
            Self::Stac { service, .. } => Some(&service.selection.id),
            _ => None,
        }
    }
    pub fn format(&self) -> &str {
        match self {
            Self::Native { .. } => dataset::FORMAT,
            Self::Layers { .. } => crate::layers::catalog::FORMAT,
            _ => crate::DATABASE_COMPATIBILITY,
        }
    }
    pub fn legacy_decimal_stretch(&self) -> bool {
        matches!(self,Self::Sql(store) if store.legacy_decimal_stretch())
    }
    pub fn query_datasets(
        &self,
        filters: &BTreeMap<String, Vec<String>>,
        order: &[String],
        ascending: bool,
        pagination: Option<(usize, usize)>,
    ) -> Result<Vec<Dataset>> {
        let data = match self {
            Self::Sql(s) | Self::Native { store: s, .. } => {
                return s.query_datasets(filters, order, ascending, pagination);
            }
            Self::Stac {
                entries, service, ..
            } => entries
                .iter()
                .map(|(keys, (index, _))| Dataset {
                    keys: keys.clone(),
                    path: PathBuf::from(&service.assets()[*index].revision),
                })
                .collect(),
            Self::Layers { store, .. } => {
                let mut data = Vec::new();
                for id in store.ids()? {
                    let layer = store.get(&id)?.ok_or("No layer found")?;
                    if let Some(spec) = &layer.default_render {
                        for observation in &layer.observations {
                            for i in 0..spec.channels.len() {
                                data.push(Dataset {
                                    keys: vec![
                                        id.clone(),
                                        observation.id.clone(),
                                        (i + 1).to_string(),
                                    ],
                                    path: PathBuf::from("layer"),
                                });
                            }
                        }
                    }
                }
                data
            }
        };
        crate::storage::query_datasets(
            data,
            &self.key_names(),
            filters,
            order,
            ascending,
            pagination,
        )
    }
    pub fn descriptor(&self, keys: &[String]) -> Result<Option<(Dataset, Metadata, Source)>> {
        self.resolve(keys, &mut Vec::new())
    }
    fn resolve(
        &self,
        keys: &[String],
        stack: &mut Vec<Vec<String>>,
    ) -> Result<Option<(Dataset, Metadata, Source)>> {
        if stack.len() >= 32 || stack.iter().any(|k| k == keys) {
            return Err("cyclic or excessively deep dataset expression".into());
        }
        stack.push(keys.to_vec());
        let result = self.resolve_inner(keys, stack);
        stack.pop();
        result
    }
    fn resolve_inner(
        &self,
        keys: &[String],
        stack: &mut Vec<Vec<String>>,
    ) -> Result<Option<(Dataset, Metadata, Source)>> {
        match self {
            Self::Sql(store) => Ok(store.descriptor(keys)?.map(|(d, m)| {
                let source = Source {
                    path: d.path.clone(),
                    plan: None,
                    bounds: None,
                };
                (d, m, source)
            })),
            Self::Native {
                store,
                sources,
                derived,
            } => {
                let Some(mut d) = store.dataset(keys)? else {
                    return Ok(None);
                };
                let definition: Definition = serde_json::from_value(
                    store
                        .definition(keys)?
                        .ok_or("native dataset has no binding")?,
                )
                .map_err(|e| e.to_string())?;
                let source = match &definition {
                    Definition::Band {
                        asset,
                        band,
                        values,
                        ..
                    } => Source::band(asset.as_ref().clone(), *band, *values)?,
                    Definition::Expression {
                        expression,
                        bindings,
                        ..
                    } => {
                        let inputs = bindings
                            .iter()
                            .map(|(var, keys)| {
                                Ok((
                                    var.clone(),
                                    self.resolve(keys, stack)?
                                        .ok_or("expression input dataset is missing")?
                                        .2,
                                ))
                            })
                            .collect::<Result<_>>()?;
                        Source::expression(expression.clone(), inputs)?
                    }
                    Definition::Catalog { source, keys, .. } => {
                        let config = store
                            .source_definition(source)?
                            .ok_or("stored source is missing")?;
                        let identity = crate::stac::identity(&config);
                        let mut cache = sources.borrow_mut();
                        if !cache.contains_key(&identity) {
                            if cache.len() >= 64 {
                                return Err("stored source generation limit exceeded; restart the application".into());
                            }
                            cache.insert(
                                identity.clone(),
                                Box::new(Self::stac(crate::stac::serve::Service::from_snapshot(
                                    &config,
                                )?)?),
                            );
                        }
                        cache[&identity]
                            .descriptor(keys)?
                            .ok_or("stored source dataset is missing")?
                            .2
                    }
                };
                let stored = store.metadata_stored(keys)?;
                let mut m = if matches!(definition, Definition::Expression { .. }) {
                    let identity = source.identity();
                    let cached = derived.borrow().get(&identity).cloned();
                    if let Some(m) = cached {
                        m
                    } else {
                        let m = match stored {
                            Some(m)
                                if m.metadata["statistics_revision"].as_str()
                                    == Some(&identity) =>
                            {
                                m
                            }
                            _ => source.metadata()?,
                        };
                        let mut cache = derived.borrow_mut();
                        if cache.len() >= 256 {
                            cache.pop_first();
                        }
                        cache.insert(identity, m.clone());
                        m
                    }
                } else {
                    match stored {
                        Some(m) => m,
                        None => source.metadata()?,
                    }
                };
                m.keys = self
                    .key_names()
                    .iter()
                    .zip(keys)
                    .map(|(k, v)| (k.to_string(), v.clone()))
                    .collect();
                m.metadata["defaults"] =
                    serde_json::to_value(definition.style()).map_err(|e| e.to_string())?;
                m.metadata["revision"] = json!(source.identity());
                // Encoded caches see the complete plan and every local generation.
                d.path = source.cache_path();
                Ok(Some((d, m, source)))
            }
            Self::Stac {
                service, entries, ..
            } => {
                let Some(&(index, band)) = entries.get(keys) else {
                    return Ok(None);
                };
                let (path, mut m) = service.descriptor_band(index, band)?;
                let source = service.source_band(index, band, &path)?;
                m.keys = self
                    .key_names()
                    .iter()
                    .zip(keys)
                    .map(|(k, v)| (k.to_string(), v.clone()))
                    .collect();
                m.metadata["revision"] = json!(source.identity());
                Ok(Some((
                    Dataset {
                        keys: keys.to_vec(),
                        path,
                    },
                    m,
                    source,
                )))
            }
            Self::Layers { store, .. } => {
                if keys.len() != 3 {
                    return Err("must specify every catalog key".into());
                };
                let Some(mut layer) = store.get(&keys[0])? else {
                    return Ok(None);
                };
                layer.observations.retain(|o| o.id == keys[1]);
                if layer.observations.is_empty() {
                    return Ok(None);
                };
                let Some(mut spec) = layer.default_render.clone() else {
                    return Ok(None);
                };
                let i = keys[2]
                    .parse::<usize>()
                    .ok()
                    .and_then(|i| i.checked_sub(1))
                    .ok_or("invalid logical channel")?;
                let Some(channel) = spec.channels.get(i).cloned() else {
                    return Ok(None);
                };
                spec.channels = vec![channel];
                let bounds = crate::raster::Gdal::load()?.asset_map_bounds(
                    &layer.observations[0]
                        .assets
                        .values()
                        .next()
                        .ok_or("layer has no assets")?
                        .location,
                )?;
                let source = Source {
                    path: PathBuf::new(),
                    plan: Some(Plan::Layer { layer, spec }),
                    bounds: Some(bounds),
                };
                let mut m = source.metadata()?;
                m.keys = self
                    .key_names()
                    .iter()
                    .zip(keys)
                    .map(|(k, v)| (k.to_string(), v.clone()))
                    .collect();
                m.metadata["revision"] = json!(source.identity());
                Ok(Some((
                    Dataset {
                        keys: keys.to_vec(),
                        path: source.cache_path(),
                    },
                    m,
                    source,
                )))
            }
        }
    }
    pub fn metadata_json(&self, keys: &[String]) -> Result<Option<Value>> {
        if let Self::Sql(store) = self {
            return store.metadata_json(keys);
        };
        self.descriptor(keys)?
            .map(|(_, m, _)| serde_json::to_value(m).map_err(|e| e.to_string()))
            .transpose()
    }
}

#[derive(Clone)]
pub(crate) struct Source {
    path: PathBuf,
    plan: Option<Plan>,
    bounds: Option<[f64; 4]>,
}
impl Source {
    pub fn validate(&self, band: usize) -> Result<()> {
        if self.mapped() && band != 1 {
            return Err(
                "logical datasets select their stored band; omit physical band parameters".into(),
            );
        }
        Ok(())
    }
    pub fn read(&self, executor: &RasterExecutor, request: ReadRequest) -> Result<Arc<Raster>> {
        self.validate(request.band)?;
        self.planned_read(executor, request)
    }
    pub fn identity(&self) -> String {
        let description = match &self.plan {
            Some(Plan::Band {
                asset,
                band,
                values,
            }) => {
                json!({"asset":asset,"band":band,"values":values,"generation":crate::execution::source_generation(&asset.location)})
            }
            Some(Plan::Expression {
                expression,
                bindings,
            }) => {
                json!({"expression":expression,"bindings":bindings.iter().map(|(k,v)|(k,v.identity())).collect::<BTreeMap<_,_>>()})
            }
            Some(Plan::Layer { layer, spec }) => {
                json!({"layer":layer,"spec":spec,"generations":layer.observations.iter().flat_map(|o|o.assets.values()).map(|a|crate::execution::source_generation(&a.location)).collect::<Vec<_>>()})
            }
            None => {
                json!({"path":self.path,"generation":crate::execution::source_generation(&self.path)})
            }
        };
        format!("{:x}", sha2::Sha256::digest(description.to_string()))
    }
    pub(crate) fn cache_path(&self) -> PathBuf {
        if matches!(
            self.plan,
            Some(Plan::Expression { .. }) | Some(Plan::Layer { .. })
        ) {
            PathBuf::from(self.identity())
        } else {
            self.path.clone()
        }
    }
}

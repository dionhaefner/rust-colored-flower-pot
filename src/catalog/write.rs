//! Keyed ingestion and database transactions for native catalogs.
use super::*;
use crate::layers::{Asset, Values};
use dataset::{Definition, Style};
use std::path::Path;

impl Catalog {
    pub fn operation(&self, request: &Value) -> Result<Value> {
        let op = request["operation"].as_str().ok_or("operation required")?;
        if let Self::Sql(store) = self {
            return crate::cli::store_operation(store, request);
        }
        match op {
            "keys" => return Ok(json!(self.keys())),
            "datasets" => {
                let filters =
                    serde_json::from_value(request.get("where").cloned().unwrap_or(json!({})))
                        .map_err(|e| e.to_string())?;
                let order: Vec<String> = request
                    .get("order_by")
                    .map(|v| serde_json::from_value(v.clone()).map_err(|e| e.to_string()))
                    .transpose()?
                    .unwrap_or_else(|| self.key_names().iter().map(|k| k.to_string()).collect());
                return Ok(json!(
                    self.query_datasets(
                        &filters,
                        &order,
                        request["ascending"].as_bool().unwrap_or(true),
                        request["limit"]
                            .as_u64()
                            .map(|n| (request["page"].as_u64().unwrap_or(0) as usize, n as usize))
                    )?
                    .into_iter()
                    .map(|d| json!({"keys":d.keys,"path":d.path}))
                    .collect::<Vec<_>>()
                ));
            }
            "metadata" => {
                return Ok(self
                    .metadata_json(
                        &serde_json::from_value::<Vec<String>>(request["keys"].clone())
                            .map_err(|e| e.to_string())?,
                    )?
                    .unwrap_or(Value::Null));
            }
            _ => {}
        }
        let Self::Native { store, .. } = self else {
            return Err("source catalog is read-only".into());
        };
        if op == "batch" {
            return store.transaction(|_| {
                request["operations"]
                    .as_array()
                    .ok_or("operations required")?
                    .iter()
                    .map(|r| self.operation(r))
                    .collect::<Result<Vec<_>>>()
                    .map(|v| json!(v))
            });
        }
        if op == "ingest_source" {
            return self.ingest_source(request);
        }
        if op == "version" {
            return Ok(json!(dataset::FORMAT));
        }
        if op == "delete" || op == "metadata_stored" {
            return crate::cli::store_operation(store, request);
        }
        let keys: Vec<String> =
            serde_json::from_value(request["keys"].clone()).map_err(|e| e.to_string())?;
        if keys.len() != self.keys().len() {
            return Err("must specify every catalog key".into());
        }
        let style: Style =
            serde_json::from_value(request.get("style").cloned().unwrap_or(json!({})))
                .map_err(|e| e.to_string())?;
        style.validate()?;
        let (definition, source, path) = match op {
            "insert" => {
                let path = Path::new(request["raster"].as_str().ok_or("raster required")?);
                let mut asset = Asset {
                    location: path.to_path_buf(),
                    revision: request["revision"].as_str().unwrap_or("").into(),
                    band_names: Default::default(),
                    categorical_bands: Default::default(),
                    metadata: None,
                };
                asset.inspect(&crate::raster::Gdal::load()?)?;
                let band = request
                    .get("band")
                    .map(|v| {
                        v.as_u64()
                            .filter(|n| *n > 0)
                            .ok_or("band must be a positive integer")
                    })
                    .transpose()?
                    .unwrap_or(1) as usize;
                let values: Values =
                    serde_json::from_value(request.get("values").cloned().unwrap_or(json!("raw")))
                        .map_err(|e| e.to_string())?;
                let source = Source::band(asset.clone(), band, values)?;
                if let Some(location) = request["override_path"].as_str().filter(|s| !s.is_empty())
                {
                    asset.location = location.into();
                }
                let path = asset.location.clone();
                (
                    Definition::Band {
                        asset: Box::new(asset),
                        band,
                        values,
                        style,
                    },
                    source,
                    path,
                )
            }
            "define" => {
                let expression = request["expression"]
                    .as_str()
                    .ok_or("expression required")?
                    .to_owned();
                let bindings: BTreeMap<String, Vec<String>> =
                    serde_json::from_value(request["bindings"].clone())
                        .map_err(|e| e.to_string())?;
                let mut stack = vec![keys.clone()];
                let inputs = bindings
                    .iter()
                    .map(|(name, keys)| {
                        Ok((
                            name.clone(),
                            self.resolve(keys, &mut stack)?
                                .ok_or("expression input dataset is missing")?
                                .2,
                        ))
                    })
                    .collect::<Result<_>>()?;
                let source = Source::expression(expression.clone(), inputs)?;
                (
                    Definition::Expression {
                        expression,
                        bindings,
                        style,
                    },
                    source,
                    PathBuf::from("expression"),
                )
            }
            _ => return Err(format!("unknown data operation: {op}")),
        };
        let metadata = if request["skip_metadata"] == true {
            None
        } else {
            let mut metadata = if let Some(value) = request.get("metadata").filter(|v| !v.is_null())
            {
                serde_json::from_value(value.clone()).map_err(|e| e.to_string())?
            } else {
                source.metadata()?
            };
            metadata.metadata["statistics_revision"] = json!(source.identity());
            Some(metadata)
        };
        store.transaction(|store| {
            if store.dataset(&keys)?.is_some() {
                store.delete(&keys)?;
            }
            store.put_definition(
                &keys,
                &serde_json::to_value(definition).map_err(|e| e.to_string())?,
            )?;
            store.insert(&Dataset { keys, path }, metadata.as_ref())?;
            Ok(Value::Null)
        })
    }

    fn ingest_source(&self, request: &Value) -> Result<Value> {
        let Self::Native { store, .. } = self else {
            return Err("stored sources require a native keyed catalog".into());
        };
        let location = request["source"].as_str().ok_or("source required")?;
        let options: crate::stac::serve::ServeOptions =
            serde_json::from_value(request.get("options").cloned().unwrap_or(json!({})))
                .map_err(|e| e.to_string())?;
        let search = request.get("search").filter(|v| !v.is_null()).cloned();
        let source = Self::stac(options.resolve_location(location, search.clone())?)?;
        let Self::Stac {
            service, entries, ..
        } = &source
        else {
            unreachable!()
        };
        let mut snapshot = service.snapshot();
        snapshot["origin"] = json!({"source":location,"search":search,"options":request.get("options").cloned().unwrap_or(json!({}))});
        let id = crate::stac::identity(&snapshot);
        let mapping: BTreeMap<String, String> =
            serde_json::from_value(request.get("key_mapping").cloned().unwrap_or(json!({})))
                .map_err(|e| e.to_string())?;
        if mapping
            .keys()
            .any(|key| !self.key_names().contains(&key.as_str()))
        {
            return Err("key mapping contains unknown catalog keys".into());
        }
        let style: Style =
            serde_json::from_value(request.get("style").cloned().unwrap_or(json!({})))
                .map_err(|e| e.to_string())?;
        style.validate()?;
        let mut rows = Vec::new();
        let mut seen = std::collections::BTreeSet::new();
        for keys in entries.keys() {
            let target =
                self.key_names()
                    .iter()
                    .map(|key| {
                        let field = mapping.get(*key).map(String::as_str).unwrap_or(key);
                        let index = source.key_names().iter().position(|k| *k == field).ok_or(
                            "key mapping must refer to collection, datetime, item or asset",
                        )?;
                        Ok(keys[index].clone())
                    })
                    .collect::<Result<Vec<_>>>()?;
            if !seen.insert(target.clone()) {
                return Err("source key mapping creates duplicate datasets".into());
            }
            // Resolve and compute metadata before the transaction: staging errors cannot leave a partial import.
            let metadata = if request["skip_metadata"] == true {
                None
            } else {
                Some(
                    source
                        .descriptor(keys)?
                        .ok_or("source dataset is missing")?
                        .1,
                )
            };
            rows.push((
                target,
                Definition::Catalog {
                    source: id.clone(),
                    keys: keys.clone(),
                    style: style.clone(),
                },
                metadata,
            ));
        }
        store.transaction(|store| {
            store.put_source(&id, &snapshot)?;
            for (keys, definition, metadata) in &rows {
                if store.dataset(keys)?.is_some() {
                    store.delete(keys)?;
                }
                store.put_definition(
                    keys,
                    &serde_json::to_value(definition).map_err(|e| e.to_string())?,
                )?;
                store.insert(
                    &Dataset {
                        keys: keys.clone(),
                        path: PathBuf::from(&id),
                    },
                    metadata.as_ref(),
                )?;
            }
            Ok(json!(rows.len()))
        })
    }
}

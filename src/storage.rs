pub mod migrations;
mod native;
pub mod provider;
pub mod relational;
pub mod remote;
mod store;
pub use store::Store;
// Legacy SQLite encoding, transactional writes and coordinated lazy metadata.

use rusqlite::{
    Connection, OptionalExtension,
    types::{Value as SqlValue, ValueRef},
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};

use crate::{Result, TERRACOTTA_COMPATIBILITY_VERSION};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct KeyDefinition {
    pub key: String,
    pub description: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Dataset {
    pub keys: Vec<String>,
    pub path: PathBuf,
}

pub(crate) fn query_datasets(
    mut data: Vec<Dataset>,
    known: &[&str],
    filters: &BTreeMap<String, Vec<String>>,
    order: &[String],
    ascending: bool,
    pagination: Option<(usize, usize)>,
) -> Result<Vec<Dataset>> {
    for key in filters.keys().chain(order.iter()) {
        if key != "path" && !known.contains(&key.as_str()) {
            return Err("unrecognized dataset key".into());
        }
    }
    data.retain(|d| {
        known.iter().enumerate().all(|(i, k)| {
            filters
                .get(*k)
                .is_none_or(|v| v.is_empty() || v.contains(&d.keys[i]))
        })
    });
    data.sort_by(|a, b| {
        for k in order {
            let cmp = if k == "path" {
                a.path.as_os_str().cmp(b.path.as_os_str())
            } else {
                let i = known.iter().position(|v| *v == k).unwrap();
                a.keys[i].cmp(&b.keys[i])
            };
            if cmp != std::cmp::Ordering::Equal {
                return if ascending { cmp } else { cmp.reverse() };
            }
        }
        std::cmp::Ordering::Equal
    });
    if let Some((page, limit)) = pagination {
        data = data
            .into_iter()
            .skip(page.saturating_mul(limit))
            .take(limit)
            .collect();
    }
    Ok(data)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Metadata {
    #[serde(default)]
    pub keys: BTreeMap<String, String>,
    pub bounds: [f64; 4],
    pub convex_hull: Value,
    pub valid_percentage: f64,
    pub range: [f64; 2],
    pub mean: f64,
    pub stdev: f64,
    pub percentiles: Vec<f64>,
    pub metadata: Value,
}

pub struct SqliteStore {
    connection: Connection,
    path: PathBuf,
    lazy: bool,
    key_definitions: Vec<KeyDefinition>,
    descriptors: RefCell<DescriptorCache>,
}

struct DescriptorCache {
    version: Option<i64>,
    entries: BTreeMap<Vec<String>, (Dataset, Metadata, usize)>,
    order: VecDeque<Vec<String>>,
    bytes: usize,
    capacity: usize,
}
impl DescriptorCache {
    fn clear(&mut self) {
        self.entries.clear();
        self.order.clear();
        self.bytes = 0;
        self.version = None;
    }
}

impl SqliteStore {
    pub fn db_version(&self) -> Result<String> {
        if self.native_format()? {
            return Ok(crate::catalog::dataset::FORMAT.into());
        }
        self.connection
            .query_row("SELECT version FROM terracotta LIMIT 1", [], |row| {
                row.get(0)
            })
            .map_err(db_error)
    }

    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        Self::open_mode(path.as_ref(), false, true)
    }

    pub fn open_read_only(path: impl AsRef<Path>) -> Result<Self> {
        Self::open_mode(path.as_ref(), false, false)
    }

    pub fn open_writable(path: impl AsRef<Path>) -> Result<Self> {
        Self::open_mode(path.as_ref(), true, true)
    }

    fn open_mode(path: &Path, writable: bool, lazy: bool) -> Result<Self> {
        let capacity = crate::settings::var("TC_CATALOG_CACHE_BYTES")
            .unwrap_or_else(|_| "0".into())
            .parse::<usize>()
            .map_err(|_| "invalid catalog cache size")?;
        if capacity > 64 * 1024 * 1024 {
            return Err("catalog cache exceeds 64 MiB".into());
        }
        if !path.is_file() {
            return Err(format!("database does not exist: {}", path.display()));
        }
        let connection = Connection::open_with_flags(
            path,
            (if writable {
                rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE
            } else {
                rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
            }) | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|error| format!("could not open database: {error}"))?;
        let native_marker: bool = connection.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='rcfp_catalog')", [], |row| row.get(0)).map_err(db_error)?;
        if native_marker {
            let format: String = connection
                .query_row(
                    "SELECT MIN(format) FROM rcfp_catalog HAVING COUNT(*)=1",
                    [],
                    |row| row.get(0),
                )
                .map_err(|_| "invalid native catalog marker")?;
            if format != crate::catalog::dataset::FORMAT {
                return Err("native layer catalog cannot be opened as a Terracotta catalog; use LayerStore or /layers".into());
            }
            crate::compatibility::CompatibilityProfile::current().require_native()?;
        }
        connection
            .pragma_update(None, "query_only", !writable)
            .map_err(|error| format!("could not make database read-only: {error}"))?;

        connection
            .busy_timeout(std::time::Duration::from_secs(
                crate::settings::var("TC_DB_CONNECTION_TIMEOUT")
                    .ok()
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(10),
            ))
            .map_err(db_error)?;
        let version: String = if native_marker {
            crate::settings::protocol_version()
        } else {
            connection
                .query_row("SELECT version FROM terracotta LIMIT 1", [], |row| {
                    row.get(0)
                })
                .map_err(|error| format!("not a Terracotta database: {error}"))?
        };
        if major_minor(&version) != major_minor(&crate::settings::protocol_version()) {
            return Err(format!(
                "Version conflict: database was created in v{version}, but this is compatible with v{TERRACOTTA_COMPATIBILITY_VERSION}"
            ));
        }

        let key_definitions = {
            let mut statement = connection
                .prepare("SELECT key_name, description FROM key_names ORDER BY idx")
                .map_err(|error| format!("could not retrieve keys: {error}"))?;
            let rows = statement
                .query_map([], |row| {
                    Ok(KeyDefinition {
                        key: row.get(0)?,
                        description: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    })
                })
                .map_err(|error| format!("could not retrieve keys: {error}"))?;
            rows.collect::<rusqlite::Result<Vec<_>>>()
                .map_err(|error| format!("could not retrieve keys: {error}"))?
        };
        if key_definitions.is_empty() {
            return Err("Terracotta database contains no keys".into());
        }

        Ok(Self {
            path: path.to_owned(),
            lazy,
            connection,
            key_definitions,
            descriptors: RefCell::new(DescriptorCache {
                version: None,
                entries: BTreeMap::new(),
                order: VecDeque::new(),
                bytes: 0,
                capacity,
            }),
        })
    }

    pub fn create(path: impl AsRef<Path>, definitions: &[KeyDefinition]) -> Result<Self> {
        validate_definitions(definitions)
            .map_err(|_| "key names must be alphanumeric and not reserved".to_owned())?;
        let mut connection = Connection::open(path.as_ref()).map_err(write_error)?;
        let transaction = connection.transaction().map_err(write_error)?;
        let connection = &transaction;
        connection
        .execute_batch(
            "CREATE TABLE terracotta (version VARCHAR(255) PRIMARY KEY);\n\
             CREATE TABLE key_names (key_name VARCHAR(256) PRIMARY KEY, description VARCHAR(8000), idx INTEGER UNIQUE);",
        )
        .map_err(write_error)?;

        let columns = definitions
            .iter()
            .map(|definition| quote_identifier(&definition.key))
            .collect::<Vec<_>>();
        let key_schema = columns
            .iter()
            .map(|column| format!("{column} VARCHAR(256) NOT NULL"))
            .collect::<Vec<_>>()
            .join(", ");
        let primary_key = columns.join(", ");
        connection
        .execute_batch(&format!(
            "CREATE TABLE datasets ({key_schema}, path VARCHAR(8000), PRIMARY KEY ({primary_key}));\n\
             CREATE TABLE metadata ({key_schema}, bounds_north REAL, bounds_east REAL, bounds_south REAL, bounds_west REAL, convex_hull TEXT, valid_percentage REAL, min REAL, max REAL, mean REAL, stdev REAL, percentiles BLOB, metadata TEXT, PRIMARY KEY ({primary_key}));"
        ))
        .map_err(write_error)?;
        connection
            .execute(
                "INSERT INTO terracotta (version) VALUES (?)",
                [TERRACOTTA_COMPATIBILITY_VERSION],
            )
            .map_err(write_error)?;
        for (index, definition) in definitions.iter().enumerate() {
            connection
                .execute(
                    "INSERT INTO key_names (key_name, description, idx) VALUES (?, ?, ?)",
                    rusqlite::params![definition.key, definition.description, index as i64],
                )
                .map_err(write_error)?;
        }

        transaction.commit().map_err(write_error)?;
        Self::open_writable(path)
    }

    /// A nested operation is a savepoint; the outer operation controls commit.
    pub fn transaction<T>(&self, operation: impl FnOnce(&Self) -> Result<T>) -> Result<T> {
        if self.connection.is_autocommit() {
            let transaction = rusqlite::Transaction::new_unchecked(
                &self.connection,
                rusqlite::TransactionBehavior::Immediate,
            )
            .map_err(write_error)?;
            let value = operation(self)?;
            transaction.commit().map_err(write_error)?;
            return Ok(value);
        }
        // A guard also rolls back on unwinding or failed RELEASE.
        struct Guard<'a> {
            connection: &'a Connection,
            active: bool,
        }
        impl Drop for Guard<'_> {
            fn drop(&mut self) {
                if self.active {
                    let _ = self
                        .connection
                        .execute_batch("ROLLBACK TO terracotta_write; RELEASE terracotta_write");
                }
            }
        }
        self.connection
            .execute_batch("SAVEPOINT terracotta_write")
            .map_err(write_error)?;
        let mut guard = Guard {
            connection: &self.connection,
            active: true,
        };
        let value = operation(self)?;
        self.connection
            .execute_batch("RELEASE terracotta_write")
            .map_err(write_error)?;
        guard.active = false;
        Ok(value)
    }

    pub fn insert(&self, dataset: &Dataset, metadata: Option<&Metadata>) -> Result<()> {
        self.descriptors.borrow_mut().clear();
        self.validate_key_count(&dataset.keys)?;
        self.transaction(|store| store.insert_inner(dataset, metadata))
    }
    fn insert_inner(&self, dataset: &Dataset, metadata: Option<&Metadata>) -> Result<()> {
        let columns = self.quoted_key_columns();
        let placeholders = vec!["?"; columns.len() + 1].join(", ");
        let mut dataset_values = dataset
            .keys
            .iter()
            .cloned()
            .map(SqlValue::Text)
            .collect::<Vec<_>>();
        dataset_values.push(SqlValue::Text(dataset.path.to_string_lossy().into_owned()));
        self.connection
            .execute(
                &format!(
                    "INSERT OR REPLACE INTO datasets ({}, path) VALUES ({placeholders})",
                    columns.join(", ")
                ),
                rusqlite::params_from_iter(dataset_values),
            )
            .map_err(write_error)?;

        if metadata.is_none()
            && !crate::compatibility::CompatibilityProfile::current().is_terracotta()
        {
            let predicate = columns
                .iter()
                .map(|k| format!("{k} = ?"))
                .collect::<Vec<_>>()
                .join(" AND ");
            self.connection
                .execute(
                    &format!("DELETE FROM metadata WHERE {predicate}"),
                    rusqlite::params_from_iter(&dataset.keys),
                )
                .map_err(write_error)?;
        }
        if let Some(metadata) = metadata {
            let metadata_columns = [
                "bounds_north",
                "bounds_east",
                "bounds_south",
                "bounds_west",
                "convex_hull",
                "valid_percentage",
                "min",
                "max",
                "mean",
                "stdev",
                "percentiles",
                "metadata",
            ];
            let mut metadata_values = dataset
                .keys
                .iter()
                .cloned()
                .map(SqlValue::Text)
                .collect::<Vec<_>>();
            metadata_values.extend(metadata.bounds.into_iter().map(SqlValue::Real));
            metadata_values.push(SqlValue::Text(
                serde_json::to_string(&metadata.convex_hull).map_err(|error| error.to_string())?,
            ));
            metadata_values.extend(
                [
                    metadata.valid_percentage,
                    metadata.range[0],
                    metadata.range[1],
                    metadata.mean,
                    metadata.stdev,
                ]
                .into_iter()
                .map(SqlValue::Real),
            );
            metadata_values.push(SqlValue::Blob(
                metadata
                    .percentiles
                    .iter()
                    .flat_map(|value| (*value as f32).to_ne_bytes())
                    .collect(),
            ));
            metadata_values.push(SqlValue::Text(
                serde_json::to_string(&metadata.metadata).map_err(|error| error.to_string())?,
            ));
            let metadata_placeholders = vec!["?"; metadata_values.len()].join(", ");
            self.connection
                .execute(
                    &format!(
                        "INSERT OR REPLACE INTO metadata ({}, {}) VALUES ({metadata_placeholders})",
                        columns.join(", "),
                        metadata_columns.join(", ")
                    ),
                    rusqlite::params_from_iter(metadata_values),
                )
                .map_err(write_error)?;
        }

        Ok(())
    }
    pub fn delete(&self, keys: &[String]) -> Result<()> {
        self.descriptors.borrow_mut().clear();
        self.validate_key_count(keys)?;
        self.transaction(|store| {
            if store.dataset(keys)?.is_none() {
                return Err("No dataset found".into());
            }
            let predicate = store
                .quoted_key_columns()
                .iter()
                .map(|k| format!("{k} = ?"))
                .collect::<Vec<_>>()
                .join(" AND ");
            for table in ["datasets", "metadata"] {
                store
                    .connection
                    .execute(
                        &format!("DELETE FROM {table} WHERE {predicate}"),
                        rusqlite::params_from_iter(keys),
                    )
                    .map_err(write_error)?;
            }
            if store.native_format()? {
                store
                    .connection
                    .execute(
                        "DELETE FROM rcfp_definitions WHERE keys=?",
                        [serde_json::to_string(keys).map_err(|e| e.to_string())?],
                    )
                    .map_err(write_error)?;
            }
            Ok(())
        })
    }

    pub fn integrity_check(&self) -> Result<()> {
        let result: String = self
            .connection
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .map_err(db_error)?;
        if result != "ok" {
            return Err("invalid remote SQLite snapshot".into());
        }
        self.datasets()?;
        self.connection.prepare(&format!("SELECT {}, bounds_north, bounds_east, bounds_south, bounds_west, convex_hull, valid_percentage, min, max, mean, stdev, percentiles, metadata FROM metadata LIMIT 0",self.quoted_key_columns().join(","))).map_err(|e|format!("invalid remote SQLite schema: {e}"))?;
        Ok(())
    }

    pub fn keys(&self) -> &[KeyDefinition] {
        &self.key_definitions
    }

    pub fn key_names(&self) -> Vec<&str> {
        self.key_definitions
            .iter()
            .map(|item| item.key.as_str())
            .collect()
    }

    pub fn datasets(&self) -> Result<Vec<Dataset>> {
        let columns = self.quoted_key_columns();
        let sql = format!(
            "SELECT {columns}, path FROM datasets",
            columns = columns.join(", ")
        );
        let mut statement = self.connection.prepare_cached(&sql).map_err(db_error)?;
        let key_count = self.key_definitions.len();
        let rows = statement
            .query_map([], |row| {
                let mut keys = Vec::with_capacity(key_count);
                for index in 0..key_count {
                    keys.push(row.get(index)?);
                }
                Ok(Dataset {
                    keys,
                    path: PathBuf::from(row.get::<_, String>(key_count)?),
                })
            })
            .map_err(db_error)?;
        rows.collect::<rusqlite::Result<Vec<_>>>().map_err(db_error)
    }

    pub fn dataset(&self, keys: &[String]) -> Result<Option<Dataset>> {
        self.validate_key_count(keys)?;
        let predicates = self
            .quoted_key_columns()
            .iter()
            .map(|key| format!("{key} = ?"))
            .collect::<Vec<_>>()
            .join(" AND ");
        let sql = format!("SELECT path FROM datasets WHERE {predicates} LIMIT 1");
        let mut statement = self.connection.prepare_cached(&sql).map_err(db_error)?;
        statement
            .query_row(rusqlite::params_from_iter(keys.iter()), |row| {
                Ok(Dataset {
                    keys: keys.to_vec(),
                    path: PathBuf::from(row.get::<_, String>(0)?),
                })
            })
            .optional()
            .map_err(db_error)
    }

    /// Revision-checked point lookups for repeated tile requests. Transactions
    /// bypass retention so rolled-back data can never survive in the cache.
    pub fn descriptor(&self, keys: &[String]) -> Result<Option<(Dataset, Metadata)>> {
        let capacity = self.descriptors.borrow().capacity;
        if capacity == 0 || !self.connection.is_autocommit() {
            let Some(dataset) = self.dataset(keys)? else {
                return Ok(None);
            };
            return self
                .metadata(keys)
                .map(|metadata| metadata.map(|metadata| (dataset, metadata)));
        }
        let version = || {
            self.connection
                .prepare_cached("PRAGMA main.data_version")
                .map_err(db_error)?
                .query_row([], |row| row.get::<_, i64>(0))
                .map_err(db_error)
        };
        for _ in 0..3 {
            let before = version()?;
            {
                let mut cache = self.descriptors.borrow_mut();
                if cache.version != Some(before) {
                    cache.clear();
                    cache.version = Some(before);
                }
                if let Some((dataset, metadata, _)) = cache.entries.get(keys) {
                    let result = (dataset.clone(), metadata.clone());
                    cache.order.retain(|key| key != keys);
                    cache.order.push_back(keys.to_vec());
                    return Ok(Some(result));
                }
            }
            let Some((dataset, metadata)) = self.descriptor_snapshot(keys)? else {
                return Ok(None);
            };
            if version()? != before {
                continue;
            }
            let weight = serde_json::to_vec(&metadata)
                .map_err(|e| e.to_string())?
                .len()
                + dataset.path.as_os_str().len()
                + keys.iter().map(|key| key.len() * 3).sum::<usize>()
                + std::mem::size_of::<Metadata>()
                + std::mem::size_of::<Dataset>();
            if weight <= capacity {
                let mut cache = self.descriptors.borrow_mut();
                while cache.bytes + weight > capacity || cache.entries.len() >= 256 {
                    let Some(oldest) = cache.order.pop_front() else {
                        break;
                    };
                    if let Some((_, _, bytes)) = cache.entries.remove(&oldest) {
                        cache.bytes -= bytes;
                    }
                }
                cache.bytes += weight;
                cache.order.push_back(keys.to_vec());
                cache
                    .entries
                    .insert(keys.to_vec(), (dataset.clone(), metadata.clone(), weight));
            }
            return Ok(Some((dataset, metadata)));
        }
        // Busy writers may prevent retention, but readers can still obtain a
        // coherent descriptor without adding a new failure mode.
        self.descriptor_snapshot(keys)
    }

    fn descriptor_snapshot(&self, keys: &[String]) -> Result<Option<(Dataset, Metadata)>> {
        for attempt in 0..2 {
            let snapshot = self.connection.unchecked_transaction().map_err(db_error)?;
            let dataset = self.dataset(keys)?;
            let metadata = self.metadata_stored(keys)?;
            snapshot.commit().map_err(db_error)?;
            let Some(dataset) = dataset else {
                return Ok(None);
            };
            if let Some(metadata) = metadata {
                return Ok(Some((dataset, metadata)));
            }
            if attempt == 0 {
                // Lazy fill may publish a newer descriptor. Re-read both fields
                // together rather than pairing new metadata with an old path.
                self.metadata(keys)?;
            }
        }
        Ok(None)
    }

    pub fn metadata(&self, keys: &[String]) -> Result<Option<Metadata>> {
        if let Some(metadata) = self.metadata_stored(keys)? {
            return Ok(Some(metadata));
        }
        if self.dataset(keys)?.is_none() {
            return Ok(None);
        }
        if !self.lazy {
            return Err("Lazy loading requires a writable database".into());
        }
        // BEGIN IMMEDIATE coordinates separate applications/processes as well as threads.
        // Re-read both metadata and dataset under the write lock before computing.
        let writer = Self::open_writable(&self.path)
            .map_err(|e| format!("Lazy loading requires a writable database: {e}"))?;
        writer.transaction(|store| {
            if let Some(metadata) = store.metadata_stored(keys)? {
                return Ok(Some(metadata));
            }
            let Some(dataset) = store.dataset(keys)? else {
                return Ok(None);
            };
            let shape = crate::settings::var("TC_LAZY_LOADING_MAX_SHAPE")
                .map(|s| serde_json::from_str::<[usize; 2]>(&s).map_err(|e| e.to_string()))
                .unwrap_or(Ok([1024, 1024]))?;
            let metadata = crate::raster::Gdal::load()?.compute_metadata(
                &dataset.path,
                &crate::raster::MetadataOptions {
                    max_shape: Some(shape),
                    ..Default::default()
                },
            )?;
            store.insert(&dataset, Some(&metadata))?;
            store.metadata_stored(keys)
        })
    }

    pub fn metadata_stored(&self, keys: &[String]) -> Result<Option<Metadata>> {
        self.validate_key_count(keys)?;
        let predicates = self
            .quoted_key_columns()
            .iter()
            .map(|key| format!("{key} = ?"))
            .collect::<Vec<_>>()
            .join(" AND ");
        let sql = format!(
            "SELECT bounds_north, bounds_east, bounds_south, bounds_west, convex_hull, valid_percentage, min, max, mean, stdev, percentiles, metadata FROM metadata WHERE {predicates} LIMIT 1"
        );
        let mut statement = self.connection.prepare_cached(&sql).map_err(db_error)?;
        statement
            .query_row(rusqlite::params_from_iter(keys.iter()), |row| {
                let blob = match row.get_ref(10)? {
                    ValueRef::Blob(bytes) => bytes,
                    _ => {
                        return Err(rusqlite::Error::InvalidColumnType(
                            10,
                            "percentiles".into(),
                            rusqlite::types::Type::Blob,
                        ));
                    }
                };
                if blob.len() % 4 != 0 {
                    return Err(rusqlite::Error::InvalidQuery);
                }
                let percentiles = blob
                    .chunks_exact(4)
                    .map(|bytes| f32::from_ne_bytes(bytes.try_into().expect("four bytes")) as f64)
                    .collect();
                let convex_hull_text: String = row.get(4)?;
                let metadata_text: String = row.get(11)?;
                let keys = self
                    .key_definitions
                    .iter()
                    .zip(keys)
                    .map(|(definition, value)| (definition.key.clone(), value.clone()))
                    .collect();
                Ok(Metadata {
                    keys,
                    // Legacy columns are misnamed: positional values are W,S,E,N.
                    bounds: [row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?],
                    convex_hull: json_column(4, convex_hull_text)?,
                    valid_percentage: row.get(5)?,
                    range: [row.get(6)?, row.get(7)?],
                    mean: row.get(8)?,
                    stdev: row.get(9)?,
                    percentiles,
                    metadata: json_column(11, metadata_text)?,
                })
            })
            .optional()
            .map_err(db_error)
    }

    pub fn metadata_json(&self, keys: &[String]) -> Result<Option<Value>> {
        Ok(self.metadata(keys)?.map(|metadata| {
            let mut object = Map::new();
            object.insert("bounds".into(), serde_json::json!(metadata.bounds));
            object.insert("convex_hull".into(), metadata.convex_hull);
            object.insert("keys".into(), serde_json::json!(metadata.keys));
            object.insert("mean".into(), serde_json::json!(metadata.mean));
            object.insert("metadata".into(), metadata.metadata);
            object.insert(
                "percentiles".into(),
                serde_json::json!(metadata.percentiles),
            );
            object.insert("range".into(), serde_json::json!(metadata.range));
            object.insert("stdev".into(), serde_json::json!(metadata.stdev));
            object.insert(
                "valid_percentage".into(),
                serde_json::json!(metadata.valid_percentage),
            );
            Value::Object(object)
        }))
    }

    fn validate_key_count(&self, keys: &[String]) -> Result<()> {
        if keys.len() != self.key_definitions.len() {
            return Err(format!(
                "expected {} dataset keys, received {}",
                self.key_definitions.len(),
                keys.len()
            ));
        }
        Ok(())
    }

    fn quoted_key_columns(&self) -> Vec<String> {
        self.key_definitions
            .iter()
            .map(|definition| quote_identifier(&definition.key))
            .collect()
    }
}

fn json_column(index: usize, text: String) -> rusqlite::Result<Value> {
    serde_json::from_str(&text).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            index,
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })
}

fn major_minor(version: &str) -> Vec<&str> {
    version.split('.').take(2).collect()
}
fn db_error(error: rusqlite::Error) -> String {
    format!("could not retrieve data: {error}")
}

fn quote_identifier(identifier: &str) -> String {
    format!("\"{}\"", identifier.replace('"', "\"\""))
}

fn write_error(error: rusqlite::Error) -> String {
    format!("could not write compatibility database: {error}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checks_major_minor_versions() {
        assert_eq!(major_minor("0.10.9"), major_minor("0.10.1"));
        assert_ne!(major_minor("0.11.0"), major_minor("0.10.1"));
    }

    #[test]
    fn metadata_round_trip() {
        let source =
            std::env::temp_dir().join(format!("rcfp-storage-{}-source.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&source);
        let keys = vec!["lagoon".into(), "red".into()];
        let dataset = Dataset {
            keys: keys.clone(),
            path: "red.tif".into(),
        };
        let metadata = Metadata {
            keys: BTreeMap::new(),
            bounds: [12.25, 45.34, 12.50, 45.60],
            convex_hull: serde_json::json!({"type": "Polygon", "coordinates": []}),
            valid_percentage: 0.75,
            range: [160.0, 2614.0],
            mean: 1000.0,
            stdev: 250.0,
            percentiles: (1..=99).map(f64::from).collect(),
            metadata: serde_json::json!({"source": "test"}),
        };
        let store = SqliteStore::create(
            &source,
            &[
                KeyDefinition {
                    key: "scene".into(),
                    description: String::new(),
                },
                KeyDefinition {
                    key: "band".into(),
                    description: String::new(),
                },
            ],
        )
        .unwrap();
        store.insert(&dataset, Some(&metadata)).unwrap();
        drop(store);
        let reopened = SqliteStore::open(&source).unwrap();
        assert_eq!(reopened.key_names(), vec!["scene", "band"]);
        assert_eq!(reopened.datasets().unwrap(), vec![dataset.clone()]);
        let observed = reopened.metadata(&keys).unwrap().unwrap();
        assert_eq!(observed.bounds, metadata.bounds);
        assert_eq!(observed.percentiles, metadata.percentiles);
        assert_eq!(observed.metadata, metadata.metadata);
        reopened.descriptors.borrow_mut().capacity = 4096;
        assert_eq!(
            reopened.descriptor(&keys).unwrap().unwrap().1.range,
            metadata.range
        );
        assert!(!reopened.descriptors.borrow().entries.is_empty());
        let writer = SqliteStore::open_writable(&source).unwrap();
        let revised = Metadata {
            range: [200., 3000.],
            ..metadata.clone()
        };
        let moved = Dataset {
            path: "moved.tif".into(),
            ..dataset.clone()
        };
        writer.insert(&moved, Some(&revised)).unwrap();
        let (loaded, updated) = reopened.descriptor(&keys).unwrap().unwrap();
        assert_eq!(loaded.path, moved.path);
        assert_eq!(updated.range, revised.range);
        // Same-connection writes and rollbacks must not retain transaction data.
        writer.descriptors.borrow_mut().capacity = 4096;
        writer.descriptor(&keys).unwrap();
        let rollback: Result<()> = writer.transaction(|store| {
            store.insert(&dataset, Some(&metadata))?;
            assert_eq!(store.descriptor(&keys)?.unwrap().0.path, dataset.path);
            Err("rollback".into())
        });
        assert!(rollback.is_err());
        assert_eq!(
            writer.descriptor(&keys).unwrap().unwrap().0.path,
            moved.path
        );
        writer.delete(&keys).unwrap();
        assert!(reopened.descriptor(&keys).unwrap().is_none());
        drop(writer);
        drop(reopened);
        std::fs::remove_file(source).unwrap();
    }
}

#[cfg(test)]
mod write_tests {
    use super::*;
    #[test]
    fn nested_write_failure_and_unwind_restore_the_original_dataset() {
        let path =
            std::env::temp_dir().join(format!("rcfp-transaction-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let store = SqliteStore::create(
            &path,
            &[KeyDefinition {
                key: "name".into(),
                description: "Name".into(),
            }],
        )
        .unwrap();
        let original = Dataset {
            keys: vec!["first".into()],
            path: "original.tif".into(),
        };
        store.insert(&original, None).unwrap();
        let replacement = Dataset {
            path: "replacement.tif".into(),
            ..original.clone()
        };
        let failed: Result<()> = store.transaction(|s| {
            s.insert(&replacement, None)?;
            s.delete(&["absent".into()])
        });
        assert!(failed.is_err());
        assert_eq!(
            store.dataset(&original.keys).unwrap(),
            Some(original.clone())
        );
        let panic = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _: Result<()> = store.transaction(|s| {
                s.insert(&replacement, None)?;
                panic!("transaction client unwound");
            });
        }));
        assert!(panic.is_err());
        assert!(store.connection.is_autocommit());
        assert_eq!(store.dataset(&original.keys).unwrap(), Some(original));
        store.delete(&replacement.keys).unwrap();
        assert!(store.datasets().unwrap().is_empty());
        drop(store);
        std::fs::remove_file(path).unwrap();
    }
}

pub(super) fn validate_definitions(definitions: &[KeyDefinition]) -> Result<()> {
    let name = regex::Regex::new(r"^\w+$").unwrap();
    let mut seen = std::collections::HashSet::new();
    if definitions.is_empty()
        || definitions.iter().any(|d| {
            !name.is_match(&d.key)
                || ["limit", "page", "order_by", "ascending"].contains(&d.key.as_str())
                || !seen.insert(&d.key)
        })
    {
        return Err("key names must be unique, alphanumeric and not reserved".into());
    }
    Ok(())
}

pub mod migrations;
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
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::{REFERENCE_VERSION, Result};

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
}

impl SqliteStore {
    pub fn db_version(&self) -> Result<String> {
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
        let version: String = connection
            .query_row("SELECT version FROM terracotta LIMIT 1", [], |row| {
                row.get(0)
            })
            .map_err(|error| format!("not a Terracotta database: {error}"))?;
        if major_minor(&version) != major_minor(&crate::settings::protocol_version()) {
            return Err(format!(
                "Version conflict: database was created in v{version}, but this is compatible with v{REFERENCE_VERSION}"
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
        })
    }

    pub fn create(path: impl AsRef<Path>, definitions: &[KeyDefinition]) -> Result<Self> {
        let name = regex::Regex::new(r"^\w+$").unwrap();
        if definitions.is_empty()
            || definitions.iter().any(|d| {
                !name.is_match(&d.key)
                    || ["limit", "page", "order_by", "ascending"].contains(&d.key.as_str())
            })
        {
            return Err("key names must be alphanumeric and not reserved".into());
        }
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
                [REFERENCE_VERSION],
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
        let mut statement = self.connection.prepare(&sql).map_err(db_error)?;
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
        let mut statement = self.connection.prepare(&sql).map_err(db_error)?;
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
        let mut statement = self.connection.prepare(&sql).map_err(db_error)?;
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
            .map(|definition| format!("\"{}\"", definition.key.replace('"', "\"\"")))
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

/// Write one already-computed dataset using the frozen 0.10 SQLite encoding.
///
/// This deliberately small P03 operation proves cross-language format
/// compatibility. Transactional create/insert/delete/overwrite APIs remain P05.
pub fn write_compat_database(
    path: impl AsRef<Path>,
    definitions: &[KeyDefinition],
    dataset: &Dataset,
    metadata: &Metadata,
) -> Result<()> {
    let path = path.as_ref();
    if path.exists() {
        return Err(format!("refusing to replace database: {}", path.display()));
    }
    if definitions.is_empty() || definitions.len() != dataset.keys.len() {
        return Err("key definitions and dataset values must have equal non-zero length".into());
    }
    let store = SqliteStore::create(path, definitions)?;
    store.insert(dataset, Some(metadata))
}

pub fn copy_dataset(
    source: impl AsRef<Path>,
    destination: impl AsRef<Path>,
    keys: &[String],
) -> Result<()> {
    let store = SqliteStore::open(source)?;
    let dataset = store
        .dataset(keys)?
        .ok_or_else(|| "No dataset found".to_string())?;
    let metadata = store
        .metadata(keys)?
        .ok_or_else(|| "No dataset metadata found".to_string())?;
    write_compat_database(destination, store.keys(), &dataset, &metadata)
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
    fn reads_python_created_database_and_legacy_encodings() {
        let path = Path::new("validation/artifacts/capture-reproduction/reference.sqlite");
        if !path.exists() {
            return;
        }
        let store = SqliteStore::open(path).unwrap();
        assert_eq!(store.key_names(), vec!["scene", "band"]);
        assert_eq!(store.datasets().unwrap().len(), 7);
        let metadata = store
            .metadata(&["lagoon".into(), "red".into()])
            .unwrap()
            .unwrap();
        assert_eq!(metadata.bounds[0], 12.254818102684911);
        assert_eq!(metadata.bounds[1], 45.34255438818542);
        assert_eq!(metadata.percentiles.len(), 99);
        assert_eq!(metadata.percentiles[0], 160.0);
        assert_eq!(metadata.percentiles[98], 2614.0);
    }

    #[test]
    fn rust_compatibility_write_round_trips() {
        let source = Path::new("validation/artifacts/capture-reproduction/reference.sqlite");
        if !source.exists() {
            return;
        }
        let output = std::env::temp_dir().join(format!(
            "terracotta-p03-{}-{}.sqlite",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        if output.exists() {
            std::fs::remove_file(&output).unwrap();
        }
        let keys = vec!["lagoon".into(), "red".into()];
        copy_dataset(source, &output, &keys).unwrap();
        let copied = SqliteStore::open(&output).unwrap();
        assert_eq!(copied.datasets().unwrap().len(), 1);
        assert_eq!(
            copied.metadata(&keys).unwrap().unwrap().percentiles.len(),
            99
        );
        std::fs::remove_file(output).unwrap();
    }
}

#[cfg(test)]
mod write_tests {
    use super::*;
    #[test]
    fn nested_write_failure_and_unwind_restore_the_original_dataset() {
        let path = std::env::temp_dir().join(format!(
            "terracotta-p05-{}-transaction.sqlite",
            std::process::id()
        ));
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

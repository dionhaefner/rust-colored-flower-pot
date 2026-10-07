//! Versioned SQLite persistence for native layers; legacy catalogs are never rewritten.
use super::*;
use rusqlite::{Connection, OpenFlags, OptionalExtension};

const MOSAIC_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS rcfp_mosaics (layer TEXT PRIMARY KEY, header TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rcfp_observations (rowid INTEGER PRIMARY KEY, layer TEXT NOT NULL, ordinal INTEGER NOT NULL, definition TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS rcfp_observation_order ON rcfp_observations(layer, ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS rcfp_footprints USING rtree(rowid, west, east, south, north);
CREATE TABLE IF NOT EXISTS rcfp_attributes (observation INTEGER NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS rcfp_attribute_selection ON rcfp_attributes(name, value, observation);
";

fn attribute(value: &Value) -> String {
    fn canonical(value: &Value) -> Value {
        match value {
            Value::Object(m) => Value::Object(
                m.iter()
                    .map(|(k, v)| (k.clone(), canonical(v)))
                    .collect::<BTreeMap<_, _>>()
                    .into_iter()
                    .collect(),
            ),
            Value::Array(a) => Value::Array(a.iter().map(canonical).collect()),
            Value::Number(n) if n.is_f64() && n.as_f64() == Some(0.) => serde_json::json!(0.0),
            _ => value.clone(),
        }
    }
    canonical(value).to_string()
}

pub const FORMAT: &str = "rcfp-native-1";
pub struct Catalog {
    connection: Connection,
}
impl Catalog {
    pub fn is_native(path: &Path) -> Result<bool> {
        if !path.is_file() {
            return Ok(false);
        }
        let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
            .map_err(|e| e.to_string())?;
        connection.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='rcfp_catalog')", [], |r| r.get(0)).map_err(|e| e.to_string())
    }

    pub fn create(path: &Path) -> Result<Self> {
        require_native()?;
        if path.exists() {
            return Err("native catalog already exists".into());
        }
        let reservation = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|e| e.to_string())?;
        drop(reservation);
        let mut connection = Connection::open(path).map_err(|e| e.to_string())?;
        let result = (|| {
            let transaction = connection.transaction().map_err(|e| e.to_string())?;
            transaction.execute_batch("CREATE TABLE rcfp_catalog (format TEXT NOT NULL); CREATE TABLE rcfp_layers (id TEXT PRIMARY KEY, definition TEXT NOT NULL);").map_err(|e| e.to_string())?;
            transaction
                .execute("INSERT INTO rcfp_catalog VALUES (?)", [FORMAT])
                .map_err(|e| e.to_string())?;
            transaction.commit().map_err(|e| e.to_string())
        })();
        if let Err(error) = result {
            drop(connection);
            let _ = std::fs::remove_file(path);
            return Err(error);
        }
        Ok(Self { connection })
    }
    pub fn open(path: &Path, writable: bool) -> Result<Self> {
        require_native()?;
        let connection = Connection::open_with_flags(
            path,
            if writable {
                OpenFlags::SQLITE_OPEN_READ_WRITE
            } else {
                OpenFlags::SQLITE_OPEN_READ_ONLY
            },
        )
        .map_err(|e| e.to_string())?;
        let format: String = connection
            .query_row(
                "SELECT MIN(format) FROM rcfp_catalog HAVING COUNT(*)=1",
                [],
                |r| r.get(0),
            )
            .map_err(|_| "not a native layer catalog; import into a new database")?;
        if format != FORMAT {
            return Err(format!("unsupported native catalog format '{format}'"));
        }
        Ok(Self { connection })
    }
    pub fn put(&mut self, id: &str, mut layer: Layer) -> Result<()> {
        if id.is_empty()
            || id.len() > 256
            || !id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        {
            return Err(
                "layer IDs must contain ASCII letters, digits, underscores or hyphens".into(),
            );
        }
        layer.register()?;
        let bounds = if layer.mosaic.is_some() {
            let gdal = Gdal::load()?;
            layer
                .observations
                .iter()
                .map(|o| {
                    o.assets.values().try_fold(
                        [
                            f64::INFINITY,
                            f64::INFINITY,
                            f64::NEG_INFINITY,
                            f64::NEG_INFINITY,
                        ],
                        |b, a| {
                            let a = gdal.asset_map_bounds(&a.location)?;
                            if a.iter().any(|v| !v.is_finite()) || a[0] >= a[2] || a[1] >= a[3] {
                                return Err(
                                    "mosaic footprints require finite increasing bounds".into()
                                );
                            }
                            Ok([
                                b[0].min(a[0]),
                                b[1].min(a[1]),
                                b[2].max(a[2]),
                                b[3].max(a[3]),
                            ])
                        },
                    )
                })
                .collect::<Result<Vec<_>>>()?
        } else {
            Vec::new()
        };
        let definition = serde_json::to_string(&layer).map_err(|e| e.to_string())?;
        let transaction = self.connection.transaction().map_err(|e| e.to_string())?;
        transaction
            .execute_batch(MOSAIC_SCHEMA)
            .map_err(|e| e.to_string())?;
        Self::remove_index(&transaction, id)?;
        if layer.mosaic.is_some() {
            let mut header = layer.clone();
            header.observations.clear();
            transaction
                .execute(
                    "INSERT INTO rcfp_mosaics VALUES (?,?)",
                    rusqlite::params![
                        id,
                        serde_json::to_string(&header).map_err(|e| e.to_string())?
                    ],
                )
                .map_err(|e| e.to_string())?;
            for (ordinal, (observation, b)) in layer.observations.iter().zip(bounds).enumerate() {
                transaction
                    .execute(
                        "INSERT INTO rcfp_observations(layer,ordinal,definition) VALUES (?,?,?)",
                        rusqlite::params![
                            id,
                            ordinal,
                            serde_json::to_string(observation).map_err(|e| e.to_string())?
                        ],
                    )
                    .map_err(|e| e.to_string())?;
                let row = transaction.last_insert_rowid();
                transaction
                    .execute(
                        "INSERT INTO rcfp_footprints VALUES (?,?,?,?,?)",
                        rusqlite::params![row, b[0], b[2], b[1], b[3]],
                    )
                    .map_err(|e| e.to_string())?;
                for (name, value) in &observation.attributes {
                    transaction
                        .execute(
                            "INSERT INTO rcfp_attributes VALUES (?,?,?)",
                            rusqlite::params![row, name, attribute(value)],
                        )
                        .map_err(|e| e.to_string())?;
                }
            }
        }
        transaction.execute("INSERT INTO rcfp_layers (id, definition) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET definition=excluded.definition",rusqlite::params![id,definition]).map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())
    }
    fn remove_index(connection: &Connection, id: &str) -> Result<()> {
        for sql in [
            "DELETE FROM rcfp_footprints WHERE rowid IN (SELECT rowid FROM rcfp_observations WHERE layer=?)",
            "DELETE FROM rcfp_attributes WHERE observation IN (SELECT rowid FROM rcfp_observations WHERE layer=?)",
            "DELETE FROM rcfp_observations WHERE layer=?",
            "DELETE FROM rcfp_mosaics WHERE layer=?",
        ] {
            connection.execute(sql, [id]).map_err(|e| e.to_string())?;
        }
        Ok(())
    }
    pub fn for_render(
        &self,
        id: &str,
        spec: Option<RenderSpec>,
    ) -> Result<Option<(Layer, RenderSpec)>> {
        // A read transaction keeps the header, footprints and observation rows
        // on one generation while another connection replaces the layer.
        let transaction = self
            .connection
            .unchecked_transaction()
            .map_err(|e| e.to_string())?;
        let indexed: bool = transaction
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='rcfp_mosaics')",
                [],
                |r| r.get(0),
            )
            .map_err(|e| e.to_string())?;
        let header: Option<String> = if indexed {
            transaction
                .query_row("SELECT header FROM rcfp_mosaics WHERE layer=?", [id], |r| {
                    r.get(0)
                })
                .optional()
                .map_err(|e| e.to_string())?
        } else {
            None
        };
        let Some(header) = header else {
            let Some(layer) = self.get(id)? else {
                return Ok(None);
            };
            let spec = spec
                .or_else(|| layer.default_render.clone())
                .ok_or("layer has no default rendering specification")?;
            return Ok(Some((layer, spec)));
        };
        let mut layer: Layer = serde_json::from_str(&header).map_err(|e| e.to_string())?;
        let spec = spec
            .or_else(|| layer.default_render.clone())
            .ok_or("layer has no default rendering specification")?;
        let budget = layer.mosaic.as_ref().ok_or("missing mosaic budget")?;
        budget.check(&layer, &spec)?;
        for (name, value) in &spec.selection {
            if layer.selection.get(name).is_some_and(|v| v != value) {
                return Ok(Some((layer, spec)));
            }
            layer.selection.insert(name.clone(), value.clone());
        }
        let mut sql = String::from(
            "SELECT o.definition FROM rcfp_footprints f CROSS JOIN rcfp_observations o ON o.rowid=f.rowid WHERE o.layer=? AND f.west<? AND f.east>? AND f.south<? AND f.north>?",
        );
        use rusqlite::types::Value as SqlValue;
        let b = spec.grid.bounds;
        let mut parameters = vec![
            SqlValue::Text(id.into()),
            SqlValue::Real(b[2]),
            SqlValue::Real(b[0]),
            SqlValue::Real(b[3]),
            SqlValue::Real(b[1]),
        ];
        for (name, value) in &layer.selection {
            sql.push_str(" AND EXISTS(SELECT 1 FROM rcfp_attributes a WHERE a.observation=o.rowid AND a.name=? AND a.value=?)");
            parameters.push(SqlValue::Text(name.clone()));
            parameters.push(SqlValue::Text(attribute(value)));
        }
        sql.push_str(" ORDER BY o.ordinal LIMIT ?");
        parameters.push(SqlValue::Integer((budget.max_sources + 1) as i64));
        let mut statement = transaction.prepare(&sql).map_err(|e| e.to_string())?;
        let mut rows = statement
            .query(rusqlite::params_from_iter(parameters))
            .map_err(|e| e.to_string())?;
        while let Some(row) = rows.next().map_err(|e| e.to_string())? {
            if layer.observations.len() == budget.max_sources {
                return Err(mosaic::EXCEEDED.into());
            }
            let text: String = row.get(0).map_err(|e| e.to_string())?;
            layer
                .observations
                .push(serde_json::from_str(&text).map_err(|e| e.to_string())?);
        }
        budget.check(&layer, &spec)?;
        Ok(Some((layer, spec)))
    }
    pub fn get(&self, id: &str) -> Result<Option<Layer>> {
        let definition: Option<String> = self
            .connection
            .query_row("SELECT definition FROM rcfp_layers WHERE id=?", [id], |r| {
                r.get(0)
            })
            .optional()
            .map_err(|e| e.to_string())?;
        definition
            .map(|text| serde_json::from_str(&text).map_err(|e| e.to_string()))
            .transpose()
    }
    pub fn ids(&self) -> Result<Vec<String>> {
        self.connection
            .prepare("SELECT id FROM rcfp_layers ORDER BY id")
            .map_err(|e| e.to_string())?
            .query_map([], |r| r.get(0))
            .map_err(|e| e.to_string())?
            .collect::<std::result::Result<_, _>>()
            .map_err(|e| e.to_string())
    }
    pub fn delete(&self, id: &str) -> Result<()> {
        let transaction = self
            .connection
            .unchecked_transaction()
            .map_err(|e| e.to_string())?;
        let indexed: bool = transaction
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='rcfp_mosaics')",
                [],
                |r| r.get(0),
            )
            .map_err(|e| e.to_string())?;
        if indexed {
            Self::remove_index(&transaction, id)?;
        }
        transaction
            .execute("DELETE FROM rcfp_layers WHERE id=?", [id])
            .map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_format_is_distinct_and_unknown_markers_are_rejected() {
        crate::settings::scope(
            serde_json::Map::from_iter([("COMPAT_PROFILE".into(), "native".into())]),
            || {
                let directory = tempfile::tempdir().unwrap();
                let path = directory.path().join("layers.sqlite");
                let catalog = Catalog::create(&path).unwrap();
                assert!(catalog.ids().unwrap().is_empty());
                assert!(Catalog::create(&path).is_err());
                assert!(crate::storage::SqliteStore::open(&path).is_err());
                catalog
                    .connection
                    .execute("UPDATE rcfp_catalog SET format='unknown'", [])
                    .unwrap();
                assert!(
                    Catalog::open(&path, false)
                        .err()
                        .unwrap()
                        .contains("unsupported")
                );
                catalog
                    .connection
                    .execute("DELETE FROM rcfp_catalog", [])
                    .unwrap();
                assert!(Catalog::open(&path, false).is_err());
            },
        );
    }
}

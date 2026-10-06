//! Versioned SQLite persistence for native layers; legacy catalogs are never rewritten.
use super::*;
use rusqlite::{Connection, OpenFlags, OptionalExtension};

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
        let definition = serde_json::to_string(&layer).map_err(|e| e.to_string())?;
        let transaction = self.connection.transaction().map_err(|e| e.to_string())?;
        transaction.execute("INSERT INTO rcfp_layers (id, definition) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET definition=excluded.definition",rusqlite::params![id,definition]).map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())
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
        self.connection
            .execute("DELETE FROM rcfp_layers WHERE id=?", [id])
            .map_err(|e| e.to_string())?;
        Ok(())
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

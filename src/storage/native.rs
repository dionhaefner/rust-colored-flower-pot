//! Native keyed catalogs reuse the existing dataset, metadata and transaction tables.
use super::*;

impl SqliteStore {
    pub fn native_format(&self) -> Result<bool> {
        self.connection.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='rcfp_definitions')", [], |row| row.get(0)).map_err(db_error)
    }

    pub fn create_native(path: &Path, definitions: &[KeyDefinition]) -> Result<Self> {
        crate::compatibility::CompatibilityProfile::current().require_native()?;
        if path.exists() {
            return Err("catalog already exists".into());
        }
        let store = Self::create(path, definitions)?;
        let result=store.transaction(|store| {
            store.connection.execute_batch("DROP TABLE terracotta; CREATE TABLE rcfp_catalog (format TEXT NOT NULL); CREATE TABLE rcfp_definitions (keys TEXT PRIMARY KEY, definition TEXT NOT NULL); CREATE TABLE rcfp_sources (id TEXT PRIMARY KEY, definition TEXT NOT NULL);").map_err(write_error)?;
            store.connection.execute("INSERT INTO rcfp_catalog VALUES (?)", [crate::catalog::dataset::FORMAT]).map_err(write_error)?;
            Ok(())
        });
        if let Err(error) = result {
            drop(store);
            let _ = std::fs::remove_file(path);
            return Err(error);
        }
        Ok(store)
    }

    pub(crate) fn definition(&self, keys: &[String]) -> Result<Option<Value>> {
        if !self.native_format()? {
            return Ok(None);
        }
        let key = serde_json::to_string(keys).map_err(|e| e.to_string())?;
        let text: Option<String> = self
            .connection
            .query_row(
                "SELECT definition FROM rcfp_definitions WHERE keys=?",
                [key],
                |row| row.get(0),
            )
            .optional()
            .map_err(db_error)?;
        text.map(|s| serde_json::from_str(&s).map_err(|e| e.to_string()))
            .transpose()
    }

    pub(crate) fn put_definition(&self, keys: &[String], value: &Value) -> Result<()> {
        self.validate_key_count(keys)?;
        if !self.native_format()? {
            return Err("band bindings and expressions require a native keyed catalog".into());
        }
        self.connection.execute("INSERT INTO rcfp_definitions VALUES (?,?) ON CONFLICT(keys) DO UPDATE SET definition=excluded.definition", rusqlite::params![serde_json::to_string(keys).map_err(|e| e.to_string())?, value.to_string()]).map_err(write_error)?;
        self.descriptors.borrow_mut().clear();
        Ok(())
    }

    pub(crate) fn source_definition(&self, id: &str) -> Result<Option<Value>> {
        if !self.native_format()? {
            return Ok(None);
        }
        let text: Option<String> = self
            .connection
            .query_row(
                "SELECT definition FROM rcfp_sources WHERE id=?",
                [id],
                |row| row.get(0),
            )
            .optional()
            .map_err(db_error)?;
        text.map(|s| serde_json::from_str(&s).map_err(|e| e.to_string()))
            .transpose()
    }

    pub(crate) fn put_source(&self, id: &str, value: &Value) -> Result<()> {
        if !self.native_format()? {
            return Err("stored sources require a native keyed catalog".into());
        }
        self.connection.execute("INSERT INTO rcfp_sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET definition=excluded.definition", rusqlite::params![id, value.to_string()]).map_err(write_error)?;
        Ok(())
    }
}

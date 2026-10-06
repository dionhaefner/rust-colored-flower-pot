//! Storage facade shared by HTTP, native preparation and Python adapters.
use super::{
    Dataset, KeyDefinition, Metadata, SqliteStore,
    provider::{Provider, SqlPath, local_path},
    relational::RelationalStore,
    remote::{Generation, Remote},
};
use crate::Result;
use serde_json::Value;
use std::{collections::BTreeMap, path::Path, sync::Arc};

pub enum Store {
    Sqlite(SqliteStore),
    Relational(Box<RelationalStore>),
    Remote {
        store: SqliteStore,
        remote: Arc<Remote>,
        generation: Arc<Generation>,
    },
}
impl Store {
    pub(crate) fn definition(&self, keys: &[String]) -> Result<Option<Value>> {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.definition(keys),
            _ => Ok(None),
        }
    }
    pub(crate) fn put_definition(&self, keys: &[String], value: &Value) -> Result<()> {
        match self {
            Self::Sqlite(s) => s.put_definition(keys, value),
            _ => Err("native keyed catalog writes currently require local SQLite".into()),
        }
    }
    pub(crate) fn source_definition(&self, id: &str) -> Result<Option<Value>> {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.source_definition(id),
            _ => Ok(None),
        }
    }
    pub(crate) fn put_source(&self, id: &str, value: &Value) -> Result<()> {
        match self {
            Self::Sqlite(s) => s.put_source(id, value),
            _ => Err("native keyed catalog writes currently require local SQLite".into()),
        }
    }
    pub fn db_version(&self) -> Result<String> {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.db_version(),
            Self::Relational(s) => s.db_version(),
        }
    }
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        Self::open_with(
            &path.as_ref().to_string_lossy(),
            crate::settings::var("TC_DRIVER_PROVIDER").ok().as_deref(),
            false,
        )
    }
    pub fn open_with(path: &str, provider: Option<&str>, read_only: bool) -> Result<Self> {
        match Provider::detect(path, provider)? {
            Provider::Sqlite => Ok(Self::Sqlite(if read_only {
                SqliteStore::open_read_only(local_path(path)?)?
            } else {
                SqliteStore::open(local_path(path)?)?
            })),
            Provider::Remote => {
                let remote = Remote::open(path)?;
                let generation = remote.snapshot()?;
                let store = SqliteStore::open_read_only(&generation.path)?;
                Ok(Self::Remote {
                    store,
                    remote,
                    generation,
                })
            }
            p => Ok(Self::Relational(Box::new(RelationalStore::open(
                &SqlPath::parse(path, p)?,
                !read_only,
            )?))),
        }
    }
    pub fn open_writable(path: &str, provider: Option<&str>) -> Result<Self> {
        if Provider::detect(path, provider)? == Provider::Sqlite {
            Ok(Self::Sqlite(SqliteStore::open_writable(local_path(path)?)?))
        } else {
            Self::open_with(path, provider, false)
        }
    }
    pub fn create_native(
        path: &str,
        provider: Option<&str>,
        keys: &[KeyDefinition],
    ) -> Result<Self> {
        if Provider::detect(path, provider)? != Provider::Sqlite {
            return Err("native keyed catalog creation requires local SQLite".into());
        }
        Ok(Self::Sqlite(SqliteStore::create_native(
            &local_path(path)?,
            keys,
        )?))
    }

    pub fn create(path: &str, provider: Option<&str>, keys: &[KeyDefinition]) -> Result<Self> {
        match Provider::detect(path, provider)? {
            Provider::Sqlite => Ok(Self::Sqlite(SqliteStore::create(local_path(path)?, keys)?)),
            Provider::Remote => Err("Database is not writable".into()),
            p => Ok(Self::Relational(Box::new(RelationalStore::create(
                &SqlPath::parse(path, p)?,
                keys,
            )?))),
        }
    }
    pub fn refresh(&mut self) -> Result<()> {
        if let Self::Relational(s) = self {
            s.refresh()?;
        }
        if let Self::Remote {
            store,
            remote,
            generation,
        } = self
        {
            let next = remote.snapshot()?;
            if !Arc::ptr_eq(&next, generation) {
                let next_store = SqliteStore::open_read_only(&next.path)?;
                *store = next_store;
                *generation = next;
            }
        }
        Ok(())
    }
    pub fn keys(&self) -> &[KeyDefinition] {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.keys(),
            Self::Relational(s) => s.keys(),
        }
    }
    pub fn key_names(&self) -> Vec<&str> {
        self.keys().iter().map(|k| k.key.as_str()).collect()
    }
    pub fn datasets(&self) -> Result<Vec<Dataset>> {
        self.query_datasets(
            &BTreeMap::new(),
            &self
                .key_names()
                .iter()
                .map(|k| k.to_string())
                .collect::<Vec<_>>(),
            true,
            None,
        )
    }
    pub fn query_datasets(
        &self,
        filters: &BTreeMap<String, Vec<String>>,
        order: &[String],
        ascending: bool,
        pagination: Option<(usize, usize)>,
    ) -> Result<Vec<Dataset>> {
        if let Self::Relational(s) = self {
            return s.datasets(filters, order, ascending, pagination);
        }
        let s = match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s,
            _ => unreachable!(),
        };
        super::query_datasets(
            s.datasets()?,
            &self.key_names(),
            filters,
            order,
            ascending,
            pagination,
        )
    }
    pub fn dataset(&self, keys: &[String]) -> Result<Option<Dataset>> {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.dataset(keys),
            Self::Relational(s) => s.dataset(keys),
        }
    }
    pub fn descriptor(&self, keys: &[String]) -> Result<Option<(Dataset, Metadata)>> {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.descriptor(keys),
            Self::Relational(s) => {
                let Some(dataset) = s.dataset(keys)? else {
                    return Ok(None);
                };
                Ok(s.metadata(keys)?.map(|metadata| (dataset, metadata)))
            }
        }
    }
    pub fn metadata_stored(&self, keys: &[String]) -> Result<Option<Metadata>> {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.metadata_stored(keys),
            Self::Relational(s) => s.metadata_stored(keys),
        }
    }
    pub fn metadata(&self, keys: &[String]) -> Result<Option<Metadata>> {
        match self {
            Self::Sqlite(s) | Self::Remote { store: s, .. } => s.metadata(keys),
            Self::Relational(s) => s.metadata(keys),
        }
    }
    pub fn legacy_decimal_stretch(&self) -> bool {
        matches!(self,Self::Relational(s) if s.legacy_decimal_stretch())
    }
    pub fn metadata_json(&self, keys: &[String]) -> Result<Option<Value>> {
        self.metadata(keys)?
            .map(|m| {
                let mut value = serde_json::to_value(m).map_err(|e| e.to_string())?;
                if let Self::Relational(s) = self {
                    s.mysql_decimal_json(&mut value);
                }
                Ok(value)
            })
            .transpose()
    }
    pub fn insert(&self, d: &Dataset, m: Option<&Metadata>) -> Result<()> {
        match self {
            Self::Sqlite(s) => s.insert(d, m),
            Self::Relational(s) => s.insert(d, m),
            Self::Remote { .. } => Err("Database is not writable".into()),
        }
    }
    pub fn delete(&self, keys: &[String]) -> Result<()> {
        match self {
            Self::Sqlite(s) => s.delete(keys),
            Self::Relational(s) => s.delete(keys),
            Self::Remote { .. } => Err("Database is not writable".into()),
        }
    }
    pub fn transaction<T>(&self, op: impl FnOnce(&Self) -> Result<T>) -> Result<T> {
        match self {
            Self::Sqlite(s) => s.transaction(|_| op(self)),
            Self::Relational(s) => s.transaction(|_| op(self)),
            Self::Remote { .. } => Err("Database is not writable".into()),
        }
    }
}

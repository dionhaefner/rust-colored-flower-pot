//! Real MySQL/PostgreSQL connections. SQL owns collation, matching and ordering.
use super::provider::{Provider, SqlPath, timeout};
use super::{Dataset, KeyDefinition, Metadata};
use crate::{Result, TERRACOTTA_COMPATIBILITY_VERSION};
use mysql::prelude::Queryable;
use postgres::types::{ToSql, Type};
use rusqlite::types::Value as V;
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    net::IpAddr,
    path::PathBuf,
};

fn loopback_ip(host: &str) -> bool {
    host.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback())
}

pub enum Connection {
    Mysql(mysql::Conn),
    Postgres(Box<postgres::Client>),
}
impl Connection {
    pub fn open(path: &SqlPath, database: Option<&str>) -> Result<Self> {
        let database = database.unwrap_or(&path.database);
        let mut c = if path.provider == Provider::Mysql {
            let mut ssl_mode = None;
            let mut root_cert = None;
            for (key, value) in &path.query {
                match key.as_str() {
                    "sslmode" if ssl_mode.replace(value.as_str()).is_none() => {}
                    "sslrootcert" if root_cert.replace(value.as_str()).is_none() => {}
                    _ => return Err(format!("unsupported MySQL connection option: {key}")),
                }
            }
            let ssl = match ssl_mode {
                None | Some("verify-full") => Some(
                    mysql::SslOpts::default()
                        .with_root_cert_path(root_cert.map(|path| PathBuf::from(path.to_owned()))),
                ),
                Some("disable") if loopback_ip(&path.host) && root_cert.is_none() => None,
                _ => return Err("MySQL connections require verified TLS or an explicit loopback plaintext connection".into()),
            };
            let opts = mysql::OptsBuilder::new()
                .ip_or_hostname(Some(&path.host))
                .tcp_port(path.port)
                .user(path.user.clone())
                .pass(path.password.clone())
                .db_name(Some(database))
                .prefer_socket(false)
                .tcp_connect_timeout(Some(timeout()?))
                .ssl_opts(ssl)
                .init(vec![
                    "SET NAMES utf8mb4",
                    "SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
                ]);
            Self::Mysql(
                mysql::Conn::new(opts)
                    .map_err(|e| format!("Could not connect to database: {e}"))?,
            )
        } else {
            let mut config = postgres::Config::new();
            config
                .host(&path.host)
                .port(path.port)
                .dbname(database)
                .connect_timeout(timeout()?);
            if let Some(u) = &path.user {
                config.user(u);
            } else {
                config.user(&crate::settings::var("USER").unwrap_or_else(|_| "postgres".into()));
            }
            if let Some(p) = &path.password {
                config.password(p);
            }
            let mut tls = native_tls::TlsConnector::builder();
            let mut ssl_mode = None;
            let mut root_cert = false;
            for (k, v) in &path.query {
                match k.as_str() {
                    "sslmode" if ssl_mode.replace(v.as_str()).is_none() => {}
                    "sslrootcert" => {
                        if root_cert {
                            return Err("duplicate PostgreSQL sslrootcert".into());
                        }
                        root_cert = true;
                        tls.add_root_certificate(
                            native_tls::Certificate::from_pem(
                                &std::fs::read(v).map_err(|e| e.to_string())?,
                            )
                            .map_err(|e| e.to_string())?,
                        );
                    }
                    "application_name" => {
                        config.application_name(v);
                    }
                    "options" => {
                        config.options(v);
                    }
                    "connect_timeout" => {
                        config.connect_timeout(std::time::Duration::from_secs(
                            v.parse().map_err(|_| "invalid connect_timeout")?,
                        ));
                    }
                    _ => return Err(format!("unsupported PostgreSQL connection option: {k}")),
                }
            }
            let client = match ssl_mode {
                None | Some("verify-full") => {
                    config.ssl_mode(postgres::config::SslMode::Require);
                    let tls = postgres_native_tls::MakeTlsConnector::new(
                        tls.build().map_err(|e| e.to_string())?,
                    );
                    config.connect(tls)
                }
                Some("disable") if loopback_ip(&path.host) && !root_cert => {
                    config.ssl_mode(postgres::config::SslMode::Disable);
                    config.connect(postgres::NoTls)
                }
                _ => return Err("PostgreSQL connections require sslmode=verify-full or an explicit loopback plaintext connection".into()),
            }
            .map_err(|e| format!("Could not connect to database: {e}"))?;
            Self::Postgres(Box::new(client))
        };
        if path.provider == Provider::Postgres {
            c.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
                &[],
            )?;
        }
        Ok(c)
    }
    pub fn execute(&mut self, sql: &str, values: &[V]) -> Result<()> {
        self.query(sql, values).map(|_| ())
    }
    pub fn query(&mut self, sql: &str, values: &[V]) -> Result<Vec<Vec<V>>> {
        match self {
            Self::Mysql(c) => {
                let params = values
                    .iter()
                    .map(|v| match v {
                        V::Null => mysql::Value::NULL,
                        V::Text(t) => mysql::Value::Bytes(t.as_bytes().to_vec()),
                        V::Blob(b) => mysql::Value::Bytes(b.clone()),
                        V::Real(n) => mysql::Value::Double(*n),
                        V::Integer(n) => mysql::Value::Int(*n),
                    })
                    .collect::<Vec<_>>();
                let rows: Vec<mysql::Row> = if values.is_empty() {
                    c.query(sql)
                } else {
                    c.exec(sql, params)
                }
                .map_err(|e| format!("database operation failed: {e}"))?;
                rows.into_iter()
                    .map(|row| {
                        row.unwrap()
                            .into_iter()
                            .map(|v| match v {
                                mysql::Value::NULL => Ok(V::Null),
                                mysql::Value::Int(n) => Ok(V::Integer(n)),
                                mysql::Value::UInt(n) => Ok(V::Integer(n as i64)),
                                mysql::Value::Float(n) => Ok(V::Real(n as f64)),
                                mysql::Value::Double(n) => Ok(V::Real(n)),
                                mysql::Value::Bytes(b) => Ok(V::Blob(b)),
                                _ => Err("unexpected database value type".into()),
                            })
                            .collect()
                    })
                    .collect()
            }
            Self::Postgres(c) => {
                // SQL is generated here; question marks occur only as parameters, never literals.
                let mut n = 0;
                let sql = sql
                    .chars()
                    .map(|ch| {
                        if ch == '?' {
                            n += 1;
                            format!("${n}")
                        } else {
                            ch.to_string()
                        }
                    })
                    .collect::<String>();
                let boxed = values
                    .iter()
                    .map(|v| match v {
                        V::Text(v) => Box::new(v.clone()) as Box<dyn ToSql + Sync>,
                        V::Blob(v) => Box::new(v.clone()),
                        V::Real(v) => Box::new(*v),
                        V::Integer(v) => Box::new(*v),
                        V::Null => Box::new(None::<String>),
                    })
                    .collect::<Vec<_>>();
                let params = boxed.iter().map(|v| v.as_ref()).collect::<Vec<_>>();
                let rows = c.query(&sql, &params).map_err(|e| {
                    format!(
                        "database operation failed: {e}{}",
                        e.as_db_error()
                            .map(|e| format!(": {}", e.message()))
                            .unwrap_or_default()
                    )
                })?;
                rows.into_iter()
                    .map(|row| {
                        (0..row.len())
                            .map(|i| {
                                let val = match *row.columns()[i].type_() {
                                    Type::BYTEA => row
                                        .try_get::<_, Option<Vec<u8>>>(i)
                                        .map(|v| v.map(V::Blob).unwrap_or(V::Null)),
                                    Type::FLOAT8 => row
                                        .try_get::<_, Option<f64>>(i)
                                        .map(|v| v.map(V::Real).unwrap_or(V::Null)),
                                    Type::FLOAT4 => row
                                        .try_get::<_, Option<f32>>(i)
                                        .map(|v| v.map(|v| V::Real(v as f64)).unwrap_or(V::Null)),
                                    Type::INT4 => row.try_get::<_, Option<i32>>(i).map(|v| {
                                        v.map(|v| V::Integer(v as i64)).unwrap_or(V::Null)
                                    }),
                                    Type::INT8 => row
                                        .try_get::<_, Option<i64>>(i)
                                        .map(|v| v.map(V::Integer).unwrap_or(V::Null)),
                                    _ => row
                                        .try_get::<_, Option<String>>(i)
                                        .map(|v| v.map(V::Text).unwrap_or(V::Null)),
                                };
                                val.map_err(|e| e.to_string())
                            })
                            .collect()
                    })
                    .collect()
            }
        }
    }
}

pub struct RelationalStore {
    path: SqlPath,
    connection: RefCell<Connection>,
    provider: Provider,
    keys: Vec<KeyDefinition>,
    depth: Cell<usize>,
    writable: bool,
}
const META: [&str; 12] = [
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
impl RelationalStore {
    pub fn db_version(&self) -> Result<String> {
        let rows = self
            .connection
            .borrow_mut()
            .query("SELECT version FROM terracotta LIMIT 1", &[])?;
        text(
            rows.first()
                .and_then(|r| r.first())
                .ok_or("missing version")?,
        )
    }

    pub fn open(path: &SqlPath, writable: bool) -> Result<Self> {
        let mut connection = Connection::open(path, None)?;
        let rows = connection.query("SELECT version FROM terracotta LIMIT 1", &[])?;
        let version = text(
            rows.first()
                .and_then(|r| r.first())
                .ok_or("not a Terracotta database")?,
        )?;
        if super::major_minor(&version) != super::major_minor(&crate::settings::protocol_version())
        {
            return Err(format!(
                "Version conflict: database was created in v{version}, but this is compatible with v{TERRACOTTA_COMPATIBILITY_VERSION}"
            ));
        }
        let keys = connection
            .query(
                "SELECT key_name, description FROM key_names ORDER BY idx",
                &[],
            )?
            .iter()
            .map(|r| {
                Ok(KeyDefinition {
                    key: text(&r[0])?,
                    description: if r[1] == V::Null {
                        String::new()
                    } else {
                        text(&r[1])?
                    },
                })
            })
            .collect::<Result<Vec<_>>>()?;
        if keys.is_empty() {
            return Err("Terracotta database contains no keys".into());
        }
        Ok(Self {
            path: path.clone(),
            connection: RefCell::new(connection),
            provider: path.provider,
            keys,
            depth: Cell::new(0),
            writable,
        })
    }
    pub fn create(path: &SqlPath, keys: &[KeyDefinition]) -> Result<Self> {
        super::validate_definitions(keys)?;
        let provider = path.provider;
        let mut admin = Connection::open(
            path,
            Some(if provider == Provider::Mysql {
                "mysql"
            } else {
                "postgres"
            }),
        )?;
        admin.execute(
            &format!("CREATE DATABASE {}", provider.quote(&path.database)),
            &[],
        )?;
        let mut c = Connection::open(path, None)?;
        let size = if provider == Provider::Mysql {
            191
        } else {
            682
        } / keys.len();
        let varchar = |n| {
            format!(
                "VARCHAR({n}){}",
                if provider == Provider::Mysql {
                    " CHARACTER SET utf8mb4"
                } else {
                    ""
                }
            )
        };
        let columns = keys
            .iter()
            .map(|k| provider.quote(&k.key))
            .collect::<Vec<_>>();
        let schema = columns
            .iter()
            .map(|k| format!("{k} {} NOT NULL", varchar(size)))
            .collect::<Vec<_>>()
            .join(", ");
        let primary = columns.join(", ");
        let meta = META
            .iter()
            .enumerate()
            .map(|(i, k)| {
                format!(
                    "{} {}",
                    provider.quote(k),
                    match i {
                        4 | 11 =>
                            if provider == Provider::Mysql {
                                "TEXT CHARACTER SET utf8mb4"
                            } else {
                                "TEXT"
                            },
                        10 =>
                            if provider == Provider::Mysql {
                                "BLOB"
                            } else {
                                "BYTEA"
                            },
                        _ => "DOUBLE PRECISION",
                    }
                )
            })
            .collect::<Vec<_>>()
            .join(", ");
        // MySQL DDL commits implicitly. Publish format/key records only after every table exists.
        for sql in [
            format!(
                "CREATE TABLE terracotta (version {} PRIMARY KEY)",
                varchar(255)
            ),
            format!(
                "CREATE TABLE key_names (key_name {} PRIMARY KEY, description {}, idx INTEGER UNIQUE)",
                varchar(size),
                varchar(8000)
            ),
            format!(
                "CREATE TABLE datasets ({schema}, path {}, PRIMARY KEY ({primary}))",
                varchar(8000)
            ),
            format!("CREATE TABLE metadata ({schema}, {meta}, PRIMARY KEY ({primary}))"),
        ] {
            c.execute(&sql, &[])?;
        }
        c.execute("BEGIN", &[])?;
        let result = (|| {
            for (i, k) in keys.iter().enumerate() {
                c.execute(
                    &format!("INSERT INTO key_names (key_name,description,idx) VALUES (?,?,{i})"),
                    &[V::Text(k.key.clone()), V::Text(k.description.clone())],
                )?;
            }
            c.execute(
                "INSERT INTO terracotta (version) VALUES (?)",
                &[V::Text(TERRACOTTA_COMPATIBILITY_VERSION.into())],
            )
        })();
        if result.is_err() {
            let _ = c.execute("ROLLBACK", &[]);
        } else {
            c.execute("COMMIT", &[])?;
        }
        result?;
        Self::open(path, true)
    }
    pub fn refresh(&self) -> Result<()> {
        if self.depth.get() == 0
            && self
                .connection
                .borrow_mut()
                .execute("SELECT 1", &[])
                .is_err()
        {
            *self.connection.borrow_mut() = Connection::open(&self.path, None)?;
        }
        Ok(())
    }
    pub fn legacy_decimal_stretch(&self) -> bool {
        self.provider == Provider::Mysql
            && crate::compatibility::CompatibilityProfile::current().is_terracotta()
    }
    pub fn mysql_decimal_json(&self, value: &mut serde_json::Value) {
        if self.provider != Provider::Mysql {
            return;
        }
        for key in ["bounds", "range"] {
            if let Some(values) = value[key].as_array_mut() {
                for v in values {
                    if let Some(n) = v.as_f64() {
                        *v = serde_json::json!(format!("{n:.10}").parse::<f64>().unwrap());
                    }
                }
            }
        }
        for key in ["mean", "stdev", "valid_percentage"] {
            if let Some(n) = value[key].as_f64() {
                value[key] = serde_json::json!(format!("{n:.10}").parse::<f64>().unwrap());
            }
        }
    }
    pub fn keys(&self) -> &[KeyDefinition] {
        &self.keys
    }
    fn cols(&self) -> Vec<String> {
        self.keys
            .iter()
            .map(|k| self.provider.quote(&k.key))
            .collect()
    }
    fn predicate(&self, keys: &[String]) -> Result<(String, Vec<V>)> {
        if keys.len() != self.keys.len() {
            return Err(format!(
                "expected {} dataset keys, received {}",
                self.keys.len(),
                keys.len()
            ));
        }
        Ok((
            self.cols()
                .iter()
                .map(|k| format!("{k} = ?"))
                .collect::<Vec<_>>()
                .join(" AND "),
            keys.iter().cloned().map(V::Text).collect(),
        ))
    }
    pub fn transaction<T>(&self, op: impl FnOnce(&Self) -> Result<T>) -> Result<T> {
        if !self.writable {
            return Err("Database is not writable".into());
        }
        let depth = self.depth.get();
        let name = format!("tc_write_{depth}");
        self.connection.borrow_mut().execute(
            &if depth == 0 {
                "BEGIN".into()
            } else {
                format!("SAVEPOINT {name}")
            },
            &[],
        )?;
        self.depth.set(depth + 1);
        struct Guard<'a> {
            s: &'a RelationalStore,
            depth: usize,
            name: String,
            active: bool,
        }
        impl Drop for Guard<'_> {
            fn drop(&mut self) {
                self.s.depth.set(self.depth);
                if self.active {
                    let mut c = self.s.connection.borrow_mut();
                    let _ = c.execute(
                        &if self.depth == 0 {
                            "ROLLBACK".into()
                        } else {
                            format!("ROLLBACK TO SAVEPOINT {}", self.name)
                        },
                        &[],
                    );
                    if self.depth > 0 {
                        let _ = c.execute(&format!("RELEASE SAVEPOINT {}", self.name), &[]);
                    }
                }
            }
        }
        let mut guard = Guard {
            s: self,
            depth,
            name: name.clone(),
            active: true,
        };
        let result = op(self)?;
        self.connection.borrow_mut().execute(
            &if depth == 0 {
                "COMMIT".into()
            } else {
                format!("RELEASE SAVEPOINT {name}")
            },
            &[],
        )?;
        guard.active = false;
        Ok(result)
    }
    pub fn datasets(
        &self,
        filters: &BTreeMap<String, Vec<String>>,
        order: &[String],
        ascending: bool,
        pagination: Option<(usize, usize)>,
    ) -> Result<Vec<Dataset>> {
        let mut values = Vec::new();
        let mut predicates = Vec::new();
        for (k, v) in filters {
            if !self.keys.iter().any(|d| d.key == *k) {
                return Err("unrecognized filter key".into());
            }
            if v.is_empty() {
                continue; // SQLAlchemy or_() with no arguments adds no predicate.
            } else {
                predicates.push(format!(
                    "{} IN ({})",
                    self.provider.quote(k),
                    vec!["?"; v.len()].join(",")
                ));
                values.extend(v.iter().cloned().map(V::Text));
            }
        }
        let mut sql = format!("SELECT {},path FROM datasets", self.cols().join(","));
        if !predicates.is_empty() {
            sql.push_str(&format!(" WHERE {}", predicates.join(" AND ")));
        }
        if !order.is_empty() {
            let mut clauses = Vec::new();
            for k in order {
                if k != "path" && !self.keys.iter().any(|d| d.key == *k) {
                    return Err("unrecognized order_by key".into());
                }
                clauses.push(format!(
                    "{} {}",
                    self.provider.quote(k),
                    if ascending { "ASC" } else { "DESC" }
                ));
            }
            sql.push_str(&format!(" ORDER BY {}", clauses.join(",")));
        }
        if let Some((page, limit)) = pagination {
            sql.push_str(&format!(
                " LIMIT {limit} OFFSET {}",
                page.saturating_mul(limit)
            ));
        }
        self.connection
            .borrow_mut()
            .query(&sql, &values)?
            .iter()
            .map(|r| {
                Ok(Dataset {
                    keys: r[..self.keys.len()]
                        .iter()
                        .map(text)
                        .collect::<Result<_>>()?,
                    path: PathBuf::from(text(&r[self.keys.len()])?),
                })
            })
            .collect()
    }
    pub fn dataset(&self, keys: &[String]) -> Result<Option<Dataset>> {
        let (p, v) = self.predicate(keys)?;
        self.connection
            .borrow_mut()
            .query(&format!("SELECT path FROM datasets WHERE {p} LIMIT 1"), &v)?
            .first()
            .map(|r| {
                Ok(Dataset {
                    keys: keys.to_vec(),
                    path: PathBuf::from(text(&r[0])?),
                })
            })
            .transpose()
    }
    pub fn insert(&self, dataset: &Dataset, metadata: Option<&Metadata>) -> Result<()> {
        let (p, v) = self.predicate(&dataset.keys)?;
        self.transaction(|s| {
            let mut c = s.connection.borrow_mut();
            c.execute(&format!("DELETE FROM datasets WHERE {p}"), &v)?;
            let mut data = v.clone();
            data.push(V::Text(dataset.path.to_string_lossy().into()));
            c.execute(
                &format!(
                    "INSERT INTO datasets ({},path) VALUES ({})",
                    s.cols().join(","),
                    vec!["?"; data.len()].join(",")
                ),
                &data,
            )?;
            if metadata.is_none()
                && !crate::compatibility::CompatibilityProfile::current().is_terracotta()
            {
                c.execute(&format!("DELETE FROM metadata WHERE {p}"), &v)?;
            }
            if let Some(m) = metadata {
                let mut data = v.clone();
                data.extend(m.bounds.map(V::Real));
                data.push(V::Text(m.convex_hull.to_string()));
                data.extend(
                    [m.valid_percentage, m.range[0], m.range[1], m.mean, m.stdev].map(V::Real),
                );
                data.push(V::Blob(
                    m.percentiles
                        .iter()
                        .flat_map(|p| (*p as f32).to_ne_bytes())
                        .collect(),
                ));
                data.push(V::Text(m.metadata.to_string()));
                c.execute(&format!("DELETE FROM metadata WHERE {p}"), &v)?;
                c.execute(
                    &format!(
                        "INSERT INTO metadata ({},{}) VALUES ({})",
                        s.cols().join(","),
                        META.iter()
                            .map(|k| s.provider.quote(k))
                            .collect::<Vec<_>>()
                            .join(","),
                        vec!["?"; data.len()].join(",")
                    ),
                    &data,
                )?;
            }
            Ok(())
        })
    }
    pub fn delete(&self, keys: &[String]) -> Result<()> {
        let (p, v) = self.predicate(keys)?;
        self.transaction(|s| {
            if s.dataset(keys)?.is_none() {
                return Err("No dataset found".into());
            }
            for table in ["datasets", "metadata"] {
                s.connection
                    .borrow_mut()
                    .execute(&format!("DELETE FROM {table} WHERE {p}"), &v)?;
            }
            Ok(())
        })
    }
    pub fn metadata_stored(&self, keys: &[String]) -> Result<Option<Metadata>> {
        let (p, v) = self.predicate(keys)?;
        let rows = self.connection.borrow_mut().query(
            &format!(
                "SELECT {} FROM metadata WHERE {p} LIMIT 1",
                META.iter()
                    .map(|k| self.provider.quote(k))
                    .collect::<Vec<_>>()
                    .join(",")
            ),
            &v,
        )?;
        let Some(r) = rows.first() else {
            return Ok(None);
        };
        let V::Blob(blob) = &r[10] else {
            return Err("invalid percentile blob".into());
        };
        if blob.len() % 4 != 0 {
            return Err("invalid percentile blob length".into());
        }
        Ok(Some(Metadata {
            keys: self
                .keys
                .iter()
                .zip(keys)
                .map(|(k, v)| (k.key.clone(), v.clone()))
                .collect(),
            bounds: [
                number(&r[0])?,
                number(&r[1])?,
                number(&r[2])?,
                number(&r[3])?,
            ],
            convex_hull: serde_json::from_str(&text(&r[4])?).map_err(|e| e.to_string())?,
            valid_percentage: number(&r[5])?,
            range: [number(&r[6])?, number(&r[7])?],
            mean: number(&r[8])?,
            stdev: number(&r[9])?,
            percentiles: blob
                .chunks_exact(4)
                .map(|b| f32::from_ne_bytes(b.try_into().unwrap()) as f64)
                .collect(),
            metadata: serde_json::from_str(&text(&r[11])?).map_err(|e| e.to_string())?,
        }))
    }
    pub fn metadata(&self, keys: &[String]) -> Result<Option<Metadata>> {
        if let Some(m) = self.metadata_stored(keys)? {
            return Ok(Some(m));
        }
        if self.dataset(keys)?.is_none() {
            return Ok(None);
        }
        if !self.writable {
            return Err("Lazy loading requires a writable database".into());
        }
        self.transaction(|s| {
            let (p, v) = s.predicate(keys)?;
            // Lock the dataset before rechecking: only one concurrent miss computes.
            s.connection.borrow_mut().query(
                &format!("SELECT path FROM datasets WHERE {p} FOR UPDATE"),
                &v,
            )?;
            if let Some(m) = s.metadata_stored(keys)? {
                return Ok(Some(m));
            }
            let Some(dataset) = s.dataset(keys)? else {
                return Ok(None);
            };
            let shape = crate::settings::var("TC_LAZY_LOADING_MAX_SHAPE")
                .map(|s| serde_json::from_str::<[usize; 2]>(&s).map_err(|e| e.to_string()))
                .unwrap_or(Ok([1024, 1024]))?;
            let m = crate::raster::Gdal::load()?.compute_metadata(
                &dataset.path,
                &crate::raster::MetadataOptions {
                    max_shape: Some(shape),
                    ..Default::default()
                },
            )?;
            s.insert(&dataset, Some(&m))?;
            s.metadata_stored(keys)
        })
    }
}
fn text(v: &V) -> Result<String> {
    match v {
        V::Text(s) => Ok(s.clone()),
        V::Blob(b) => String::from_utf8(b.clone()).map_err(|e| e.to_string()),
        _ => Err("invalid database text".into()),
    }
}
fn number(v: &V) -> Result<f64> {
    match v {
        V::Real(n) => Ok(*n),
        V::Integer(n) => Ok(*n as f64),
        _ => Err("invalid database number".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_postgres_connections_without_full_certificate_verification() {
        for mode in ["disable", "prefer", "require", "verify-ca"] {
            let path = SqlPath::parse(
                &format!("postgresql://192.0.2.1/catalog?sslmode={mode}"),
                Provider::Postgres,
            )
            .unwrap();
            let error = Connection::open(&path, None).err().unwrap();
            assert!(error.contains("require sslmode=verify-full"));
        }
    }

    #[test]
    fn rejects_remote_mysql_plaintext() {
        let path =
            SqlPath::parse("mysql://192.0.2.1/catalog?sslmode=disable", Provider::Mysql).unwrap();
        let error = Connection::open(&path, None).err().unwrap();
        assert!(error.contains("require verified TLS"));
    }

    #[test]
    fn only_literal_loopback_ips_can_use_plaintext_sql() {
        for host in ["127.0.0.1", "127.1.2.3", "::1"] {
            assert!(loopback_ip(host));
        }
        for host in ["localhost", "192.0.2.1", "example.com"] {
            assert!(!loopback_ip(host));
        }
    }
}

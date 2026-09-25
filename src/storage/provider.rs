//! Provider detection and credential-free display identities.
use crate::Result;
use std::path::PathBuf;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Provider {
    Sqlite,
    Remote,
    Mysql,
    Postgres,
}
impl Provider {
    pub fn detect(path: &str, explicit: Option<&str>) -> Result<Self> {
        if path.is_empty() {
            return Err("Database url or path must not be empty".into());
        }
        let name = explicit.unwrap_or_else(|| {
            match path
                .split(':')
                .next()
                .unwrap_or("")
                .to_ascii_lowercase()
                .as_str()
            {
                "s3" => "sqlite-remote",
                "mysql" => "mysql",
                "postgresql" => "postgresql",
                _ => "sqlite",
            }
        });
        match name {
            "sqlite" => Ok(Self::Sqlite),
            "sqlite-remote" => Ok(Self::Remote),
            "mysql" => Ok(Self::Mysql),
            "postgresql" => Ok(Self::Postgres),
            _ => Err(format!("Unknown database provider {name}")),
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Self::Sqlite => "sqlite",
            Self::Remote => "sqlite-remote",
            Self::Mysql => "mysql",
            Self::Postgres => "postgresql",
        }
    }
    pub fn quote(self, text: &str) -> String {
        let c = if self == Self::Mysql { '`' } else { '"' };
        format!("{c}{}{c}", text.replace(c, &format!("{c}{c}")))
    }
}

#[derive(Clone)]
pub struct SqlPath {
    pub provider: Provider,
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: Option<String>,
    pub password: Option<String>,
    pub query: Vec<(String, String)>,
}
impl SqlPath {
    pub fn parse(path: &str, provider: Provider) -> Result<Self> {
        let path = if path.contains("://") {
            path.to_owned()
        } else {
            format!("{}://{}", provider.name(), path.trim_start_matches("//"))
        };
        let url = url::Url::parse(&path).map_err(|_| "invalid database URL")?;
        if url.scheme() != provider.name() {
            return Err("unsupported URL scheme".into());
        }
        let database = url.path().trim_start_matches('/').to_owned();
        if database.is_empty() || database.trim_matches('/').contains('/') {
            return Err("database must be specified; invalid database path".into());
        }
        let setting = |suffix: &str| {
            crate::settings::var(format!("TC_SQL_{suffix}"))
                .ok()
                .or_else(|| {
                    crate::settings::var(format!("TC_{}_{suffix}", provider.name().to_uppercase()))
                        .ok()
                })
        };
        Ok(Self {
            provider,
            host: match url.host().ok_or("database host required")? {
                url::Host::Ipv6(ip) => ip.to_string(),
                other => other.to_string(),
            },
            port: url.port().unwrap_or(if provider == Provider::Mysql {
                3306
            } else {
                5432
            }),
            database,
            // Python urlparse retains percent escapes in userinfo. Preserve that quirk.
            user: if url.username().is_empty() {
                setting("USER")
            } else {
                Some(url.username().into())
            },
            password: url
                .password()
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
                .or_else(|| setting("PASSWORD")),
            query: url
                .query_pairs()
                .map(|(k, v)| (k.into_owned(), v.into_owned()))
                .collect(),
        })
    }
    pub fn identity(&self) -> String {
        format!(
            "{}+{}://{}:{}/{}",
            self.provider.name(),
            if self.provider == Provider::Mysql {
                "pymysql"
            } else {
                "psycopg2"
            },
            self.host,
            self.port,
            self.database.trim_end_matches('/')
        )
    }
}
pub fn local_path(path: &str) -> Result<PathBuf> {
    let path = PathBuf::from(path.strip_prefix("sqlite:///").unwrap_or(path));
    let absolute = if path.is_absolute() {
        path
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(path)
    };
    let mut normalized = PathBuf::new();
    for component in absolute.components() {
        use std::path::Component;
        match component {
            Component::ParentDir => {
                normalized.pop();
            }
            Component::CurDir => {}
            other => {
                normalized.push(other.as_os_str());
                if let Ok(real) = normalized.canonicalize() {
                    normalized = real;
                }
            }
        }
    }
    Ok(normalized)
}

pub fn timeout() -> Result<std::time::Duration> {
    let seconds = crate::settings::var("TC_DB_CONNECTION_TIMEOUT")
        .unwrap_or_else(|_| "10".into())
        .parse::<u64>()
        .map_err(|_| "invalid database connection timeout")?;
    Ok(std::time::Duration::from_secs(seconds))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn scheme_case_and_ipv6_match_urlparse() {
        assert_eq!(
            Provider::detect("S3://bucket/key", None).unwrap(),
            Provider::Remote
        );
        assert_eq!(
            Provider::detect("POSTGRESQL://localhost/db", None).unwrap(),
            Provider::Postgres
        );
        let p = SqlPath::parse("postgresql://user@[::1]:55436/db", Provider::Postgres).unwrap();
        assert_eq!(p.host, "::1");
        assert_eq!(p.port, 55436);
        assert_eq!(p.user.as_deref(), Some("user"));
        assert!(!p.identity().contains("user"));
    }
}

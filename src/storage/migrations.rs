//! Frozen 0.7 -> 0.10 upgrades. No production mutation is performed by tests.
use super::{
    provider::{Provider, SqlPath, local_path},
    relational::Connection,
};
use crate::{REFERENCE_VERSION, Result};
use std::io::{BufRead, Write};

type Version = (u32, u32);
fn version(text: &str) -> Result<Version> {
    let parts = text
        .trim_start_matches('v')
        .split('.')
        .take(2)
        .map(|v| v.parse::<u32>().map_err(|_| "invalid version".to_string()))
        .collect::<Result<Vec<_>>>()?;
    if parts.len() != 2 {
        return Err("invalid version".into());
    }
    Ok((parts[0], parts[1]))
}
pub fn chain(from: &str, to: &str) -> Result<Vec<String>> {
    let from = version(from)?;
    let to = version(to)?;
    if to > version(REFERENCE_VERSION)? {
        return Err(format!(
            "Unknown target version v{}.{} (this is {}). Try upgrading terracotta.",
            to.0, to.1, REFERENCE_VERSION
        ));
    }
    if from == to {
        return Ok(Vec::new());
    }
    if from.0 != 0 || to.0 != 0 || from.1 < 7 || to.1 > 10 || from > to {
        return Err("Unexpected error, missing migration for requested versions".into());
    }
    let mut sql = Vec::new();
    for minor in from.1..to.1 {
        if minor == 7 {
            sql.extend([
            "CREATE TABLE key_names (key_name TEXT PRIMARY KEY, description TEXT, idx INTEGER UNIQUE)",
            "INSERT INTO key_names (key_name, description, idx) SELECT key, description, row_number() over (order by (select NULL)) FROM keys",
            "ALTER TABLE datasets RENAME COLUMN filepath TO path",
        ].map(str::to_owned));
        }
        if minor == 8 {
            let cols = [
                "bounds_north",
                "bounds_east",
                "bounds_south",
                "bounds_west",
                "valid_percentage",
                "min",
                "max",
                "mean",
                "stdev",
            ];
            for c in cols {
                sql.push(format!("ALTER TABLE metadata ADD COLUMN {c}_ FLOAT(53)"));
            }
            sql.push(format!(
                "UPDATE metadata SET {}",
                cols.map(|c| format!("{c}_ = {c}")).join(", ")
            ));
            for c in cols {
                sql.push(format!("ALTER TABLE metadata DROP COLUMN {c}"));
            }
            for c in cols {
                sql.push(format!("ALTER TABLE metadata RENAME COLUMN {c}_ TO {c}"));
            }
        }
        sql.push(format!("UPDATE terracotta SET version='0.{}.0'", minor + 1));
    }
    Ok(sql)
}
enum MigrationConnection {
    Sqlite(rusqlite::Connection),
    Sql(Connection),
}
impl MigrationConnection {
    fn open(path: &str, provider: Option<&str>) -> Result<Self> {
        match Provider::detect(path, provider)? {
            Provider::Remote => Err("Database is not writable".into()),
            Provider::Sqlite => Ok(Self::Sqlite(
                rusqlite::Connection::open_with_flags(
                    local_path(path)?,
                    rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE,
                )
                .map_err(|e| e.to_string())?,
            )),
            p => Ok(Self::Sql(Connection::open(
                &SqlPath::parse(path, p)?,
                None,
            )?)),
        }
    }
    fn version(&mut self) -> Result<String> {
        match self {
            Self::Sqlite(c) => c
                .query_row("SELECT version FROM terracotta LIMIT 1", [], |r| r.get(0))
                .map_err(|_| "Cannot determine database version.".into()),
            Self::Sql(c) => {
                let r = c.query("SELECT version FROM terracotta LIMIT 1", &[])?;
                match r.first().and_then(|r| r.first()) {
                    Some(rusqlite::types::Value::Text(v)) => Ok(v.clone()),
                    Some(rusqlite::types::Value::Blob(v)) => String::from_utf8(v.clone())
                        .map_err(|_| "Cannot determine database version.".into()),
                    _ => Err("Cannot determine database version.".into()),
                }
            }
        }
    }
    fn execute(&mut self, sql: &str) -> Result<()> {
        match self {
            Self::Sqlite(c) => c.execute_batch(sql).map_err(|e| e.to_string()),
            Self::Sql(c) => c.execute(sql, &[]),
        }
    }
}
pub fn command(args: &[String]) -> Result<()> {
    let (mut database, mut from, mut to, mut provider) =
        (None, None, REFERENCE_VERSION.to_owned(), None);
    let mut yes = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-y" | "--yes" => yes = true,
            "--from" | "--to" | "--provider" => {
                let key = &args[i];
                i += 1;
                let val = args.get(i).ok_or("missing option value")?.clone();
                match key.as_str() {
                    "--from" => from = Some(val),
                    "--to" => to = val,
                    _ => provider = Some(val),
                }
            }
            "--help" | "-h" => {
                println!(
                    "Usage: terracotta migrate DATABASE [--from VERSION] [--to VERSION] [-y|--yes]"
                );
                return Ok(());
            }
            s if !s.starts_with('-') && database.is_none() => database = Some(s.to_owned()),
            s => return Err(format!("unknown migrate argument: {s}")),
        }
        i += 1;
    }
    // Validate target before accessing the database, matching the legacy CLI.
    if version(&to)? > version(REFERENCE_VERSION)? {
        return Err(format!(
            "Unknown target version {to}. Try upgrading terracotta."
        ));
    }
    let path = database.ok_or("migrate requires DATABASE")?;
    let mut c = MigrationConnection::open(&path, provider.as_deref())?;
    let from = from.map(Ok).unwrap_or_else(|| c.version())?;
    let sql = chain(&from, &to)?;
    if sql.is_empty() {
        println!("Already at target version, nothing to do.");
        return Ok(());
    }
    println!("Upgrade path found\n");
    let mut previous = version(&from)?.1;
    for cmd in &sql {
        if cmd.starts_with("UPDATE terracotta SET version=") {
            println!("v0.{previous} -> v0.{}", previous + 1);
            previous += 1;
        }
        println!("    {cmd}");
    }
    println!(
        "This will upgrade the database from {from} -> {to} and execute the above SQL commands."
    );
    if !yes {
        print!("Continue? [y/N]: ");
        std::io::stdout().flush().map_err(|e| e.to_string())?;
        let mut line = String::new();
        std::io::stdin()
            .lock()
            .read_line(&mut line)
            .map_err(|e| e.to_string())?;
        if !["y", "yes"].contains(&line.trim().to_lowercase().as_str()) {
            return Err("Aborted!".into());
        }
    }
    c.execute("BEGIN")?;
    for cmd in &sql {
        if let Err(e) = c.execute(cmd) {
            let _ = c.execute("ROLLBACK");
            return Err(format!(
                "Migration failed: {e}. MySQL DDL may have committed partial schema changes; restore the pre-migration copy before retrying."
            ));
        }
    }
    c.execute("COMMIT")
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn supported_chain() {
        assert_eq!(chain("v0.7.5", "0.10.1").unwrap().len(), 34);
        assert!(chain("0.10", "0.9").is_err());
        assert!(chain("0.9", "0.11").is_err());
        assert!(chain("0.10.9", "0.10.1").unwrap().is_empty());
    }
}

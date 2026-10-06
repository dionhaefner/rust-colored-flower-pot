//! Source detection and local deployment configuration for all application adapters.
use super::*;
use serde::Deserialize;
use serde_json::Value;
use std::{
    io::Read,
    path::{Path, PathBuf},
};

pub(crate) enum Opened {
    Datasets(Box<Catalog>),
    Layers(crate::layers::catalog::Catalog),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Definition {
    #[serde(rename = "type")]
    kind: String,
    source: String,
    search: Option<Value>,
    #[serde(default)]
    options: crate::stac::serve::ServeOptions,
}

pub(super) fn open(location: &Path) -> Result<Opened> {
    open_definition(location, 0)
}

fn open_definition(location: &Path, depth: usize) -> Result<Opened> {
    if depth >= 8 {
        return Err("source configuration nesting limit exceeded".into());
    }
    let name = location.to_str().ok_or("invalid source location")?;
    if name.starts_with("https://") || name.starts_with("http://") {
        return stac(crate::stac::serve::ServeOptions::default().resolve_location(name, None)?);
    }
    let local = if name.starts_with("file:") {
        let url = url::Url::parse(name).map_err(|_| "invalid source file URL")?;
        if url.query().is_some() || url.fragment().is_some() {
            return Err("source file URLs cannot contain queries or fragments".into());
        }
        url.to_file_path().map_err(|_| "invalid source file URL")?
    } else {
        location.to_path_buf()
    };
    if json_file(&local)? {
        let url = crate::stac::catalog::document_url(name)?;
        let (value, bytes) = crate::stac::catalog::read_document(&url, &BTreeMap::new())?;
        if value["type"] == "rcfp-source" {
            let mut definition: Definition =
                serde_json::from_value(value).map_err(|_| "invalid source configuration")?;
            if definition.kind != "rcfp-source" || definition.source.is_empty() {
                return Err("invalid source configuration".into());
            }
            let base = local.parent().unwrap_or(Path::new("."));
            let target = if definition.source.contains("://")
                || definition.source.starts_with("file:")
                || Path::new(&definition.source).is_absolute()
            {
                PathBuf::from(&definition.source)
            } else {
                base.join(&definition.source)
            };
            definition.options.relative_paths(base);
            if definition.search.is_some() || definition.options.has_options() {
                return stac(definition.options.resolve_location(
                    target.to_str().ok_or("invalid source location")?,
                    definition.search,
                )?);
            }
            return open_definition(&target, depth + 1);
        }
        crate::compatibility::CompatibilityProfile::current().require_native()?;
        let selection = crate::stac::Selection::from_loaded(
            url,
            value,
            bytes,
            &BTreeMap::new(),
            crate::stac::Options {
                discover_assets: true,
                ..Default::default()
            },
            10000,
        )?;
        return stac(crate::stac::serve::Service::new(selection, None));
    }
    if crate::layers::catalog::Catalog::is_native(&local)? && native_layer_format(&local)? {
        Ok(Opened::Layers(crate::layers::catalog::Catalog::open(
            &local, false,
        )?))
    } else {
        Ok(Opened::Datasets(Box::new(Catalog::sql(Store::open(
            &local,
        )?)?)))
    }
}

fn stac(service: crate::stac::serve::Service) -> Result<Opened> {
    Ok(Opened::Datasets(Box::new(Catalog::stac(service)?)))
}

fn json_file(path: &Path) -> Result<bool> {
    if !path.is_file() {
        return Ok(false);
    }
    let mut prefix = [0; 1024];
    let count = std::fs::File::open(path)
        .and_then(|mut file| file.read(&mut prefix))
        .map_err(|_| "source file unavailable")?;
    if prefix[..count].starts_with(b"SQLite format 3\0") {
        return Ok(false);
    }
    Ok(prefix[..count]
        .iter()
        .find(|byte| !byte.is_ascii_whitespace())
        == Some(&b'{')
        || path
            .extension()
            .is_some_and(|value| value.eq_ignore_ascii_case("json")))
}

fn native_layer_format(path: &Path) -> Result<bool> {
    let c = rusqlite::Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| e.to_string())?;
    let format: String = c
        .query_row(
            "SELECT MIN(format) FROM rcfp_catalog HAVING COUNT(*)=1",
            [],
            |r| r.get(0),
        )
        .map_err(|_| "invalid native catalog marker")?;
    Ok(format == crate::layers::catalog::FORMAT)
}

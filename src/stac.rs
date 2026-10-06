//! Bounded STAC discovery and checksum-pinned asset selections.
use crate::{Result, execution::RasterExecutor, image, raster::ReadRequest};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Read, Write},
    path::PathBuf,
    sync::Mutex,
    time::{Duration, Instant},
};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use url::Url;

pub(crate) mod catalog;
pub mod serve;

const PAGE_BYTES: u64 = 8 * 1024 * 1024;
const SEARCH_BYTES: u64 = 32 * 1024 * 1024;

fn digest(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}
fn canonical(value: &Value) -> Value {
    match value {
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), canonical(v)))
                .collect::<BTreeMap<_, _>>()
                .into_iter()
                .collect(),
        ),
        Value::Array(values) => Value::Array(values.iter().map(canonical).collect()),
        _ => value.clone(),
    }
}
pub(crate) fn identity(value: &Value) -> String {
    digest(canonical(value).to_string().as_bytes())
}
fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value[key]
        .as_str()
        .filter(|s| !s.is_empty() && s.len() <= 1024)
        .ok_or_else(|| format!("STAC requires {key}"))
}
fn timestamp(value: &str) -> Result<String> {
    OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| "invalid STAC datetime")?
        .to_offset(time::UtcOffset::UTC)
        .format(&Rfc3339)
        .map_err(|_| "invalid STAC datetime".into())
}
fn bbox(value: &Value) -> Result<[f64; 4]> {
    let b: [f64; 4] =
        serde_json::from_value(value.clone()).map_err(|_| "STAC bbox must have four numbers")?;
    if b.iter().any(|v| !v.is_finite())
        || b[0].abs() > 180.
        || b[2].abs() > 180.
        || b[1].abs() > 90.
        || b[3].abs() > 90.
        || b[1] > b[3]
    {
        return Err("invalid STAC bbox".into());
    }
    Ok(b)
}
fn http_url(value: &str) -> Result<Url> {
    let u = Url::parse(value).map_err(|_| "invalid STAC URL")?;
    let loopback = matches!(u.host_str(), Some("127.0.0.1" | "[::1]"));
    if !(u.scheme() == "https" || u.scheme() == "http" && loopback)
        || !u.username().is_empty()
        || u.password().is_some()
        || u.fragment().is_some()
    {
        return Err("STAC URLs require HTTPS without userinfo or fragments (HTTP is allowed on literal loopback)".into());
    }
    Ok(u)
}
fn agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .redirects(0)
        .timeout(Duration::from_secs(30))
        .build()
}
fn remote(
    method: &str,
    url: &Url,
    headers: &BTreeMap<String, String>,
    body: &Value,
) -> Result<ureq::Response> {
    let mut request = agent()
        .request(method, url.as_str())
        .set("Accept", "application/geo+json, application/json");
    for (key, value) in headers {
        if !["authorization", "x-api-key"].contains(&key.to_ascii_lowercase().as_str())
            || value.len() > 8192
            || value.bytes().any(|b| b.is_ascii_control())
        {
            return Err("unsupported STAC credential header".into());
        }
        request = request.set(key, value);
    }
    let response = if method == "POST" {
        request
            .set("Content-Type", "application/json")
            .send_string(&body.to_string())
    } else {
        request.call()
    };
    let response = response.map_err(|e| match e {
        ureq::Error::Status(status, _) => format!("STAC upstream HTTP {status}"),
        _ => "STAC upstream transport failure".to_string(),
    })?;
    if response.status() != 200 {
        return Err(format!("STAC upstream HTTP {}", response.status()));
    }
    Ok(response)
}

#[derive(Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Options {
    pub assets: Vec<String>,
    #[serde(skip)]
    pub(crate) discover_assets: bool,
    /// Map collection/item/asset to a SHA-256 digest when file:checksum is absent.
    pub revisions: BTreeMap<String, String>,
    pub max_items: usize,
    pub max_pages: usize,
    pub max_staged_bytes: u64,
    pub base_url: Option<String>,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            assets: vec![],
            discover_assets: false,
            revisions: BTreeMap::new(),
            max_items: 1000,
            max_pages: 100,
            max_staged_bytes: 512 * 1024 * 1024,
            base_url: None,
        }
    }
}
impl Options {
    fn validate(&self) -> Result<()> {
        if self.max_items == 0
            || self.max_items > 10000
            || self.max_pages == 0
            || self.max_pages > 1000
            || self.max_staged_bytes == 0
            || self.max_staged_bytes > 16 * 1024 * 1024 * 1024
            || (self.assets.is_empty() && !self.discover_assets)
            || self.assets.iter().any(String::is_empty)
            || self.assets.len() > 16
        {
            return Err("invalid STAC selection limits or asset keys".into());
        }
        let unique: BTreeSet<_> = self.assets.iter().collect();
        if unique.len() != self.assets.len() {
            return Err("duplicate STAC asset keys".into());
        }
        Ok(())
    }
}

#[derive(Clone, Serialize)]
pub struct Asset {
    pub collection: String,
    pub item: String,
    pub asset: String,
    pub revision: String,
    pub datetime: String,
    pub end_datetime: String,
    pub bbox: [f64; 4],
    pub geometry: Value,
    pub crs: Value,
    pub bands: Vec<Band>,
    #[serde(skip)]
    href: String,
}
pub use crate::catalog::plan::BandInfo as Band;
#[derive(Default, Serialize)]
pub struct SelectionMetrics {
    pub pages: usize,
    pub returned_items: usize,
    pub response_bytes: u64,
    pub selection_ms: f64,
}
struct Materialized {
    paths: BTreeMap<String, PathBuf>,
    bytes: u64,
}
pub struct Selection {
    pub id: String,
    pub search_id: Option<String>,
    assets: Vec<Asset>,
    metrics: SelectionMetrics,
    options: Options,
    directory: tempfile::TempDir,
    materialized: Mutex<Materialized>,
}

impl Selection {
    pub fn from_items(items: &[Value], options: Options) -> Result<Self> {
        let start = Instant::now();
        options.validate()?;
        if items.len() > options.max_items {
            return Err("STAC result limit exceeded".into());
        }
        let mut assets = BTreeMap::new();
        let mut seen = BTreeMap::new();
        for item in items {
            if item["type"] != "Feature"
                || !matches!(item["stac_version"].as_str(), Some("1.0.0" | "1.1.0"))
            {
                return Err("unsupported STAC Item type or version".into());
            }
            if !item["properties"].is_object()
                || !item["assets"].is_object()
                || !item["links"].is_array()
            {
                return Err("STAC Item requires properties, assets and links".into());
            }
            let id = text(item, "id")?;
            let collection = item["collection"].as_str().unwrap_or("");
            let properties = &item["properties"];
            let (start_time, end_time) = if let Some(t) = properties["datetime"].as_str() {
                (timestamp(t)?, timestamp(t)?)
            } else {
                (
                    timestamp(text(properties, "start_datetime")?)?,
                    timestamp(text(properties, "end_datetime")?)?,
                )
            };
            if OffsetDateTime::parse(&start_time, &Rfc3339).unwrap()
                > OffsetDateTime::parse(&end_time, &Rfc3339).unwrap()
            {
                return Err("reversed STAC time interval".into());
            }
            let bounds = bbox(&item["bbox"])?;
            if !matches!(
                item["geometry"]["type"].as_str(),
                Some("Polygon" | "MultiPolygon")
            ) || !item["geometry"]["coordinates"].is_array()
            {
                return Err("STAC selection requires a polygon footprint".into());
            }
            let mut item_assets = Vec::new();
            let discovered;
            let keys = if options.discover_assets {
                discovered = item["assets"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .filter(|(_, asset)| raster_asset(asset))
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                if discovered.len() > 16 {
                    return Err("STAC Item exceeds 16 raster assets".into());
                }
                &discovered
            } else {
                &options.assets
            };
            for key in keys {
                if key.is_empty() {
                    return Err("invalid STAC asset key".into());
                }
                let Some(a) = item["assets"].get(key) else {
                    continue;
                };
                let revision_key = format!("{collection}/{id}/{key}");
                let checksum = options
                    .revisions
                    .get(&revision_key)
                    .map(String::as_str)
                    .or_else(|| {
                        a["file:checksum"]
                            .as_str()
                            .and_then(|v| v.strip_prefix("1220"))
                    })
                    .ok_or(
                        "STAC assets require SHA-256 file:checksum or an explicit revision digest",
                    )?;
                if checksum.len() != 64 || !checksum.bytes().all(|b| b.is_ascii_hexdigit()) {
                    return Err("STAC revision must be a SHA-256 digest".into());
                }
                if let Some(declared) = a["file:checksum"]
                    .as_str()
                    .and_then(|v| v.strip_prefix("1220"))
                {
                    if !checksum.eq_ignore_ascii_case(declared) {
                        return Err("STAC explicit revision conflicts with file:checksum".into());
                    }
                }
                let href = a["href"]
                    .as_str()
                    .filter(|h| !h.is_empty() && h.len() <= 16384)
                    .ok_or("STAC asset href required")?;
                let href = if let Some(base) = &options.base_url {
                    Url::parse(base)
                        .and_then(|u| u.join(href))
                        .map_err(|_| "invalid STAC asset base URL")?
                        .to_string()
                } else {
                    href.into()
                };
                if href.contains("://") {
                    let u = Url::parse(&href).map_err(|_| "invalid STAC asset URL")?;
                    if !["file", "s3"].contains(&u.scheme()) {
                        http_url(&href)?;
                    }
                }
                let raster_bands = a["raster:bands"].as_array();
                let eo_bands = a["eo:bands"].as_array();
                let count = raster_bands
                    .map(Vec::len)
                    .or_else(|| eo_bands.map(Vec::len))
                    .unwrap_or(1);
                if count == 0
                    || count > 256
                    || raster_bands
                        .zip(eo_bands)
                        .is_some_and(|(r, e)| r.len() != e.len())
                {
                    return Err("invalid STAC band descriptors".into());
                }
                let mut bands = Vec::new();
                for n in 0..count {
                    let r = raster_bands.and_then(|b| b.get(n)).unwrap_or(&Value::Null);
                    let e = eo_bands.and_then(|b| b.get(n)).unwrap_or(&Value::Null);
                    let scale = r
                        .get("scale")
                        .map(|v| v.as_f64().ok_or("invalid STAC scale"))
                        .transpose()?
                        .unwrap_or(1.);
                    let offset = r
                        .get("offset")
                        .map(|v| v.as_f64().ok_or("invalid STAC offset"))
                        .transpose()?
                        .unwrap_or(0.);
                    let nodata = r["nodata"].clone();
                    if !(nodata.is_null()
                        || nodata.is_number()
                        || matches!(nodata.as_str(), Some("nan" | "inf" | "-inf")))
                    {
                        return Err("invalid STAC nodata".into());
                    }
                    if !scale.is_finite() || !offset.is_finite() {
                        return Err("invalid STAC scale or offset".into());
                    }
                    bands.push(Band {
                        band: n + 1,
                        name: e["name"].as_str().map(Into::into),
                        common_name: e["common_name"].as_str().map(Into::into),
                        scale,
                        offset,
                        nodata,
                    });
                }
                let crs = [a, properties]
                    .into_iter()
                    .find_map(|source| {
                        ["proj:code", "proj:epsg", "proj:wkt2", "proj:projjson"]
                            .iter()
                            .find_map(|k| source.get(*k))
                    })
                    .cloned()
                    .unwrap_or(Value::Null);
                item_assets.push(Asset {
                    collection: collection.into(),
                    item: id.into(),
                    asset: key.clone(),
                    revision: checksum.to_ascii_lowercase(),
                    datetime: start_time.clone(),
                    end_datetime: end_time.clone(),
                    bbox: bounds,
                    geometry: item["geometry"].clone(),
                    crs,
                    bands,
                    href,
                });
            }
            let item_identity = identity(&json!(item_assets));
            if let Some(previous) = seen.insert(
                (collection.to_owned(), id.to_owned()),
                item_identity.clone(),
            ) {
                if previous != item_identity {
                    return Err("conflicting STAC Item revisions in one selection".into());
                }
            }
            for a in item_assets {
                assets.insert(
                    (
                        a.datetime.clone(),
                        a.collection.clone(),
                        a.item.clone(),
                        a.asset.clone(),
                    ),
                    a,
                );
            }
        }
        // Compare actual instants, including fractional seconds, rather than text.
        let mut assets: Vec<_> = assets.into_values().collect();
        assets.sort_by_key(|a| {
            (
                OffsetDateTime::parse(&a.datetime, &Rfc3339).unwrap(),
                a.collection.clone(),
                a.item.clone(),
                a.asset.clone(),
            )
        });
        let id = identity(&json!(assets));
        Ok(Self {
            id,
            search_id: None,
            assets,
            metrics: SelectionMetrics {
                returned_items: items.len(),
                selection_ms: start.elapsed().as_secs_f64() * 1000.,
                ..Default::default()
            },
            options,
            directory: tempfile::tempdir().map_err(|_| "STAC staging directory unavailable")?,
            materialized: Mutex::new(Materialized {
                paths: BTreeMap::new(),
                bytes: 0,
            }),
        })
    }

    pub fn from_document(
        location: &str,
        headers: &BTreeMap<String, String>,
        mut options: Options,
    ) -> Result<Self> {
        options.validate()?;
        let url = catalog::document_url(location)?;
        let (document, _) = catalog::read_document(&url, headers)?;
        options.base_url = Some(url.to_string());
        match document["type"].as_str() {
            Some("Feature") => Self::from_items(&[document], options),
            Some("FeatureCollection") => Self::from_items(document["features"].as_array().ok_or("STAC ItemCollection requires features")?, options),
            _ => Err("STAC source must be an Item or ItemCollection; use --stac-catalog for a Catalog or --stac-search for an API".into()),
        }
    }

    pub fn describe(&self) -> Value {
        json!({"id":self.id,"search_id":self.search_id,"assets":self.assets,"metrics":self.metrics})
    }

    pub fn search(
        endpoint: &str,
        query: Value,
        headers: &BTreeMap<String, String>,
        options: Options,
    ) -> Result<Self> {
        let start = Instant::now();
        options.validate()?;
        let endpoint = http_url(endpoint)?;
        if endpoint.query().is_some() {
            return Err("STAC endpoint query must be supplied as search parameters".into());
        }
        let mut query = query
            .as_object()
            .cloned()
            .ok_or("STAC search requires an object")?;
        if query
            .keys()
            .any(|k| !["bbox", "datetime", "collections", "ids", "limit"].contains(&k.as_str()))
        {
            return Err("unsupported STAC search parameter".into());
        }
        if let Some(b) = query.get("bbox") {
            bbox(b)?;
        }
        if let Some(t) = query.get("datetime") {
            let t = t.as_str().ok_or("invalid STAC datetime")?;
            let parts: Vec<_> = t.split('/').collect();
            if parts.len() > 2
                || parts.iter().any(|p| *p != ".." && timestamp(p).is_err())
                || parts.iter().all(|p| *p == "..")
            {
                return Err("invalid STAC datetime".into());
            }
            if parts.len() == 2
                && parts[0] != ".."
                && parts[1] != ".."
                && OffsetDateTime::parse(parts[0], &Rfc3339).unwrap()
                    > OffsetDateTime::parse(parts[1], &Rfc3339).unwrap()
            {
                return Err("reversed STAC search time interval".into());
            }
        }
        for key in ["collections", "ids"] {
            if let Some(v) = query.get(key) {
                if !v.as_array().is_some_and(|a| {
                    !a.is_empty()
                        && a.len() <= 100
                        && a.iter()
                            .all(|v| v.as_str().is_some_and(|s| !s.is_empty() && s.len() <= 1024))
                }) {
                    return Err("invalid STAC search identifiers".into());
                }
            }
        }
        let limit = query
            .get("limit")
            .map(|v| v.as_u64().ok_or("invalid STAC page size"))
            .transpose()?
            .unwrap_or(100);
        if limit == 0 || limit > 1000 {
            return Err("invalid STAC page size".into());
        }
        query.insert("limit".into(), json!(limit));
        let query = Value::Object(query);
        let search_id = identity(
            &json!({"endpoint":endpoint.as_str(),"query":query,"assets":options.assets,"discover_assets":options.discover_assets,"max_items":options.max_items,"max_pages":options.max_pages,"revisions":options.revisions}),
        );
        let mut url = endpoint.clone();
        let mut method = "POST".to_string();
        let mut body = query.clone();
        let mut visited = BTreeSet::new();
        let mut items = Vec::new();
        let mut metrics = SelectionMetrics::default();
        loop {
            if metrics.pages >= options.max_pages {
                return Err("STAC page limit exceeded".into());
            }
            if !visited.insert(identity(&json!([url.as_str(), method, body]))) {
                return Err("STAC pagination cycle".into());
            }
            let response = remote(&method, &url, headers, &body)?;
            let mut bytes = Vec::new();
            response
                .into_reader()
                .take(PAGE_BYTES + 1)
                .read_to_end(&mut bytes)
                .map_err(|_| "STAC upstream response read failure")?;
            metrics.response_bytes += bytes.len() as u64;
            if bytes.len() as u64 > PAGE_BYTES || metrics.response_bytes > SEARCH_BYTES {
                return Err("STAC response byte limit exceeded".into());
            }
            let page: Value =
                serde_json::from_slice(&bytes).map_err(|_| "invalid STAC upstream JSON")?;
            if page["type"] != "FeatureCollection" {
                return Err("STAC search response must be a FeatureCollection".into());
            }
            let features = page["features"]
                .as_array()
                .ok_or("STAC search response requires features")?;
            if items.len() + features.len() > options.max_items {
                return Err("STAC result limit exceeded".into());
            }
            // Resolve relative asset links against each Item's self link, then page URL.
            for feature in features {
                let mut feature = feature.clone();
                let item_base = feature["links"]
                    .as_array()
                    .and_then(|links| links.iter().find(|l| l["rel"] == "self"))
                    .and_then(|l| l["href"].as_str())
                    .map(|h| url.join(h))
                    .transpose()
                    .map_err(|_| "invalid STAC Item self link")?
                    .unwrap_or_else(|| url.clone());
                if let Some(assets) = feature["assets"].as_object_mut() {
                    for asset in assets.values_mut() {
                        if let Some(href) = asset["href"].as_str() {
                            asset["href"] = json!(
                                item_base
                                    .join(href)
                                    .map_err(|_| "invalid STAC asset link")?
                                    .as_str()
                            );
                        }
                    }
                }
                items.push(feature);
            }
            metrics.pages += 1;
            let links = page["links"]
                .as_array()
                .ok_or("STAC search response requires links")?;
            let next: Vec<_> = links.iter().filter(|l| l["rel"] == "next").collect();
            if next.len() > 1 {
                return Err("ambiguous STAC pagination links".into());
            }
            let Some(next) = next.first() else {
                break;
            };
            let next_url = url
                .join(text(next, "href")?)
                .map_err(|_| "invalid STAC pagination URL")?;
            http_url(next_url.as_str())?;
            if next_url.origin() != endpoint.origin() {
                return Err("STAC pagination must remain on the provider origin".into());
            }
            if next.get("headers").is_some() {
                return Err("STAC pagination headers are unsupported".into());
            }
            method = next["method"].as_str().unwrap_or("GET").to_uppercase();
            if !["GET", "POST"].contains(&method.as_str()) {
                return Err("unsupported STAC pagination method".into());
            }
            body = if method == "POST" {
                let incoming = next.get("body").cloned().unwrap_or(json!({}));
                if !incoming.is_object() {
                    return Err("STAC pagination body must be an object".into());
                }
                if next["merge"] == true {
                    let mut merged = body.as_object().cloned().unwrap_or_default();
                    merged.extend(incoming.as_object().unwrap().clone());
                    Value::Object(merged)
                } else {
                    incoming
                }
            } else {
                Value::Null
            };
            url = next_url;
        }
        let mut selection = Self::from_items(&items, options)?;
        metrics.returned_items = items.len();
        metrics.selection_ms = start.elapsed().as_secs_f64() * 1000.;
        selection.metrics = metrics;
        selection.search_id = Some(search_id);
        Ok(selection)
    }

    fn materialize(&self, asset: &Asset, signed_href: Option<&str>) -> Result<PathBuf> {
        let mut state = self
            .materialized
            .lock()
            .map_err(|_| "STAC staging lock poisoned")?;
        if let Some(path) = state.paths.get(&asset.revision) {
            return Ok(path.clone());
        }
        let href = signed_href.unwrap_or(&asset.href);
        let mut reader: Box<dyn Read> = if href.contains("://") {
            let u = Url::parse(href).map_err(|_| "invalid STAC asset URL")?;
            if u.scheme() == "file" {
                Box::new(
                    std::fs::File::open(u.to_file_path().map_err(|_| "invalid STAC file URL")?)
                        .map_err(|_| "STAC asset unavailable")?,
                )
            } else {
                Box::new(
                    remote("GET", &http_url(href)?, &BTreeMap::new(), &Value::Null)?.into_reader(),
                )
            }
        } else {
            Box::new(std::fs::File::open(href).map_err(|_| "STAC asset unavailable")?)
        };
        let remaining = self.options.max_staged_bytes.saturating_sub(state.bytes);
        let mut file = tempfile::NamedTempFile::new_in(self.directory.path())
            .map_err(|_| "STAC staging file unavailable")?;
        let mut hash = Sha256::new();
        let mut length = 0u64;
        let mut buffer = [0; 65536];
        loop {
            let n = reader
                .read(&mut buffer)
                .map_err(|_| "STAC asset read failure")?;
            if n == 0 {
                break;
            }
            length += n as u64;
            if length > remaining {
                return Err("STAC staged asset byte limit exceeded".into());
            }
            hash.update(&buffer[..n]);
            file.write_all(&buffer[..n])
                .map_err(|_| "STAC asset staging failure")?;
        }
        if format!("{:x}", hash.finalize()) != asset.revision {
            return Err("STAC asset revision checksum mismatch".into());
        }
        let path = self
            .directory
            .path()
            .join(format!("{}.tif", asset.revision));
        file.persist(&path)
            .map_err(|_| "STAC asset publication failure")?;
        state.bytes += length;
        state.paths.insert(asset.revision.clone(), path.clone());
        Ok(path)
    }

    pub fn render(&self, executor: &RasterExecutor, request: &Value) -> Result<Vec<u8>> {
        let index = request["asset_index"]
            .as_u64()
            .ok_or("STAC asset_index required")? as usize;
        let asset = self
            .assets
            .get(index)
            .ok_or("STAC asset_index out of range")?;
        let band = request["band"].as_u64().unwrap_or(1) as usize;
        let descriptor = asset
            .bands
            .get(band.wrapping_sub(1))
            .ok_or("STAC band out of range")?;
        let size: [usize; 2] =
            serde_json::from_value(request.get("size").cloned().unwrap_or(json!([256, 256])))
                .map_err(|_| "invalid STAC render dimensions")?;
        let range: [f64; 2] = serde_json::from_value(request["range"].clone())
            .map_err(|_| "STAC render requires numeric range")?;
        let bounds: [f64; 4] = serde_json::from_value(request["bounds_mercator"].clone())
            .map_err(|_| "STAC render requires map bounds")?;
        if size.contains(&0)
            || size.iter().any(|n| *n > 4096)
            || size[0].checked_mul(size[1]).is_none_or(|n| n > 1024 * 1024)
            || range.iter().chain(&bounds).any(|v| !v.is_finite())
            || range[0] >= range[1]
            || bounds[0] >= bounds[2]
            || bounds[1] >= bounds[3]
        {
            return Err("invalid STAC render grid or range".into());
        }
        let path = self.materialize(asset, request["signed_href"].as_str())?;
        validate_band(&path, descriptor)?;
        let raster = executor.read(
            &path,
            ReadRequest {
                band,
                bounds_mercator: Some(bounds),
                size,
                preserve_values: false,
                ..Default::default()
            },
        )?;
        let raster = calibrate(raster, descriptor);
        image::viridis_png(
            raster.width,
            raster.height,
            &image::stretch_to_u8(&raster, range),
        )
    }
}

fn raster_asset(asset: &Value) -> bool {
    if let Some(kind) = asset["type"].as_str() {
        return kind
            .split(';')
            .next()
            .is_some_and(|v| v.trim() == "image/tiff");
    }
    let href = asset["href"]
        .as_str()
        .unwrap_or("")
        .split('?')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();
    href.ends_with(".tif") || href.ends_with(".tiff") || asset["raster:bands"].is_array()
}

pub(crate) fn validate_band(path: &std::path::Path, descriptor: &Band) -> Result<()> {
    // Reject nested source graphs before opening the file in GDAL.
    let mut header = [0; 4];
    std::fs::File::open(path)
        .and_then(|mut f| f.read_exact(&mut header))
        .map_err(|_| "STAC rendering requires a self-contained GeoTIFF")?;
    if ![b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"].contains(&&header) {
        return Err("STAC rendering requires a self-contained GeoTIFF".into());
    }
    if !descriptor.nodata.is_null() {
        let expected = match descriptor.nodata.as_str() {
            Some("nan") => f64::NAN,
            Some("inf") => f64::INFINITY,
            Some("-inf") => f64::NEG_INFINITY,
            _ => descriptor.nodata.as_f64().unwrap(),
        };
        if !crate::raster::Gdal::load()?
            .band_nodata(path, descriptor.band)?
            .is_some_and(|actual| actual == expected || actual.is_nan() && expected.is_nan())
        {
            return Err("STAC nodata differs from the embedded raster declaration".into());
        }
    }
    Ok(())
}

pub(crate) fn calibrate(
    raster: std::sync::Arc<crate::raster::Raster>,
    band: &Band,
) -> std::sync::Arc<crate::raster::Raster> {
    if band.scale == 1.0 && band.offset == 0.0 {
        return raster;
    }
    let mut scaled = (*raster).clone();
    scaled.data_type = 7;
    for (value, mask) in scaled.values.iter_mut().zip(&mut scaled.mask) {
        *value = *value * band.scale + band.offset;
        *mask |= !value.is_finite();
    }
    std::sync::Arc::new(scaled)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn item(id: &str, t: &str) -> Value {
        json!({"type":"Feature","stac_version":"1.0.0","id":id,"collection":"c","properties":{"datetime":t,"proj:epsg":32632},"bbox":[1,2,3,4],"geometry":{"type":"Polygon","coordinates":[[[1,2],[3,2],[3,4],[1,4],[1,2]]]},"assets":{"red":{"href":"https://example.org/a.tif?secret=one","file:checksum":format!("1220{}","a".repeat(64)),"raster:bands":[{"scale":0.1,"offset":-1,"nodata":0}],"eo:bands":[{"name":"B04","common_name":"red"}]}},"links":[]})
    }
    fn options() -> Options {
        Options {
            assets: vec!["red".into()],
            ..Default::default()
        }
    }
    #[test]
    fn identity_order_revision_and_private_locations() {
        let a = item("a", "2021-01-01T01:00:00+01:00");
        let b = item("b", "2021-01-01T00:00:00.1Z");
        let s = Selection::from_items(&[b.clone(), a.clone(), a.clone()], options()).unwrap();
        assert_eq!(s.assets.len(), 2);
        assert_eq!(s.assets[0].item, "a");
        let mut signed = a.clone();
        signed["assets"]["red"]["href"] = json!("https://other.example/path?secret=two");
        assert_eq!(
            s.id,
            Selection::from_items(&[signed, b.clone()], options())
                .unwrap()
                .id
        );
        let public = s.describe().to_string();
        assert!(!public.contains("secret") && !public.contains("https://"));
        assert_eq!(s.assets[0].bands[0].scale, 0.1);
        assert_eq!(s.assets[0].crs, 32632);
        let mut changed = a.clone();
        changed["assets"]["red"]["file:checksum"] = json!(format!("1220{}", "b".repeat(64)));
        assert_ne!(
            s.id,
            Selection::from_items(&[changed.clone(), b], options())
                .unwrap()
                .id
        );
        assert!(Selection::from_items(&[a, changed], options()).is_err());
    }
    #[test]
    fn rejects_missing_revisions_and_bad_descriptors() {
        for (key, value) in [
            ("file:checksum", json!("bad")),
            ("raster:bands", json!([])),
            ("raster:bands", json!([{"scale":"secret"}])),
            ("raster:bands", json!([{"nodata":"secret"}])),
        ] {
            let mut a = item("a", "2021-01-01T00:00:00Z");
            a["assets"]["red"][key] = value;
            assert!(Selection::from_items(&[a], options()).is_err(), "{key}");
        }
        let empty = Selection::from_items(&[], options()).unwrap();
        assert!(empty.assets.is_empty());
        let mut opts = options();
        opts.max_items = 0;
        assert!(Selection::from_items(&[], opts).is_err());
        let mut a = item("a", "not-a-time");
        assert!(Selection::from_items(&[a.clone()], options()).is_err());
        a["properties"]["datetime"] = json!("2021-01-01T00:00:00Z");
        a["bbox"] = json!([0, 0, 0]);
        assert!(Selection::from_items(&[a], options()).is_err());
    }
    #[test]
    fn checksum_staging_rejects_mutation_without_publishing_or_spending_budget() {
        let src = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(src.path(), b"first").unwrap();
        let mut a = item("a", "2021-01-01T00:00:00Z");
        a["assets"]["red"]["href"] = json!(src.path());
        a["assets"]["red"]["file:checksum"] = json!(format!("1220{}", digest(b"first")));
        let selection = Selection::from_items(&[a.clone()], options()).unwrap();
        let path = selection.materialize(&selection.assets[0], None).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"first");
        std::fs::write(src.path(), b"second").unwrap();
        assert_eq!(
            std::fs::read(selection.materialize(&selection.assets[0], None).unwrap()).unwrap(),
            b"first"
        );
        let mismatch = Selection::from_items(&[a.clone()], options()).unwrap();
        assert!(mismatch.materialize(&mismatch.assets[0], None).is_err());
        assert_eq!(mismatch.materialized.lock().unwrap().bytes, 0);
        assert_eq!(
            std::fs::read_dir(mismatch.directory.path())
                .unwrap()
                .count(),
            0
        );
        let mut opts = options();
        opts.max_staged_bytes = 2;
        let limited = Selection::from_items(&[a], opts).unwrap();
        assert!(limited.materialize(&limited.assets[0], None).is_err());
        drop(selection);
        assert!(!path.exists());
    }
    #[test]
    fn canonical_object_identity_and_transport_policy() {
        assert_eq!(
            identity(&json!({"a":1,"b":2})),
            identity(&json!({"b":2,"a":1}))
        );
        for url in [
            "http://example.org/search",
            "https://user:secret@example.org/search",
            "https://example.org/search#secret",
        ] {
            assert!(http_url(url).is_err());
        }
        assert!(http_url("http://127.0.0.1:8000/search").is_ok());
    }
}

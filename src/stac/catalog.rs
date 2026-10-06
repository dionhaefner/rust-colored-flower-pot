//! Bounded static Catalog/Collection discovery before raster execution.
use super::*;
use std::collections::VecDeque;

pub(crate) fn document_url(location: &str) -> Result<Url> {
    if location.starts_with("https://") || location.starts_with("http://") {
        http_url(location)
    } else {
        let path = if location.starts_with("file:") {
            let url = Url::parse(location).map_err(|_| "invalid STAC document URL")?;
            if url.query().is_some() || url.fragment().is_some() {
                return Err("STAC document file URLs cannot contain queries or fragments".into());
            }
            url.to_file_path()
                .map_err(|_| "invalid STAC document file path")?
        } else {
            PathBuf::from(location)
        };
        let path = std::fs::canonicalize(path).map_err(|_| "STAC document file unavailable")?;
        Url::from_file_path(path).map_err(|_| "invalid STAC document file path".into())
    }
}

pub(crate) fn read_document(url: &Url, headers: &BTreeMap<String, String>) -> Result<(Value, u64)> {
    let reader: Box<dyn Read> = if url.scheme() == "file" {
        let path = url
            .to_file_path()
            .map_err(|_| "invalid STAC document file path")?;
        Box::new(std::fs::File::open(path).map_err(|_| "STAC document file unavailable")?)
    } else {
        Box::new(remote("GET", url, headers, &Value::Null)?.into_reader())
    };
    let mut bytes = Vec::new();
    reader
        .take(PAGE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "STAC document read failure")?;
    if bytes.len() as u64 > PAGE_BYTES {
        return Err("STAC document exceeds byte limit".into());
    }
    let document = serde_json::from_slice(&bytes).map_err(|_| "invalid STAC document JSON")?;
    Ok((document, bytes.len() as u64))
}

impl Selection {
    pub(crate) fn from_source(
        location: &str,
        headers: &BTreeMap<String, String>,
        options: Options,
        max_documents: usize,
    ) -> Result<Self> {
        let url = document_url(location)?;
        let (document, bytes) = read_document(&url, headers)?;
        Self::from_loaded(url, document, bytes, headers, options, max_documents)
    }

    pub(crate) fn from_loaded(
        url: Url,
        document: Value,
        bytes: u64,
        headers: &BTreeMap<String, String>,
        mut options: Options,
        max_documents: usize,
    ) -> Result<Self> {
        options.validate()?;
        if max_documents == 0 || max_documents > 20000 {
            return Err("STAC document limit must be between 1 and 20000".into());
        }
        match document["type"].as_str() {
            Some("Catalog" | "Collection") => Self::catalog_document(
                url,
                Some((document, bytes)),
                headers,
                options,
                max_documents,
            ),
            Some("Feature" | "FeatureCollection") => {
                options.base_url = Some(url.to_string());
                let mut selection = if document["type"] == "Feature" {
                    Self::from_items(&[document], options)?
                } else {
                    Self::from_items(
                        document["features"]
                            .as_array()
                            .ok_or("STAC ItemCollection requires features")?,
                        options,
                    )?
                };
                selection.metrics.pages = 1;
                selection.metrics.response_bytes = bytes;
                Ok(selection)
            }
            _ => Err(
                "source document must be a STAC Catalog, Collection, Item or ItemCollection".into(),
            ),
        }
    }

    pub fn from_catalog(
        location: &str,
        headers: &BTreeMap<String, String>,
        options: Options,
        max_documents: usize,
    ) -> Result<Self> {
        let root = document_url(location)?;
        Self::catalog_document(root, None, headers, options, max_documents)
    }

    fn catalog_document(
        root: Url,
        mut first: Option<(Value, u64)>,
        headers: &BTreeMap<String, String>,
        mut options: Options,
        max_documents: usize,
    ) -> Result<Self> {
        let start = Instant::now();
        options.validate()?;
        if max_documents == 0 || max_documents > 20000 {
            return Err("STAC document limit must be between 1 and 20000".into());
        }
        let mut queued = BTreeMap::from([(root.to_string(), "child")]);
        let mut pending = VecDeque::from([(root.clone(), "child")]);
        let mut items = Vec::new();
        let mut metrics = SelectionMetrics::default();
        while let Some((url, relation)) = pending.pop_front() {
            let (mut document, bytes) = match first.take() {
                Some(value) => value,
                None => read_document(&url, headers)?,
            };
            metrics.pages += 1;
            metrics.response_bytes += bytes;
            if metrics.response_bytes > SEARCH_BYTES {
                return Err("STAC catalog response byte limit exceeded".into());
            }
            match (relation, document["type"].as_str()) {
                ("item", Some("Feature")) => {
                    if items.len() == options.max_items {
                        return Err("STAC result limit exceeded".into());
                    }
                    // Canonical self links can describe the published copy of a local catalog.
                    // Relative assets belong to the document actually consumed here.
                    if let Some(assets) = document["assets"].as_object_mut() {
                        for asset in assets.values_mut() {
                            if let Some(href) = asset["href"].as_str() {
                                asset["href"] = json!(
                                    url.join(href)
                                        .map_err(|_| "invalid STAC asset link")?
                                        .as_str()
                                );
                            }
                        }
                    }
                    items.push(document);
                }
                ("child", Some("Catalog" | "Collection")) => {
                    if !matches!(document["stac_version"].as_str(), Some("1.0.0" | "1.1.0")) {
                        return Err("unsupported STAC Catalog version".into());
                    }
                    text(&document, "id")?;
                    let links = document["links"]
                        .as_array()
                        .ok_or("STAC Catalog requires links")?;
                    for link in links {
                        let relation = match link["rel"].as_str() {
                            Some("child") => "child",
                            Some("item") => "item",
                            _ => continue,
                        };
                        if link.get("headers").is_some()
                            || link["method"].as_str().is_some_and(|m| m != "GET")
                        {
                            return Err(
                                "STAC Catalog links require GET without link-supplied headers"
                                    .into(),
                            );
                        }
                        let href = link["href"]
                            .as_str()
                            .filter(|s| !s.is_empty() && s.len() <= 16384)
                            .ok_or("STAC Catalog link requires href")?;
                        let url = url.join(href).map_err(|_| "invalid STAC Catalog link")?;
                        let url = if root.scheme() == "file" {
                            if url.scheme() != "file"
                                || url.query().is_some()
                                || url.fragment().is_some()
                            {
                                return Err(
                                    "local STAC Catalog links must reference local documents"
                                        .into(),
                                );
                            }
                            document_url(url.as_str())?
                        } else {
                            let url = http_url(url.as_str())?;
                            if url.origin() != root.origin() {
                                return Err("STAC Catalog document link crosses origin".into());
                            }
                            url
                        };
                        if let Some(previous) = queued.get(url.as_str()) {
                            if *previous != relation {
                                return Err("conflicting STAC Catalog link relations".into());
                            }
                        } else {
                            queued.insert(url.to_string(), relation);
                            if queued.len() > max_documents {
                                return Err("STAC catalog document limit exceeded".into());
                            }
                            pending.push_back((url, relation));
                        }
                    }
                }
                _ => {
                    return Err(
                        "STAC Catalog child/item link has an unsupported document type".into(),
                    );
                }
            }
        }
        options.base_url = None;
        let mut selection = Self::from_items(&items, options)?;
        metrics.returned_items = items.len();
        metrics.selection_ms = start.elapsed().as_secs_f64() * 1000.;
        selection.metrics = metrics;
        Ok(selection)
    }
}

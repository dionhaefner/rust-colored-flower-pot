//! Native tileset discovery, TileJSON and source-cell inspection.
use super::*;
use crate::{
    layers::{GridRead, Resampling},
    tile_matrix::TileMatrixSet,
};

pub(super) fn route(path: &str) -> bool {
    ["/tilesets", "/tileMatrixSets"].contains(&path)
        || ["/tileMatrixSets/", "/tilejson/", "/tiles/", "/point/"]
            .iter()
            .any(|prefix| path.starts_with(prefix))
}
fn tileset_id(keys: &[String]) -> String {
    let json = serde_json::to_vec(keys).expect("dataset keys");
    format!(
        "k-{}",
        json.iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    )
}
fn tileset_keys(id: &str) -> Result<Vec<String>> {
    let hex = id
        .strip_prefix("k-")
        .ok_or("invalid tileset ID; use /tilesets discovery")?;
    if hex.is_empty() || hex.len() % 2 != 0 {
        return Err("invalid tileset ID".into());
    }
    let bytes = hex
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            std::str::from_utf8(pair)
                .map_err(|_| "invalid tileset ID")
                .and_then(|pair| u8::from_str_radix(pair, 16).map_err(|_| "invalid tileset ID"))
        })
        .collect::<std::result::Result<Vec<_>, _>>()?;
    serde_json::from_slice(&bytes).map_err(|_| "invalid tileset ID".into())
}
pub(super) fn public_url(headers: &BTreeMap<String, String>) -> Result<String> {
    let configured = crate::settings::var("TC_PUBLIC_URL").unwrap_or_default();
    let candidate = if configured.is_empty() {
        format!(
            "http://{}",
            headers
                .get("host")
                .ok_or("TileJSON requires a Host header or PUBLIC_URL setting")?
        )
    } else {
        configured
    };
    let url =
        url::Url::parse(&candidate).map_err(|_| "PUBLIC_URL must be an absolute HTTP(S) URL")?;
    if !["http", "https"].contains(&url.scheme())
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(
            "PUBLIC_URL must be an absolute HTTP(S) URL without credentials, query or fragment"
                .into(),
        );
    }
    if crate::settings::var("TC_PUBLIC_URL")
        .unwrap_or_default()
        .is_empty()
        && url.path() != "/"
    {
        return Err("invalid Host header".into());
    }
    Ok(url.as_str().trim_end_matches('/').to_owned())
}
pub(super) fn discovery_base(headers: &BTreeMap<String, String>) -> Result<String> {
    if crate::settings::var("TC_PUBLIC_URL")
        .unwrap_or_default()
        .is_empty()
    {
        Ok(String::new())
    } else {
        public_url(headers)
    }
}
impl Application {
    pub(super) fn native_response(
        &self,
        method: &Method,
        path: &str,
        headers: &BTreeMap<String, String>,
        parameters: &BTreeMap<String, String>,
    ) -> AppResponse {
        if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
            return html_error(404);
        }
        if method == &Method::Options || ![Method::Get, Method::Head].contains(method) {
            let mut response = if method == &Method::Options {
                bytes_response("text/html; charset=utf-8", Vec::new())
            } else {
                html_error(405)
            };
            response
                .headers
                .insert("Allow".into(), "GET, HEAD, OPTIONS".into());
            return response;
        }
        match self.native_get(path, headers, parameters) {
            Ok(response) => response,
            Err(error) if error == "No dataset found" => json_error(404, &error),
            Err(error) if error == OVERLOADED => json_error(503, &error),
            Err(error) if error == TIMED_OUT => json_error(504, &error),
            Err(error) => json_error(400, &error),
        }
    }
    fn native_get(
        &self,
        path: &str,
        headers: &BTreeMap<String, String>,
        parameters: &BTreeMap<String, String>,
    ) -> Result<AppResponse> {
        if path == "/tileMatrixSets" || path.starts_with("/tileMatrixSets/") {
            if !parameters.is_empty() {
                return Err("tile matrix definitions do not accept query parameters".into());
            }
            return if path == "/tileMatrixSets" {
                let base = discovery_base(headers)?;
                json_response(
                    200,
                    &json!({"tileMatrixSets":TileMatrixSet::ALL.map(|matrix| json!({"id":matrix.id(),"href":format!("{base}/tileMatrixSets/{}",matrix.id())}))}),
                )
            } else {
                json_response(
                    200,
                    &TileMatrixSet::parse(path.trim_start_matches("/tileMatrixSets/"))?
                        .definition(),
                )
            };
        }
        if path == "/tilesets" {
            if parameters
                .keys()
                .any(|key| !["page", "limit"].contains(&key.as_str()))
            {
                return Err("tileset discovery accepts page and limit".into());
            }
            let page = parameters
                .get("page")
                .map(|v| v.parse::<usize>().map_err(|_| "invalid page"))
                .transpose()?
                .unwrap_or(0);
            let limit = parameters
                .get("limit")
                .map(|v| v.parse::<usize>().map_err(|_| "invalid limit"))
                .transpose()?
                .unwrap_or(100);
            if limit == 0
                || limit > 1000
                || page
                    .checked_mul(limit)
                    .is_none_or(|offset| offset > i64::MAX as usize)
            {
                return Err("invalid discovery pagination".into());
            }
            let mut catalog = self.catalog()?;
            catalog.refresh()?;
            let datasets =
                catalog.query_datasets(&BTreeMap::new(), &[], true, Some((page, limit)))?;
            let base = discovery_base(headers)?;
            let tilesets = datasets.iter().map(|dataset| {
                let id = tileset_id(&dataset.keys);
                json!({"id":id,"keys":dataset.keys,"title":dataset.keys.join(" / "),"tilejson":format!("{base}/tilejson/{id}.json"),"point":format!("{base}/point/{id}"),"tileMatrixSets":TileMatrixSet::ALL.map(|matrix|json!({"id":matrix.id(),"definition":format!("{base}/tileMatrixSets/{}",matrix.id()),"tiles":format!("{base}/tiles/{id}/{}/{{z}}/{{x}}/{{y}}.png",matrix.id())}))})
            }).collect::<Vec<_>>();
            return json_response(200, &json!({"tilesets":tilesets,"page":page,"limit":limit}));
        }
        if path.starts_with("/tiles/") {
            if parameters.keys().any(|key| {
                ![
                    "stretch_range",
                    "colormap",
                    "explicit_color_map",
                    "color_transform",
                    "revision",
                ]
                .contains(&key.as_str())
            }) {
                return Err("unrecognized native tile rendering parameter".into());
            }
            let parts = path
                .trim_start_matches("/tiles/")
                .split('/')
                .collect::<Vec<_>>();
            let count = 1;
            if parts.len() != count + 4 || !parts[count + 3].ends_with(".png") {
                return Err("invalid native tile route".into());
            }
            let matrix = TileMatrixSet::parse(parts[count])?;
            let number = |value: &str| {
                value
                    .parse::<u32>()
                    .map_err(|_| "tile coordinates must be nonnegative integers")
            };
            let zoom = number(parts[count + 1])?;
            let column = number(parts[count + 2])?;
            let row = number(parts[count + 3].trim_end_matches(".png"))?;
            let bounds = matrix.bounds(zoom, column, row)?;
            let grid = GridRead {
                epsg: matrix.epsg(),
                resampling: resampling()?,
                revision: [0; 32],
                matrix: Some(matrix),
                point: None,
            };
            let legacy_path = format!(
                "/singleband/{}/{zoom}/{column}/{row}.png",
                tileset_keys(parts[0])?
                    .iter()
                    .map(|key| url::form_urlencoded::byte_serialize(key.as_bytes())
                        .collect::<String>()
                        .replace('+', "%20"))
                    .collect::<Vec<_>>()
                    .join("/")
            );
            let mut parameters = parameters.clone();
            parameters.remove("revision");

            self.validate_render_options(&legacy_path, &parameters)?;
            return self.singleband_on(&legacy_path, &parameters, Some((grid, bounds)));
        }
        let point = path.starts_with("/point/");
        let key_string = if point {
            path.trim_start_matches("/point/")
        } else {
            path.trim_start_matches("/tilejson/")
                .strip_suffix(".json")
                .ok_or("TileJSON route must end in .json")?
        };
        let keys = tileset_keys(key_string)?;
        let mut catalog = self.catalog()?;
        catalog.refresh()?;
        if keys.len() != catalog.keys().len() {
            return Err("must specify every catalog key".into());
        }
        let (_, metadata, source) = catalog.descriptor(&keys)?.ok_or("No dataset found")?;
        drop(catalog);
        if point {
            if parameters
                .keys()
                .any(|key| !["lon", "lat"].contains(&key.as_str()))
            {
                return Err("point inspection accepts lon and lat only".into());
            }
            let coordinate = ["lon", "lat"].map(|key| {
                parameters
                    .get(key)
                    .ok_or("point inspection requires lon and lat")
                    .and_then(|value| {
                        value
                            .parse::<f64>()
                            .map_err(|_| "point coordinates must be finite numbers")
                    })
            });
            let [longitude, latitude] = [coordinate[0]?, coordinate[1]?];
            if !longitude.is_finite()
                || !latitude.is_finite()
                || !(-180. ..=180.).contains(&longitude)
                || !(-90. ..=90.).contains(&latitude)
            {
                return Err(
                    "point coordinates must be longitude [-180,180] and latitude [-90,90]".into(),
                );
            }
            let mut value = source.point(&self.executor, [longitude, latitude])?;
            value["coordinate"] = json!([longitude, latitude]);
            value["crs"] = json!("OGC:CRS84");
            value["interpolation"] = json!("nearest");
            value["keys"] = json!(keys);
            return json_response(200, &value);
        }
        if parameters.keys().any(|key| {
            ![
                "stretch_range",
                "colormap",
                "explicit_color_map",
                "color_transform",
            ]
            .contains(&key.as_str())
        }) {
            return Err("unrecognized TileJSON rendering parameter".into());
        }
        let rendering_path = format!("/singleband/{}/0/0/0.png", tileset_id(&keys));
        self.validate_render_options(&rendering_path, parameters)?;
        if let Some(range) = parameters.get("stretch_range") {
            parse_stretch(range, &metadata)?;
        }
        let query = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(parameters)
            .finish();
        let base = public_url(headers)?;
        let template = format!(
            "{base}/tiles/{}/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png{}",
            tileset_id(&keys),
            if query.is_empty() {
                String::new()
            } else {
                format!("?{query}")
            }
        );
        let mut bounds = metadata.bounds;
        if bounds[0] > bounds[2] {
            bounds[0] = -180.;
            bounds[2] = 180.;
        }
        bounds[0] = bounds[0].clamp(-180., 180.);
        bounds[2] = bounds[2].clamp(-180., 180.);
        bounds[1] = bounds[1].clamp(-85.0511287798066, 85.0511287798066);
        bounds[3] = bounds[3].clamp(-85.0511287798066, 85.0511287798066);
        json_response(
            200,
            &json!({"tilejson":"3.0.0","name":keys.join(" / "),"tiles":[template],"scheme":"xyz","minzoom":0,"maxzoom":22,"bounds":bounds,"attribution":metadata.metadata["attribution"].as_str().unwrap_or(""),"tileSize":256,"rcfp":{"keys":keys,"tileMatrixSet":"WebMercatorQuad","revision":source.identity(),"defaults":metadata.metadata["defaults"],"parameters":parameters,"point":format!("{base}/point/{}",tileset_id(&keys))}}),
        )
    }
}
fn resampling() -> Result<Resampling> {
    match crate::settings::var("TC_REPROJECTION_METHOD")
        .unwrap_or_else(|_| "linear".into())
        .as_str()
    {
        "nearest" => Ok(Resampling::Nearest),
        "linear" | "bilinear" => Ok(Resampling::Bilinear),
        "cubic" => Ok(Resampling::Cubic),
        "average" => Ok(Resampling::Average),
        _ => Err("native grid resampling supports nearest, bilinear, cubic or average".into()),
    }
}

pub(super) fn update_spec(spec: &mut Value) {
    let keys = json!({"name":"id","in":"path","required":true,"description":"Opaque tileset ID returned by /tilesets discovery.","schema":{"type":"string"}});
    let render_parameters = spec["paths"]
        .as_object()
        .expect("paths")
        .iter()
        .find(|(path, _)| path.starts_with("/singleband/"))
        .map(|(_, entry)| {
            entry["get"]["parameters"]
                .as_array()
                .expect("singleband parameters")
                .iter()
                .filter(|parameter| {
                    parameter["in"] == "query"
                        && [
                            "stretch_range",
                            "colormap",
                            "explicit_color_map",
                            "color_transform",
                        ]
                        .iter()
                        .any(|name| parameter["name"] == *name)
                })
                .cloned()
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let response = |schema: &str| json!({"200":{"description":"Native response","content":{"application/json":{"schema":{"$ref":format!("#/components/schemas/{schema}")}}}},"400":{"description":"Invalid parameters"},"404":{"description":"Dataset absent or native route disabled in the Terracotta profile"},"503":{"description":"Capacity is full"},"504":{"description":"Raster deadline elapsed; active work continues"}});
    spec["paths"]["/tilesets"] = json!({"get":{"summary":"Discover native logical tilesets across catalog layouts","parameters":[{"name":"page","in":"query","schema":{"type":"integer","minimum":0,"default":0}},{"name":"limit","in":"query","schema":{"type":"integer","minimum":1,"maximum":1000,"default":100}}],"responses":response("Tilesets")}});
    spec["paths"]["/tileMatrixSets"] = json!({"get":{"summary":"List supported tile matrix set identities","responses":response("TileMatrixSets")}});
    spec["paths"]["/tileMatrixSets/{id}"] = json!({"get":{"summary":"Describe output CRS, axes, origin, extent and all tile matrices","parameters":[{"name":"id","in":"path","required":true,"schema":{"type":"string","enum":TileMatrixSet::ALL.map(|g|g.id())}}],"responses":response("TileMatrixSet")}});
    let mut tilejson_parameters = vec![keys.clone()];
    tilejson_parameters.extend(render_parameters.clone());
    spec["paths"]["/tilejson/{id}.json"] = json!({"get":{"summary":"TileJSON 3.0 raster discovery for WebMercatorQuad","description":"Absolute tile URLs use PUBLIC_URL, including an optional deployment prefix, or plain HTTP and Host. TileJSON uses XYZ, 256 pixel tiles and zooms 0 through 22. Rendering query parameters are copied to every tile URL; stored defaults apply when omitted.","parameters":tilejson_parameters,"responses":response("TileJSON")}});
    spec["paths"]["/point/{id}"] = json!({"get":{"summary":"Inspect a nearest source cell independently of display colors","description":"Longitude/latitude in OGC:CRS84 x/y order. No interpolation or overview is used. Cells have inclusive top/left and exclusive bottom/right pixel boundaries. Nodata, nonfinite values, masks, alpha and out-of-source points return valid=false and null values. Scale/offset applies after sampling. Expression operands retain their declared value modes; units are not inferred. No source locations or credentials are returned.","parameters":[keys.clone(),{"name":"lon","in":"query","required":true,"schema":{"type":"number","minimum":-180,"maximum":180}},{"name":"lat","in":"query","required":true,"schema":{"type":"number","minimum":-90,"maximum":90}}],"responses":response("PointInspection")}});
    let mut tile_parameters = vec![
        keys,
        json!({"name":"tileMatrixSet","in":"path","required":true,"schema":{"type":"string","enum":TileMatrixSet::ALL.map(|g|g.id())}}),
    ];
    for name in ["z", "x", "y"] {
        tile_parameters.push(json!({"name":name,"in":"path","required":true,"schema":{"type":"integer","minimum":0},"description":if name=="z" {"Matrix identifier, 0 through 22"} else {"Zero-based column (x) or row (y), bounded by the selected matrix"}}));
    }
    tile_parameters.extend(render_parameters);
    spec["paths"]["/tiles/{id}/{tileMatrixSet}/{z}/{x}/{y}.png"] = json!({"get":{"summary":"Render a native logical dataset on a declared tile grid","description":"Fixed 256 by 256 PNG. Output bounds and CRS come from the named matrix, with x/y axes and top-left origin. No server wrapping. Outside-source coverage is transparent. Style follows native singleband rendering. Grid identity participates in array and encoded-image caching.","parameters":tile_parameters,"responses":{"200":{"description":"PNG with selected-band coverage","content":{"image/png":{"schema":{"type":"string","format":"binary"}}}},"400":{"description":"Invalid grid, coordinates or rendering parameters"},"404":{"description":"Unknown dataset or route disabled in compatibility profile"},"503":{"description":"Capacity is full"},"504":{"description":"Raster deadline elapsed"}}}});
    spec["components"]["schemas"]["Tilesets"] = json!({"type":"object","required":["tilesets","page","limit"],"properties":{"page":{"type":"integer"},"limit":{"type":"integer"},"tilesets":{"type":"array","items":{"type":"object","required":["id","keys","title","tilejson","point","tileMatrixSets"],"properties":{"id":{"type":"string"},"keys":{"type":"array","items":{"type":"string"}},"title":{"type":"string"},"tilejson":{"type":"string"},"point":{"type":"string"},"tileMatrixSets":{"type":"array","items":{"type":"object","required":["id","definition","tiles"],"properties":{"id":{"type":"string"},"definition":{"type":"string"},"tiles":{"type":"string"}}}}}}}}});
    spec["components"]["schemas"]["TileMatrixSets"] = json!({"type":"object","required":["tileMatrixSets"],"properties":{"tileMatrixSets":{"type":"array","items":{"type":"object","required":["id","href"],"properties":{"id":{"type":"string"},"href":{"type":"string"}}}}}});
    spec["components"]["schemas"]["TileMatrixSet"] = json!({"type":"object","required":["id","crs","orderedAxes","boundingBox","tileMatrices"],"properties":{"id":{"type":"string"},"crs":{"type":"string"},"orderedAxes":{"type":"array","items":{"type":"string"},"minItems":2,"maxItems":2},"boundingBox":{"type":"object","required":["lowerLeft","upperRight","crs","orderedAxes"]},"tileMatrices":{"type":"array","minItems":23,"maxItems":23,"items":{"type":"object","required":["id","cellSize","scaleDenominator","pointOfOrigin","cornerOfOrigin","tileWidth","tileHeight","matrixWidth","matrixHeight"]}}}});
    spec["components"]["schemas"]["TileJSON"] = json!({"type":"object","required":["tilejson","tiles","name","bounds","scheme","minzoom","maxzoom","attribution","tileSize","rcfp"],"properties":{"tilejson":{"type":"string","enum":["3.0.0"]},"tiles":{"type":"array","minItems":1,"items":{"type":"string"}},"name":{"type":"string"},"bounds":{"type":"array","minItems":4,"maxItems":4,"items":{"type":"number"}},"scheme":{"type":"string","enum":["xyz"]},"minzoom":{"type":"integer","enum":[0]},"maxzoom":{"type":"integer","enum":[22]},"attribution":{"type":"string"},"tileSize":{"type":"integer","enum":[256]},"rcfp":{"type":"object","required":["keys","tileMatrixSet","revision","defaults","parameters","point"]}}});
    spec["components"]["schemas"]["PointInspection"] = json!({"type":"object","required":["value","valid","raw","scaled","values","units","provenance","coordinate","crs","interpolation","keys"],"properties":{"value":{"type":"number","nullable":true},"valid":{"type":"boolean"},"raw":{"type":"number","nullable":true},"scaled":{"type":"number","nullable":true},"values":{"type":"string","enum":["raw","scaled","expression"]},"units":{"type":"string","nullable":true},"scale":{"type":"number"},"offset":{"type":"number"},"provenance":{"type":"object","required":["revision"]},"coordinate":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2},"crs":{"type":"string","enum":["OGC:CRS84"]},"interpolation":{"type":"string","enum":["nearest"]},"keys":{"type":"array","items":{"type":"string"}}}});
}

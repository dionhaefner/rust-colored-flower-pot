//! Admission estimates for indexed virtual mosaics.
use super::*;

pub const EXCEEDED: &str = "mosaic budget exceeded";

pub fn generation(path: &Path) -> Option<String> {
    use sha2::Digest;
    if crate::execution::is_remote(path) {
        return None;
    }
    let mut hash = sha2::Sha256::new();
    for (size, stamp) in crate::execution::source_generation(path) {
        hash.update(size.to_le_bytes());
        hash.update(stamp.to_le_bytes());
    }
    Some(format!("{:x}", hash.finalize()))
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Budget {
    pub max_sources: usize,
    pub max_reads: usize,
    pub max_buffer_bytes: usize,
    pub max_source_bytes: u64,
}

#[derive(Debug, Serialize)]
pub struct Plan {
    pub observations: Vec<String>,
    pub source_count: usize,
    pub reads: usize,
    pub buffer_bytes: usize,
    pub source_bytes: u64,
}

pub struct Samples {
    pub rasters: Vec<Raster>,
    pub contributors: Vec<String>,
    /// Observation indexes per channel and pixel, with None for invalid output.
    pub provenance: Vec<Vec<Option<usize>>>,
}
impl Samples {
    pub fn json(&self, provenance: bool) -> Value {
        let samples =
            serde_json::json!(self.rasters.iter().map(|r| {
            serde_json::json!({"shape":[r.height,r.width],"values":r.values,"mask":r.mask})
        }).collect::<Vec<_>>());
        if provenance {
            serde_json::json!({"samples":samples,"contributors":self.contributors,"provenance":self.provenance})
        } else {
            samples
        }
    }
}

impl Budget {
    pub fn validate(&self) -> Result<()> {
        if !(1..=64).contains(&self.max_sources)
            || !(1..=1024).contains(&self.max_reads)
            || !(1..=512 * 1024 * 1024).contains(&self.max_buffer_bytes)
            || !(1..=64 * 1024 * 1024 * 1024).contains(&self.max_source_bytes)
        {
            return Err("mosaic limits require 1–64 sources, 1–1024 reads, 1–512 MiB buffers and 1–64 GiB uncompressed source bytes".into());
        }
        Ok(())
    }
    pub fn check(&self, layer: &Layer, spec: &RenderSpec) -> Result<Plan> {
        self.validate()?;
        if spec.grid.epsg()? != 3857 {
            return Err("indexed mosaics require an EPSG:3857 output grid".into());
        }
        if spec.bindings.len() > 16
            || spec.channels.len() > 3
            || spec.channels.iter().any(|c| c.expression.len() > 4096)
        {
            return Err("mosaic render specification exceeds expression limits".into());
        }
        let selected = layer.selected().collect::<Vec<_>>();
        let reads = selected.len() * spec.bindings.len();
        // Reserve for input copies, expression intermediates, output, ownership,
        // normalization and encoding. GDAL/worker caches have separate limits.
        let intermediates = spec
            .channels
            .iter()
            .map(|c| c.expression.len())
            .sum::<usize>();
        let buffer_bytes = spec.grid.size.iter().product::<usize>()
            * (64 + 32 * spec.bindings.len() + 32 * spec.channels.len() + 18 * intermediates);
        let mut source_bytes = 0u64;
        let mut sources = BTreeSet::new();
        for observation in &selected {
            for binding in spec.bindings.values() {
                sources.insert((&observation.id, &binding.asset));
                let asset = observation
                    .assets
                    .get(&binding.asset)
                    .ok_or_else(|| format!("unknown asset '{}'", binding.asset))?;
                let metadata = asset.metadata.as_ref().ok_or("unregistered mosaic asset")?;
                if metadata.generation != generation(&asset.location) {
                    return Err(
                        "mosaic source changed; re-register its footprint and metadata".into(),
                    );
                }
                let bytes_per_pixel = metadata.bands.iter().try_fold(1u64, |n, b| {
                    let size = match b.data_type {
                        1 | 14 => 1,
                        2 | 3 => 2,
                        4..=6 => 4,
                        7 | 12 | 13 => 8,
                        _ => return Err("unsupported mosaic source dtype"),
                    };
                    Ok(n + size)
                })?;
                let estimate = (metadata.size[0] as u64)
                    .checked_mul(metadata.size[1] as u64)
                    .and_then(|n| n.checked_mul(bytes_per_pixel))
                    .ok_or(EXCEEDED)?;
                source_bytes = source_bytes
                    .checked_add(estimate)
                    .ok_or("mosaic source size overflow")?;
            }
        }
        if selected.len() > self.max_sources
            || sources.len() > self.max_sources
            || reads > self.max_reads
            || buffer_bytes > self.max_buffer_bytes
            || source_bytes > self.max_source_bytes
        {
            return Err(EXCEEDED.into());
        }
        Ok(Plan {
            observations: selected.iter().map(|o| o.id.clone()).collect(),
            source_count: sources.len(),
            reads,
            buffer_bytes,
            source_bytes,
        })
    }
}

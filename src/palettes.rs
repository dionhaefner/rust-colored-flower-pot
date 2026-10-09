//! Built-in palettes and validated custom NumPy uint8 palette files.
use crate::{Result, image::Rgba};
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;

pub struct Palettes(pub BTreeMap<String, Vec<Rgba>>, BTreeMap<String, PathBuf>);
impl Palettes {
    pub fn load() -> Result<Self> {
        let mut maps = BTreeMap::new();
        let mut custom = BTreeMap::new();
        macro_rules! load_builtins {
            ($($name:literal),* $(,)?) => {
                $(maps.insert(
                    $name.into(),
                    read_npy(include_bytes!(concat!("assets/cmaps/", $name, "_rgba.npy")))?,
                );)*
            };
        }
        load_builtins!(
            "accent_r",
            "accent",
            "afmhot_r",
            "afmhot",
            "autumn_r",
            "autumn",
            "binary_r",
            "binary",
            "blues_r",
            "blues",
            "bone_r",
            "bone",
            "brbg_r",
            "brbg",
            "brg_r",
            "brg",
            "bugn_r",
            "bugn",
            "bupu_r",
            "bupu",
            "bwr_r",
            "bwr",
            "cividis_r",
            "cividis",
            "cloud",
            "cmrmap_r",
            "cmrmap",
            "cool_r",
            "cool",
            "coolwarm_r",
            "coolwarm",
            "copper_r",
            "copper",
            "cubehelix_r",
            "cubehelix",
            "dark2_r",
            "dark2",
            "flag_r",
            "flag",
            "gist_earth_r",
            "gist_earth",
            "gist_gray_r",
            "gist_gray",
            "gist_grey",
            "gist_heat_r",
            "gist_heat",
            "gist_ncar_r",
            "gist_ncar",
            "gist_rainbow_r",
            "gist_rainbow",
            "gist_stern_r",
            "gist_stern",
            "gist_yarg_r",
            "gist_yarg",
            "gist_yerg",
            "gnbu_r",
            "gnbu",
            "gnuplot2_r",
            "gnuplot2",
            "gnuplot_r",
            "gnuplot",
            "gray_r",
            "gray",
            "grays",
            "greens_r",
            "greens",
            "grey",
            "greys_r",
            "greys",
            "hot_r",
            "hot",
            "hsv_r",
            "hsv",
            "inferno_r",
            "inferno",
            "jet_r",
            "jet",
            "magma_r",
            "magma",
            "nipy_spectral_r",
            "nipy_spectral",
            "ocean_r",
            "ocean",
            "oranges_r",
            "oranges",
            "orrd_r",
            "orrd",
            "paired_r",
            "paired",
            "pastel1_r",
            "pastel1",
            "pastel2_r",
            "pastel2",
            "pink_r",
            "pink",
            "piyg_r",
            "piyg",
            "plasma_r",
            "plasma",
            "prgn_r",
            "prgn",
            "prism_r",
            "prism",
            "pubu_r",
            "pubu",
            "pubugn_r",
            "pubugn",
            "puor_r",
            "puor",
            "purd_r",
            "purd",
            "purples_r",
            "purples",
            "rainbow_r",
            "rainbow",
            "rdbu_r",
            "rdbu",
            "rdgy_r",
            "rdgy",
            "rdpu_r",
            "rdpu",
            "rdylbu_r",
            "rdylbu",
            "rdylgn_r",
            "rdylgn",
            "reds_r",
            "reds",
            "seismic_r",
            "seismic",
            "set1_r",
            "set1",
            "set2_r",
            "set2",
            "set3_r",
            "set3",
            "spectral_r",
            "spectral",
            "spring_r",
            "spring",
            "summer_r",
            "summer",
            "tab10_r",
            "tab10",
            "tab20_r",
            "tab20",
            "tab20b_r",
            "tab20b",
            "tab20c_r",
            "tab20c",
            "terrain_r",
            "terrain",
            "turbo_r",
            "turbo",
            "twilight_r",
            "twilight",
            "twilight_shifted_r",
            "twilight_shifted",
            "viridis_r",
            "viridis",
            "winter_r",
            "winter",
            "wistia_r",
            "wistia",
            "ylgn_r",
            "ylgn",
            "ylgnbu_r",
            "ylgnbu",
            "ylorbr_r",
            "ylorbr",
            "ylorrd_r",
            "ylorrd",
        );
        if let Some(folder) =
            crate::settings::var_os("TC_EXTRA_CMAP_FOLDER").filter(|v| !v.is_empty())
        {
            for entry in std::fs::read_dir(Path::new(&folder))
                .map_err(|e| format!("invalid TC_EXTRA_CMAP_FOLDER: {e}"))?
            {
                let entry = entry.map_err(|e| e.to_string())?;
                let name = entry.file_name().to_string_lossy().into_owned();
                if let Some(name) = name.strip_suffix("_rgba.npy") {
                    custom.insert(name.to_owned(), entry.path());
                    let bytes = std::fs::read(entry.path()).map_err(|e| e.to_string())?;
                    maps.insert(
                        name.into(),
                        read_npy(&bytes)
                            .map_err(|e| format!("invalid custom colormap {name}: {e}"))?,
                    );
                }
            }
        }
        Ok(Self(maps, custom))
    }
    pub fn get(&self, name: &str) -> Result<Cow<'_, [Rgba]>> {
        if let Some(path) = self.1.get(name) {
            return read_npy(&std::fs::read(path).map_err(|e| e.to_string())?).map(Cow::Owned);
        }
        self.0
            .get(name)
            .map(|v| Cow::Borrowed(v.as_slice()))
            .ok_or_else(|| format!("Encountered invalid color map {name}"))
    }
}

pub fn read_npy(bytes: &[u8]) -> Result<Vec<Rgba>> {
    let bad = || "palette must be a NumPy uint8 array of shape (255, 4)".to_string();
    if bytes.get(..6) != Some(b"\x93NUMPY") {
        return Err(bad());
    }
    let (start, length) = match bytes.get(6) {
        Some(1) if bytes.len() >= 10 => (10, u16::from_le_bytes([bytes[8], bytes[9]]) as usize),
        Some(2 | 3) if bytes.len() >= 12 => (
            12,
            u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize,
        ),
        _ => return Err(bad()),
    };
    let header = std::str::from_utf8(bytes.get(start..start + length).ok_or_else(bad)?)
        .map_err(|_| bad())?;
    let normalized = header.replace('"', "'").replace(' ', "");
    if !normalized.contains("'descr':'|u1'") || !normalized.contains("'shape':(255,4)") {
        return Err(bad());
    }
    let fortran = normalized.contains("'fortran_order':True");
    let data = bytes
        .get(start + length..start + length + 1020)
        .ok_or_else(bad)?;
    Ok((0..255)
        .map(|i| std::array::from_fn(|j| data[if fortran { j * 255 + i } else { i * 4 + j }]))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtin_registration_preserves_every_asset_name_and_rgba_byte() {
        let palettes = crate::settings::scope(
            serde_json::from_value(serde_json::json!({"EXTRA_CMAP_FOLDER": null})).unwrap(),
            Palettes::load,
        )
        .unwrap();
        let directory = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/assets/cmaps");
        let mut count = 0;
        for entry in std::fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            let filename = path.file_name().unwrap().to_str().unwrap();
            let Some(name) = filename.strip_suffix("_rgba.npy") else {
                continue;
            };
            let bytes = std::fs::read(&path).unwrap();
            assert!(
                std::str::from_utf8(&bytes[10..128])
                    .unwrap()
                    .contains("False")
            );
            let colors = palettes.get(name).unwrap();
            let actual = colors.iter().flatten().copied().collect::<Vec<_>>();
            assert_eq!(actual, &bytes[128..], "{name}");
            count += 1;
        }
        assert_eq!(count, 171);
        assert_eq!(palettes.0.len(), count);
    }
}

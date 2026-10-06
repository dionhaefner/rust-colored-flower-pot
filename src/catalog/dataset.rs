//! Persisted logical datasets independent of raster layout and acquisition provider.
use crate::{
    Result,
    layers::{Asset, Values},
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const FORMAT: &str = "rcfp-native-2";

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Style {
    pub stretch: Option<[f64; 2]>,
    pub colormap: Option<String>,
}
impl Style {
    pub fn validate(&self) -> Result<()> {
        if self
            .stretch
            .is_some_and(|r| !r.iter().all(|v| v.is_finite()) || r[0] > r[1])
        {
            return Err("default stretch requires finite ordered endpoints".into());
        }
        if let Some(name) = &self.colormap {
            crate::palettes::Palettes::load()?.get(name)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Definition {
    Band {
        asset: Box<Asset>,
        band: usize,
        values: Values,
        #[serde(default)]
        style: Style,
    },
    Expression {
        expression: String,
        bindings: BTreeMap<String, Vec<String>>,
        #[serde(default)]
        style: Style,
    },
    Catalog {
        source: String,
        keys: Vec<String>,
        #[serde(default)]
        style: Style,
    },
}
impl Definition {
    pub fn style(&self) -> &Style {
        match self {
            Self::Band { style, .. }
            | Self::Expression { style, .. }
            | Self::Catalog { style, .. } => style,
        }
    }
}

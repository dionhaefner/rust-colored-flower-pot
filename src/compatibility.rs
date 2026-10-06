//! Stable compatibility contracts selected at an adapter's ownership boundary.
use serde_json::{Map, Value};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum CompatibilityProfile {
    #[default]
    Native,
    Terracotta0101,
}

impl CompatibilityProfile {
    pub fn parse(value: &str) -> crate::Result<Self> {
        match value {
            "native" => Ok(Self::Native),
            "terracotta-0.10.1" => Ok(Self::Terracotta0101),
            _ => Err(format!(
                "unknown compatibility profile {value:?}; expected native or terracotta-0.10.1"
            )),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Native => "native",
            Self::Terracotta0101 => "terracotta-0.10.1",
        }
    }

    pub fn resolve(settings: &mut Map<String, Value>) -> crate::Result<Self> {
        let profile = match settings.get("COMPAT_PROFILE") {
            Some(value) => Self::parse(value.as_str().ok_or("COMPAT_PROFILE must be a string")?)?,
            None => Self::parse(
                &std::env::var("RCFP_COMPAT_PROFILE").unwrap_or_else(|_| "native".into()),
            )?,
        };
        settings.insert("COMPAT_PROFILE".into(), profile.name().into());
        Ok(profile)
    }

    pub fn current() -> Self {
        // Adapter construction validates and captures this setting before work starts.
        crate::settings::compatibility_profile()
    }

    pub fn is_terracotta(self) -> bool {
        self == Self::Terracotta0101
    }

    pub(crate) fn require_native(self) -> crate::Result<()> {
        if self.is_terracotta() {
            return Err("this operation requires the native profile".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unknown_contracts() {
        assert!(CompatibilityProfile::parse("latest").is_err());
        assert!(CompatibilityProfile::parse("terracotta-0.10.1").is_ok());
    }

    #[test]
    fn preserves_compatible_optimizations() {
        let settings = serde_json::json!({"COMPAT_PROFILE": "terracotta-0.10.1",
            "RUST_UTM_FAST": "1", "RUST_MERCATOR_FAST": "1", "PREFETCH_TILE_RING": "1"})
        .as_object()
        .unwrap()
        .clone();
        crate::settings::scope(settings, || {
            assert_eq!(crate::settings::var("TC_RUST_UTM_FAST").unwrap(), "0");
            assert_eq!(crate::settings::var("TC_RUST_MERCATOR_FAST").unwrap(), "1");
            assert_eq!(crate::settings::var("TC_PREFETCH_TILE_RING").unwrap(), "1");
        });
        assert_eq!(
            CompatibilityProfile::current(),
            CompatibilityProfile::Native
        );
    }
}

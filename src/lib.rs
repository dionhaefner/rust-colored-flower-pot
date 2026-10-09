//! Shared application core for the RCFP service, CLI, Lambda, and Python APIs.

mod catalog;
pub mod execution;
pub mod ffi;
pub mod http;
pub mod image;
pub mod lambda;
pub mod layers;
pub mod raster;
pub mod stac;
mod statistics;
pub mod storage;
pub mod tile_matrix;

/// RCFP release version, independent of protocol and database versions.
pub const IMPLEMENTATION_VERSION: &str = env!("CARGO_PKG_VERSION");
/// Legacy protocol/database compatibility identity.
pub const TERRACOTTA_COMPATIBILITY_VERSION: &str = "0.10.1";
/// Terracotta catalog format accepted and emitted by the legacy catalog APIs.
pub const DATABASE_COMPATIBILITY: &str = "0.10";

pub type Result<T> = std::result::Result<T, String>;
pub mod color;
pub mod expressions;
pub mod palettes;

pub mod cli;
pub mod diagnostics;
pub mod ingest;

pub mod python;
pub mod settings;

pub mod client;

pub mod compatibility;
mod telemetry;
mod tracing;

//! Shared application core for the RCFP service, CLI, Lambda, and Python APIs.
//! Rendering, storage, metadata, and raster operations use the same native core.

pub mod execution;
pub mod ffi;
pub mod http;
pub mod image;
pub mod lambda;
pub mod raster;
mod statistics;
pub mod storage;

/// RCFP release version, independent of protocol and database versions.
pub const IMPLEMENTATION_VERSION: &str = env!("CARGO_PKG_VERSION");
/// Legacy protocol/database compatibility identity.
pub const TERRACOTTA_COMPATIBILITY_VERSION: &str = "0.10.1";
/// Database format accepted and emitted by RCFP.
pub const DATABASE_COMPATIBILITY: &str = "0.10";

pub type Result<T> = std::result::Result<T, String>;
pub mod color;
pub mod expressions;
pub mod palettes;

pub mod cli;
pub mod ingest;

pub mod python;
pub mod settings;

pub mod client;

mod tracing;

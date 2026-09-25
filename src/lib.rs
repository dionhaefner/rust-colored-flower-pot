//! Shared application core for the Terracotta rewrite.
//!
//! P05 provides rendering and HTTP contracts over legacy SQLite and bounded
//! local/remote GDAL execution. HTTP, Lambda and Python/WSGI share this core.
//! Native ingestion, metadata, SQLite writes and optimization share this core.
//! Complete legacy Python APIs and further deployment qualification remain later phases.

pub mod execution;
pub mod ffi;
pub mod http;
pub mod image;
pub mod lambda;
pub mod raster;
mod statistics;
pub mod storage;

/// Rewrite release identity; independent of Python protocol/database versions.
pub const IMPLEMENTATION_VERSION: &str = env!("CARGO_PKG_VERSION");
/// Legacy protocol/database compatibility identity.
pub const REFERENCE_VERSION: &str = "0.10.1";
/// Legacy major/minor database format accepted and emitted by this slice.
pub const DATABASE_COMPATIBILITY: &str = "0.10";
pub const REFERENCE_COMMIT: &str = "6db38d5873cde5ebabe47338a1f61ddc788eada6";

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

//! Explicit north-up tile grids. Coordinates use x/y order in the output CRS.
use crate::Result;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum TileMatrixSet {
    WebMercatorQuad,
    WorldCRS84Quad,
    RCFPArcticQuad,
    RCFPAntarcticQuad,
}
impl TileMatrixSet {
    pub const ALL: [Self; 4] = [
        Self::WebMercatorQuad,
        Self::WorldCRS84Quad,
        Self::RCFPArcticQuad,
        Self::RCFPAntarcticQuad,
    ];
    pub fn parse(id: &str) -> Result<Self> {
        Self::ALL
            .into_iter()
            .find(|grid| grid.id() == id)
            .ok_or_else(|| "unknown tile matrix set".into())
    }
    pub fn id(self) -> &'static str {
        match self {
            Self::WebMercatorQuad => "WebMercatorQuad",
            Self::WorldCRS84Quad => "WorldCRS84Quad",
            Self::RCFPArcticQuad => "RCFPArcticQuad",
            Self::RCFPAntarcticQuad => "RCFPAntarcticQuad",
        }
    }
    pub fn epsg(self) -> u32 {
        match self {
            Self::WebMercatorQuad => 3857,
            Self::WorldCRS84Quad => 4326,
            Self::RCFPArcticQuad => 3413,
            Self::RCFPAntarcticQuad => 3031,
        }
    }
    pub fn extent(self) -> [f64; 4] {
        match self {
            Self::WebMercatorQuad => {
                let edge = std::f64::consts::PI * 6378137.;
                [-edge, -edge, edge, edge]
            }
            Self::WorldCRS84Quad => [-180., -90., 180., 90.],
            Self::RCFPArcticQuad | Self::RCFPAntarcticQuad => {
                [-8388608., -8388608., 8388608., 8388608.]
            }
        }
    }
    pub fn dimensions(self, zoom: u32) -> Result<[u32; 2]> {
        if zoom > 22 {
            return Err("tile matrix must be between 0 and 22".into());
        }
        let height = 1 << zoom;
        Ok([
            height * if self == Self::WorldCRS84Quad { 2 } else { 1 },
            height,
        ])
    }
    pub fn bounds(self, zoom: u32, column: u32, row: u32) -> Result<[f64; 4]> {
        let [width, height] = self.dimensions(zoom)?;
        if column >= width || row >= height {
            return Err(
                "tile column or row is outside the matrix; wrapping is client-owned".into(),
            );
        }
        let [west, south, east, north] = self.extent();
        let dx = (east - west) / width as f64;
        let dy = (north - south) / height as f64;
        Ok([
            west + column as f64 * dx,
            north - (row + 1) as f64 * dy,
            west + (column + 1) as f64 * dx,
            north - row as f64 * dy,
        ])
    }
    pub fn definition(self) -> Value {
        let extent = self.extent();
        let geographic = self == Self::WorldCRS84Quad;
        let crs = if geographic {
            "http://www.opengis.net/def/crs/OGC/1.3/CRS84".into()
        } else {
            format!("http://www.opengis.net/def/crs/EPSG/0/{}", self.epsg())
        };
        let matrices = (0..=22).map(|zoom| {
            let [width,height] = self.dimensions(zoom).expect("bounded matrix");
            let cell = (extent[2]-extent[0]) / width as f64 / 256.;
            json!({"id":zoom.to_string(), "cellSize":cell, "scaleDenominator":cell * if geographic { std::f64::consts::PI * 6378137. / 180. } else { 1. } / 0.00028, "pointOfOrigin":[extent[0],extent[3]], "cornerOfOrigin":"topLeft", "tileWidth":256,"tileHeight":256,"matrixWidth":width,"matrixHeight":height})
        }).collect::<Vec<_>>();
        json!({"id":self.id(), "crs":crs, "orderedAxes":if geographic { ["Lon","Lat"] } else { ["E","N"] }, "boundingBox":{"lowerLeft":[extent[0],extent[1]],"upperRight":[extent[2],extent[3]],"crs":crs,"orderedAxes":if geographic { ["Lon","Lat"] } else { ["E","N"] }}, "tileMatrices":matrices})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn geographic_axes_edges_and_dateline_do_not_wrap() {
        let grid = TileMatrixSet::WorldCRS84Quad;
        assert_eq!(grid.dimensions(0).unwrap(), [2, 1]);
        assert_eq!(grid.bounds(0, 0, 0).unwrap(), [-180., -90., 0., 90.]);
        assert_eq!(grid.bounds(0, 1, 0).unwrap(), [0., -90., 180., 90.]);
        assert!(grid.bounds(0, 2, 0).is_err());
        assert!(grid.bounds(23, 0, 0).is_err());
    }
    #[test]
    fn neighboring_tiles_share_edges_on_every_grid() {
        for grid in TileMatrixSet::ALL {
            let west = grid.bounds(3, 2, 2).unwrap();
            let east = grid.bounds(3, 3, 2).unwrap();
            let south = grid.bounds(3, 2, 3).unwrap();
            assert_eq!(west[2], east[0]);
            assert_eq!(west[1], south[3]);
            let definition = grid.definition();
            assert_eq!(
                definition["tileMatrices"][3]["pointOfOrigin"],
                json!([grid.extent()[0], grid.extent()[3]])
            );
            let cell = definition["tileMatrices"][3]["cellSize"].as_f64().unwrap();
            assert!((cell - (west[2] - west[0]) / 256.).abs() <= cell * f64::EPSILON * 4.);
        }
    }
    #[test]
    fn mercator_matches_existing_xyz_and_polar_grids_have_distinct_identity() {
        let actual = TileMatrixSet::WebMercatorQuad
            .bounds(13, 4406, 2927)
            .unwrap();
        let expected = crate::raster::mercator_tile_bounds(4406., 2927., 13);
        for (a, b) in actual.into_iter().zip(expected) {
            assert!((a - b).abs() < 1e-8);
        }
        assert_eq!(
            TileMatrixSet::RCFPArcticQuad.extent(),
            TileMatrixSet::RCFPAntarcticQuad.extent()
        );
        assert_ne!(
            TileMatrixSet::RCFPArcticQuad,
            TileMatrixSet::RCFPAntarcticQuad
        );
        assert_ne!(
            TileMatrixSet::RCFPArcticQuad.epsg(),
            TileMatrixSet::RCFPAntarcticQuad.epsg()
        );
    }
}

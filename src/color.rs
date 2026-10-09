//! Port of color-operations 0.4.0 (Mapbox MIT). See THIRD_PARTY_NOTICES.
use crate::{Result, raster::Raster};

#[derive(Debug)]
pub struct Operation {
    name: String,
    bands: Vec<usize>,
    args: Vec<f64>,
}

pub fn parse(input: &str, channels: usize) -> Result<Vec<Operation>> {
    let invalid = || "Invalid color transform".to_owned();
    let input = input.replace(',', "").to_lowercase();
    let tokens = input.split(' ').collect::<Vec<_>>();
    if tokens.len() < 2 {
        return Err("legacy internal error".into());
    }
    let mut result = Vec::new();
    let mut i = 0;
    while i < tokens.len() {
        let name = tokens[i];
        i += 1;
        let rgb = name == "saturation";
        let count = match name {
            "gamma" | "saturation" => 1,
            "sigmoidal" => 2,
            _ => return Err(invalid()),
        };
        let bands = if rgb {
            if channels != 3 {
                return Err(invalid());
            }
            vec![0, 1, 2]
        } else {
            let bandstr = tokens.get(i).ok_or_else(invalid)?;
            i += 1;
            let mut bands = Vec::new();
            for c in bandstr.chars() {
                let b = match c {
                    'r' | '1' => 0,
                    'g' | '2' => 1,
                    'b' | '3' => 2,
                    _ => return Err(invalid()),
                };
                if b >= channels {
                    return Err(invalid());
                }
                if !bands.contains(&b) {
                    bands.push(b);
                }
            }
            bands
        };
        let mut args = Vec::new();
        while i < tokens.len() && !["gamma", "sigmoidal", "saturation"].contains(&tokens[i]) {
            args.push(tokens[i].parse::<f64>().map_err(|_| invalid())?);
            i += 1;
        }
        if args.len() < count
            || (name == "gamma" && (args[0] <= 0.0 || args[0].is_nan()))
            || (name == "sigmoidal" && !(-f64::EPSILON..=1.0 + f64::EPSILON).contains(&args[1]))
        {
            return Err(invalid());
        }
        result.push(Operation {
            name: name.into(),
            bands,
            args,
        });
    }
    Ok(result)
}

pub fn normalize(raster: &Raster, range: [f64; 2]) -> Raster {
    let _timer = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
    let norm = range[1] - range[0];
    Raster {
        data_type: 7,
        width: raster.width,
        height: raster.height,
        mask: raster.mask.clone(),
        values: raster
            .values
            .iter()
            .zip(&raster.mask)
            .map(|(v, masked)| {
                if *masked {
                    return v.clamp(0.0, 1.0);
                }
                let mut v = v - range[0];
                if norm.abs() > 1e-8 {
                    v *= 1.0 / norm;
                }
                v.clamp(0.0, 1.0)
            })
            .collect(),
    }
}

pub fn apply(channels: &mut [Raster], operations: &[Operation]) {
    let _timer = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
    for op in operations {
        if op.name == "saturation" {
            for i in 0..channels[0].values.len() {
                let rgb = saturate(
                    [
                        channels[0].values[i],
                        channels[1].values[i],
                        channels[2].values[i],
                    ],
                    op.args[0],
                );
                for c in 0..3 {
                    channels[c].values[i] = rgb[c];
                }
            }
        } else {
            for &b in &op.bands {
                for (v, masked) in channels[b].values.iter_mut().zip(&channels[b].mask) {
                    if *masked {
                        if op.name == "sigmoidal" && op.args[0] != 0.0 {
                            *v = if op.args[0] > 0.0 {
                                1.0
                            } else {
                                op.args[0] * op.args[1]
                            };
                        }
                        continue;
                    }
                    *v = if op.name == "gamma" {
                        v.powf(1.0 / op.args[0])
                    } else {
                        sigmoid(*v, op.args[0], op.args[1])
                    };
                }
            }
        }
    }
}

fn sigmoid(v: f64, beta: f64, mut alpha: f64) -> f64 {
    if alpha == 0.0 {
        alpha = f64::EPSILON;
    }
    if beta == 0.0 {
        return v;
    }
    let base = 1.0 / (1.0 + (beta * alpha).exp());
    let high = 1.0 / (1.0 + (beta * (alpha - 1.0)).exp());
    if beta > 0.0 {
        (1.0 / (1.0 + (beta * (alpha - v)).exp()) - base) / (high - base)
    } else {
        (beta * alpha - (1.0 / (v * high - v * base + base) - 1.0).ln()) / beta
    }
}

fn saturate(rgb: [f64; 3], proportion: f64) -> [f64; 3] {
    let [r, g, b] = rgb.map(|v| {
        if v <= 0.04045 {
            v / 12.92
        } else {
            ((v + 0.055) / 1.055).powf(2.4)
        }
    });
    let xyz = [
        (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047,
        r * 0.2126729 + g * 0.7151522 + b * 0.0721750,
        (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883,
    ];
    let delta: f64 = 6.0 / 29.0;
    let [fx, fy, fz] = xyz.map(|v| {
        if v > delta.powi(3) {
            v.powf(1.0 / 3.0)
        } else {
            v / (3.0 * delta * delta) + 4.0 / 29.0
        }
    });
    let l = 116.0 * fy - 16.0;
    let a = 500.0 * (fx - fy);
    let b = 200.0 * (fy - fz);
    let c = (a * a + b * b).sqrt() * proportion;
    let h = b.atan2(a);
    let a = c * h.cos();
    let b = c * h.sin();
    let [x, y, z] = [
        (l + 16.0) / 116.0 + a / 500.0,
        (l + 16.0) / 116.0,
        (l + 16.0) / 116.0 - b / 200.0,
    ]
    .map(|v| {
        if v > delta {
            v.powi(3)
        } else {
            3.0 * delta * delta * (v - 4.0 / 29.0)
        }
    });
    let x = x * 0.95047;
    let z = z * 1.08883;
    [
        x * 3.2404542 + y * (-1.5371385) + z * (-0.4985314),
        x * (-0.9692660) + y * 1.8760108 + z * 0.0415560,
        x * 0.0556434 + y * (-0.2040259) + z * 1.0572252,
    ]
    .map(|v| {
        (if v <= 0.0031308 {
            12.92 * v
        } else {
            1.055 * v.powf(1.0 / 2.4) - 0.055
        })
        .clamp(0.0, 1.0)
    })
}

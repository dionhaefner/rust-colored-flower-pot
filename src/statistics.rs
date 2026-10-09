//! Crick 0.0.8 merging t-digest with compression=100. BSD notice in assets.
#[derive(Clone, Copy)]
struct Centroid {
    mean: f64,
    weight: f64,
}

pub struct Digest {
    centroids: Vec<Centroid>,
    buffer: Vec<Centroid>,
    total: f64,
    min: f64,
    max: f64,
}
impl Default for Digest {
    fn default() -> Self {
        Self {
            centroids: Vec::new(),
            buffer: Vec::with_capacity(42),
            total: 0.,
            min: f64::MAX,
            max: -f64::MAX,
        }
    }
}
impl Digest {
    pub fn add(&mut self, value: f64) {
        if self.buffer.len() == 42 {
            self.flush();
        }
        self.buffer.push(Centroid {
            mean: value,
            weight: 1.,
        });
    }
    fn flush(&mut self) {
        if self.buffer.is_empty() {
            return;
        }
        self.buffer.sort_by(|a, b| a.mean.total_cmp(&b.mean));
        self.min = self.min.min(self.buffer[0].mean);
        self.max = self.max.max(self.buffer.last().unwrap().mean);
        self.total += self.buffer.len() as f64;
        let mut merged: Vec<Centroid> = Vec::with_capacity(200);
        let (mut i, mut j, mut weight, mut k1) = (0, 0, 0., 0.);
        let integrate = |q: f64| {
            100. * ((2. * q.min(1.) - 1.).asin() + std::f64::consts::FRAC_PI_2)
                / std::f64::consts::PI
        };
        while i < self.buffer.len() || j < self.centroids.len() {
            let c = if i < self.buffer.len()
                && (j == self.centroids.len() || self.buffer[i].mean < self.centroids[j].mean)
            {
                i += 1;
                self.buffer[i - 1]
            } else {
                j += 1;
                self.centroids[j - 1]
            };
            let k2 = integrate((weight + c.weight) / self.total);
            if !merged.is_empty() && k2 - k1 <= 1. {
                let last = merged.last_mut().unwrap();
                last.weight += c.weight;
                last.mean += (c.mean - last.mean) * c.weight / last.weight;
            } else {
                if !merged.is_empty() {
                    k1 = integrate(weight / self.total);
                }
                merged.push(c);
            }
            weight += c.weight;
        }
        self.centroids = merged;
        self.buffer.clear();
    }
    pub fn percentiles(&mut self) -> Vec<f64> {
        self.flush();
        (0..99)
            .map(|p| {
                // np.arange(.01, 1, .01), including its floating point increments.
                let index = (0.01 + p as f64 * 0.01) * self.total;
                let (mut cumulative, mut x0, mut y0) = (0., 0., self.min);
                for c in &self.centroids {
                    let x1 = cumulative + c.weight / 2.;
                    if index <= x1 {
                        return y0 + (index - x0) * (c.mean - y0) / (x1 - x0);
                    }
                    cumulative += c.weight;
                    x0 = x1;
                    y0 = c.mean;
                }
                y0 + (index - x0) * (self.max - y0) / (self.total - x0)
            })
            .collect()
    }
}

#[derive(Default)]
pub struct Moments {
    pub count: u64,
    pub sum: f64,
    pub m2: f64,
}
impl Moments {
    pub fn add(&mut self, x: f64) {
        let delta = if self.count == 0 {
            x
        } else {
            x - self.sum / self.count as f64
        };
        self.m2 += self.count as f64 * delta * (delta / (self.count + 1) as f64);
        self.count += 1;
        self.sum += x;
    }
}

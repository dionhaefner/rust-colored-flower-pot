//! Worker-owned compressed ranges share the TIFF retention budget with headers.
use bytes::Bytes;
use std::{collections::VecDeque, ops::Range, sync::Arc};

struct Entry {
    owner: Arc<()>,
    range: Range<u64>,
    data: Bytes,
}

pub(super) struct Ranges {
    entries: VecDeque<Entry>,
    bytes: usize,
    limit: usize,
}

impl Ranges {
    pub fn new() -> Self {
        Self {
            entries: VecDeque::new(),
            bytes: 0,
            limit: super::HEADER_BUDGET,
        }
    }
    pub fn limit(&mut self, limit: usize) {
        self.limit = limit;
        self.trim();
    }
    fn trim(&mut self) {
        while self.bytes > self.limit || self.entries.len() > 1024 {
            if let Some(entry) = self.entries.pop_back() {
                self.bytes -= entry.data.len();
            } else {
                break;
            }
        }
    }
    pub fn get(&mut self, owner: &Arc<()>, range: &Range<u64>) -> Option<Bytes> {
        let index = self.entries.iter().position(|e| {
            Arc::ptr_eq(owner, &e.owner) && e.range.start <= range.start && e.range.end >= range.end
        })?;
        let entry = self.entries.remove(index)?;
        let data = entry.data.slice(
            (range.start - entry.range.start) as usize..(range.end - entry.range.start) as usize,
        );
        self.entries.push_front(entry);
        Some(data)
    }
    pub fn insert(&mut self, owner: Arc<()>, range: Range<u64>, data: Bytes) {
        if data.len() > self.limit {
            return;
        }
        self.bytes += data.len();
        self.entries.push_front(Entry { owner, range, data });
        self.trim();
    }
    pub fn remove(&mut self, owner: &Arc<()>) {
        self.entries.retain(|e| !Arc::ptr_eq(owner, &e.owner));
        self.bytes = self.entries.iter().map(|e| e.data.len()).sum();
    }
}

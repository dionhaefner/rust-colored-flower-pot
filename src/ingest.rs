//! Raster pattern expansion and shared ingestion workflow.
use crate::{
    Result,
    raster::{Gdal, MetadataOptions},
    storage::{Dataset, KeyDefinition, Store},
};
use regex::Regex;
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

pub fn glob(pattern: &str) -> Result<Vec<PathBuf>> {
    let path = Path::new(pattern);
    let path = if path.is_absolute() {
        path.to_owned()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(path)
    };
    let mut paths = vec![PathBuf::from("/")];
    for part in path.components().skip(1) {
        let part = part.as_os_str().to_string_lossy();
        if !part.contains(['*', '?', '[']) {
            paths = paths.into_iter().map(|p| p.join(part.as_ref())).collect();
            continue;
        }
        let mut expression = String::from("^");
        let mut chars = part.chars().peekable();
        while let Some(c) = chars.next() {
            match c {
                '*' => expression.push_str(".*"),
                '?' => expression.push('.'),
                '[' => {
                    expression.push('[');
                    if chars.peek() == Some(&'!') {
                        chars.next();
                        expression.push('^');
                    }
                    for c in chars.by_ref() {
                        expression.push(c);
                        if c == ']' {
                            break;
                        }
                    }
                }
                _ => expression.push_str(&regex::escape(&c.to_string())),
            }
        }
        expression.push('$');
        let regex = Regex::new(&expression).map_err(|e| format!("invalid glob: {e}"))?;
        let mut matches = Vec::new();
        for parent in paths {
            if let Ok(entries) = std::fs::read_dir(parent) {
                for entry in entries {
                    let entry = entry.map_err(|e| e.to_string())?;
                    let name = entry.file_name();
                    let name = name.to_string_lossy();
                    if name.starts_with('.') && !part.starts_with('.') {
                        continue;
                    }
                    if regex.is_match(&name) {
                        matches.push(entry.path());
                    }
                }
            }
        }
        paths = matches;
    }
    paths.retain(|p| p.exists());
    paths.sort();
    Ok(paths)
}

pub fn raster_pattern(
    pattern: &str,
    rgb_key: Option<&str>,
) -> Result<(Vec<KeyDefinition>, Vec<Dataset>)> {
    let pattern = if Path::new(pattern).is_absolute() {
        pattern.to_owned()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(pattern)
            .to_string_lossy()
            .into_owned()
    };
    let mut keys = Vec::<String>::new();
    let mut parts = Vec::new();
    let mut glob_pattern = String::new();
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    let valid = Regex::new(r"^[\p{L}_][\p{L}\p{N}_]*$").unwrap();
    while i < chars.len() {
        let c = chars[i];
        i += 1;
        if (c == '{' || c == '}') && chars.get(i) == Some(&c) {
            i += 1;
            pattern_literal(&mut parts, c);
            glob_pattern.push(c);
            continue;
        }
        if c == '}' {
            return Err("Invalid pattern: unmatched }".into());
        }
        if c != '{' {
            pattern_literal(&mut parts, c);
            glob_pattern.push(c);
            continue;
        }
        let start = i;
        while i < chars.len() && chars[i] != '}' {
            i += 1;
        }
        if i == chars.len() {
            return Err("Invalid pattern: unmatched {".into());
        }
        let field = chars[start..i].iter().collect::<String>();
        i += 1;
        let name = field.split([':', '!']).next().unwrap();
        glob_pattern.push('*');
        if name.is_empty() {
            parts.push(PatternPart::Wildcard);
            continue;
        }
        if !valid.is_match(name) {
            return Err("Key names must be alphanumeric".into());
        }
        let index = if let Some(k) = keys.iter().position(|k| k == name) {
            k
        } else {
            keys.push(name.into());
            keys.len() - 1
        };
        parts.push(PatternPart::Key(index));
    }
    if keys.is_empty() {
        return Err("Pattern must contain at least one placeholder".into());
    }
    let mut datasets = Vec::new();
    let mut seen = HashSet::new();
    for path in glob(&glob_pattern)? {
        let text = path.to_string_lossy();
        let mut values = vec![String::new(); keys.len()];
        let mut budget = 1_000_000;
        let Some(length) = match_pattern(&parts, &text, &mut values, &mut budget)? else {
            continue;
        };
        if !seen.insert(values.clone()) {
            return Err("Pattern leads to duplicate keys".into());
        }
        datasets.push(Dataset {
            keys: values,
            path: PathBuf::from(&text[..length]),
        });
    }
    if datasets.is_empty() {
        return Err("Given pattern matches no files".into());
    }
    if let Some(rgb) = rgb_key {
        let index = keys
            .iter()
            .position(|k| k == rgb)
            .ok_or("RGB key not found in raster pattern")?;
        let key = keys.remove(index);
        keys.push(key);
        for d in &mut datasets {
            let value = d.keys.remove(index);
            d.keys.push(value);
        }
    }
    Ok((
        keys.into_iter()
            .map(|key| KeyDefinition {
                key,
                description: String::new(),
            })
            .collect(),
        datasets,
    ))
}

pub fn ingest(
    pattern: &str,
    output: &Path,
    rgb: Option<&str>,
    skip_metadata: bool,
    skip_existing: bool,
) -> Result<usize> {
    let (definitions, datasets) = raster_pattern(pattern, rgb)?;
    let path = output.to_string_lossy();
    let provider = crate::settings::var("TC_DRIVER_PROVIDER").ok();
    let store = match Store::open_writable(&path, provider.as_deref()) {
        Ok(store) => store,
        Err(open_error) => Store::create(&path, provider.as_deref(), &definitions)
            .map_err(|create_error| format!("{open_error}; {create_error}"))?,
    };
    // The reference emits the mismatch but keeps inserting positionally (RW-021).
    if store.key_names()
        != definitions
            .iter()
            .map(|d| d.key.as_str())
            .collect::<Vec<_>>()
    {
        eprintln!(
            "Database file {} has incompatible key names {:?}",
            output.display(),
            store.key_names()
        );
    }
    let gdal = if skip_metadata {
        None
    } else {
        Some(Gdal::load()?)
    };
    let mut count = 0;
    // Legacy nested connect contexts commit each insert, even inside CLI's outer context.
    for dataset in datasets {
        if skip_existing && store.dataset(&dataset.keys)?.is_some() {
            continue;
        }
        let metadata = gdal
            .as_ref()
            .map(|g| g.compute_metadata(&dataset.path, &MetadataOptions::default()))
            .transpose()?;
        store.insert(&dataset, metadata.as_ref())?;
        count += 1;
    }
    Ok(count)
}

#[derive(Debug)]
enum PatternPart {
    Literal(String),
    Key(usize),
    Wildcard,
}
fn pattern_literal(parts: &mut Vec<PatternPart>, c: char) {
    if let Some(PatternPart::Literal(s)) = parts.last_mut() {
        s.push(c);
    } else {
        parts.push(PatternPart::Literal(c.to_string()));
    }
}
fn match_pattern(
    parts: &[PatternPart],
    text: &str,
    values: &mut [String],
    budget: &mut usize,
) -> Result<Option<usize>> {
    if *budget == 0 {
        return Err("raster pattern matching limit exceeded".into());
    }
    *budget -= 1;
    let Some((part, rest)) = parts.split_first() else {
        return Ok(Some(0));
    };
    match part {
        PatternPart::Literal(s) => {
            if let Some(tail) = text.strip_prefix(s) {
                Ok(match_pattern(rest, tail, values, budget)?.map(|n| n + s.len()))
            } else {
                Ok(None)
            }
        }
        PatternPart::Key(k) if !values[*k].is_empty() => {
            let length = values[*k].len();
            if let Some(tail) = text.strip_prefix(&values[*k]) {
                Ok(match_pattern(rest, tail, values, budget)?.map(|n| n + length))
            } else {
                Ok(None)
            }
        }
        PatternPart::Key(k) => {
            let ends = text
                .char_indices()
                .take_while(|(_, c)| c.is_alphanumeric())
                .map(|(i, c)| i + c.len_utf8())
                .collect::<Vec<_>>();
            for end in ends.into_iter().rev() {
                values[*k] = text[..end].into();
                if let Some(n) = match_pattern(rest, &text[end..], values, budget)? {
                    return Ok(Some(n + end));
                }
                values[*k].clear();
            }
            Ok(None)
        }
        PatternPart::Wildcard => {
            for end in std::iter::once(0).chain(
                text.char_indices()
                    .take_while(|(_, c)| *c != '\n')
                    .map(|(i, c)| i + c.len_utf8()),
            ) {
                if let Some(n) = match_pattern(rest, &text[end..], values, budget)? {
                    return Ok(Some(n + end));
                }
            }
            Ok(None)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn repeated_adjacent_keys_backtrack_and_anonymous_fields_are_lazy() {
        let mut values = vec![String::new()];
        let parts = [
            PatternPart::Key(0),
            PatternPart::Key(0),
            PatternPart::Literal(".tif".into()),
        ];
        assert_eq!(
            match_pattern(&parts, "foofoo.tif", &mut values, &mut 1000).unwrap(),
            Some(10)
        );
        assert_eq!(values, vec!["foo"]);
        let mut values = vec![String::new()];
        let parts = [
            PatternPart::Wildcard,
            PatternPart::Key(0),
            PatternPart::Literal(".tif".into()),
        ];
        assert_eq!(
            match_pattern(&parts, "foo.tif", &mut values, &mut 1000).unwrap(),
            Some(7)
        );
        assert_eq!(values, vec!["foo"]);
    }
}

//! Python driver representations normalized before shared storage operations.
use crate::{Result, cli, storage::Store};
use serde_json::{Map, Value, json};

fn scalar(value: &Value) -> Result<String> {
    match value {
        Value::String(text) => Ok(text.clone()),
        Value::Number(number) => Ok(number.to_string()),
        Value::Bool(true) => Ok("True".into()),
        Value::Bool(false) => Ok("False".into()),
        _ => Err("Encountered unknown key type, expected scalar key values".into()),
    }
}

fn mapping(names: &[&str], values: &Value, required: bool) -> Result<Map<String, Value>> {
    if required && values.is_null() {
        return Err("Got wrong number of keys".into());
    }
    let map = match values {
        Value::Object(map) => map.clone(),
        Value::Array(values) => {
            if required && values.len() != names.len() {
                return Err("Got wrong number of keys".into());
            }
            names
                .iter()
                .zip(values)
                .map(|(key, value)| ((*key).into(), value.clone()))
                .collect()
        }
        Value::Null if !required => Map::new(),
        _ => return Err("Encountered unknown key type, expected Mapping or Sequence".into()),
    };
    if required && map.len() != names.len() {
        return Err("Got wrong number of keys".into());
    }
    if map.keys().any(|name| !names.contains(&name.as_str())) {
        return Err("Encountered unrecognized keys".into());
    }
    Ok(map)
}

pub(super) fn keys(store: &Store, values: &Value) -> Result<Vec<String>> {
    let names = store.key_names();
    let map = mapping(&names, values, true)?;
    names.iter().map(|name| scalar(&map[*name])).collect()
}

pub(super) fn operation(store: &Store, request: &Value) -> Result<Value> {
    let mut request = request.clone();
    let op = request["operation"]
        .as_str()
        .ok_or("operation required")?
        .to_owned();
    if op == "datasets" {
        let mut filters = Map::new();
        for (name, values) in mapping(&store.key_names(), &request["where"], false)? {
            let values = match values {
                Value::Array(values) => values.iter().map(scalar).collect::<Result<Vec<_>>>()?,
                value => vec![scalar(&value)?],
            };
            filters.insert(name, json!(values));
        }
        request["where"] = json!(filters);
        if request["order_by"].is_null() {
            let mut order = store.key_names();
            order.push("path");
            request["order_by"] = json!(order);
        }
        if request["page"].as_i64().is_some_and(|page| page < 0)
            || request["limit"].as_i64().is_some_and(|limit| limit < 0)
        {
            return Err("page and limit must be nonnegative".into());
        }
    } else if !matches!(op.as_str(), "keys" | "version") {
        request["keys"] = json!(keys(store, &request["keys"])?);
    }
    let result = cli::store_operation(store, &request)?;
    if op == "metadata" && result.is_null() {
        return Err("No dataset found".into());
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_public_mapping_sequence_and_numeric_keys() {
        let names = ["scene", "band"];
        assert_eq!(
            mapping(&names, &json!([250, "red"]), true).unwrap(),
            mapping(&names, &json!({"band":"red", "scene":250}), true).unwrap()
        );
        assert_eq!(scalar(&json!(250)).unwrap(), "250");
        assert!(mapping(&names, &json!(["scene"]), true).is_err());
        assert!(mapping(&names, &json!({"unknown":"x"}), false).is_err());
        assert!(mapping(&names, &json!(7), false).is_err());
    }
}

//! Restricted Python expression grammar compiled to bounded postfix instructions.
//! No interpreter, arbitrary calls, attribute access, or executable user code.
use crate::{Result, raster::Raster};
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Float,
    Float32,
    Float16,
    Int(u8, bool),
    Bool,
}
#[derive(Clone, Copy)]
struct Cell {
    value: f64,
    mask: bool,
    kind: Kind,
    array: bool,
}
impl Cell {
    fn number(value: f64, kind: Kind) -> Self {
        Self {
            value,
            mask: false,
            kind,
            array: false,
        }
    }
    fn truth(self) -> bool {
        self.value != 0.0
    }
}
#[derive(Clone)]
enum Instruction {
    Constant(Cell),
    Variable(String),
    Unary(String),
    Binary(String),
    Call(String, usize),
}
#[derive(Clone, Debug, PartialEq)]
enum Token {
    Number(f64, bool),
    Name(String),
    Op(String),
    End,
}
struct Parser {
    tokens: Vec<Token>,
    pos: usize,
    code: Vec<Instruction>,
    depth: usize,
}

fn tokenize(input: &str) -> Result<Vec<Token>> {
    if input.len() > 65536 {
        return Err("expression exceeds 65536 bytes".into());
    }
    let bytes = input.as_bytes();
    let mut i = 0;
    let mut tokens = Vec::new();
    while i < bytes.len() {
        let c = bytes[i];
        if c.is_ascii_whitespace() {
            i += 1;
            continue;
        }
        if c.is_ascii_digit() || (c == b'.' && bytes.get(i + 1).is_some_and(u8::is_ascii_digit)) {
            let start = i;
            while i < bytes.len() && (bytes[i].is_ascii_digit() || bytes[i] == b'.') {
                i += 1;
            }
            if i < bytes.len() && matches!(bytes[i], b'e' | b'E') {
                i += 1;
                if i < bytes.len() && matches!(bytes[i], b'+' | b'-') {
                    i += 1;
                }
                while i < bytes.len() && bytes[i].is_ascii_digit() {
                    i += 1;
                }
            }
            let s = &input[start..i];
            tokens.push(Token::Number(
                s.parse()
                    .map_err(|_| "given string is not a valid expression")?,
                !s.contains(['.', 'e', 'E']),
            ));
        } else if c.is_ascii_alphabetic() || c == b'_' {
            let start = i;
            i += 1;
            while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
                i += 1;
            }
            tokens.push(Token::Name(input[start..i].into()));
        } else {
            let two = bytes
                .get(i..i + 2)
                .and_then(|v| std::str::from_utf8(v).ok())
                .unwrap_or("");
            if ["**", "==", "!=", "<=", ">=", "//", "<<", ">>"].contains(&two) {
                tokens.push(Token::Op(two.into()));
                i += 2;
            } else {
                tokens.push(Token::Op((c as char).to_string()));
                i += 1;
            }
        }
        if tokens.len() > 16384 {
            return Err("expression has too many tokens".into());
        }
    }
    tokens.push(Token::End);
    Ok(tokens)
}
impl Parser {
    fn op(&self, op: &str) -> bool {
        self.tokens[self.pos] == Token::Op(op.into())
    }
    fn expression(&mut self, min: u8) -> Result<bool> {
        self.depth += 1;
        if self.depth > 128 {
            return Err("expression nesting exceeds 128".into());
        }
        let token = self.tokens[self.pos].clone();
        self.pos += 1;
        let mut comparison = false;
        match token {
            Token::Number(v, int) => self.code.push(Instruction::Constant(Cell::number(
                v,
                if int {
                    Kind::Int(64, true)
                } else {
                    Kind::Float
                },
            ))),
            Token::Name(name) => {
                if name == "not" {
                    return Err("unary operator Not not allowed in expressions".into());
                }
                if self.op("(") {
                    self.pos += 1;
                    let mut nargs = 0;
                    if !self.op(")") {
                        loop {
                            self.expression(0)?;
                            nargs += 1;
                            if !self.op(",") {
                                break;
                            }
                            self.pos += 1;
                            if self.op(")") {
                                break;
                            }
                        }
                    }
                    if !self.op(")") {
                        return Err("given string is not a valid expression".into());
                    }
                    self.pos += 1;
                    let expected = arity(&name)
                        .ok_or_else(|| format!("unrecognized name '{name}' in expression"))?;
                    if nargs != expected {
                        return Err(format!(
                            "wrong number of arguments for function {name} (got {nargs}, expected {expected})"
                        ));
                    }
                    self.code.push(Instruction::Call(name, nargs));
                } else {
                    let value = match name.as_str() {
                        "pi" => Some(Cell::number(std::f64::consts::PI, Kind::Float)),
                        "nan" => Some(Cell::number(f64::NAN, Kind::Float)),
                        "inf" => Some(Cell::number(f64::INFINITY, Kind::Float)),
                        "nomask" | "False" => Some(Cell::number(0.0, Kind::Bool)),
                        "True" => Some(Cell::number(1.0, Kind::Bool)),
                        _ => None,
                    };
                    self.code.push(if let Some(v) = value {
                        Instruction::Constant(v)
                    } else {
                        Instruction::Variable(name)
                    });
                }
            }
            Token::Op(op) if op == "-" || op == "~" => {
                self.expression(6)?;
                self.code.push(Instruction::Unary(op));
            }
            Token::Op(op) if op == "(" => {
                self.expression(0)?;
                if !self.op(")") {
                    return Err("given string is not a valid expression".into());
                }
                self.pos += 1;
            }
            Token::Op(op) if op == "+" => {
                return Err("unary operator UAdd not allowed in expressions".into());
            }
            _ => return Err("construct not allowed in expressions".into()),
        }
        loop {
            let Token::Op(op) = self.tokens[self.pos].clone() else {
                break;
            };
            let precedence = match op.as_str() {
                "==" | "!=" | "<" | "<=" | ">" | ">=" => 1,
                "|" => 2,
                "&" => 3,
                "+" | "-" => 4,
                "*" | "/" | "%" => 5,
                "**" => 7,
                _ => break,
            };
            if precedence < min {
                break;
            }
            if precedence == 1 && comparison {
                return Err("chained comparisons are not supported".into());
            }
            self.pos += 1;
            self.expression(if op == "**" {
                precedence
            } else {
                precedence + 1
            })?;
            self.code.push(Instruction::Binary(op));
            comparison = precedence == 1;
        }
        self.depth -= 1;
        Ok(comparison)
    }
}
fn arity(name: &str) -> Option<usize> {
    Some(match name {
        "where" | "masked_inside" | "masked_outside" => 3,
        "minimum"
        | "maximum"
        | "setmask"
        | "masked_equal"
        | "masked_greater"
        | "masked_greater_equal"
        | "masked_less"
        | "masked_less_equal"
        | "masked_not_equal"
        | "masked_where" => 2,
        "getmask" | "masked_invalid" | "abs" | "mod" | "sqrt" | "log" | "log10" | "exp" | "sin"
        | "cos" | "tan" | "sinh" | "cosh" | "tanh" | "arcsin" | "arccos" | "arctan" | "arcsinh"
        | "arccosh" | "arctanh" => 1,
        _ => return None,
    })
}

pub fn evaluate(input: &str, operands: &BTreeMap<String, &Raster>) -> Result<Raster> {
    let mut parser = Parser {
        tokens: tokenize(input)?,
        pos: 0,
        code: Vec::new(),
        depth: 0,
    };
    parser.expression(0)?;
    if parser.tokens[parser.pos] != Token::End {
        let error = match &parser.tokens[parser.pos] {
            Token::Op(s) if s == "^" => "binary operator BitXor not allowed in expressions",
            Token::Op(s) if s == "//" => "binary operator FloorDiv not allowed in expressions",
            _ => "construct not allowed in expressions",
        };
        return Err(error.into());
    }
    for op in &parser.code {
        if let Instruction::Variable(name) = op {
            if !operands.contains_key(name) {
                return Err(format!("unrecognized name '{name}' in expression"));
            }
        }
    }
    let first = operands.values().next();
    let (width, height) = first.map(|r| (r.width, r.height)).unwrap_or((1, 1));
    if operands
        .values()
        .any(|r| r.width != width || r.height != height)
    {
        return Err("operands could not be broadcast together".into());
    }
    let mut out = Raster {
        data_type: 7,
        width,
        height,
        values: Vec::with_capacity(width * height),
        mask: Vec::with_capacity(width * height),
    };
    let mut stack = Vec::<Cell>::with_capacity(parser.code.len());
    let start = Instant::now();
    for i in 0..width * height {
        if i % 1024 == 0 && start.elapsed() > Duration::from_secs(10) {
            return Err("timeout during pattern evaluation".into());
        }
        stack.clear();
        for op in &parser.code {
            match op {
                Instruction::Constant(c) => stack.push(*c),
                Instruction::Variable(name) => {
                    let r = operands[name];
                    stack.push(Cell {
                        value: r.values[i],
                        mask: r.mask[i],
                        kind: sample_kind(r.data_type),
                        array: true,
                    });
                }
                Instruction::Unary(op) => {
                    let mut a = stack.pop().unwrap();
                    if op == "-" {
                        a.value = cast(-a.value, a.kind);
                    } else if a.kind == Kind::Bool {
                        a.value = (!a.truth()) as u8 as f64;
                    } else if matches!(a.kind, Kind::Int(..)) {
                        a.value = cast((!(a.value as i64)) as f64, a.kind);
                    } else {
                        return Err("unexpected error while evaluating expression: invert is not supported for float operands".into());
                    }
                    stack.push(a);
                }
                Instruction::Binary(op) => {
                    let b = stack.pop().unwrap();
                    let a = stack.pop().unwrap();
                    stack.push(binary(op, a, b)?);
                }
                Instruction::Call(name, n) => {
                    let pos = stack.len() - n;
                    let c = call(name, &stack[pos..])?;
                    stack.truncate(pos);
                    stack.push(c);
                }
            }
        }
        let c = stack[0];
        if !c.array {
            return Err("expression does not return an array".into());
        }
        out.values.push(c.value);
        out.mask.push(c.mask || !c.value.is_finite());
    }
    Ok(out)
}
fn binary(op: &str, a: Cell, b: Cell) -> Result<Cell> {
    if !a.array && !b.array && b.value == 0.0 && ["/", "%"].contains(&op) {
        return Err(
            "unexpected error while evaluating expression: division or modulo by zero".into(),
        );
    }
    let mut c = a;
    c.mask = a.mask || b.mask;
    c.array = a.array || b.array;
    c.kind = promote(a.kind, b.kind);
    c.value = match op {
        "+" => a.value + b.value,
        "-" => a.value - b.value,
        "*" => a.value * b.value,
        "/" => {
            c.kind = Kind::Float;
            if b.value.abs() * 1e35 <= a.value.abs() {
                c.mask = true;
            }
            a.value / b.value
        }
        "%" => {
            if b.value == 0.0 {
                c.mask = true;
            }
            ((a.value % b.value) + b.value) % b.value
        }
        "**" => {
            if matches!(a.kind, Kind::Int(..)) && matches!(b.kind, Kind::Int(..)) && b.value < 0.0 {
                return Err("unexpected error while evaluating expression: Integers to negative integer powers are not allowed.".into());
            }
            let v = a.value.powf(b.value);
            if !v.is_finite() {
                c.mask = true;
            }
            v
        }
        "&" | "|" => {
            if matches!(a.kind, Kind::Float | Kind::Float32 | Kind::Float16)
                || matches!(b.kind, Kind::Float | Kind::Float32 | Kind::Float16)
            {
                return Err("unexpected error while evaluating expression: bitwise operation is not supported for float operands".into());
            }
            c.kind = if a.kind == Kind::Bool && b.kind == Kind::Bool {
                Kind::Bool
            } else {
                Kind::Int(64, true)
            };
            if op == "&" {
                (a.value as i64 & b.value as i64) as f64
            } else {
                (a.value as i64 | b.value as i64) as f64
            }
        }
        _ => {
            c.kind = Kind::Bool;
            (match op {
                "==" => a.value == b.value,
                "!=" => a.value != b.value,
                "<" => a.value < b.value,
                "<=" => a.value <= b.value,
                ">" => a.value > b.value,
                ">=" => a.value >= b.value,
                _ => unreachable!(),
            }) as u8 as f64
        }
    };
    c.value = cast(c.value, c.kind);
    // MaskedArray arithmetic keeps the left operand's hidden data. Comparisons
    // and NumPy ufuncs still compute data; setmask can make it observable.
    if c.mask && ["+", "-", "*", "/", "%"].contains(&op) {
        c.value = a.value;
    }
    Ok(c)
}
fn call(name: &str, args: &[Cell]) -> Result<Cell> {
    let a = args[0];
    let b = args.get(1).copied().unwrap_or(a);
    let d = args.get(2).copied().unwrap_or(a);
    let mut c = a;
    c.array = args.iter().any(|a| a.array);
    if name.starts_with("masked_") && name != "masked_where" {
        c.mask |= args.iter().any(|a| a.mask);
    }
    match name {
        "where"=>{c=if a.truth() {b}else{d}; c.array=args.iter().any(|a|a.array); c.mask|=a.mask;}
        "getmask"=>{c.value=a.mask as u8 as f64;c.mask=false;c.kind=Kind::Bool;c.array=true;}
        "setmask"=>{c.mask=a.mask||b.truth();}
        "masked_where"=>{c=b;c.array=a.array||b.array;c.mask=b.mask||a.mask||a.truth();}
        "masked_invalid"=>c.mask|= !a.value.is_finite(),
        "masked_equal"=>c.mask|=a.value==b.value,
        "masked_not_equal"=>c.mask|=a.value!=b.value,
        "masked_greater"=>c.mask|=a.value>b.value,
        "masked_greater_equal"=>c.mask|=a.value>=b.value,
        "masked_less"=>c.mask|=a.value<b.value,
        "masked_less_equal"=>c.mask|=a.value<=b.value,
        "masked_inside"=>c.mask|=a.value>=b.value.min(d.value)&&a.value<=b.value.max(d.value),
        "masked_outside"=>c.mask|=a.value<b.value.min(d.value)||a.value>b.value.max(d.value),
        "minimum"|"maximum"=>{c.mask|=b.mask;c.value=if a.value.is_nan()||b.value.is_nan(){f64::NAN}else if name=="minimum" {a.value.min(b.value)}else{a.value.max(b.value)};}
        "mod"=>return Err("unexpected error while evaluating expression: remainder() takes from 2 to 3 positional arguments but 1 were given".into()),
        _=>{c.value=match name {"abs"=>a.value.abs(),"sqrt"=>a.value.sqrt(),"log"=>a.value.ln(),"log10"=>a.value.log10(),"exp"=>a.value.exp(),"sin"=>a.value.sin(),"cos"=>a.value.cos(),"tan"=>a.value.tan(),"sinh"=>a.value.sinh(),"cosh"=>a.value.cosh(),"tanh"=>a.value.tanh(),"arcsin"=>a.value.asin(),"arccos"=>a.value.acos(),"arctan"=>a.value.atan(),"arcsinh"=>a.value.asinh(),"arccosh"=>a.value.acosh(),"arctanh"=>a.value.atanh(),_=>unreachable!()};c.kind=match a.kind { Kind::Bool|Kind::Int(8,_)=>Kind::Float16,Kind::Int(16,_)|Kind::Float32=>Kind::Float32,Kind::Float16=>Kind::Float16,_=>Kind::Float };
            if name=="abs" {c.kind=a.kind;}
            c.value=cast(c.value,c.kind);
            // NumPy masked ufuncs mark invalid domains immediately.
            if ["sqrt","log","log10","arcsin","arccos","arccosh","arctanh"].contains(&name) && !c.value.is_finite() {c.mask=true;}
        }
    }
    Ok(c)
}

fn sample_kind(dtype: i32) -> Kind {
    match dtype {
        1 => Kind::Int(8, false),
        2 => Kind::Int(16, false),
        3 => Kind::Int(16, true),
        4 => Kind::Int(32, false),
        5 => Kind::Int(32, true),
        6 => Kind::Float32,
        12 => Kind::Int(64, false),
        13 => Kind::Int(64, true),
        14 => Kind::Int(8, true),
        _ => Kind::Float,
    }
}
fn promote(a: Kind, b: Kind) -> Kind {
    if a == b {
        return a;
    }
    match (a, b) {
        (Kind::Float, _) | (_, Kind::Float) => Kind::Float,
        (Kind::Float32, Kind::Int(bits, _)) | (Kind::Int(bits, _), Kind::Float32) if bits > 16 => {
            Kind::Float
        }
        (Kind::Float32, _) | (_, Kind::Float32) => Kind::Float32,
        (Kind::Float16, b) | (b, Kind::Float16) => {
            if matches!(b, Kind::Bool | Kind::Int(8, _) | Kind::Float16) {
                Kind::Float16
            } else {
                Kind::Float32
            }
        }
        (Kind::Bool, b) => b,
        (a, Kind::Bool) => a,
        (Kind::Int(x, s), Kind::Int(y, t)) => Kind::Int(
            if s == t {
                x.max(y)
            } else {
                x.max(y).saturating_mul(2).min(64)
            },
            s || t,
        ),
    }
}
fn cast(value: f64, kind: Kind) -> f64 {
    match kind {
        Kind::Int(bits, signed) if value.is_finite() => {
            let modulo = 2_f64.powi(bits as i32);
            if signed && value >= -modulo / 2.0 && value < modulo / 2.0 {
                return value.trunc();
            }
            let v = value.rem_euclid(modulo);
            if signed && v >= modulo / 2.0 {
                v - modulo
            } else {
                v
            }
        }
        Kind::Float16 => {
            let v = (value as f32) as f64;
            if !v.is_finite() || v == 0.0 {
                v
            } else if v.abs() >= 65520.0 {
                f64::INFINITY.copysign(v)
            } else {
                let step = 2_f64.powf((v.abs().log2().floor() - 10.0).max(-24.0));
                (v / step).round_ties_even() * step
            }
        }
        Kind::Float32 => (value as f32) as f64,
        _ => value,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn expression_contract_vectors() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/expressions.json")).unwrap();
        check_fixture(&fixture);
        let suites: Vec<serde_json::Value> =
            serde_json::from_str(include_str!("../tests/fixtures/expression-types.json")).unwrap();
        for fixture in suites {
            check_fixture(&fixture);
        }
    }
    fn check_fixture(fixture: &serde_json::Value) {
        let rasters: BTreeMap<String, Raster> = fixture["operands"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| {
                (
                    k.clone(),
                    Raster {
                        data_type: match v["dtype"].as_str().unwrap_or("float64") {
                            "uint8" => 1,
                            "uint16" => 2,
                            "int16" => 3,
                            "float32" => 6,
                            _ => 7,
                        },
                        width: v["values"].as_array().unwrap().len(),
                        height: 1,
                        values: serde_json::from_value(v["values"].clone()).unwrap(),
                        mask: serde_json::from_value(v["mask"].clone()).unwrap(),
                    },
                )
            })
            .collect();
        let operands = rasters.iter().map(|(k, v)| (k.clone(), v)).collect();
        for case in fixture["cases"].as_array().unwrap() {
            let expr = case["expression"].as_str().unwrap();
            let result = evaluate(expr, &operands);
            if case.get("error").is_some() {
                assert!(result.is_err(), "{expr}");
                continue;
            }
            let result = result.unwrap_or_else(|e| panic!("{expr}: {e}"));
            let masks: Vec<bool> = serde_json::from_value(case["mask"].clone()).unwrap();
            assert_eq!(result.mask, masks, "mask: {expr}");
            for (i, v) in case["values"].as_array().unwrap().iter().enumerate() {
                if !masks[i] {
                    let expected = v
                        .as_f64()
                        .unwrap_or_else(|| v.as_bool().unwrap() as u8 as f64);
                    let transcendental = [
                        "exp(", "log(", "log10(", "sin(", "cos(", "tan(", "sinh(", "cosh(",
                        "tanh(", "arcsin(", "arccos(", "arctan(", "arcsinh(", "arccosh(",
                        "arctanh(",
                    ]
                    .iter()
                    .any(|f| expr.starts_with(f));
                    let tolerance = if case["dtype"] == "float32" && transcendental {
                        2.0 * f32::EPSILON as f64 * expected.abs().max(f32::MIN_POSITIVE as f64)
                    } else {
                        1e-12 * expected.abs().max(1.0)
                    };
                    assert!(
                        (result.values[i] - expected).abs() <= tolerance,
                        "{expr} index {i}: {} != {expected}",
                        result.values[i]
                    );
                }
            }
        }
    }
}

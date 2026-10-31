//! Allocation-light preflight before serde's internally tagged enum buffers its
//! content. Reject excessive structure/strings before constructing owned rows.
use crate::table::{MAX_ROWS, MAX_STRING};
use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use std::fmt;

pub(crate) fn error_code(e: &serde_json::Error) -> &'static str {
    let message = e.to_string();
    for code in [
        "row_limit",
        "string_limit",
        "table_bytes_limit",
        "nul_not_supported",
        "json_structure_limit",
    ] {
        if message.starts_with(code) {
            return code;
        }
    }
    if e.is_syntax() || e.is_eof() {
        "invalid_json"
    } else {
        "invalid_request"
    }
}

pub(crate) fn preflight(input: &str) -> Result<(), &'static str> {
    let mut de = serde_json::Deserializer::from_str(input);
    let mut nodes = 0;
    Seed { nodes: &mut nodes }
        .deserialize(&mut de)
        .map_err(|e| error_code(&e))?;
    de.end().map_err(|e| error_code(&e))
}
struct Seed<'a> {
    nodes: &'a mut usize,
}
impl<'de> DeserializeSeed<'de> for Seed<'_> {
    type Value = ();
    fn deserialize<D: de::Deserializer<'de>>(self, d: D) -> Result<(), D::Error> {
        *self.nodes += 1;
        // OURS: 100k rows * (array + two strings), plus protocol envelope.
        if *self.nodes > 3 * MAX_ROWS + 64 {
            return Err(de::Error::custom("json_structure_limit"));
        }
        d.deserialize_any(self)
    }
}
impl<'de> Visitor<'de> for Seed<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("bounded JSON")
    }
    fn visit_unit<E: de::Error>(self) -> Result<(), E> {
        Ok(())
    }
    fn visit_bool<E: de::Error>(self, _: bool) -> Result<(), E> {
        Ok(())
    }
    fn visit_u64<E: de::Error>(self, _: u64) -> Result<(), E> {
        Ok(())
    }
    fn visit_i64<E: de::Error>(self, _: i64) -> Result<(), E> {
        Ok(())
    }
    fn visit_f64<E: de::Error>(self, _: f64) -> Result<(), E> {
        Ok(())
    }
    fn visit_str<E: de::Error>(self, s: &str) -> Result<(), E> {
        if s.len() > MAX_STRING {
            return Err(E::custom("string_limit"));
        }
        if s.contains('\0') {
            return Err(E::custom("nul_not_supported"));
        }
        Ok(())
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
        let mut count = 0;
        while seq.next_element_seed(Seed { nodes: self.nodes })?.is_some() {
            count += 1;
            if count > MAX_ROWS {
                return Err(de::Error::custom("row_limit"));
            }
        }
        Ok(())
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
        let mut count = 0;
        while map.next_key_seed(Seed { nodes: self.nodes })?.is_some() {
            count += 1;
            if count > 16 {
                return Err(de::Error::custom("json_structure_limit"));
            }
            map.next_value_seed(Seed { nodes: self.nodes })?;
        }
        Ok(())
    }
}

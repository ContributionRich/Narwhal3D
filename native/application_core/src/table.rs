use crate::ordinal_map::ORDINAL_MAP;
use serde::de::{self, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use std::{collections::HashMap, fmt, sync::Arc};

// OURS: protocol resource bounds, not reference-application limits. All sizes are UTF-8 bytes.
pub(crate) const MAX_ROWS: usize = 100_000;
pub(crate) const MAX_STRING: usize = 8 * 1024 * 1024;
pub(crate) const MAX_TABLE_BYTES: usize = 64 * 1024 * 1024;
pub(crate) type Row = (Arc<str>, Arc<str>);
pub(crate) type Result<T> = std::result::Result<T, &'static str>;

#[derive(Debug)]
pub(crate) struct Text(pub String);
impl<'de> Deserialize<'de> for Text {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        struct TextVisitor;
        impl Visitor<'_> for TextVisitor {
            type Value = Text;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a bounded NUL-free string")
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Text, E> {
                validate_text(v).map_err(E::custom)?;
                Ok(Text(v.to_owned()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> std::result::Result<Text, E> {
                validate_text(&v).map_err(E::custom)?;
                Ok(Text(v))
            }
        }
        deserializer.deserialize_string(TextVisitor)
    }
}
fn validate_text(s: &str) -> Result<()> {
    if s.len() > MAX_STRING {
        return Err("string_limit");
    }
    if s.contains('\0') {
        return Err("nul_not_supported");
    }
    Ok(())
}

pub(crate) fn deserialize_rows<'de, D: Deserializer<'de>>(
    d: D,
) -> std::result::Result<Vec<(Text, Text)>, D::Error> {
    struct RowsVisitor;
    impl<'de> Visitor<'de> for RowsVisitor {
        type Value = Vec<(Text, Text)>;
        fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
            f.write_str("bounded [key,value] rows")
        }
        fn visit_seq<A: SeqAccess<'de>>(
            self,
            mut seq: A,
        ) -> std::result::Result<Self::Value, A::Error> {
            let mut rows = Vec::new();
            let mut bytes = 0;
            while let Some((k, v)) = seq.next_element::<(Text, Text)>()? {
                if rows.len() == MAX_ROWS {
                    return Err(de::Error::custom("row_limit"));
                }
                bytes += k.0.len() + v.0.len();
                if bytes > MAX_TABLE_BYTES {
                    return Err(de::Error::custom("table_bytes_limit"));
                }
                rows.push((k, v));
            }
            Ok(rows)
        }
    }
    d.deserialize_seq(RowsVisitor)
}

// MEASURED: exactly ONE mapping pass. E.g. dotless i -> capital I, not ASCII i.
// Reapplying the map or using Rust Unicode case conversion changes equality.
pub(crate) fn normalized(key: &str) -> String {
    key.chars()
        .map(|c| {
            let cp = c as u32;
            let mapped = ORDINAL_MAP
                .binary_search_by_key(&cp, |pair| pair.0)
                .map(|i| ORDINAL_MAP[i].1)
                .unwrap_or(cp);
            char::from_u32(mapped).expect("generated mapping contains only Unicode scalars")
        })
        .collect()
}

#[derive(Clone, Debug)]
pub(crate) struct Table {
    pub rows: Vec<Row>,
    index: HashMap<Arc<str>, usize>,
    pub bytes: usize,
}
impl Table {
    pub fn replay(rows: Vec<(Text, Text)>) -> Self {
        // Replay Set with stable first spelling/order, without O(n²) shifting on
        // duplicate-heavy imports. An actual delete followed by reinsert appends.
        let mut builder: HashMap<String, (usize, Row)> = HashMap::new();
        for (order, (Text(key), Text(value))) in rows.into_iter().enumerate() {
            if key.is_empty() {
                continue;
            }
            let norm = normalized(&key);
            if value.is_empty() {
                builder.remove(&norm);
            } else if let Some((_, (_, previous))) = builder.get_mut(&norm) {
                *previous = value.into();
            } else {
                builder.insert(norm, (order, (key.into(), value.into())));
            }
        }
        let mut ordered: Vec<_> = builder.into_values().collect();
        ordered.sort_unstable_by_key(|(order, _)| *order);
        Self::from_rows(ordered.into_iter().map(|(_, row)| row).collect())
    }
    fn from_rows(rows: Vec<Row>) -> Self {
        let bytes = rows.iter().map(|(k, v)| k.len() + v.len()).sum();
        let index = rows
            .iter()
            .enumerate()
            .map(|(i, (k, _))| (Arc::from(normalized(k)), i))
            .collect();
        Self { rows, index, bytes }
    }
    // MEASURED: rhino3dm 8.35.0 File3dm.Strings.DocumentUserTextCount counts
    // keys containing ASCII backslash once per row, including leading/trailing
    // separators. Values and forward/fullwidth slashes do not contribute.
    pub fn document_user_text_count(&self) -> u64 {
        self.rows
            .iter()
            .filter(|(key, _)| key.contains('\\'))
            .count() as u64
    }
    pub fn find(&self, key: &str, exact: bool) -> Option<&Row> {
        let row = self
            .index
            .get(normalized(key).as_str())
            .map(|i| &self.rows[*i])?;
        (!exact || row.0.as_ref() == key).then_some(row)
    }
    // Validate and classify before cloning anything; no-op prepares retain identity.
    pub fn change(&self, key: &str, value: Option<&str>) -> Result<Option<Change>> {
        if key.is_empty() {
            return Ok(None);
        }
        let norm = normalized(key);
        let index = self.index.get(norm.as_str()).copied();
        let value = value.filter(|v| !v.is_empty());
        match (index, value) {
            (None, None) => Ok(None),
            (Some(i), Some(v)) if self.rows[i].1.as_ref() == v => Ok(None),
            _ => {
                let bytes = match (index, value) {
                    (Some(i), None) => self.bytes - self.rows[i].0.len() - self.rows[i].1.len(),
                    (Some(i), Some(v)) => self.bytes - self.rows[i].1.len() + v.len(),
                    (None, Some(v)) => self.bytes + key.len() + v.len(),
                    _ => unreachable!(),
                };
                let count =
                    self.rows.len() + usize::from(index.is_none()) - usize::from(value.is_none());
                if count > MAX_ROWS {
                    return Err("row_limit");
                }
                if bytes > MAX_TABLE_BYTES {
                    return Err("table_bytes_limit");
                }
                Ok(Some(Change {
                    index,
                    key: key.into(),
                    value: value.map(Arc::from),
                    bytes,
                    count,
                }))
            }
        }
    }
    pub fn changed(&self, change: Change) -> Self {
        let mut table = self.clone();
        match (change.index, change.value) {
            (Some(i), Some(value)) => table.rows[i].1 = value,
            (Some(i), None) => {
                table.index.remove(normalized(&table.rows[i].0).as_str());
                table.rows.remove(i);
                for value in table.index.values_mut() {
                    if *value > i {
                        *value -= 1;
                    }
                }
            }
            (None, Some(value)) => {
                table
                    .index
                    .insert(normalized(&change.key).into(), table.rows.len());
                table.rows.push((change.key, value));
            }
            _ => unreachable!(),
        }
        table.bytes = change.bytes;
        table
    }
}
pub(crate) struct Change {
    index: Option<usize>,
    key: Arc<str>,
    value: Option<Arc<str>>,
    pub bytes: usize,
    pub count: usize,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn every_scalar_uses_exactly_one_generated_mapping() {
        let mut at = 0;
        for cp in 0..=0x10ffff {
            let Some(c) = char::from_u32(cp) else {
                continue;
            };
            let expected = if at < ORDINAL_MAP.len() && ORDINAL_MAP[at].0 == cp {
                at += 1;
                ORDINAL_MAP[at - 1].1
            } else {
                cp
            };
            assert_eq!(
                normalized(&c.to_string()),
                char::from_u32(expected).unwrap().to_string()
            );
        }
        assert_eq!(at, 205);
    }
    #[test]
    fn table_byte_bound_and_first_spelling_accounting() {
        let value = "v".repeat(MAX_STRING - 1);
        let input: Vec<_> = (0..8)
            .map(|i| (Text(i.to_string()), Text(value.clone())))
            .collect();
        let table = Table::replay(input);
        assert_eq!(table.bytes, MAX_TABLE_BYTES);
        assert!(matches!(
            table.change("extra", Some("v")),
            Err("table_bytes_limit")
        ));
        assert!(matches!(
            table.change("0", Some(&"v".repeat(MAX_STRING))),
            Err("table_bytes_limit")
        ));
        assert!(table.change("0", None).unwrap().is_some());
        // Capital dotted I folds to one-byte i, but the first key uses two bytes.
        let table = Table::replay(vec![(Text("İ".into()), Text("old".into()))]);
        let changed = table.changed(table.change("i", Some("newer")).unwrap().unwrap());
        assert_eq!(changed.bytes, "İnewer".len());
        assert_eq!(changed.rows[0].0.as_ref(), "İ");
    }
}

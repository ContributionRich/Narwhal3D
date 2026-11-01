//! Bounded, borrowed decoding of the read-only NWOBS01 openNURBS host boundary.
//! Archive bytes are opaque witnesses, NOT substitutes for exact kernel geometry.
//! Parsing does not publish registry state or establish full-document authority.
use std::collections::HashSet;

pub const MAX_BATCH: usize = 128 * 1024 * 1024;
pub const MAX_ARCHIVE: usize = 16 * 1024 * 1024;
pub const MAX_ROWS: usize = 100_000;
pub const MAX_NAME: usize = 4096;

#[derive(Debug, PartialEq, Eq)]
pub struct Object<'a> {
    pub id: &'a str,
    pub name: &'a str,
    pub geometry: &'a [u8],
    pub attributes: &'a [u8],
}

struct Reader<'a> {
    remaining: &'a [u8],
}
impl<'a> Reader<'a> {
    fn take(&mut self, size: usize) -> Result<&'a [u8], &'static str> {
        if size > self.remaining.len() {
            return Err("observation_truncated");
        }
        let (value, rest) = self.remaining.split_at(size);
        self.remaining = rest;
        Ok(value)
    }
    fn u32(&mut self) -> Result<usize, &'static str> {
        let bytes: [u8; 4] = self.take(4)?.try_into().expect("exact length");
        usize::try_from(u32::from_le_bytes(bytes)).map_err(|_| "observation_length")
    }
    fn blob(&mut self, max: usize) -> Result<&'a [u8], &'static str> {
        let size = self.u32()?;
        if size > max {
            return Err("observation_field_limit");
        }
        self.take(size)
    }
}

/// Decode one complete batch. No bytes or pointers are retained beyond its borrow.
/// Duplicate/noncanonical/nil UUIDs and incomplete/oversized fields refuse before
/// any registry operation. Native archive validity is the observer's responsibility;
/// this parser neither reconstructs geometry nor treats arbitrary input as trusted.
pub fn decode(bytes: &[u8]) -> Result<Vec<Object<'_>>, &'static str> {
    decode_kind(bytes, true)
}

/// Membership/name-only index batch; never a geometry witness.
pub fn decode_index(bytes: &[u8]) -> Result<Vec<Object<'_>>, &'static str> {
    decode_kind(bytes, false)
}

fn decode_kind(bytes: &[u8], full: bool) -> Result<Vec<Object<'_>>, &'static str> {
    if bytes.len() > MAX_BATCH {
        return Err("observation_batch_limit");
    }
    let mut input = Reader { remaining: bytes };
    let expected = if full { b"NWOBS01\0" } else { b"NWIDX01\0" };
    if input.take(8)? != expected {
        return Err("observation_protocol");
    }
    let count = input.u32()?;
    if count > MAX_ROWS {
        return Err("observation_row_limit");
    }
    // 36 UUID bytes, three length words, and two nonempty archive fields.
    // Refuse forged counts before allocating vectors or the duplicate index.
    let minimum_row = if full { 50 } else { 40 };
    if count > input.remaining.len() / minimum_row {
        return Err("observation_truncated");
    }
    let mut rows = Vec::with_capacity(count);
    let mut seen = HashSet::with_capacity(count);
    for _ in 0..count {
        let id = std::str::from_utf8(input.take(36)?).map_err(|_| "invalid_uuid")?;
        super::object_registry::uuid(id)?;
        if !seen.insert(id) {
            return Err("duplicate_uuid");
        }
        let name =
            std::str::from_utf8(input.blob(MAX_NAME)?).map_err(|_| "observation_name_utf8")?;
        if name.contains('\0') {
            return Err("observation_name_nul");
        }
        let (geometry, attributes) = if full {
            let geometry = input.blob(MAX_ARCHIVE)?;
            let attributes = input.blob(MAX_ARCHIVE)?;
            if geometry.is_empty() || attributes.is_empty() {
                return Err("observation_empty_archive");
            }
            (geometry, attributes)
        } else {
            (&[][..], &[][..])
        };
        rows.push(Object {
            id,
            name,
            geometry,
            attributes,
        });
    }
    if !input.remaining.is_empty() {
        return Err("observation_trailing_bytes");
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn batch(ids: &[&str], name: &[u8], geometry: &[u8], attributes: &[u8]) -> Vec<u8> {
        let mut bytes = b"NWOBS01\0".to_vec();
        bytes.extend_from_slice(&(ids.len() as u32).to_le_bytes());
        for id in ids {
            bytes.extend_from_slice(id.as_bytes());
            for field in [name, geometry, attributes] {
                bytes.extend_from_slice(&(field.len() as u32).to_le_bytes());
                bytes.extend_from_slice(field);
            }
        }
        bytes
    }
    const FIRST: &str = "00000000-0000-0000-0000-000000000001";
    const SECOND: &str = "00000000-0000-0000-0000-000000000002";

    #[test]
    fn exact_borrowed_fields_keep_order_and_unicode() {
        let bytes = batch(
            &[SECOND, FIRST],
            "海🐋".as_bytes(),
            &[0, 255, 3],
            &[4, 0, 6],
        );
        let rows = decode(&bytes).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].id, SECOND);
        assert_eq!(rows[1].id, FIRST);
        assert_eq!(rows[0].name, "海🐋");
        assert_eq!(rows[0].geometry, &[0, 255, 3]);
        assert_eq!(rows[0].attributes, &[4, 0, 6]);
        assert!(rows[0].geometry.as_ptr() >= bytes.as_ptr());
        assert!(rows[0].geometry.as_ptr() < bytes.as_ptr().wrapping_add(bytes.len()));
        assert!(decode(&batch(&[], b"", b"g", b"a")).unwrap().is_empty());
    }

    #[test]
    fn every_truncated_prefix_and_trailing_byte_refuses() {
        let bytes = batch(&[FIRST], b"a", b"geometry", b"attributes");
        for end in 0..bytes.len() {
            assert!(decode(&bytes[..end]).is_err(), "accepted prefix {end}");
        }
        let mut trailing = bytes.clone();
        trailing.push(0);
        assert_eq!(decode(&trailing), Err("observation_trailing_bytes"));
        let mut wrong = bytes;
        wrong[6] = b'2';
        assert_eq!(decode(&wrong), Err("observation_protocol"));
    }

    #[test]
    fn identity_duplicate_nil_and_noncanonical_refuse() {
        assert_eq!(
            decode(&batch(&[FIRST, FIRST], b"", b"g", b"a")),
            Err("duplicate_uuid")
        );
        for id in [
            "00000000-0000-0000-0000-000000000000",
            "00000000-0000-0000-0000-00000000000A",
            "00000000_0000-0000-0000-000000000001",
        ] {
            assert_eq!(decode(&batch(&[id], b"", b"g", b"a")), Err("invalid_uuid"));
        }
    }

    #[test]
    fn empty_archives_invalid_names_and_bounds_refuse() {
        assert_eq!(
            decode(&batch(&[FIRST], b"", b"", b"a")),
            Err("observation_truncated")
        );
        assert_eq!(
            decode(&batch(&[FIRST], b"padding", b"g", b"")),
            Err("observation_empty_archive")
        );
        assert_eq!(
            decode(&batch(&[FIRST], b"\xff", b"g", b"a")),
            Err("observation_name_utf8")
        );
        assert_eq!(
            decode(&batch(&[FIRST], b"x\0y", b"g", b"a")),
            Err("observation_name_nul")
        );
        assert_eq!(
            decode(&batch(&[FIRST], &vec![b'x'; MAX_NAME + 1], b"g", b"a")),
            Err("observation_field_limit")
        );
        let mut oversized = batch(&[FIRST], b"", b"g", b"a");
        oversized[52..56].copy_from_slice(&((MAX_ARCHIVE + 1) as u32).to_le_bytes());
        assert_eq!(decode(&oversized), Err("observation_field_limit"));
        oversized[8..12].copy_from_slice(&((MAX_ROWS + 1) as u32).to_le_bytes());
        assert_eq!(decode(&oversized), Err("observation_row_limit"));
        assert_eq!(
            decode(&vec![0; MAX_BATCH + 1]),
            Err("observation_batch_limit")
        );
    }
}

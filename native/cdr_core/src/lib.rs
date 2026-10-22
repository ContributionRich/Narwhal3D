mod reader;

use flate2::{read::DeflateDecoder, write::DeflateEncoder, write::ZlibEncoder, Compression};
use serde::Deserialize;
use std::{
    collections::{HashMap, HashSet},
    io::{self, Read, Write},
    slice,
};

const ABI_VERSION: u32 = 3;
const CDR_VERSION: u16 = 1500;
const UNITS_PER_MM: f64 = 10_000.0;
const HAIRLINE_MM: f64 = 0.0762;
const MAX_INPUT_BYTES: usize = 64 * 1024 * 1024;
const MAX_OUTPUT_BYTES: usize = 512 * 1024 * 1024;
const MAX_PATH_NODES: usize = 10_000_000;
const MAX_METADATA_BYTES: usize = 4 * 1024 * 1024;

struct LimitedOutput {
    bytes: Vec<u8>,
    limit: usize,
}

impl io::Write for LimitedOutput {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        let end = self
            .bytes
            .len()
            .checked_add(buffer.len())
            .ok_or_else(|| io::Error::other("CDR JSON size overflow"))?;
        if end > self.limit {
            return Err(io::Error::other("CDR JSON exceeds the output limit"));
        }
        if end > self.bytes.capacity() {
            let doubled = self.bytes.capacity().saturating_mul(2).min(self.limit);
            let target = end.max(doubled);
            self.bytes
                .try_reserve_exact(target - self.bytes.len())
                .map_err(|_| io::Error::other("cannot reserve CDR JSON output"))?;
        }
        self.bytes.extend_from_slice(buffer);
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[derive(Deserialize)]
struct Request {
    page_w_mm: f64,
    page_h_mm: f64,
    hairline: bool,
    #[serde(default)]
    title: String,
    strokes: Vec<Stroke>,
    layers: Vec<Layer>,
}

#[derive(Deserialize)]
struct Stroke {
    beziers: Vec<[[f64; 2]; 4]>,
    closed: bool,
    rgb: [f64; 3],
    width_mm: f64,
    layer: String,
}

#[derive(Deserialize)]
struct Layer {
    name: String,
    guid_le: [u8; 16],
}

type Bounds = (i32, i32, i32, i32);
type EncodedPoint = (i32, i32);
type PathNodes = (Vec<EncodedPoint>, Vec<u8>);

fn push_u16(out: &mut Vec<u8>, value: u16) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn push_u32(out: &mut Vec<u8>, value: u32) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn push_i32(out: &mut Vec<u8>, value: i32) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn chunk(tag: &[u8; 4], payload: &[u8]) -> Result<Vec<u8>, String> {
    let length = u32::try_from(payload.len()).map_err(|_| "CDR chunk exceeds u32")?;
    let mut out = Vec::with_capacity(8 + payload.len() + (payload.len() & 1));
    out.extend_from_slice(tag);
    push_u32(&mut out, length);
    out.extend_from_slice(payload);
    if payload.len() & 1 != 0 {
        out.push(0);
    }
    Ok(out)
}

fn list(kind: &[u8; 4], children: &[Vec<u8>]) -> Result<Vec<u8>, String> {
    let size = children
        .iter()
        .try_fold(4usize, |total, child| total.checked_add(child.len()))
        .ok_or("CDR list size overflow")?;
    let mut payload = Vec::with_capacity(size);
    payload.extend_from_slice(kind);
    for child in children {
        payload.extend_from_slice(child);
    }
    chunk(b"LIST", &payload)
}

fn index_record_lengths(
    data: &mut [u8],
    start: usize,
    end: usize,
    lengths: &mut Vec<u32>,
    depth: usize,
) -> Result<(), String> {
    if depth > 256 {
        return Err("CDR record nesting exceeds the compression limit".to_owned());
    }
    let mut pos = start;
    while pos < end {
        if pos.checked_add(8).ok_or("CDR record offset overflow")? > end {
            return Err("truncated CDR record stream".to_owned());
        }
        let length = u32::from_le_bytes(
            data[pos + 4..pos + 8]
                .try_into()
                .map_err(|_| "invalid CDR record length")?,
        );
        let length_usize = usize::try_from(length).map_err(|_| "CDR record length overflow")?;
        let stop = pos
            .checked_add(8)
            .and_then(|value| value.checked_add(length_usize))
            .ok_or("CDR record length overflow")?;
        if stop > end {
            return Err("CDR record exceeds its parent".to_owned());
        }
        let index = u32::try_from(lengths.len()).map_err(|_| "too many CDR records")?;
        lengths.push(length);
        data[pos + 4..pos + 8].copy_from_slice(&index.to_le_bytes());
        let is_container =
            (&data[pos..pos + 4] == b"LIST" || &data[pos..pos + 4] == b"RIFF") && length_usize >= 4;
        if is_container {
            index_record_lengths(data, pos + 12, stop, lengths, depth + 1)?;
        }
        pos = stop
            .checked_add(length_usize & 1)
            .ok_or("CDR padding overflow")?;
        if pos > end {
            return Err("CDR record padding exceeds its parent".to_owned());
        }
    }
    Ok(())
}

fn zlib(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::best());
    encoder
        .write_all(data)
        .map_err(|error| format!("cannot compress CDR stream: {error}"))?;
    encoder
        .finish()
        .map_err(|error| format!("cannot finish CDR stream: {error}"))
}

fn compressed_list(records: &[u8]) -> Result<Vec<u8>, String> {
    let mut indexed = records.to_vec();
    let mut lengths = Vec::new();
    index_record_lengths(&mut indexed, 0, records.len(), &mut lengths, 0)?;
    let mut length_bytes = Vec::with_capacity(lengths.len() * 4);
    for length in lengths {
        push_u32(&mut length_bytes, length);
    }
    let record_z = zlib(&indexed)?;
    let length_z = zlib(&length_bytes)?;
    let mut payload = Vec::with_capacity(36 + record_z.len() + length_z.len());
    payload.extend_from_slice(b"cmpr");
    push_u32(
        &mut payload,
        u32::try_from(
            record_z
                .len()
                .checked_add(8)
                .ok_or("compressed size overflow")?,
        )
        .map_err(|_| "compressed CDR stream exceeds u32")?,
    );
    push_u32(
        &mut payload,
        u32::try_from(indexed.len()).map_err(|_| "expanded CDR stream exceeds u32")?,
    );
    push_u32(
        &mut payload,
        u32::try_from(
            length_z
                .len()
                .checked_add(8)
                .ok_or("length size overflow")?,
        )
        .map_err(|_| "compressed CDR length table exceeds u32")?,
    );
    push_u32(
        &mut payload,
        u32::try_from(length_bytes.len()).map_err(|_| "CDR length table exceeds u32")?,
    );
    payload.extend_from_slice(b"CPng\x01\x00\x04\x00");
    payload.extend_from_slice(&record_z);
    payload.extend_from_slice(b"CPng\x01\x00\x04\x00");
    payload.extend_from_slice(&length_z);
    chunk(b"LIST", &payload)
}

fn coord(value: f64) -> Result<i32, String> {
    if !value.is_finite() {
        return Err("CDR coordinate is not finite".to_owned());
    }
    let scaled = (value * UNITS_PER_MM).round_ties_even();
    if scaled < i32::MIN as f64 || scaled > i32::MAX as f64 {
        return Err("CDR v15 coordinate exceeds signed 32-bit range".to_owned());
    }
    Ok(scaled as i32)
}

fn rgb8(rgb: [f64; 3]) -> Result<[u8; 3], String> {
    let mut out = [0u8; 3];
    for (slot, value) in out.iter_mut().zip(rgb) {
        if !value.is_finite() {
            return Err("CDR outline color is not finite".to_owned());
        }
        *slot = (value * 255.0).round_ties_even().clamp(0.0, 255.0) as u8;
    }
    Ok(out)
}

fn write_at(target: &mut [u8], offset: usize, value: &[u8]) -> Result<(), String> {
    let end = offset
        .checked_add(value.len())
        .ok_or("CDR field offset overflow")?;
    target
        .get_mut(offset..end)
        .ok_or_else(|| "CDR field exceeds its record".to_owned())?
        .copy_from_slice(value);
    Ok(())
}

fn outline_payload(id: u32, rgb: [u8; 3], width_mm: f64, line_type: u8) -> Result<Vec<u8>, String> {
    // MEASURED v15 optional-field layout. Corel requires these four field blocks;
    // libcdr only needs block 1, which is why the former short record looked valid
    // outside CorelDRAW but failed there.
    let mut payload = vec![0u8; 269];
    for (offset, value) in [
        (4, &[0x05][..]),
        (8, &[0x30][..]),
        (60, &[0x03][..]),
        (64, &[0x04][..]),
        (68, &[0x40, 0x4b, 0x4c][..]),
        (72, &[0x02][..]),
        (76, &[0x2b][..]),
        (80, &[0x01, 0x0c][..]),
        (85, &[0x05][..]),
        (87, &[0x05][..]),
        (97, &[0x07, 0x10][..]),
        (123, &[0x01][..]),
        (127, &[0x82][..]),
        (131, &[0x02][..]),
        (141, &[0x64][..]),
        (153, &[0xf0, 0x3f][..]),
        (185, &[0xf0, 0x3f][..]),
        (195, &[0x05][..]),
        (197, &[0x05][..]),
        (215, &[0x40, 0xa5, 0xae, 0x02, 0x3c][..]),
    ] {
        write_at(&mut payload, offset, value)?;
    }
    write_at(&mut payload, 0, &id.to_le_bytes())?;
    payload[131] = line_type;
    write_at(&mut payload, 93, &[rgb[2], rgb[1], rgb[0]])?;
    write_at(&mut payload, 137, &coord(width_mm)?.to_le_bytes())?;
    let packed = (u32::from(rgb[0]) << 16) | (u32::from(rgb[1]) << 8) | u32::from(rgb[2]);
    write_at(&mut payload, 203, &packed.to_le_bytes())?;
    chunk(b"outl", &payload)
}

fn outline(id: u32, rgb: [u8; 3], width_mm: f64) -> Result<Vec<u8>, String> {
    if !width_mm.is_finite() || width_mm <= 0.0 {
        return Err("CDR outline width must be positive".to_owned());
    }
    outline_payload(id, rgb, width_mm, 2)
}

fn no_outline(id: u32) -> Result<Vec<u8>, String> {
    outline_payload(id, [0, 0, 0], 0.0, 1)
}

fn point(value: [f64; 2], page_w: f64, page_h: f64) -> Result<(i32, i32), String> {
    Ok((
        coord(value[0] - page_w / 2.0)?,
        coord(value[1] - page_h / 2.0)?,
    ))
}

fn path_nodes(stroke: &Stroke, page_w: f64, page_h: f64) -> Result<PathNodes, String> {
    let Some(first) = stroke.beziers.first() else {
        return Ok((Vec::new(), Vec::new()));
    };
    let count = 1usize
        .checked_add(
            stroke
                .beziers
                .len()
                .checked_mul(3)
                .ok_or("path node overflow")?,
        )
        .ok_or("path node overflow")?;
    if count > u16::MAX as usize {
        return Err("one CDR curve exceeds the v15 limit of 65535 path nodes".to_owned());
    }
    let mut points = Vec::with_capacity(count);
    let mut types = Vec::with_capacity(count);
    points.push(point(first[0], page_w, page_h)?);
    types.push(0x00);
    for (index, bezier) in stroke.beziers.iter().enumerate() {
        let p0 = point(bezier[0], page_w, page_h)?;
        if index > 0 && points.last().copied() != Some(p0) {
            return Err("CDR Bezier segments must form one continuous stroke".to_owned());
        }
        points.push(point(bezier[1], page_w, page_h)?);
        points.push(point(bezier[2], page_w, page_h)?);
        points.push(point(bezier[3], page_w, page_h)?);
        types.extend_from_slice(&[0xc0, 0xc0, 0x80]);
    }
    if stroke.closed {
        if let Some(first) = types.first_mut() {
            *first = 0x0c; // move + closed-subpath + modifiable node
        }
        for index in (3..types.len()).step_by(3) {
            types[index] |= 0x04; // modifiable cubic endpoint
        }
        if let Some(last) = types.last_mut() {
            *last = (*last & !0x04) | 0x08; // closing endpoint is not modifiable
        }
    }
    Ok((points, types))
}

fn loda(kind: u32, arguments: &[(u32, Vec<u8>)]) -> Result<Vec<u8>, String> {
    let count = u32::try_from(arguments.len()).map_err(|_| "too many loda arguments")?;
    let table_bytes = arguments
        .len()
        .checked_mul(8)
        .ok_or("loda table overflow")?;
    // The offset table has count + 1 entries. Its last value is the end of
    // the final argument; treating it as padding makes Corel reject the object.
    let mut cursor = 24usize
        .checked_add(table_bytes)
        .ok_or("loda size overflow")?;
    let mut offsets = Vec::with_capacity(arguments.len());
    for (_, data) in arguments {
        offsets.push(u32::try_from(cursor).map_err(|_| "loda offset overflow")?);
        cursor = cursor.checked_add(data.len()).ok_or("loda data overflow")?;
    }
    let mut payload = Vec::with_capacity(cursor);
    push_u32(
        &mut payload,
        u32::try_from(cursor).map_err(|_| "loda payload overflow")?,
    );
    push_u32(&mut payload, count);
    push_u32(&mut payload, 20);
    push_u32(&mut payload, 24 + count * 4);
    push_u32(&mut payload, kind);
    for offset in offsets {
        push_u32(&mut payload, offset);
    }
    push_u32(
        &mut payload,
        u32::try_from(cursor).map_err(|_| "loda end offset overflow")?,
    );
    for (argument_kind, _) in arguments.iter().rev() {
        push_u32(&mut payload, *argument_kind);
    }
    for (_, data) in arguments {
        payload.extend_from_slice(data);
    }
    chunk(b"loda", &payload)
}

fn loda_path(
    stroke: &Stroke,
    object_id: u32,
    fill_id: u32,
    outline_id: u32,
    page_w: f64,
    page_h: f64,
) -> Result<Vec<u8>, String> {
    let (points, types) = path_nodes(stroke, page_w, page_h)?;
    let mut shape = Vec::with_capacity(4 + points.len() * 8 + types.len());
    push_u16(
        &mut shape,
        u16::try_from(points.len()).map_err(|_| "too many path nodes")?,
    );
    push_u16(&mut shape, 0);
    for (x, y) in points {
        push_i32(&mut shape, x);
        push_i32(&mut shape, y);
    }
    shape.extend_from_slice(&types);
    // CDR v15 curves carry this fixed trailer. Corel drops or rejects curves
    // without it even though permissive readers stop after the node bytes.
    shape.extend_from_slice(&[0; 8]);
    let scalar = |value: u32| value.to_le_bytes().to_vec();
    loda(
        3,
        &[
            (0x2ee0, scalar(0)),
            (0x00c8, scalar(5)),
            (0x0064, scalar(object_id)),
            (0x001e, shape),
            (0x0014, scalar(fill_id)),
            (0x000a, scalar(outline_id)),
        ],
    )
}

fn identity_fill_transform() -> Result<Vec<u8>, String> {
    let mut payload = Vec::with_capacity(48);
    for value in [1.0f64, 0.0, 0.0, 0.0, 1.0, 0.0] {
        payload.extend_from_slice(&value.to_le_bytes());
    }
    chunk(b"ftil", &payload)
}

fn identity_object_transform() -> Result<Vec<u8>, String> {
    let mut payload = vec![0u8; 96];
    for (offset, value) in [
        (0, 96u32),
        (4, 1),
        (8, 16),
        (12, 0x0000_ffff),
        (16, 24),
        (20, 96),
        (24, 0x514),
        (32, 8),
    ] {
        write_at(&mut payload, offset, &value.to_le_bytes())?;
    }
    write_at(&mut payload, 40, &1.0f64.to_le_bytes())?;
    write_at(&mut payload, 72, &1.0f64.to_le_bytes())?;
    chunk(b"trfd", &payload)
}

fn layer_metadata(role: u8) -> Vec<u8> {
    let mut value = vec![0u8; 43];
    value[0] = 1;
    value[1] = 12;
    value[5] = if role == 1 { 2 } else { 5 };
    value[7] = 5;
    match role {
        0 => value[13..17].copy_from_slice(&[0, 0xff, 0, 0]),
        1 => value[16] = 100,
        2 => value[13..17].copy_from_slice(&[0xc1, 0xc2, 0xc2, 0]),
        _ => {}
    }
    value[17..20].copy_from_slice(&[7, 16, 0]);
    value
}

fn layer_descriptor(
    name: Option<&str>,
    guid_le: [u8; 16],
    role: u8,
    descriptor_kind: u32,
) -> Result<Vec<u8>, String> {
    let mut arguments = vec![(0x9c72, guid_le.to_vec()), (0x07d0, layer_metadata(role))];
    if let Some(name) = name {
        let mut encoded = Vec::new();
        for unit in name.encode_utf16() {
            push_u16(&mut encoded, unit);
        }
        push_u16(&mut encoded, 0);
        arguments.push((0x03e8, encoded));
    }
    loda(descriptor_kind, &arguments)
}

fn layer_chunk(
    list_flags: u32,
    name: Option<&str>,
    guid_le: [u8; 16],
    role: u8,
    descriptor_kind: u32,
    objects: &[Vec<u8>],
) -> Result<Vec<u8>, String> {
    let mut children = vec![
        chunk(b"flgs", &list_flags.to_le_bytes())?,
        list(
            b"lgob",
            &[layer_descriptor(name, guid_le, role, descriptor_kind)?],
        )?,
    ];
    children.extend_from_slice(objects);
    list(b"layr", &children)
}

fn bounds(stroke: &Stroke, page_w: f64, page_h: f64) -> Result<Bounds, String> {
    let (points, _) = path_nodes(stroke, page_w, page_h)?;
    if points.is_empty() {
        return Ok((0, 0, 0, 0));
    }
    // CDR boxes are left, top, right, bottom (not min-x/min-y/max-x/max-y).
    let (mut left, mut top, mut right, mut bottom) = (i32::MAX, i32::MIN, i32::MIN, i32::MAX);
    for (x, y) in points {
        left = left.min(x);
        top = top.max(y);
        right = right.max(x);
        bottom = bottom.min(y);
    }
    Ok((left, top, right, bottom))
}

fn bbox(value: Bounds) -> Result<Vec<u8>, String> {
    let mut payload = Vec::with_capacity(16);
    push_i32(&mut payload, value.0);
    push_i32(&mut payload, value.1);
    push_i32(&mut payload, value.2);
    push_i32(&mut payload, value.3);
    chunk(b"bbox", &payload)
}

fn expanded_bounds(value: Bounds, amount: i32) -> Result<Bounds, String> {
    Ok((
        value
            .0
            .checked_sub(amount)
            .ok_or("CDR outline bounds overflow")?,
        value
            .1
            .checked_add(amount)
            .ok_or("CDR outline bounds overflow")?,
        value
            .2
            .checked_add(amount)
            .ok_or("CDR outline bounds overflow")?,
        value
            .3
            .checked_sub(amount)
            .ok_or("CDR outline bounds overflow")?,
    ))
}

fn union(values: &[Bounds]) -> Bounds {
    if values.is_empty() {
        return (0, 0, 0, 0);
    }
    values
        .iter()
        .copied()
        .fold((i32::MAX, i32::MIN, i32::MIN, i32::MAX), |out, value| {
            (
                out.0.min(value.0),
                out.1.max(value.1),
                out.2.max(value.2),
                out.3.min(value.3),
            )
        })
}

fn obbx(first: Bounds, second: Bounds) -> Result<Vec<u8>, String> {
    let mut payload = Vec::with_capacity(32);
    for value in [
        first.0, first.1, first.2, first.3, second.0, second.1, second.2, second.3,
    ] {
        push_i32(&mut payload, value);
    }
    chunk(b"obbx", &payload)
}

fn model_config(page_w_mm: f64, page_h_mm: f64) -> Result<Vec<u8>, String> {
    // MEASURED CDR v15 default configuration. Only 201 of the fixed 8,323 bytes
    // are nonzero; dimensions at 12/16 are authored dynamically. The sparse
    // constructor avoids embedding a copied document or opaque compressed blob.
    let mut payload = vec![0u8; 8323];
    for (offset, value) in [
        (0, &[0x14, 0x05][..]),
        (4, &[0x7b, 0x20]),
        (22, &[0x01]),
        (24, &[0x01]),
        (34, &[0x01]),
        (38, &[0x01]),
        (42, &[0x06, 0x7c]),
        (670, &[0xe0, 0x5e, 0xf8, 0xff, 0x80, 0xe5, 0xf9, 0xff]),
        (684, &[0x10, 0x40]),
        (692, &[0x10, 0x40, 0x02]),
        (702, &[0xf0, 0x3f, 0x02]),
        (706, &[0x02]),
        (708, &[0x01]),
        (716, &[0xd0, 0x38, 0xb4]),
        (720, &[0xd0, 0x38, 0xb4]),
        (724, &[0x01]),
        (728, &[0x01]),
        (732, &[0x01]),
        (736, &[0xd0, 0x38, 0xb4]),
        (740, &[0xd0, 0x38, 0xb4]),
        (744, &[0x01]),
        (760, &[0x01, 0x0c]),
        (767, &[0x05]),
        (777, &[0x07, 0x10]),
        (811, &[0x01]),
        (815, &[0x01]),
        (819, &[0x01]),
        (827, &[0x01]),
        (831, &[0x50, 0xc3]),
        (835, &[0x50, 0xc3]),
        (839, &[0xe8, 0x03]),
        (843, &[0x02]),
        (847, &[0x02]),
        (851, &[0x01]),
        (859, &[0x02]),
        (861, &[0x9c, 0x31]),
        (869, &[0xe8, 0x03]),
        (873, &[0xe8, 0x03]),
        (877, &[0x01]),
        (881, &[0x01]),
        (885, &[0x01]),
        (893, &[0x01]),
        (909, &[0x01, 0x0c]),
        (914, &[0x05]),
        (916, &[0x05]),
        (922, &[0xc4]),
        (926, &[0x07, 0x10]),
        (952, &[0x01, 0x0c]),
        (957, &[0x05]),
        (959, &[0x05]),
        (965, &[0x20]),
        (967, &[0xc4]),
        (969, &[0x07, 0x10]),
        (995, &[0x01, 0x0c]),
        (1000, &[0x05]),
        (1002, &[0x05]),
        (1008, &[0xc4]),
        (1010, &[0xc4]),
        (1012, &[0x07, 0x10]),
        (1192, &[0x03]),
        (1196, &[0x01]),
        (1200, &[0x01]),
        (1204, &[0x01]),
        (1212, &[0x01]),
        (1236, &[0x02]),
        (1244, &[0x04]),
        (1248, &[0x64]),
        (1260, &[0x01]),
        (1264, &[0x01, 0x0c]),
        (1269, &[0x05]),
        (1271, &[0x05]),
        (1277, &[0xff]),
        (1281, &[0x07, 0x10]),
        (1307, &[0x01, 0x0c]),
        (1312, &[0x05]),
        (1314, &[0x05]),
        (1321, &[0x64]),
        (1324, &[0x07, 0x10]),
        (1350, &[0x01]),
        (1386, &[0x01]),
        (1404, &[0xf0, 0x3f]),
        (1412, &[0xf0, 0x3f]),
        (1420, &[0xf0, 0x3f]),
        (1428, &[0xf0, 0x3f, 0x02]),
        (
            1434,
            &[0x9a, 0x99, 0x99, 0x99, 0x99, 0x99, 0xc9, 0x3f, 0x01],
        ),
        (1452, &[0x10, 0x40]),
        (1460, &[0x10, 0x40, 0x04, 0x01]),
        (1479, &[0x01]),
        (7745, &[0x28, 0x40, 0x01]),
        (7751, &[0x01]),
        (7755, &[0x01]),
        (
            7759,
            &[
                0x54, 0xe3, 0xa5, 0x9b, 0xc4, 0x20, 0xea, 0x3f, 0x54, 0xe3, 0xa5, 0x9b, 0xc4, 0x20,
                0xea, 0x3f, 0x54, 0xe3, 0xa5, 0x9b, 0xc4, 0x20, 0xea, 0x3f, 0x80, 0x66, 0xab, 0x13,
                0x80, 0x4a, 0x5d, 0x05, 0x80, 0x58, 0x84, 0x0c, 0x49,
            ],
        ),
        (7797, &[0x73]),
        (7799, &[0x6f]),
        (7801, &[0x6d]),
        (7803, &[0x65]),
        (7805, &[0x74]),
        (7807, &[0x72]),
        (7809, &[0x69]),
        (7811, &[0x63]),
    ] {
        write_at(&mut payload, offset, value)?;
    }
    write_at(&mut payload, 12, &coord(page_w_mm)?.to_le_bytes())?;
    write_at(&mut payload, 16, &coord(page_h_mm)?.to_le_bytes())?;
    chunk(b"mcfg", &payload)
}

fn no_fill() -> Result<Vec<u8>, String> {
    let mut payload = vec![0u8; 22];
    write_at(&mut payload, 0, &1u32.to_le_bytes())?;
    write_at(&mut payload, 4, &0x514u32.to_le_bytes())?;
    write_at(&mut payload, 8, &10u32.to_le_bytes())?;
    chunk(b"fild", &payload)
}

fn solid_black_fill() -> Result<Vec<u8>, String> {
    // Version 15 uses a leading 2400 identity record, then the 1300 uniform-fill
    // property list. The older 97-byte body is parseable but Corel does not draw it.
    let mut payload = vec![0u8; 121];
    write_at(&mut payload, 0, &2u32.to_le_bytes())?;
    write_at(&mut payload, 4, &2400u32.to_le_bytes())?;
    write_at(&mut payload, 8, &16u32.to_le_bytes())?;
    write_at(
        &mut payload,
        12,
        &[
            0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x45, 0x95, 0x55, 0x55, 0x55, 0x55, 0x55,
            0x55, 0x55,
        ],
    )?;
    write_at(&mut payload, 28, &1300u32.to_le_bytes())?;
    write_at(&mut payload, 32, &77u32.to_le_bytes())?;
    write_at(&mut payload, 36, &1u16.to_le_bytes())?;
    write_at(&mut payload, 38, &1300u32.to_le_bytes())?;
    write_at(&mut payload, 42, &59u32.to_le_bytes())?;
    payload[46] = 1;
    write_at(&mut payload, 47, &12u32.to_le_bytes())?;
    write_at(&mut payload, 51, &5u16.to_le_bytes())?;
    write_at(&mut payload, 53, &5u16.to_le_bytes())?;
    // bytes 55..62 are reserved + RGB value; zero is exact #000000.
    payload[63] = 7;
    write_at(&mut payload, 64, &16u32.to_le_bytes())?;
    payload[91] = 4;
    write_at(&mut payload, 97, &[0x40, 0xa5, 0xae, 0x02, 0x3c])?;
    chunk(b"fild", &payload)
}

fn master_page(
    guide_guid: [u8; 16],
    desktop_guid: [u8; 16],
    grid_guid: [u8; 16],
) -> Result<Vec<u8>, String> {
    let layers = vec![
        layer_chunk(0x9801_000a, Some("Guides"), guide_guid, 0, 12, &[])?,
        layer_chunk(0x9801_0008, Some("Desktop"), desktop_guid, 1, 17, &[])?,
        layer_chunk(0x9801_011a, Some("Document Grid"), grid_guid, 2, 11, &[])?,
    ];
    let bounds = (-2, 2, 2, -2);
    list(
        b"page",
        &[
            chunk(b"flgs", &0x9001_0040u32.to_le_bytes())?,
            bbox(bounds)?,
            obbx(bounds, bounds)?,
            list(b"gobj", &layers)?,
        ],
    )
}

fn is_registration_fill(stroke: &Stroke) -> Result<bool, String> {
    Ok(stroke.closed
        && stroke.layer.eq_ignore_ascii_case("Register")
        && rgb8(stroke.rgb)? == [0, 0, 0])
}

fn width_key(width: f64) -> Result<i64, String> {
    if !width.is_finite() || width <= 0.0 {
        return Err("CDR outline width must be positive".to_owned());
    }
    Ok((width * 1_000_000_000.0).round_ties_even() as i64)
}

fn validate_request(request: &Request) -> Result<(), String> {
    let mut layer_names = HashSet::with_capacity(request.layers.len());
    for layer in &request.layers {
        if !layer_names.insert(layer.name.as_str()) {
            return Err("CDR request contains duplicate layer descriptors".to_owned());
        }
    }
    let mut nodes = 0usize;
    for stroke in request
        .strokes
        .iter()
        .filter(|stroke| !stroke.beziers.is_empty())
    {
        if !layer_names.contains(stroke.layer.as_str()) {
            return Err("CDR stroke references an absent layer".to_owned());
        }
        nodes = nodes
            .checked_add(1 + stroke.beziers.len().checked_mul(3).ok_or("node overflow")?)
            .ok_or("node overflow")?;
        if nodes > MAX_PATH_NODES {
            return Err("CDR request has too many path nodes".to_owned());
        }
    }
    let estimated = nodes
        .checked_mul(16)
        .and_then(|value| value.checked_add(request.strokes.len().checked_mul(1024)?))
        .and_then(|value| value.checked_add(request.layers.len().checked_mul(1024)?))
        .ok_or("CDR estimated output overflow")?;
    if estimated > MAX_OUTPUT_BYTES {
        return Err("CDR output exceeds the native limit".to_owned());
    }
    Ok(())
}

fn encode(request: &Request) -> Result<Vec<u8>, String> {
    if !request.page_w_mm.is_finite()
        || !request.page_h_mm.is_finite()
        || request.page_w_mm <= 0.0
        || request.page_h_mm <= 0.0
    {
        return Err("CDR page dimensions must be positive".to_owned());
    }
    if request.strokes.is_empty()
        || request
            .strokes
            .iter()
            .all(|stroke| stroke.beziers.is_empty())
    {
        return Err("a CDR drawing needs at least one non-empty stroke".to_owned());
    }
    validate_request(request)?;
    let mut styles: Vec<([u8; 3], i64, f64)> = Vec::new();
    for stroke in request
        .strokes
        .iter()
        .filter(|stroke| !stroke.beziers.is_empty())
    {
        if is_registration_fill(stroke)? {
            continue;
        }
        let rgb = rgb8(stroke.rgb)?;
        let width = if request.hairline {
            HAIRLINE_MM
        } else {
            stroke.width_mm
        };
        let width_key = width_key(width)?;
        if !styles.iter().any(|(r, w, _)| *r == rgb && *w == width_key) {
            styles.push((rgb, width_key, width));
        }
    }
    let mut outline_chunks = Vec::new();
    for (index, (rgb, _, width)) in styles.iter().enumerate() {
        outline_chunks.push(outline(
            u32::try_from(index + 1).map_err(|_| "too many outline styles")?,
            *rgb,
            *width,
        )?);
    }

    let no_outline_id =
        u32::try_from(outline_chunks.len() + 1).map_err(|_| "too many outline styles")?;
    outline_chunks.push(no_outline(no_outline_id)?);

    let mut strokes_by_layer: HashMap<&str, Vec<&Stroke>> = request
        .layers
        .iter()
        .map(|layer| (layer.name.as_str(), Vec::new()))
        .collect();
    for stroke in request
        .strokes
        .iter()
        .filter(|stroke| !stroke.beziers.is_empty())
    {
        strokes_by_layer
            .get_mut(stroke.layer.as_str())
            .ok_or("CDR stroke references an absent layer")?
            .push(stroke);
    }
    // Deterministic valid UUID-v4 byte layouts. In little-endian UUID storage,
    // byte 7 carries the version nibble and byte 8 the RFC 4122 variant.
    let guide_guid = [
        0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x41, 0x91, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
        0x11,
    ];
    let desktop_guid = [
        0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x42, 0xa2, 0x22, 0x22, 0x22, 0x22, 0x22, 0x22,
        0x22,
    ];
    let grid_guid = [
        0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x43, 0xb3, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33,
        0x33,
    ];
    let local_guide_guid = [
        0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x84, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44,
        0x44,
    ];
    let mut user_layers = Vec::new();
    let mut all_bounds = Vec::new();
    let mut object_id = 0u32;
    for layer in &request.layers {
        let mut objects = Vec::new();
        for stroke in &strokes_by_layer[layer.name.as_str()] {
            let stroke = *stroke;
            let value = bounds(stroke, request.page_w_mm, request.page_h_mm)?;
            all_bounds.push(value);
            let registration = is_registration_fill(stroke)?;
            let (fill_id, outline_id, outline_width) = if registration {
                (2, no_outline_id, 0.0)
            } else {
                let rgb = rgb8(stroke.rgb)?;
                let width = if request.hairline {
                    HAIRLINE_MM
                } else {
                    stroke.width_mm
                };
                let width_key = width_key(width)?;
                let style = styles
                    .iter()
                    .position(|(r, w, _)| *r == rgb && *w == width_key)
                    .ok_or("missing outline style")?;
                (
                    1,
                    u32::try_from(style + 1).map_err(|_| "too many outline styles")?,
                    width,
                )
            };
            object_id = object_id.checked_add(1).ok_or("too many CDR objects")?;
            let object = list(
                b"obj ",
                &[
                    chunk(b"flgs", &0x0800_0200u32.to_le_bytes())?,
                    bbox(value)?,
                    obbx(value, expanded_bounds(value, coord(outline_width / 2.0)?)?)?,
                    chunk(b"usdn", &object_id.to_le_bytes())?,
                    list(
                        b"lgob",
                        &[
                            loda_path(
                                stroke,
                                object_id,
                                fill_id,
                                outline_id,
                                request.page_w_mm,
                                request.page_h_mm,
                            )?,
                            identity_fill_transform()?,
                            list(b"trfl", &[identity_object_transform()?])?,
                        ],
                    )?,
                ],
            )?;
            objects.push(object);
        }
        user_layers.push(layer_chunk(
            0x9800_0000,
            Some(&layer.name),
            layer.guid_le,
            1,
            0,
            &objects,
        )?);
    }

    let page_bounds = union(&all_bounds);
    let mut page_layers = vec![
        layer_chunk(0x9801_000a, None, guide_guid, 0, 12, &[])?,
        layer_chunk(0x9801_0008, None, desktop_guid, 1, 17, &[])?,
        layer_chunk(0x9800_000a, Some("Guides"), local_guide_guid, 0, 12, &[])?,
    ];
    page_layers.extend(user_layers);
    page_layers.push(layer_chunk(0x9801_011a, None, grid_guid, 2, 11, &[])?);
    let page = list(
        b"page",
        &[
            chunk(b"flgs", &0x9000_0040u32.to_le_bytes())?,
            bbox(page_bounds)?,
            obbx(page_bounds, page_bounds)?,
            list(b"gobj", &page_layers)?,
        ],
    )?;
    let doc = list(
        b"doc ",
        &[
            chunk(b"pfrd", &[0, 0])?,
            model_config(request.page_w_mm, request.page_h_mm)?,
            chunk(
                b"ptrt",
                &[
                    page_bounds.0.to_le_bytes(),
                    page_bounds.1.to_le_bytes(),
                    page_bounds.2.to_le_bytes(),
                    page_bounds.3.to_le_bytes(),
                ]
                .concat(),
            )?,
            list(
                b"filt",
                &[
                    list(b"filc", &[no_fill()?])?,
                    list(b"filc", &[solid_black_fill()?])?,
                ],
            )?,
            list(b"otlt", &outline_chunks)?,
        ],
    )?;
    let master = master_page(guide_guid, desktop_guid, grid_guid)?;
    let mut body = Vec::new();
    body.extend_from_slice(b"CDRF");
    body.extend_from_slice(&chunk(b"vrsn", &CDR_VERSION.to_le_bytes())?);
    body.extend_from_slice(&compressed_list(&[])?);
    let mut records = Vec::with_capacity(doc.len() + master.len() + page.len());
    records.extend_from_slice(&doc);
    records.extend_from_slice(&master);
    records.extend_from_slice(&page);
    body.extend_from_slice(&compressed_list(&records)?);
    let mut riff = Vec::new();
    riff.extend_from_slice(b"RIFF");
    push_u32(
        &mut riff,
        u32::try_from(body.len()).map_err(|_| "RIFF too large")?,
    );
    riff.extend_from_slice(&body);
    Ok(riff)
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn inch_text(mm: f64) -> String {
    let mut value = format!("{:.6}", mm / 25.4);
    while value.contains('.') && value.ends_with('0') {
        value.pop();
    }
    if value.ends_with('.') {
        value.pop();
    }
    value
}

fn textinfo(request: &Request) -> Vec<u8> {
    let items = request
        .layers
        .iter()
        .map(|layer| format!("<rdf:li>{}</rdf:li>", xml_escape(&layer.name)))
        .collect::<String>();
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>\n\
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\
<rdf:Description rdf:about=\"\" xmlns=\"http://namespace.corel.com/Cdr/metadata\">\
<ObjectNames><rdf:Bag /></ObjectNames><LayerNames><rdf:Bag>{items}</rdf:Bag></LayerNames>\
<PageNames><rdf:Bag /></PageNames></rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    .into_bytes()
}

fn metadata(request: &Request) -> Result<Vec<u8>, String> {
    let width = coord(request.page_w_mm)?;
    let height = coord(request.page_h_mm)?;
    let strokes: Vec<&Stroke> = request
        .strokes
        .iter()
        .filter(|stroke| !stroke.beziers.is_empty())
        .collect();
    let closed = strokes.iter().filter(|stroke| stroke.closed).count();
    let nodes = strokes.iter().try_fold(0usize, |total, stroke| {
        total
            .checked_add(1 + 3 * stroke.beziers.len())
            .ok_or("CDR metadata node count overflow")
    })?;
    let registration_count = strokes.iter().try_fold(0usize, |count, stroke| {
        is_registration_fill(stroke).map(|registration| count + usize::from(registration))
    })?;
    let outlined = strokes.len() - registration_count;
    let hairlines = if request.hairline { outlined } else { 0 };
    let solids = if request.hairline { 0 } else { outlined };
    let orientation = if request.page_w_mm >= request.page_h_mm {
        2
    } else {
        1
    };
    Ok(format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>\n\
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\
<rdf:Description rdf:about=\"\" xmlns=\"http://namespace.corel.com/Cdr/metadata\" xmlns:crl=\"http://namespace.corel.com/zcf/metadata/1.0/\">\
<FontsUsed><rdf:Bag /></FontsUsed><CoreVersion>1500</CoreVersion><ProductName>CorelDRAW</ProductName>\
<AppVersion>1500</AppVersion><DocID>00000000-0000-4000-8000-000000000000</DocID>\
<Subject>{}</Subject><NumPages>1</NumPages><NumLayers>{}</NumLayers>\
<PageWidth>{width}</PageWidth><PageHeight>{height}</PageHeight>\
<PageDimensions>{} x {} in</PageDimensions><PageOrientation>{orientation}</PageOrientation>\
<ResolutionX>150</ResolutionX><ResolutionY>150</ResolutionY>\
<Objects><Total>{}</Total><Curve>{}</Curve></Objects>\
<CurveInfo><Subpaths>{}</Subpaths><Nodes>{nodes}</Nodes><Closed>{closed}</Closed></CurveInfo>\
<Outlines><Hairline>{hairlines}</Hairline><Solid>{solids}</Solid></Outlines>\
</rdf:Description></rdf:RDF></x:xmpmeta>",
        xml_escape(&request.title),
        request.layers.len(),
        inch_text(request.page_w_mm),
        inch_text(request.page_h_mm),
        strokes.len(),
        strokes.len(),
        strokes.len(),
    )
    .into_bytes())
}

fn preview_dimensions(page_w: f64, page_h: f64, max_w: u32, max_h: u32) -> (u32, u32) {
    let scale = (f64::from(max_w) / page_w).min(f64::from(max_h) / page_h);
    let width = (page_w * scale).round().clamp(1.0, f64::from(max_w)) as u32;
    let height = (page_h * scale).round().clamp(1.0, f64::from(max_h)) as u32;
    (width, height)
}

fn blank_bmp(width: u32, height: u32) -> Result<(Vec<u8>, usize), String> {
    let row = width
        .checked_mul(3)
        .and_then(|value| value.checked_add(3))
        .map(|value| value & !3)
        .ok_or("BMP row size overflow")?;
    let pixels = row.checked_mul(height).ok_or("BMP pixel size overflow")?;
    let file_size = 54u32.checked_add(pixels).ok_or("BMP file size overflow")?;
    let mut out = Vec::with_capacity(file_size as usize);
    out.extend_from_slice(b"BM");
    push_u32(&mut out, file_size);
    push_u32(&mut out, 0);
    push_u32(&mut out, 54);
    push_u32(&mut out, 40);
    push_i32(&mut out, width as i32);
    push_i32(&mut out, height as i32);
    push_u16(&mut out, 1);
    push_u16(&mut out, 24);
    push_u32(&mut out, 0);
    push_u32(&mut out, pixels);
    push_i32(&mut out, 5906);
    push_i32(&mut out, 5906);
    push_u32(&mut out, 0);
    push_u32(&mut out, 0);
    out.resize(file_size as usize, 0xFF);
    Ok((out, row as usize))
}

fn bezier_point(bezier: &[[f64; 2]; 4], t: f64) -> [f64; 2] {
    let s = 1.0 - t;
    let weights = [s * s * s, 3.0 * s * s * t, 3.0 * s * t * t, t * t * t];
    let mut result = [0.0, 0.0];
    for (point, weight) in bezier.iter().zip(weights) {
        result[0] += point[0] * weight;
        result[1] += point[1] * weight;
    }
    result
}

fn preview_pixel(point: [f64; 2], request: &Request, width: u32, height: u32) -> (i32, i32) {
    let x = (point[0] / request.page_w_mm * f64::from(width.saturating_sub(1)))
        .round()
        .clamp(0.0, f64::from(width.saturating_sub(1))) as i32;
    let y = (point[1] / request.page_h_mm * f64::from(height.saturating_sub(1)))
        .round()
        .clamp(0.0, f64::from(height.saturating_sub(1))) as i32;
    (x, y)
}

fn draw_bmp_line(bmp: &mut [u8], row: usize, mut start: (i32, i32), end: (i32, i32), rgb: [u8; 3]) {
    let dx = (end.0 - start.0).abs();
    let sx = if start.0 < end.0 { 1 } else { -1 };
    let dy = -(end.1 - start.1).abs();
    let sy = if start.1 < end.1 { 1 } else { -1 };
    let mut error = dx + dy;
    loop {
        let offset = 54 + start.1 as usize * row + start.0 as usize * 3;
        if offset + 2 < bmp.len() {
            bmp[offset] = rgb[2];
            bmp[offset + 1] = rgb[1];
            bmp[offset + 2] = rgb[0];
        }
        if start == end {
            break;
        }
        let twice = 2 * error;
        if twice >= dy {
            error += dy;
            start.0 += sx;
        }
        if twice <= dx {
            error += dx;
            start.1 += sy;
        }
    }
}

fn preview_bmp(request: &Request, width: u32, height: u32) -> Result<Vec<u8>, String> {
    let (mut bmp, row) = blank_bmp(width, height)?;
    let segment_count = request
        .strokes
        .iter()
        .try_fold(0usize, |total, stroke| {
            total.checked_add(stroke.beziers.len())
        })
        .ok_or("preview segment count overflow")?;
    let steps = 100_000usize
        .checked_div(segment_count)
        .unwrap_or(2)
        .clamp(2, 16);
    for stroke in &request.strokes {
        let color = rgb8(stroke.rgb)?;
        for bezier in &stroke.beziers {
            let mut previous = preview_pixel(bezier[0], request, width, height);
            for sample in 1..=steps {
                let point = bezier_point(bezier, sample as f64 / steps as f64);
                let current = preview_pixel(point, request, width, height);
                draw_bmp_line(&mut bmp, row, previous, current, color);
                previous = current;
            }
        }
    }
    Ok(bmp)
}

struct ZipEntry {
    name: &'static str,
    data: Vec<u8>,
    deflate: bool,
}

struct PreparedZipEntry {
    name: &'static str,
    compressed: Vec<u8>,
    uncompressed_len: u32,
    crc: u32,
    method: u16,
    offset: u32,
}

fn raw_deflate(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut encoder = DeflateEncoder::new(Vec::new(), Compression::best());
    encoder
        .write_all(data)
        .map_err(|error| format!("cannot deflate CDR package entry: {error}"))?;
    encoder
        .finish()
        .map_err(|error| format!("cannot finish CDR package entry: {error}"))
}

fn zip_package(entries: Vec<ZipEntry>) -> Result<Vec<u8>, String> {
    let mut output = Vec::new();
    let mut prepared = Vec::with_capacity(entries.len());
    for entry in entries {
        let name = entry.name.as_bytes();
        let compressed = if entry.deflate {
            raw_deflate(&entry.data)?
        } else {
            entry.data.clone()
        };
        let offset = u32::try_from(output.len()).map_err(|_| "CDR ZIP exceeds u32")?;
        let compressed_len = u32::try_from(compressed.len()).map_err(|_| "CDR entry too large")?;
        let uncompressed_len =
            u32::try_from(entry.data.len()).map_err(|_| "CDR entry too large")?;
        let crc = crc32fast::hash(&entry.data);
        let method = if entry.deflate { 8 } else { 0 };
        push_u32(&mut output, 0x0403_4b50);
        push_u16(&mut output, 20);
        push_u16(&mut output, 0);
        push_u16(&mut output, method);
        push_u16(&mut output, 0);
        push_u16(&mut output, 33);
        push_u32(&mut output, crc);
        push_u32(&mut output, compressed_len);
        push_u32(&mut output, uncompressed_len);
        push_u16(
            &mut output,
            u16::try_from(name.len()).map_err(|_| "CDR ZIP name too long")?,
        );
        push_u16(&mut output, 0);
        output.extend_from_slice(name);
        output.extend_from_slice(&compressed);
        prepared.push(PreparedZipEntry {
            name: entry.name,
            compressed,
            uncompressed_len,
            crc,
            method,
            offset,
        });
    }
    let central_offset = u32::try_from(output.len()).map_err(|_| "CDR ZIP exceeds u32")?;
    for entry in &prepared {
        let name = entry.name.as_bytes();
        push_u32(&mut output, 0x0201_4b50);
        push_u16(&mut output, 0x0314);
        push_u16(&mut output, 20);
        push_u16(&mut output, 0);
        push_u16(&mut output, entry.method);
        push_u16(&mut output, 0);
        push_u16(&mut output, 33);
        push_u32(&mut output, entry.crc);
        push_u32(
            &mut output,
            u32::try_from(entry.compressed.len()).map_err(|_| "CDR entry too large")?,
        );
        push_u32(&mut output, entry.uncompressed_len);
        push_u16(
            &mut output,
            u16::try_from(name.len()).map_err(|_| "CDR ZIP name too long")?,
        );
        push_u16(&mut output, 0);
        push_u16(&mut output, 0);
        push_u16(&mut output, 0);
        push_u16(&mut output, 0);
        push_u32(&mut output, 0o100644 << 16);
        push_u32(&mut output, entry.offset);
        output.extend_from_slice(name);
    }
    let central_size = u32::try_from(output.len())
        .map_err(|_| "CDR ZIP exceeds u32")?
        .checked_sub(central_offset)
        .ok_or("CDR ZIP central directory underflow")?;
    let count = u16::try_from(prepared.len()).map_err(|_| "too many CDR ZIP entries")?;
    push_u32(&mut output, 0x0605_4b50);
    push_u16(&mut output, 0);
    push_u16(&mut output, 0);
    push_u16(&mut output, count);
    push_u16(&mut output, count);
    push_u32(&mut output, central_size);
    push_u32(&mut output, central_offset);
    push_u16(&mut output, 0);
    if output.len() > MAX_OUTPUT_BYTES {
        return Err("CDR package exceeds the native output limit".to_owned());
    }
    Ok(output)
}

fn le_u16(data: &[u8], offset: usize) -> Result<u16, String> {
    let bytes = data
        .get(offset..offset + 2)
        .ok_or("truncated CDR ZIP u16")?;
    Ok(u16::from_le_bytes(bytes.try_into().unwrap()))
}

fn le_u32(data: &[u8], offset: usize) -> Result<u32, String> {
    let bytes = data
        .get(offset..offset + 4)
        .ok_or("truncated CDR ZIP u32")?;
    Ok(u32::from_le_bytes(bytes.try_into().unwrap()))
}

fn validate_package_bytes(package: &[u8]) -> Result<(), String> {
    if package.len() < 22 {
        return Err("CDR ZIP is truncated".to_owned());
    }
    let eocd = package.len() - 22;
    if le_u32(package, eocd)? != 0x0605_4b50
        || le_u16(package, eocd + 4)? != 0
        || le_u16(package, eocd + 6)? != 0
        || le_u16(package, eocd + 8)? != 9
        || le_u16(package, eocd + 10)? != 9
        || le_u16(package, eocd + 20)? != 0
    {
        return Err("CDR ZIP end record is invalid".to_owned());
    }
    let central_size = le_u32(package, eocd + 12)? as usize;
    let central_offset = le_u32(package, eocd + 16)? as usize;
    if central_offset.checked_add(central_size) != Some(eocd) {
        return Err("CDR ZIP central directory bounds are invalid".to_owned());
    }
    let expected_names = [
        "mimetype",
        "content/riffData.cdr",
        "metadata/thumbnails/thumbnail.bmp",
        "metadata/thumbnails/page1.bmp",
        "color/color.xml",
        "metadata/textinfo.xml",
        "color/docPalette.xml",
        "metadata/metadata.xml",
        "links.xml",
    ];
    let mut entries = Vec::with_capacity(expected_names.len());
    let mut expanded_total = 0usize;
    let mut pos = central_offset;
    for expected_name in expected_names {
        if le_u32(package, pos)? != 0x0201_4b50 {
            return Err("CDR ZIP central entry signature is invalid".to_owned());
        }
        let method = le_u16(package, pos + 10)?;
        let crc = le_u32(package, pos + 16)?;
        let compressed_len = le_u32(package, pos + 20)? as usize;
        let uncompressed_len = le_u32(package, pos + 24)? as usize;
        expanded_total = expanded_total
            .checked_add(uncompressed_len)
            .ok_or("CDR ZIP expanded-size overflow")?;
        if uncompressed_len > MAX_OUTPUT_BYTES || expanded_total > MAX_OUTPUT_BYTES {
            return Err("CDR ZIP expanded data exceeds the limit".to_owned());
        }
        let name_len = le_u16(package, pos + 28)? as usize;
        let extra_len = le_u16(package, pos + 30)? as usize;
        let comment_len = le_u16(package, pos + 32)? as usize;
        let local_offset = le_u32(package, pos + 42)? as usize;
        let name_start = pos.checked_add(46).ok_or("CDR ZIP offset overflow")?;
        let name_end = name_start
            .checked_add(name_len)
            .ok_or("CDR ZIP name overflow")?;
        let name = std::str::from_utf8(
            package
                .get(name_start..name_end)
                .ok_or("truncated CDR ZIP central name")?,
        )
        .map_err(|_| "CDR ZIP entry name is not UTF-8")?;
        if name != expected_name {
            return Err("CDR ZIP entries are missing or out of order".to_owned());
        }
        pos = name_end
            .checked_add(extra_len)
            .and_then(|value| value.checked_add(comment_len))
            .ok_or("CDR ZIP central entry overflow")?;
        if le_u32(package, local_offset)? != 0x0403_4b50
            || le_u16(package, local_offset + 8)? != method
            || le_u32(package, local_offset + 14)? != crc
            || le_u32(package, local_offset + 18)? as usize != compressed_len
            || le_u32(package, local_offset + 22)? as usize != uncompressed_len
        {
            return Err("CDR ZIP local and central entries disagree".to_owned());
        }
        let local_name_len = le_u16(package, local_offset + 26)? as usize;
        let local_extra_len = le_u16(package, local_offset + 28)? as usize;
        let local_name_start = local_offset
            .checked_add(30)
            .ok_or("CDR ZIP local offset overflow")?;
        let local_name_end = local_name_start
            .checked_add(local_name_len)
            .ok_or("CDR ZIP local name overflow")?;
        if package.get(local_name_start..local_name_end) != Some(name.as_bytes()) {
            return Err("CDR ZIP local entry name disagrees".to_owned());
        }
        let data_start = local_name_end
            .checked_add(local_extra_len)
            .ok_or("CDR ZIP entry offset overflow")?;
        let data_end = data_start
            .checked_add(compressed_len)
            .ok_or("CDR ZIP entry size overflow")?;
        if data_end > central_offset {
            return Err("CDR ZIP local entry overlaps the central directory".to_owned());
        }
        let compressed = package
            .get(data_start..data_end)
            .ok_or("truncated CDR ZIP entry")?;
        let decoded = match method {
            0 => compressed.to_vec(),
            8 => {
                let mut decoder = DeflateDecoder::new(compressed);
                let mut output = Vec::new();
                decoder
                    .by_ref()
                    .take(
                        u64::try_from(uncompressed_len)
                            .map_err(|_| "CDR ZIP expanded length overflow")?
                            .saturating_add(1),
                    )
                    .read_to_end(&mut output)
                    .map_err(|error| format!("invalid CDR ZIP deflate stream: {error}"))?;
                output
            }
            _ => return Err("CDR ZIP uses an unsupported compression method".to_owned()),
        };
        if decoded.len() != uncompressed_len || crc32fast::hash(&decoded) != crc {
            return Err("CDR ZIP entry length or CRC is invalid".to_owned());
        }
        entries.push(decoded);
    }
    if pos != eocd {
        return Err("CDR ZIP central directory has trailing data".to_owned());
    }
    if entries[0] != b"application/x-vnd.corel.draw.document+zip"
        || !entries[2].starts_with(b"BM")
        || !entries[3].starts_with(b"BM")
    {
        return Err("CDR ZIP identity or previews are invalid".to_owned());
    }
    crate::reader::decode(&entries[1], &entries[7])
        .map_err(|error| format!("CDR ZIP RIFF is invalid: {error}"))?;
    Ok(())
}

fn encode_package(request: &Request) -> Result<Vec<u8>, String> {
    let riff = encode(request)?;
    let thumbnail_size = preview_dimensions(request.page_w_mm, request.page_h_mm, 395, 256);
    let page_size = preview_dimensions(request.page_w_mm, request.page_h_mm, 256, 256);
    let entries = vec![
        ZipEntry {
            name: "mimetype",
            data: b"application/x-vnd.corel.draw.document+zip".to_vec(),
            deflate: false,
        },
        ZipEntry {
            name: "content/riffData.cdr",
            data: riff,
            deflate: false,
        },
        ZipEntry {
            name: "metadata/thumbnails/thumbnail.bmp",
            data: preview_bmp(request, thumbnail_size.0, thumbnail_size.1)?,
            deflate: true,
        },
        ZipEntry {
            name: "metadata/thumbnails/page1.bmp",
            data: preview_bmp(request, page_size.0, page_size.1)?,
            deflate: true,
        },
        ZipEntry {
            name: "color/color.xml",
            data: b"<Color><ColorContext><ColorModel>Rgb</ColorModel></ColorContext><HasRgbObjects>true</HasRgbObjects></Color>".to_vec(),
            deflate: true,
        },
        ZipEntry {
            name: "metadata/textinfo.xml",
            data: textinfo(request),
            deflate: true,
        },
        ZipEntry {
            name: "color/docPalette.xml",
            data: b"<?xml version=\"1.0\"?><palette name=\"Document Palette\"><colors/></palette>".to_vec(),
            deflate: true,
        },
        ZipEntry {
            name: "metadata/metadata.xml",
            data: metadata(request)?,
            deflate: true,
        },
        ZipEntry {
            name: "links.xml",
            data: b"<?xml version=\"1.0\" encoding=\"UTF-8\" ?>\n<linkInfo />\n".to_vec(),
            deflate: true,
        },
    ];
    let package = zip_package(entries)?;
    validate_package_bytes(&package)?;
    Ok(package)
}

#[no_mangle]
pub extern "C" fn narwhal_cdr_abi_version() -> u32 {
    ABI_VERSION
}

/// Encode the declared CorelDRAW v15 vector subset into one RIFF stream.
///
/// # Safety
/// `input` must address `input_len` readable bytes, `required` one writable `usize`, and when
/// non-null `output` must address `output_capacity` writable bytes for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn narwhal_cdr_v15_riff(
    input: *const u8,
    input_len: usize,
    output: *mut u8,
    output_capacity: usize,
    required: *mut usize,
) -> i32 {
    if required.is_null() {
        return -1;
    }
    // SAFETY: required was checked non-null.
    unsafe { *required = 0 };
    if input.is_null() || input_len > MAX_INPUT_BYTES || input_len > isize::MAX as usize {
        return -1;
    }
    // SAFETY: pointers and byte spans are validated above; caller owns buffers for this call.
    let input = unsafe { slice::from_raw_parts(input, input_len) };
    let request = match serde_json::from_slice::<Request>(input) {
        Ok(request) => request,
        Err(_) => return -2,
    };
    let data = match encode(&request) {
        Ok(data) => data,
        Err(_) => return -3,
    };
    // SAFETY: required was checked non-null.
    unsafe { *required = data.len() };
    if output_capacity < data.len() || output.is_null() {
        return 1;
    }
    if output_capacity > isize::MAX as usize {
        return -1;
    }
    // SAFETY: the caller promised output_capacity writable bytes and it is large enough.
    unsafe { std::ptr::copy_nonoverlapping(data.as_ptr(), output, data.len()) };
    0
}

/// Encode a complete CorelDRAW v15 ZIP package through the native writer.
///
/// # Safety
/// The pointer contract matches `narwhal_cdr_v15_riff`.
#[no_mangle]
pub unsafe extern "C" fn narwhal_cdr_v15_package(
    input: *const u8,
    input_len: usize,
    output: *mut u8,
    output_capacity: usize,
    required: *mut usize,
) -> i32 {
    if required.is_null() {
        return -1;
    }
    // SAFETY: required was checked non-null.
    unsafe { *required = 0 };
    if input.is_null() || input_len > MAX_INPUT_BYTES || input_len > isize::MAX as usize {
        return -1;
    }
    // SAFETY: pointers and byte spans are validated above; caller owns buffers for this call.
    let input = unsafe { slice::from_raw_parts(input, input_len) };
    let request = match serde_json::from_slice::<Request>(input) {
        Ok(request) => request,
        Err(_) => return -2,
    };
    let data = match encode_package(&request) {
        Ok(data) => data,
        Err(_) => return -3,
    };
    // SAFETY: required was checked non-null.
    unsafe { *required = data.len() };
    if output_capacity < data.len() || output.is_null() {
        return 1;
    }
    if output_capacity > isize::MAX as usize {
        return -1;
    }
    // SAFETY: the caller promised output_capacity writable bytes and it is large enough.
    unsafe { std::ptr::copy_nonoverlapping(data.as_ptr(), output, data.len()) };
    0
}

/// Validate a complete package returned by the v15 package ABI.
///
/// # Safety
/// `package` must address `package_len` readable bytes for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn narwhal_cdr_v15_validate_package(
    package: *const u8,
    package_len: usize,
) -> i32 {
    if package.is_null() || package_len > MAX_OUTPUT_BYTES || package_len > isize::MAX as usize {
        return -1;
    }
    // SAFETY: the pointer and bounded byte span were validated above.
    let package = unsafe { slice::from_raw_parts(package, package_len) };
    if validate_package_bytes(package).is_ok() {
        0
    } else {
        -2
    }
}

/// Decode the declared CorelDRAW v15 vector subset into validated JSON.
///
/// Returns `1` when the caller must allocate `required` bytes, `-4` for a
/// well-formed version marker from an unsupported CDR generation, and a
/// negative status for invalid arguments or malformed input.
///
/// # Safety
/// Non-empty input spans must address readable bytes. `required` must address
/// one writable `usize`. A non-null output must address `output_capacity`
/// writable bytes for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn narwhal_cdr_v15_read(
    riff: *const u8,
    riff_len: usize,
    metadata: *const u8,
    metadata_len: usize,
    output: *mut u8,
    output_capacity: usize,
    required: *mut usize,
) -> i32 {
    if required.is_null() {
        return -1;
    }
    // SAFETY: required was checked non-null.
    unsafe { *required = 0 };
    if riff_len > MAX_INPUT_BYTES
        || metadata_len > MAX_METADATA_BYTES
        || riff_len > isize::MAX as usize
        || metadata_len > isize::MAX as usize
        || (riff.is_null() && riff_len != 0)
        || (metadata.is_null() && metadata_len != 0)
        || (output.is_null() && output_capacity != 0)
        || output_capacity > isize::MAX as usize
    {
        return -1;
    }
    let riff = if riff_len == 0 {
        &[]
    } else {
        // SAFETY: the non-empty pointer and bounded span were validated above.
        unsafe { slice::from_raw_parts(riff, riff_len) }
    };
    let metadata = if metadata_len == 0 {
        &[]
    } else {
        // SAFETY: the non-empty pointer and bounded span were validated above.
        unsafe { slice::from_raw_parts(metadata, metadata_len) }
    };
    if riff.len() >= 22
        && &riff[..4] == b"RIFF"
        && &riff[8..12] == b"CDRF"
        && u32::from_le_bytes([riff[4], riff[5], riff[6], riff[7]]) as usize == riff.len() - 8
        && &riff[12..16] == b"vrsn"
        && u32::from_le_bytes([riff[16], riff[17], riff[18], riff[19]]) == 2
    {
        let version = u16::from_le_bytes([riff[20], riff[21]]);
        if version != CDR_VERSION {
            return -4;
        }
    }
    let drawing = match reader::decode(riff, metadata) {
        Ok(drawing) => drawing,
        Err(_) => return -2,
    };
    let mut output_data = LimitedOutput {
        bytes: Vec::with_capacity((1024 * 1024).min(MAX_OUTPUT_BYTES)),
        limit: MAX_OUTPUT_BYTES,
    };
    if serde_json::to_writer(&mut output_data, &drawing).is_err() {
        return -3;
    }
    let data = output_data.bytes;
    // SAFETY: required was checked non-null.
    unsafe { *required = data.len() };
    if output_capacity < data.len() || output.is_null() {
        return 1;
    }
    // SAFETY: caller promises output_capacity writable bytes; it is large enough.
    unsafe { std::ptr::copy_nonoverlapping(data.as_ptr(), output, data.len()) };
    0
}

#[cfg(test)]
mod tests {
    use super::{
        coord, encode, encode_package, is_registration_fill, metadata, preview_bmp, rgb8,
        validate_package_bytes, validate_request, Layer, LimitedOutput, Request, Stroke,
    };
    use std::io::Write;

    fn package_request() -> Request {
        Request {
            page_w_mm: 508.0,
            page_h_mm: 762.0,
            hairline: true,
            title: "20 x 30 & Cut".to_owned(),
            strokes: vec![Stroke {
                beziers: vec![[[0.0, 0.0], [10.0, 0.0], [20.0, 0.0], [30.0, 0.0]]],
                closed: false,
                rgb: [1.0, 0.0, 0.0],
                width_mm: 0.25,
                layer: "Cut".to_owned(),
            }],
            layers: vec![Layer {
                name: "Cut".to_owned(),
                guid_le: [0; 16],
            }],
        }
    }

    #[test]
    fn coordinates_and_colors_are_bounded() {
        assert_eq!(coord(1.25).unwrap(), 12_500);
        assert_eq!(rgb8([1.0, 0.5, -1.0]).unwrap(), [255, 128, 0]);
        assert!(coord(f64::INFINITY).is_err());
    }

    #[test]
    fn production_riff_uses_two_compressed_lists_and_round_trips() {
        let request = package_request();
        let riff = encode(&request).unwrap();
        assert_eq!(riff.windows(4).filter(|value| *value == b"cmpr").count(), 2);
        assert_eq!(
            riff.windows(8)
                .filter(|value| *value == b"CPng\x01\x00\x04\x00")
                .count(),
            4
        );
        let metadata = metadata(&request).unwrap();
        assert!(crate::reader::decode(&riff, &metadata).is_ok());
    }

    #[test]
    fn registration_fill_requires_closed_black_register_path() {
        let mut request = package_request();
        let stroke = &mut request.strokes[0];
        stroke.layer = "Register".to_owned();
        stroke.rgb = [0.0, 0.0, 0.0];
        stroke.closed = true;
        assert!(is_registration_fill(stroke).unwrap());
        stroke.closed = false;
        assert!(!is_registration_fill(stroke).unwrap());
        stroke.closed = true;
        stroke.rgb = [1.0, 0.0, 0.0];
        assert!(!is_registration_fill(stroke).unwrap());
        stroke.rgb = [0.0, 0.0, 0.0];
        stroke.layer = "Other".to_owned();
        assert!(!is_registration_fill(stroke).unwrap());
    }

    #[test]
    fn duplicate_layer_descriptors_are_rejected_before_object_expansion() {
        let mut request = package_request();
        request.layers.push(Layer {
            name: "Cut".to_owned(),
            guid_le: [1; 16],
        });
        assert!(validate_request(&request)
            .unwrap_err()
            .contains("duplicate layer descriptors"));
    }

    #[test]
    fn generated_preview_contains_the_red_drawing_on_white() {
        let bmp = preview_bmp(&package_request(), 128, 128).unwrap();
        let pixels = &bmp[54..];
        assert!(pixels.chunks_exact(3).any(|pixel| pixel == [0, 0, 255]));
        assert!(pixels.chunks_exact(3).any(|pixel| pixel == [255, 255, 255]));
    }

    #[test]
    fn package_has_nine_entries_metadata_previews_and_valid_zip_directory() {
        let package = encode_package(&package_request()).unwrap();
        assert!(package.starts_with(b"PK\x03\x04"));
        assert_eq!(
            package
                .windows(4)
                .filter(|value| *value == b"PK\x03\x04")
                .count(),
            9
        );

        for name in [
            b"metadata/metadata.xml".as_slice(),
            b"metadata/thumbnails/thumbnail.bmp".as_slice(),
            b"metadata/thumbnails/page1.bmp".as_slice(),
        ] {
            assert!(package.windows(name.len()).any(|value| value == name));
        }
        let end = package.len() - 22;
        assert_eq!(&package[end..end + 4], b"PK\x05\x06");
        assert_eq!(
            u16::from_le_bytes(package[end + 10..end + 12].try_into().unwrap()),
            9
        );
        let mut damaged = package;
        damaged[end..end + 4].copy_from_slice(b"BAD!");
        assert!(validate_package_bytes(&damaged).is_err());
    }

    #[test]
    fn reader_output_limit_applies_before_writing_bytes() {
        let mut output = LimitedOutput {
            bytes: Vec::new(),
            limit: 3,
        };
        assert!(output.write_all(b"four").is_err());
        assert!(output.bytes.is_empty());
    }
}

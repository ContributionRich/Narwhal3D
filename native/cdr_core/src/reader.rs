use flate2::read::ZlibDecoder;
use serde::Serialize;
use std::collections::BTreeMap;
use std::io::Read;

const UNITS_PER_MM: f64 = 10_000.0;
const MAX_EXPANDED: usize = 256 * 1024 * 1024;
const MAX_RECORDS: usize = 500_000;
const MAX_NODES: usize = 2_000_000;
const MAX_OBJECTS: usize = 100_000;
const MAX_PATHS: usize = 500_000;
const MAX_LAYER_BYTES: usize = 64 * 1024 * 1024;
const MAX_LAYER_NAME_BYTES: usize = 1024 * 1024;
const MAX_DEPTH: usize = 256;

type Point = [f64; 2];
type Bezier = [Point; 4];
type Transform = [f64; 6];

#[derive(Clone)]
struct Node {
    tag: [u8; 4],
    kind: [u8; 4],
    payload: Vec<u8>,
    children: Vec<Node>,
}

#[derive(Serialize)]
pub struct Drawing {
    width_mm: f64,
    height_mm: f64,
    version: u16,
    objects: Vec<Object>,
}

#[derive(Serialize)]
struct Object {
    paths: Vec<Path>,
    layer: String,
    rgb: [u8; 3],
    width_mm: f64,
    hairline: bool,
}

#[derive(Serialize)]
struct Path {
    beziers: Vec<Bezier>,
    closed: bool,
}

fn bytes<const N: usize>(data: &[u8], offset: usize) -> Result<[u8; N], String> {
    let end = offset.checked_add(N).ok_or("CDR field offset overflow")?;
    data.get(offset..end)
        .ok_or_else(|| "short CDR field".to_owned())?
        .try_into()
        .map_err(|_| "short CDR field".to_owned())
}

fn u16_at(data: &[u8], offset: usize) -> Result<u16, String> {
    Ok(u16::from_le_bytes(bytes(data, offset)?))
}

fn u32_at(data: &[u8], offset: usize) -> Result<u32, String> {
    Ok(u32::from_le_bytes(bytes(data, offset)?))
}

fn i32_at(data: &[u8], offset: usize) -> Result<i32, String> {
    Ok(i32::from_le_bytes(bytes(data, offset)?))
}

fn f64_at(data: &[u8], offset: usize) -> Result<f64, String> {
    Ok(f64::from_le_bytes(bytes(data, offset)?))
}

fn records(
    data: &[u8],
    start: usize,
    end: usize,
    lengths: Option<&[u32]>,
    count: &mut usize,
    depth: usize,
) -> Result<Vec<Node>, String> {
    if depth > MAX_DEPTH {
        return Err("CDR RIFF nesting is too deep".to_owned());
    }
    if end > data.len() || start > end {
        return Err("invalid CDR record range".to_owned());
    }
    let mut result = Vec::new();
    let mut pos = start;
    while pos + 8 <= end {
        *count += 1;
        if *count > MAX_RECORDS {
            return Err("CDR has too many records".to_owned());
        }
        let tag = bytes(data, pos)?;
        let raw_length = usize::try_from(u32_at(data, pos + 4)?).map_err(|_| "length overflow")?;
        let length = if let Some(table) = lengths {
            table
                .get(raw_length)
                .copied()
                .map(|value| value as usize)
                .unwrap_or(raw_length)
        } else {
            raw_length
        };
        let stop = pos
            .checked_add(8)
            .and_then(|value| value.checked_add(length))
            .ok_or("CDR record length overflow")?;
        if stop > end || stop <= pos {
            return Err("invalid CDR RIFF record length".to_owned());
        }
        let is_container = (&tag == b"LIST" || &tag == b"RIFF") && length >= 4;
        let mut kind = [0; 4];
        if is_container {
            kind.copy_from_slice(&data[pos + 8..pos + 12]);
        }
        let payload = if !is_container || &kind == b"cmpr" {
            data[pos + 8..stop].to_vec()
        } else {
            Vec::new()
        };
        let children = if is_container && &kind != b"cmpr" {
            records(data, pos + 12, stop, lengths, count, depth + 1)?
        } else {
            Vec::new()
        };
        let node = Node {
            tag,
            kind,
            payload,
            children,
        };
        result.push(node);
        pos = stop.checked_add(length & 1).ok_or("CDR padding overflow")?;
        if pos > end {
            return Err("CDR record is missing its padding byte".to_owned());
        }
    }
    if pos != end && (end - pos >= 8 || data[pos..end].iter().any(|byte| *byte != 0)) {
        return Err("trailing invalid CDR record bytes".to_owned());
    }
    Ok(result)
}

fn inflate(data: &[u8], limit: usize) -> Result<Vec<u8>, String> {
    let mut decoder = ZlibDecoder::new(data);
    let mut out = Vec::new();
    decoder
        .by_ref()
        .take((limit + 1) as u64)
        .read_to_end(&mut out)
        .map_err(|error| format!("invalid CDR compressed stream: {error}"))?;
    if out.len() > limit {
        return Err("CDR compressed stream exceeds the limit".to_owned());
    }
    Ok(out)
}

fn decompress(
    node: &Node,
    count: &mut usize,
    expanded_total: &mut usize,
) -> Result<Vec<Node>, String> {
    let payload = &node.payload;
    if payload.len() < 30 || &payload[..4] != b"cmpr" {
        return Err("invalid compressed CDR list".to_owned());
    }
    let span = u32_at(payload, 4)? as usize;
    let expected = u32_at(payload, 8)? as usize;
    let compressed_size = span
        .checked_sub(8)
        .ok_or("invalid CDR compression lengths")?;
    let zstart = 28usize;
    if zstart.checked_add(span).ok_or("compression overflow")? > payload.len() {
        return Err("invalid CDR compression lengths".to_owned());
    }
    let remaining = MAX_EXPANDED
        .checked_sub(*expanded_total)
        .ok_or("CDR expanded data exceeds the limit")?;
    let record_data = inflate(&payload[zstart..zstart + compressed_size], remaining)?;
    if expected != 0 && record_data.len() != expected {
        return Err("CDR compressed stream has wrong expanded size".to_owned());
    }
    let remaining = MAX_EXPANDED
        .checked_sub(
            expanded_total
                .checked_add(record_data.len())
                .ok_or("CDR expanded-size overflow")?,
        )
        .ok_or("CDR expanded data exceeds the limit")?;
    let length_data = inflate(&payload[zstart + span..], remaining.min(32 * 1024 * 1024))?;
    *expanded_total = expanded_total
        .checked_add(record_data.len())
        .and_then(|value| value.checked_add(length_data.len()))
        .ok_or("CDR expanded-size overflow")?;
    if *expanded_total > MAX_EXPANDED {
        return Err("CDR expanded data exceeds the limit".to_owned());
    }
    if length_data.len() % 4 != 0 {
        return Err("invalid CDR record-length table".to_owned());
    }
    let lengths: Vec<u32> = length_data
        .chunks_exact(4)
        .map(|item| u32::from_le_bytes(item.try_into().unwrap()))
        .collect();
    records(&record_data, 0, record_data.len(), Some(&lengths), count, 0)
}

fn collect<'a>(nodes: &'a [Node], tag: &[u8; 4], out: &mut Vec<&'a Node>) {
    for node in nodes {
        if &node.tag == tag {
            out.push(node);
        }
        collect(&node.children, tag, out);
    }
}

fn all<'a>(nodes: &'a [Node], tag: &[u8; 4]) -> Vec<&'a Node> {
    let mut out = Vec::new();
    collect(nodes, tag, &mut out);
    out
}

fn args(payload: &[u8]) -> Result<(u32, BTreeMap<u32, &[u8]>), String> {
    if payload.len() < 20 {
        return Err("short CDR loda record".to_owned());
    }
    let total = u32_at(payload, 0)? as usize;
    let count = u32_at(payload, 4)? as usize;
    let offsets_at = u32_at(payload, 8)? as usize;
    let types_at = u32_at(payload, 12)? as usize;
    let kind = u32_at(payload, 16)?;
    if total < 20 || total > payload.len() || count > 4096 {
        return Err("invalid CDR loda header".to_owned());
    }
    let table_bytes = count.checked_mul(4).ok_or("loda overflow")?;
    let offsets_end = offsets_at.checked_add(table_bytes).ok_or("loda overflow")?;
    let types_end = types_at.checked_add(table_bytes).ok_or("loda overflow")?;
    if offsets_at < 20
        || types_at < 20
        || offsets_end > total
        || types_end > total
        || (offsets_at < types_end && types_at < offsets_end)
    {
        return Err("invalid CDR loda argument table".to_owned());
    }
    let arguments_at = offsets_end.max(types_end);
    let mut offsets = Vec::with_capacity(count);
    let mut types = Vec::with_capacity(count);
    for index in 0..count {
        offsets.push(u32_at(payload, offsets_at + index * 4)? as usize);
        types.push(u32_at(payload, types_at + index * 4)?);
    }
    types.reverse();
    let mut result = BTreeMap::new();
    for index in 0..count {
        let offset = offsets[index];
        let stop = offsets.get(index + 1).copied().unwrap_or(total);
        if offset < arguments_at || offset > stop || stop > total {
            return Err("invalid CDR loda argument offset".to_owned());
        }
        if types[index] == 0x03e8 && stop - offset > MAX_LAYER_NAME_BYTES {
            return Err("CDR layer name exceeds the limit".to_owned());
        }
        if result
            .insert(types[index], &payload[offset..stop])
            .is_some()
        {
            return Err("duplicate CDR loda argument type".to_owned());
        }
    }
    Ok((kind, result))
}

fn layer_name(layer: &Node, fallback: &str) -> Result<String, String> {
    for node in all(&layer.children, b"loda") {
        let (kind, values) = args(&node.payload)?;
        if kind != 0 {
            continue;
        }
        if let Some(raw) = values.get(&0x03e8) {
            if raw.len() > MAX_LAYER_NAME_BYTES || raw.len() % 2 != 0 {
                return Err("invalid or oversized CDR layer name".to_owned());
            }
            let words: Vec<u16> = raw
                .chunks_exact(2)
                .map(|item| u16::from_le_bytes(item.try_into().unwrap()))
                .take_while(|value| *value != 0)
                .collect();
            let name = String::from_utf16(&words).map_err(|_| "invalid UTF-16 CDR layer name")?;
            if !name.is_empty() {
                return Ok(name);
            }
        }
    }
    Ok(fallback.to_owned())
}

fn rgb(data: &[u8], offset: usize) -> [u8; 3] {
    let Ok(model) = u16_at(data, offset) else {
        return [0; 3];
    };
    let Ok(value) = u32_at(data, offset + 8) else {
        return [0; 3];
    };
    if model == 5 {
        return [
            ((value >> 16) & 255) as u8,
            ((value >> 8) & 255) as u8,
            (value & 255) as u8,
        ];
    }
    if model == 2 || model == 3 {
        let scale = if model == 2 { 100.0 } else { 255.0 };
        let channels = [
            (value & 255) as f64,
            ((value >> 8) & 255) as f64,
            ((value >> 16) & 255) as f64,
        ];
        let key = ((value >> 24) & 255) as f64;
        return channels.map(|channel| {
            (255.0 * (1.0 - (channel + key).min(scale) / scale)).round_ties_even() as u8
        });
    }
    [0; 3]
}

fn outlines(nodes: &[Node]) -> BTreeMap<u32, ([u8; 3], f64, bool)> {
    let mut result = BTreeMap::new();
    for node in all(nodes, b"outl") {
        let data = &node.payload;
        if data.len() < 20 {
            continue;
        }
        let Ok(id) = u32_at(data, 0) else { continue };
        let (mut pos, mut skip, mut field_id) = (4usize, 0usize, 0u32);
        for _ in 0..64 {
            let Some(next) = pos.checked_add(skip) else {
                break;
            };
            pos = next;
            if pos + 8 > data.len() {
                break;
            }
            field_id = u32_at(data, pos).unwrap_or(0);
            skip = u32_at(data, pos + 4).unwrap_or(0) as usize;
            pos += 8;
            if field_id == 1 {
                break;
            }
        }
        if field_id != 1 || pos + 76 > data.len() {
            continue;
        }
        let line_type = u16_at(data, pos).unwrap_or(0);
        if line_type & 0x20 == 0 {
            continue;
        }
        let width = i32_at(data, pos + 6).unwrap_or(0) as f64 / UNITS_PER_MM;
        let color = rgb(data, pos + 64);
        result.insert(id, (color, width, (width - 0.0762).abs() < 1e-9));
    }
    result
}

fn transform(node: &Node) -> Result<Transform, String> {
    for record in all(&node.children, b"trfd") {
        let data = &record.payload;
        if data.len() < 20 {
            continue;
        }
        let count = u32_at(data, 4).unwrap_or(0) as usize;
        let offsets_at = u32_at(data, 8).unwrap_or(usize::MAX as u32) as usize;
        if count == 0
            || offsets_at
                .checked_add(count * 4)
                .is_none_or(|end| end > data.len())
        {
            continue;
        }
        let offset = u32_at(data, offsets_at).unwrap_or(usize::MAX as u32) as usize;
        if offset.checked_add(64).is_none_or(|end| end > data.len())
            || u16_at(data, offset + 8).unwrap_or(0) != 0x08
        {
            continue;
        }
        if let Ok(values) = (0..6)
            .map(|index| f64_at(data, offset + 16 + index * 8))
            .collect::<Result<Vec<_>, _>>()
        {
            if values.iter().any(|value| !value.is_finite()) {
                return Err("CDR transform contains a non-finite value".to_owned());
            }
            return Ok([
                values[0],
                values[1],
                values[2] / UNITS_PER_MM,
                values[3],
                values[4],
                values[5] / UNITS_PER_MM,
            ]);
        }
    }
    Ok([1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
}

fn line_bezier(start: Point, end: Point) -> Bezier {
    [
        start,
        [
            start[0] + (end[0] - start[0]) / 3.0,
            start[1] + (end[1] - start[1]) / 3.0,
        ],
        [
            start[0] + 2.0 * (end[0] - start[0]) / 3.0,
            start[1] + 2.0 * (end[1] - start[1]) / 3.0,
        ],
        end,
    ]
}

fn path_data(
    data: &[u8],
    page_w: f64,
    page_h: f64,
    transform: Transform,
    total_nodes: &mut usize,
) -> Result<Vec<Path>, String> {
    if data.len() < 4 {
        return Ok(Vec::new());
    }
    let count = u16_at(data, 0)? as usize;
    let need = 4usize
        .checked_add(count.checked_mul(8).ok_or("path overflow")?)
        .and_then(|value| value.checked_add(count))
        .ok_or("path overflow")?;
    if count == 0 || need > data.len() {
        return Err("invalid CDR path node table".to_owned());
    }
    *total_nodes = total_nodes
        .checked_add(count)
        .ok_or("CDR path-node count overflow")?;
    if *total_nodes > MAX_NODES {
        return Err("CDR has too many path nodes".to_owned());
    }
    let [a, b, tx, d, e, ty] = transform;
    let mut points = Vec::with_capacity(count);
    for index in 0..count {
        let at = 4 + index * 8;
        let x = i32_at(data, at)? as f64 / UNITS_PER_MM;
        let y = i32_at(data, at + 4)? as f64 / UNITS_PER_MM;
        points.push([
            a * x + b * y + tx + page_w / 2.0,
            d * x + e * y + ty + page_h / 2.0,
        ]);
    }
    let types = &data[4 + count * 8..need];
    let mut paths = Vec::new();
    let mut beziers = Vec::new();
    let mut controls = Vec::new();
    let (mut start, mut current): (Option<Point>, Option<Point>) = (None, None);
    let mut closed = false;
    let finish = |paths: &mut Vec<Path>, beziers: &mut Vec<Bezier>, closed: &mut bool| {
        if !beziers.is_empty() {
            paths.push(Path {
                beziers: std::mem::take(beziers),
                closed: *closed,
            });
        }
        *closed = false;
    };
    for (point, node_type) in points.into_iter().zip(types.iter().copied()) {
        let command = node_type & 0xc0;
        let closes = node_type & 0x08 != 0;
        match command {
            0x00 => {
                finish(&mut paths, &mut beziers, &mut closed);
                controls.clear();
                start = Some(point);
                current = Some(point);
            }
            0x40 if current.is_some() => {
                beziers.push(line_bezier(current.unwrap(), point));
                current = Some(point);
                controls.clear();
                closed = closes;
            }
            0xc0 => controls.push(point),
            0x80 if current.is_some() => {
                let curve = if controls.len() >= 2 {
                    [
                        current.unwrap(),
                        controls[controls.len() - 2],
                        controls[controls.len() - 1],
                        point,
                    ]
                } else {
                    [current.unwrap(), current.unwrap(), point, point]
                };
                beziers.push(curve);
                current = Some(point);
                controls.clear();
                closed = closes;
            }
            _ => {}
        }
        if closes {
            if let (Some(current_point), Some(start_point)) = (current, start) {
                if current_point != start_point {
                    beziers.push(line_bezier(current_point, start_point));
                    current = start;
                }
            }
        }
    }
    finish(&mut paths, &mut beziers, &mut closed);
    Ok(paths)
}

fn object(
    node: &Node,
    layer: &str,
    styles: &BTreeMap<u32, ([u8; 3], f64, bool)>,
    page_w: f64,
    page_h: f64,
    total_nodes: &mut usize,
) -> Result<Object, String> {
    for loda in all(&node.children, b"loda") {
        let (kind, values) = args(&loda.payload)?;
        let Some(shape) = values.get(&0x1e) else {
            continue;
        };
        if kind != 3 {
            continue;
        }
        let outline_id = values
            .get(&0x0a)
            .and_then(|data| u32_at(data, 0).ok())
            .unwrap_or(0);
        let fill_id = values
            .get(&0x14)
            .and_then(|data| u32_at(data, 0).ok())
            .unwrap_or(0);
        let (mut color, width, hairline) =
            styles
                .get(&outline_id)
                .copied()
                .unwrap_or(([0, 0, 0], 0.0, false));
        if !width.is_finite() || width < 0.0 {
            return Err("invalid CDR outline width".to_owned());
        }
        if !styles.contains_key(&outline_id) && fill_id != 0 {
            color = [0, 0, 0];
        }
        let paths = path_data(shape, page_w, page_h, transform(node)?, total_nodes)?;
        if !paths.is_empty() {
            return Ok(Object {
                paths,
                layer: layer.to_owned(),
                rgb: color,
                width_mm: width,
                hairline,
            });
        }
    }
    Err("the CDR contains an unsupported non-curve object".to_owned())
}

fn metadata_dimension(metadata: &str, tag: &str) -> Result<f64, String> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let start = metadata
        .find(&open)
        .ok_or("CDR package has no measurable page dimensions")?
        + open.len();
    let end = metadata[start..]
        .find(&close)
        .ok_or("CDR package has no measurable page dimensions")?
        + start;
    let value = metadata[start..end]
        .trim()
        .parse::<u64>()
        .map_err(|_| "invalid CDR page dimension")?;
    Ok(value as f64 / UNITS_PER_MM)
}

pub fn decode(riff: &[u8], metadata: &[u8]) -> Result<Drawing, String> {
    if riff.len() < 22 || &riff[..4] != b"RIFF" || &riff[8..12] != b"CDRF" {
        return Err("not a Corel CDRF RIFF stream".to_owned());
    }
    if u32_at(riff, 4)? as usize != riff.len() - 8 {
        return Err("invalid CDR RIFF size".to_owned());
    }
    let version = if &riff[12..16] == b"vrsn" {
        u16_at(riff, 20)?
    } else {
        0
    };
    if version != 1500 {
        return Err(format!(
            "native CDR reader supports version 15, not {version}"
        ));
    }
    let metadata = std::str::from_utf8(metadata).map_err(|_| "CDR metadata is not UTF-8")?;
    let width = metadata_dimension(metadata, "PageWidth")?;
    let height = metadata_dimension(metadata, "PageHeight")?;
    if width <= 0.0 || height <= 0.0 {
        return Err("CDR page dimensions must be positive".to_owned());
    }
    let mut count = 0;
    let outer = records(riff, 12, riff.len(), None, &mut count, 0)?;
    let mut nodes = Vec::new();
    let mut expanded_total = 0;
    for node in outer {
        if &node.tag == b"LIST" && &node.kind == b"cmpr" {
            nodes.extend(decompress(&node, &mut count, &mut expanded_total)?);
        } else if &node.tag != b"vrsn" {
            nodes.push(node);
        }
    }
    let styles = outlines(&nodes);
    let pages: Vec<&Node> = nodes
        .iter()
        .filter(|node| &node.tag == b"LIST" && &node.kind == b"page")
        .collect();
    let drawing_pages: Vec<&Node> = pages
        .into_iter()
        .filter(|page| {
            all(&page.children, b"LIST")
                .iter()
                .any(|node| &node.kind == b"obj ")
        })
        .collect();
    if drawing_pages.len() != 1 {
        return Err(format!(
            "native CDR reader needs one drawing page, found {}",
            drawing_pages.len()
        ));
    }
    let layers: Vec<&Node> = all(&drawing_pages[0].children, b"LIST")
        .into_iter()
        .filter(|node| &node.kind == b"layr")
        .collect();
    let mut objects = Vec::new();
    let mut total_nodes = 0;
    let mut total_paths = 0usize;
    let mut total_layer_bytes = 0usize;
    for (index, layer) in layers.into_iter().enumerate() {
        let name = layer_name(layer, &format!("Layer {}", index + 1))?;
        total_layer_bytes = total_layer_bytes
            .checked_add(name.len())
            .ok_or("CDR layer-size overflow")?;
        if total_layer_bytes > MAX_LAYER_BYTES {
            return Err("CDR reconstructed layer data exceeds the limit".to_owned());
        }
        for child in &layer.children {
            if &child.tag == b"LIST" && &child.kind == b"obj " {
                if objects.len() >= MAX_OBJECTS {
                    return Err("CDR has too many objects".to_owned());
                }
                total_layer_bytes = total_layer_bytes
                    .checked_add(name.len())
                    .ok_or("CDR layer-size overflow")?;
                if total_layer_bytes > MAX_LAYER_BYTES {
                    return Err("CDR reconstructed layer data exceeds the limit".to_owned());
                }
                let value = object(child, &name, &styles, width, height, &mut total_nodes)?;
                total_paths = total_paths
                    .checked_add(value.paths.len())
                    .ok_or("CDR path-count overflow")?;
                if total_paths > MAX_PATHS {
                    return Err("CDR reconstructed data exceeds the limit".to_owned());
                }
                objects.push(value);
            }
        }
    }
    if objects.is_empty() {
        return Err("the CDR contains no supported curve objects".to_owned());
    }
    Ok(Drawing {
        width_mm: width,
        height_mm: height,
        version,
        objects,
    })
}

#[cfg(test)]
mod tests {
    use super::{inflate, layer_name, object, path_data, Node, MAX_LAYER_NAME_BYTES};
    use flate2::{write::ZlibEncoder, Compression};
    use std::io::Write;

    #[test]
    fn move_close_starts_a_new_open_subpath_without_a_chord() {
        let points = [(0i32, 0i32), (100_000, 0), (200_000, 0), (300_000, 0)];
        let mut data = Vec::new();
        data.extend_from_slice(&(points.len() as u16).to_le_bytes());
        data.extend_from_slice(&[0, 0]);
        for (x, y) in points {
            data.extend_from_slice(&x.to_le_bytes());
            data.extend_from_slice(&y.to_le_bytes());
        }
        data.extend_from_slice(&[0x00, 0x40, 0x08, 0x40]);
        let mut total = 0;
        let paths = path_data(&data, 0.0, 0.0, [1.0, 0.0, 0.0, 0.0, 1.0, 0.0], &mut total).unwrap();
        assert_eq!(paths.len(), 2);
        assert!(paths.iter().all(|path| !path.closed));
        assert!(paths.iter().all(|path| path.beziers.len() == 1));
        assert_eq!(paths[0].beziers[0][3], [10.0, 0.0]);
        assert_eq!(paths[1].beziers[0][0], [20.0, 0.0]);
    }

    #[test]
    fn inflate_stops_at_the_explicit_expansion_limit() {
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::best());
        encoder.write_all(&vec![0u8; 4096]).unwrap();
        let compressed = encoder.finish().unwrap();
        assert!(inflate(&compressed, 1024).is_err());
    }

    #[test]
    fn oversized_layer_names_are_rejected_before_decoding() {
        let raw = vec![b'a'; MAX_LAYER_NAME_BYTES + 2];
        let total = 28 + raw.len();
        let mut payload = Vec::with_capacity(total);
        payload.extend_from_slice(&(total as u32).to_le_bytes());
        payload.extend_from_slice(&1u32.to_le_bytes());
        payload.extend_from_slice(&20u32.to_le_bytes());
        payload.extend_from_slice(&24u32.to_le_bytes());
        payload.extend_from_slice(&0u32.to_le_bytes());
        payload.extend_from_slice(&28u32.to_le_bytes());
        payload.extend_from_slice(&0x03e8u32.to_le_bytes());
        payload.extend_from_slice(&raw);
        let layer = Node {
            tag: *b"LIST",
            kind: *b"layr",
            payload: Vec::new(),
            children: vec![Node {
                tag: *b"loda",
                kind: [0; 4],
                payload,
                children: Vec::new(),
            }],
        };
        assert!(layer_name(&layer, "Layer 1").is_err());
    }

    #[test]
    fn unsupported_objects_are_not_silently_dropped() {
        let node = Node {
            tag: *b"LIST",
            kind: *b"obj ",
            payload: Vec::new(),
            children: Vec::new(),
        };
        let mut total_nodes = 0;
        assert!(object(
            &node,
            "Layer 1",
            &Default::default(),
            100.0,
            80.0,
            &mut total_nodes,
        )
        .is_err());
    }
}

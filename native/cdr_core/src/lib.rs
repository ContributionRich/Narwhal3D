mod reader;

use serde::Deserialize;
use std::{io, slice};

const ABI_VERSION: u32 = 1;
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

fn color(rgb: [u8; 3]) -> Vec<u8> {
    let mut out = Vec::with_capacity(12);
    push_u16(&mut out, 5);
    push_u16(&mut out, 0);
    push_u32(&mut out, 0);
    push_u32(
        &mut out,
        (u32::from(rgb[0]) << 16) | (u32::from(rgb[1]) << 8) | u32::from(rgb[2]),
    );
    out
}

fn outline(id: u32, rgb: [u8; 3], width_mm: f64) -> Result<Vec<u8>, String> {
    if !width_mm.is_finite() || width_mm <= 0.0 {
        return Err("CDR outline width must be positive".to_owned());
    }
    let mut payload = Vec::new();
    push_u32(&mut payload, id);
    push_u32(&mut payload, 1);
    push_u32(&mut payload, 0);
    push_u16(&mut payload, 0x22);
    push_u16(&mut payload, 0);
    push_u16(&mut payload, 0);
    push_i32(&mut payload, coord(width_mm)?);
    push_u16(&mut payload, 100);
    payload.extend_from_slice(&[0, 0]);
    push_i32(&mut payload, 0);
    payload.extend_from_slice(&[0; 46]);
    payload.extend_from_slice(&color(rgb));
    payload.extend_from_slice(&[0; 16]);
    push_u16(&mut payload, 0);
    payload.extend_from_slice(&[0; 22]);
    push_u32(&mut payload, 0);
    push_u32(&mut payload, 0);
    chunk(b"outl", &payload)
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
        if let Some(last) = types.last_mut() {
            *last |= 0x08;
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
    let mut cursor = 20usize
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
    push_u32(&mut payload, 20 + count * 4);
    push_u32(&mut payload, kind);
    for offset in offsets {
        push_u32(&mut payload, offset);
    }
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
    let mut outline = Vec::new();
    push_u32(&mut outline, outline_id);
    loda(3, &[(0x1e, shape), (0x0a, outline)])
}

fn layer_descriptor(layer: &Layer) -> Result<Vec<u8>, String> {
    let mut flags = vec![
        1, 12, 0, 0, 0, 2, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 100, 7, 16, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ];
    let mut zero = Vec::new();
    push_u32(&mut zero, 0);
    let mut name = Vec::new();
    for unit in layer.name.encode_utf16() {
        push_u16(&mut name, unit);
    }
    push_u16(&mut name, 0);
    loda(
        0,
        &[
            (0x9c72, layer.guid_le.to_vec()),
            (0x2ee0, zero),
            (0x07d0, std::mem::take(&mut flags)),
            (0x03e8, name),
        ],
    )
}

fn bounds(stroke: &Stroke, page_w: f64, page_h: f64) -> Result<Bounds, String> {
    let (points, _) = path_nodes(stroke, page_w, page_h)?;
    if points.is_empty() {
        return Ok((0, 0, 0, 0));
    }
    let (mut x0, mut y0, mut x1, mut y1) = (i32::MAX, i32::MAX, i32::MIN, i32::MIN);
    for (x, y) in points {
        x0 = x0.min(x);
        y0 = y0.min(y);
        x1 = x1.max(x);
        y1 = y1.max(y);
    }
    Ok((x0, y0, x1, y1))
}

fn bbox(value: Bounds) -> Result<Vec<u8>, String> {
    let mut payload = Vec::with_capacity(16);
    push_i32(&mut payload, value.0);
    push_i32(&mut payload, value.1);
    push_i32(&mut payload, value.2);
    push_i32(&mut payload, value.3);
    chunk(b"bbox", &payload)
}

fn union(values: &[Bounds]) -> Bounds {
    if values.is_empty() {
        return (0, 0, 0, 0);
    }
    values
        .iter()
        .copied()
        .fold((i32::MAX, i32::MAX, i32::MIN, i32::MIN), |out, value| {
            (
                out.0.min(value.0),
                out.1.min(value.1),
                out.2.max(value.2),
                out.3.max(value.3),
            )
        })
}

fn width_key(width: f64) -> Result<i64, String> {
    if !width.is_finite() || width <= 0.0 {
        return Err("CDR outline width must be positive".to_owned());
    }
    Ok((width * 1_000_000_000.0).round_ties_even() as i64)
}

fn validate_request(request: &Request) -> Result<(), String> {
    let mut nodes = 0usize;
    for stroke in request
        .strokes
        .iter()
        .filter(|stroke| !stroke.beziers.is_empty())
    {
        if !request
            .layers
            .iter()
            .any(|layer| layer.name == stroke.layer)
        {
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

fn encode(request: Request) -> Result<Vec<u8>, String> {
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
    validate_request(&request)?;
    let mut styles: Vec<([u8; 3], i64, f64)> = Vec::new();
    for stroke in request
        .strokes
        .iter()
        .filter(|stroke| !stroke.beziers.is_empty())
    {
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

    let mut layer_chunks = Vec::new();
    for layer in &request.layers {
        let mut objects = Vec::new();
        let mut layer_bounds = Vec::new();
        for stroke in request
            .strokes
            .iter()
            .filter(|stroke| !stroke.beziers.is_empty() && stroke.layer == layer.name)
        {
            let value = bounds(stroke, request.page_w_mm, request.page_h_mm)?;
            layer_bounds.push(value);
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
            let object = list(
                b"obj ",
                &[
                    bbox(value)?,
                    list(
                        b"lgob",
                        &[loda_path(
                            stroke,
                            u32::try_from(style + 1).map_err(|_| "too many outline styles")?,
                            request.page_w_mm,
                            request.page_h_mm,
                        )?],
                    )?,
                ],
            )?;
            objects.push(object);
        }
        let mut children = vec![
            chunk(b"flgs", &0u32.to_le_bytes())?,
            bbox(union(&layer_bounds))?,
        ];
        children.push(list(b"lgob", &[layer_descriptor(layer)?])?);
        children.extend(objects);
        layer_chunks.push(list(b"layr", &children)?);
    }

    let page_bounds = (
        -coord(request.page_w_mm / 2.0)?,
        -coord(request.page_h_mm / 2.0)?,
        coord(request.page_w_mm / 2.0)?,
        coord(request.page_h_mm / 2.0)?,
    );
    let page = list(
        b"page",
        &[
            chunk(b"flgs", &0u32.to_le_bytes())?,
            bbox(page_bounds)?,
            list(b"gobj", &layer_chunks)?,
        ],
    )?;
    let mut mcfg = vec![0u8; 12];
    push_i32(&mut mcfg, coord(request.page_w_mm)?);
    push_i32(&mut mcfg, coord(request.page_h_mm)?);
    let mut doc_children = vec![chunk(b"mcfg", &mcfg)?];
    if !outline_chunks.is_empty() {
        doc_children.push(list(b"otlt", &outline_chunks)?);
    }
    let doc = list(b"doc ", &doc_children)?;
    let mut body = Vec::new();
    body.extend_from_slice(b"CDRF");
    body.extend_from_slice(&chunk(b"vrsn", &CDR_VERSION.to_le_bytes())?);
    body.extend_from_slice(&doc);
    body.extend_from_slice(&page);
    let mut riff = Vec::new();
    riff.extend_from_slice(b"RIFF");
    push_u32(
        &mut riff,
        u32::try_from(body.len()).map_err(|_| "RIFF too large")?,
    );
    riff.extend_from_slice(&body);
    Ok(riff)
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
    let data = match encode(request) {
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
    use super::{coord, rgb8, LimitedOutput};
    use std::io::Write;

    #[test]
    fn coordinates_and_colors_are_bounded() {
        assert_eq!(coord(1.25).unwrap(), 12_500);
        assert_eq!(rgb8([1.0, 0.5, -1.0]).unwrap(), [255, 128, 0]);
        assert!(coord(f64::INFINITY).is_err());
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

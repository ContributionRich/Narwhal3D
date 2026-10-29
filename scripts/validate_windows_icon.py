"""Test-only validation of the curated PNG-backed Windows launcher ICO.

Stdlib Python keeps packaging tests compiler/native-library independent. This is
not a general ICO decoder: the asset contract is seven non-interlaced RGBA PNGs.
"""
from __future__ import annotations

import struct
import zlib

RESOLUTIONS = (16, 24, 32, 48, 64, 128, 256)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decode_rgba_png(data: bytes, size: int) -> bytes:
    """Check PNG framing/CRCs and reconstruct all five PNG scanline filters."""
    if not 1 <= size <= 256 or not data.startswith(PNG_SIGNATURE):
        raise ValueError("expected bounded PNG image")
    offset, chunks = 8, []
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("truncated PNG chunk")
        length, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("PNG chunk outside image")
        payload = data[offset + 8:end - 4]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) != crc:
            raise ValueError("PNG CRC mismatch")
        chunks.append((kind, payload))
        offset = end
    if (len(chunks) < 3 or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13
            or chunks[-1] != (b"IEND", b"")
            or any(kind != b"IDAT" for kind, _ in chunks[1:-1])):
        raise ValueError("expected IHDR, contiguous IDAT and terminal IEND")
    if struct.unpack(">IIBBBBB", chunks[0][1]) != (size, size, 8, 6, 0, 0, 0):
        raise ValueError("PNG dimensions/format disagree with RGBA ICO directory")
    stride = size * 4
    expected = size * (stride + 1)
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(b"".join(payload for _, payload in chunks[1:-1]), expected + 1)
    except zlib.error as exc:
        raise ValueError("invalid PNG compressed stream") from exc
    if (len(raw) != expected or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail):
        raise ValueError("invalid PNG decoded length/end")
    pixels = bytearray()
    previous = bytearray(stride)
    for offset in range(0, expected, stride + 1):
        filtering = raw[offset]
        if filtering > 4:
            raise ValueError("invalid PNG scanline filter")
        row = bytearray(raw[offset + 1:offset + 1 + stride])
        for i in range(stride):
            left = row[i - 4] if i >= 4 else 0
            up = previous[i]
            upper_left = previous[i - 4] if i >= 4 else 0
            if filtering == 0:
                predictor = 0
            elif filtering == 1:
                predictor = left
            elif filtering == 2:
                predictor = up
            elif filtering == 3:
                predictor = (left + up) // 2
            else:
                p = left + up - upper_left
                distances = (abs(p - left), abs(p - up), abs(p - upper_left))
                predictor = (left, up, upper_left)[distances.index(min(distances))]
            row[i] = (row[i] + predictor) & 255
        pixels.extend(row)
        previous = row
    return bytes(pixels)


def validate_icon(data: bytes) -> tuple[int, ...]:
    """Reject invalid directory/image data, missing sizes and lost alpha coverage."""
    if len(data) < 6 or struct.unpack_from("<HHH", data) != (0, 1, len(RESOLUTIONS)):
        raise ValueError("expected seven-image ICO header")
    directory_end = 6 + 16 * len(RESOLUTIONS)
    if len(data) < directory_end:
        raise ValueError("truncated ICO directory")
    sizes, spans = [], []
    for index in range(len(RESOLUTIONS)):
        width, height, colours, reserved, planes, depth, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index)
        size = width or 256
        if ((height or 256) != size or size not in RESOLUTIONS or size in sizes
                or colours != 0 or reserved != 0 or planes not in (0, 1) or depth != 32):
            raise ValueError("invalid ICO resolution/colour directory entry")
        if offset < directory_end or length == 0 or offset + length > len(data):
            raise ValueError("ICO image outside payload")
        spans.append((offset, offset + length))
        sizes.append(size)
    cursor = directory_end
    for start, end in sorted(spans):
        if start != cursor:
            raise ValueError("ICO image overlap or unreferenced bytes")
        cursor = end
    if cursor != len(data):
        raise ValueError("unreferenced ICO trailing bytes")
    for size, (start, end) in zip(sizes, spans):
        pixels = decode_rgba_png(data[start:end], size)
        alpha = pixels[3::4]
        if not (0 in alpha and 255 in alpha and any(0 < a < 255 for a in alpha)):
            raise ValueError("icon needs transparent background, opaque artwork and antialiased alpha")
        visible_colours = {pixels[i:i + 3] for i in range(0, len(pixels), 4) if pixels[i + 3]}
        if len(visible_colours) < 2:
            raise ValueError("icon has no visible colour detail")
    return tuple(sorted(sizes))

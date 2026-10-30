"""Approved v4.4 chat presentation, with no provider or document authority.

Python is host UI glue here: line layout requires Blender's main-thread BLF
measurement callback. Results are cached by the renderer, not recomputed per frame.
No synthetic conversation is supplied; roles come only from runtime messages.
"""
from . import textfield


PALETTES = {
    "light": {
        "canvas": (238, 243, 249), "assistant": (255, 255, 255),
        "assistant_border": (214, 225, 239), "user": (226, 237, 255),
        "user_border": (187, 210, 242), "meta": (82, 103, 126),
        "meta_bg": (229, 238, 250), "notice": (244, 247, 250),
        "notice_border": (220, 228, 238),
    },
    "dark": {
        "canvas": (41, 41, 41), "assistant": (57, 57, 57),
        "assistant_border": (80, 80, 80), "user": (38, 63, 91),
        "user_border": (67, 101, 139), "meta": (186, 197, 211),
        "meta_bg": (41, 67, 95), "notice": (53, 53, 53),
        "notice_border": (83, 83, 83),
    },
}


def transcript_layout(messages, width, measure, scale=1.0):
    """Content coordinates are top-down; all text, including newlines, is literal.

    Runtime bounds (100 messages / 16K characters each) apply before this UI seam.
    `measure` uses the same 11px font as the renderer. Never infer role from text.
    """
    s = max(0.5, float(scale))
    width = max(1.0, float(width))
    inset = min(10 * s, width / 4)
    available = max(1.0, width - 2 * inset)
    cursor = 13 * s
    items = []
    for message in messages:
        role = message.role
        if role not in ("user", "assistant", "status"):
            raise ValueError("unsupported conversation role")
        status = role == "status"
        max_width = available if status else available * .95
        padding = min((8 if status else 10) * s, max_width / 4)
        text_width = max(1.0, max_width - 2 * padding)
        spans = textfield.wrap_spans(message.text, text_width, measure)
        lines = tuple(message.text[a:b] for a, b in spans)
        bubble_width = min(max_width, max((measure(line) for line in lines), default=0)
                           + 2 * padding)
        # Metadata needs room independently of a short reply's text width.
        outer_width = min(max_width, max(bubble_width, 96 * s if not status else 0))
        left = width - inset - outer_width if role == "user" else inset
        meta_h = 0 if status else 25 * s
        line_h = 16.72 * s
        bubble_h = 2 * padding + max(1, len(lines)) * line_h
        bubble_left = left + outer_width - bubble_width if role == "user" else left
        items.append({"message": message, "role": role, "label": {"user": "You", "assistant": "Narwhal AI",
                                                "status": ""}[role],
                      "left": left, "top": cursor, "width": outer_width,
                      "bubble": (bubble_left, cursor + meta_h,
                                 bubble_left + bubble_width, cursor + meta_h + bubble_h),
                      "lines": lines, "padding": padding, "line_h": line_h})
        cursor += meta_h + bubble_h + 13 * s
    return {"items": items, "height": cursor + 4 * s if items else 0.0}


def scroll_offset(offset, content_height, viewport_height):
    """Distance from the newest end; zero follows new replies."""
    return min(max(0.0, float(offset)), max(0.0, content_height - viewport_height))


def visible_items(layout, box, offset=0):
    """Return only intersecting messages, translated into region coordinates."""
    x0, y0, x1, y1 = box
    height = max(0.0, y1 - y0)
    offset = scroll_offset(offset, layout["height"], height)
    origin = y1 + max(0.0, layout["height"] - height) - offset
    if height <= 0 or x1 <= x0:
        return [], offset
    visible = []
    for item in layout["items"]:
        a, top, b, bottom = item["bubble"]
        if origin - item["top"] <= y0 or origin - bottom >= y1:
            continue
        visible.append((item, (x0 + a, origin - bottom, x0 + b, origin - top),
                        origin - item["top"]))
    return visible, offset


def anchored_offset(old, new, old_box, box, offset):
    """Keep a surviving visible message in place when the oldest turn is evicted."""
    if not offset:
        return 0.0
    visible, _ = visible_items(old, old_box, offset)
    for previous, _bubble, top in visible:
        for current in new['items']:
            if current['message'] is previous['message']:
                target = box[3] - (old_box[3] - top)
                origin = box[3] + max(0, new['height'] - (box[3] - box[1]))
                return scroll_offset(origin - current['top'] - target,
                                     new['height'], box[3] - box[1])
    # The entire visible range was evicted: show the oldest remaining context.
    return scroll_offset(new['height'], new['height'], box[3] - box[1])


def scroll_key(key, offset, content_height, viewport_height, scale=1.0):
    """Keyboard browsing while the transcript, not the composer, has focus."""
    page = max(16 * scale, viewport_height * .9)
    if key == "HOME":
        offset = content_height
    elif key == "END":
        offset = 0
    elif key in ("PAGE_UP", "UP_ARROW"):
        offset += page if key == "PAGE_UP" else 48 * scale
    elif key in ("PAGE_DOWN", "DOWN_ARROW"):
        offset -= page if key == "PAGE_DOWN" else 48 * scale
    else:
        return None
    return scroll_offset(offset, content_height, viewport_height)

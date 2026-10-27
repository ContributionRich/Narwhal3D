"""Title-anchored shell menus; no command execution and no eager Blender imports.

Integration (all coordinates are WINDOW pixels, y up)::

    register()  # after the other shell draw handlers, so menus paint last
    open(rows, (title_left, title_bottom), title, callback)
    if handle_event(event):  # UNCONDITIONALLY before pickers/navigation/shortcuts
        return {'RUNNING_MODAL'}
    unregister()

``callback(action, explanation)`` receives ``(row['action'], None)`` for an
available leaf, or ``(None, '<label>: not implemented yet')`` for an unavailable
one. An optional row ``explanation`` overrides that message. Separators never
activate. Children remain browsable even when their parent is unavailable.
The caller converts ``ui._caption_tree()`` using ``views.caption_action`` and
captures the target viewport in its callback; this module never consults the
current viewport to execute an action.

``draw(context)`` draws the intersection with each WINDOW region. ``register``
installs it for SpaceView3D and SpaceInfo; do not also call it from ui draw passes.
``open`` accepts optional ``context``, ``window_size`` and ``scale`` keywords.
For bpy-free tests use ``PopupModel(window_size, scale, measure)`` directly.
Keep routing events after dismissal: matching mouse/key releases are consumed.

MEASURED: v4 HTML .popup CSS and title.onclick define the anchor, palette,
minimum width, padding, radius and height limit. OURS: adjacent stacked submenus,
keyboard navigation, scroll buttons and click-through protection. Python is
intentional Blender/UI orchestration: it retains Python action payloads/callbacks
and uses Blender's font measurement and event objects, not a computational core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

FONT_SIZE = 12.0
ROW_HEIGHT = 22.0
SEPARATOR_HEIGHT = 9.0
PADDING = 4.0
TITLE_HEIGHT = 22.0
SCROLL_HEIGHT = 14.0
MIN_WIDTH = 200.0
MAX_HEIGHT = 0.8
ANCHOR_GAP = 3.0


def _clamp(value, low, high):
    return max(low, min(value, high))


def _inside(rect, x, y):
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def _intersection(a, b):
    rect = (max(a[0], b[0]), max(a[1], b[1]),
            min(a[2], b[2]), min(a[3], b[3]))
    return rect if rect[0] < rect[2] and rect[1] < rect[3] else None


def region_rect(rect, origin):
    """Translate only at paint time; the model never depends on a region."""
    x, y = origin
    return rect[0] - x, rect[1] - y, rect[2] - x, rect[3] - y


def _rows_copy(rows):
    # Copy the dictionaries, not opaque action payloads (which may contain RNA).
    result = []
    for row in rows:
        item = dict(row)
        if item.get('children') is not None:
            item['children'] = _rows_copy(item['children'])
        result.append(item)
    return tuple(result)


@dataclass
class Panel:
    rows: tuple
    title: str = ''
    parent_index: int | None = None
    selected: int | None = None
    offset: float = 0.0
    rect: tuple = (0, 0, 0, 0)
    body: tuple = (0, 0, 0, 0)
    row_rects: list = field(default_factory=list)
    max_scroll: float = 0.0
    up: tuple | None = None
    down: tuple | None = None
    width: float = MIN_WIDTH


class PopupModel:
    """Small event/layout model. ``measure(text, size)`` returns pixel width."""

    def __init__(self, window_size, scale=1.0, measure=None):
        self.scale = float(scale)
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError('scale must be positive and finite')
        self.measure = measure or (lambda text, size: len(text) * size * 0.6)
        self.window_size = self._size(window_size)
        self.anchor = (0.0, 0.0)
        self.panels = []
        self.callback = None
        self._releases = set()
        self._pressed = None

    @staticmethod
    def _size(size):
        w, h = map(float, size)
        if not all(math.isfinite(v) and v > 0 for v in (w, h)):
            raise ValueError('window_size must contain positive finite dimensions')
        return w, h

    @property
    def is_open(self):
        return bool(self.panels)

    def open(self, rows, anchor, title, callback):
        """Anchor is the title's (left, bottom), not the mouse or region point."""
        if not callable(callback):
            raise TypeError('callback(action, explanation) is required')
        x, y = map(float, anchor)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('anchor must be finite')
        panel = self._panel(_rows_copy(rows), str(title or ''))
        self.anchor = (x, y)
        self.callback = callback
        self.panels = [panel]
        self._pressed = None
        self.layout()
        return self

    def close(self):
        """Dismiss, retaining release guards until their originating presses end."""
        self.panels.clear()
        self._pressed = None
        self.callback = None

    def _panel(self, rows, title='', parent_index=None):
        widths = [self.measure(title, FONT_SIZE * self.scale)]
        for row in rows:
            widths.append(self.measure(str(row.get('label', '')), FONT_SIZE * self.scale)
                          + self.measure(str(row.get('shortcut', '')), FONT_SIZE * self.scale))
        return Panel(rows=rows, title=title, parent_index=parent_index,
                     width=max(MIN_WIDTH * self.scale, max(widths) + 64 * self.scale))

    def resize(self, window_size):
        size = self._size(window_size)
        if size != self.window_size:
            self.window_size = size
            self._pressed = None
            self.layout()

    def layout(self):
        w, h = self.window_size
        s = self.scale
        pad = PADDING * s
        # Keep a border margin, including in extremely small windows.
        margin = min(pad, w / 4, h / 4)
        for depth, panel in enumerate(self.panels):
            heights = [(SEPARATOR_HEIGHT if row.get('separator') else ROW_HEIGHT) * s
                       for row in panel.rows]
            content = sum(heights)
            header = TITLE_HEIGHT * s if panel.title else 0.0
            height = min(content + header + 2 * pad, MAX_HEIGHT * h, h - 2 * margin)
            header = min(header, height * 0.3)
            inset = min(pad, height * 0.1)
            body_height = max(0.0, height - header - 2 * inset)
            scrolling = content > body_height
            scroll_h = min(SCROLL_HEIGHT * s, body_height / 4) if scrolling else 0.0
            width = min(panel.width, w - 2 * margin)
            if depth == 0:
                x, top = self.anchor[0], self.anchor[1] - ANCHOR_GAP * s
            else:
                parent = self.panels[depth - 1]
                row_rect = parent.row_rects[panel.parent_index]
                x, top = parent.rect[2], min(row_rect[3] + inset, parent.body[3])
                if x + width > w - margin:
                    x = parent.rect[0] - width
            x = _clamp(x, margin, w - margin - width)
            top = _clamp(top, margin + height, h - margin)
            panel.rect = (x, top - height, x + width, top)
            bottom, body_top = top - height + inset, top - inset - header
            panel.up = (x, body_top - scroll_h, x + width, body_top) if scrolling else None
            panel.down = (x, bottom, x + width, bottom + scroll_h) if scrolling else None
            panel.body = (x + min(1 * s, width / 4), bottom + scroll_h,
                          x + width - min(1 * s, width / 4), body_top - scroll_h)
            visible = max(0.0, panel.body[3] - panel.body[1])
            panel.max_scroll = max(0.0, content - visible)
            panel.offset = _clamp(panel.offset, 0, panel.max_scroll)
            y = panel.body[3] + panel.offset
            panel.row_rects = []
            for row_height in heights:
                panel.row_rects.append((panel.body[0], y - row_height, panel.body[2], y))
                y -= row_height

    def hit_test(self, x, y):
        """Return (depth, row index or 'up'/'down'/None), topmost panel first."""
        for depth in range(len(self.panels) - 1, -1, -1):
            panel = self.panels[depth]
            if not _inside(panel.rect, x, y):
                continue
            if panel.up and _inside(panel.up, x, y):
                return depth, 'up'
            if panel.down and _inside(panel.down, x, y):
                return depth, 'down'
            if _inside(panel.body, x, y):
                for index, rect in enumerate(panel.row_rects):
                    if _inside(rect, x, y):
                        return depth, index
            return depth, None
        return None

    def _submenu(self, depth, index):
        panel = self.panels[depth]
        children = panel.rows[index].get('children')
        if not children:
            del self.panels[depth + 1:]
            return False
        if len(self.panels) > depth + 1 and self.panels[depth + 1].parent_index == index:
            return True
        del self.panels[depth + 1:]
        self.panels.append(self._panel(children, parent_index=index))
        self.layout()
        return True

    def _activate(self, depth, index):
        row = self.panels[depth].rows[index]
        if row.get('separator'):
            return
        if self._submenu(depth, index):
            return
        callback = self.callback
        action = row.get('action')
        unavailable = not row.get('enabled', True) or action is None
        explanation = (row.get('explanation') or
                       f"{row.get('label', 'This item')}: not implemented yet") if unavailable else None
        self.close()  # Callbacks may rebuild UI or open another popup.
        callback(None if unavailable else action, explanation)

    def _scroll(self, depth, delta):
        panel = self.panels[depth]
        panel.offset = _clamp(panel.offset + delta, 0, panel.max_scroll)
        panel.selected = None
        self._pressed = None
        del self.panels[depth + 1:]
        self.layout()

    def _select(self, panel, index):
        panel.selected = index
        rect = panel.row_rects[index]
        if rect[3] > panel.body[3]:
            panel.offset -= rect[3] - panel.body[3]
        elif rect[1] < panel.body[1]:
            panel.offset += panel.body[1] - rect[1]
        self.layout()

    def _keyboard(self, key):
        panel = self.panels[-1]
        choices = [i for i, row in enumerate(panel.rows) if not row.get('separator')]
        if key == 'ESC':
            self.close()
        elif key == 'LEFT_ARROW' and len(self.panels) > 1:
            self.panels.pop()
        elif key in {'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'} and choices:
            if key in {'HOME', 'END'}:
                index = choices[0 if key == 'HOME' else -1]
            elif panel.selected not in choices:
                index = choices[0 if key == 'DOWN_ARROW' else -1]
            else:
                step = 1 if key == 'DOWN_ARROW' else -1
                index = choices[(choices.index(panel.selected) + step) % len(choices)]
            self._select(panel, index)
        elif key in {'RET', 'NUMPAD_ENTER', 'RIGHT_ARROW'} and panel.selected is not None:
            depth, index = len(self.panels) - 1, panel.selected
            if key == 'RIGHT_ARROW':
                if self._submenu(depth, index):
                    self._keyboard('HOME')
            else:
                self._activate(depth, index)
        elif key in {'PAGE_UP', 'PAGE_DOWN'}:
            direction = -1 if key == 'PAGE_UP' else 1
            self._scroll(len(self.panels) - 1, direction * (panel.body[3] - panel.body[1]))

    def handle_event(self, event):
        """Blender-shaped event -> consumed. Uses mouse_x/y, NEVER mouse_region_x/y.

        Left click activates only after its matching press and release on the
        same leaf. Every mouse button is shielded, not just the left button.
        TIMER/window housekeeping passes through; keyboard input stays modal.
        """
        key, value = event.type, event.value
        guarded = key in self._releases
        if value == 'RELEASE' and guarded:
            self._releases.discard(key)
        if not self.is_open:
            return guarded
        if key in {'TIMER', 'TIMER0', 'TIMER1', 'TIMER2', 'WINDOW_DEACTIVATE'}:
            if key == 'WINDOW_DEACTIVATE':
                self.close()
                self._releases.clear()
            return False
        if key in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if not self._pressed:
                hit = self.hit_test(event.mouse_x, event.mouse_y)
                if hit and isinstance(hit[1], int):
                    depth, index = hit
                    panel = self.panels[depth]
                    panel.selected = None if panel.rows[index].get('separator') else index
                    self._submenu(depth, index)
            return True
        if key.startswith('WHEEL'):
            hit = self.hit_test(event.mouse_x, event.mouse_y)
            if value == 'PRESS':
                depth = hit[0] if hit else len(self.panels) - 1
                direction = -1 if key in {'WHEELUPMOUSE', 'WHEELINMOUSE'} else 1
                self._scroll(depth, direction * 3 * ROW_HEIGHT * self.scale)
            return True
        if key.endswith('MOUSE') or key in {'PEN', 'ERASER'}:
            hit = self.hit_test(event.mouse_x, event.mouse_y)
            if value in {'PRESS', 'DOUBLE_CLICK'}:
                self._releases.add(key)
                self._pressed = None
                if hit is None:
                    self.close()
                elif key == 'LEFTMOUSE':
                    depth, index = hit
                    if index in {'up', 'down'}:
                        self._scroll(depth, (-1 if index == 'up' else 1) * ROW_HEIGHT * self.scale)
                    elif isinstance(index, int) and not self.panels[depth].rows[index].get('separator'):
                        self.panels[depth].selected = index
                        if not self._submenu(depth, index):
                            self._pressed = (key, hit)
            elif value == 'RELEASE':
                pressed = self._pressed
                self._pressed = None
                if pressed == (key, hit) and hit is not None:
                    self._activate(*hit)
            return True
        if key in {'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM'}:
            return True
        if value == 'PRESS':
            self._releases.add(key)
            self._keyboard(key)
        return value in {'PRESS', 'RELEASE', 'CLICK', 'DOUBLE_CLICK'} or guarded


_model = None
_window = None
_handlers = []


def _redraw():
    if _window is not None:
        for area in _window.screen.areas:
            area.tag_redraw()


def open(rows, anchor, title, callback, *, context=None, window_size=None, scale=None):
    """Open in context.window (defaults to bpy.context); return the model.

    With explicit window_size, context may be omitted for pure callers. Runtime
    callers should pass context or omit window_size, to bind to the owner window.
    """
    global _model, _window
    if context is None and window_size is None:
        import bpy
        context = bpy.context
    window = getattr(context, 'window', None)
    if window_size is None:
        window_size = (window.width, window.height)
    measure = None
    if context is not None:
        from . import ui, uiscale
        measure = ui._text_width
        if scale is None:
            scale = uiscale.current()
    model = PopupModel(window_size, 1.0 if scale is None else scale, measure)
    model.open(rows, anchor, title, callback)
    if _model is not None and _window == window:
        model._releases.update(_model._releases)
    _redraw()
    _model, _window = model, window
    _redraw()
    return model


def is_open():
    return _model is not None and _model.is_open


def close():
    if _model is not None:
        _model.close()
        _redraw()


def handle_event(event, context=None):
    """Call before ANY picker, even when is_open() is false (release shielding)."""
    if _model is None:
        return False
    if _window is not None:
        if context is None:
            import bpy
            context = bpy.context
        if context.window != _window:
            return False
        _model.resize((_window.width, _window.height))
    try:
        return _model.handle_event(event)
    finally:
        if event.type not in {'TIMER', 'TIMER0', 'TIMER1', 'TIMER2'}:
            _redraw()


def draw(context, window_level=False):
    """Paint across the composed window when supported, with a region fallback."""
    if not is_open() or (_window is not None and context.window != _window):
        return
    import blf
    import gpu
    from . import design, ui
    region = ui._overlay_paint_region(True) if window_level else context.region
    if region is None or region.type != 'WINDOW':
        return

    model = _model
    model.resize((region.width, region.height) if window_level else
                 (context.window.width, context.window.height))
    shader = ui._uniform_color_shader()
    if shader is None:
        return
    origin = (region.x, region.y)
    bounds = (region.x, region.y, region.x + region.width, region.y + region.height)
    color = lambda name: design.rgb01(getattr(design, name))
    s = model.scale
    font = ui._DEFAULT_FONT_ID

    def text(x, y, label, clip, ink, size=FONT_SIZE):
        clip = _intersection(clip, bounds)
        if clip is None:
            return
        blf.enable(font, blf.CLIPPING)
        try:
            blf.clipping(font, *region_rect(clip, origin))
            ui._draw_text(x - origin[0], y - origin[1], str(label), size * s, ink)
        finally:
            blf.disable(font, blf.CLIPPING)

    def rect(box, ink):
        box = _intersection(box, bounds)
        if box:
            shader.bind()
            ui._draw_rect(shader, *region_rect(box, origin), ink)

    previous = gpu.state.blend_get()
    gpu.state.blend_set('ALPHA')
    try:
        for panel in model.panels:
            if not _intersection(panel.rect, bounds):
                continue
            shader.bind()
            ui._draw_round_rect(shader, *region_rect(panel.rect, origin),
                                design.CHIP_RADIUS * s, color('MENU_BG'),
                                border=color('BORDER_STRONG'), border_width=s)
            x0, y0, x1, y1 = panel.rect
            if panel.title:
                text(x0 + 10*s, y1 - (PADDING + 14)*s, panel.title,
                     panel.rect, color('TEXT_MUTED'), 11)
            for index, row in enumerate(panel.rows):
                box = panel.row_rects[index]
                visible = _intersection(box, panel.body)
                if not visible:
                    continue
                if row.get('separator'):
                    cy = (box[1] + box[3]) / 2
                    line = _intersection((x0 + 4*s, cy, x1 - 4*s, cy + s), panel.body)
                    if line:
                        rect(line, color('BORDER'))
                    continue
                selected = panel.selected == index
                if selected:
                    rect(visible, color('ACCENT'))
                available = row.get('enabled', True) and (row.get('children') or row.get('action') is not None)
                ink = color('TEXT_DISABLED' if not available else
                            'BAR_TEXT_STRONG' if selected else 'TEXT')
                baseline = box[1] + 6*s
                right = x1 - 14*s
                if row.get('children'):
                    text(right - 2*s, baseline, '›', visible, ink)
                    right -= 16*s
                shortcut = str(row.get('shortcut', ''))
                if shortcut:
                    tw = ui._text_width(shortcut, FONT_SIZE*s)
                    sx = max(x0 + 28*s, right - tw)
                    text(sx, baseline, shortcut, visible, ink)
                    right = sx - 18*s
                label_clip = _intersection(visible, (x0 + 28*s, box[1], right, box[3]))
                if label_clip:
                    text(x0 + 28*s, baseline, row.get('label', ''), label_clip, ink)
                if row.get('checked'):
                    text(x0 + 9*s, baseline, '✓', visible, ink)
            for box, symbol, active in ((panel.up, '▴', panel.offset > 0),
                                         (panel.down, '▾', panel.offset < panel.max_scroll)):
                if box:
                    text((x0 + x1)/2 - 4*s, box[1] + 2*s, symbol, box,
                         color('TEXT' if active else 'TEXT_DISABLED'))
    finally:
        gpu.state.blend_set(previous)


def _draw_current():
    import bpy
    draw(bpy.context)


def _draw_window():
    import bpy
    draw(bpy.context, window_level=True)


def register():
    """Install POST_PIXEL handlers once, after other shell overlays."""
    if _handlers:
        return
    import bpy
    try:
        window_type = getattr(bpy.types, 'Window', None)
        if window_type is not None and hasattr(window_type, 'draw_handler_add'):
            _handlers.append((window_type, window_type.draw_handler_add(_draw_window, (), 'POST_PIXEL')))
        else:
            for space in (bpy.types.SpaceView3D, bpy.types.SpaceInfo):
                token = space.draw_handler_add(_draw_current, (), 'WINDOW', 'POST_PIXEL')
                _handlers.append((space, token))
    except Exception:
        unregister()
        raise


def unregister():
    """Remove handlers and forget all owner/model state; safe to call twice."""
    global _model, _window
    close()
    import bpy
    for space, token in _handlers:
        if space is getattr(bpy.types, 'Window', None):
            space.draw_handler_remove(token)
        else:
            space.draw_handler_remove(token, 'WINDOW')
    _handlers.clear()
    _model = _window = None

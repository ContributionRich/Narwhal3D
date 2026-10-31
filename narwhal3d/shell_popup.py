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
For bpy-free tests use ``native_popup.NativePopup`` directly.
Keep routing events after dismissal: matching mouse/key releases are consumed.

MEASURED: v4 HTML .popup CSS and title.onclick define the anchor, palette,
minimum width, padding, radius and height limit. OURS: adjacent stacked submenus,
keyboard navigation, scroll buttons and click-through protection. Python is
intentional Blender/UI orchestration: it retains Python action payloads/callbacks
and Blender font/draw glue. The runtime factory uses persistent Rust popup state
exclusively; missing/invalid ui_core fails explicitly, with no Python fallback.
"""
from __future__ import annotations


FONT_SIZE = 12.0
ROW_HEIGHT = 22.0
SEPARATOR_HEIGHT = 9.0
PADDING = 4.0
TITLE_HEIGHT = 22.0
SCROLL_HEIGHT = 14.0
MIN_WIDTH = 200.0
MAX_HEIGHT = 0.8
ANCHOR_GAP = 3.0



def _intersection(a, b):
    rect = (max(a[0], b[0]), max(a[1], b[1]),
            min(a[2], b[2]), min(a[3], b[3]))
    return rect if rect[0] < rect[2] and rect[1] < rect[3] else None


def region_rect(rect, origin):
    """Translate only at paint time; the model never depends on a region."""
    x, y = origin
    return rect[0] - x, rect[1] - y, rect[2] - x, rect[3] - y


def _make_model(window_size, scale=1.0, measure=None):
    from .native_popup import model
    return model(window_size, scale, measure)


_model = None
_window = None
_handlers = []
# Emergency-only escrow after an owner fails. Normal guards remain Rust-owned.
_failure_releases = set()


def _report_failure(exc):
    message = f'Popup failed ({type(exc).__name__}: {exc}); not replayed'
    try:
        from . import ui
        ui.app_state.log(message)
    except Exception:
        import sys
        print(message, file=sys.stderr)


def _detach_failed(model, releases, exc):
    global _model
    # An activation callback may have published a replacement before raising.
    if _model is model:
        _model = None
        _failure_releases.update(releases)
    try:
        model.dispose()
    except Exception:
        pass  # Reporting/cleanup must not kill the command-input modal either.
    _report_failure(exc)
    _redraw()


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
    if window is not None:
        # An overlap pick can be the first popup in a fresh session. Do not open
        # an input-consuming model before its paint handlers exist.
        register()
    if window_size is None:
        window_size = (window.width, window.height)
    measure = None
    if context is not None:
        from . import ui, uiscale
        measure = ui._text_width
        if scale is None:
            scale = uiscale.current()
    model = _make_model(window_size, 1.0 if scale is None else scale, measure)
    try:
        model.open(rows, anchor, title, callback)
        if _model is not None and _window == window and not getattr(_model, 'failed', False):
            model.inherit_releases(_model)
    except BaseException:
        model.dispose()
        raise
    _redraw()
    if _model is not None:
        _model.dispose()
    if _window != window:
        _failure_releases.clear()
    _model, _window = model, window
    _redraw()
    return model


def is_open():
    return _model is not None and _model.is_open


def close():
    model = _model
    if model is not None:
        releases = set()
        try:
            releases = set(model._releases)
            model.close()
        except Exception as exc:
            _detach_failed(model, releases, exc)
        else:
            _redraw()


def handle_event(event, context=None):
    """Call before ANY picker, even when is_open() is false (release shielding)."""
    if _model is None and not _failure_releases:
        return False
    if _window is not None:
        if context is None:
            import bpy
            context = bpy.context
        if context.window != _window:
            return False
    guarded = event.type in _failure_releases
    if event.value == 'RELEASE':
        _failure_releases.discard(event.type)
    if event.type == 'WINDOW_DEACTIVATE':
        _failure_releases.clear()
    model = _model
    if model is None:
        return guarded
    releases = set()
    was_open = model.is_open
    try:
        # Snapshot BEFORE resize/event: a native failure destroys the owner, and
        # its partially mutated state must never be read back or replayed.
        releases = set(model._releases)
        if _window is not None:
            model.resize((_window.width, _window.height))
        return model.handle_event(event) or guarded
    except Exception as exc:
        # The failing press itself also belongs to the popup, even when resize
        # failed before Rust saw it. Do not pass it (or its release) to a picker.
        if event.value == 'RELEASE':
            releases.discard(event.type)
        elif was_open and event.value in {'PRESS', 'DOUBLE_CLICK'} and not (
                event.type.startswith(('TIMER', 'WHEEL')) or event.type in {
                    'WINDOW_DEACTIVATE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE',
                    'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM'}):
            releases.add(event.type)
        _detach_failed(model, releases, exc)
        return True
    finally:
        if event.type not in {'TIMER', 'TIMER0', 'TIMER1', 'TIMER2'}:
            _redraw()


def _safe_draw(context, window_level=False):
    """Contain registered draw-handler failures just like modal-event failures."""
    model = _model
    if model is None or (_window is not None and context.window != _window):
        return
    releases = set()
    try:
        if not model.is_open:
            return
        releases = set(model._releases)
        draw(context, window_level)
    except Exception as exc:
        _detach_failed(model, releases, exc)


def draw(context, window_level=False):
    """Low-level painter; registered handlers contain failures in _safe_draw."""
    if not is_open() or (_window is not None and context.window != _window):
        return
    model = _model
    import blf
    import gpu
    from . import design, ui
    region = ui._overlay_paint_region(True) if window_level else context.region
    if region is None or region.type != 'WINDOW':
        return

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
    _safe_draw(bpy.context)


def _draw_window():
    import bpy
    _safe_draw(bpy.context, window_level=True)


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
    if _model is not None:
        _model.dispose()
    _model = _window = None
    _failure_releases.clear()

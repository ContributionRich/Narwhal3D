"""Persistent popup ABI bridge; Python owns opaque payloads, BLF calls and GC only.

OURS: typed additive popup ABI 1, independent of editor ABI 1/State88. Structural
rows upload once on open; events return 48 bytes. Panel/rectangle projections are
read-only, revision checked and cached, never replayed into the native model.
"""
import ctypes as C
import math
import weakref
from types import MappingProxyType

from . import native_textfield

MAX_ROWS, MAX_DEPTH, GUARD_BYTES = 65536, 64, 512 * 65
Rect = C.c_double * 4
Measure = C.CFUNCTYPE(C.c_uint32, C.c_int64, C.c_uint32, C.c_double, C.POINTER(C.c_double))


class Row(C.Structure):
    _fields_ = [('child_start', C.c_uint64), ('child_count', C.c_uint64), ('separator', C.c_uint64)]


class Request(C.Structure):
    _fields_ = [('op', C.c_uint32), ('value', C.c_uint32), ('index', C.c_int64),
                ('x', C.c_double), ('y', C.c_double), ('scale', C.c_double),
                ('source', C.c_uint64), ('root_count', C.c_uint64), ('title', C.c_uint64)]


class Reply(C.Structure):
    _fields_ = [('handle', C.c_uint64), ('revision', C.c_uint64),
                ('layout_revision', C.c_uint64), ('panels', C.c_uint64),
                ('action', C.c_int64), ('consumed', C.c_uint64)]


class PanelState(C.Structure):
    _fields_ = [('start', C.c_uint64), ('count', C.c_uint64), ('parent', C.c_int64),
                ('selected', C.c_int64), ('rect', Rect), ('body', Rect), ('up', Rect),
                ('down', Rect), ('offset', C.c_double), ('max_scroll', C.c_double),
                ('width', C.c_double), ('scrolling', C.c_uint64)]


_bound = None


def library():
    """Reuse the editor loader/path, but do not alter any editor signatures."""
    global _bound
    lib = native_textfield.library()
    if lib is None:
        raise RuntimeError('required native UI popup core is unavailable')
    if lib is _bound:
        return lib
    try:
        lib.narwhal_popup_abi.argtypes = []
        lib.narwhal_popup_abi.restype = C.c_uint32
        if lib.narwhal_popup_abi() != 1:
            raise RuntimeError('popup ABI mismatch')
        signatures = {
            'apply': [C.c_uint64, C.POINTER(Request), C.c_size_t, C.c_char_p, C.c_size_t,
                      C.POINTER(Row), C.c_size_t, Measure, C.POINTER(Reply), C.c_size_t],
            'destroy': [C.c_uint64],
            'panel': [C.c_uint64, C.c_uint64, C.c_size_t, C.POINTER(PanelState), C.c_size_t],
            'rows': [C.c_uint64, C.c_uint64, C.c_size_t, C.c_size_t, C.POINTER(Rect), C.c_size_t],
            'hit': [C.c_uint64, C.c_uint64, C.c_double, C.c_double, C.POINTER(C.c_int64), C.POINTER(C.c_int64)],
            'guards': [C.c_uint64, C.c_uint64, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)],
        }
        for name, args in signatures.items():
            func = getattr(lib, 'narwhal_popup_' + name)
            func.argtypes, func.restype = args, C.c_uint32
    except (AttributeError, RuntimeError) as exc:
        raise RuntimeError('cannot load configured/installed popup core') from exc
    _bound = lib
    return lib


def model(window_size, scale, measure):
    """Construct the sole popup owner; absence is an error, never a fallback."""
    lib = library()
    if lib is None:
        raise RuntimeError('required native UI popup core is unavailable')
    return NativePopup(window_size, scale, measure, lib=lib)


def _dimension(value):
    value = float(value)
    if not math.isfinite(value) or not 0 < value <= 1e12:
        raise ValueError('popup dimensions must be positive finite and <= 1e12')
    return value


def _tree(rows):
    # Host dictionary copies preserve opaque RNA/action identities. Never serialize
    # actions, labels or explanations through the native ABI.
    flat, descriptors = [], []

    def visit(source, depth):
        if depth > MAX_DEPTH:
            raise ValueError('popup tree exceeds 64 panel levels')
        start = len(flat)
        copied = []
        for row in source:
            # Check before copying, including for unsized/infinite iterables.
            if start + len(copied) >= MAX_ROWS:
                raise ValueError('popup tree exceeds 65536 rows')
            copied.append(dict(row))
        flat.extend(copied)
        descriptors.extend(Row() for _ in copied)
        for index, item in enumerate(copied, start):
            children = item.get('children')
            child_start, child_count = 0, 0
            if children is not None:
                child_start = len(flat)
                item['children'] = visit(children, depth + 1)
                child_count = len(item['children'])
            descriptors[index] = Row(child_start if child_count else 0, child_count,
                                     bool(item.get('separator')))
            flat[index] = MappingProxyType(item)
        return tuple(flat[start:start + len(copied)])

    root = visit(rows, 1)
    return root, tuple(flat), (Row * len(descriptors))(*descriptors)


class NativePanel:
    """Live read-only view; retained panel references follow native revisions."""
    def __init__(self, owner, depth, state):
        self._owner = weakref.ref(owner)
        self._epoch = owner._epoch
        self._depth, self._state = depth, state
        key = (state.start, state.count)
        if key not in owner._row_bindings:
            owner._row_bindings[key] = owner._flat[state.start:state.start + state.count]
        self.rows = owner._row_bindings[key]
        self.title = owner._title if depth == 0 else ''
        self.parent_index = None if state.parent == -1 else state.parent

    def _current(self):
        owner = self._owner()
        if owner is None or owner._epoch != self._epoch:
            raise RuntimeError('stale popup panel projection')
        owner._ready()
        panels = owner.panels
        if self._depth >= len(panels) or panels[self._depth]._state.start != self._state.start:
            raise RuntimeError('stale popup panel projection')
        return owner, panels[self._depth]._state

    @property
    def rect(self):
        return tuple(self._current()[1].rect)

    @property
    def body(self):
        return tuple(self._current()[1].body)

    @property
    def up(self):
        p = self._current()[1]
        return tuple(p.up) if p.scrolling else None

    @property
    def down(self):
        p = self._current()[1]
        return tuple(p.down) if p.scrolling else None

    @property
    def offset(self):
        return self._current()[1].offset

    @property
    def max_scroll(self):
        return self._current()[1].max_scroll

    @property
    def width(self):
        return self._current()[1].width

    @property
    def selected(self):
        p = self._current()[1]
        return None if p.selected == -1 else p.selected

    @selected.setter
    def selected(self, index):
        owner, _ = self._current()
        owner._apply(6, value=self._depth, index=-1 if index is None else index)

    @property
    def row_rects(self):
        owner, state = self._current()
        row_key = (owner._state.layout_revision, self._depth, state.start, state.count)
        cached = owner._row_cache.get(row_key)
        if cached is not None:
            return cached
        count = state.count
        buf = (Rect * count)()
        owner._check(owner.lib.narwhal_popup_rows(owner.handle, owner._state.revision,
                     self._depth, 0, buf, count))
        result = tuple(tuple(r) for r in buf)
        if any(not math.isfinite(v) for r in result for v in r):
            owner._fail('invalid popup row projection')
        owner.downloaded_rows += count * C.sizeof(Rect)
        owner._row_cache[row_key] = result
        return result


class NativePopup:
    """One UI-thread-confined native owner, including after dismissal for guards.

    Arbitrary mutation of projected row dictionaries/layout is not supported.
    Call open/resize/handle_event instead. Host callbacks may reopen after action
    dispatch; recursive mutation during measurement fails explicitly, without retry.
    """
    _backend_reason = 'native'

    def __init__(self, window_size, scale=1.0, measure=None, *, lib=None):
        self.lib = library() if lib is None else lib
        if self.lib is None:
            raise RuntimeError('NativePopup requires the UI core')
        self.scale = _dimension(scale)
        if self.scale > 1e6:
            raise ValueError('popup scale exceeds native bound')
        self.window_size = tuple(_dimension(v) for v in window_size)
        if len(self.window_size) != 2:
            raise ValueError('window_size must have two dimensions')
        # Same complete-string estimate as the bpy-free reference ONLY when the
        # caller supplies no measurer. Runtime shell.open always passes ui._text_width.
        self.measure = measure or (lambda text, size: len(text) * size * .6)
        self.anchor = (0.0, 0.0)
        self.callback = None
        self._title, self._flat = '', ()
        self._row_bindings = {}
        self._state = None
        self._epoch = 0
        self._busy = self.failed = False
        self._panels_revision, self._panels_cache, self._row_cache = 0, (), {}
        self.calls = self.uploaded_rows = self.downloaded_rows = self.mutation_reply_bytes = 0
        self._apply(0, x=self.window_size[0], y=self.window_size[1], scale=self.scale)
        self._finalizer = weakref.finalize(self, self.lib.narwhal_popup_destroy, self.handle)

    @property
    def handle(self):
        return self._state.handle if self._state is not None else 0

    def _ready(self):
        if self.failed or (hasattr(self, '_finalizer') and not self._finalizer.alive):
            raise RuntimeError('popup unavailable after failure/disposal; not replayed')
        if self._busy:
            raise RuntimeError('recursive popup operation during measurement; not replayed')

    def _drop_tree(self):
        self.callback = None
        self._flat, self._title = (), ''
        self._panels_cache, self._row_cache, self._row_bindings = (), {}, {}

    def dispose(self):
        if self._busy:
            raise RuntimeError('recursive popup disposal during measurement; not replayed')
        if hasattr(self, '_finalizer'):
            self._finalizer()
        self._drop_tree()

    def _fail(self, message):
        self.failed = True
        self.dispose()
        raise RuntimeError(message + '; not replayed')

    def _check(self, rc):
        if rc:
            self._fail(f'popup native operation/read failed ({rc})')

    def _apply(self, op, *, rows=None, key='', measurement=None, **kwargs):
        self._ready()
        request, out = Request(op=op, **kwargs), Reply()
        payload = key.encode('ascii')
        if len(payload) > 64:
            raise ValueError('popup event type exceeds native bound')
        error = []
        flat, title = measurement if measurement is not None else (self._flat, self._title)

        @Measure
        def measured(index, kind, size, width):
            try:
                if index == -1:
                    text = title
                elif index == -2:
                    text = ''
                elif 0 <= index < len(flat) and kind in (0, 1):
                    text = str(flat[index].get('label' if kind == 0 else 'shortcut', ''))
                else:
                    raise RuntimeError('invalid popup metric request')
                value = float(self.measure(text, size))
                if not math.isfinite(value) or abs(value) > 1e12:
                    raise ValueError('popup metric must be finite and bounded')
                width[0] = value
                return 0
            except BaseException as exc:
                error.append(exc)
                return 1

        self._busy = True
        try:
            self.calls += 1
            self.uploaded_rows += 0 if rows is None else C.sizeof(rows)
            rc = self.lib.narwhal_popup_apply(self.handle, C.byref(request), C.sizeof(request),
                 payload, len(payload), rows, 0 if rows is None else len(rows), measured,
                 C.byref(out), C.sizeof(out))
        except BaseException:
            self._busy = False
            self.failed = True
            self.dispose()
            raise
        finally:
            self._busy = False
        if error:
            self.failed = True
            self.dispose()
            raise error[0]
        self._check(rc)
        if (not out.handle or (op != 0 and out.handle != self.handle) or not out.revision
                or not 0 < out.layout_revision <= out.revision * 3
                or out.panels > MAX_DEPTH or out.consumed > 1 or out.action < -1
                or (out.action >= 0 and (op != 2 or out.action >= len(flat) or out.panels != 0))
                or (self._state is not None and (out.revision <= self._state.revision
                    or out.layout_revision < self._state.layout_revision))):
            self._fail('invalid popup reply')
        if self._state is None or out.layout_revision != self._state.layout_revision:
            self._row_cache.clear()
        self._state = out
        self.mutation_reply_bytes += C.sizeof(out)
        return out

    @property
    def is_open(self):
        return bool(not self.failed and self._finalizer.alive and self._state.panels)

    @property
    def panels(self):
        if not self.is_open:
            return ()
        self._ready()
        if self._panels_revision != self._state.revision:
            panels = []
            for depth in range(self._state.panels):
                p = PanelState()
                self._check(self.lib.narwhal_popup_panel(self.handle, self._state.revision,
                                                       depth, C.byref(p), C.sizeof(p)))
                if (p.start > len(self._flat) or p.count > len(self._flat) - p.start
                        or p.selected < -1 or p.selected >= p.count or p.scrolling > 1
                        or (depth == 0 and p.parent != -1)
                        or (depth > 0 and not 0 <= p.parent < len(panels[-1].rows))
                        or any(not math.isfinite(v) for r in (p.rect, p.body, p.up, p.down) for v in r)
                        or not all(math.isfinite(v) for v in (p.offset, p.max_scroll, p.width))
                        or not 0 <= p.offset <= p.max_scroll or p.width <= 0):
                    self._fail('invalid popup panel projection')
                panels.append(NativePanel(self, depth, p))
            self._panels_cache = tuple(panels)
            self._panels_revision = self._state.revision
        return self._panels_cache

    @property
    def _releases(self):
        self._ready()
        buf, written = C.create_string_buffer(GUARD_BYTES), C.c_size_t()
        self._check(self.lib.narwhal_popup_guards(self.handle, self._state.revision,
                                                buf, len(buf), C.byref(written)))
        if written.value > len(buf):
            self._fail('invalid popup guards projection')
        return frozenset(buf.raw[:written.value].decode('ascii').rstrip('\0').split('\0')) if written.value else frozenset()

    def inherit_releases(self, other):
        if isinstance(other, NativePopup) and other.lib is self.lib:
            other._ready()
            self._apply(7, source=other.handle)
        else:
            for key in other._releases:
                self._apply(8, key=key)

    def open(self, rows, anchor, title, callback):
        self._ready()
        if not callable(callback):
            raise TypeError('callback(action, explanation) is required')
        x, y = map(float, anchor)
        if not all(math.isfinite(v) and abs(v) <= 1e12 for v in (x, y)):
            raise ValueError('anchor must be finite and bounded')
        root, flat, descriptors = _tree(rows)
        title = str(title or '')
        self._apply(1, rows=descriptors, x=x, y=y, root_count=len(root), title=bool(title),
                    measurement=(flat, title))
        self._epoch += 1
        self._row_bindings = {(0, len(root)): root}
        self._flat, self._title, self.anchor, self.callback = flat, title, (x, y), callback
        return self

    def close(self):
        if self.failed or not self._finalizer.alive:
            return
        self._apply(4)
        self._drop_tree()

    def resize(self, window_size):
        self._ready()
        w, h = map(_dimension, window_size)
        if (w, h) != self.window_size:
            self._apply(3, x=w, y=h)
            self.window_size = (w, h)

    def layout(self):
        self._apply(5)

    def hit_test(self, x, y):
        self._ready()
        depth, part = C.c_int64(), C.c_int64()
        self._check(self.lib.narwhal_popup_hit(self.handle, self._state.revision, x, y,
                                              C.byref(depth), C.byref(part)))
        if depth.value == -1:
            return None
        if not 0 <= depth.value < self._state.panels or part.value < -3:
            self._fail('invalid popup hit projection')
        return depth.value, {-1: None, -2: 'up', -3: 'down'}.get(part.value, part.value)

    def handle_event(self, event):
        callback, flat = self.callback, self._flat
        value = {'NOTHING': 0, 'PRESS': 1, 'RELEASE': 2, 'CLICK': 3, 'DOUBLE_CLICK': 4}.get(event.value, 5)
        out = self._apply(2, key=event.type, value=value,
                          x=getattr(event, 'mouse_x', 0), y=getattr(event, 'mouse_y', 0))
        if not out.panels:
            self._drop_tree()
        if out.action >= 0:
            row = flat[out.action]
            action = row.get('action')
            unavailable = not row.get('enabled', True) or action is None
            explanation = (row.get('explanation') or
                           f"{row.get('label', 'This item')}: not implemented yet") if unavailable else None
            callback(None if unavailable else action, explanation)
        return bool(out.consumed)

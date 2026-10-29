"""Deferred overlap selection using the existing themed, window-level shell menu.

MEASURED: options/selection_menu documents multiple objects in the pick area and
row-hover highlighting. OURS: row labels, ordering and aperture use current app
pickers. Python is UI callback/lifecycle glue; candidate math is elsewhere.
"""
from . import shell_popup

_session = None


def close():
    global _session
    if _session is not None and shell_popup._model is _session['menu']:
        shell_popup.close()
    _session = None


def current():
    global _session
    st = _session
    if st is None:
        return None
    if shell_popup._model is not st['menu'] or not st['menu'].is_open:
        _session = None
        return None
    if not st['valid']():
        close()
        return None
    return st


def highlighted():
    st = current()
    if st is None:
        return None
    panel = st['menu'].panels[0]
    index = panel.selected
    return st['candidates'][index] if index is not None else None


def open(candidates, anchor, callback, valid, *, context=None, scale=1.0, window_size=None):
    """No selection changes until a leaf is confirmed. Cancel is a genuine no-op."""
    global _session
    close()
    if len(candidates) < 2:
        return False
    candidates = tuple(candidates)
    def choose(index, explanation):
        global _session
        _session = None
        if explanation is None and valid() and type(index) is int and 0 <= index < len(candidates):
            callback(candidates[index].key)
    rows = [{'label': c.label, 'action': i} for i, c in enumerate(candidates)]
    menu = shell_popup.open(rows, anchor, 'Choose object', choose, context=context,
                            scale=scale, window_size=window_size)
    _session = {'menu': menu, 'candidates': candidates, 'valid': valid}
    return True


def handle_event(event, context=None):
    st = current()
    if st is None:
        # Shell routing still owns matching releases after dismissal.
        return False
    return shell_popup.handle_event(event, context)

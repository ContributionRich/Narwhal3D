"""OURS: physical GUI checks for the centred workspace Rebuild and Print dialogs.

Standalone harness, loaded BEFORE launch/bootstrap.py, never under -b:
  blender --factory-startup --enable-event-simulate --python-exit-code 1 \
    --python spike/test_workspace_dialogs_gui.py --python launch/bootstrap.py
Requires a fresh absolute temporary NARWHAL3D_SETTINGS_FILE, NARWHAL3D_DEFER_FIRST_PRESENT=1
and the normal native environment. The scratch host /var/tmp/workspace_dialogs_host/host.py
sets these and bounds the run (see its README.md). Bound the process to 330 s (300 s watchdog).

Both dialogs are native window overlays centred on the UNION of the modeling panes, not on
the active pane. The union is computed here independently of `workspace_overlay`, so a broken
overlay cannot grade itself. Every control is reached by a real Window.event_simulate click or
key at window pixels; fixture setup and typed command starts are the only API use. Painted
output is observed read-only: the draw callbacks run with no area or region in context, and
every window-pixel rectangle the dialog paints is recorded and matched to the expected offset.

Negative controls (process-local, must go RED with their witness check):
  WORKSPACE_DIALOGS_MUTATION=pane-bounds   workspace_overlay.region returns one pane only
  WORKSPACE_DIALOGS_MUTATION=paint-origin  rebuild_popup._GpuPainter ignores its origin

Output: WORKSPACE_DIALOGS_RESULT JSON marker, and result.json plus screenshots in
WORKSPACE_DIALOGS_OUT (default artifacts/workspace_dialogs).
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('WORKSPACE_DIALOGS_OUT', str(ROOT / 'artifacts' / 'workspace_dialogs')))
START = time.monotonic()
MUTATION = os.environ.get('WORKSPACE_DIALOGS_MUTATION', '')
WITNESS = {
    'pane-bounds': 'rebuild: dialog is centred on the independent union of the panes',
    'paint-origin': 'rebuild: painted panel sits at the independent workspace offset',
}
RESULT = {'status': 'running', 'checks': [], 'facts': {}, 'clicks': [], 'callbacks': {},
          'paint': {}, 'shots': {}, 'mutation': MUTATION, 'witness': WITNESS.get(MUTATION)}
POSITION = (0, 0)
FINISH_LOCK = threading.Lock()
REBUILD_NAME = None
CIRCLE_NAME = None
CAPTURE = {'label': None, 'calls': []}
PAINTS = {}
PAINT_TOLERANCE_PX = 1.5


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('WORKSPACE_DIALOGS', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def fact(key, value):
    RESULT['facts'][key] = value
    print('WORKSPACE_DIALOGS FACT', key, value, flush=True)


def finish(code, error=None):
    if not FINISH_LOCK.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=round(time.monotonic() - START, 2))
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
        print('WORKSPACE_DIALOGS_RESULT ' + json.dumps(RESULT, default=str), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def window():
    return bpy.context.window_manager.windows[0]


def send(kind, value, x, y, unicode=None):
    kwargs = {} if unicode is None else {'unicode': unicode}
    window().event_simulate(type=kind, value=value, x=round(x), y=round(y), **kwargs)


def pane_window(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def union_of_panes():
    """The union of the VIEW_3D WINDOW regions, computed here (not via workspace_overlay)."""
    rects = [pane_window(a) for a in ui._view3d_areas()]
    x0, y0 = min(r.x for r in rects), min(r.y for r in rects)
    x1, y1 = max(r.x + r.width for r in rects), max(r.y + r.height for r in rects)
    return SimpleNamespace(x=x0, y=y0, width=x1 - x0, height=y1 - y0)


def pane_at(point):
    for area in ui._view3d_areas():
        r = pane_window(area)
        if r.x <= point[0] < r.x + r.width and r.y <= point[1] < r.y + r.height:
            return ui.pane_label(area)
    return None


def hover(point):
    global POSITION
    POSITION = (round(point[0]), round(point[1]))
    send('MOUSEMOVE', 'NOTHING', *POSITION)
    yield .1


def click(point, label):
    yield from hover(point)
    RESULT['clicks'].append({'control': label, 'at': POSITION, 'pane': pane_at(POSITION)})
    send('LEFTMOUSE', 'PRESS', *POSITION)
    yield .06
    send('LEFTMOUSE', 'RELEASE', *POSITION)
    yield .3


def press(kind, unicode=None):
    send(kind, 'PRESS', *POSITION, unicode=unicode)
    yield .05
    send(kind, 'RELEASE', *POSITION)
    yield .1


DIGIT_KEYS = dict(zip('0123456789', ('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX',
                                     'SEVEN', 'EIGHT', 'NINE')))


def type_text(text):
    for ch in text:
        yield from press(DIGIT_KEYS.get(ch, ch.upper()), unicode=ch)


def wait_until(predicate, label, seconds=8):
    until = time.monotonic() + seconds
    while not predicate():
        check(time.monotonic() < until, 'timed out: ' + label)
        yield .05


def centre(rect, region):
    return (region.x + rect.x + rect.w / 2, region.y + rect.y + rect.h / 2)


def centre_of_pane(area):
    r = pane_window(area)
    return (r.x + r.width / 2, r.y + r.height / 2)


def shot(label):
    path = OUT / f'{label}.png'
    OUT.mkdir(parents=True, exist_ok=True)
    yield .3
    for area in ui._view3d_areas():
        area.tag_redraw()
    result = bpy.ops.screen.screenshot(filepath=str(path))
    ok = 'FINISHED' in result and path.is_file() and path.stat().st_size > 0
    RESULT['shots'][label] = str(path)
    check(ok, f'screenshot {label} written')


def boundaries(union):
    xs, ys = set(), set()
    for area in ui._view3d_areas():
        r = pane_window(area)
        xs.add(r.x + r.width)
        ys.add(r.y + r.height)
    xs = sorted(x for x in xs if union.x < x < union.x + union.width)
    ys = sorted(y for y in ys if union.y < y < union.y + union.height)
    return xs, ys


def geometry(label, panel, origin):
    """The dialog the app laid out, against the independently computed union."""
    union = union_of_panes()
    px0, py0 = origin[0] + panel.x, origin[1] + panel.y
    px1, py1 = px0 + panel.w, py0 + panel.h
    cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
    ux, uy = union.x + union.width / 2, union.y + union.height / 2
    xs, ys = boundaries(union)
    crossed_x = [x for x in xs if px0 < x < px1]
    crossed_y = [y for y in ys if py0 < y < py1]
    active = pane_window(ui.active_pane_area())
    fact(label + ' geometry', {
        'union': [union.x, union.y, union.width, union.height],
        'panel': [round(px0, 1), round(py0, 1), round(px1, 1), round(py1, 1)],
        'centre_offset_px': [round(cx - ux, 2), round(cy - uy, 2)],
        'active_pane_centre': [active.x + active.width / 2, active.y + active.height / 2],
        'x_dividers_crossed': crossed_x, 'y_dividers_crossed': crossed_y})
    check(abs(cx - ux) <= 2 and abs(cy - uy) <= 2,
          f'{label}: dialog is centred on the independent union of the panes'
          if label == 'rebuild' else f'{label}: dialog is centred on the union of the panes')
    check(abs(cx - (active.x + active.width / 2)) > 50 or abs(cy - (active.y + active.height / 2)) > 50,
          f'{label}: centre differs from the active pane centre (not pane-centred)')
    check(bool(crossed_x) and bool(crossed_y),
          f'{label}: dialog crosses both the vertical and horizontal pane dividers')


def painted(label, expected):
    """The window-pixel rectangle the dialog actually painted, against the expectation."""
    want = [round(v, 2) for v in expected]
    calls = PAINTS.get(label) or []
    match = next((c for c in calls if all(abs(a - b) <= PAINT_TOLERANCE_PX
                                          for a, b in zip(c['bounds'], want))), None)
    RESULT['paint'][label] = {'expected_window_bounds': want,
                              'tolerance_px': PAINT_TOLERANCE_PX,
                              'painted_rects_this_frame': len(calls), 'matched': match}
    check(match is not None, f'{label}: painted panel sits at the independent workspace offset')


def expected_rect(local_panel, union):
    return (union.x + local_panel.x, union.y + local_panel.y,
            union.x + local_panel.x + local_panel.w, union.y + local_panel.y + local_panel.h)


def pane_snapshot():
    out = []
    for area in ui._view3d_areas():
        r = pane_window(area)
        rv3d = area.spaces.active.region_3d
        out.append({'label': ui.pane_label(area), 'rect': [area.x, area.y, area.width, area.height],
                    'window': [r.x, r.y, r.width, r.height],
                    'view_location': [round(v, 6) for v in rv3d.view_location],
                    'view_rotation': [round(v, 6) for v in rv3d.view_rotation],
                    'distance': round(rv3d.view_distance, 6)})
    return sorted(out, key=lambda p: p['label'])


def doc_snapshot():
    ctx = st.ctx
    names = sorted(o.Attributes.Name for o in st.model.file.Objects)
    return {'objects': names, 'undo': list(ctx.undo.undo_names()),
            'fingerprints': {n: bridge.curve_fingerprint(ctx.geom_by_name(n)) for n in names}}


def xyz(p):
    return (round(p.X, 6), round(p.Y, 6), round(p.Z, 6))


def cubic(c, t):
    s = 1 - t
    w = (s ** 3, 3 * s * s * t, 3 * s * t * t, t ** 3)
    return (sum(wi * p[0] for wi, p in zip(w, c)), sum(wi * p[1] for wi, p in zip(w, c)))


def ink_extent(page):
    pts = [cubic(c, i / 16) for s in page.strokes for c in s.beziers for i in range(17)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def record_callbacks(rp):
    """Read-only observation of the real draw path: callback contexts and painted rectangles."""
    def wrap_frame(module, attr, label, opened):
        original = getattr(module, attr)

        def probe(*args, **kwargs):
            if not opened():
                return original(*args, **kwargs)
            c = bpy.context
            tally = RESULT['callbacks'].setdefault(label, {'calls': 0, 'area_none': 0,
                                                           'region_none': 0})
            tally['calls'] += 1
            tally['area_none'] += getattr(c, 'area', None) is None
            tally['region_none'] += getattr(c, 'region', None) is None
            CAPTURE['label'], CAPTURE['calls'] = label, []
            try:
                return original(*args, **kwargs)
            finally:
                PAINTS[label] = list(CAPTURE['calls'])
                CAPTURE['label'] = None
        setattr(module, attr, probe)

    def wrap_rect(name, colour_index):
        original = getattr(ui, name)

        def probe(*args, **kwargs):
            if CAPTURE['label'] is not None and len(args) > colour_index:
                CAPTURE['calls'].append({
                    'fn': name, 'bounds': [round(v, 2) for v in args[1:5]],
                    'colour': [round(c, 3) for c in args[colour_index]]})
            return original(*args, **kwargs)
        setattr(ui, name, probe)

    wrap_rect('_draw_rect', 5)
    wrap_rect('_draw_round_rect', 6)
    wrap_frame(ui, 'draw_print_dialog', 'print', ui.print_dialog_open)
    wrap_frame(rp, 'paint_popup', 'rebuild', rp.is_open)


def install_mutation(rp):
    if not MUTATION:
        return
    if MUTATION == 'pane-bounds':
        from narwhal3d import workspace_overlay as wo
        original = wo.region

        def top_pane_only(ui_):
            if original(ui_) is None:
                return None
            r = pane_window(ui._pane_by_label('Top'))
            return wo.Workspace(r.x, r.y, r.width, r.height)
        wo.region = top_pane_only
    elif MUTATION == 'paint-origin':
        original_init = rp._GpuPainter.__init__

        def ignore_origin(self, ui_, origin=(0, 0)):
            original_init(self, ui_, (0, 0))
        rp._GpuPainter.__init__ = ignore_origin
    else:
        raise ValueError('unknown WORKSPACE_DIALOGS_MUTATION ' + MUTATION)
    fact('mutation installed', MUTATION)


def rebuild_flow(rp):
    yield from hover(centre_of_pane(ui.active_pane_area()))
    yield from type_text('Rebuild')
    yield from press('RET')
    yield from wait_until(lambda: rp.is_open() and ui._rebuild_window is not None,
                          'Rebuild popup opens from typed Rebuild')
    yield .25
    st_ = rp._popup
    region = rp._pane_region(ui, st_.pane_key)
    lay = rp._layout(st_, ui, region.width, region.height)
    geometry('rebuild', lay.dialog, (region.x, region.y))
    union = union_of_panes()
    lay_u = rp._layout(st_, ui, union.width, union.height)
    painted('rebuild', expected_rect(lay_u.dialog, union))
    fact('rebuild current labels', list(lay.current))
    check('6' in str(lay.current[0]) and '3' in str(lay.current[1]),
          'Rebuild shows the selected curve current counts (6 points, degree 3)')
    check('preview' not in lay.targets and 'preview' not in lay.rects,
          'Rebuild has no Preview control')
    check(not (ui._rebuild_window or {}).get('preview'), 'no preview computed on open')
    yield from shot('rebuild-open')

    before = doc_snapshot()
    yield from click(centre(lay.targets['point'], region), 'rebuild point field')
    yield from press('BACK_SPACE')
    yield from press('BACK_SPACE')
    yield from press('BACK_SPACE')
    yield from type_text('5')
    yield from press('RET')
    check(rp._popup is not None and rp._popup.draft.point_count == 5,
          'typing 5 and Enter sets the draft point count only')
    check(doc_snapshot() == before, 'count edit + Enter changes neither geometry nor Undo')
    check(not (ui._rebuild_window or {}).get('preview'), 'Enter does not fit a preview')
    yield from shot('rebuild-count-5')

    region = rp._pane_region(ui, rp._popup.pane_key)
    lay = rp._layout(rp._popup, ui, region.width, region.height)
    yield from click(centre(lay.targets['cancel'], region), 'rebuild cancel')
    yield from wait_until(lambda: not rp.is_open(), 'Cancel closes the popup')
    check(ui._rebuild_window is None and doc_snapshot() == before,
          'Cancel leaves objects, geometry and Undo unchanged')
    yield from shot('rebuild-cancelled')

    orig = st.ctx.geom_by_name(REBUILD_NAME)
    orig_ends = (xyz(orig.PointAt(orig.Domain.T0)), xyz(orig.PointAt(orig.Domain.T1)))
    yield from hover(centre_of_pane(ui.active_pane_area()))
    yield from type_text('Rebuild')
    yield from press('RET')
    yield from wait_until(lambda: rp.is_open(), 'Rebuild reopens')
    region = rp._pane_region(ui, rp._popup.pane_key)
    lay = rp._layout(rp._popup, ui, region.width, region.height)
    yield from click(centre(lay.targets['point'], region), 'rebuild point field (OK run)')
    yield from press('BACK_SPACE')
    yield from press('BACK_SPACE')
    yield from press('BACK_SPACE')
    yield from type_text('5')
    yield from press('RET')
    region = rp._pane_region(ui, rp._popup.pane_key)
    lay = rp._layout(rp._popup, ui, region.width, region.height)
    yield from click(centre(lay.targets['ok'], region), 'rebuild OK')
    yield from wait_until(lambda: not rp.is_open(), 'OK closes the popup')
    yield .3
    after = doc_snapshot()
    check(after['undo'] == ['Rebuild'] + before['undo'],
          f'OK records exactly one Rebuild Undo step ({after["undo"][:2]})')
    fact('objects before OK', before['objects'])
    fact('objects after OK', after['objects'])
    fits = [st.ctx.geom_by_name(n) for n in after['objects']]
    fits = [g for g in fits if g is not None and hasattr(g, 'Points')
            and len(g.Points) == 5 and g.Degree == 3]
    check(len(fits) == 1, f'OK fits exactly one curve to 5 points at degree 3 ({len(fits)})')
    new = fits[0]
    new_ends = (xyz(new.PointAt(new.Domain.T0)), xyz(new.PointAt(new.Domain.T1)))
    fact('rebuild endpoints before/after', {'before': orig_ends, 'after': new_ends})
    check(len(after['objects']) == len(before['objects']), 'OK does not add or remove objects')
    yield from shot('rebuild-after-ok')


def choose(rid, value, region, lay):
    """Open the field `rid` with a click, then click `value` in its list. Returns the layout."""
    row = next(r for r in lay.rows if r[0] == rid)
    yield from click(centre(row[2], region), f'print {rid} field')
    lay = ui._print_dialog_layout(region)
    check(ui.print_dialog_state().open_dropdown == rid and lay.dropdown,
          f'{rid} field opens its choice list')
    index = ui.print_dialog_state().choices(rid).index(value)
    item = next(r for i, r in lay.dropdown if i == index)
    yield from click(centre(item, region), f'print {rid} choice {value}')
    lay = ui._print_dialog_layout(region)
    check(getattr(ui.print_dialog_state(), rid) == value, f'{rid} is set to {value!r}')
    return lay


def print_flow():
    top = ui._pane_by_label('Top')
    ui.set_active_pane(top)
    ui._tag_redraw_viewports()
    yield .3
    st.ctx.select_by_names([CIRCLE_NAME])
    yield .3
    before = doc_snapshot()
    panes_before = pane_snapshot()
    yield from hover(centre_of_pane(top))
    yield from type_text('Print')
    yield from press('RET')
    yield from wait_until(ui.print_dialog_open, 'Print dialog opens from typed Print')
    yield .25
    d = ui._print_dialog
    dst = ui.print_dialog_state()
    check(dst.view == 'Top' and d['pane'] == ui._pane_key(top),
          f'Print detects the active Top pane (view={dst.view!r})')
    page = d['page']
    check(page is not None and len(page.strokes) >= 1,
          'Print builds a page for the single selected Top circle')
    region = ui._print_dialog_region()
    lay = ui._print_dialog_layout(region)
    geometry('print', lay.panel, (region.x, region.y))
    union = union_of_panes()
    painted('print', expected_rect(printdialog_layout(union, dst).panel, union))
    x0, y0, x1, y1 = ink_extent(page)
    fact('viewport print page', {'mm': [round(page.width_mm, 1), round(page.height_mm, 1)],
                                 'scale': round(page.scale, 4), 'strokes': len(page.strokes),
                                 'ink_mm': [round(v, 1) for v in (x0, y0, x1, y1)]})
    # Viewport is the default and is pane-bounded: the large circle exceeds the visible pane,
    # so the sheet clips it. That clip is the expected behaviour, not a full fit.
    check(x1 > 0 and x0 < page.width_mm and y1 > 0 and y0 < page.height_mm,
          'Viewport print: the circle is on the sheet (pane-bounded, not blank)')
    check(y0 < 0 or y1 > page.height_mm,
          'Viewport print: the sheet clips the circle top or bottom (expected: pane-bounded)')
    yield from shot('print-open')
    lay = yield from choose('scale', '1:5', region, lay)
    check(ui._print_dialog['page'] is not None, 'the page is rebuilt from the new scale')
    close = next(r for bid, r in lay.buttons if bid == 'close')
    yield from click(centre(close, region), 'print close')
    yield from wait_until(lambda: not ui.print_dialog_open(), 'Close closes the Print dialog')
    after = doc_snapshot()
    check(after == before, 'Print preview and close change no document geometry or Undo')
    check(pane_snapshot() == panes_before,
          'Print leaves pane rectangles, cameras and the 4-pane count unchanged')


def printdialog_layout(union, dst):
    from narwhal3d import printdialog
    return printdialog.layout(union.width, union.height, ui._ui_scale(), dst)


def print_scope_flow():
    """OnlySelected + Area Extents + Scale Fit on the selected circle: fitted, inside the sheet."""
    top = ui._pane_by_label('Top')
    ui.set_active_pane(top)
    st.ctx.select_by_names([CIRCLE_NAME])
    yield .3
    yield from hover(centre_of_pane(top))
    yield from type_text('Print')
    yield from press('RET')
    yield from wait_until(ui.print_dialog_open, 'Print reopens for the scope case')
    yield .25
    dst = ui.print_dialog_state()
    fact('scale remembered from the previous print', dst.scale)
    region = ui._print_dialog_region()
    lay = ui._print_dialog_layout(region)
    lay = yield from choose('scale', 'Fit', region, lay)
    lay = yield from choose('area', 'Extents', region, lay)
    row = next(r for r in lay.rows if r[0] == 'selected_only')
    sc = ui._ui_scale()
    yield from click((region.x + row[2].x + 8 * sc, region.y + row[2].y + row[2].h / 2),
                     'print only selected objects checkbox')
    lay = ui._print_dialog_layout(region)
    check(dst.selected_only is True, 'Only selected objects is checked by a physical click')
    yield .3
    page = ui._print_dialog['page']
    names = {n.lower() for n in ui.selected_display_names()}
    fresh_objs = [o for o in st.model.file.Objects if (o.Attributes.Name or '').lower() in names]
    fresh = ui.print2d.build_page(st.model, ui._print_dialog['views'][dst.view], dst.setup(),
                                  objects=fresh_objs)
    fact('scope diagnostics', {'selected_names': sorted(names),
                               'page_strokes_shown': len(page.strokes),
                               'page_strokes_if_rebuilt': len(fresh.strokes)})
    check(page is not None and len(page.strokes) == 1 and page.strokes[0].closed,
          f'OnlySelected + Extents: the page holds only the selected circle ({len(page.strokes)} stroke)')
    x0, y0, x1, y1 = ink_extent(page)
    W, H = page.width_mm, page.height_mm
    fact('scope page', {'mm': [round(W, 1), round(H, 1)], 'scale': round(page.scale, 4),
                        'ink_mm': [round(v, 2) for v in (x0, y0, x1, y1)]})
    check(x0 >= -0.01 and y0 >= -0.01 and x1 <= W + 0.01 and y1 <= H + 0.01,
          'OnlySelected + Extents + Fit: the circle ink lies inside the sheet')
    check(abs((x1 - x0) - W) < 0.5 or abs((y1 - y0) - H) < 0.5,
          'OnlySelected + Extents + Fit: the circle fills the sheet in one dimension')
    yield from shot('print-selected-extents-fit')
    close = next(r for bid, r in lay.buttons if bid == 'close')
    yield from click(centre(close, region), 'print close (scope case)')
    yield from wait_until(lambda: not ui.print_dialog_open(), 'Close the scope case dialog')


def run():
    global st, REBUILD_NAME, CIRCLE_NAME
    deadline = time.monotonic() + 60
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready within 60s')
        yield .15
    st = ui.app_state
    import rhino3dm as r3
    from narwhal3d import rebuild_popup as rp
    check(not list(st.model.file.Objects), 'refuse nonempty startup document')
    check(ui._viewport_count() == 4, 'four modeling panes present')
    check(ui._pane_by_label('Top') is not None and ui._pane_by_label('Front') is not None,
          'Top and Front panes exist')
    ui.set_gumball(False)
    record_callbacks(rp)
    install_mutation(rp)
    curve = r3.NurbsCurve.Create(False, 3, [r3.Point3d(i, i % 2, 0) for i in range(6)])
    _oid, REBUILD_NAME = st.ctx.add_geometry(curve, 'WorkspaceRebuild')
    circle = r3.NurbsCurve.CreateFromCircle(r3.Circle(r3.Point3d(0, 0, 0), 20))
    _oid, CIRCLE_NAME = st.ctx.add_geometry(circle, 'WorkspaceCircle')
    yield .3
    panes_before = pane_snapshot()
    check(len(panes_before) == 4, f'4 VIEW_3D panes before ({[p["label"] for p in panes_before]})')
    ui.set_active_pane(ui._pane_by_label('Front'))
    st.ctx.select_by_names([REBUILD_NAME])
    yield .3
    yield from rebuild_flow(rp)
    yield from print_flow()
    yield from print_scope_flow()
    check(pane_snapshot() == panes_before, 'final pane rectangles, cameras and count match the start')
    cb = RESULT['callbacks']
    check(cb.get('print', {}).get('calls', 0) > 0 and cb.get('rebuild', {}).get('calls', 0) > 0,
          f'both dialog callbacks ran ({cb})')
    check(all(v['area_none'] == v['calls'] and v['region_none'] == v['calls'] for v in cb.values()),
          'every dialog draw callback ran with no area and no region (native window overlay)')


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(300), finish(1, '300s watchdog expired')),
                 daemon=True).start()
try:
    override = os.environ.get('NARWHAL3D_SETTINGS_FILE', '')
    check(bool(override), 'require disposable preferences before bootstrap')
    settings = Path(override).expanduser()
    check(settings.is_absolute() and settings.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
          and not settings.is_symlink(), 'refuse non-temporary/symlink settings')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'),
          'test must precede bootstrap')
    check(os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1', 'require deferred launch environment')
    check(MUTATION in ('',) + tuple(WITNESS), f'known negative control only ({MUTATION!r})')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    from narwhal3d import ui, bridge
    check(not bpy.app.background, 'real GUI required')
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())

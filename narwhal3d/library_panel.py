"""Library dock host UI: native layout/catalogue, Blender drawing and exact placement.

No demo data. Store operations never enter document history; insertion uses the
existing single history. Python only routes events, native fields, file dialogs
and GPU calls which the Rust helper cannot call directly.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
from . import design, filedialog, native_library, shell_popup, textfield

_store = None
_catalog = {'categories': [], 'assets': []}
_query = None
_category = None
_scroll = 0.0
_hits = {}
_tiles = []
_region = None
_panel_box = None
_form = None
_focus = False
_drag = None
_ghost_handles = []
_textures = {}
_pending_images = []
_notice = 'Click or drag a tile to insert · Right-click for options'
_loaded = False
_busy = False
_hover = None
_hover_control = None
_hover_states = {}
_hover_frames = []
_hover_mouse = (0, 0)
_hover_since = 0.0
_tooltip_ready = False
_tooltip_box = None
_load_error = None
_native_job = None
_asset_loading = False


def store():
    global _store
    if _store is None:
        _store = native_library.LibraryStore()
    return _store


def field():
    global _query
    if _query is None:
        _query = textfield.TextField('', max_chars=256)
    return _query


def _ui():
    from . import ui
    return ui


def _hover_tick():
    global _tooltip_ready
    ui = _ui()
    active = ui.rightpanel.TABS[ui.right_tab_index()].name == 'Library'
    target = _hover[0]['id'] if active and _hover and not _form and not _drag and not shell_popup.is_open() else None
    changed = False
    for ident, frame in list(_hover_states.items()):
        next_frame = min(9, frame+1) if ident == target else max(0, frame-1)
        changed |= next_frame != frame
        if next_frame == 0 and ident != target:
            del _hover_states[ident]
        else:
            _hover_states[ident] = next_frame
    if target and not _tooltip_ready and time.monotonic()-_hover_since >= .55:
        _tooltip_ready = True
        changed = True
    if changed:
        ui._tag_redraw_bands()
    if any(frame != (9 if ident == target else 0) for ident, frame in _hover_states.items()):
        return 1/60
    if target and not _tooltip_ready:
        return min(1/60, max(.01, .55-(time.monotonic()-_hover_since)))
    return None


def _set_hover(hovered, point=None):
    global _hover, _hover_mouse, _hover_since, _tooltip_ready, _tooltip_box
    import bpy
    previous = _hover[0]['id'] if _hover else None
    current = hovered[0]['id'] if hovered else None
    moved = point is not None and point != _hover_mouse
    _hover = hovered
    if point is not None:
        _hover_mouse = point
    if previous != current or (current is not None and moved):
        _hover_since = time.monotonic()
        _tooltip_ready = False
        _tooltip_box = None
        if current is not None:
            _hover_states.setdefault(current, 0)
        _ui()._tag_redraw_bands()
        if not bpy.app.timers.is_registered(_hover_tick):
            bpy.app.timers.register(_hover_tick, first_interval=1/60)


def refresh():
    global _catalog, _loaded, _category, _hover
    _catalog = store().catalog(field().text, _category)
    if _category is not None and not any(c['id'] == _category for c in _catalog['categories']):
        _category = None
        _catalog = store().catalog(field().text)
    _set_hover(None)
    _hover_states.clear()
    _loaded = True


def _message(text):
    global _notice
    _notice = str(text)
    ui = _ui()
    ui.app_state.log('Library: ' + _notice)
    ui.redraw_cmdbar()
    ui._tag_redraw_bands()
    ui._tag_redraw_viewports()


def _schedule(fn):
    """Leave the current event before host dialogs/capture or document publication."""
    import bpy
    global _busy
    if _busy:
        return
    _busy = True
    expected = _ui().app_state.model
    def run():
        global _busy
        try:
            if _ui().app_state.model is not expected:
                raise native_library.LibraryError('Document changed; repeat the Library action')
            fn()
            if _native_job is None:
                refresh()
        except Exception as exc:
            _message(exc)
        finally:
            _busy = _native_job is not None or _asset_loading
            _ui()._tag_redraw_bands()
        return None
    bpy.app.timers.register(run, first_interval=.02)


def _background(fn, message, on_success=None):
    # File-only validation and package I/O run without Blender APIs; completion is main-thread.
    import bpy
    import threading
    global _native_job, _busy
    if _native_job is not None:
        raise native_library.LibraryError("Another Library transfer is still running")
    job = dict(done=False, error=None, result=None)
    _native_job, _busy = job, True
    def work():
        try:
            job['result'] = fn()
        except Exception as exc:
            job['error'] = str(exc)
        finally:
            job['done'] = True
    def poll():
        global _native_job, _busy
        if not job['done']:
            return .1
        _native_job, _busy = None, False
        try:
            if job['error'] is None and on_success is not None:
                on_success(job['result'])
            refresh()
            _message(job['error'] or message)
        except Exception as exc:
            _message(exc)
        return None
    try:
        threading.Thread(target=work, name='Narwhal Library transfer', daemon=True).start()
    except BaseException:
        _native_job, _busy = None, False
        raise
    bpy.app.timers.register(poll, first_interval=.1)


def open_form(mode, asset=None, source=None):
    global _form, _focus
    _set_hover(None)
    cats = _catalog['categories']
    category_name = next((c['name'] for c in cats if c['id'] == _category), '')
    name = asset['name'] if asset else category_name if mode == 'rename_category' else ''
    if source:
        name = Path(source).stem
    previous = _form if mode == 'create_category' else None
    _form = dict(mode=mode, asset=asset, source=source, return_to=previous,
                 field=textfield.TextField(name, max_chars=256),
                 category=(asset or {}).get('category_id', _category or 'uncategorized'))
    _focus = mode in ('capture', 'import', 'rename_asset', 'create_category', 'rename_category')
    _ui()._tag_redraw_bands()


def _close_form():
    global _form, _focus
    _form = (_form or {}).get('return_to')
    _focus = _form is not None
    _ui()._tag_redraw_bands()


def submit_form():
    global _category
    if not _form:
        return
    draft = _form.copy()
    name = draft['field'].text
    def commit():
        global _category
        ui = _ui()
        mode, asset = draft['mode'], draft['asset']
        if mode in ('capture', 'import'):
            from . import library_geometry
            if ui.app_state.active_command is not None:
                raise native_library.LibraryError('Finish the current command before adding assets')
            if mode == 'capture':
                form = _form
                pending = library_geometry.prepare_capture(ui.app_state.ctx, store(), name, draft['category'])
                def complete(result):
                    pending.admit(result)
                    if _form is form:
                        _close_form()
                try:
                    _background(pending.finish, 'Saved to your library', on_success=complete)
                except BaseException:
                    pending.cleanup()
                    raise
                _message('Validating and saving asset…')
                return
            else:
                library_geometry.import_asset(ui.app_state.ctx, store(), draft['source'], name, draft['category'])
        elif mode == 'create_category':
            _category = store().create_category(name)['id']
            if draft.get('return_to'):
                draft['return_to']['category'] = _category
        elif mode == 'rename_category':
            store().rename_category(draft['category'], name)
        elif mode == 'delete_category':
            store().delete_category(draft['category'])
            _category = None
        elif mode == 'rename_asset':
            store().rename_asset(asset['id'], name)
        elif mode == 'move_asset':
            store().move_asset(asset['id'], draft['category'])
        elif mode == 'remove_asset':
            store().remove_asset(asset['id'])
        _close_form()
        _message('Saved to your library' if mode in ('capture', 'import') else 'Library updated')
    _schedule(commit)


def _menu(entries, anchor, title):
    _set_hover(None)
    callbacks = {str(i): fn for i, (_label, fn) in enumerate(entries)}
    rows = [{'label': label, 'action': str(i), 'available': True,
             'danger': label.startswith(('Remove ', 'Delete '))} for i, (label, _fn) in enumerate(entries)]
    def choose(action, explanation):
        if explanation:
            _message(explanation)
        elif action in callbacks:
            try:
                callbacks[action]()
            except Exception as exc:
                _message(exc)
    shell_popup.open(rows, anchor, title, choose)


def category_menu(anchor):
    def choose(ident):
        global _category, _scroll
        if _form:
            _form['category'] = ident
        else:
            _category, _scroll = ident, 0.0
            refresh()
        _ui()._tag_redraw_bands()
    entries = [] if _form else [('All assets', lambda: choose(None))]
    entries += [(c['name'], lambda ident=c['id']: choose(ident)) for c in _catalog['categories']]
    _menu(entries, anchor, 'Category')


def _choose_asset_file():
    from . import library_geometry
    def choose():
        status, path = filedialog.ask_open_path('Import Library Asset', filedialog.start_dir(),
                                               library_geometry.SUPPORTED_FILTERS)
        if status == filedialog.OK and path:
            open_form('import', source=path)
        elif status == filedialog.UNAVAILABLE:
            raise native_library.LibraryError(filedialog.unavailable_hint())
    _schedule(choose)


def _package(export=False, replace=False, current=False):
    def transfer():
        picker = filedialog.ask_save_path if export else filedialog.ask_open_path
        status, path = picker('Export Library' if export else 'Import Library',
                              filedialog.start_dir(), (('Narwhal library', '*.narwhallib'),))
        if status == filedialog.UNAVAILABLE:
            raise native_library.LibraryError(filedialog.unavailable_hint())
        if status != filedialog.OK or not path:
            return
        if export:
            if not path.lower().endswith('.narwhallib'):
                path += '.narwhallib'
            selected_category = _category if current else None
            _background(lambda: store().export_library(path, selected_category), 'Library exported')
        else:
            if replace:
                answer, yes = filedialog.ask_yes_no_cancel('Replace Library?',
                    'Replace your Library catalogue? Existing model copies are unaffected. Export a backup first.')
                if answer != filedialog.OK or yes is not True:
                    return
            _background(lambda: store().import_library(path, 'replace' if replace else 'merge'), 'Library imported')
    _schedule(transfer)


def header_menu(which, anchor):
    if which == 'add':
        entries = [('Add selected object…', lambda: open_form('capture')),
                   ('Import asset file…', _choose_asset_file)]
    else:
        entries = [('Import library — merge…', lambda: _package()),
                   ('Import library — replace…', lambda: _package(replace=True)),
                   ('Export all categories…', lambda: _package(export=True)),
                   ('Export current category…', lambda: _package(export=True, current=True)),
                   ('New category…', lambda: open_form('create_category'))]
        if _category and _category != 'uncategorized':
            entries += [('Rename category…', lambda: open_form('rename_category')),
                        ('Delete category…', lambda: open_form('delete_category'))]
    _menu(entries, anchor, 'Add to Library' if which == 'add' else 'Library')


def insert(asset, point=None, plane=None):
    def publish():
        global _asset_loading, _busy
        from . import library_geometry, file_commands
        ui = _ui()
        if ui.app_state.active_command is not None:
            raise native_library.LibraryError('Finish the current command before inserting an asset')
        target_plane = ui.active_cplane() if plane is None else plane
        if (Path(asset['source_file']).suffix.lower()=='.narwhal'
                and not library_geometry.asset_is_cached(asset)
                and file_commands.background_loading_enabled()):
            def apply(candidate, source):
                names = library_geometry.insert_loaded_asset(ui.app_state.ctx,asset,candidate,point,target_plane)
                _message(f'Inserted {asset["name"]} ({len(names)} objects)')
            def done():
                global _asset_loading, _busy
                _asset_loading = False
                _busy = _native_job is not None
                ui._tag_redraw_bands()
            _asset_loading, _busy = True, True
            try:
                _ok, message = file_commands.background_load(asset['source_file'],apply,strict=False,done=done)
                _message(f'Loading {asset["name"]}…')
            except BaseException:
                done()
                raise
            return
        names = library_geometry.insert_asset(ui.app_state.ctx, asset, point, target_plane)
        _message(f'Inserted {asset["name"]} ({len(names)} objects)')
    _schedule(publish)


def refresh_preview(asset):
    def render():
        from . import library_geometry
        library_geometry.refresh_preview(_ui().app_state.ctx, store(), asset)
        _message('Preview refreshed')
    _schedule(render)


def asset_menu(asset, anchor):
    _menu([('Insert into model', lambda: insert(asset)),
           ('Rename asset…', lambda: open_form('rename_asset', asset)),
           ('Move to category…', lambda: open_form('move_asset', asset)),
           ('Refresh preview', lambda: refresh_preview(asset)),
           ('Remove from Library…', lambda: open_form('remove_asset', asset))], anchor, asset['name'])


def _release_images():
    import bpy
    while _pending_images:
        image = _pending_images.pop()
        try:
            if image.users == 0:
                bpy.data.images.remove(image)
        except ReferenceError:
            pass
    return None


def _retire_image(image):
    import bpy
    _pending_images.append(image)
    if not bpy.app.timers.is_registered(_release_images):
        bpy.app.timers.register(_release_images, first_interval=.05)


def _image(path, rect):
    import bpy
    import gpu
    from gpu_extras.batch import batch_for_shader
    if path not in _textures:
        if len(_textures) >= 64:
            # Only our thumbnails are held here, never document pictures.
            old_image, _old_texture = _textures.pop(next(iter(_textures)))
            _retire_image(old_image)
        image = bpy.data.images.load(path, check_existing=False)
        _textures[path] = (image, gpu.texture.from_image(image))
    texture = _textures[path][1]
    # Blender image textures are scene-linear, while the dock's UI target
    # expects display colour. Use Blender's colour-managed image variant.
    shader = gpu.shader.from_builtin('IMAGE_SCENE_LINEAR_TO_REC709_SRGB')
    shader.bind()
    shader.uniform_sampler('image', texture)
    x0, y0, x1, y1 = rect
    blend = gpu.state.blend_get()
    gpu.state.blend_set('ALPHA')
    try:
        batch_for_shader(shader, 'TRI_FAN', {'pos': ((x0,y0),(x1,y0),(x1,y1),(x0,y1)),
            'texCoord': ((0,0),(1,0),(1,1),(0,1))}).draw(shader)
    finally:
        gpu.state.blend_set(blend)


def draw(ui, shader, region, box):
    import gpu
    previous = gpu.state.blend_get()
    gpu.state.blend_set('ALPHA')
    try:
        _draw_panel(ui, shader, region, box)
    finally:
        gpu.state.blend_set(previous)


def _draw_panel(ui, shader, region, box):
    global _hits, _tiles, _region, _scroll, _panel_box, _load_error, _hover_frames, _tooltip_box
    import blf
    import gpu
    _region = region
    _hits, _tiles = {}, []
    bx0, by0, bx1, by1 = box['body']
    _panel_box = (region.x+bx0, region.y+by0, region.x+bx1, region.y+box['panel_title'][3])
    s = ui._ui_scale()
    ink, muted = design.rgb01(design.TEXT_STRONG), design.rgb01(design.TEXT_MUTED)
    fill, border = design.rgb01(design.PANEL_BG), design.rgb01(design.BORDER)
    accent = design.rgb01(design.ACCENT)
    def text(rect, value, color=ink, size=11, center=False):
        blend = gpu.state.blend_get()
        font = ui._DEFAULT_FONT_ID
        blf.enable(font, blf.CLIPPING)
        blf.clipping(font, *rect)
        if center:
            ui._draw_centered_panel_text(rect, str(value), size*s, color)
        else:
            blf.size(font, size*s)
            height = blf.dimensions(font, 'Hg')[1]
            ui._draw_text(rect[0]+7*s, (rect[1]+rect[3]-height)/2,
                          str(value), size*s, color)
        blf.disable(font, blf.CLIPPING)
        gpu.state.blend_set(blend)
    def button(key, rect, value, active=False, danger=False, primary=False):
        shader.bind()
        hovered = _hover_control == key
        background = design.rgb01(design.ACCENT_SOFT) if active else design.rgb01(design.TOOL_HOVER) if hovered else design.rgb01(design.FIELD_BG if key in ('search', 'category') else design.CHIP_BG)
        if danger:
            background = (0.75, 0.29, 0.35, 1) if hovered else (174/255, 63/255, 77/255, 1)
        elif primary:
            background = accent
        ui._draw_round_rect(shader, *rect, 5*s, background, background if danger or primary else accent if active or hovered else border)
        symbolic = value in ('+', '⋯', '‹')
        text(rect, value, (1, 1, 1, 1) if danger else design.rgb01(design.BAR_TEXT_STRONG) if primary else accent if symbolic and key != 'manage' else ink,
             18 if symbolic else 11, center=symbolic or key in ('submit', 'cancel', 'category_add'))
        _hits[key] = tuple(v + (region.x if i%2==0 else region.y) for i,v in enumerate(rect))
    title = box['panel_title']
    button('add', (bx1-62*s,title[1]+4*s,bx1-35*s,title[3]-4*s), '+', True)
    button('manage', (bx1-32*s,title[1]+4*s,bx1-5*s,title[3]-4*s), '⋯')
    if not _loaded:
        if _load_error is None:
            try:
                refresh()
            except Exception as exc:
                _load_error = str(exc)
        if not _loaded:
            lines = textfield.wrap_spans(_load_error or "Library unavailable", max(10,bx1-bx0-20*s), lambda t: ui._text_width(t,10*s))
            for index,(start,end) in enumerate(lines):
                y = by1-32*s-index*15*s
                text((bx0+8*s,y-12*s,bx1-8*s,y+4*s), (_load_error or "Library unavailable")[start:end], muted, 10)
            y = by1-45*s-len(lines)*15*s
            button("retry", (bx0+9*s,y-30*s,bx1-9*s,y), "Retry Library")
            return
    plan = store().layout(bx1-bx0, by1-by0, s, _scroll, len(_catalog['assets']))
    _scroll = plan['scroll']
    _hover_frames = plan['hover_frames']
    _tooltip_box = None
    rect = lambda key: tuple(v + (bx0 if i%2==0 else by0) for i,v in enumerate(plan[key]))
    if _form:
        mode, asset = _form['mode'], _form['asset']
        titles = {'capture':'Add selected object', 'import':'Import asset file',
                  'create_category':'New category', 'rename_category':'Rename category',
                  'delete_category':'Delete category', 'rename_asset':'Rename asset',
                  'move_asset':'Move asset', 'remove_asset':'Remove asset'}
        editor = plan['editor']
        er = lambda key: tuple(v + (bx0 if i%2==0 else by0) for i,v in enumerate(editor[key]))
        button('back', er('back'), '‹')
        text(er('title'), titles[mode], size=12)
        shader.bind()
        ui._draw_rect(shader, bx0, er('head')[1], bx1, er('head')[1]+s, border)
        y = er('head')[1]-15*s
        def paragraph(value, color=muted, size=11):
            nonlocal y
            spans = textfield.wrap_spans(value, max(10,bx1-bx0-24*s), lambda t: ui._text_width(t,size*s))
            for begin, end in spans:
                text((bx0+5*s,y-18*s,bx1-5*s,y),value[begin:end],color,size)
                y -= 17*s
            y -= 10*s
        def input_field(label, key, value, active=False):
            nonlocal y
            text((bx0+5*s,y-16*s,bx1-5*s,y),label,muted,10)
            y -= 21*s
            bounds = (bx0+11*s,y-33*s,bx1-11*s,y)
            button(key,bounds,value,active)
            if key=='search' and _focus:
                caret = min(bounds[2]-5*s,bounds[0]+7*s+ui._text_width(_form['field'].text[:_form['field'].caret],11*s))
                shader.bind()
                ui._draw_rect(shader,caret,bounds[1]+7*s,caret+s,bounds[3]-7*s,accent)
            y -= 47*s
        if mode in ('remove_asset','delete_category'):
            target = asset['name'] if asset else next((c['name'] for c in _catalog['categories'] if c['id']==_form['category']), '')
            paragraph(('Remove “'+target+'” from your library?') if asset else ('Delete “'+target+'”?'),ink)
            paragraph('Existing model copies stay unchanged. This removes the library entry, not its stored geometry files.' if asset else 'Its assets will be kept and moved to Uncategorized.',size=10)
        elif mode=='move_asset':
            paragraph('Move “'+asset['name']+'” to another category.',ink)
        if mode in ('capture','import','rename_asset','create_category','rename_category'):
            input_field('Category name' if 'category' in mode else 'Asset name','search',_form['field'].text or 'Enter a name…',_focus)
        if mode in ('capture','import','move_asset'):
            category_label = next((c['name'] for c in _catalog['categories'] if c['id']==_form['category']), 'Uncategorized')
            input_field('Destination category' if mode=='move_asset' else 'Save in category','category',category_label+' ▾')
            if mode in ('capture','import'):
                button('category_add',(bx0+11*s,y-28*s,bx1-11*s,y),'New category…')
                y -= 42*s
                paragraph('Save independent, reusable CAD geometry without changing your current model.',size=10)
        actions = {'capture':'Save to library','import':'Add from file','create_category':'Create category',
                   'rename_category':'Save name','rename_asset':'Save','move_asset':'Move asset',
                   'remove_asset':'Remove asset','delete_category':'Delete category'}
        shader.bind()
        ui._draw_rect(shader,bx0,er('submit')[3]+10*s,bx1,er('submit')[3]+11*s,border)
        button('cancel',er('cancel'),'Cancel')
        button('submit',er('submit'),actions[mode],danger=mode in ('remove_asset','delete_category'),primary=mode not in ('remove_asset','delete_category'))
        return
    edit = field()
    search = rect('search')
    button('search', search, edit.text or 'Search your library…', _focus)
    if _focus:
        visible = edit.text[:edit.caret]
        caret = search[0]+7*s+ui._text_width(visible,11*s)
        shader.bind()
        ui._draw_rect(shader, caret, search[1]+6*s, caret+s, search[3]-6*s, accent)
    label = next((c['name'] for c in _catalog['categories'] if c['id']==_category), 'All assets')
    button('category', rect('category'), label+' ▾')
    button('category_add', rect('category_add'), '+')
    text(rect('overview'), f'Your assets · {len(_catalog["assets"])}')
    if not _catalog['assets']:
        text((bx0+9*s,by1-175*s,bx1-9*s,by1-125*s), 'No assets found', muted)
    clip = rect('grid')
    previous_scissor = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(clip[0]), int(clip[1]), max(0,int(clip[2]-clip[0])), max(0,int(clip[3]-clip[1])))
    try:
        for tile in plan['tiles']:
            a = _catalog['assets'][tile['index']]
            frame = _hover_frames[_hover_states.get(a['id'], 0)]
            weight, lift = frame['weight'], frame['lift']
            local = lambda key: tuple(v+(bx0 if i%2==0 else by0+lift) for i,v in enumerate(tile[key]))
            gpu.state.blend_set('ALPHA')
            card = local('box')
            raw_art = local('art')
            art = (raw_art[0]+s, raw_art[1], raw_art[2]-s, raw_art[3]-s)
            dark = design.THEME == 'dark'
            card_fill = design.rgb01((55,55,55) if dark else (251,253,255))
            card_border = design.rgb01((81,81,81) if dark else (213,222,234))
            art_fill = design.rgb01((66,66,66) if dark else (237,242,248))
            shader.bind()
            if weight:
                for spread, alpha in ((6,.018),(4,.028),(2,.045)):
                    ui._draw_round_rect(shader,card[0]-spread*s,card[1]-(spread+2)*s,
                                        card[2]+spread*s,card[3]+(spread-2)*s,
                                        (7+spread)*s,(*accent[:3],alpha*weight))
            stroke = tuple(card_border[i]*(1-weight)+accent[i]*weight for i in range(4))
            ui._draw_round_rect(shader, *card, 7*s, card_fill, stroke)
            ui._draw_round_rect(shader, *art, 6*s, art_fill)
            ui._draw_rect(shader,art[0],art[1],art[2],art[1]+6*s,art_fill)
            step = 18*s
            x, y = art[0]+step, art[1]+step
            grid_color = (220/255,230/255,240/255,.04) if dark else (90/255,116/255,151/255,.055)
            while x < art[2]-6*s:
                ui._draw_rect(shader, x, art[1], x+s, art[3]-s, grid_color)
                x += step
            while y < art[3]-s:
                corner = 6*s if y>art[3]-6*s else 0
                ui._draw_rect(shader, art[0]+corner, y, art[2]-corner, y+s, grid_color)
                y += step
            try:
                inset = 4*s
                _image(a['thumbnail'], (raw_art[0]+inset, raw_art[1]+inset, raw_art[2]-inset, raw_art[3]-inset))
            except Exception:
                text(art, 'Preview unavailable', muted)
            shader.bind()
            if weight:
                ui._draw_round_rect(shader,*local('more'),4*s,(*design.rgb01(design.TOOL_HOVER)[:3],weight))
                overlay = (art[0],art[1]-frame['slide'],art[2],art[1]+24*s-frame['slide'])
                gpu.state.scissor_set(int(art[0]),int(max(art[1],clip[1])),max(0,int(art[2]-art[0])),max(0,int(min(art[3],clip[3])-max(art[1],clip[1]))))
                for strip in range(12):
                    y0 = overlay[1]+strip*2*s
                    ui._draw_rect(shader,overlay[0],y0,overlay[2],y0+2*s,
                                  (22/255,38/255,58/255,.82*weight*(1-strip/12)))
                hint = 'Click or drag to insert'
                hint_lines = textfield.wrap_spans(hint,max(10,art[2]-art[0]-8*s),lambda t:ui._text_width(t,9*s))
                for index,(begin,end) in enumerate(hint_lines[:2]):
                    y0 = overlay[1]+(len(hint_lines[:2])-index-1)*10*s+2*s
                    text((overlay[0],y0,overlay[2],y0+11*s),hint[begin:end],(1,1,1,weight),9,center=True)
                gpu.state.scissor_set(int(clip[0]),int(clip[1]),max(0,int(clip[2]-clip[0])),max(0,int(clip[3]-clip[1])))
            caption = ui._fit_panel_text(a['name'], max(0, local('name')[2]-local('name')[0]-14*s), 11*s)
            text(local('name'), caption)
            text(local('more'), '⋯', accent if weight else muted, 17, center=True)
            window = lambda r: tuple(v+(region.x if i%2==0 else region.y) for i,v in enumerate(r))
            hit = tuple(v+(bx0 if i%2==0 else by0) for i,v in enumerate(tile['box']))
            hit = (hit[0], max(hit[1],clip[1]), hit[2], min(hit[3],clip[3]))
            _tiles.append((a, window(hit), window(local('more'))))
    finally:
        gpu.state.scissor_set(*previous_scissor)
    if _hover and _tooltip_ready and not _drag and not _form and not shell_popup.is_open():
        asset, bounds = _hover
        category = next((c['name'] for c in _catalog['categories'] if c['id']==asset['category_id']), 'Uncategorized')
        lines = []
        for value in (asset['name'],category,'Click or drag to insert'):
            spans = textfield.wrap_spans(value,max(10,bx1-bx0-26*s),lambda t:ui._text_width(t,10*s))
            lines.extend(value[begin:end] for begin,end in spans)
        measured = max((ui._text_width(line,10*s) for line in lines),default=0)
        tip = store().tooltip_layout(bx1-bx0,by1-by0,_hover_mouse[0]-region.x-bx0,
                                     _hover_mouse[1]-region.y-by0,measured,len(lines),s)
        tooltip = tuple(v+(bx0 if i%2==0 else by0) for i,v in enumerate(tip['box']))
        _tooltip_box = tuple(v+(region.x if i%2==0 else region.y) for i,v in enumerate(tooltip))
        gpu.state.blend_set('ALPHA')
        shader.bind()
        ui._draw_round_rect(shader,tooltip[0]+2*s,tooltip[1]-3*s,tooltip[2]+2*s,tooltip[3]-3*s,6*s,(0,0,0,.12))
        ui._draw_round_rect(shader,*tooltip,5*s,design.rgb01(design.MENU_BG),design.rgb01(design.BORDER_STRONG))
        for index,line in enumerate(lines):
            baseline = tooltip[3]-(index+1)*15*s-4*s
            text((tooltip[0]+2*s,baseline-2*s,tooltip[2]-2*s,baseline+13*s),line,ink if index==0 else muted,10)
    text(rect('footer'), 'Working…' if _busy else _notice, muted, 9)


def _inside(event, rect):
    return rect and rect[0] <= event.mouse_x < rect[2] and rect[1] <= event.mouse_y < rect[3]


def _stop_drag():
    global _drag
    import bpy
    for owner, handle in _ghost_handles:
        owner.draw_handler_remove(handle, 'WINDOW')
    _ghost_handles.clear()
    _drag = None
    if bpy.context.window:
        bpy.context.window.cursor_modal_restore()


def _ghost():
    if not _drag or not _drag['moved']:
        return
    import bpy
    context = bpy.context
    r = context.region
    if not r:
        return
    x, y = _drag['xy']
    x, y = x-r.x+12, y-r.y+12
    try:
        _image(_drag['asset']['thumbnail'], (x,y,x+76,y+76))
    except Exception:
        pass


def handle_event(event):
    global _focus, _scroll, _drag, _hover, _load_error, _hover_control
    import bpy
    ui = _ui()
    if _drag:
        if event.type == 'ESC' and event.value == 'PRESS':
            _stop_drag()
            ui._tag_redraw_viewports()
            return True
        if event.type == 'MOUSEMOVE':
            _drag['xy'] = (event.mouse_x,event.mouse_y)
            if abs(event.mouse_x-_drag['start'][0])+abs(event.mouse_y-_drag['start'][1]) > 6:
                _drag['moved'] = True
            ui._tag_redraw_viewports()
            return True
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            pending = _drag.copy()
            _stop_drag()
            if pending['model'] is not ui.app_state.model:
                _message('Document changed; asset placement cancelled')
                return True
            if not pending['moved']:
                insert(pending['asset'])
            else:
                for area in ui._view3d_areas():
                    for region in area.regions:
                        if region.type=='WINDOW' and region.x<=event.mouse_x<region.x+region.width and region.y<=event.mouse_y<region.y+region.height:
                            point = ui.pick_point(area,area.spaces.active,region,event.mouse_x-region.x,event.mouse_y-region.y)
                            if point is not None:
                                snap = ui.snap_at(area, area.spaces.active, region, point,
                                                  mouse_region=(event.mouse_x-region.x,event.mouse_y-region.y))
                                if snap is not None:
                                    point = snap.point
                                insert(pending['asset'],point,ui.get_cplane(area))
                            else:
                                _message('Cannot place on this construction plane')
                            return True
                _message('Drop onto a modeling viewport to insert')
            return True
        return True
    active = ui.rightpanel.TABS[ui.right_tab_index()].name == 'Library'
    if not active:
        _focus = False
        _set_hover(None)
        if _hover_control is not None and bpy.context.window:
            bpy.context.window.cursor_set('DEFAULT')
        _hover_control = None
        return False
    if event.type == 'MOUSEMOVE':
        control = next((key for key, bounds in _hits.items() if _inside(event, bounds)), None)
        previous_control = _hover_control
        if control != _hover_control:
            _hover_control = control
            ui._tag_redraw_bands()
        if (_inside(event, _panel_box) or previous_control is not None) and bpy.context.window:
            bpy.context.window.cursor_set('TEXT' if control == 'search' else 'DEFAULT')
        hovered = next(((a,box) for a,box,_more in _tiles if _inside(event,box)),None)
        _set_hover(hovered,(event.mouse_x,event.mouse_y))
    if _form and event.type == 'ESC' and event.value == 'PRESS':
        _close_form()
        return True
    if _focus and event.value in ('PRESS','REPEAT'):
        f = _form['field'] if _form else field()
        if event.type=='ESC':
            _close_form() if _form else None
            _focus = False
        elif event.type in ('RET','NUMPAD_ENTER'):
            submit_form() if _form else None
            _focus = bool(_form)
        elif event.ctrl and event.type=='A': f.select_all()
        elif event.ctrl and event.type=='C': bpy.context.window_manager.clipboard=f.selected_text()
        elif event.ctrl and event.type=='X': bpy.context.window_manager.clipboard=f.cut()
        elif event.ctrl and event.type=='V': f.insert(bpy.context.window_manager.clipboard.replace('\n',' '))
        elif event.type=='BACK_SPACE': f.backspace()
        elif event.type=='DEL': f.delete()
        elif event.type in ('LEFT_ARROW','RIGHT_ARROW'): f.move(-1 if event.type=='LEFT_ARROW' else 1,select=event.shift)
        elif event.type=='HOME': f.home(select=event.shift)
        elif event.type=='END': f.end(select=event.shift)
        elif event.type not in ('LEFTMOUSE','RIGHTMOUSE','MIDDLEMOUSE','MOUSEMOVE','WHEELUPMOUSE','WHEELDOWNMOUSE'):
            char=getattr(event,'unicode','') or getattr(event,'ascii','')
            if char and char.isprintable() and not event.ctrl and not event.alt: f.insert(char)
            else: return True
        else:
            f = None
        if f is not None:
            if not _form:
                _scroll=0.
                try: refresh()
                except Exception as exc: _message(exc)
            ui._tag_redraw_bands()
            return True
    panel = _inside(event, _panel_box)
    if not panel:
        if event.type=='LEFTMOUSE' and event.value=='PRESS': _focus=False
        return False
    if event.type in ('WHEELUPMOUSE','WHEELDOWNMOUSE'):
        _scroll=max(0.,_scroll+(65 if event.type=='WHEELDOWNMOUSE' else -65)*ui._ui_scale())
        ui._tag_redraw_bands()
        return True
    if event.value != 'PRESS' or event.type not in ('LEFTMOUSE','RIGHTMOUSE'):
        return event.type in ('LEFTMOUSE','RIGHTMOUSE')
    for key, rect in _hits.items():
        if not _inside(event,rect): continue
        anchor=(rect[0],rect[1])
        if key=='search': _focus=True
        elif key=='category': category_menu(anchor)
        elif key=='category_add': open_form('create_category')
        elif key in ('add','manage'): header_menu(key,anchor)
        elif key=='submit': submit_form()
        elif key in ('cancel','back'): _close_form()
        elif key=='retry':
            _load_error = None
            try: refresh()
            except Exception as exc: _message(exc)
        ui._tag_redraw_bands()
        return True
    _focus=False
    for a, box, more in _tiles:
        if not _inside(event,box): continue
        if event.type=='RIGHTMOUSE' or _inside(event,more):
            asset_menu(a,(box[0],box[1]))
        elif not _busy:
            if ui.app_state.active_command is not None:
                _message('Finish the current command before inserting an asset')
                return True
            _set_hover(None)
            _drag=dict(asset=a,model=ui.app_state.model,start=(event.mouse_x,event.mouse_y),xy=(event.mouse_x,event.mouse_y),moved=False)
            for owner in (bpy.types.SpaceView3D,bpy.types.SpaceInfo):
                _ghost_handles.append((owner,owner.draw_handler_add(_ghost,(),'WINDOW','POST_PIXEL')))
            bpy.context.window.cursor_modal_set('DEFAULT')
        return True
    return True


def close():
    """Release only this panels drag and preview references on host shutdown."""
    global _focus, _form, _tooltip_ready, _tooltip_box, _hover
    import bpy
    if bpy.app.timers.is_registered(_hover_tick):
        bpy.app.timers.unregister(_hover_tick)
    _hover_states.clear()
    _hover, _tooltip_ready, _tooltip_box = None, False, None
    if _drag is not None:
        _stop_drag()
    _focus, _form = False, None
    geometry = sys.modules.get(__package__ + '.library_geometry')
    if geometry is not None:
        geometry.clear_asset_cache()
    for image, _texture in _textures.values():
        _retire_image(image)
    _textures.clear()

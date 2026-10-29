/* Editable, offline HTML adapters for Narwhal's dialog surfaces. No dependencies.
 * JS is intentional: this is browser/UI glue, not product or geometry code.
 * MEASURED = read from the named Python source; OURS = browser adaptation.
 * Source provenance and ALL known fidelity gaps are exposed by catalog().
 *
 * API: NarwhalDialogs.open(name, options?), close(), setTheme(name), catalog(), snapshot().
 * Common options: title, values (copied draft), onSelect(value, meta),
 * onChange(snapshot), onCancel(meta), onClose(meta), onAction(action, value, meta).
 * Color also accepts color (hex or RGB bytes), layerName, onColor(rgb, meta).
 * Picker accepts candidates:[{key,label}], anchor:{x,y} (CSS viewport coordinates),
 * onHover(key|null). File accepts files:[{name,path,directory,size,modified}], path,
 * filename, filters:[[label,glob]], places:[{label,path}]. No filesystem is read.
 * Print accepts views:[name], previewLines:[{points:[[x,y]],color,width}] in page mm;
 * these must be synthetic display data, never CAD geometry or export instructions.
 * Callbacks are notifications to the parent PREVIEW only: do not wire to product APIs.
 * Print notifies onAction('print', draft, meta) and stays open; it never calls print().
 * Confirm notifies onSelect; Cancel/ESC/close never do. State is memory-only.
 * Parent owns loading these files, limitation labels, fixtures and browser tests.
 */
(function () {
  'use strict';

  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const data = () => window.NARWHAL_UI || {};
  const CATALOG = [
    {name: 'preferences', title: 'View preferences', fidelity: 'partial-source-menu',
      sources: ['narwhal3d/preferences.py:34-58', 'narwhal3d/ui.py:9836-9851'],
      controls: ['Theme > Light / Dark', 'Show Unavailable'], tabs: [],
      differences: ['No general Preferences window or tabs exist in the inspected source. This entry exposes ONLY the drawn View preference menu; no invented settings tabs.', 'The source check mark on Show Unavailable means hide-unavailable. Preserved here.', 'Choices notify the preview, never apply or persist product preferences.']},
    {name: 'open', title: 'Open', fidelity: 'native-layout-approximation',
      sources: ['narwhal3d/filedialog.py:1-127', 'narwhal3d/file_commands.py:217-234', 'narwhal3d/import_commands.py:62-67'],
      differences: ['KDE, GTK and Win32 own native layout and dimensions; Python defines no single layout to reproduce.', 'Browser file table, places, path bar and 760x480 size are OURS, not measured native pixels.', 'Only supplied synthetic entries are browsed. No native picker, disk enumeration, overwrite, import or save occurs.']},
    {name: 'save', title: 'Save As', fidelity: 'native-layout-approximation',
      sources: ['narwhal3d/filedialog.py:62-125', 'narwhal3d/file_commands.py:123-132'],
      differences: ['Same native-layout caveat as Open. Source-backed .narwhal and .3dm filters.', 'Filename confirmation returns a string only. No file is created; overwrite confirmation is not covered.']},
    {name: 'color', title: 'Color', fidelity: 'native-layout-approximation',
      sources: ['narwhal3d/colordialog.py:15-28,63-135', 'narwhal3d/ui.py:3732-3735'],
      differences: ['KDE/GTK own the actual widget layout; the 540px HTML HSV field, RGB/HSV sliders and swatches are OURS editable equivalent, not a native pixel match.', 'RGB byte parsing and layer-only full-value initial color follow source. No alpha editing; source ignores accepted integer alpha.', 'Palette swatches are browser fixtures, not a captured desktop palette.']},
    {name: 'print', title: 'Print Setup', fidelity: 'source-layout',
      sources: ['narwhal3d/printdialog.py:24-134,200-263', 'narwhal3d/print2d.py:43-60', 'narwhal3d/ui.py:18235-18373'],
      differences: ['Source maximum 1100x720, 30px title/rows, 44% controls column reproduced; browser centers over the page rather than active Blender viewport.', 'Sheet uses real page dimensions; drawing is empty unless parent supplies display-only previewLines. No projection, scaling CAD, visibility filtering, export or print. Product perspective-view refusal is not simulated.', 'Close discards draft here; the product close_print_dialog persists print choices. This preview intentionally never persists settings.']},
    {name: 'picker', title: 'Choose object', fidelity: 'source-layout',
      sources: ['narwhal3d/selection_menu.py:33-57', 'narwhal3d/shell_popup.py:36-44,141-197,260-346,458-504'],
      differences: ['Source 200px minimum, 22px rows/title, 4px padding; browser text measurement and scrollbar differ from BLF and source scroll arrows.', 'Parent supplies candidates and optional hover highlighting. No scene hit test or selection occurs. Empty candidates show no invented objects.']},
    {name: 'units', title: 'Units', fidelity: 'source-controls',
      sources: ['narwhal3d/ui.py:11674-11715', 'narwhal3d/units.py:74-99'],
      differences: ['Source width 380 and all three controls reproduced. Blender RNA widget metrics are approximated by themed HTML controls.', 'No unit conversion or model scaling is executed.']},
    {name: 'tolerance', title: 'Absolute Tolerance', fidelity: 'source-controls',
      sources: ['narwhal3d/ui.py:11817-11854'],
      differences: ['Source width 280, unit label and decimal string field reproduced; Blender widget metrics approximated.', 'Finite positive validation only; no document change.']},
    {name: 'fillet-radius', title: 'Fillet Radius', fidelity: 'source-controls',
      sources: ['narwhal3d/ui.py:11875-11894'],
      differences: ['Source 260px dialog and Radius string field. Blender RNA widget rasterization differs from HTML; no geometry or radius application is executed.']},
    {name: 'save-changes', title: 'Narwhal 3D', fidelity: 'native-layout-approximation',
      sources: ['narwhal3d/file_commands.py:134-153', 'narwhal3d/filedialog.py:86-125'],
      differences: ['Source message and Yes/No/Cancel contract, browser-sized native-dialog equivalent. No save or discard is performed.']}
  ];
  const ALIASES = {'file-open': 'open', 'file-save': 'save', 'save-as': 'save',
    'color-picker': 'color', colour: 'color', overlap: 'picker', 'overlap-chooser': 'picker',
    'print-setup': 'print', 'custom-tolerance': 'tolerance'};
  const PAGE_SIZES = {Letter: [215.9,279.4], Legal: [215.9,355.6], Tabloid: [279.4,431.8],
    A4: [210,297], A3: [297,420], A2: [420,594], A1: [594,841], A0: [841,1189]};
  const DESTINATIONS = [['PDF', '.pdf'], ['CorelDRAW 2015/X7', '.cdr'], ['Adobe Illustrator', '.ai'], ['SVG', '.svg']];
  const UNITS = ['Angstroms','Nanometers','Microns','Millimeters','Centimeters','Decimeters','Meters',
    'Dekameters','Hectometers','Kilometers','Megameters','Gigameters','Microinches','Mils','Inches',
    'Feet','Yards','Miles','PrinterPoints','PrinterPicas','NauticalMiles','AstronomicalUnits','LightYears','Parsecs'];
  let current = null, theme = null, serial = 0;

  function element(tag, cls, text) {
    const node = document.createElement(tag);
    node.className = 'cad-dialog-' + cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function button(text, fn, cls = 'button') {
    const node = element('button', cls, text);
    node.type = 'button'; node.onclick = fn;
    return node;
  }
  function notify(session, name, ...args) {
    if (typeof session.options[name] === 'function') session.options[name](...args);
  }
  function snapshot() {
    return current ? {name: current.name, theme, draft: copy(current.draft),
      dropdown: current.dropdown ? current.dropdown.key : null,
      highlighted: copy(current.highlighted ?? null), simulated: true} : null;
  }
  function meta(session, status) { return {name: session.name, status, simulated: true}; }
  function changed() { if (current) notify(current, 'onChange', snapshot()); }
  function finish(value, status = 'ok') {
    const session = current;
    if (!session) return false;
    closeDropdown(false);
    current = null;

    session.root.close(); session.root.remove();
    if (session.previousFocus?.isConnected) session.previousFocus.focus();
    if (session.name === 'picker') notify(session, 'onHover', null);
    if (status === 'ok') {
      if (session.name === 'color') notify(session, 'onColor', copy(value), meta(session, status));
      notify(session, 'onSelect', copy(value), meta(session, status));
    } else notify(session, 'onCancel', meta(session, status));
    notify(session, 'onClose', meta(session, status));
    return true;
  }
  function close() { return finish(null, 'cancel'); }
  function applyTheme() {
    if (!current) return;
    // Set tokens on our root only. Never skin the document or change preferences.
    const tokens = data().themes?.[theme] || {};
    for (const key of current.themeKeys || []) current.root.style.removeProperty('--' + key);
    current.themeKeys = Object.keys(tokens);
    for (const [key, value] of Object.entries(tokens)) current.root.style.setProperty('--' + key, value);
    current.root.dataset.theme = theme || 'inherit';
  }
  function setTheme(name) {
    if (name != null && !['light', 'dark'].includes(name)) throw new RangeError('Unknown theme: ' + name);
    theme = name; applyTheme(); return theme;
  }
  function error(text) {
    current.message.textContent = text || '';
    current.message.hidden = !text;
  }
  function shell(title, kind) {
    const session = current;
    const root = element('dialog', 'root cad-dialog-' + kind);
    root.id = 'cad-dialog-' + (++serial);
    root.setAttribute('aria-labelledby', root.id + '-title');
    const heading = element('header', 'title', title);
    heading.id = root.id + '-title';
    const body = element('div', 'body');
    const message = element('div', 'message');
    message.setAttribute('role', 'status'); message.hidden = true;
    const footer = element('footer', 'footer');
    root.append(heading, body, message, footer);
    Object.assign(session, {root, body, footer, message, heading});
    root.addEventListener('cancel', event => {
      event.preventDefault();
      if (session.dropdown) closeDropdown(); else close();
    });
    // Shield the parent's global command/shortcut routing. Native inputs still work.
    root.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.key === 'Escape') {
        event.preventDefault();
        if (session.dropdown) closeDropdown(); else close();
      }
    });
    for (const type of ['keyup', 'pointerdown', 'pointerup', 'click', 'dblclick', 'wheel']) {
      root.addEventListener(type, event => event.stopPropagation());
    }
    root.addEventListener('contextmenu', event => event.preventDefault());
    root.addEventListener('click', event => {
      if (event.target === root && (kind === 'picker' || kind === 'preferences')) close();
    });
    // An open source dropdown eats the first outside click, rather than activating
    // whatever control was behind it. Capture before the button's click handler.
    root.addEventListener('click', event => {
      if (session.dropdown && !session.dropdown.node.contains(event.target) &&
          event.target !== session.dropdown.trigger) {
        event.preventDefault(); event.stopImmediatePropagation(); closeDropdown();
      }
    }, true);
    return body;
  }
  function closeDropdown(focus = true) {
    const menu = current?.dropdown;
    if (!menu) return;
    menu.node.remove(); menu.trigger.setAttribute('aria-expanded', 'false');
    current.dropdown = null;
    if (focus) menu.trigger.focus();
  }
  function choice(parent, label, key, entries, onChange, disabled = false) {
    const row = element('div', 'row');
    const labelNode = element('label', 'label', label);
    const pairs = entries.map(item => Array.isArray(item) ? item : [item, String(item)]);
    const selected = pairs.find(item => item[0] === current.draft[key]);
    const trigger = button((selected || pairs[0] || ['', '--'])[1], () => {
      if (current.dropdown?.trigger === trigger) { closeDropdown(); return; }
      closeDropdown(false);
      const node = element('div', 'dropdown');
      node.setAttribute('role', 'listbox'); node.setAttribute('aria-label', label);
      const items = pairs.map(([value, text]) => {
        const item = button(text, () => {
          current.draft[key] = value;
          trigger.textContent = text;
          closeDropdown();
          if (onChange) onChange(value);
          changed();
        }, 'option');
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(current.draft[key] === value));
        node.append(item); return item;
      });
      current.dropdown = {node, trigger, key};
      trigger.setAttribute('aria-expanded', 'true'); row.append(node);
      let index = Math.max(0, pairs.findIndex(pair => pair[0] === current.draft[key]));
      node.addEventListener('keydown', event => {
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
            (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
          items[index]?.focus();
        }
      });
      const bounds = node.getBoundingClientRect();
      if (bounds.bottom > window.innerHeight - 8) node.classList.add('cad-dialog-dropdown-up');
      items[index]?.focus();
    }, 'choice');
    trigger.disabled = disabled;
    trigger.id = 'cad-dialog-field-' + (++serial);
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    labelNode.htmlFor = trigger.id;
    row.append(labelNode, trigger); parent.append(row);
    return trigger;
  }
  function field(parent, label, key, config = {}) {
    const row = element('label', 'row');
    row.append(element('span', 'label', label));
    const input = element('input', 'input');
    input.type = config.type || 'text'; input.value = current.draft[key] ?? '';
    if (config.min != null) input.min = config.min;
    if (config.max != null) input.max = config.max;
    if (config.step != null) input.step = config.step;
    input.autocomplete = 'off';
    let before = input.value;
    input.addEventListener('focus', () => { before = input.value; });
    input.addEventListener('input', () => {
      current.draft[key] = input.value;
      if (config.input) config.input(input.value);
      changed();
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Escape' && input.value !== before) {
        event.preventDefault(); event.stopPropagation();
        input.value = before; current.draft[key] = before;
        if (config.input) config.input(before);
        changed();
      } else if (event.key === 'Enter') {
        event.preventDefault(); before = input.value; input.blur();
      }
    });
    row.append(input); parent.append(row); return input;
  }
  function check(parent, label, key) {
    const row = element('label', 'check');
    const input = element('input', 'checkbox'); input.type = 'checkbox'; input.checked = !!current.draft[key];
    input.onchange = () => { current.draft[key] = input.checked; changed(); };
    row.append(input, element('span', 'label', label)); parent.append(row); return input;
  }
  function buttons(confirm = () => finish(current.draft), text = 'OK') {
    current.footer.append(button('Cancel', close), button(text, confirm, 'button cad-dialog-primary'));
  }

  function renderPreferences() {
    // No tabbed Preferences draw function exists. Do not manufacture one from
    // persistence keys: many settings are owned by entirely different panels.
    const defaults = data().panels?.preference_defaults || {};
    current.draft = {theme: defaults.theme || 'light', show_unavailable: defaults.show_unavailable ?? true,
      ...current.draft};
    const body = shell(current.options.title || 'View', 'preferences');
    choice(body, 'Theme', 'theme', [['light', 'Light'], ['dark', 'Dark']], () => finish(current.draft));
    const toggle = button('Show Unavailable', () => {
      current.draft.show_unavailable = !current.draft.show_unavailable; finish(current.draft);
    }, 'menu-item');
    toggle.setAttribute('role', 'menuitemcheckbox');
    toggle.setAttribute('aria-checked', String(!current.draft.show_unavailable));
    body.append(toggle); current.footer.hidden = true;
  }

  function renderFile() {
    const session = current, options = session.options, saving = session.name === 'save';
    const filters = copy(options.filters || (saving ? [['Narwhal 3D Projects', '*.narwhal'], ['3DM Models', '*.3dm']] :
      [['Supported files', '*.narwhal *.3dm *.step *.stp *.dxf *.svg *.cdr *.pdf *.ai *.obj *.stl'],
       ['Narwhal 3D Projects', '*.narwhal'], ['3DM Interchange', '*.3dm']]));
    session.draft = {path: options.path || '/', filename: options.filename || (saving ? 'Untitled.narwhal' : ''),
      filter: 0, ...session.draft};
    const files = (options.files || []).map(file => ({...file,
      path: file.path || session.draft.path.replace(/\/$/, '') + '/' + file.name}));
    const body = shell(options.title || (saving ? 'Save As' : 'Open'), 'file');
    const nav = element('div', 'file-nav');
    nav.append(button('↑', () => {
      session.draft.path = session.draft.path.replace(/\/?[^/]+\/?$/, '') || '/';
      pathInput.value = session.draft.path; refresh(); changed();
    }));
    const pathInput = field(nav, 'Location', 'path', {input: refresh});
    body.append(nav);
    const workspace = element('div', 'file-workspace');
    const places = element('nav', 'places'); places.setAttribute('aria-label', 'Places');
    for (const place of options.places || [{label: 'Home', path: '/'}, {label: 'Documents', path: '/Documents'}]) {
      places.append(button(place.label, () => {
        session.draft.path = place.path; pathInput.value = place.path; refresh(); changed();
      }, 'place'));
    }
    const table = element('table', 'files');
    const head = element('thead', 'files-head'), header = element('tr', 'files-header');
    ['Name', 'Size', 'Modified'].forEach(text => header.append(element('th', 'files-heading', text)));
    head.append(header);
    const entries = element('tbody', 'files-body'); table.append(head, entries);
    const listing = element('div', 'file-list'); listing.append(table);
    workspace.append(places, listing); body.append(workspace);
    const filename = field(body, 'File name', 'filename');
    choice(body, 'Files of type', 'filter', filters.map((filter, i) => [i, `${filter[0]} (${filter[1]})`]), refresh);
    function accept() {
      const name = String(session.draft.filename).trim();
      if (!name) { error('Enter a file name.'); filename.focus(); return; }
      const path = name.startsWith('/') ? name : session.draft.path.replace(/\/$/, '') + '/' + name;
      finish(path);
    }
    function refresh() {
      if (!session.root) return;
      entries.replaceChildren();
      const globs = (filters[session.draft.filter]?.[1] || '*').split(/\s+/);
      for (const file of files) {
        const filePath = file.path || session.draft.path.replace(/\/$/, '') + '/' + file.name;
        const parent = filePath.slice(0, filePath.lastIndexOf('/')) || '/';
        if (file.path && parent !== session.draft.path.replace(/\/$/, '') && !(parent === '/' && session.draft.path === '/')) continue;
        if (!file.directory && !globs.some(glob => glob === '*' || glob === '*.*' ||
            String(file.name).toLowerCase().endsWith(glob.replace(/^\*/, '').toLowerCase()))) continue;
        const row = element('tr', 'file-entry');
        const name = element('td', 'file-name');
        const item = button((file.directory ? '▸ ' : '') + file.name, () => {
          session.draft.filename = file.directory ? '' : file.name; filename.value = session.draft.filename;
          entries.querySelectorAll('[aria-selected]').forEach(node => node.removeAttribute('aria-selected'));
          row.setAttribute('aria-selected', 'true'); changed();
        }, 'file-item');
        item.ondblclick = () => {
          if (file.directory) {
            session.draft.path = filePath; pathInput.value = filePath; refresh(); changed();
          } else accept();
        };
        name.append(item); row.append(name, element('td', 'file-size', file.size || ''), element('td', 'file-date', file.modified || ''));
        entries.append(row);
      }
    }
    buttons(accept, saving ? 'Save' : 'Open'); refresh();
  }

  function parseColor(value) {
    const text = Array.isArray(value) ? value.join(',') : String(value ?? '').trim();
    if (/^#(?:[a-f\d]{3}|[a-f\d]{6})$/i.test(text)) {
      const hex = text.length === 4 ? [...text.slice(1)].map(c => c + c).join('') : text.slice(1);
      return [0,2,4].map(i => parseInt(hex.slice(i, i + 2), 16));
    }
    const match = text.match(/^(rgba?)\s*\(([^()]*)\)$/i);
    const parts = (match ? match[2] : text).trim().split(/[,\s]+/);
    const count = match?.[1].toLowerCase() === 'rgba' ? 4 : 3;
    if (parts.length !== count || parts.some(p => !/^[+-]?\d+$/.test(p) || +p < 0 || +p > 255)) return null;
    return parts.slice(0, 3).map(Number);
  }
  const hexColor = rgb => '#' + rgb.map(c => c.toString(16).padStart(2, '0')).join('');
  function toHSV(rgb) {
    const [r,g,b] = rgb.map(c => c / 255), max = Math.max(r,g,b), min = Math.min(r,g,b), delta = max - min;
    let h = delta === 0 ? 0 : max === r ? ((g-b)/delta) % 6 : max === g ? (b-r)/delta + 2 : (r-g)/delta + 4;
    return [(h * 60 + 360) % 360, max ? delta/max : 0, max];
  }
  function fromHSV(h, s, v) {
    const c = v*s, x = c*(1-Math.abs((h/60)%2-1)), m = v-c;
    const parts = h < 60 ? [c,x,0] : h < 120 ? [x,c,0] : h < 180 ? [0,c,x] :
      h < 240 ? [0,x,c] : h < 300 ? [x,0,c] : [c,0,x];
    return parts.map(n => Math.round((n+m)*255));
  }
  function renderColor() {
    const session = current, options = session.options;
    let rgb = parseColor(options.color ?? session.draft.color ?? '#ffffff');
    if (!rgb) throw new TypeError('Color must be #rgb, #rrggbb or three RGB bytes');
    const original = rgb.slice();
    if (options.layerName != null) {
      const peak = Math.max(...rgb); rgb = peak ? rgb.map(c => Math.round(c*255/peak)) : [255,255,255];
    }
    let hsv = toHSV(rgb);
    session.draft = {color: rgb, hex: hexColor(rgb)};
    const body = shell(options.title || (options.layerName != null ? 'Layer color: ' + options.layerName : 'Color'), 'color');
    const layout = element('div', 'color-layout'), left = element('div', 'color-left'), controls = element('div', 'color-controls');
    const plane = element('div', 'color-plane'), cursor = element('span', 'color-cursor');
    plane.tabIndex = 0; plane.setAttribute('role', 'group');
    plane.setAttribute('aria-label', 'Saturation and value. Arrow keys adjust; sliders provide individual values.');
    plane.append(cursor); left.append(plane);
    const swatches = element('div', 'swatches');
    // OURS: synthetic palette, not a claimed desktop palette export.
    ['#000000','#808080','#ffffff','#ff0000','#ffff00','#00ff00','#00ffff','#0000ff','#ff00ff','#2674df','#dc3c3c','#28aa46'].forEach(color => {
      const swatch = button('', () => update(parseColor(color)), 'swatch');
      swatch.style.backgroundColor = color; swatch.setAttribute('aria-label', color); swatches.append(swatch);
    });
    left.append(swatches); layout.append(left, controls); body.append(layout);
    const sliders = [];
    ['Red','Green','Blue','Hue','Saturation','Value'].forEach((label, index) => {
      const row = element('label', 'color-row');
      const slider = element('input', 'slider'); slider.type = 'range'; slider.min = 0;
      slider.max = index < 3 ? 255 : index === 3 ? 359 : 100; slider.step = 1;
      const output = element('output', 'color-number');
      slider.oninput = () => {
        if (index < 3) { const next = rgb.slice(); next[index] = +slider.value; update(next); }
        else { hsv[index-3] = +slider.value / (index > 3 ? 100 : 1); update(fromHSV(...hsv), true); }
      };
      row.append(element('span', 'label', label), slider, output); controls.append(row); sliders.push([slider, output]);
    });
    const text = field(body, 'HTML / RGB', 'hex', {input: readColorText});
    function readColorText() {
      const parsed = parseColor(text.value);
      text.setAttribute('aria-invalid', String(!parsed));
      if (parsed) { error(''); update(parsed, false, true); }
      else error('Enter #rgb, #rrggbb or RGB byte values (0..255).');
    }
    const samples = element('div', 'color-samples');
    const old = element('div', 'color-sample', 'Current'), next = element('div', 'color-sample', 'New');
    old.style.setProperty('--cad-swatch', hexColor(original)); samples.append(old, next); body.append(samples);
    function update(value, keepHSV = false, keepText = false) {
      rgb = value.slice(); if (!keepHSV) hsv = toHSV(rgb);
      session.draft.color = rgb.slice(); session.draft.hex = keepText ? text.value : hexColor(rgb);
      if (!keepText) { text.value = session.draft.hex; text.setAttribute('aria-invalid', 'false'); error(''); }
      const values = [...rgb, Math.round(hsv[0]), Math.round(hsv[1]*100), Math.round(hsv[2]*100)];
      sliders.forEach(([input, output], i) => { input.value = values[i]; output.textContent = values[i]; });
      plane.style.setProperty('--cad-hue', `hsl(${hsv[0]} 100% 50%)`);
      cursor.style.left = hsv[1]*100 + '%'; cursor.style.top = (1-hsv[2])*100 + '%';
      next.style.setProperty('--cad-swatch', hexColor(rgb)); changed();
    }
    function point(event) {
      const bounds = plane.getBoundingClientRect();
      hsv[1] = Math.max(0, Math.min(1, (event.clientX-bounds.left)/bounds.width));
      hsv[2] = 1-Math.max(0, Math.min(1, (event.clientY-bounds.top)/bounds.height));
      update(fromHSV(...hsv), true);
    }
    plane.onpointerdown = event => { if (event.button !== 0) return; plane.setPointerCapture(event.pointerId); point(event); };
    plane.onpointermove = event => { if (plane.hasPointerCapture(event.pointerId)) point(event); };
    plane.onkeydown = event => {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const index = ['ArrowLeft','ArrowRight'].includes(event.key) ? 1 : 2;
      hsv[index] = Math.max(0, Math.min(1, hsv[index] + (['ArrowRight','ArrowUp'].includes(event.key) ? .01 : -.01)));
      update(fromHSV(...hsv), true);
    };
    buttons(() => { const parsed = parseColor(text.value); if (parsed) finish(parsed); else error('Invalid color.'); });
    update(rgb);
  }

  function renderPrint() {
    const session = current, options = session.options;
    const views = options.views?.length ? options.views : ['Perspective','Top','Front','Right'];
    session.draft = {destination: 0, cdr_version: '15', size: 'Letter', orientation: 'Landscape',
      color: 'PrintColor', view: views[0], area: 'Viewport', scale: 'Fit', width: '0.13',
      selected_only: false, custom_w: 210, custom_h: 297,
      ...(data().panels?.preference_defaults?.print_settings || {}), ...session.draft};
    const body = shell(options.title || 'Print Setup', 'print');
    const controls = element('div', 'print-controls'), preview = element('div', 'print-preview');
    preview.setAttribute('aria-label', 'Page preview');
    body.append(controls, preview);
    function rebuild() {
      controls.replaceChildren();
      choice(controls, 'Printer', 'destination', DESTINATIONS.map(([n,e], i) => [i, `${n} (${e})`]), rebuild);
      if (+session.draft.destination === 1) choice(controls, 'CorelDRAW version', 'cdr_version', ['15','17']);
      choice(controls, 'Size', 'size', [...Object.entries(PAGE_SIZES).map(([n, wh]) => [n, `${n}  (${wh.join(' x ')} mm)`]), ['Custom','Custom...']], rebuild);
      if (session.draft.size === 'Custom') {
        field(controls, '  Width (mm)', 'custom_w', {type: 'number', min: .001, max: 5000, step: 'any', input: drawPage});
        field(controls, '  Height (mm)', 'custom_h', {type: 'number', min: .001, max: 5000, step: 'any', input: drawPage});
      }
      const orientation = choice(controls, 'Orientation', 'orientation', ['Landscape','Portrait'], drawPage, session.draft.size === 'Custom');
      if (session.draft.size === 'Custom') orientation.textContent = '(from width x height)';
      choice(controls, 'Output color', 'color', [['PrintColor','Print color'],['DisplayColor','Display color'],['BlackWhite','Black and white']], drawPage);
      choice(controls, 'View', 'view', views, drawPage);
      choice(controls, 'Area', 'area', ['Viewport','Extents']);
      choice(controls, 'Scale (paper = model)', 'scale', ['Fit','1:1','1:2','1:5','1:10','1:20','1:50','1:100','2:1','5:1','10:1']);
      choice(controls, 'Default line width', 'width', ['Hairline','0.13','0.18','0.25','0.30','0.35','0.50','0.60','0.70','0.80','0.90','1.00','1.20','1.40','2.00'].map(v => [v, v === 'Hairline' ? v : v + ' mm']), drawPage);
      check(controls, 'Only selected objects', 'selected_only'); drawPage();
    }
    function drawPage() {
      preview.replaceChildren();
      const state = session.draft;
      let [w,h] = state.size === 'Custom' ? [+state.custom_w, +state.custom_h] : PAGE_SIZES[state.size] || PAGE_SIZES.Letter;
      if (![w,h].every(value => Number.isFinite(value) && value > 0 && value <= 5000)) {
        error('Custom sheet dimensions must be greater than 0 and at most 5000 mm.'); return false;
      }
      error('');
      if (state.size !== 'Custom' && state.orientation === 'Landscape') [w,h] = [Math.max(w,h), Math.min(w,h)];
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'cad-dialog-sheet'); svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `${w} x ${h} mm page`);
      const paper = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      paper.setAttribute('width', String(w)); paper.setAttribute('height', String(h));
      paper.setAttribute('fill', '#ffffff'); svg.append(paper);
      for (const line of options.previewLines || []) {
        if (!Array.isArray(line.points) || !line.points.every(p => p.length === 2 && p.every(Number.isFinite))) continue;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        path.setAttribute('points', line.points.map(p => p.join(',')).join(' ')); path.setAttribute('fill', 'none');
        const rgb = parseColor(line.color || '#000000');
        path.setAttribute('stroke', state.color === 'BlackWhite' || !rgb ? '#000000' : hexColor(rgb));
        path.setAttribute('stroke-width', state.width === 'Hairline' ? '.0762' : String(+state.width || .13));
        svg.append(path);
      }
      preview.append(svg); return true;
    }
    session.footer.append(button('Print', () => {
      if (!drawPage()) return;
      const result = copy(session.draft);
      result.custom_w = +result.custom_w; result.custom_h = +result.custom_h;
      notify(session, 'onAction', 'print', result, meta(session, 'simulated'));
    }, 'button cad-dialog-primary'), button('Close', close));
    rebuild();
  }

  function renderPicker() {
    const session = current;
    const candidates = copy(session.options.candidates || []);
    const body = shell(session.options.title || 'Choose object', 'picker');
    body.setAttribute('role', 'menu');
    session.footer.hidden = true;
    let index = -1;
    const rows = candidates.map((candidate, i) => {
      const row = button(candidate.label, () => finish(candidate.key), 'menu-item');
      row.setAttribute('role', 'menuitem');
      row.onpointerenter = () => highlight(i);
      row.onfocus = () => highlight(i);
      body.append(row); return row;
    });
    function highlight(i) {
      if (index === i) return;
      rows[index]?.classList.remove('cad-dialog-highlight'); index = i;
      rows[index]?.classList.add('cad-dialog-highlight');
      session.highlighted = candidates[index]?.key ?? null;
      notify(session, 'onHover', copy(session.highlighted));
    }
    session.root.addEventListener('keydown', event => {
      if (['ArrowDown','ArrowUp','Home','End'].includes(event.key) && rows.length) {
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length-1 :
          index < 0 ? (event.key === 'ArrowDown' ? 0 : rows.length-1) :
          (index + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
        highlight(next); rows[next].focus();
      }
    });
    session.root.tabIndex = -1;
    session.afterOpen = () => {
      // showModal autofocus would otherwise highlight/select the first candidate.
      session.root.focus(); highlight(-1);
      const anchor = session.options.anchor;
      if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(anchor.y)) return;
      const rect = session.root.getBoundingClientRect();
      const scale = parseFloat(getComputedStyle(session.root).zoom) || 1;
      session.root.style.margin = '0';
      session.root.style.left = Math.max(4, Math.min(anchor.x, window.innerWidth-rect.width-4))/scale + 'px';
      session.root.style.top = Math.max(4, Math.min(anchor.y+3*scale, window.innerHeight-rect.height-4))/scale + 'px';
    };
  }

  function renderUnits() {
    const session = current, toleranceOnly = session.name === 'tolerance';
    session.draft = {model_units: 'Millimeters', absolute_tolerance: toleranceOnly ? .01 : .001,
      scale_model: true, ...session.draft};
    const body = shell(session.options.title || (toleranceOnly ? 'Absolute Tolerance' : 'Units'), session.name);
    body.append(element('p', 'section-label', toleranceOnly ? 'In ' + session.draft.model_units.toLowerCase() : 'Units and tolerances'));
    if (!toleranceOnly) choice(body, 'Model units', 'model_units', UNITS);
    const input = field(body, toleranceOnly ? 'Tolerance' : 'Absolute tolerance', 'absolute_tolerance');
    if (!toleranceOnly) check(body, 'Scale the model to match the new units', 'scale_model');
    buttons(() => {
      const value = +session.draft.absolute_tolerance;
      if (!Number.isFinite(value) || value <= 0 || (!toleranceOnly && value < 1e-9)) {
        error(toleranceOnly ? 'Absolute tolerance must be a finite positive number' : 'Absolute tolerance must be at least 0.000000001');
        input.setAttribute('aria-invalid', 'true'); input.focus(); return;
      }
      finish({...session.draft, absolute_tolerance: value});
    });
  }
  function renderFilletRadius() {
    current.draft = {radius: '1', ...current.draft};
    const body = shell('Fillet Radius', 'fillet-radius');
    field(body, 'Radius', 'radius');
    buttons(() => finish(current.draft.radius));
  }
  function renderSaveChanges() {
    shell(current.options.title || 'Narwhal 3D', 'save-changes').append(
      element('p', 'question', `Save changes to ${current.options.documentTitle || 'Untitled'}?`));
    current.footer.append(button('Yes', () => finish('yes'), 'button cad-dialog-primary'),
      button('No', () => finish('no')), button('Cancel', close));
  }
  function open(name, options = {}) {
    name = String(name).toLowerCase(); name = ALIASES[name] || name;
    if (!CATALOG.some(item => item.name === name)) throw new RangeError('Unknown Narwhal dialog: ' + name);
    close();
    current = {name, options: options || {}, draft: copy(options?.values || {}), previousFocus: document.activeElement};
    try {
      ({preferences: renderPreferences, open: renderFile, save: renderFile, color: renderColor,
        print: renderPrint, picker: renderPicker, units: renderUnits, tolerance: renderUnits,
        'save-changes': renderSaveChanges, 'fillet-radius': renderFilletRadius})[name]();
      document.body.append(current.root); applyTheme(); current.root.showModal();
      current.afterOpen?.(); return snapshot();
    } catch (exception) {
      current?.root?.remove(); current = null; throw exception;
    }
  }
  window.NarwhalDialogs = Object.freeze({open, close, setTheme,
    catalog: () => copy(CATALOG), snapshot});
})();

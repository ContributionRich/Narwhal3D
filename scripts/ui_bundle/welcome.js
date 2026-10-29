/* Editable HTML projection of native/welcome/src/main.rs, not a runtime launcher.
 * OURS: browser DOM/accessibility and explicit synthetic state replace egui/IPC.
 * No requests, file picker, settings I/O, updater, or automatic ready/dismiss timer.
 * open() updates an already-open window; omitted options retain their current value.
 * New sessions default to light / Recent / preparing / empty recent / idle News.
 * Optional fixtures: recent: [path | {path, name}], loading: boolean,
 * elapsed: seconds (freezes progress), news: {state, version, title, notes}.
 * News states: idle, checking, unavailable, unsafe, current, available, updating.
 * Available/current/updating require an explicit fixture version (no invented release).
 * Native action callbacks: onNew(unit), onOpen(path?) [absent for file browser],
 * onClose(). Also emits document CustomEvent('narwhal-welcome', {detail}), whose
 * action is show, state, tab, theme, new, open-dialog, open, close, download,
 * or release-page. News actions are intent-only; fixtures never install anything.
 */
(function () {
  "use strict";

  // main.rs:20,42–48,1224–1228. Values passed to callbacks are native unit names.
  const UNITS = ["Millimeters", "Centimeters", "Meters", "Inches", "Feet"];
  const TABS = ["New", "Recent", "Open", "About"];
  const NEWS_STATES = ["idle", "checking", "unavailable", "unsafe", "current", "available", "updating"];
  let state = null;
  let root = null;
  let content = null;
  let newsPanel = null;
  let statusLabel = null;
  let progress = null;
  let fill = null;
  let tabButtons = [];
  let callbacks = {};
  let previousFocus = null;
  let clock = 0;
  let started = 0;
  let preferredTheme = "light";

  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(text, className, onClick) {
    const node = element("button", "welcome-button " + className, text);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function themeName(value) {
    const name = String(value).trim().toLowerCase();
    if (name !== "light" && name !== "dark") throw new TypeError("Welcome theme must be light or dark");
    return name;
  }

  function snapshot() {
    if (!state) return { open: false, theme: preferredTheme };
    // Plain, detached data only; no DOM, callbacks, paths read from disk, or providers.
    return JSON.parse(JSON.stringify(state));
  }

  function emit(action, extra) {
    document.dispatchEvent(new CustomEvent("narwhal-welcome", {
      detail: Object.assign({ action, snapshot: snapshot(), synthetic: true }, extra)
    }));
  }

  function setTheme(name) {
    preferredTheme = themeName(name);
    if (state) state.theme = preferredTheme;
    if (root) root.dataset.theme = preferredTheme;
    emit("theme");
    return snapshot();
  }

  function close(reason = "api") {
    if (!root) return snapshot();
    const onClose = callbacks.onClose;
    root.remove();
    root = null;
    window.clearInterval(clock);
    window.removeEventListener("resize", center);
    document.removeEventListener("scroll", center, true);
    document.removeEventListener("keydown", onKey, true);
    state.open = false;
    const restore = previousFocus;
    previousFocus = null;
    if (restore && restore.isConnected && typeof restore.focus === "function") restore.focus({ preventScroll: true });
    emit("close", { reason });
    if (typeof onClose === "function") onClose();
    return snapshot();
  }

  function submit(action, argument) {
    // main.rs:1014–1030: submit updates status and hides immediately, even before ready.
    state.status = action === "new" ? "Creating a new model…" :
      action === "open" ? "Opening selected model…" : "Opening file browser…";
    state.lastRequest = { action, argument: argument === undefined ? null : argument };
    const callback = action === "new" ? callbacks.onNew : callbacks.onOpen;
    emit(action, { argument: state.lastRequest.argument });
    close(action);
    if (typeof callback === "function") {
      if (argument === undefined) callback();
      else callback(argument);
    }
  }

  function selectTab(name, focus) {
    state.tab = name;
    tabButtons.forEach((node, index) => {
      node.setAttribute("aria-selected", String(TABS[index] === name));
      node.tabIndex = TABS[index] === name ? 0 : -1;
    });
    content.setAttribute("aria-labelledby", "welcome-tab-" + name.toLowerCase());
    renderContent();
    if (focus) tabButtons[TABS.indexOf(name)].focus();
    emit("tab", { tab: name });
  }

  function renderContent() {
    content.replaceChildren();
    content.dataset.tab = state.tab.toLowerCase();
    if (state.tab === "New") {
      content.append(element("h2", "welcome-heading welcome-strong", "Start a new model"),
        element("p", "welcome-label", "Choose the document unit system:"));
      const choices = element("div", "welcome-units");
      UNITS.forEach(unit => choices.append(button(unit, "welcome-unit", () => submit("new", unit))));
      content.append(choices);
    } else if (state.tab === "Open") {
      content.append(element("h2", "welcome-heading welcome-strong", "Open a model"),
        element("p", "welcome-label", "Open a .3dm or another supported model file."),
        button("Choose a model…", "welcome-choose", () => submit("open-dialog")));
    } else if (state.tab === "About") {
      content.append(element("h2", "welcome-heading", "Narwhal 3D"),
        element("p", "welcome-label", "Early precision NURBS CAD preview"),
        element("p", "welcome-label", "Open-source, independently developed, and still under active construction."));
    } else if (!state.recent.length) {
      const empty = element("div", "welcome-empty");
      empty.append(element("p", "welcome-empty-title", "No recent models yet"),
        element("p", "welcome-label", "Use Open… to choose a .3dm model."));
      content.append(empty);
    } else {
      // main.rs:1285–1325: four columns; these are text tiles, not model thumbnails.
      const grid = element("div", "welcome-recent-grid");
      state.recent.forEach(item => {
        const cell = element("div", "welcome-recent-item");
        const tile = button("NARWHAL\n3DM", "welcome-recent-preview", () => submit("open", item.path));
        tile.title = item.path;
        tile.setAttribute("aria-label", item.name);
        const label = element("p", "welcome-recent-name", item.name);
        label.title = item.name;
        cell.append(tile, label);
        grid.append(cell);
      });
      content.append(grid);
    }
  }

  function renderNews() {
    // main.rs:1176–1210. All outcomes are explicitly supplied fixtures, never fetched.
    newsPanel.replaceChildren(element("h2", "welcome-heading", "News"));
    const news = state.news;
    const label = text => newsPanel.append(element("p", "welcome-label", text));
    const releasePage = () => {
      const link = button("Open release page", "welcome-link", () => emit("release-page"));
      link.setAttribute("role", "link");
      newsPanel.append(link);
    };
    switch (news.state) {
      case "available":
      case "updating": {
        newsPanel.append(element("p", "welcome-label welcome-strong", "Version " + news.version + " available"));
        label(news.title);
        label(news.notes);
        const install = button(news.state === "updating" ? "Updating…" : "Download and install",
          "welcome-install", () => emit("download", { version: news.version }));
        install.disabled = news.state === "updating";
        newsPanel.append(install);
        releasePage();
        if (news.state === "updating") label("Downloading and verifying update…");
        break;
      }
      case "current": label("Version " + news.version + " is up to date."); break;
      case "checking": label("Checking for updates…"); break;
      case "unavailable": label("Update check unavailable in this package."); releasePage(); break;
      case "unsafe": label("Could not check for updates safely."); releasePage(); break;
      default: label("Rust acceleration and startup work are in progress.");
    }
  }

  function updateProgress() {
    if (!root) return;
    // main.rs:286–291: looping fill, floor 4%, ready exactly 100%.
    const elapsed = state.elapsed === null ? (performance.now() - started) / 1000 : state.elapsed;
    state.progress = state.ready ? 1 : Math.max(0.04, (elapsed * 0.55) % 1);
    progress.setAttribute("aria-valuenow", String(Math.round(state.progress * 100)));
    fill.style.width = (state.progress * 100) + "%";
    // egui 0.36.2 widgets/progress_bar.rs: 20-point, radius-7 arc and pulsing fill.
    const arc = progress.querySelector(".welcome-progress-arc");
    arc.style.display = state.ready ? "none" : "block";
    fill.style.setProperty("--welcome-progress-opacity", state.ready ? "1" : String(0.7 + 0.3 * Math.abs(Math.cos(elapsed))));
    if (!state.ready) {
      const start = elapsed * Math.PI * 2;
      const sweep = (240 * Math.PI / 180) * Math.sin(elapsed);
      const points = Array.from({ length: 20 }, (_, index) => {
        const angle = start + sweep * index / 20;
        return (9 + 7 * Math.cos(angle)) + "," + (9 + 7 * Math.sin(angle));
      });
      arc.firstChild.setAttribute("points", points.join(" "));
    }
  }

  function center() {
    if (!root) return;
    // OURS: app-centering, not the native window manager; body portal avoids clipping.
    const app = document.getElementById("app") || document.querySelector(".app");
    const rect = app ? app.getBoundingClientRect() : { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
    root.style.left = (rect.left + rect.width / 2) + "px";
    root.style.top = (rect.top + rect.height / 2) + "px";
    // The parent may set --preview-scale on the app rather than :root.
    root.style.removeProperty("--preview-scale");
    const scale = app && window.getComputedStyle(app).getPropertyValue("--preview-scale").trim();
    // Leave shared/root scale inherited so the parent's scale slider stays live.
    const inherited = window.getComputedStyle(document.body).getPropertyValue("--preview-scale").trim();
    if (scale && scale !== inherited) root.style.setProperty("--preview-scale", scale);
  }

  function onKey(event) {
    if (!root) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      close("escape");
    } else if (root.contains(event.target)) {
      // OURS: keep shell command shortcuts out of this independent native-like window.
      event.stopPropagation();
      if (event.key === "Tab") {
        const controls = Array.from(root.querySelectorAll("button:not(:disabled), [tabindex='0']"))
          .filter(node => node.tabIndex >= 0);
        const index = controls.indexOf(document.activeElement);
        if (event.shiftKey && index <= 0) {
          event.preventDefault();
          controls[controls.length - 1].focus();
        } else if (!event.shiftKey && (index < 0 || index === controls.length - 1)) {
          event.preventDefault();
          controls[0].focus();
        }
      } else if (tabButtons.includes(event.target) && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const index = TABS.indexOf(state.tab);
        const next = event.key === "Home" ? 0 : event.key === "End" ? 3 :
          (index + (event.key === "ArrowRight" ? 1 : 3)) % 4;
        selectTab(TABS[next], true);
      }
    }
  }

  function build() {
    previousFocus = document.activeElement;
    root = element("section", "welcome-window");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", "Narwhal 3D");
    root.tabIndex = -1;
    const header = element("header", "welcome-header");
    const tabs = element("div", "welcome-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Welcome");
    tabButtons = TABS.map(name => {
      const node = button(name === "Open" ? "Open…" : name, "welcome-tab", () => selectTab(name));
      node.id = "welcome-tab-" + name.toLowerCase();
      node.setAttribute("role", "tab");
      node.setAttribute("aria-controls", "welcome-content");
      tabs.append(node);
      return node;
    });
    const dismiss = button("×", "welcome-close", () => close("button"));
    dismiss.title = "Close welcome window";
    dismiss.setAttribute("aria-label", dismiss.title);
    header.append(tabs, element("span", "welcome-preview", "EARLY PREVIEW"), dismiss);
    const body = element("div", "welcome-body");
    content = element("div", "welcome-content");
    content.id = "welcome-content";
    content.setAttribute("role", "tabpanel");
    content.tabIndex = 0;
    const divider = element("div", "welcome-divider");
    divider.setAttribute("aria-hidden", "true");
    const sidebar = element("aside", "welcome-sidebar");
    newsPanel = element("section", "welcome-news");
    const tips = element("section", "welcome-tips");
    tips.append(element("h2", "welcome-heading", "Tips"),
      element("p", "welcome-label", "Use the viewport-title arrow to change display mode."));
    const branding = element("div", "welcome-branding");
    const logo = element("img", "welcome-logo");
    // main.rs:879–888 embeds launch/narwhal3d.png; build_ui_bundle.py:asset_destination.
    logo.src = "assets/branding/narwhal3d.png";
    logo.alt = "";
    logo.width = 85;
    logo.height = 85;
    branding.append(logo, element("p", "welcome-brand-name", "Narwhal 3D"));
    sidebar.append(newsPanel, tips, branding);
    body.append(content, divider, sidebar);
    const footer = element("footer", "welcome-footer");
    statusLabel = element("p", "welcome-status");
    statusLabel.setAttribute("role", "status");
    progress = element("div", "welcome-progress");
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-label", "Preparing Narwhal 3D");
    progress.setAttribute("aria-valuemin", "0");
    progress.setAttribute("aria-valuemax", "100");
    fill = element("div", "welcome-progress-fill");
    const arc = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    arc.setAttribute("class", "welcome-progress-arc");
    arc.setAttribute("viewBox", "0 0 18 18");
    arc.setAttribute("aria-hidden", "true");
    arc.append(document.createElementNS("http://www.w3.org/2000/svg", "polyline"));
    fill.append(arc);
    progress.append(fill);
    footer.append(statusLabel, progress);
    root.append(header, body, footer);
    document.body.append(root);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", center);
    document.addEventListener("scroll", center, true);
    clock = window.setInterval(updateProgress, 33);
  }

  function open(options = {}) {
    const fresh = !root;
    const next = fresh ? {
      open: true, theme: preferredTheme, tab: "Recent", ready: false, loading: false,
      recent: [], news: { state: "idle" }, elapsed: null, progress: 0.04,
      status: "Preparing Narwhal 3D…", lastRequest: null
    } : snapshot();
    if (options.theme !== undefined) next.theme = themeName(options.theme);
    if (options.tab !== undefined) {
      next.tab = TABS.find(name => name.toLowerCase() === String(options.tab).toLowerCase().replace(/…$/, ""));
      if (!next.tab) throw new TypeError("Unknown welcome tab");
    }
    if (options.ready !== undefined) next.ready = Boolean(options.ready);
    if (options.loading !== undefined) next.loading = Boolean(options.loading);
    if (options.elapsed !== undefined) {
      if (options.elapsed !== null && (!Number.isFinite(options.elapsed) || options.elapsed < 0))
        throw new TypeError("Welcome elapsed must be null or nonnegative seconds");
      next.elapsed = options.elapsed;
    }
    if (options.recent !== undefined) {
      if (!Array.isArray(options.recent)) throw new TypeError("Welcome recent must be an array");
      next.recent = options.recent.map(item => {
        const path = typeof item === "string" ? item : item && item.path;
        if (typeof path !== "string") throw new TypeError("Recent fixtures require a path string");
        return { path, name: String((item && item.name) || path.split(/[\\/]/).pop() || "Model.3dm") };
      });
    }
    if (options.news !== undefined) {
      const news = options.news;
      if (!news || !NEWS_STATES.includes(news.state)) throw new TypeError("Unknown synthetic News state");
      if (["available", "updating", "current"].includes(news.state) && !news.version)
        throw new TypeError("Release fixtures require a version");
      next.news = { state: news.state, version: String(news.version || ""), title: String(news.title || ""), notes: String(news.notes || "") };
    }
    const contentChanged = fresh || state.tab !== next.tab || options.recent !== undefined;
    const newsChanged = fresh || options.news !== undefined;
    const contentHadFocus = !fresh && content.contains(document.activeElement);
    const newsHadFocus = !fresh && newsPanel.contains(document.activeElement);
    state = next;
    preferredTheme = state.theme;
    if (fresh) { callbacks = {}; started = performance.now(); }
    ["onNew", "onOpen", "onClose"].forEach(key => {
      if (Object.prototype.hasOwnProperty.call(options, key)) callbacks[key] = options[key];
    });
    if (fresh) build();
    root.dataset.theme = state.theme;
    state.status = state.ready ? "Ready" : state.loading ? "Loading application components…" : "Preparing Narwhal 3D…";
    statusLabel.textContent = state.status;
    progress.setAttribute("aria-label", state.ready ? "Ready" : "Preparing Narwhal 3D");
    if (contentChanged) selectTab(state.tab);
    if (newsChanged) renderNews();
    updateProgress();
    center();
    if (fresh || (contentChanged && contentHadFocus) || (newsChanged && newsHadFocus))
      tabButtons[TABS.indexOf(state.tab)].focus({ preventScroll: true });
    emit(fresh ? "show" : "state");
    return snapshot();
  }

  window.NarwhalWelcome = { open, close, setTheme, snapshot };
})();

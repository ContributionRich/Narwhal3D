#!/usr/bin/env python3
"""Designer bundle orchestration, not product code. Python reuses the existing
Blender exporter and stdlib archive tools; no new application compute path.

--prepare validates a temporary complete bundle without touching dist. Final builds
must follow Main's picker integration. GUI captures are deliberately not automatic.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BLENDER = ROOT / "tools/blender-patched/blender"
NAME = "Narwhal3D-UI-Designer"
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
PLAYGROUND_FILES = (
    'app.js', 'style.css', 'welcome.js', 'welcome.css', 'dialogs.js', 'dialogs.css',
    'fonts/Inter.woff2', 'fonts/OFL-1.1.txt', 'fonts/Ubuntu-Light.ttf', 'fonts/UFL.txt',
    'fonts/README.md', 'references/workspace-light.png',
)

# Entry points, not a repository copy. Relative Python dependencies are followed
# recursively, including new imported picker modules without a stale hand list.
ENTRY_POINTS = (
    "narwhal3d/__init__.py", "narwhal3d/ui.py", "narwhal3d/design.py",
    "narwhal3d/ai_settings_popup.py", "narwhal3d/shell_popup.py",
    "narwhal3d/filedialog.py", "narwhal3d/printdialog.py",
    "narwhal3d/colordialog.py", "launch/bootstrap.py",
)
EXTRA_SOURCE = (
    "blender_patches/app_drawn.patch", "blender_patches/apply_app_drawn.py",
    "blender_patches/apply_window_overlay.py",
    "blender_patches/apply_statusbar_height.py",
    "blender_patches/apply_deferred_first_present.py",
    "blender_patches/apply_narwhal3d_branding.py",
    "scripts/build_ui_bundle.py", "scripts/ui_bundle_export.py", "scripts/create_public_branch.py",
    "scripts/ui_bundle_shots.py", "scripts/ui_bundle/test_preview.cjs",
)
ASSET_ROOTS = ("narwhal3d/icons", "artwork/icons", "artwork/Logo")
NATIVE_ROOTS = ("native/welcome", "native/pure_core", "native/selection_core", "native/platform_core")
FORBIDDEN_PARTS = frozenset({"More_Data", "vendor", "tools", "bug_reports", "artifacts",
                            "tmp", ".git", "__pycache__", "target", "windows build"})
FORBIDDEN_SUFFIXES = {".3dm", ".blend", ".blend1", ".dll", ".so", ".exe", ".pyc", ".env"}

# Explicit region map, complemented by every function/class in source_symbols.json.
SURFACES = {
    "shell/layout": ("ui.py", "views.py", "uiscale.py", "theme_bindings.py", "design.py"),
    "menus/toolbars/flyouts/tooltips": ("menubar.py", "menubar_resource_data.py", "toolbars.py", "toolbar_data.py", "flyout_icon_data.py"),
    "console/history/autocomplete/options": ("cmdbar.py", "command_history.py", "shell_input.py", "textfield.py", "options.py"),
    "viewports/title menus/navigation/grid/selection/gumball": ("views.py", "viewport_menu_data.py", "navigate.py", "grid_render.py", "select.py", "gumball.py", "preview.py"),
    "right/Layers": ("rightpanel.py", "layers.py", "layer_commands.py"),
    "right/Properties": ("rightpanel.py", "object_properties.py", "user_text.py"),
    "right/Display": ("rightpanel.py", "display_modes.py", "display_commands.py"),
    "right/Bug Report": ("bugreport.py", "textfield.py"),
    "right/AI chat": ("ai_assistant.py", "ai_transport.py", "textfield.py", "ui.py"),
    "right/AI settings popup": ("ai_settings_popup.py", "glyphs.py", "preferences.py"),
    "status/snaps/units/filters": ("statusbar.py", "osnap.py", "selection_filter.py", "units.py"),
    "dialogs/preferences/files/print/color": ("shell_popup.py", "preferences.py", "filedialog.py", "printdialog.py", "colordialog.py"),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if (not name or name != p.as_posix() or p.is_absolute() or "\\" in name
            or ":" in name or any(part in ("..", ".") for part in p.parts)
            or any(part in FORBIDDEN_PARTS or part == ".env" or part.startswith(".env.") for part in p.parts)
            or p.suffix.lower() in FORBIDDEN_SUFFIXES):
        raise ValueError(f"unsafe/excluded bundle path: {name}")
    return p


def read_input(root, rel):
    safe_name(rel)
    p = root / rel
    if any(part.is_symlink() for part in (p, *p.parents) if part != root.parent):
        raise ValueError(f"symlink input refused: {rel}")
    if not p.is_file():
        raise ValueError(f"required input missing: {rel}")
    if p.stat().st_size > MAX_FILE:
        raise ValueError(f"oversized input: {rel}")
    return p.read_bytes()


def module_path(root, dotted):
    base = dotted.replace(".", "/")
    for rel in (base + ".py", base + "/__init__.py"):
        if (root / rel).is_file():
            return rel
    raise ValueError(f"required source module missing: {dotted}")


def dependencies(root, rel, data):
    tree = ast.parse(data, filename=rel)
    package = rel.removesuffix(".py").split("/")[:-1]
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                parent = package[:len(package) - node.level + 1]
                parts = parent + (node.module.split(".") if node.module else [])
                if not parts or parts[0] != "narwhal3d":
                    continue
                if node.module:
                    found.add(module_path(root, ".".join(parts)))
                    # 'from .kernel import registry' may name a child module.
                    for alias in node.names:
                        sub = "/".join(parts + [alias.name])
                        if (root / (sub + ".py")).is_file() or (root / sub / "__init__.py").is_file():
                            found.add(module_path(root, ".".join(parts + [alias.name])))
                else:
                    for alias in node.names:
                        try:
                            found.add(module_path(root, ".".join(parts + [alias.name])))
                        except ValueError:
                            init = module_path(root, ".".join(parts))
                            parent_tree = ast.parse(read_input(root, init))
                            defined = {n.name for n in parent_tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
                            defined.update(n.id for statement in parent_tree.body if isinstance(statement, ast.Assign)
                                           for n in statement.targets if isinstance(n, ast.Name))
                            if alias.name not in defined:
                                raise
                            found.add(init)
            elif node.module and node.module.startswith("narwhal3d"):
                found.add(module_path(root, node.module))
                if node.module == "narwhal3d":
                    found.update(module_path(root, "narwhal3d." + a.name) for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "narwhal3d" or alias.name.startswith("narwhal3d."):
                    found.add(module_path(root, alias.name))
    return found


def source_inventory(root=ROOT):
    pending = set(ENTRY_POINTS)
    pending.update("narwhal3d/" + name for names in SURFACES.values() for name in names)
    sources = {}
    while pending:
        rel = pending.pop()
        if rel in sources:
            continue
        data = read_input(root, rel)
        sources[rel] = data
        pending.update(dependencies(root, rel, data) - sources.keys())
    for rel in EXTRA_SOURCE:
        sources[rel] = read_input(root, rel)
    # Export only reviewed native sources, never another agent's unregistered
    # experiments merely because they exist beside a compiled module.
    public_tree = ast.parse(sources["scripts/create_public_branch.py"])
    native_public = set()
    for node in public_tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"NATIVE_FILES", "WELCOME_FILES"} for t in node.targets):
            native_public.update(ast.literal_eval(node.value))
    for base in NATIVE_ROOTS:
        directory = root / base
        if not directory.is_dir():
            raise ValueError(f"required native UI integration source missing: {base}")
        # Only authored crate sources/manifests. Never compiled/native/vendor trees.
        paths = [directory / "Cargo.toml", *directory.glob("build.rs"),
                 *directory.glob("src/**/*.rs"), *directory.glob("include/*.h")]
        for path in paths:
            rel = path.relative_to(root).as_posix()
            if rel not in native_public:
                continue
            sources[rel] = read_input(root, rel)
    return sources


def asset_inventory(root=ROOT):
    assets = {}
    for base in ASSET_ROOTS:
        directory = root / base
        if not directory.is_dir():
            raise ValueError(f"required asset directory missing: {base}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symlink asset refused: {path.relative_to(root)}")
            if path.is_dir():
                continue
            if path.suffix.lower() not in {".png", ".svg", ".md", ".ttf", ".otf", ".woff", ".woff2"}:
                raise ValueError(f"unreviewed asset type: {path.relative_to(root)}")
            rel = path.relative_to(root).as_posix()
            assets[rel] = read_input(root, rel)
    for rel in ("launch/narwhal3d.png", "blender_patches/narwhal3d_app_icon.svg"):
        assets[rel] = read_input(root, rel)
    # Catch deletion of even unused tracked artwork, not just icons in the data.
    if (root / ".git").exists():
        result = subprocess.run(["git", "--no-pager", "ls-files", "-z", "--", *ASSET_ROOTS],
                                cwd=root, check=True, capture_output=True, timeout=30)
        for rel in result.stdout.decode().split("\0"):
            if rel and rel not in assets:
                raise ValueError(f"tracked asset missing: {rel}")
    return assets


def asset_destination(rel):
    if rel.startswith("narwhal3d/icons/"):
        return rel.replace("narwhal3d/icons/", "assets/icons/", 1)
    if rel.startswith("artwork/"):
        return "assets/" + rel
    return "assets/branding/" + Path(rel).name


def clean_env(home):
    # Do NOT inherit provider keys, Python hooks, model paths or preference roots.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
           "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
           "XDG_CACHE_HOME": str(home / "cache"), "PYTHONDONTWRITEBYTECODE": "1",
           "NARWHAL3D_NO_DIALOGS": "1", "NARWHAL3D_THEME": "light"}
    return env


def export_data(out, root=ROOT):
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="narwhal-ui-home-") as home:
        result = subprocess.run(
            [str(BLENDER), "-b", "--factory-startup", "--python-exit-code", "1",
             "--python", str(root / "scripts/ui_bundle_export.py"), "--", str(out)],
            cwd=root, env=clean_env(Path(home)), capture_output=True, text=True, timeout=240)
        if result.returncode or "UI DATA EXPORTED:" not in result.stdout:
            raise RuntimeError("UI export failed (no fallback data):\n" + result.stdout[-2500:] + result.stderr[-1500:])
        print(next(line for line in result.stdout.splitlines() if line.startswith("UI DATA EXPORTED:")))
    return json.loads((out / "ui_data.json").read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def icon_names(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "icon" and item:
                found.add(item)
            found.update(icon_names(item))
    elif isinstance(value, list):
        for item in value:
            found.update(icon_names(item))
    return found


def validate_stage(stage, expected_inputs):
    manifest = json.loads((stage / "MANIFEST.json").read_text())
    entries = manifest["files"]
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
    if any(p.is_symlink() for p in stage.rglob("*")):
        raise ValueError("symlink in stage")
    if actual != set(entries) | {"MANIFEST.json"}:
        raise ValueError("manifest coverage mismatch")
    if manifest["inputs"] != expected_inputs:
        raise ValueError("source/asset input coverage mismatch")
    total = 0
    for name, record in entries.items():
        safe_name(name)
        data = (stage / name).read_bytes()
        total += len(data)
        if len(data) > MAX_FILE or record != {"sha256": digest(data), "bytes": len(data)}:
            raise ValueError(f"hash/size mismatch: {name}")
    if total > MAX_TOTAL:
        raise ValueError("bundle size limit exceeded")
    for origin, record in expected_inputs.items():
        safe_name(origin)
        dest = record["bundle_path"]
        if dest not in entries or entries[dest]["sha256"] != record["sha256"]:
            raise ValueError(f"required source/asset omitted or altered: {origin}")
    data = json.loads((stage / "data/ui_data.json").read_text())
    for icon in icon_names(data):
        safe_name(icon)
        if "assets/icons/" + icon not in entries:
            raise ValueError(f"required UI icon missing: {icon}")
    coverage = json.loads((stage / "data/coverage.json").read_text())
    for paths in coverage["surfaces"].values():
        for path in paths:
            if path not in entries:
                raise ValueError(f"UI surface source omitted: {path}")
    # All browser resources are relative and present; no network runtime or fetch.
    browser_files = {"index.html", "playground/style.css", "playground/app.js"}
    browser_files.update(name for name in entries
                         if name.startswith("playground/") and Path(name).suffix in {".css", ".js"})
    for rel in sorted(browser_files):
        text = (stage / rel).read_text()
        if re.search(r"\b(?:fetch|XMLHttpRequest|WebSocket|sendBeacon)\s*\(", text):
            raise ValueError(f"network API in playground: {rel}")
        refs = re.findall(r'(?:src|href)=["\']([^"\']+)', text)
        refs += re.findall(r'url\(["\']?([^\)"\']+)', text)
        for ref in refs:
            if ref.startswith(("#", "data:")):
                continue
            safe_name(ref)
            # CSS URLs are stylesheet-relative. DOM src/href assigned by JS,
            # like HTML attributes, resolve against index.html's document URL.
            parent = PurePosixPath(rel).parent if rel.endswith('.css') else PurePosixPath('.')
            target = str(parent / ref)
            if target not in entries:
                raise ValueError(f"missing/offline-unsafe browser resource: {target}")
    return len(entries)


def validate_zip(path, stage):
    expected = {p.relative_to(stage).as_posix(): p for p in stage.rglob("*") if p.is_file()}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate ZIP member")
        wanted = {NAME + "/" + n for n in expected}
        if set(names) != wanted:
            raise ValueError("ZIP coverage mismatch")
        if sum(i.file_size for i in archive.infolist()) > MAX_TOTAL:
            raise ValueError("ZIP uncompressed size limit")
        for info in archive.infolist():
            safe_name(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP symlink refused")
            rel = info.filename.removeprefix(NAME + "/")
            if archive.read(info) != expected[rel].read_bytes():
                raise ValueError(f"ZIP content mismatch: {rel}")
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC failure")


def make_zip(path, stage):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                archive.write(p, NAME + "/" + p.relative_to(stage).as_posix())
    validate_zip(path, stage)


def prepare(stage, root=ROOT):
    sources, assets = source_inventory(root), asset_inventory(root)
    inputs = {}
    for collection, destination in ((sources, lambda rel: "source/" + rel), (assets, asset_destination)):
        for rel, data in sorted(collection.items()):
            dst = destination(rel)
            target = stage / dst
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            inputs[rel] = {"bundle_path": dst, "sha256": digest(data), "bytes": len(data)}
    preview_files = [("scripts/ui_bundle/index.html", "index.html"),
                     ("docs/UI_DESIGNER_BUNDLE.md", "README.md")]
    preview_files += [("scripts/ui_bundle/" + name, "playground/" + name)
                      for name in PLAYGROUND_FILES]
    for src, dst in preview_files:
        data = read_input(root, src)
        target = stage / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        inputs[src] = {"bundle_path": dst, "sha256": digest(data), "bytes": len(data)}
    data = export_data(stage / "data", root)
    symbols = {}
    for rel, content in sources.items():
        if rel.endswith(".py"):
            symbols[rel] = [{"name": n.name, "line": n.lineno, "end_line": n.end_lineno,
                             "kind": type(n).__name__} for n in ast.walk(ast.parse(content))
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    write_json(stage / "data/source_symbols.json", symbols)
    write_json(stage / "data/coverage.json", {
        "surfaces": {k: ["source/narwhal3d/" + n for n in v] for k, v in SURFACES.items()},
        "native_ui_sources": list(NATIVE_ROOTS),
        "source_count": len(sources), "asset_count": len(assets),
        "source_rule": "UI entry points + recursive local Python imports + reviewed public sources of named native UI crates; source only, not runnable app",
        "package_modules_outside_closure": sorted(p.relative_to(root).as_posix() for p in (root / "narwhal3d").rglob("*.py")
                                                  if "__pycache__" not in p.parts and p.relative_to(root).as_posix() not in sources),
        "screenshots": {"status": "reference included", "files": ["playground/references/workspace-light.png"], "reason": "Inspected blank application UI-report capture; no user document or VM imagery"},
        "fonts": {"actual": "Blender blf font_id 0; runtime-supplied UI font", "source": "source/narwhal3d/ui.py:_DEFAULT_FONT_ID",
                  "packaged": "Inter.woff2 from Blender source (OFL-1.1) and Ubuntu-Light.ttf from egui default fonts (UFL), original files and license notices included",
                  "playground": "Matching font files; browser and BLF/egui rasterization can differ"},
        "mocks": ["browser interactions", "sample document/layers/properties", "AI transcript/status/model discovery", "bug draft", "viewport grid"],
        "editable_preview": ["all authored menus and submenus", "all category tools and flyouts", "five inspector panels", "AI settings", "welcome New/Recent/Open/About/loading", "Print Setup", "overlap picker", "units and tolerance", "Fillet Radius", "Gumball and all three context menus", "viewport tab menus", "View preferences", "file/color/save prompts as OS-layout approximations"],
                "browser_validation": "Run source/scripts/ui_bundle/test_preview.cjs against an extracted package; receipts are separate from source-backed layout coverage",
                        "source_only": ["CAD geometry and command execution", "unmodeled command-specific dialogs/operators: inspect source_symbols.json"],
        "excluded": ["credentials and environment values", "preferences and recent files", "user models", "bug report content", "VM captures", "More_Data", "vendor/runtime", "compiled artifacts", "old design packs"],
        "categories": len(data["categories"]), "menus": len(data["menus"]),
        "inspector_tabs": [t["name"] for t in data["inspector_tabs"]],
    })
    # Refuse a mixed snapshot when Main changes source/assets during export.
    now_sources, now_assets = source_inventory(root), asset_inventory(root)
    if sources != now_sources or assets != now_assets:
        raise ValueError("working source/assets changed during export; wait for Main and retry")
    for rel, record in inputs.items():
        if digest(read_input(root, rel)) != record["sha256"]:
            raise ValueError(f"input changed during export: {rel}")
    files = {p.relative_to(stage).as_posix(): {"sha256": digest(p.read_bytes()), "bytes": p.stat().st_size}
             for p in sorted(stage.rglob("*")) if p.is_file()}
    write_json(stage / "MANIFEST.json", {"schema": 1, "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        "provenance": "Exact working-tree bytes, NOT a commit snapshot. Inputs rechecked after export.",
                                        "files": files, "inputs": inputs})
    validate_stage(stage, inputs)
    return inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="validate temporary export only; do not write dist")
    parser.add_argument("--no-shots", action="store_true", help="compatibility flag; GUI capture is always disabled")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--under-cap", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.under_cap:
        command = ["systemd-run", "--user", "--quiet", "--wait", "--pipe",
                   "-p", "MemoryMax=3G", "-p", "MemorySwapMax=0", "-p", "TasksMax=128",
                   "-p", "RuntimeMaxSec=330", "-p", "TimeoutStopSec=3",
                   "-p", "KillMode=control-group", "-p", "WorkingDirectory=" + str(ROOT),
                   sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:], "--under-cap"]
        raise SystemExit(subprocess.run(command, timeout=360).returncode)
    with tempfile.TemporaryDirectory(prefix="narwhal-ui-bundle-") as tmp:
        stage = Path(tmp) / NAME
        stage.mkdir()
        inputs = prepare(stage)
        archive = Path(tmp) / (NAME + ".zip")
        make_zip(archive, stage)
        print(f"Validated {len(inputs)} real source/asset inputs; ZIP {archive.stat().st_size / 1e6:.2f} MB")
        if args.prepare:
            print("READY: temporary export and ZIP validated, then discarded. Final dist build deferred to Main follow-up.")
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            # Preserve the last deliverable until a complete replacement is verified.
            fd, pending = tempfile.mkstemp(prefix=".ui-bundle-", suffix=".zip", dir=args.out)
            os.close(fd)
            try:
                shutil.copyfile(archive, pending)
                validate_zip(Path(pending), stage)
                os.replace(pending, args.out / (NAME + ".zip"))
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)
            print(f"Wrote {args.out / (NAME + '.zip')}")


if __name__ == "__main__":
    main()

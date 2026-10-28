#!/usr/bin/env python3
"""Capture the real, packaged 0.4.7 workspace in both themes (Linux desktop).

Requires system Python with Pillow, bwrap, systemd-run, and a working desktop GPU.
Example (OUT may be a disposable directory):
    /usr/bin/python3 scripts/capture_readme_themes.py --app-root APPROOT --out OUT
    /usr/bin/python3 scripts/capture_readme_themes.py --self-test

APPROOT is the extracted package's usr/lib/narwhal3d directory. The package and
checkout are mounted read-only; only OUT and disposable session storage are writable.
One isolated native app, a shared GUI lock, 4 GiB RAM, zero swap and a 175-second
process deadline. Existing apps are untouched; only this process's window is captured.
Launching its window may briefly take desktop focus; no input events are synthesized.

Python is UI/API orchestration only: all geometry, tessellation and camera fitting
use the shipped producers/kernel/UI. Screenshots are unmodified app-window pixels,
not renders, desktop composites, or HTML. No private documents are opened.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import time

REVISION = "94534c954dc77f1f1d22f2be6064bedcc8cd1f5b"
VERSION = "0.4.7"
LABELS = {"Perspective", "Top", "Front", "Right"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def verify_pair(light, dark):
    require(light["theme"] == "light" and dark["theme"] == "dark", "theme did not switch")
    for snapshot in (light, dark):
        require(set(snapshot["views"]) == LABELS, "expected four named viewports")
        require(snapshot["selected"] == [], "unexpected selection highlight")
        require(snapshot["bands_visible"], "workspace rails missing")
        require(all(v["mode"] == "Shaded" for v in snapshot["views"].values()),
                "expected Shaded in every pane")
    for key in ("document", "views", "window"):
        require(light[key] == dark[key], f"theme changed {key}")


def verify_pixels(light, dark):
    from PIL import ImageChops, ImageStat
    require(light.size == dark.size, "PNG dimensions differ")
    difference = ImageChops.difference(light.convert("RGB"), dark.convert("RGB"))
    means = ImageStat.Stat(difference).mean
    require(max(means) > 2, "theme screenshots have no meaningful pixel difference")
    return {"difference_bbox": difference.getbbox(), "mean_absolute_rgb_difference": means}


def self_test():
    from PIL import Image
    good = {"theme": "light", "views": {v: {"mode": "Shaded", "camera": [1, 2, 3]}
                                           for v in LABELS},
            "document": [{"recipe_bytes": "exact-recipe"}], "window": [1920, 1080],
            "selected": [], "bands_visible": True}
    dark = copy.deepcopy(good)
    dark["theme"] = "dark"
    # Theme colours may change; geometry, camera, pane layout and mode may not.
    good["viewport_backgrounds"] = {v: [0.4, 0.4, 0.4] for v in LABELS}
    dark["viewport_backgrounds"] = {v: [0.025, 0.025, 0.025] for v in LABELS}
    verify_pair(good, dark)
    moved_views = copy.deepcopy(dark["views"])
    moved_views["Perspective"]["camera"] = [4, 5, 6]
    mutations = [("theme", "light"), ("views", {}), ("views", moved_views),
                 ("document", [{"recipe_bytes": "changed-recipe"}]),
                 ("window", [800, 600]), ("selected", ["Block"]), ("bands_visible", False)]
    for key, value in mutations:
        bad = copy.deepcopy(dark)
        bad[key] = value
        try:
            verify_pair(good, bad)
        except RuntimeError:
            print(f"EXPECTED RED: {key}")
        else:
            raise RuntimeError(f"mutation escaped: {key}")
    white = Image.new("RGB", (20, 20), "white")
    black = Image.new("RGB", (20, 20), "black")
    verify_pixels(white, black)
    try:
        verify_pixels(white, white)
    except RuntimeError:
        print("EXPECTED RED: identical PNG pixels")
    else:
        raise RuntimeError("identical screenshot mutation escaped")
    verify_pair(good, dark)
    print("PASS: restored pair, pixels, and eight negative controls")


def available_memory():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("cannot measure available RAM")


def run_owned(command, *, cwd, env):
    # New session: timeout cleanup can address only our own process group, never
    # a user's existing app. bwrap also kills its child if its parent disappears.
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=180)
            return process.returncode, output
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def worker(args):
    import bpy
    root = args.app_root.resolve()
    sys.dont_write_bytecode = True
    checkout = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if p and not Path(p).resolve().is_relative_to(checkout)]
    sys.path.insert(0, str(root))
    require(Path(bpy.app.binary_path).resolve() == root / "tools/blender-patched/blender",
            "not the requested packaged binary")
    require(not bpy.app.background, "screenshots require a real desktop window")
    cgroup = Path("/sys/fs/cgroup") / Path(Path("/proc/self/cgroup").read_text().strip().split(":")[-1]).relative_to("/")
    require((cgroup / "memory.max").read_text().strip() == str(4 * 1024**3), "RAM cap missing")
    require((cgroup / "memory.swap.max").read_text().strip() == "0", "swap cap missing")
    runpy.run_path(str(root / "launch/bootstrap.py"), run_name="__main__")
    from narwhal3d import bridge, commands, design, kernel, ui
    from narwhal3d.kernel import occt_backend as occt
    from narwhal3d.state import app_state

    result = {"snapshots": [], "scene": {}}
    stage = 0
    deadline = time.monotonic() + 150

    def prepare():
        require(len(list(app_state.model.file.Objects)) == 0, "session is not empty")
        # OURS: synthetic bearing mount, dimensions in mm; no imported user data.
        block = bridge.make_box((-40, -24, 0), (40, 24, 0), 14)
        block = bridge.bevel_edges_occt(block, 2)
        require(block is not None, f"exact fillet declined: {bridge.last_occt_decline()}")
        boss = bridge.make_cylinder((0, 0, 12), 16, 16)
        body = bridge.boolean_brep(block, boss, "UNION")
        for x, y, radius in [(-28, -14, 3.5), (-28, 14, 3.5),
                             (28, -14, 3.5), (28, 14, 3.5), (0, 0, 10)]:
            tool = bridge.make_cylinder((x, y, -2), radius, 34)
            body = bridge.boolean_brep(body, tool, "DIFFERENCE")
            require(kernel.master_of(body) is not None, "boolean lost its exact master")
        master = kernel.master_of(body)
        require(kernel.honesty_problem(body) is None, "dishonest geometry representation")
        require(occt.is_airtight(master), "mount is not one valid closed solid")
        faces = occt.face_types(master)
        require(set(faces) <= {"PLANE", "CYLINDER", "SPHERE"} and faces.get("CYLINDER", 0) >= 6,
                f"expected analytic bore and fillets: {faces}")
        require(kernel.recipe_of(body), "solid has no reconstruction recipe")
        app_state.add_layer("Mounting block", (126, 159, 184))
        app_state.add_layer("Bore profile", (203, 117, 46))
        app_state.ctx.add_geometry(body, "Bearing mount", layer_index=1)
        profile = bridge.make_circle((0, 0, 34), 10)
        require(profile.IsClosed and profile.Degree == 2, "profile is not an exact circle")
        app_state.ctx.add_geometry(profile, "Bore profile", layer_index=2)
        app_state.cancel()
        for obj in bpy.context.view_layer.objects:
            obj.select_set(False)
        bpy.context.view_layer.objects.active = None
        ui.set_toolbar_tab("Standard")
        ui.set_right_tab(0)
        ui.set_active_pane(ui._pane_by_label("Perspective"))
        bpy.context.preferences.view.smooth_view = 0
        for area in ui._view3d_areas():
            ok, message = ui.set_display_mode(area, "Shaded")
            require(ok, message)
        zoom = commands.ZoomCmd()
        zoom._all = True
        zoom._zoom(selected=False)
        app_state.history[:] = ["Bearing mount | 80 x 48 mm | R2 edge fillets | five through bores",
                                "Exact CAD solid and circular construction profile. Units: millimeters."]
        app_state.input_buffer = ""
        ui.sidebar_hover_redraw_update(0, 0)
        ui.category_hover_update(0, 0)

        ui._tag_redraw_viewports()
        ui.redraw_cmdbar()
        result["scene"] = {"description": "Synthetic bearing mounting block and circular bore profile",
                           "units": "millimeters", "base_dimensions": [80, 48, 14],
                           "edge_fillet_radius": 2, "boss_radius": 16, "boss_top_z": 28,
                           "mounting_hole_radius": 3.5, "bore_radius": 10,
                           "profile_center": [0, 0, 34], "profile_radius": 10,
                           "face_types": faces, "volume_mm3": occt.volume(master),
                           "airtight": True, "exact_master": True,
                           "recipe": kernel.recipe_of(body), "private_content": False}

    def snapshot():
        doc = []
        for obj in app_state.model.file.Objects:
            geom = obj.Geometry
            doc.append({"id": str(obj.Attributes.Id), "name": obj.Attributes.Name,
                        "layer": obj.Attributes.LayerIndex,
                        "recipe_bytes": geom.GetUserString(kernel.RECIPE_KEY),
                        "geometry_sha256": hashlib.sha256(canonical(geom.Encode())).hexdigest()})
        views = {}
        backgrounds = {}
        for area in ui._view3d_areas():
            space = area.spaces.active
            rv = space.region_3d
            views[ui.pane_label(area)] = {
                "mode": ui.display_mode_of(area), "location": list(rv.view_location),
                "rotation": list(rv.view_rotation), "distance": rv.view_distance,
                "projection": rv.view_perspective, "lens": space.lens,
                "area": [area.x, area.y, area.width, area.height]}
            backgrounds[ui.pane_label(area)] = list(space.shading.background_color)
        win = bpy.context.window
        return {"theme": design.THEME, "document": doc, "views": views,
                "viewport_backgrounds": backgrounds,
                "window": [win.width, win.height],
                "selected": [o.name for o in bpy.context.selected_objects],
                "bands_visible": ui.left_band_area() is not None and ui.right_band_area() is not None}

    def tick():
        nonlocal stage
        try:
            require(time.monotonic() < deadline, "capture exceeded its UI deadline")
            if os.environ.get("NARWHAL3D_UI_READY") != "1" or getattr(ui, "_viewports_in_progress", False):
                return .2
            require(len(ui._view3d_areas()) == 4, "initial layout is not four panes")
            if stage == 0:
                prepare()
                stage = 1
                return 2
            state = snapshot()
            expected = "light" if stage == 1 else "dark"
            require(state["theme"] == expected, "wrong active theme")
            require(state["window"][0] >= 1600 and state["window"][1] >= 900, "window too small")
            for name, module in tuple(sys.modules.items()):
                if name == "narwhal3d" or name.startswith("narwhal3d."):
                    path = getattr(module, "__file__", None)
                    require(not path or Path(path).resolve().is_relative_to(root / "narwhal3d"),
                            f"import escaped package: {name}")
            result["snapshots"].append(state)
            bpy.ops.screen.screenshot(filepath=str(args.out / f"workspace-{expected}.png"))
            if stage == 1:
                # Remove startup override before exercising the real runtime preference API.
                os.environ.pop("NARWHAL3D_THEME", None)
                ui.set_shell_theme("dark")
                stage = 2
                return 2
            verify_pair(*result["snapshots"])
            args.receipt.write_text(json.dumps(result, indent=2) + "\n")
            print("README_THEME_CAPTURE_PASS", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        except Exception:
            import traceback
            traceback.print_exc()
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(1)

    bpy.app.timers.register(tick, first_interval=.5)


def capture(args):
    from PIL import Image
    root, out = args.app_root.resolve(), args.out.resolve()
    identity = json.loads((root / "packaging/release-manifest.json").read_text())["identity"]
    require(identity["version"] == VERSION and identity["source_revision"] == REVISION,
            "expected published 0.4.7 build 94534c95")
    binary = root / "tools/blender-patched/blender"
    binary_hash = sha(binary)
    require(binary_hash == identity["runtime_binary_sha256"], "runtime binary hash mismatch")
    require(not out.is_relative_to(root), "output must be outside the read-only package")
    require(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "no desktop display")
    with open("/tmp/narwhal3d_gui.lock", "a") as lock:
        print("Waiting for shared GUI lock", flush=True)
        lock_deadline = time.monotonic() + 120
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic() < lock_deadline, "GUI lock busy for 120 seconds; no app launched")
                time.sleep(.5)
        available = available_memory()
        require(available >= 6 * 1024**3, "insufficient RAM headroom for capped capture")
        print(f"Available RAM: {available / 1024**3:.1f} GiB; capture cap: 4 GiB, zero swap", flush=True)
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="narwhal-readme-") as temporary:
            temp = Path(temporary)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("NARWHAL", "RHI" + "NO", "BLENDER_"))
                   and k not in ("PYTHONPATH", "LD_LIBRARY_PATH")}
            for variable in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMPDIR"):
                directory = temp / variable.lower()
                directory.mkdir()
                env[variable] = str(directory)
            env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(root),
                       NARWHAL3D_SETTINGS_FILE=str(temp / "settings.json"),
                       NARWHAL3D_THEME="light", NARWHAL3D_VERSION=VERSION,
                       NARWHAL3D_APPIMAGE="1", NARWHAL3D_NO_DIALOGS="1",
                       NARWHAL3D_DEFER_FIRST_PRESENT="1", NARWHAL3D_STATUSBAR_HEIGHT="37",
                       OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4",
                       LD_LIBRARY_PATH=str(root / "tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"))
            for name in ("pure", "geometry", "selection", "platform"):
                env[f"NARWHAL_{name.upper()}_CORE"] = str(root / f"narwhal3d/native/libnarwhal_{name}_core.so")
            for variable, name in (("NARWHAL_DOCUMENT_CORE", "narwhal-document-core"),
                                   ("NARWHAL_CDR_CORE", "libnarwhal_cdr_core.so")):
                env[variable] = str(root / "runtime/linux-x86_64/bin" / name)
            script = Path(__file__).resolve()
            receipt = temp / "capture.json"
            command = ["systemd-run", "--user", "--scope", "--quiet",
                       "-p", "MemoryMax=4G", "-p", "MemorySwapMax=0", "-p", "CPUQuota=400%",
                       "timeout", "--kill-after=3", "175", "bwrap", "--die-with-parent", "--ro-bind", "/", "/",
                       "--dev-bind", "/dev", "/dev", "--proc", "/proc",
                       "--bind", str(temp), str(temp), "--bind", str(out), str(out), "--",
                       str(binary), "--threads", "4", "--factory-startup",
                       "--window-geometry", "0", "0", "1920", "1080", "--python-exit-code", "1",
                       "--python", str(script), "--", "--worker", "--app-root", str(root),
                       "--out", str(out), "--receipt", str(receipt)]
            started = time.monotonic()
            returncode, output = run_owned(command, cwd=temp, env=env)
            require(returncode == 0 and receipt.exists(), output[-12000:])
            require("README_THEME_CAPTURE_PASS" in output, "missing completion marker")
            result = json.loads(receipt.read_text())
            verify_pair(*result["snapshots"])
            with Image.open(out / "workspace-light.png") as light, Image.open(out / "workspace-dark.png") as dark:
                pixels = verify_pixels(light, dark)
            result.update(schema=1, product="Narwhal 3D", version=VERSION, source_revision=REVISION,
                          runtime_binary_sha256=binary_hash, package_format="AppImage extraction",
                          capture_command="/usr/bin/python3 scripts/capture_readme_themes.py --app-root APPROOT --out OUT",
                          app_root_description="Published Linux 0.4.7 extraction: usr/lib/narwhal3d",
                          helper_sha256=sha(script), elapsed_seconds=round(time.monotonic() - started, 2),
                          validation={"same_document_recipe_bytes": True, "same_four_viewports": True,
                                      "same_cameras": True, "packaged_imports_only": True,
                                      "readonly_package": True, "memory_max_bytes": 4 * 1024**3,
                                      "memory_swap_max_bytes": 0, "pixel_difference": pixels},
                          image_processing="None: full-resolution application-window screenshots")
            result["images"] = {}
            for theme in ("light", "dark"):
                path = out / f"workspace-{theme}.png"
                with Image.open(path) as image:
                    result["images"][path.name] = {"resolution": list(image.size),
                                                   "bytes": path.stat().st_size, "sha256": sha(path)}
            (out / "workspace-capture.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result["images"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--receipt", type=Path, help=argparse.SUPPRESS)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return
    if args.app_root is None or args.out is None:
        parser.error("--app-root and --out are required")
    if args.worker:
        worker(args)
    else:
        capture(args)


if __name__ == "__main__":
    main()

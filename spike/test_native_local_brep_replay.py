"""Current-native local moves must survive recipe replay and a fresh process.

Requires NARWHAL_GEOMETRY_CORE to name the library under test; never discovers a
packaged substitute or skips an unavailable core. Python here only orchestrates
native/OCP calls and reuses the independent OCP acceptance oracle. This is not a
cross-version migration test: unversioned recipes may differ on older backends.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import test_local_brep_move as oracle
from narwhal3d import native_geometry as native

ob = oracle.ob
REPRESENTATIVES = (("face", 2), ("edge", 20), ("edge", 5),
                   ("vertex", 1), ("vertex", 11))
VECTOR = (0.5, 0.0, 0.0)
FRESH_OK = "FRESH-NATIVE-LOCAL-BREP-REPLAY-OK"


def require_native():
    configured = os.environ.get("NARWHAL_GEOMETRY_CORE")
    assert configured, "set NARWHAL_GEOMETRY_CORE to the current library under test"
    assert Path(configured).expanduser().is_file(), f"native library missing: {configured}"
    assert native.available(), native.backend_status()
    assert ob.available(), ob.unavailable_reason()
    print(native.backend_status(), flush=True)


def validate(shape, before, kind, index, label, expected=None):
    assert oracle.validate_result(before, shape, kind, index, VECTOR), (
        f"{label}: independent exact/containment/B-rep oracle failed")
    cutter = oracle.translated_sphere((10.0, 5.0, 16.0), 0.2)
    cut = ob.boolean(shape, cutter, "DIFFERENCE")
    assert ob.is_airtight(cut), f"{label}: downstream cut is not airtight"
    assert oracle.BRepAlgoAPI_Check(cut).IsValid(), f"{label}: downstream cut is invalid"
    assert "SPHERE" in ob.face_types(cut), f"{label}: downstream spherical face missing"
    measurements = {"volume": ob.volume(shape), "cut_volume": ob.volume(cut)}
    assert 0.0 < measurements["cut_volume"] < measurements["volume"], (
        f"{label}: downstream subtraction did not remove volume")
    if expected is not None:
        for name, value in measurements.items():
            assert abs(value - expected[name]) <= 1e-8, (
                f"{label}: {name} differs from native direct result: "
                f"{value} vs {expected[name]}")
    print(f"  ok   {label}: independent geometry and downstream boolean", flush=True)
    return measurements


def fresh_mode(payload):
    assert len(payload) == len(REPRESENTATIVES), "fresh replay lost representatives"
    for item, (kind, index) in zip(payload, REPRESENTATIVES):
        recipe = item["recipe"]
        assert (recipe["component"], recipe["index"]) == (kind, index)
        assert tuple(recipe["vector"]) == VECTOR
        before = ob.from_recipe(recipe["a"])
        rebuilt = ob.from_recipe(recipe)
        validate(rebuilt, before, kind, index, f"fresh {kind}{index}", item["expected"])
    print(FRESH_OK, flush=True)


def run():
    parent = oracle.parent_recipe()
    before = ob.from_recipe(parent)
    payload = []
    for kind, index in REPRESENTATIVES:
        recipe = oracle.move_recipe(parent, before, kind, index, VECTOR)
        result = native.move_component(
            before, lambda: ob.shape_to_bytes(before, triangles=False),
            {"face": 1, "edge": 2, "vertex": 3}[kind], index, VECTOR, live=False)
        assert result.ok, f"native {kind}{index}: {result.kind}: {result.detail}"
        direct = ob.shape_from_bytes(result.data)
        expected = validate(direct, before, kind, index, f"native direct {kind}{index}")
        rebuilt = ob.from_recipe(recipe)
        validate(rebuilt, before, kind, index, f"recipe {kind}{index}", expected)
        payload.append({"recipe": recipe, "expected": expected})

    process = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--fresh"],
        input=json.dumps(payload), cwd=oracle.ROOT, text=True, capture_output=True,
        timeout=120, env=dict(os.environ), check=False)
    assert process.returncode == 0 and FRESH_OK in process.stdout, (
        f"fresh native replay failed (exit {process.returncode}):\n"
        f"{process.stdout}\n{process.stderr}")
    print(process.stdout.strip())
    print("15/15 native direct/recipe/fresh local B-rep replay cases passed")


def main():
    require_native()
    # Even a transient unavailable result must not silently exercise the fallback.
    with patch.object(ob, "move_subobject_local", side_effect=AssertionError(
            "native replay must not use the Python/OCP fallback")):
        if sys.argv[1:] == ["--fresh"]:
            fresh_mode(json.load(sys.stdin))
        else:
            assert not sys.argv[1:], "usage: test_native_local_brep_replay.py [--fresh]"
            run()


if __name__ == "__main__":
    main()

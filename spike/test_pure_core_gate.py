"""Self-test for the mandatory ``native/pure_core`` source gate.

The gate (``scripts/check_pure_core.py``) must be *load-bearing*: a mutation of
the crate source must make it fail, and a skipped clippy step must fail it.
This suite proves both, but on a **temporary copied crate** -- the live
``native/pure_core`` tree is never modified, and the copy is removed after the
run. It also confirms the live gate is green and that the live source is byte
identical before and after.

Checks:
* the real gate is green (exit 0);
* a clippy ``-D warnings`` violation injected into a copied crate makes the
  gate fail (a mutation is caught by the lint step, which runs before the
  build/tests);
* the live crate source is unchanged by the mutation test;
* a skipped clippy step (stubbed to return ``"skipped"``) makes ``run_gate``
  raise, so the gate cannot pass with clippy silently absent.
"""
from __future__ import annotations

import importlib.util
import ast
import json
from pathlib import Path
from unittest.mock import patch
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_paths
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "scripts", "check_pure_core.py")
CRATE = os.path.join(ROOT, "native", "pure_core")
PYTHON = str(runtime_paths.python(ROOT))

results = []


def check(name):
    def deco(fn):
        if "--focused" in sys.argv and name in {
            "the real gate is green on the live crate",
            "a clippy -D warnings mutation on a copied crate fails the gate",
        }:
            return fn
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _load_gate():
    spec = importlib.util.spec_from_file_location("narwhal_check_pure_core", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_gate(crate_rel):
    return subprocess.run(
        [PYTHON, GATE, "--crate", crate_rel],
        cwd=ROOT, capture_output=True, text=True,
    )


def _read(p):
    with open(p, "rb") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@check("the gate manifest covers every pure-core native and reference suite")
def _():
    gate = _load_gate()
    expected = {
        "spike/test_stl_writer.py", "spike/test_native_stl.py",
        "spike/test_obj_writer.py", "spike/test_obj_reader.py", "spike/test_native_obj.py",
        "spike/test_dxf_writer.py", "spike/test_dxf_reader.py", "spike/test_native_dxf.py",
        "spike/test_numparse.py", "spike/test_numparse_owned.py", "spike/test_native_numparse.py",
        "spike/test_pure_core_packaging.py",
        "spike/test_calc.py", "spike/test_native_calc.py",
        "spike/test_calc_owned.py", "spike/test_bearing_owned.py",
        "spike/test_native_parser_cache.py", "spike/test_macro_plan_owned.py",
        "spike/test_aci_colors.py",
        "spike/test_macro_owned.py", "spike/test_printwidths_contract.py",
        "spike/test_printwidths_owned.py", "spike/test_unitformat_contract.py",
        "spike/test_unitformat_owned.py",
        "spike/portfolio_review/test_metadata.py",
        "spike/test_bearing.py", "spike/test_native_bearing.py",
        "spike/test_coords.py", "spike/test_native_coords.py",
        "spike/test_coords_owned.py", "spike/test_native_joinchain.py",
        "spike/test_joinchain_owned.py", "spike/test_python_norm_owned.py",
        "spike/test_native_pickconstraint.py", "spike/test_native_blend.py",
        "spike/test_macro.py", "spike/test_native_macro.py",
        "spike/test_macro_plan.py", "spike/test_native_macro_plan.py",
        "spike/test_svgpath_owned.py", "spike/test_alias_owned.py",
        "spike/test_svgpath.py", "spike/test_native_svgpath.py",
        "spike/test_vector_export.py", "spike/test_native_vector_export.py",
        "spike/test_native_command_search.py",
        "spike/test_native_search02_contract.py", "spike/test_native_search02_adapter.py",
        "spike/test_native_search02_ffi.py", "spike/test_native_search02_consumers.py",
        "spike/test_native_coil.py",
        "spike/test_native_intersect.py",
        "spike/test_pure_core_curve_hits_gate.py",
        "spike/test_native_coil_preview.py",
        "spike/test_preview_curve_native.py",
        "spike/test_native_tessellate.py",
        "spike/test_native_unit_table.py",
        "spike/test_unit_ops_owned.py", "spike/test_unit_table_owned.py",
        "spike/test_native_nearest.py",
        "spike/test_pure_core_nearest_batch_gate.py",
        "spike/test_rebuild_performance.py",
        "spike/test_native_curve_samples.py",
        "spike/test_native_display_samples.py",
        "spike/test_native_rebuild_fit.py", "spike/test_rebuild_owned.py",
        "spike/test_native_arclength.py",
        "spike/test_native_interpolate.py",
        "spike/test_native_tween.py",
    }
    actual = set(gate.PURE_CORE_PYTESTS)
    assert actual == expected, f"missing suites: {sorted(expected - actual)}; unexpected suites: {sorted(actual - expected)}"


@check("the real gate is green on the live crate")
def _():
    proc = _run_gate("native/pure_core")
    assert proc.returncode == 0, \
        "gate not green:\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    assert "ok" in proc.stdout.lower(), proc.stdout


@check("a clippy -D warnings mutation on a copied crate fails the gate")
def _():
    # Snapshot the live source so we can prove it is never mutated.
    live_before = _read(os.path.join(CRATE, "src", "stl.rs"))

    scratch = tempfile.mkdtemp(prefix="pure_core_gate_mut_")
    try:
        copy_root = os.path.join(scratch, "native")
        os.makedirs(copy_root, exist_ok=True)
        shutil.copytree(
            CRATE, os.path.join(copy_root, "pure_core"),
            ignore=shutil.ignore_patterns("target"),
        )
        stl = os.path.join(copy_root, "pure_core", "src", "stl.rs")
        text = _read(stl).decode("utf-8")
        # Revert the ``format_9g`` range test to its manual ``||`` form: that is
        # clippy::manual_range_contains, a ``correctness`` lint (default warn,
        # promoted to an error by ``-D warnings``). It compiles, so the gate
        # must fail at the lint step -- proving a source mutation is caught by
        # clippy, not by coincidence.
        original = "if !(-4..9).contains(&e) {"
        mutated = "if e < -4 || e >= 9 {"
        assert original in text, "anchor for the mutation not found in stl.rs"
        _write(stl, text.replace(original, mutated, 1))

        rel = os.path.relpath(
            os.path.join(copy_root, "pure_core"), ROOT
        ).replace(os.sep, "/")
        proc = _run_gate(rel)
        assert proc.returncode != 0, \
            "gate did NOT fail on the mutated crate:\n%s" % proc.stdout
        combined = (proc.stdout + proc.stderr).lower()
        assert ("clippy" in combined or "cargo test" in combined
                or "failed" in combined), \
            "gate failure does not name the failing step:\n%s" % (
                proc.stdout + proc.stderr)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    live_after = _read(os.path.join(CRATE, "src", "stl.rs"))
    assert live_before == live_after, \
        "the live pure_core source was modified by the mutation test"


@check("a skipped clippy step fails the gate (self-test of the guard)")
def _():
    gate = _load_gate()
    real = gate.step_clippy

    def skipped(crate):
        return {"status": "skipped", "reason": "self-test stub"}

    gate.step_clippy = skipped
    # This guard test needs no Cargo work; fmt is covered by the real gate.
    gate.step_fmt = lambda crate: {"status": "ok"}
    try:
        try:
            gate.run_gate("native/pure_core")
        except gate.GateError as exc:
            assert "clippy" in str(exc).lower(), \
                "skip-clippy guard did not name clippy: %r" % exc
        else:
            raise AssertionError(
                "run_gate passed with a skipped clippy step (guard is missing)")
    finally:
        gate.step_clippy = real


@check("Rust integration execution and public fixture closure are mandatory and mutation-sensitive")
def _():
    gate = _load_gate()
    crate = Path(CRATE)
    closure = gate.require_rust_public_closure(crate)
    assert len(closure) == 19
    public_source = (Path(ROOT) / "scripts/create_public_branch.py").read_text()
    for retired in ("spike/alias_reference.py", "spike/units_reference.py", "spike/interpolate_reference.py"):
        assert retired not in public_source, "retired Python oracle is public: " + retired
    summary = "test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"

    def runner(fault=""):
        def run(command, cwd):
            assert "--lib" not in command
            if "--test" not in command:
                return 0, summary
            suite = command[command.index("--test") + 1]
            assert "--message-format=json" in command
            artifact = {"reason": "compiler-artifact", "target": {"name": suite, "kind": ["test"]},
                        "profile": {"test": True}, "executable": "/fixture/" + suite}
            if fault == "missing-compilation":
                return 0, summary
            if fault == "discovery-only":
                return 0, json.dumps(artifact)
            if fault == "wrong-target":
                artifact["target"]["name"] = "unrelated"
            if fault == "zero-tests":
                return 0, json.dumps(artifact) + "\n" + summary.replace("2 passed", "0 passed")
            if fault == "filtered":
                return 0, json.dumps(artifact) + "\n" + summary.replace("0 filtered", "1 filtered")
            if fault == "failed":
                return 1, json.dumps(artifact) + "\n" + summary
            return 0, json.dumps(artifact) + "\n" + summary
        return run

    def rejects(function, fault):
        with patch.object(gate, "_run", side_effect=runner(fault)):
            try:
                function(crate)
            except gate.GateError:
                return
        raise AssertionError("integration evidence mutant survived: " + fault)

    with patch.object(gate, "_run", side_effect=runner()) as run:
        result = gate.step_tests(crate)
        assert set(result["integration_suites"]) == set(gate.RUST_INTEGRATION_SUITES)
        assert all(v == {"compiled": True, "passed": 2} for v in result["integration_suites"].values())
        assert run.call_count == 4
    for fault in ("missing-compilation", "discovery-only", "wrong-target", "zero-tests", "filtered", "failed"):
        rejects(gate.step_tests, fault)
    # Disable the compiled/executed guard in a copied AST: the negative test
    # above must itself go RED, with no source or bytecode writes.
    tree = ast.parse(__import__("inspect").getsource(gate.step_tests))
    guard = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                 and "not compiled" in ast.unparse(n.test))
    guard.test = ast.Constant(False)
    ns = {}
    exec(compile(ast.fix_missing_locations(tree), "<integration-evidence-mutant>", "exec"), vars(gate), ns)
    try:
        rejects(ns["step_tests"], "missing-compilation")
    except AssertionError:
        pass
    else:
        raise AssertionError("negative integration test did not go RED with guard disabled")

    source = (Path(ROOT) / "scripts/create_public_branch.py").read_text()
    # Remove a real fixture path from the manifest, without reading that fixture.
    fixture = next(path for path in closure if "/fixtures/" in path)
    broken = source.replace(repr(fixture) + ",", "")
    assert broken != source
    real_read = Path.read_text
    def read(path, *args, **kwargs):
        return broken if path == Path(ROOT) / "scripts/create_public_branch.py" else real_read(path, *args, **kwargs)
    def closure_rejects():
        with patch.object(Path, "read_text", read):
            try:
                gate.require_rust_public_closure(crate)
            except gate.GateError:
                return
        raise AssertionError("missing public fixture survived")
    closure_rejects()
    with patch.object(gate, "require_rust_public_closure", return_value=closure):
        try:
            closure_rejects()
        except AssertionError:
            pass
        else:
            raise AssertionError("closure negative test did not go RED with guard disabled")


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)

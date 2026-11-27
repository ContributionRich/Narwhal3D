"""Compatibility transport for the Rust pure-core qualification driver.

Rust owns C-ABI semantics, subprocess supervision, closure, builds and reports.
These names remain for existing Python packaging/actual-SDK callers and their
host-injection tests; there is no Python probe program or policy engine here.
"""
from pathlib import Path
import subprocess
import sys
import source_tools as _native

ROOT = _native.ROOT
_METADATA = _native._rpc('pure_gate_metadata')
globals().update(_METADATA)


class GateError(RuntimeError):
    pass


def _call(action, *, crate=None, **arguments):
    request = dict(root=str(ROOT), action=action,
                   pure_config={name: globals()[name] for name in _METADATA}, **arguments)
    if crate is not None:
        request['crate'] = str(ROOT / Path(crate))
    try:
        return _native._rpc('pure_gate', _process_timeout=14400, **request)
    except _native.AuditError as error:
        raise GateError(str(error)) from error


def _probe(name, path, python=sys.executable, *, env=None):
    # No interpreter loads the library now; python remains a caller-compatible argument.
    arguments = dict(library=str(Path(path).resolve()), name=name)
    if env is not None:
        arguments['environment'] = env
    identity_name = name.removesuffix('_PROBE') + '_IDENTITY'
    if identity_name in globals():
        arguments['expected_identity'] = globals()[identity_name]
    try:
        return _native._rpc('pure_probe', **arguments)
    except _native.AuditError as error:
        raise ValueError(str(error)) from error


def probe_witness_records(name):
    """Return inert ABI input bytes as hex for independent real-SDK comparisons."""
    return _native._rpc('pure_probe_records', name=name)


def require_rust_public_closure(crate):
    return _call('closure', crate=crate)


def _bundled_python():
    return _call('python', fallback=sys.executable)


def run_gate(crate_rel):
    return _call('run', crate=crate_rel, python=sys.executable)


def step_direct_abi(lib_path):
    # Preserve the packaging-call injection seams; all three probes execute in Rust.
    try:
        probe_pure_core_parser(lib_path)
        probe_pure_core_retirement(lib_path)
        report = _probe('PURE_CORE_DIRECT_ABI', lib_path)
        report['command'] = 'Rust typed C ABI probe'
        return report
    except ValueError as error:
        raise GateError(str(error)) from error


def main(argv):
    return subprocess.run([str(_native.LAUNCHER), 'pure-gate', *argv],
                          cwd=ROOT, timeout=14400).returncode


def probe_pure_core_parser(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_PARSE_PROBE", path, python, env=env)


def probe_pure_core_retirement(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_RETIREMENT_PROBE", path, python, env=env)


def probe_pure_core_nearest_batch(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_NEAREST_BATCH_PROBE", path, python, env=env)


def probe_pure_core_polyline_nearest(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_POLYLINE_NEAREST_PROBE", path, python, env=env)


def probe_pure_core_curve_hits(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_CURVE_HITS_PROBE", path, python, env=env)


def probe_pure_core_curve_record_hits(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_CURVE_RECORD_HITS_PROBE", path, python, env=env)


def probe_pure_core_nurbs_record(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_NURBS_RECORD_PROBE", path, python, env=env)


def probe_pure_core_line_samples(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_LINE_SAMPLES_PROBE", path, python, env=env)


def probe_pure_core_polyline_samples(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_POLYLINE_SAMPLES_PROBE", path, python, env=env)


def probe_pure_core_polycurve_samples(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_POLYCURVE_SAMPLES_PROBE", path, python, env=env)


def probe_pure_core_offset_fit(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_OFFSET_FIT_PROBE", path, python, env=env)


def probe_pure_core_surface_offset(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_SURFACE_OFFSET_PROBE", path, python, env=env)


def probe_pure_core_surface_grid(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_SURFACE_GRID_PROBE", path, python, env=env)


def probe_pure_core_surface_display(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_SURFACE_DISPLAY_PROBE", path, python, env=env)


def probe_pure_core_svg_unit(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_SVG_UNIT_PROBE", path, python, env=env)


def probe_pure_core_remove_join_cv(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_REMOVE_JOIN_PROBE", path, python, env=env)


def probe_pure_core_dxf_approx(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_DXF_APPROX_PROBE", path, python, env=env)


def probe_pure_core_planar_area(path, python=sys.executable, *, env=None):
    return _probe("PURE_CORE_PLANAR_AREA_PROBE", path, python, env=env)


def step_fmt(crate):
    return _call("step", crate=crate, step="fmt")


def step_clippy(crate):
    return _call("step", crate=crate, step="clippy")


def step_tests(crate):
    return _call("step", crate=crate, step="tests")


def step_release_build(crate):
    return _call("step", crate=crate, step="release_build")


def step_nearest_batch(library):
    return _call("step", library=str(library), step="nearest_batch")


def step_polyline_nearest(library):
    return _call("step", library=str(library), step="polyline_nearest")


def step_curve_hits(library):
    return _call("step", library=str(library), step="curve_hits")


def step_curve_record_hits(library):
    return _call("step", library=str(library), step="curve_record_hits")


def step_nurbs_record(library):
    return _call("step", library=str(library), step="nurbs_record")


def step_line_samples(library):
    return _call("step", library=str(library), step="line_samples")


def step_polyline_samples(library):
    return _call("step", library=str(library), step="polyline_samples")


def step_polycurve_samples(library):
    return _call("step", library=str(library), step="polycurve_samples")


def step_offset_fit(library):
    return _call("step", library=str(library), step="offset_fit")


def step_surface_offset(library):
    return _call("step", library=str(library), step="surface_offset")


def step_surface_grid(library):
    return _call("step", library=str(library), step="surface_grid")


def step_surface_display(library):
    return _call("step", library=str(library), step="surface_display")


def step_svg_unit(library):
    return _call("step", library=str(library), step="svg_unit")


def step_adapter(crate, library):
    return _call("step", crate=crate, library=str(library), step="adapter", python=sys.executable)


def step_windows_check(crate):
    return _call("step", crate=crate, step="windows_check")


def step_dxf_approx(library):
    return _call("step", library=str(library), step="dxf_approx")


def step_planar_area(library):
    return _call("step", library=str(library), step="planar_area")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Required owned SVG primary route plus direct legacy packed/JSON compatibility.

Default: live public svgpath and its required adapter, independently compared
with fixed original-Python fixture anchors. --candidate/--library qualify inactive work.
No source-tree library discovery, building, Python replay, or size routing.
"""
import argparse
import ctypes
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--candidate', type=Path, default=ROOT / 'narwhal3d/svgpath.py')
parser.add_argument('--library', type=Path)
options = parser.parse_args()
if options.library:
    os.environ['NARWHAL_PURE_CORE'] = str(options.library.resolve())


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


svg = _load('svgpath_primary_test', options.candidate)
reference = _load('svgpath_independent_oracle', ROOT / 'spike/svgpath_reference.py')
compat = _load('svgpath_legacy_compat', ROOT / 'narwhal3d/native_pure.py')
native = (svg._adapter() if hasattr(svg, '_adapter') else
          svg if hasattr(svg, '_required_binding') else svg.native_svgpath)
parse_path = svg.parse_path
results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, 'PASS', ''))
        except Exception as exc:
            results.append((name, 'FAIL', repr(exc)))
    return deco


def _bits(x):
    if isinstance(x, float): return struct.pack('>d', x)
    if isinstance(x, dict): return {k: _bits(v) for k, v in x.items()}
    if isinstance(x, (tuple, list)): return tuple(map(_bits, x))
    return x


def _outcome(fn, text):
    try: return 'ok', _bits(fn(text))
    except Exception as exc: return type(exc).__name__, str(exc)


VALID_PATHS = [
    "M0 0" + "L1 1" * 40,
    "M 0 0 10 0 10 10",
    "M 0 0 H 5 V 10",
    "M 0 0 C 1 2 3 4 5 6 S 7 8 9 10",
    "M 0 0 Q 2 2 4 0 T 8 0",
    "M 10 0 A 10 10 0 1 1 -10 0 A 10 10 0 1 1 10 0",
    "M 1 0 A 1 1 0 0 1 0 1",
    "M 0 0 A 0 0 0 0 0 4 3",
    "M 0 0 L 10 0 L 10 10 Z",
    "M 0 0 L 10 0 Z M 20 0 L 30 0",
    "M -1.5e-3 .5.5.5 L 1e5 2.5",
    "m 0 0 l 10 0 h 10 v 10 c 10 10 20 20 30 30 q 10 10 20 20",
    "",
]


@check('public wrapper shares one adapter and forwards parser/converters without bypass')
def _():
    assert callable(getattr(svg, '_adapter', None)), 'public SVG must be a thin wrapper'
    assert svg._adapter() is native
    again = _load('svgpath_primary_again', options.candidate)
    assert again._adapter() is native
    for name, values in [('parse_path', ('M0 0',)),
                         ('_subpaths_from_packed', ([], [], [], 0, 0)),
                         ('_subpaths_from_json', ('[]',))]:
        sentinel = object()
        with patch.object(native, name, return_value=sentinel) as forwarded:
            assert getattr(svg, name)(*values) is sentinel, name
            forwarded.assert_called_once_with(*values)
    for text in ['', 'M0 0', 'L']:
        with patch.object(native, 'parse_path', side_effect=RuntimeError('wrapper bypass sentinel')) as forwarded:
            assert _outcome(svg.parse_path, text) == ('RuntimeError', 'wrapper bypass sentinel')
            forwarded.assert_called_once_with(text)


@check('primary requires core ABI 1 / SVG owned ABI 14 and retains CDLL')
def _():
    record = native._required_binding()
    assert callable(record.function) and record.library is not None
    assert hasattr(native, '_native_svgpath_calls')


@check('all string sizes including empty are primary native semantic operations')
def _():
    for text in ['', 'M', 'M0 0', 'M0 0'+'L1 1'*1000, 'M0 0'+'L1 1'*10000]:
        before = native._native_svgpath_calls
        assert _bits(parse_path(text)) == _bits(reference.expected(text))
        assert native._native_svgpath_calls == before + 1


@check('original valid command/number corpus: primary equals fixed original-Python answers')
def _():
    for text in VALID_PATHS:
        before = native._native_svgpath_calls
        assert _bits(parse_path(text)) == _bits(reference.expected(text)), text
        assert native._native_svgpath_calls == before + 1


@check('original path errors and typed Unicode errors are owned and exact')
def _():
    for text in ('L 10', 'M 0 0 L 10', 'M 0 0 5 L 1 2', '1', '+', 'M0 0L² 1'):
        before = native._native_svgpath_calls
        assert _outcome(parse_path, text) == _outcome(reference.expected, text), text
        assert _outcome(reference.expected, text)[0] == 'ValueError'
        assert native._native_svgpath_calls == before + 1


@check('non-string admission preserves measured errors and does not count')
def _():
    for value in (5, None, b'', b'M0 0', [], [1]):
        before = native._native_svgpath_calls
        assert _outcome(parse_path, value) == _outcome(reference.expected, value)
        assert native._native_svgpath_calls == before


@check('missing core cannot replay even empty or syntactically invalid strings')
def _():
    with patch.dict(os.environ, NARWHAL_PURE_CORE='/not/a/svg/core.so'):
        for text in ['', 'M0 0L1 2', 'L 10']:
            before = native._native_svgpath_calls
            assert _outcome(parse_path, text)[0] == 'RuntimeError'
            assert native._native_svgpath_calls == before


@check('stale core and missing owned symbol cannot replay')
def _():
    class Function:
        def __init__(self, value): self.value = value
        def __call__(self): return self.value
    class Old:
        pass
    for core, version in [(2, 14), (1, 13), (1, None)]:
        library = Old()
        library.narwhal_pure_core_abi_version = Function(core)
        if version is not None:
            library.narwhal_pure_core_svgpath_owned_version = Function(version)
        with patch.object(native, '_binding', None), patch.object(native.C, 'CDLL', return_value=library):
            before = native._native_svgpath_calls
            assert _outcome(parse_path, 'M0 0')[0] == 'RuntimeError'
            assert native._native_svgpath_calls == before


@check('failed native calls are RuntimeError, never Python replay')
def _():
    for failure in [OSError('native failed'), ctypes.ArgumentError('native failed')]:
        def fail(*args): raise failure
        with patch.object(native, '_load', return_value=(object(), fail)):
            before = native._native_svgpath_calls
            assert _outcome(parse_path, 'M0 0')[0] == 'RuntimeError'
            assert native._native_svgpath_calls == before


@check('invalid reports/geometry cannot count or replay')
def _():
    for mode in ['status', 'dirty', 'counts', 'offset', 'flag', 'finite']:
        def invalid(text, length, pts, n, offs, m, flags, fc, counts, errptr):
            error = ctypes.cast(errptr, ctypes.POINTER(native._Error)).contents
            if mode == 'status': return -99
            counts[0], counts[1] = 8, 1
            if mode == 'dirty': error.value = 1
            if mode == 'counts': error.kind = 5; return 2
            if pts is None: return 1
            if mode == 'offset': offs[0] = 1
            if mode == 'flag': flags[0] = 2
            if mode == 'finite': pts[0] = math.inf
            return 0
        with patch.object(native, '_load', return_value=(object(), invalid)):
            before = native._native_svgpath_calls
            assert _outcome(parse_path, 'M0 0L1 2')[0] == 'RuntimeError', mode
            assert native._native_svgpath_calls == before


@check('primary schema is 4-tuples of 2-tuples and bool closed')
def _():
    for text in VALID_PATHS:
        for sub in parse_path(text):
            assert type(sub['closed']) is bool
            for bez in sub['beziers']:
                assert isinstance(bez, tuple) and len(bez) == 4
                assert all(isinstance(pt, tuple) and len(pt) == 2 for pt in bez)


@check("the exact subpath structure holds on the packed route")
def _():
    got = parse_path("M 0 0 10 0 10 10")
    sp = got[0]
    assert len(sp["beziers"]) == 2, sp
    b0 = sp["beziers"][0]
    assert b0[0] == (0.0, 0.0)
    assert b0[3] == (10.0, 0.0)
    assert abs(b0[1][0] - 10.0 / 3.0) < 1e-12
    assert abs(b0[2][0] - 20.0 / 3.0) < 1e-12
    assert sp["closed"] is False
    got = parse_path("M 0 0 L 10 0 L 10 10 Z")
    sp = got[0]
    assert sp["closed"] is True
    assert len(sp["beziers"]) == 3, sp
    assert sp["beziers"][2][0] == (10.0, 10.0)
    assert sp["beziers"][2][3] == (0.0, 0.0)


@check('COMPAT only: packed binding equals fixed original-Python answers on original corpus')
def _():
    assert compat._svgpath_packed_binding() is not None
    for text in VALID_PATHS:
        packed = compat._native_svgpath_packed(text)
        assert packed is not None, text
        assert _bits(svg._subpaths_from_packed(*packed)) == _bits(reference.expected(text)), text


@check('COMPAT only: packed capacity/partition/finite/flag validators remain strict')
def _():
    valid = compat._valid_svgpath_packed
    pts = [0.0] * 8
    assert not valid(1, 1, pts, [1], [0])
    assert not valid(1, 0, pts, [], [])
    assert valid(0, 0, [], [], [])
    assert not valid(1, 1, pts, [0], [2])
    assert not valid(1, 1, [math.inf] * 8, [0], [0])
    assert not valid(1, 1, pts[:-1], [0], [0])
    assert not valid(1, 2, pts, [0], [0])


@check('COMPAT only: JSON payload, converter and schema/finite validation')
def _():
    payload = compat._native_svgpath('M 0 0 10 0 Z')
    assert payload is not None
    data = json.loads(payload)
    assert isinstance(data, list) and len(data) == 1 and data[0]['closed'] is True
    # Legacy JSON formats integral floats as integers; preserve its historical
    # numeric equality contract, not the primary owned ABI's float-bit contract.
    assert svg._subpaths_from_json(payload) == reference.expected('M 0 0 10 0 Z')
    assert compat._valid_svgpath_json(payload.encode() if isinstance(payload, str) else payload)
    for bad in [b'{}', b'[{"closed":0,"beziers":[]}]',
                b'[{"closed":false,"beziers":[[[0,0]]]}]',
                b'[{"closed":false,"beziers":[[[0,0],[0,0],[0,0],[NaN,0]]]}]']:
        assert not compat._valid_svgpath_json(bad), bad


failed = 0
for name, status, detail in results:
    print(f'[{status}] {name}' + (f' -- {detail}' if detail else ''))
    failed += status == 'FAIL'
print(f'\n{len(results)-failed}/{len(results)} passed')
sys.exit(1 if failed else 0)

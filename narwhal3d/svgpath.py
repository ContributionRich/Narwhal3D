"""Required Rust SVG parser; Python only loads and presents native results.

Historical SVG elliptical arcs remain approximate cubics, not analytic ellipses.
No Python parsing/geometry fallback.
"""
_native = None


def _adapter():
    global _native
    if _native is None:
        try:
            from . import native_svgpath
        except ImportError:
            import importlib.util
            import sys
            from pathlib import Path
            canonical = 'narwhal3d_native_svgpath'
            native_svgpath = sys.modules.get(canonical)
            if native_svgpath is None:
                here = Path(__file__).resolve().parent
                path = here / 'native_svgpath.py'
                spec = importlib.util.spec_from_file_location(canonical, path)
                native_svgpath = importlib.util.module_from_spec(spec)
                sys.modules[canonical] = native_svgpath
                try:
                    spec.loader.exec_module(native_svgpath)
                except BaseException:
                    sys.modules.pop(canonical, None)
                    raise
        _native = native_svgpath
    return _native



def parse_path(d):
    return _adapter().parse_path(d)


def _subpaths_from_packed(pts, offs, flags, n_pts, n_subs):
    return _adapter()._subpaths_from_packed(pts, offs, flags, n_pts, n_subs)


def _subpaths_from_json(payload):
    return _adapter()._subpaths_from_json(payload)

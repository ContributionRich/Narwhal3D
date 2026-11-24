"""Renderer-facing authored DATA supplied by the required Rust UI content owner."""
if __package__:
    from .native_ui_content import resource
else:  # Existing icon-build scripts load this facade directly by file path.
    import importlib.util
    from pathlib import Path
    _spec = importlib.util.spec_from_file_location(
        "narwhal_native_ui_content", Path(__file__).with_name("native_ui_content.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("cannot load required UI content transport")
    _transport = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_transport)
    resource = _transport.resource

if __package__:
    from . import flyout_icon_data as _flyout_icon_data

globals().update(resource("toolbar_data"))

"""Narwhal 3D — a command-driven NURBS CAD application.

The application is distributed with its own private runtime and starts directly
in the Narwhal 3D workspace. Exact document geometry is kept separate from the
tessellated viewport representation.
"""


def __getattr__(name):
    if name in ('_ui','ui'):
        from importlib import import_module
        module = import_module('.ui', __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register():
    from . import ui
    ui.register()


def unregister():
    from . import automation_host

    automation_host.close()
    from . import ui
    ui.unregister()


bl_info = {
    "name": "Narwhal 3D",
    "author": "Narwhal 3D contributors",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "3D Viewport > Tools sidebar",
    "description": "Narwhal 3D command-line NURBS modeling application",
    "category": "Import-Export",
}

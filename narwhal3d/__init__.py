"""Narwhal 3D — a command-driven NURBS CAD application.

The application is distributed with its own private runtime and starts directly
in the Narwhal 3D workspace. Exact document geometry is kept separate from the
tessellated viewport representation.
"""
from . import ui as _ui


def register():
    _ui.register()


def unregister():
    _ui.unregister()


bl_info = {
    "name": "Narwhal 3D",
    "author": "Narwhal 3D contributors",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "3D Viewport > Tools sidebar",
    "description": "Narwhal 3D command-line NURBS modeling application",
    "category": "Import-Export",
}

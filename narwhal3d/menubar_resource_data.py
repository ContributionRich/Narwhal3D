"""Renderer rows from the required Rust content/menu owner; registry stays host-owned."""
from . import toolbar_data
from . import native_ui_content as _content

globals().update(_content.resource("menubar_resource_data"))


def _item(label, command):
    return ("item", label, "", command)


def _todo(label, command):
    return _item(label + TODO_SUFFIX, command)


def _sep():
    return ("sep", "", "", None)


def _popup(label, rows):
    return ("popup", label, "", tuple(rows), None)


def _walk_commands(rows):
    yield from _content.read(4, rows=_content._encode(rows))


def _toolbar_groups(registered):
    return _content.menu(1, registered, toolbar_data.TABS)


def _expanded_menus(registered):
    return _content.menu(2, registered, toolbar_data.TABS)


def _registered_command_keys():
    # Lazy host import preserves import order and the original diagnostic path.
    try:
        from . import commands
    except Exception as error:
        print(f"[narwhal3d] menu generation cannot read the command registry: "
              f"{type(error).__name__}: {error}")
        return frozenset()
    return frozenset(commands.COMMANDS)


MENUS = _expanded_menus(_registered_command_keys())


def _counts():
    return _content.counts(MENUS)


REPORT = {**_counts(), "menus": len(MENUS), "source": "original"}

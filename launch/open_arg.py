"""Resolve an optional document path passed to the application at launch.

The desktop entry runs ``narwhal3d.sh %f``. The launcher forwards its arguments to the
bundled runtime after ``--``, which leaves them untouched in ``sys.argv``. This module is
pure Python so the argument handling can be tested without starting the runtime.
"""
import os


def file_to_open(argv, isfile=os.path.isfile):
    """The absolute path of the file to open, or None.

    Only arguments AFTER the first ``--`` are ours; everything before it belongs to
    Blender (``--factory-startup``, ``--python bootstrap.py``) and is never treated as a
    file. The first of ours that is an EXISTING regular file wins. Anything else -- an
    option such as ``--install`` (never an existing file), a missing path, a directory --
    is ignored, so a stale
    shortcut or a typo starts an empty session instead of failing.

    The file type is not judged here. The ``Open`` command performs format handling and
    reports unsupported input instead of silently starting an empty session.
    """
    args = list(argv or [])
    if "--" not in args:
        return None
    for arg in args[args.index("--") + 1:]:
        if not arg:
            continue
        path = os.path.abspath(os.path.expanduser(arg))
        if isfile(path):
            return path
    return None


def open_line(path):
    """The command line that opens `path`, quoted, exactly as a user would type it. The
    whole remainder after the command name is fed as ONE token (`state.feed_line`), and
    `Open` strips the quotes, so a path with spaces ("sample model.3dm") arrives
    intact.
    """
    return f'Open "{path}"'

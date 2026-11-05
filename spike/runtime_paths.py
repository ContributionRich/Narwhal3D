"""Locate the Blender runtime that the test suites execute.

run_tests.sh selects one runtime and exports it as NARWHAL_RUNTIME_ROOT. A suite run
directly (without that variable) picks the same runtime the script would:

1. NARWHAL_RUNTIME_ROOT, when set and non-empty;
2. <root>/tools/blender-patched, when its blender is executable (the Narwhal-patched
   build that the GUI suites need);
3. <root>/tools/blender-5.2.2-linux-x64, the bootstrapped official runtime that a
   fresh clone has.

The helpers return Path objects. Their str() is the same text the suites used when the
patched path was hard-coded, so command lines are unchanged when the patched runtime is
present.
"""

import os
from pathlib import Path

PATCHED = Path('tools') / 'blender-patched'
OFFICIAL = Path('tools') / 'blender-5.2.2-linux-x64'


def runtime_root(root):
    """The selected runtime directory for the source checkout at root."""
    exported = os.environ.get('NARWHAL_RUNTIME_ROOT', '')
    if exported:
        return Path(exported)
    root = Path(root)
    if os.access(root / PATCHED / 'blender', os.X_OK):
        return root / PATCHED
    return root / OFFICIAL


def blender(root):
    return runtime_root(root) / 'blender'


def python(root):
    return runtime_root(root) / '5.2' / 'python' / 'bin' / 'python3.13'


def occt_libs(root):
    return (runtime_root(root) / '5.2' / 'python' / 'lib' / 'python3.13'
            / 'site-packages' / 'cadquery_ocp_novtk.libs')


def is_patched(root):
    """True when the selected runtime is the Narwhal-patched tools/blender-patched build."""
    return runtime_root(root).resolve() == (Path(root) / PATCHED).resolve()

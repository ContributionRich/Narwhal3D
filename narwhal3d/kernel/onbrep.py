"""Adapter for an optional helper that creates exact multi-face ``rhino3dm`` Breps.

The public runtime only discovers prebuilt helpers. It never downloads dependencies or
compiles a helper. Packagers and developers may provide a compatible executable through
``NARWHAL3D_BREP_WRITER``; a packaged platform helper is used otherwise. Every candidate
must identify the supported wire protocol before it receives geometry.
"""
from __future__ import annotations

import base64
import os
import platform
import subprocess
import sys

import rhino3dm as r3

#: The protocol this module speaks; a helper must announce it from ``--version``.
PROTOCOL = "brep_writer ONBREP 1"
#: Optional explicit helper path.
ENV_VAR = "NARWHAL3D_BREP_WRITER"


def platform_tag():
    """Return the suffix used by the packaged helper for this Python platform."""
    return f"{sys.platform}-{platform.machine().lower()}"


SHIPPED = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "bin",
    f"brep_writer-{platform_tag()}" + (".exe" if sys.platform == "win32" else ""),
)

#: path -> (mtime, answered-the-protocol). A helper is probed once per file version.
_probed = {}
_last_error = [None]


class OnBrepError(RuntimeError):
    """The helper was unavailable, refused the input, or returned invalid data."""


def _speaks_protocol(path):
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return False
    hit = _probed.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=20
        )
        answer = result.stdout.strip()
        ok = result.returncode == 0 and (
            answer == PROTOCOL or answer.startswith(PROTOCOL + " ")
        )
    except (OSError, subprocess.SubprocessError):
        ok = False
    _probed[path] = (stamp, ok)
    return ok


def candidates():
    """Return the explicit and packaged helper candidates in lookup order."""
    out = []
    explicit = os.environ.get(ENV_VAR)
    if explicit:
        out.append(("environment", explicit))
    out.append(("shipped", SHIPPED))
    return out


def helper_path():
    """Return the first executable candidate that speaks :data:`PROTOCOL`."""
    for _where, path in candidates():
        if os.path.isfile(path) and os.access(path, os.X_OK) and _speaks_protocol(path):
            return path
    return None


def _unavailable_reason():
    explicit = os.environ.get(ENV_VAR)
    if explicit:
        override = (
            f"{ENV_VAR}={explicit!r} is missing, not executable, or does not speak "
            f"{PROTOCOL!r}"
        )
    else:
        override = f"{ENV_VAR} is not set"
    return (
        "the B-rep writer is not built into this installation: "
        f"{override}; no compatible packaged helper was found at {SHIPPED!r}. "
        f"Set {ENV_VAR} to a compatible executable or install the packaged helper. "
        "The helper and its build toolchain are not included in this release."
    )


#: rhino3dm's Encode/Decode dictionary keys.
ARCHIVE_3DM = 60
ENCODE_VERSION = 10000


def last_error():
    """Return the reason the last exact conversion fell back, or ``None``."""
    return _last_error[0]


def available():
    """Return whether a protocol-compatible helper is currently discoverable."""
    return helper_path() is not None


def build():
    """Compatibility entry point; source building is unavailable in public releases."""
    message = (
        "The B-rep writer build toolchain is not included in this release. "
        f"Set {ENV_VAR} to a compatible executable or install the packaged helper at "
        f"{SHIPPED}."
    )
    _last_error[0] = message
    return False, message


def ensure():
    """Probe configured candidates only and report whether a helper is available."""
    if helper_path() is not None:
        return True
    _last_error[0] = _unavailable_reason()
    return False


def _signed32(value):
    return value - (1 << 32) if value >= (1 << 31) else value


def brep_from_description(text):
    """Run the helper for ``text`` and decode its archive as an ``r3.Brep``."""
    helper = helper_path()
    if helper is None:
        raise OnBrepError(_unavailable_reason())
    try:
        result = subprocess.run(
            [helper], input=text.encode(), capture_output=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OnBrepError(f"could not run B-rep writer: {exc}") from exc
    error_text = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        raise OnBrepError(
            error_text.strip() or f"brep_writer exited {result.returncode}"
        )
    try:
        version = int(error_text.split("opennurbs", 1)[1].split()[0])
    except (IndexError, ValueError) as exc:
        raise OnBrepError("brep_writer did not report an archive version") from exc
    brep = r3.CommonObject.Decode(
        {
            "version": ENCODE_VERSION,
            "archive3dm": ARCHIVE_3DM,
            "opennurbs": _signed32(version),
            "data": base64.b64encode(result.stdout).decode(),
        }
    )
    if not isinstance(brep, r3.Brep):
        raise OnBrepError(
            f"the archive decoded as {type(brep).__name__}, not a Brep"
        )
    return brep


def exact_brep(shape, occt):
    """Return the exact ``r3.Brep`` for an OCCT shape, or ``None`` with a reason."""
    if not ensure():
        return None
    _last_error[0] = None
    try:
        return brep_from_description(occt.brep_description(shape))
    except Exception as exc:  # noqa: BLE001
        _last_error[0] = str(exc)
        return None

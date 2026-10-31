#!/usr/bin/env bash
# Primary launcher for Narwhal 3D.
#
# Starts the bundled application runtime with our bootstrap. Any arguments are
# forwarded after `--`; `./narwhal3d.sh model.3dm` and the desktop entry's `%f`
# both reach launch/open_arg.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Converter tools are optional at launch, but an app-local bundle must win over
# host tools. Normal launch deliberately does not download or bootstrap it.
runtime_platform="$(uname -s 2>/dev/null || printf unknown)"
runtime_arch="$(uname -m 2>/dev/null || printf unknown)"
case "$runtime_platform" in
  Linux*) runtime_platform=linux ;;
  Darwin*) runtime_platform=macos ;;
  MINGW*|MSYS*|CYGWIN*) runtime_platform=windows ;;
  *) runtime_platform="$(printf '%s' "$runtime_platform" | tr '[:upper:]' '[:lower:]')" ;;
esac
case "$runtime_arch" in
  amd64|x86-64) runtime_arch=x86_64 ;;
  aarch64|armv8*) runtime_arch=arm64 ;;
esac
case "${runtime_platform}-${runtime_arch}" in
  linux-x86_64) native_target=x86_64-unknown-linux-gnu ;;
  linux-arm64) native_target=aarch64-unknown-linux-gnu ;;
  macos-x86_64) native_target=x86_64-apple-darwin ;;
  macos-arm64) native_target=aarch64-apple-darwin ;;
  windows-x86_64) native_target=x86_64-pc-windows-msvc ;;
  *) native_target= ;;
esac

# Prefer the patched application runtime when it is available, then the stock bundle.
if [ "$runtime_platform" = macos ]; then
  BLENDER="${ROOT}/tools/blender-patched/Blender.app/Contents/MacOS/Blender"
  [ -x "$BLENDER" ] || BLENDER="${ROOT}/tools/Blender.app/Contents/MacOS/Blender"
else
  BLENDER="${ROOT}/tools/blender-patched/blender"
  [ -x "$BLENDER" ] || BLENDER="${ROOT}/tools/blender-5.2.2-linux-x64/blender"
fi

LOCAL_RUNTIME_BIN="$ROOT/runtime/${runtime_platform}-${runtime_arch}/bin"
case "$runtime_platform" in
  linux)
    native_prefix=lib
    native_suffix=.so
    document_core_name=narwhal-document-core
    if [ "$BLENDER" = "${ROOT}/tools/blender-patched/blender" ]; then
      OCCT_LIBRARY_DIR="${ROOT}/tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"
    else
      OCCT_LIBRARY_DIR="${ROOT}/tools/blender-5.2.2-linux-x64/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"
    fi
    if [ -d "$OCCT_LIBRARY_DIR" ]; then
      # Deliberately replace, rather than append, inherited host lookup paths.
      LD_LIBRARY_PATH="$OCCT_LIBRARY_DIR"
      export LD_LIBRARY_PATH
    fi
    ;;
  macos)
    native_prefix=lib
    native_suffix=.dylib
    document_core_name=narwhal-document-core
    ;;
  windows)
    native_prefix=
    native_suffix=.dll
    document_core_name=narwhal-document-core.exe
    ;;
  *)
    native_prefix=lib
    native_suffix=.so
    document_core_name=narwhal-document-core
    ;;
esac

set_native_library() {
  local variable="$1" filename="$2" crate="$3" candidate
  candidate="$ROOT/narwhal3d/native/$filename"
  [ -f "$candidate" ] || candidate="$LOCAL_RUNTIME_BIN/$filename"
  if [[ ! -f "$candidate" && -n "$native_target" ]]; then
    candidate="$ROOT/native/$crate/target/$native_target/release/$filename"
  fi
  [ -f "$candidate" ] || candidate="$ROOT/native/$crate/target/release/$filename"
  if [ -f "$candidate" ]; then
    printf -v "$variable" '%s' "$candidate"
    export "$variable"
  fi
}

if [ -d "$LOCAL_RUNTIME_BIN" ]; then
  PATH="$LOCAL_RUNTIME_BIN${PATH:+:$PATH}"
  export PATH
  [ ! -x "$LOCAL_RUNTIME_BIN/$document_core_name" ] || \
    export NARWHAL_DOCUMENT_CORE="$LOCAL_RUNTIME_BIN/$document_core_name"
fi
set_native_library NARWHAL_PURE_CORE "${native_prefix}narwhal_pure_core${native_suffix}" pure_core
set_native_library NARWHAL_SELECTION_CORE "${native_prefix}narwhal_selection_core${native_suffix}" selection_core
set_native_library NARWHAL_CDR_CORE "${native_prefix}narwhal_cdr_core${native_suffix}" cdr_core
set_native_library NARWHAL_GEOMETRY_CORE "${native_prefix}narwhal_geometry_core${native_suffix}" geometry_core
set_native_library NARWHAL_PLATFORM_CORE "${native_prefix}narwhal_platform_core${native_suffix}" platform_core

if [[ "${NARWHAL3D_LAUNCHER_TEST:-}" == native-paths ]]; then
  printf 'NARWHAL_PURE_CORE=%s\n' "${NARWHAL_PURE_CORE:-}"
  printf 'NARWHAL_SELECTION_CORE=%s\n' "${NARWHAL_SELECTION_CORE:-}"
  printf 'NARWHAL_CDR_CORE=%s\n' "${NARWHAL_CDR_CORE:-}"
  printf 'NARWHAL_GEOMETRY_CORE=%s\n' "${NARWHAL_GEOMETRY_CORE:-}"
  printf 'NARWHAL_PLATFORM_CORE=%s\n' "${NARWHAL_PLATFORM_CORE:-}"
  exit 0
fi
BOOTSTRAP="${ROOT}/launch/bootstrap.py"
WELCOME="${ROOT}/artifacts/welcome/${runtime_platform}-${runtime_arch}/narwhal-welcome"
[ -x "$WELCOME" ] || WELCOME="${ROOT}/runtime/${runtime_platform}-${runtime_arch}/bin/narwhal-welcome"
# The template is named `.desktop.in`, not `.desktop`, deliberately: it contains
# __ROOT__ placeholders and is not itself an installable desktop entry.
DESKTOP_TEMPLATE="${ROOT}/launch/narwhal3d.desktop.in"
APP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${APP_DIR}/narwhal3d.desktop"

MIME_XML="${ROOT}/launch/narwhal3d-3dm.xml"
PROJECT_MIME_XML="${ROOT}/launch/narwhal3d-project.xml"
MIME_DIR="${HOME}/.local/share/mime"


register_project() {
  command -v xdg-mime >/dev/null 2>&1 || return 0
  if [[ -f "$PROJECT_MIME_XML" ]] && command -v update-mime-database >/dev/null 2>&1; then
    mkdir -p "${MIME_DIR}/packages" && cp "$PROJECT_MIME_XML" "${MIME_DIR}/packages/" \
      && update-mime-database "$MIME_DIR" >/dev/null 2>&1 || true
  fi
  xdg-mime default narwhal3d.desktop application/x-narwhal3d-project >/dev/null 2>&1 || true
  echo "[narwhal3d] .narwhal projects open with Narwhal 3D"
}


register_3dm() {
  # Make a double-clicked .3dm open in this app. Stock shared-mime-info may have
  # no type for .3dm, while a desktop environment may already provide one.
  command -v xdg-mime >/dev/null 2>&1 || return 0
  local probe_dir probe type
  probe_dir="$(mktemp -d)" || return 0
  probe="${probe_dir}/probe.3dm"
  printf '3D Geometry File Format         ' > "$probe"
  type="$(xdg-mime query filetype "$probe" 2>/dev/null || true)"
  if [[ -z "$type" || "$type" == application/octet-stream || "$type" == text/plain ]]; then
    if [[ -f "$MIME_XML" ]] && command -v update-mime-database >/dev/null 2>&1; then
      mkdir -p "${MIME_DIR}/packages" && cp "$MIME_XML" "${MIME_DIR}/packages/" \
        && update-mime-database "$MIME_DIR" >/dev/null 2>&1 || true
      type="$(xdg-mime query filetype "$probe" 2>/dev/null || true)"
    fi
  fi
  rm -rf "$probe_dir"
  [[ -n "$type" && "$type" != application/octet-stream ]] || return 0
  if ! grep -q "^MimeType=.*${type};" "$DESKTOP_FILE"; then
    sed -i "s|^MimeType=|MimeType=${type};|" "$DESKTOP_FILE"
  fi
  xdg-mime default narwhal3d.desktop "$type" >/dev/null 2>&1 || true
  echo "[narwhal3d] .3dm files ($type) open with Narwhal 3D"
}

install_desktop() {
  # Write a per-user desktop entry pointing at this checkout. Idempotent; never fatal.
  local template="$DESKTOP_TEMPLATE"
  if [[ ! -f "$template" ]]; then
    echo "[narwhal3d] no desktop template at $template; skipping install" >&2
    return 0
  fi
  mkdir -p "$APP_DIR" || { echo "[narwhal3d] cannot create $APP_DIR" >&2; return 0; }
  sed "s|__ROOT__|${ROOT}|g" "$template" > "$DESKTOP_FILE"
  register_project || true
  register_3dm || true
  if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_FILE" || true
  fi
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
  fi
  echo "[narwhal3d] installed $DESKTOP_FILE"
}


# A mounted AppImage is temporary, so it must not install a desktop entry that points
# inside its mount. Desktop environments may integrate the AppImage itself.
if [[ "$runtime_platform" == linux ]]; then
  if [[ "${NARWHAL3D_APPIMAGE:-0}" != 1 ]]; then
    # --install: (re)write the desktop entry and exit. Otherwise, auto-install on
    # first run so the app appears in the launcher without extra steps.
    case "${1:-}" in
      --install)
        install_desktop
        exit 0
        ;;
    esac
    # Reinstall when the template changed since the entry was written.
    if [[ ! -f "$DESKTOP_FILE" || "$DESKTOP_TEMPLATE" -nt "$DESKTOP_FILE" ]]; then
      install_desktop
    fi
  elif [[ "${1:-}" == --install ]]; then
    echo "[narwhal3d] AppImage desktop integration is managed by the desktop environment"
    exit 0
  fi
elif [[ "${1:-}" == --install ]]; then
  echo "[narwhal3d] desktop integration is not implemented for $runtime_platform source builds"
  exit 0
fi

if [[ ! -x "$BLENDER" ]]; then
  echo "[narwhal3d] error: application runtime not found at $BLENDER" >&2
  exit 1
fi

# Native-only owners must match the source before the welcome or graphical runtime
# starts. Otherwise an import failure leaves a misleading default Blender window.
if [[ "$runtime_platform" == linux ]]; then
  native_python="$(dirname "$BLENDER")/5.2/python/bin/python3.13"
  if [[ ! -x "$native_python" ]] || ! "$native_python" -I -B "$ROOT/launch/check_native.py"; then
    echo "[narwhal3d] required native startup check failed; graphical runtime not launched" >&2
    if [[ "${NARWHAL3D_NO_DIALOGS:-0}" != 1 ]] && command -v kdialog >/dev/null 2>&1; then
      timeout 30 kdialog --error 'Narwhal 3D cannot start because required native components are missing or incompatible. Rebuild or reinstall matching components. No modeling window was opened.' || true
    fi
    exit 2
  fi
fi

# The patched runtime withholds its first presented frame until the complete Narwhal
# layout signals readiness. Stock fallback runtimes ignore these environment values.
export NARWHAL3D_DEFER_FIRST_PRESENT=1
export NARWHAL3D_STATUSBAR_HEIGHT=37
unset NARWHAL3D_UI_READY || true
unset NARWHAL3D_PRESENT_WATCHDOG || true

# The native welcome window starts the runtime behind itself, forwards model arguments,
# and remains as the supervising process after its visible surface fades. Source checkouts
# without a built welcome binary retain the direct-launch compatibility path.
if [[ -x "$WELCOME" ]]; then
  exec "$WELCOME" --runtime "$BLENDER" --bootstrap "$BOOTSTRAP" -- "$@"
fi

# --factory-startup gives a predictable layout every launch; the bootstrap
# applies Narwhal 3D's own interface and theme.
exec "$BLENDER" \
  --factory-startup \
  --python-exit-code 1 \
  --python "$BOOTSTRAP" \
  -- "$@"

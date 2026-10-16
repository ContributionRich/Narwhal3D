#!/usr/bin/env bash
# Primary launcher for Narwhal 3D.
#
# Starts the bundled application runtime with our bootstrap. Any arguments are
# forwarded after `--`; `./narwhal3d.sh model.3dm` and the desktop entry's `%f`
# both reach launch/open_arg.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Converter tools are optional at launch, but an app-local Linux bundle must win
# over host tools. Normal launch deliberately does not download or bootstrap it.
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
LOCAL_RUNTIME_BIN="$ROOT/runtime/${runtime_platform}-${runtime_arch}/bin"
if [ -d "$LOCAL_RUNTIME_BIN" ]; then
  PATH="$LOCAL_RUNTIME_BIN${PATH:+:$PATH}"
  export PATH
fi
# Prefer the patched application runtime when it is available, then the stock bundle.
BLENDER="${ROOT}/tools/blender-patched/blender"
[ -x "$BLENDER" ] || BLENDER="${ROOT}/tools/blender-5.2.2-linux-x64/blender"
BOOTSTRAP="${ROOT}/launch/bootstrap.py"
# The template is named `.desktop.in`, not `.desktop`, deliberately: it contains
# __ROOT__ placeholders and is not itself an installable desktop entry.
DESKTOP_TEMPLATE="${ROOT}/launch/narwhal3d.desktop.in"
APP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${APP_DIR}/narwhal3d.desktop"

MIME_XML="${ROOT}/launch/narwhal3d-3dm.xml"
MIME_DIR="${HOME}/.local/share/mime"


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

if [[ ! -x "$BLENDER" ]]; then
  echo "[narwhal3d] error: application runtime not found at $BLENDER" >&2
  exit 1
fi

# The patched runtime withholds its first presented frame until the complete Narwhal
# layout signals readiness. Stock fallback runtimes ignore these environment values.
export NARWHAL3D_DEFER_FIRST_PRESENT=1
unset NARWHAL3D_UI_READY || true
unset NARWHAL3D_PRESENT_WATCHDOG || true

# --factory-startup gives a predictable layout every launch; the bootstrap
# applies Narwhal 3D's own interface and theme.
exec "$BLENDER" \
  --factory-startup \
  --python "$BOOTSTRAP" \
  -- "$@"

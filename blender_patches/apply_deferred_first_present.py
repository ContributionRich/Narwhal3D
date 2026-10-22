#!/usr/bin/env python3
"""Apply Narwhal's deferred-first-presentation patch to Blender 5.2.2."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "blender-src" / "source" / "blender"

WINDOW_OLD = '''    /* Until screens get drawn, draw a default background using the window theme color. */
    wm_window_swap_buffer_acquire(win);
    GPU_clear_color(window_bg_color[0], window_bg_color[1], window_bg_color[2], 1.0f);

    /* Needed here, because it's used before it reads #UserDef. */
    WM_window_dpi_set_userdef(win);

    wm_window_swap_buffer_release(win);

    /* Clear double buffer to avoids flickering of new windows on certain drivers, see #97600. */
    GPU_clear_color(window_bg_color[0], window_bg_color[1], window_bg_color[2], 1.0f);
'''
WINDOW_NEW = '''    /* Narwhal builds its application layout through Python after native window creation.
     * Suppress the factory-layout buffer when its launcher requests deferred presentation. */
    const char *narwhal_defer = std::getenv("NARWHAL3D_DEFER_FIRST_PRESENT");
    const bool narwhal_waiting = narwhal_defer != nullptr && narwhal_defer[0] == '1';

    /* Needed here, because it's used before it reads #UserDef. */
    WM_window_dpi_set_userdef(win);

    if (!narwhal_waiting) {
      /* Until screens get drawn, draw a default background using the window theme color. */
      wm_window_swap_buffer_acquire(win);
      GPU_clear_color(window_bg_color[0], window_bg_color[1], window_bg_color[2], 1.0f);
      wm_window_swap_buffer_release(win);

      /* Clear double buffer to avoids flickering of new windows on certain drivers, see #97600. */
      GPU_clear_color(window_bg_color[0], window_bg_color[1], window_bg_color[2], 1.0f);
    }
'''

RELEASE_OLD = '''void wm_window_swap_buffer_release(wmWindow *win)
{
  GHOST_IWindow *ghost_window = static_cast<GHOST_IWindow *>(win->runtime->ghostwin);
  ghost_window->swapBufferRelease();
}
'''
RELEASE_NEW = '''static bool narwhal_defer_first_present(GHOST_IWindow *ghost_window)
{
  /* OpenGL's swap acquire is stateless. Vulkan acquires a presentation image, so
   * withholding its paired release would strand that image before the next draw. */
  if (ghost_window->getDrawingContextType() != GHOST_kDrawingContextTypeOpenGL) {
    return false;
  }
  const char *defer = std::getenv("NARWHAL3D_DEFER_FIRST_PRESENT");
  if (defer == nullptr || defer[0] != '1') {
    return false;
  }
  const char *watchdog = std::getenv("NARWHAL3D_PRESENT_WATCHDOG");
  if (watchdog != nullptr && watchdog[0] == '1') {
    return false;
  }
  const char *ready = std::getenv("NARWHAL3D_UI_READY");
  if (ready != nullptr && ready[0] == '1') {
    return false;
  }
  /* Never strand an invisible window when bootstrap fails. Drawing and area reflow
   * continue into the back buffer; only presentation is withheld. */
  static const auto wait_started = std::chrono::steady_clock::now();
  return std::chrono::steady_clock::now() - wait_started < std::chrono::seconds(10);
}

void wm_window_swap_buffer_release(wmWindow *win)
{
  GHOST_IWindow *ghost_window = static_cast<GHOST_IWindow *>(win->runtime->ghostwin);
  if (narwhal_defer_first_present(ghost_window)) {
    return;
  }
  static bool narwhal_release_reported = false;
  const char *narwhal_defer = std::getenv("NARWHAL3D_DEFER_FIRST_PRESENT");
  if (!narwhal_release_reported && narwhal_defer != nullptr && narwhal_defer[0] == '1' &&
      ghost_window->getDrawingContextType() == GHOST_kDrawingContextTypeOpenGL)
  {
    const char *ready = std::getenv("NARWHAL3D_UI_READY");
    std::fprintf(stderr,
                 "[narwhal3d] first presentation released (%s)\\n",
                 ready != nullptr && ready[0] == '1' ? "ui-ready" : "watchdog");
    narwhal_release_reported = true;
  }
  ghost_window->swapBufferRelease();
}
'''

EDITS = (
    ("windowmanager/intern/wm_window.cc", WINDOW_OLD, WINDOW_NEW),
    ("windowmanager/intern/wm_window.cc", RELEASE_OLD, RELEASE_NEW),
)


def apply(source: str | os.PathLike[str] | None = None) -> list[str]:
    root = Path(source) if source is not None else Path(SRC)
    changed = []
    for relative, old, new in EDITS:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        if new in text:
            continue
        if text.count(old) != 1:
            raise RuntimeError(f"deferred-present patch anchor mismatch: {relative}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        changed.append(relative)
    return changed


def main() -> None:
    for path in apply():
        print(f"patched: {path}")


if __name__ == "__main__":
    main()

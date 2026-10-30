#!/usr/bin/env python3
"""Compile and execute the actual patched release functions for both GPU builds."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / "blender_patches/apply_deferred_first_present.py"
spec = importlib.util.spec_from_file_location("present", path)
present = importlib.util.module_from_spec(spec)
spec.loader.exec_module(present)

PRELUDE = r'''
#include <chrono>
#include <cstdio>
#include <cstdlib>
#ifdef WITH_OPENGL_BACKEND
enum { GHOST_kDrawingContextTypeOpenGL = 1 };
#endif
struct GHOST_IWindow {
  int backend = 1;
  int swaps = 0;
  int getDrawingContextType() { return backend; }
  void swapBufferRelease() { ++swaps; }
};
struct Runtime { void *ghostwin; };
struct wmWindow { Runtime *runtime; };
'''
MAIN = r'''
int main() {
  GHOST_IWindow ghost;
  Runtime runtime{&ghost};
  wmWindow window{&runtime};
  setenv("NARWHAL3D_DEFER_FIRST_PRESENT", "1", 1);
  unsetenv("NARWHAL3D_UI_READY");
  unsetenv("NARWHAL3D_PRESENT_WATCHDOG");
  wm_window_swap_buffer_release(&window);
#ifdef WITH_OPENGL_BACKEND
  if (ghost.swaps != 0) return 1;
#else
  if (ghost.swaps != 1) return 2;
#endif
  ghost.swaps = 0;
  ghost.backend = 2;
  wm_window_swap_buffer_release(&window);
  if (ghost.swaps != 1) return 3;
  ghost.backend = 1;
  setenv("NARWHAL3D_UI_READY", "1", 1);
  wm_window_swap_buffer_release(&window);
  if (ghost.swaps != 2) return 4;
  return 0;
}
'''


class BackendTests(unittest.TestCase):
    def test_opengl_and_metal_only_builds_release_correctly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "release.cc"
            source.write_text(PRELUDE + present.RELEASE_NEW + MAIN)
            for flags in ([], ["-DWITH_OPENGL_BACKEND"]):
                with self.subTest(flags=flags):
                    result = subprocess.run(["c++", "-std=c++17", *flags, str(source), "-o", str(root / "probe")],
                                            text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    subprocess.run([str(root / "probe")], check=True, timeout=5, capture_output=True)

    def test_previous_patch_upgrades_without_resetting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "windowmanager/intern/wm_window.cc"
            source.parent.mkdir(parents=True)
            source.write_text(present.WINDOW_NEW + present.RELEASE_PREVIOUS)
            self.assertEqual(present.apply(root), ["windowmanager/intern/wm_window.cc"])
            self.assertIn(present.RELEASE_NEW, source.read_text())
            self.assertEqual(present.apply(root), [])


if __name__ == "__main__":
    unittest.main()

"""Real source-launcher/preflight contract using owned fake graphical executables.

Actual native libraries run in a child; no Blender or welcome window is launched.
The complementary startup_layout_gui.sh checks the real welcome-style GUI route.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCES=('native_textfield','native_popup','command_transcript','native_transcript',
         'native_document_session','native_history','undo','numparse','native_pure')


class DesktopNativeStartup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='desktop-native-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'launch').mkdir()
        (self.root/'narwhal3d/native').mkdir(parents=True)
        shutil.copy2(ROOT/'narwhal3d.sh',self.root/'narwhal3d.sh')
        shutil.copy2(ROOT/'launch/check_native.py',self.root/'launch/check_native.py')
        for name in SOURCES:
            shutil.copy2(ROOT/'narwhal3d'/(name+'.py'),self.root/'narwhal3d'/(name+'.py'))
        libraries={
            'libnarwhal_ui_core.so':Path(os.environ.get('NARWHAL_UI_CORE_LIBRARY',ROOT/'artifacts/ui-core/linux-x86_64/libnarwhal_ui_core.so')),
            'libnarwhal_application_core.so':Path(os.environ.get('NARWHAL_APPLICATION_CORE',ROOT/'artifacts/application-core/linux-x86_64/libnarwhal_application_core.so')),
            'libnarwhal_pure_core.so':Path(os.environ.get('NARWHAL_PURE_CORE',ROOT/'native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so')),
        }
        for name,source in libraries.items():shutil.copy2(source,self.root/'narwhal3d/native'/name)
        runtime=self.root/'tools/blender-patched/blender'
        python=runtime.parent/'5.2/python/bin/python3.13'
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        self.started=self.root/'graphical-started'
        for executable in (runtime,self.root/'artifacts/welcome/linux-x86_64/narwhal-welcome'):
            executable.parent.mkdir(parents=True,exist_ok=True)
            executable.write_text('#!/bin/sh\nprintf started > "'+str(self.started)+'"\n')
            executable.chmod(0o755)
        self.env={k:v for k,v in os.environ.items() if not k.startswith('NARWHAL')}
        self.env.update(HOME=str(self.root),NARWHAL3D_APPIMAGE='1',NARWHAL3D_NO_DIALOGS='1',PYTHONDONTWRITEBYTECODE='1')

    def run_launcher(self):
        return subprocess.run(['bash',str(self.root/'narwhal3d.sh')],env=self.env,text=True,
                              capture_output=True,timeout=20)

    def test_matching_native_components_launch_welcome(self):
        result=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('required native startup checks passed',result.stdout)
        self.assertTrue(self.started.exists())

    def test_missing_ui_never_launches_graphical_runtime(self):
        (self.root/'narwhal3d/native/libnarwhal_ui_core.so').unlink()
        result=self.run_launcher()
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('graphical runtime not launched',result.stderr)
        self.assertFalse(self.started.exists())

    def test_broken_numeric_core_never_launches_graphical_runtime(self):
        (self.root/'narwhal3d/native/libnarwhal_pure_core.so').write_bytes(b'invalid library')
        result=self.run_launcher()
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('number parser unavailable',result.stderr)
        self.assertFalse(self.started.exists())

    def test_bypass_mutation_is_detected(self):
        (self.root/'narwhal3d/native/libnarwhal_ui_core.so').unlink()
        launcher=self.root/'narwhal3d.sh'
        source=launcher.read_text()
        marker='! "$native_python" -I -B "$ROOT/launch/check_native.py"'
        self.assertEqual(source.count(marker),1)
        launcher.write_text(source.replace(marker,'false'))
        result=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(self.started.exists(),'mutant must demonstrate the forbidden launch')
        with self.assertRaises(AssertionError):
            self.assertEqual(result.returncode,2,'startup admission regression')
        launcher.write_text(source)
        self.started.unlink()
        result=self.run_launcher()
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertFalse(self.started.exists())


if __name__=='__main__':unittest.main(verbosity=2)

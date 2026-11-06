#!/usr/bin/env python3
"""Pure command-shape tests for observer qualification; no compiler or host runtime is run."""
from __future__ import annotations

import argparse
import importlib.util
import io
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
QUALIFY = ROOT / 'native/document_observer/qualify.py'
spec = importlib.util.spec_from_file_location('observer_qualify', QUALIFY)
qualify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualify)

VCVARS = PureWindowsPath(r'C:\VS\VC\Auxiliary\Build\vcvars64.bat')
SOURCE_TREE = PureWindowsPath(r'C:\narwhal\sdist\rhino3dm-8.35.0')
WORK = PureWindowsPath(r'C:\work\control')
OPENNURBS = PureWindowsPath(r'C:\build\opennurbs')
PYTHON_LIB = PureWindowsPath(r'C:\Py\libs\python313.lib')
SYSTEM_LIBS = ('shlwapi.lib', 'usp10.lib', 'shell32.lib', 'ole32.lib', 'rpcrt4.lib', 'advapi32.lib', 'user32.lib', 'gdi32.lib')


class WindowsCommandTests(unittest.TestCase):
    def command(self, **overrides):
        options = dict(vcvars=VCVARS, python_include=PureWindowsPath(r'C:\Py\include'), source_tree=SOURCE_TREE,
                       opennurbs_lib_dir=OPENNURBS, python_lib=PYTHON_LIB)
        options.update(overrides)
        return qualify.windows_compile_command(WORK / 'observer.cpp', WORK / qualify.WINDOWS_LIBRARY, **options)

    def test_is_one_cmd_line_that_runs_vcvars_then_msvc_md_compile(self):
        command = self.command()
        self.assertIsInstance(command, str)
        tokens = command.split()
        self.assertEqual(tokens[:7], ['cmd', '/d', '/c', 'call', f'"{VCVARS}"', '&&', 'cl'])
        for flag in ('/nologo', '/LD', '/MD', '/O2', '/EHsc', '/std:c++17', '/bigobj', '/DNOMINMAX',
                     '/DUNICODE', '/D_UNICODE', '/link'):
            self.assertIn(flag, tokens)
        self.assertNotIn('/MT', tokens)
        self.assertNotIn('/MDd', tokens)
        self.assertNotIn('/MTd', tokens)

    def test_output_is_the_pyd_and_its_object_stays_in_the_variant_directory(self):
        tokens = self.command().split()
        self.assertIn('"/OUT:C:\\work\\control\\_narwhal_document_observer.pyd"', tokens)
        self.assertIn('"/FoC:\\work\\control\\_narwhal_document_observer.obj"', tokens)
        self.assertIn('"C:\\work\\control\\observer.cpp"', tokens)

    def test_links_openNURBS_zlib_python_and_system_libraries(self):
        names = [token.strip('"').rsplit('\\', 1)[-1] for token in self.command().split()]
        for name in ('opennurbsStatic.lib', 'zlib.lib', 'python313.lib', *SYSTEM_LIBS):
            self.assertIn(name, names)
        self.assertIn('"C:\\build\\opennurbs\\opennurbsStatic.lib"', self.command().split())
        self.assertIn('"C:\\Py\\libs\\python313.lib"', self.command().split())

    def test_include_paths_cover_python_bindings_pybind11_and_opennurbs(self):
        tokens = self.command().split()
        for include in (r'C:\Py\include', r'C:\narwhal\sdist\rhino3dm-8.35.0\src\bindings',
                        r'C:\narwhal\sdist\rhino3dm-8.35.0\src\lib\pybind11\include',
                        r'C:\narwhal\sdist\rhino3dm-8.35.0\src\lib\opennurbs'):
            self.assertIn(f'"/I{include}"', tokens)

    def test_no_linux_wrappers_or_gcc_flags_leak_into_the_windows_line(self):
        command = self.command()
        for word in ('systemd-run', 'flock', 'timeout', 'g++', '-fPIC', '.a"'):
            self.assertNotIn(word, command)

    def test_vcvars_path_with_spaces_stays_one_quoted_argument(self):
        command = self.command(vcvars=PureWindowsPath(r'C:\Program Files\VS\vcvars64.bat'))
        self.assertIn('cmd /d /c call "C:\\Program Files\\VS\\vcvars64.bat" &&', command)


@patch.dict('os.environ', {'NARWHAL_MEMORY_BOUND': 'systemd'})  # the systemd command shape, anywhere
class LinuxCommandTests(unittest.TestCase):
    def test_gcc_compile_command_is_unchanged(self):
        base = Path('/opt/nw-observer-fixture')
        python_include, source_tree = base / 'python/include', base / 'sdist'
        candidate, library = base / 'control/observer.cpp', base / 'control/_narwhal_document_observer.so'
        archives = [base / 'pic/libopennurbsStatic.a', base / 'pic/zlib/libzlib.a']
        command = qualify.linux_compile_command(candidate, library, python_include=python_include,
                                                source_tree=source_tree, archives=archives)

        def resolved(path):
            return str(Path(path).resolve())

        self.assertEqual(command, [
            'flock', '-w', '120', '/tmp/narwhal3d-heavy-build.lock', 'systemd-run', '--user', '--scope', '--quiet',
            '-p', 'MemoryMax=1024M', '-p', 'MemorySwapMax=0', 'timeout', '--kill-after=5s', '120s',
            'g++', '-std=c++17', '-O2', '-fPIC', '-shared', '-fvisibility=hidden',
            '-Wl,--exclude-libs,ALL', '-I' + resolved(python_include),
            '-I' + resolved(source_tree / 'src/bindings'),
            '-I' + resolved(source_tree / 'src/lib/pybind11/include'),
            resolved(candidate), '-Wl,--start-group',
            resolved(archives[0]), resolved(archives[1]), '-Wl,--end-group', '-lpthread',
            '-o', resolved(library)])

    def test_bounded_wrapper_is_unchanged_on_linux(self):
        with patch.object(qualify, 'WINDOWS', False):
            self.assertEqual(qualify.bounded(30, ['py', '-B']), [
                'systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1024M', '-p', 'MemorySwapMax=0',
                'timeout', '--kill-after=5s', '30s', 'py', '-B'])

    def test_bounded_wrapper_is_only_the_subprocess_timeout_on_windows(self):
        with patch.object(qualify, 'WINDOWS', True):
            self.assertEqual(qualify.bounded(30, ['py', '-B']), ['py', '-B'])


class LinkInputTests(unittest.TestCase):
    def inputs(self, windows, **fields):
        args = argparse.Namespace(pic_build=PurePosixPath('/pic'), vcvars=VCVARS, opennurbs_lib_dir=OPENNURBS,
                                  python_lib=PYTHON_LIB, python_include=PureWindowsPath(r'C:\Py\include'),
                                  source_tree=SOURCE_TREE)
        for name, value in fields.items():
            setattr(args, name, value)
        with patch.object(qualify, 'WINDOWS', windows):
            return qualify.link_inputs(args)

    def test_windows_records_the_two_openNURBS_libs_and_python_lib(self):
        archives, _ = self.inputs(True)
        self.assertEqual(archives, [OPENNURBS / 'opennurbsStatic.lib', OPENNURBS / 'zlib.lib', PYTHON_LIB])

    def test_windows_requires_vcvars_before_compiling(self):
        _, needed = self.inputs(True)
        self.assertIn(VCVARS, needed)
        _, needed = self.inputs(True, vcvars=PureWindowsPath(r'C:\missing\vcvars64.bat'))
        self.assertIn(PureWindowsPath(r'C:\missing\vcvars64.bat'), needed)

    def test_linux_keeps_the_pic_archives_and_no_vcvars(self):
        archives, needed = self.inputs(False)
        self.assertEqual(archives[0], PurePosixPath('/pic/libopennurbsStatic.a'))
        self.assertNotIn(VCVARS, needed)


class PlatformTagTests(unittest.TestCase):
    def tag(self, system, machine):
        with patch.object(qualify.sys, 'platform', system), patch.object(qualify.platform, 'machine', return_value=machine):
            return qualify.platform_tag()

    def test_windows_and_linux_tags(self):
        self.assertEqual(self.tag('win32', 'AMD64'), 'windows-x86_64')
        self.assertEqual(self.tag('linux', 'x86_64'), 'linux-x86_64')


class ArgumentTests(unittest.TestCase):
    BASE = ['--source-tree', 'src', '--sdist', 'sdist.tar.gz', '--python', 'python.exe', '--rust-check', 'rust',
            '--python-include', 'include', '--output-root', 'out', '--core', 'core.dll']

    def refuses(self, windows, extra):
        with patch.object(qualify, 'WINDOWS', windows), patch.object(sys, 'argv', ['qualify.py', *self.BASE, *extra]), \
                patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                qualify.main()
        self.assertEqual(caught.exception.code, 2)

    def test_windows_requires_the_msvc_inputs_and_rejects_pic_build(self):
        self.refuses(True, ['--pic-build', 'pic'])
        self.refuses(True, ['--vcvars', 'v.bat', '--opennurbs-lib-dir', 'lib'])

    def test_linux_requires_pic_build(self):
        self.refuses(False, [])

    def test_help_parses_on_this_host(self):
        result = subprocess.run([sys.executable, '-B', str(QUALIFY), '--help'], capture_output=True, text=True,
                                timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ('--vcvars', '--opennurbs-lib-dir', '--python-lib', '--pic-build'):
            self.assertIn(option, result.stdout)


class MutationAnchorTests(unittest.TestCase):
    def test_every_mutation_anchor_is_unique_in_the_shared_source(self):
        source = (ROOT / 'native/document_observer/observer.cpp').read_text(encoding='utf-8')
        for label, old, _new, _case in qualify.MUTATIONS:
            self.assertEqual(source.count(old), 1, label)


if __name__ == '__main__':
    unittest.main(verbosity=2)

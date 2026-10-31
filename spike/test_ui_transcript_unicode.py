"""Reproducible transcript Unicode data; generation touches only transcript.rs.

Reuse qualified CPython lower/Cased/Case_Ignorable probes, not an FFI adapter.
--generate builds both supported versions with their actual interpreters.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / 'native/ui_core/transcript_unicode.py'
START = '// BEGIN TRANSCRIPT UNICODE TABLES'
END = '// END TRANSCRIPT UNICODE TABLES'


def block():
    spec = importlib.util.spec_from_file_location('qualified_unicode_generator', GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.build_command_history_block()
    text = text.replace('const PY_', 'pub(super) const PY_')
    return text


if __name__ == '__main__':
    if '--block' in sys.argv:
        assert unicodedata.unidata_version in ('15.1.0', '16.0.0')
        print(block())
    elif '--generate' in sys.argv or '--check' in sys.argv:
        interpreters = [('unicode15', ROOT / 'tools/blender-patched/5.2/python/bin/python3.13'),
                        ('unicode16', Path(sys.executable))]
        chunks = []
        for module, interpreter in interpreters:
            data = subprocess.check_output([str(interpreter), '-B', __file__, '--block'], timeout=60).decode()
            version = '15.1.0' if module == 'unicode15' else '16.0.0'
            assert f'Unicode {version}' in data
            chunks.append(f'mod {module} {{\n' + data + '}\n')
        path = ROOT / 'native/ui_core/src/transcript.rs'
        source = path.read_text()
        before, rest = source.split(START)
        _, after = rest.split(END)
        generated = before + START + '\n' + '\n'.join(chunks) + END + after
        if '--check' in sys.argv:
            # rustfmt changes layout, never data; compare whitespace-free tokens.
            assert ''.join(generated.split()) == ''.join(source.split()), 'embedded transcript Unicode data differs'
            print('checked Unicode15.1 +16 exact lowercase data')
        else:
            path.write_text(generated)
            print('generated Unicode15.1 +16 exact lowercase data')
    else:
        raise SystemExit('use --generate, --check or --block; runtime differential is in test_ui_transcript_model.py')

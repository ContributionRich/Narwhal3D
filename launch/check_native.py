"""Check required native application owners before launching the graphical runtime.

Python only orchestrates the application's real loaders/ABI calls. No bpy, document,
settings or user-file access. A source/library mismatch must not launch bare Blender.
"""
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def check():
    package = types.ModuleType('narwhal3d')
    package.__path__ = [str(ROOT / 'narwhal3d')]
    sys.modules['narwhal3d'] = package
    from narwhal3d.native_textfield import TextField
    from narwhal3d.native_popup import NativePopup
    from narwhal3d.command_transcript import Transcript
    from narwhal3d.native_document_session import Session
    from narwhal3d.undo import UndoStack
    from narwhal3d.numparse import parse_length

    field = TextField('startup')
    try:
        if field.text != 'startup':
            raise RuntimeError('native editor did not preserve startup text')
    finally:
        field._native.close()
    popup = NativePopup((320, 240))
    popup.dispose()
    transcript = Transcript()
    try:
        transcript.append('startup')
        if transcript[:] != ['startup']:
            raise RuntimeError('native transcript did not preserve startup text')
    finally:
        transcript.close()
    session = Session()
    session.close()
    history = UndoStack()
    history.close()
    if parse_length('27cm', 'Meters') != (0.27, None):
        raise RuntimeError('native numeric parser returned an invalid startup result')
    print('[narwhal3d] required native startup checks passed', flush=True)


if __name__ == '__main__':
    try:
        check()
    except Exception as error:
        print('[narwhal3d] Cannot start: ' + str(error) +
              '. Install/build matching native components; no fallback UI was launched.',
              file=sys.stderr, flush=True)
        raise SystemExit(2)

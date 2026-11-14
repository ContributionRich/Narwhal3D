"""Fixed original-Python SVG answers, NOT an SVG parser or fallback engine.

Computation qualification lives in Rust's svgpath_golden integration test.
This decoder supplies immutable anchors for Python boundary/COMPAT checks.
The original engine and capture script are private in legacy/retired-rust18.
"""
import ast
from functools import lru_cache
from pathlib import Path
import struct

FIXTURES = Path(__file__).resolve().parents[1] / 'native/pure_core/tests/fixtures'


def _text(field):
    return ''.join(chr(int(c, 16)) for c in field.split(',') if c)


@lru_cache(maxsize=1)
def _answers():
    answers = {}
    with (FIXTURES / 'svgpath_golden.txt').open() as source:
        for line in source:
            _, group, text, status, a, b, c = line.rstrip('\n').split('|')
            if group.startswith('test_generated_') and status != 'ERR':
                continue  # Rust owns the differential loops, not this host suite.
            if status == 'ERR':
                answer = (a, _text(b))
            else:
                offsets = [int(v) for v in a.split(',') if v]
                flags = [bool(int(v)) for v in b.split(',') if v]
                values = [struct.unpack('>d', bytes.fromhex(v))[0] for v in c.split(',') if v]
                beziers = [tuple(tuple(values[j:j+2]) for j in range(i, i+8, 2))
                           for i in range(0, len(values), 8)]
                ends = offsets[1:] + [len(beziers)]
                answer = ('ok', [{'beziers': beziers[start:end], 'closed': flag}
                                 for start, end, flag in zip(offsets, ends, flags)])
            answers[_text(text)] = answer
    return answers


def expected(value):
    if not isinstance(value, str):
        for line in (FIXTURES / 'svgpath_host.txt').read_text().splitlines():
            literal, kind, message = line.split('|')
            anchor = ast.literal_eval(literal)
            if type(value) is type(anchor) and value == anchor:
                if kind == 'ok':
                    return []
                raise {'ValueError': ValueError, 'TypeError': TypeError}[kind](_text(message))
        raise AssertionError('uncaptured host anchor')
    kind, answer = _answers()[value]
    if kind != 'ok':
        raise {'ValueError': ValueError, 'TypeError': TypeError}[kind](answer)
    return answer

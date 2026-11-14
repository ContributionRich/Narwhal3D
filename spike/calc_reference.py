"""Static original-calculator answers only: no evaluator, parser, or replay.

Rust owns differential qualification. Python uses small boundary fixtures and
full error strings; private original/capture receipts are not runtime inputs.
"""
import ast
from functools import lru_cache
from pathlib import Path
import struct

FIXTURES = Path(__file__).resolve().parents[1] / 'native/pure_core/tests/fixtures'


def text(field):
    return ''.join(chr(int(c, 16)) for c in field.split(',') if c)


def number(field):
    return None if field == '-' else struct.unpack('>d', bytes.fromhex(field))[0]


def rows(name='boundary'):
    with (FIXTURES / ('calc_' + name + '.txt')).open() as source:
        for line in source:
            r = line.rstrip('\n').split('|')
            yield text(r[2]), ast.literal_eval(text(r[3])), (number(r[6]), text(r[7]) or None), r


@lru_cache(maxsize=1)
def _anchors():
    return {(source, repr(model)): want for source, model, want, _ in rows()}


def expected(source, model='Millimeters'):
    if isinstance(source, str):
        return _anchors()[source, repr(model)]
    with (FIXTURES / 'calc_host.txt').open() as fixture:
        for line in fixture:
            literal, message = line.rstrip('\n').split('|')
            anchor = ast.literal_eval(text(literal))
            if type(source) is type(anchor) and source == anchor:
                return None, text(message)
    raise AssertionError('uncaptured host anchor')

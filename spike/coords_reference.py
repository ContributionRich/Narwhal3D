"""Static original-coordinate answers only; no grammar or scalar replay.

Coordinate computation qualification lives in Rust coords_golden. Original
production calc/bearing dependencies and capture hashes are in provenance.
"""
import ast
from functools import lru_cache
from pathlib import Path
import struct

FIXTURES = Path(__file__).resolve().parents[1] / 'native/pure_core/tests/fixtures'


def text(field):
    return ''.join(chr(int(c, 16)) for c in field.split(',') if c)


def number(field):
    return struct.unpack('>d', bytes.fromhex(field))[0]


def rows(name):
    with (FIXTURES / ('coords_' + name + '.txt')).open() as source:
        for line in source:
            yield line.rstrip('\n').split('|')


@lru_cache(maxsize=1)
def _host():
    return {(r[0], text(r[1])): (r[2], text(r[3])) for r in rows('host')}


def expected(operation, *args):
    status, answer = _host()[operation, repr(args)]
    if status == 'OK':
        return ast.literal_eval(answer)
    raise {'TypeError': TypeError, 'AttributeError': AttributeError, 'ValueError': ValueError}[status](answer)


def examples():
    return [(text(r[0]), tuple(map(number, r[1].split(','))), bool(int(r[2])), bool(int(r[3])))
            for r in rows('examples')]

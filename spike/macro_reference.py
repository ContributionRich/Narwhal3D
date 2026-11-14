"""Static original macro/planner answers: no tokenizer, planner, or replay.

Both original engines and capture hashes are archived; computation is qualified
by Rust macro_golden. This decoder only restores JSON prefixes to host tuples.
"""
from functools import lru_cache
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / 'native/pure_core/tests/fixtures'

class MacroError(ValueError): pass
class MacroPlanError(ValueError): pass
class NonNestableCommandError(MacroPlanError): pass


def rows():
    with (FIXTURES / 'macro_host.txt').open() as source:
        for line in source:
            yield line.rstrip('\n').split('|')


@lru_cache(maxsize=1)
def _answers():
    return {(r[0], r[1], r[2]): r[3:] for r in rows()}


def expected(operation, value, lossless=False):
    key = 'S' + value.encode('utf-8', 'surrogatepass').hex() if isinstance(value, str) else 'N' + type(value).__name__
    status, payload, error_type, error, name = _answers()[operation, str(int(bool(lossless))), key]
    if error_type:
        raise {'MacroError': MacroError, 'MacroPlanError': MacroPlanError,
               'NonNestableCommandError': NonNestableCommandError}[error_type](bytes.fromhex(error).decode('utf-8'))
    result = json.loads(bytes.fromhex(payload))
    for step in result:
        if 'prefixes' in step:
            step['prefixes'] = tuple(step['prefixes'])
    return result


def tokenize(value, *, lossless=False):
    return expected('T', value, lossless)

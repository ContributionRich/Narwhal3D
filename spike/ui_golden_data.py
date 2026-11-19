"""Host-only loader for immutable UI acceptance JSON; never execute fixture code."""
import hashlib
import json
from pathlib import Path

GOLDEN_PATH = Path(__file__).resolve().parents[1] / 'native/ui_core/tests/data/host_ui31.json'
GOLDEN_SHA256 = '32c845272f9ee20e012aeaadea9ec9bc8b6e21ca8ad2e9b16a9a4afbbf37faa1'


def load_data():
    source = GOLDEN_PATH.read_bytes()
    if hashlib.sha256(source).hexdigest() != GOLDEN_SHA256:
        raise ValueError('captured golden DATA SHA-256 mismatch')
    return json.loads(source)


DATA = load_data()

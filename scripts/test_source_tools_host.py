"""Unavoidable calls into retained Python bundle and actual document producers.

All source selection/audit/doc contracts live in Rust. This transport preserves
qualification of existing producers until those producers migrate too.
"""
from pathlib import Path
import subprocess
import sys
import build_ui_bundle as bundle
import source_tools

root = Path(__file__).resolve().parents[1]
preview = {'scripts/build_ui_bundle.py', 'scripts/ui_bundle_export.py',
           'scripts/ui_bundle_shots.py', 'scripts/ui_bundle/test_preview.cjs',
           'scripts/ui_bundle/index.html', 'docs/UI_DESIGNER_BUNDLE.md'}
preview.update('scripts/ui_bundle/' + name for name in bundle.PLAYGROUND_FILES)
preview.update(bundle.source_inventory())
preview.update(bundle.asset_inventory())
reply = source_tools._rpc('preview_check', paths=sorted(preview))
assert reply['ok'], reply
subprocess.run([sys.executable, str(root / 'spike/test_document_core_artifacts.py')],
               cwd=root, check=True, timeout=300)
print('Public preview source/asset closure and actual document producer: PASS')

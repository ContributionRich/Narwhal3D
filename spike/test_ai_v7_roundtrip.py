#!/usr/bin/env python3
"""Fresh-process geometry acceptance for a file saved by the visible review fixture."""
import json
import math
from pathlib import Path
import sys
import types
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
pkg=types.ModuleType('narwhal3d');pkg.__path__=[str(ROOT/'narwhal3d')];sys.modules['narwhal3d']=pkg
from narwhal3d import model,kernel
from narwhal3d.kernel import occt_backend as occt

path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
report=json.loads(path.read_text());expected=report['saved_geometry']
loaded=model.NarwhalModel.load(report['saved_model'])
actual={str(obj.Attributes.Id):obj.Geometry for obj in loaded.file.Objects}
assert set(actual)==set(expected),'saved UUID set changed'
for oid,geom in actual.items():
    master=kernel.master_of(geom)
    assert master is not None,'fresh process did not reconstruct analytic master'
    assert occt.is_airtight(master),'reopened solid is not airtight'
    assert occt.face_types(master)==expected[oid]['faces'],'analytic face classes changed'
    assert kernel.recipe_of(geom)['kind']==expected[oid]['kind'],'recipe kind changed'
    assert math.isclose(occt.volume(master),expected[oid]['volume'],rel_tol=1e-10),'reopened volume changed'
    bounds=occt.tight_bbox(master)
    assert all(abs(a-b)<1e-8 for row,want in zip(bounds,expected[oid]['bounds']) for a,b in zip(row,want)),'reopened bounds changed'
print('REVIEW06_FRESH_ROUNDTRIP_PASS',len(actual),'analytic solids; UUIDs/recipes/volume/bounds/airtightness')

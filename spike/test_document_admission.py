"""Exact admission receipts speed unchanged saves without trusting file-supplied claims.

Blender suite, real isolated worker. All geometry is synthetic and all targets temporary.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from narwhal3d import bridge, document_admission as admission, kernel  # noqa: E402
from narwhal3d import narwhal_document as document, native_document  # noqa: E402
from narwhal3d.kernel import occt_backend as occt  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402


def check(condition, label):
    assert condition, label
    print("PASS:", label, flush=True)


reports = []
original_run = document.converter_process.run


def observed_run(*args, **kwargs):
    result = original_run(*args, **kwargs)
    for line in result.stdout.splitlines():
        if line.startswith("NARWHAL-DOCUMENT-WORKER "):
            reports.append(json.loads(line.split(" ", 1)[1]))
    return result


def drilled(x):
    box = bridge.make_box((0, 0, 0), (10, 10, 0), 10)
    cylinder = bridge.make_cylinder((x, 5, -1), 1, 12)
    return bridge.boolean_brep(box, cylinder, "DIFFERENCE")


admission._receipts.clear()
first, changed = drilled(3), drilled(7)
master = kernel.master_of(first)
changed_master = kernel.master_of(changed)
check(master is not None and occt.is_airtight(master), "fixture has an exact airtight master")
model = NarwhalModel()
object_id, _attributes = model.add_object(first, "Drilled", 0)

with tempfile.TemporaryDirectory(prefix="narwhal-admission-test-") as folder, \
        patch.object(document.converter_process, "run", side_effect=observed_run):
    root = Path(folder)
    target = root / "safe.narwhal"
    document.save_model(model, target)
    check(reports[-1].get("reused") == 0, "the first save performs full exact admission")
    check(0 < len(admission._receipts) <= admission.MAX_RECEIPTS,
          "only completed worker admission issues bounded process-local receipts")
    model.add_object(r3.LineCurve(r3.Point3d(20, 0, 0), r3.Point3d(30, 0, 0)), "New curve", 0)
    document.save_model(model, target)
    check(reports[-1].get("reused") == 1,
          "editing another curve reuses the unchanged exact master/proxy pair")
    previous = target.read_bytes()
    original = next(o.Geometry.Duplicate() for o in model.file.Objects
                    if str(o.Attributes.Id) == object_id)
    for name, value in original.GetUserStrings():
        changed.SetUserString(name, value)
    kernel.register_master(changed, master)
    check(occt.tight_bbox(changed_master) == occt.tight_bbox(master),
          "adversarial proxy retains the exact master's bounds")
    check(abs(occt.volume(changed_master) - occt.volume(master)) < 1e-10,
          "adversarial proxy retains the exact master's volume")
    check(model.replace_geometry(object_id, changed), "attack preserves the document object UUID")
    try:
        document.save_model(model, target)
    except document.NarwhalDocumentError as error:
        check("proxy" in str(error), "changed proxy with the old master cannot reuse admission")
    else:
        raise AssertionError("same-bounds/volume proxy substitution was admitted")
    check(target.read_bytes() == previous, "refused save leaves the previous good archive untouched")
    check(model.replace_geometry(object_id, original), "restore the original geometry")
    document.save_model(model, target)
    check(reports[-1].get("reused") == 1, "a failed candidate cannot poison valid receipts")

    tolerance = model.file.Settings.ModelAbsoluteTolerance
    model.file.Settings.ModelAbsoluteTolerance = tolerance * 2
    document.save_model(model, target)
    check(reports[-1].get("reused") == 0, "document tolerance changes force full admission")
    model.file.Settings.ModelAbsoluteTolerance = tolerance
    document.save_model(model, target)
    check(reports[-1].get("reused") == 1, "the exact original admission inputs remain reusable")

    # Even in a warm process, imported files never receive the trusted Save hints.
    with patch.dict(os.environ, {admission.ENVIRONMENT: "/untrusted/file"}):
        candidate = document.load_model(target)
    check(candidate.object_count() == 2 and reports[-1].get("reused") == 0,
          "Open validates fully and clears any ambient admission-hint environment")
    payload = root / "unpacked"
    native_document.unpack(target, payload)
    check(not any(p.name == admission.OUTPUT or "receipt" in p.name for p in payload.rglob("*")),
          "trusted receipts are never packed into a project")

    original_command = document._worker_command
    script = root / "different-decoder.py"
    script.write_text("import sys\nsys.path.insert(0, " + repr(str(ROOT)) + ")\n"
                      "from narwhal3d import document_worker, document_admission\n"
                      "original = document_admission.contract\n"
                      "document_admission.contract = lambda: 'changed:' + original()\n"
                      "document_worker.main()\n")
    def changed_decoder(extracted):
        argv = list(original_command(extracted))
        argv[argv.index("--python") + 1] = str(script)
        return tuple(argv)
    with patch.object(document, "_worker_command", side_effect=changed_decoder):
        document.save_model(model, target)
    check(reports[-1].get("reused") == 0, "a changed decoder identity cannot reuse prior facts")

    # Exact distance constructors already compute their result: do not Perform twice.
    import OCP.BRepExtrema as extrema
    real_distance = extrema.BRepExtrema_DistShapeShape
    repeats = []
    class Distance:
        def __init__(self, *args):
            self.query = real_distance(*args)
        def IsDone(self):
            return self.query.IsDone()
        def Value(self):
            return self.query.Value()
        def Perform(self):
            repeats.append(True)
            return self.query.Perform()
    with patch.object(extrema, "BRepExtrema_DistShapeShape", Distance):
        bounds = occt._exact_bounds(occt.make_sphere(2), [-2] * 3, [2] * 3)
    check(not repeats and all(abs(abs(v) - 2) < 1e-8 for side in bounds for v in side),
          "exact bound queries retain their result without duplicate native Perform calls")

admission._receipts.clear()
for i in range(admission.MAX_RECEIPTS + 10):
    admission._receipts[f"{i:064x}"] = None
# The public insertion path enforces the bound, including after an overfull prior state.
with tempfile.TemporaryDirectory() as folder:
    output = Path(folder)
    admission.publish(output, ["f" * 64])
    admission.remember(output)
check(len(admission._receipts) == admission.MAX_RECEIPTS, "receipt retention remains bounded")
admission._receipts.clear()

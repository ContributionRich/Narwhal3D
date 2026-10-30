#!/usr/bin/env python3
"""Pure Document User Text model."""
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "narwhal3d", "document_user_text.py")
spec = importlib.util.spec_from_file_location("document_user_text", PATH)
dut = importlib.util.module_from_spec(spec)
sys.modules["document_user_text"] = dut
spec.loader.exec_module(dut)

checks = 0

def check(condition, label):
    global checks
    checks += 1
    if not condition:
        raise AssertionError(label)
    print("  ok  " + label)


table = dut.Table([
    dut.Row("Material", "Brass"),
    dut.Row(".secret", "literal {Textfield}", dut.TEXTFIELD),
    dut.Row("Block", "A", dut.BLOCK_ATTRIBUTE),
])
check([r.key for r in table.rows()] == ["Material", "Block"],
      "period keys are hidden from the normal document panel")
check(table.get(".secret").value == "literal {Textfield}"
      and table.get(".secret").type == dut.TEXTFIELD,
      "Textfield is retained as an opaque value and type")
check([r.key for r in table.search("BRASS")] == ["Material"],
      "search is case-insensitive across values")
check([r.key for r in table.search("secret", include_hidden=True)] == [".secret"],
      "hidden rows are searchable only when explicitly exposed")
check([r.key for r in table.sorted("type", include_hidden=True)] ==
      ["Block", "Material", ".secret"], "sort is deterministic by selected column")

# Ordered CRUD and explicit invalid data.
table.set("Material", "Steel")
table.set("Count", "5")
check([r.key for r in table.rows(include_hidden=True)][-1] == "Count"
      and table.get("Material").value == "Steel", "set updates in place and appends")
check(table.delete("Count") and not table.delete("Missing"), "delete distinguishes absent keys")
for bad in (("", "x", dut.PLAIN), ("Key", 4, dut.PLAIN), ("Key", "x", "Nope")):
    try:
        dut.Row(*bad)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid row was silently accepted")
checks += 1
print("  ok  invalid rows are rejected explicitly")

# Both interchange formats retain quoting, order, hidden rows, and types.
table.set("Comma", "a,b\nnext", dut.TEXTFIELD)
for fmt in ("csv", "txt"):
    encoded = table.export_text(fmt)
    restored = dut.Table.import_text(encoded, fmt)
    check(restored.rows(include_hidden=True) == table.rows(include_hidden=True),
          f"{fmt.upper()} round-trip is deterministic and lossless")
try:
    dut.Table.import_text("Key,Value,Type\nGood,yes,Plain text\nBroken\n")
except ValueError as exc:
    check("row 3" in str(exc), "invalid import rows name their line")
else:
    raise AssertionError("invalid import row was silently dropped")

with tempfile.NamedTemporaryFile(suffix=".3dm") as handle:
    try:
        dut.Table.import_file(handle.name)
    except dut.ThreeDMImportDependencyError as exc:
        check("3DM" in str(exc), ".3dm import refuses with a named dependency")
    else:
        raise AssertionError(".3dm import was silently accepted")

# Attribute -> document conversion and ordinary paste overwrite.
table.paste("Part:Bracket|new value")
check(table.get("Part:Bracket").value == "new value",
      "attribute clipboard converts to a document key/value")
table.paste("Part:Bracket|replacement")
check(table.get("Part:Bracket").value == "replacement", "ordinary paste updates its key")

# Documented paste-to-all-layouts never overwrites existing values.
first = dut.Table([dut.Row("Part:Bracket", "old")])
second = dut.Table([dut.Row("Other", "keep")])
changed = table.paste_to_all_layouts("Part:Bracket|incoming", [first, second])
check(changed == 1 and first.get("Part:Bracket").value == "old"
      and second.get("Part:Bracket").value == "incoming",
      "paste to all layouts does not overwrite existing rows")
try:
    table.paste("|bad")
except ValueError:
    checks += 1
    print("  ok  invalid clipboard rows are rejected explicitly")
else:
    raise AssertionError("invalid clipboard row was silently accepted")

check(dut.Table().export_on_document_export is False,
      "export-on-document-export defaults to false")
print(f"DOCUMENT USER TEXT PASSED ({checks} checks)")

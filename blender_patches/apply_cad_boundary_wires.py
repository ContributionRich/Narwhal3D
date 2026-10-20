"""Apply Narwhal 3D's CAD boundary-wire patch to Blender 5.2.2.

CAD solids are triangulated only for viewport shading. Their meaningful lines are the
split Brep boundaries; manifold triangle edges must never leak into Shaded or Wireframe.
The patch adds ``Mesh.narwhal_boundary_wires`` and makes the wire extractor reserve/hide
all manifold edges on flagged meshes. Actual mesh-command objects remain stock.

Run from the project root: ``python3 blender_patches/apply_cad_boundary_wires.py``.
Idempotent; every source anchor is asserted.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "tools", "blender-src", "source", "blender")

EDITS = [
    ("makesdna/DNA_mesh_types.h",
     "  ME_FLAG_UNUSED_6 = 1 << 6,     /* cleared */\n",
     "  /** CAD viewport mesh: draw split Brep boundaries, not the manifold\n"
     "   * triangulation used only to shade surfaces. */\n"
     "  ME_NARWHAL_BOUNDARY_WIRES = 1 << 6,\n"
     "  ME_FLAG_UNUSED_6 = ME_NARWHAL_BOUNDARY_WIRES, /* versioning compatibility */\n"),
    ("makesrna/intern/rna_mesh.cc",
     "  RNA_define_verify_sdna(false);\n"
     "  prop = RNA_def_property(srna, \"has_custom_normals\", PROP_BOOLEAN, PROP_NONE);\n",
     "  /* Narwhal 3D analytic CAD objects use a render mesh for fill only. */\n"
     "  prop = RNA_def_property(srna, \"narwhal_boundary_wires\", PROP_BOOLEAN, PROP_NONE);\n"
     "  RNA_def_property_boolean_sdna(prop, nullptr, \"flag\", ME_NARWHAL_BOUNDARY_WIRES);\n"
     "  RNA_def_property_clear_flag(prop, PROP_ANIMATABLE);\n"
     "  RNA_def_property_ui_text(\n"
     "      prop,\n"
     "      \"Boundary Wires Only (Narwhal 3D)\",\n"
     "      \"Draw split CAD boundaries but hide manifold viewport triangulation\");\n"
     "  RNA_def_property_update(prop, 0, \"rna_Mesh_update_draw\");\n\n"
     "  RNA_define_verify_sdna(false);\n"
     "  prop = RNA_def_property(srna, \"has_custom_normals\", PROP_BOOLEAN, PROP_NONE);\n"),
    ("draw/intern/mesh_extractors/extract_mesh_vbo_edge_fac.cc",
     "  const BitSpan optimal_display_edges = mr.mesh->runtime->subsurf_optimal_display_edges;\n\n"
     "  Array<int8_t> edge_face_count(mr.edges_num, 0);\n",
     "  const BitSpan optimal_display_edges = mr.mesh->runtime->subsurf_optimal_display_edges;\n"
     "  /* The mesh shades analytic CAD surfaces. Only non-manifold or split Brep\n"
     "   * boundaries are meaningful wires; manifold edges are internal tessellation. */\n"
     "  const bool boundary_wires_only =\n"
     "      (mr.mesh->flag & ME_NARWHAL_BOUNDARY_WIRES) != 0;\n\n"
     "  Array<int8_t> edge_face_count(mr.edges_num, 0);\n"),
    ("draw/intern/mesh_extractors/extract_mesh_vbo_edge_fac.cc",
     "          const float factor = edge_factor_calc(face_normals[other_face], face_normals[face]);\n"
     "          vbo_data[medata->corner_a] = factor;\n",
     "          const float factor = boundary_wires_only ?\n"
     "                                   1.0f :\n"
     "                                   edge_factor_calc(face_normals[other_face],\n"
     "                                                    face_normals[face]);\n"
     "          vbo_data[medata->corner_a] = factor;\n"),
]


def main():
    for rel, old, new in EDITS:
        path = os.path.join(SRC, rel)
        text = open(path, encoding="utf-8").read()
        if new in text:
            print(f"already patched: {rel}")
            continue
        if text.count(old) != 1:
            sys.exit(f"PATCH FAILED: {rel}: anchor found {text.count(old)} times")
        open(path, "w", encoding="utf-8").write(text.replace(old, new))
        print(f"patched: {rel}")


if __name__ == "__main__":
    main()

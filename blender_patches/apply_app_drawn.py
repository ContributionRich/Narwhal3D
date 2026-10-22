"""Apply Narwhal 3D's app-drawn collection patch to Blender 5.2.2.

The application draws CAD curves itself while Blender retains selectable objects. The
patch adds `Collection.narwhal_app_drawn` (`COLLECTION_APP_DRAWN`): flagged objects keep
their view-layer base for selection, active-object, and hide state, while `BASE_APP_DRAWN`
keeps them out of dependency-graph evaluation. Ordinary scenes never set the flag.

Run from the project root: `python3 blender_patches/apply_app_drawn.py`.
The patch is idempotent and asserts every source anchor.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "tools", "blender-src", "source", "blender")
MARK = "narwhal_app_drawn"

EDITS = [
    ("makesdna/DNA_collection_types.h",
     "  COLLECTION_HAS_OBJECT_CACHE_INSTANCED = (1 << 6),\n};",
     "  COLLECTION_HAS_OBJECT_CACHE_INSTANCED = (1 << 6),\n"
     "  /** Objects drawn by Narwhal 3D retain selectable bases but stay out of the\n"
     "   * dependency graph. See BASE_APP_DRAWN. */\n"
     "  COLLECTION_APP_DRAWN = (1 << 7),\n};"),
    ("makesdna/DNA_layer_types.h",
     "  BASE_INDIRECT_ONLY = (1 << 11),\n};",
     "  BASE_INDIRECT_ONLY = (1 << 11),\n"
     "  /* COLLECTION_APP_DRAWN objects keep selection, active, and hide state without\n"
     "   * being pulled into the dependency graph. */\n"
     "  BASE_APP_DRAWN = (1 << 12),\n};"),
    ("blenkernel/intern/layer.cc",
     "                                                   BASE_ENABLED_RENDER | BASE_HOLDOUT |\n"
     "                                                   BASE_INDIRECT_ONLY);",
     "                                                   BASE_ENABLED_RENDER | BASE_HOLDOUT |\n"
     "                                                   BASE_INDIRECT_ONLY |\n"
     "                                                   BASE_APP_DRAWN /* Narwhal 3D */);"),
    ("blenkernel/intern/layer.cc",
     "    if ((collection_restrict & COLLECTION_HIDE_RENDER) == 0) {\n"
     "      base->flag_from_collection |= BASE_ENABLED_RENDER;\n    }\n",
     "    if ((collection_restrict & COLLECTION_HIDE_RENDER) == 0) {\n"
     "      base->flag_from_collection |= BASE_ENABLED_RENDER;\n    }\n"
     "    /* Application-drawn object; see BASE_APP_DRAWN. */\n"
     "    if (collection_restrict & COLLECTION_APP_DRAWN) {\n"
     "      base->flag_from_collection |= BASE_APP_DRAWN;\n    }\n"),
    ("depsgraph/intern/builder/deg_builder.cc",
     "bool DepsgraphBuilder::need_pull_base_into_graph(const Base *base)\n{\n",
     "bool DepsgraphBuilder::need_pull_base_into_graph(const Base *base)\n{\n"
     "  /* Narwhal 3D draws these objects itself, so Blender need not evaluate them. */\n"
     "  if (base->flag & BASE_APP_DRAWN) {\n    return false;\n  }\n"),
    ("makesrna/intern/rna_collection.cc",
     "  prop = RNA_def_property(srna, \"hide_viewport\", PROP_BOOLEAN, PROP_NONE);\n"
     "  RNA_def_property_boolean_sdna(prop, nullptr, \"flag\", COLLECTION_HIDE_VIEWPORT);\n",
     "  /* Narwhal 3D app-drawn collection flag; see COLLECTION_APP_DRAWN. */\n"
     "  prop = RNA_def_property(srna, \"narwhal_app_drawn\", PROP_BOOLEAN, PROP_NONE);\n"
     "  RNA_def_property_boolean_sdna(prop, nullptr, \"flag\", COLLECTION_APP_DRAWN);\n"
     "  RNA_def_property_clear_flag(prop, PROP_ANIMATABLE);\n"
     "  RNA_def_property_ui_text(prop,\n"
     "                           \"App Drawn (Narwhal 3D)\",\n"
     "                           \"Objects are drawn by the application and kept out of the \"\n"
     "                           \"dependency graph; they stay selectable\");\n"
     "  RNA_def_property_update(prop, NC_SCENE | ND_LAYER_CONTENT, \"rna_Collection_flag_update\");\n\n"
     "  prop = RNA_def_property(srna, \"hide_viewport\", PROP_BOOLEAN, PROP_NONE);\n"
     "  RNA_def_property_boolean_sdna(prop, nullptr, \"flag\", COLLECTION_HIDE_VIEWPORT);\n"),
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

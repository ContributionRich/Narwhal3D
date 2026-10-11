"""Pure LAYER model: a tree of layers with the rules that keep it safe.
**No `bpy`, no rhino3dm.**

Why this module exists
----------------------
``model.py`` has ``add_layer`` / ``layers()`` / ``set_layer_visible`` /
``set_layer_locked`` and nothing else -- a flat list of layers, no tree, no
current layer, and none of the safety rules Rhino enforces. This module keeps
the decidable part of the layer system in plain Python, independently of UI and
geometry dependencies.

What it models
--------------
A TREE of :class:`Layer` nodes (Rhino layers NEST -- sub-layers are real, see
below), each with a ``name``, ``color``, ``visible`` and ``locked`` flag, plus
a single CURRENT layer. Operations: ``add``, ``add_child``, ``rename``,
``delete``, ``set_current``, ``set_visible``, ``set_locked``.

Where every fact below comes from
--------------------------------
* **Sub-layers are real** -- MEASURED from ``commands/layer.htm``: the context
  menu lists "New Sublayer", "Select Sublayer Objects", "Expand Sublayers" and
  "Collapse Sublayers", and the panel has an ``Expand All`` / ``Collapse All``
  pair. So a layer is a node with children, not a flat entry.
* **A layer's properties are name / color / visible / locked** -- MEASURED from
  the same page's "Column options": ``Name  Current  On  Lock  Color`` (plus
  Material / Linetype / Print Color / Print Width, which are attributes we do
  not model here). "On" is the visible flag, "Lock" the locked flag.
* **The actions we implement are all documented** -- MEASURED from the same
  page's context menu: "New Layer", "New Sublayer", "Rename", "Delete".

Implementation rules not stated by the help
--------------------------------------------
* **cannot delete the current layer without choosing another** -- this prevents
  the document from having no valid current layer.
* **a name must be unique among its siblings** -- this matches standard Rhino
  behaviour but is not stated on the page read above.
* **hiding a parent hides its children** -- DERIVED. Sub-layers are real
  (MEASURED) and a parent's "On" flag gates what is drawn, so a child is
  *effectively* hidden when any ancestor is off; the child's own "On" flag is
  left untouched (the panel's checkbox does not change when you switch a
  parent off). The inheritance rule itself is not stated verbatim in the help.
* **deleting a layer removes its sub-layers** -- DERIVED. Sub-layers belong to
  their parent's entry in the panel, so deleting the parent deletes the subtree
  (standard tree semantics).
* **deleting a layer that still holds objects is refused** -- this avoids
  orphaning object-layer assignments. The help does not state the rule, and the
  model does not reach into the document to count objects: the caller passes
  the count to :meth:`LayerTree.delete` (see below).

Anything that needs the document (which objects are on a layer) is NOT this
module's job: :meth:`LayerTree.delete` takes the object count as an argument
rather than reading it from ``model.py``.
"""
from __future__ import annotations

#: A layer's default colour. White -- the same default ``model.add_layer``
#: uses, so a layer created here matches one created there.
DEFAULT_COLOR = (255, 255, 255)


class Layer:
    """One node in the layer tree.

    ``visible`` and ``locked`` are the layer's OWN flags (the panel's "On" and
    "Lock" checkboxes). A child's flags do not change when its parent's do --
    :meth:`LayerTree.effective_visible` computes the drawn state from the whole
    chain.
    """

    __slots__ = ("name", "color", "visible", "locked", "children", "parent")

    def __init__(self, name, color=DEFAULT_COLOR, visible=True, locked=False):
        self.name = name
        self.color = tuple(color)
        self.visible = bool(visible)
        self.locked = bool(locked)
        self.children = []          # list[Layer] -- its sub-layers, in order
        self.parent = None          # Layer or None (None = a top-level layer)

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"Layer({self.name!r}, color={self.color}, "
                f"visible={self.visible}, locked={self.locked}, "
                f"children={len(self.children)})")


class LayerTree:
    """The document's layers: a tree of :class:`Layer` nodes + a CURRENT one.

    Top-level layers are stored in insertion order in ``self._top``; a
    sub-layer is a ``children`` entry of its parent. ``current`` is the
    current layer (``None`` until :meth:`set_current` is called).
    """

    def __init__(self):
        self._top = []              # ordered top-level layers
        self._known = set()         # every layer node, for membership checks
        self.current = None         # Layer or None

    # ------------------------------------------------------------------ tree

    def _register(self, layer):
        self._known.add(layer)

    def _unregister_subtree(self, layer):
        stack = [layer]
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            self._known.discard(node)

    def _require(self, layer):
        """`layer` must be a real :class:`Layer` that belongs to THIS tree.

        A layer from a different tree is rejected too (``_known`` is per-tree),
        so a caller cannot smuggle in a dangling reference.
        """
        if not isinstance(layer, Layer) or layer not in self._known:
            raise ValueError("that is not a layer in this tree")

    # ----------------------------------------------------------- operations

    def add(self, name, color=DEFAULT_COLOR, visible=True, locked=False):
        """Create a new TOP-LEVEL layer. The name must be unique among the
        other top-level layers. Returns the new :class:`Layer`."""
        layer = Layer(name, color, visible, locked)
        if any(l.name == name for l in self._top):
            raise ValueError(f"a top-level layer named {name!r} already exists")
        self._top.append(layer)
        self._register(layer)
        return layer

    def add_child(self, parent, name, color=DEFAULT_COLOR, visible=True,
                  locked=False):
        """Create a new SUB-LAYER under `parent`. The name must be unique
        among `parent`'s other children. Returns the new :class:`Layer`."""
        self._require(parent)
        layer = Layer(name, color, visible, locked)
        if any(c.name == name for c in parent.children):
            raise ValueError(
                f"{parent.name!r} already has a sub-layer named {name!r}")
        layer.parent = parent
        parent.children.append(layer)
        self._register(layer)
        return layer

    def rename(self, layer, new_name):
        """Rename `layer`. The new name must not collide with a SIBLING's name.
        (Renaming to the layer's own current name is a no-op.)"""
        self._require(layer)
        siblings = (self._top if layer.parent is None else layer.parent.children)
        if any(s is not layer and s.name == new_name for s in siblings):
            raise ValueError(
                f"a sibling named {new_name!r} already exists")
        layer.name = new_name

    def delete(self, layer, object_count=None):
        """Delete `layer` and its sub-layers.

        Refuses, in this order:
          * if `layer` is the current layer -- you must choose another first;
          * if `object_count` is given and is greater than zero -- the layer
            still holds objects.

        `object_count` is the number of objects the *document* reports on this
        layer (sub-layers included). This module never reaches into
        ``model.py`` for it -- the caller supplies it. If it is ``None`` the
        caller has not told us, and the delete proceeds.
        """
        self._require(layer)
        if layer is self.current:
            raise ValueError(
                "cannot delete the current layer without choosing another first")
        if object_count is not None and object_count > 0:
            raise ValueError(
                f"the layer still holds {object_count} object(s); move them "
                "or delete the objects first (refusal is OURS -- the help does "
                "not state the rule, and this module cannot count objects)")
        parent = layer.parent
        if parent is not None:
            parent.children.remove(layer)
        else:
            self._top.remove(layer)
        # Deleting `layer` deletes its subtree (DERIVED). If the CURRENT layer
        # is a strict descendant of `layer`, it is being deleted too -- clear
        # `current` rather than leave it pointing at a layer that is no longer
        # in the tree. (Deleting `layer` ITSELF when it is current is refused
        #  above; this is the separate "current is a sub-layer of what we are
        #  deleting" case; clearing the reference avoids retaining a layer that is
        #  no longer in the tree.)
        if self.current is not None and self._in_subtree(layer, self.current):
            self.current = None
        self._unregister_subtree(layer)

    @classmethod
    def _in_subtree(cls, root, node):
        stack = [root]
        while stack:
            n = stack.pop()
            if n is node:
                return True
            stack.extend(n.children)
        return False

    def set_current(self, layer):
        """Make `layer` the current layer."""
        self._require(layer)
        self.current = layer

    def set_visible(self, layer, visible):
        """Set `layer`'s OWN "On" flag. Children are left as-is; their drawn
        state follows via :meth:`effective_visible` (hiding a parent hides its
        children without changing their checkboxes)."""
        self._require(layer)
        layer.visible = bool(visible)

    def set_locked(self, layer, locked):
        """Set `layer`'s "Lock" flag."""
        self._require(layer)
        layer.locked = bool(locked)

    # --------------------------------------------------------------- queries

    def is_current(self, layer):
        return layer is self.current

    def siblings(self, layer):
        """The layer's siblings (same parent), including itself, in order."""
        self._require(layer)
        return self._top if layer.parent is None else list(layer.parent.children)

    def iter_all(self):
        """Every layer in the tree, depth-first (parents before their children)."""
        stack = list(reversed(self._top))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def effective_visible(self, layer):
        """Whether `layer` is drawn: its own "On" flag AND every ancestor's.

        This is the "hiding a parent hides its children" rule (DERIVED) -- a
        child whose parent is off is effectively hidden even though its own
        flag is still True.
        """
        self._require(layer)
        node = layer
        while node is not None:
            if not node.visible:
                return False
            node = node.parent
        return True

    def find(self, name):
        """The first layer named `name` (depth-first), or ``None``."""
        for layer in self.iter_all():
            if layer.name == name:
                return layer
        return None

    def count(self):
        """The total number of layers (sub-layers included)."""
        return len(self._known)


# ---------------------------------------------------------------------------
# provenance of the safety rules
# ---------------------------------------------------------------------------

def rules():
    """Return each safety rule with its provenance classification.

    ``MEASURED`` means stated by Rhino help, ``DERIVED`` follows from documented
    behavior and standard semantics, and ``OURS`` marks an application choice.
    """
    return {
        "sub-layers_are_real":
            "MEASURED (commands/layer.htm: New Sublayer, Select Sublayer "
            "Objects, Expand/Collapse Sublayers)",
        "layer_properties":
            "MEASURED (commands/layer.htm Column options: Name, Current, On, "
            "Lock, Color)",
        "actions_documented":
            "MEASURED (commands/layer.htm context menu: New Layer, New "
            "Sublayer, Rename, Delete)",
        "cannot_delete_current_layer":
            "OURS (task-required; the help does not state it)",
        "name_unique_among_siblings":
            "OURS (task-required; standard Rhino, not stated on the page read)",
        "hiding_parent_hides_children":
            "DERIVED (sub-layers are real + parent 'On' gates drawing; the "
            "inheritance rule is not stated verbatim)",
        "delete_removes_sublayers":
            "DERIVED (sub-layers belong to the parent's panel entry)",
        "refuses_to_delete_layer_with_objects":
            "OURS (task-flagged as the obvious one; the model cannot count "
            "objects, so the count is passed in)",
    }

"""Pure Named View model: the named-viewport list.
**No ``bpy``, no rhino3dm.**

Follows the structure of ``named_cplanes.py``: an ordered
name -> value map with save / get / rename / delete / names / sorted_names,
plus JSON round-trip.

Where every fact below comes from (``commands/namedview.htm``, MEASURED)
---------------------------------------------------------------------------
* **Save As** -- "Saves the current viewport as a named view."
  This is our :meth:`NamedViews.save`.
* **Restore** -- "Applies the selected named view to the current viewport.
  The same action can be performed by double-clicking on the name view."
  This is our :meth:`NamedViews.get` (the *apply-to-viewport* half is the
  command's job; the model returns the stored dict).
* **Delete** -- "Removes the selected named view from the list."
  :meth:`NamedViews.delete`.
* **Rename** -- "Changes the name of the selected named view."
  :meth:`NamedViews.rename`.
* **Sort By / Name Ascending / Name Descending** -- "Sorts the list from
  A to Z" / "Z to A". :meth:`NamedViews.sorted_names`.
* **Custom Order** -- "Allows to sort the list using drag and drop, or the
  Move up / Move down buttons." Insertion order *is* the custom order, which
  is what :meth:`NamedViews.names` returns.

The stored value is a plain dict
--------------------------------
``{"location": (x,y,z), "target": (x,y,z), "up": (x,y,z),
  "projection": "Parallel"|"Perspective", "lens_mm": float, "extent": float}``

This is the same shape as the ``ViewportProperties`` fields referenced in the
help page ("For more information about these settings, see: ViewportProperties").

Implementation rules not stated by the help
---------------------------------------------------------------------------
* **a name is unique case-insensitively** -- an application rule not stated by
  the help. Consequence: :meth:`NamedViews.save` refuses to
  overwrite an existing name (case-insensitively), and
  :meth:`NamedViews.rename` refuses to collide with a different entry's name.
* **restoring a name that is gone is reported, not silent** -- an application choice.
  :meth:`NamedViews.get` returns ``None`` rather than raising, so the caller
  can report it. Never invent a view.
* **a name must not be blank** -- OURS. A named entry needs a name.
* **the view dict must be well-formed** -- OURS. The model validates the six
  fields and refuses a malformed dict (never a silent partial save).

Anything that needs the *active viewport* (reading/saving the view, applying a
restored view) is NOT this module's job: the command supplies the view dict to
:meth:`NamedViews.save` and takes the returned dict from :meth:`NamedViews.get`
back to the viewport.
"""
from __future__ import annotations

PROJECTIONS = ("Parallel", "Perspective")
_VIEW_KEYS = ("location", "target", "up", "projection", "lens_mm", "extent")


def _check_vec3(label, v):
    if not isinstance(v, (tuple, list)) or len(v) != 3:
        raise ValueError(
            f"view['{label}'] must be a 3-vector, got {type(v).__name__}")
    for i, c in enumerate(v):
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            raise ValueError(
                f"view['{label}'][{i}] must be a number, got {type(c).__name__}")
    return (float(v[0]), float(v[1]), float(v[2]))


def check_view(view) -> dict:
    """Validate and normalise a view dict. Returns a fresh dict.

    Raises ``ValueError`` on any malformed field.
    """
    if not isinstance(view, dict):
        raise ValueError(f"a view must be a dict, got {type(view).__name__}")
    for k in _VIEW_KEYS:
        if k not in view:
            raise ValueError(f"view is missing required key {k!r}")
    out = {
        "location": _check_vec3("location", view["location"]),
        "target":   _check_vec3("target",   view["target"]),
        "up":       _check_vec3("up",       view["up"]),
    }
    proj = view["projection"]
    if proj not in PROJECTIONS:
        raise ValueError(
            f"view['projection'] must be one of {PROJECTIONS}, got {proj!r}")
    out["projection"] = proj
    for key in ("lens_mm", "extent"):
        val = view[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(f"view['{key}'] must be a number, got {type(val).__name__}")
        if val <= 0:
            raise ValueError(f"view['{key}'] must be > 0, got {val}")
        out[key] = float(val)
    return out


class NamedViews:
    """The document's named views: an ordered name -> view-dict map.

    Names are unique **case-insensitively** (OURS). Entries are stored in
    insertion order, which is the reference CAD application's **Custom Order** (the panel's default).
    ``names()`` returns them in the order they were saved/renamed in, and
    ``sorted_names()`` applies the documented Name Ascending / Descending sorts.

    The stored value is a plain dict (see :func:`check_view` for the required
    shape). :meth:`get` returns the stored dict (a copy), so a restored view
    is independent of later mutations to the stored one.
    """

    __slots__ = ("_order", "_views")

    def __init__(self):
        self._order = []          # display names, in Custom (insertion) order
        self._views = {}          # lowercase name -> (display_name, view_dict)

    # ------------------------------------------------------------- lookups

    def __contains__(self, name):
        return self._key(name) in self._views

    def __len__(self):
        return len(self._views)

    def count(self):
        """The number of named views."""
        return len(self._views)

    def names(self):
        """The names in Custom (insertion) order."""
        return list(self._order)

    def has(self, name):
        return self._key(name) in self._views

    def get(self, name):
        """The view dict stored under ``name``, or ``None`` if no such name.

        Returns a **copy** of the stored dict so the caller can mutate it
        without affecting the stored view. Returning ``None`` (rather than
        raising) lets the caller report a missing name without inventing a view.
        """
        key = self._key(name)
        if key not in self._views:
            return None
        return dict(self._views[key][1])

    # ------------------------------------------------------------ operations

    def save(self, name, view):
        """Record ``view`` under ``name``.

        Refuses (``ValueError``) when:
          * ``name`` is blank (OURS -- a named entry needs a name);
          * ``view`` is not a well-formed view dict (see :func:`check_view`);
          * ``name`` is already taken case-insensitively (OURS -- the help
            does not state the rule; we refuse to silently overwrite).
        Returns a copy of the stored view dict.
        """
        self._check_name(name)
        stored = check_view(view)
        key = self._key(name)
        if key in self._views:
            raise ValueError(
                f"a named view called {name!r} already exists; delete it "
                "first or save under a different name (refusal is OURS -- the "
                "help does not state the rule)")
        self._views[key] = (name, stored)
        self._order.append(name)
        return dict(stored)

    def rename(self, old, new):
        """Rename the view called ``old`` to ``new``.

        Refuses (``ValueError``) when ``old`` is not a known name, when
        ``new`` is blank, or when ``new`` already names a DIFFERENT entry
        (case-insensitively, OURS -- the uniqueness rule). Renaming to the
        same name (case-insensitively) is a no-op. Returns the new display
        name.
        """
        old_key = self._key(old)
        if old_key not in self._views:
            raise ValueError(f"there is no named view called {old!r}")
        self._check_name(new)
        new_key = self._key(new)
        if new_key in self._views and new_key != old_key:
            raise ValueError(
                f"a named view called {new!r} already exists (uniqueness is "
                "OURS -- the help does not state the rule)")
        if new_key != old_key:
            display, view_dict = self._views.pop(old_key)
            self._order.remove(display)
            self._views[new_key] = (new, view_dict)
            self._order.append(new)   # rename keeps the entry, re-files it last
            return new
        # same name (possibly different case) -- update the display name
        _, view_dict = self._views[old_key]
        self._views[old_key] = (new, view_dict)
        # update the order list to reflect the new display name
        for i, n in enumerate(self._order):
            if n.lower() == old_key:
                self._order[i] = new
                break
        return new

    def delete(self, name):
        """Delete the view called ``name``. Refuses if ``name`` is not known.

        Returns ``True`` on success.
        """
        key = self._key(name)
        if key not in self._views:
            raise ValueError(f"there is no named view called {name!r}")
        display, _ = self._views.pop(key)
        self._order.remove(display)
        return True

    # --------------------------------------------------------------- sorting

    def sorted_names(self, ascending=True):
        """The names sorted by name (the reference CAD application's "Name Ascending" / "Name
        Descending"). The order returned by ``names()`` (Custom Order) is
        otherwise untouched."""
        return sorted(self._order, key=str.lower, reverse=not ascending)

    # ------------------------------------------------------------------ rules

    @staticmethod
    def _check_name(name):
        if name is None or not str(name).strip():
            raise ValueError("a named view needs a non-blank name")

    @staticmethod
    def _key(name):
        return str(name).strip().lower()

    # ------------------------------------------------------------- round-trip

    def to_dict(self):
        """A JSON-serialisable dict of the whole list."""
        return {
            "order": list(self._order),
            "views": {n: self._views[self._key(n)][1] for n in self._order},
        }

    @classmethod
    def from_dict(cls, d):
        """Rebuild a :class:`NamedViews` from the output of :meth:`to_dict`."""
        nv = cls()
        for name in d.get("order", ()):
            view = d["views"][name]
            nv._views[nv._key(name)] = (name, check_view(view))
            nv._order.append(name)
        return nv

    @staticmethod
    def rules():
        """Return each rule with its provenance classification."""
        return {
            "save_as_is_documented":
                "MEASURED (namedview.htm: Save as / Save Current Viewport As "
                "Named View)",
            "restore_is_documented":
                "MEASURED (namedview.htm: Restore / To current viewport / "
                "double-click a view name)",
            "delete_is_documented":
                "MEASURED (namedview.htm: Delete / Delete Named View)",
            "rename_is_documented":
                "MEASURED (namedview.htm: Rename Named View)",
            "sort_by_name_is_documented":
                "MEASURED (namedview.htm: Sort By Name Ascending/Descending)",
            "custom_order_is_insertion_order":
                "MEASURED (namedview.htm: Custom Order via drag and drop / "
                "Move up / Move down)",
            "copy_paste_clipboard_not_modelled":
                "MEASURED (namedview.htm: Copy / Paste -- one the reference CAD application session or "
                "between sessions) -- not a model concern; the command "
                "reports it not implemented",
            "import_3dm_not_modelled":
                "MEASURED (namedview.htm: Import from 3dm file) -- a document "
                "concern; the command reports it not implemented",
            "animation_not_modelled":
                "MEASURED (namedview.htm: Animation / Animate named view "
                "restoration) -- a UI concern; not modelled",
            "name_is_unique_case_insensitive":
                "OURS (the task requires case-insensitive uniqueness; the help "
                "does not state it)",
            "save_refuses_to_overwrite":
                "OURS (consequence of uniqueness; the help is silent)",
            "restore_gone_name_is_reported":
                "OURS (the task asks what happens to a gone name; the help is "
                "silent -- we report it, never invent a view)",
            "name_must_not_be_blank":
                "OURS (a named entry needs a name)",
            "view_dict_must_be_well_formed":
                "OURS (the model validates the six fields; the help references "
                "ViewportProperties but does not specify validation)",
        }

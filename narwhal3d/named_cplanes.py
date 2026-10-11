"""Pure Named CPlane model: the named-construction-plane list.
**No `bpy`, no rhino3dm.**

Why this module exists
----------------------
``cplane.py`` holds the CPlane *value* (an orthonormal frame) and the six named
world planes; ``commands.py`` has a working `CPlane` command that can change the
active viewport's plane. What is missing is the *named* list: saving the active
plane under a name and restoring it later -- Rhino's ``NamedCPlane`` (a panel,
per its help page). This module holds that list as plain Python and stores
:class:`cplane.CPlane` objects directly rather than introducing a second frame
representation.

The stored value is a ``CPlane`` frame
-------------------------------------
``cplane.py`` is pure (no ``bpy``) and its only import is
``from __future__ import annotations``, so it is safe to import here and to use
its :class:`CPlane` type. The six world frames it defines are a ready-made set
of distinct construction planes.

Where every fact below comes from (commands/namedcplane.htm, MEASURED)
-----------------------------------------------------------------------
* **Save As** -- "Saves the construction plane of the active viewport as a
  Named CPlane." (also "Save Current CPlane As Named CPlane..." in the context
  menu). This is our :meth:`NamedCPlanes.save`.
* **Restore** -- "Restores a Named CPlane to the active viewport. Or,
  double-click a Named CPlane." This is our :meth:`NamedCPlanes.get` /
  :meth:`NamedCPlanes.restore`; the *apply-to-viewport* half is the command's
  job (it owns ``ctx.set_active_cplane``).
* **Delete** -- "Deletes the selected Named CPlanes." :meth:`NamedCPlanes.delete`.
* **Rename** -- "Edits the name of the selected Named CPlane."
  :meth:`NamedCPlanes.rename`.
* **the list** -- the panel is a list of named planes, so the model must report
  them in order: :meth:`NamedCPlanes.names`.
* **Sort By / Name Ascending / Name Descending** -- "Sorts the list from A to Z"
  / "Z to A". :meth:`NamedCPlanes.sorted_names`.
* **Custom Order** -- "Allows to sort the list using drag and drop, or the Move
  up / Move down buttons." Insertion order *is* the custom order, which is what
  :meth:`NamedCPlanes.names` returns (the same split as ``layers.py`` keeping
  insertion order in ``iter_all``).

Actions this pure model does not provide
---------------------------------------------------------------------
* **Copy / Paste** -- the help says "Copies the selected Named CPlane into
  Windows Clipboard" / "Pastes the Named CPlane stored in Windows Clipboard".
  That is an OS-clipboard concern, not a model concern; the command reports it
  as not implemented rather than touching the clipboard.
* **Import** -- "Imports Named CPlanes from a .3dm file." Reading a ``.3dm`` is
  a document/``rhino3dm`` concern, outside this pure model; the command
  reports it as not implemented.
* **Move up / Move down** -- "Moves the selected item up/down in the list",
  available only under Custom Order. A panel-drag affordance; not modelled here
  (the command reports it as not implemented). This model provides save,
  restore, rename, delete, and list operations.

Implementation rules not stated by the help
---------------------------------------------------------------------------
* **a name is unique in the list** -- A *named* collection is keyed by
  name (the same shape as ``layers.py``'s "a name must be unique among its
  siblings"), and restoring by name is only well-defined if names are unique.
  The help does not state the rule. Consequence: :meth:`NamedCPlanes.save`
  REFUSES to silently overwrite an existing name (re-saving under a taken name
  is refused, not a quiet replacement), and :meth:`NamedCPlanes.rename` refuses
  to collide with a different plane's name.
* **restoring a name that is gone is reported, not silent** -- The help
  does not say what happens if you restore a name that no longer exists. The
  safe behaviour is to say so plainly (the command reports it; the model
  :meth:`returns <NamedCPlanes.get>` ``None`` rather than raising, so the
  caller can report). Never invent a plane and never pretend it was restored.
* **a name must not be blank** -- A named entry needs a name; a blank one
  is refused. (The help's "Naming conventions in Rhino" page forbids control
  characters and names beginning with ``( ) [ ] { }``; we check the blank case
  here and leave the rest to the caller, matching how ``layers.py`` does not
  police every character.)

Anything that needs the *active viewport* (reading/saving the plane, applying a
restored plane) is NOT this module's job: the command supplies the
``CPlane`` to :meth:`NamedCPlanes.save` and takes the returned one from
:meth:`NamedCPlanes.get` back to the viewport.
"""
from __future__ import annotations

from . import cplane


class NamedCPlanes:
    """The document's named construction planes: an ordered name -> CPlane map.

    Entries are stored in insertion order, which is Rhino's **Custom Order**
    (the panel's default). ``names()`` therefore returns them in the order they
    were saved/renamed in, and ``sorted_names()`` applies the documented
    Name Ascending / Descending sorts.

    The stored value is a :class:`cplane.CPlane` (the task is explicit: keep
    ``CPlane`` as the stored value, do not invent a second plane
    representation). :meth:`get` returns the stored plane itself (an identity
    match), so a restored plane compares equal to the one that was saved.
    """

    __slots__ = ("_order", "_planes")

    def __init__(self):
        self._order = []          # display names, in Custom (insertion) order
        self._planes = {}         # display name -> cplane.CPlane

    # ------------------------------------------------------------- lookups

    def __contains__(self, name):
        return name in self._planes

    def __len__(self):
        return len(self._planes)

    def count(self):
        """The number of named planes."""
        return len(self._planes)

    def names(self):
        """The names in Custom (insertion) order."""
        return list(self._order)

    def has(self, name):
        return name in self._planes

    def get(self, name):
        """The plane stored under `name`, or ``None`` if there is no such name.

        Returning ``None`` rather than raising lets the caller report a missing name
        without inventing a replacement plane.
        """
        return self._planes.get(name)

    # ------------------------------------------------------------ operations

    def save(self, name, plane):
        """Record `plane` under `name`.

        Refuses (``ValueError``) when:
          * `name` is blank (OURS -- a named entry needs a name);
          * `plane` is not a :class:`cplane.CPlane` (we keep ``CPlane`` as the
            stored value and do not accept a second representation);
          * `name` is already taken -- re-saving under a taken name is refused,
            not a quiet overwrite (OURS -- the help does not state the rule).
        Returns the stored :class:`cplane.CPlane` (the same object passed in).
        """
        self._check_name(name)
        if not isinstance(plane, cplane.CPlane):
            raise ValueError(
                f"the stored value must be a cplane.CPlane, got "
                f"{type(plane).__name__} (we keep CPlane as the stored value)")
        if name in self._planes:
            raise ValueError(
                f"a named CPlane called {name!r} already exists; delete it "
                "first or save under a different name (refusal is OURS -- the "
                "help does not state the rule)")
        self._planes[name] = plane
        self._order.append(name)
        return plane

    def rename(self, old, new):
        """Rename the plane called `old` to `new`.

        Refuses (``ValueError``) when `old` is not a known name, when `new` is
        blank, or when `new` already names a DIFFERENT plane (OURS -- the
        uniqueness rule). Renaming to the same name is a no-op. Returns the new
        display name.
        """
        if old not in self._planes:
            raise ValueError(f"there is no named CPlane called {old!r}")
        self._check_name(new)
        if new in self._planes and new != old:
            raise ValueError(
                f"a named CPlane called {new!r} already exists (uniqueness is "
                "OURS -- the help does not state the rule)")
        if new != old:
            plane = self._planes.pop(old)
            self._order.remove(old)
            self._planes[new] = plane
            self._order.append(new)   # rename keeps the entry, re-files it last
        return new

    def delete(self, name):
        """Delete the plane called `name`. Refuses if `name` is not known.

        Returns ``True`` on success. (There is no "current named CPlane" to
        protect the way ``layers.py`` protects the current layer -- the active
        viewport's plane is separate and is not deleted by deleting a name.)
        """
        if name not in self._planes:
            raise ValueError(f"there is no named CPlane called {name!r}")
        del self._planes[name]
        self._order.remove(name)
        return True

    # --------------------------------------------------------------- sorting

    def sorted_names(self, ascending=True):
        """The names sorted by name (Rhino's "Name Ascending" / "Name
        Descending"). The order returned by ``names()`` (Custom Order) is
        otherwise untouched."""
        return sorted(self._order, key=str.lower, reverse=not ascending)

    # ------------------------------------------------------------------ rules

    @staticmethod
    def _check_name(name):
        if name is None or not str(name).strip():
            raise ValueError("a named CPlane needs a non-blank name")

    @staticmethod
    def rules():
        """Return each rule with its provenance classification.

        ``MEASURED`` means stated by Rhino help; ``OURS`` marks an application
        choice where the help is silent.
        """
        return {
            "save_as_is_documented":
                "MEASURED (namedcplane.htm: Save As / Save Current CPlane As "
                "Named CPlane)",
            "restore_is_documented":
                "MEASURED (namedcplane.htm: Restore / double-click a Named CPlane)",
            "delete_is_documented":
                "MEASURED (namedcplane.htm: Delete)",
            "rename_is_documented":
                "MEASURED (namedcplane.htm: Rename Named CPlane)",
            "sort_by_name_is_documented":
                "MEASURED (namedcplane.htm: Sort By Name Ascending/Descending)",
            "custom_order_is_insertion_order":
                "MEASURED (namedcplane.htm: Custom Order via drag and drop / "
                "Move up / Move down)",
            "copy_paste_clipboard_not_modelled":
                "MEASURED (namedcplane.htm: Windows Clipboard) -- not a model "
                "concern; the command reports it not implemented",
            "import_3dm_not_modelled":
                "MEASURED (namedcplane.htm: Imports Named CPlanes from a .3dm "
                "file) -- a document concern; the command reports it not "
                "implemented",
            "name_is_unique":
                "OURS (the task requires the uniqueness rule; the help does not "
                "state it)",
            "save_refuses_to_overwrite":
                "OURS (consequence of uniqueness; the help is silent)",
            "restore_gone_name_is_reported":
                "OURS (the task asks what happens to a gone name; the help is "
                "silent -- we report it, never invent a plane)",
            "name_must_not_be_blank":
                "OURS (a named entry needs a name; the help's naming-conventions "
                "page is not re-implemented here)",
        }

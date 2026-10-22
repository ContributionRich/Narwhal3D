"""Pure Layer State Manager model.

the reference CAD application help (MEASURED, ``commands/layer.htm#LayerStateManager``) says that
Layer State Manager saves the current layer settings under a typed name.  The
public document layer-record shape used here is also MEASURED from
``NarwhalModel.layer_records``: every record has ``Id``, ``Name``, ``Color``,
``Visible``, ``Locked``, ``PlotColor`` and ``PlotWeight``.

This module deliberately stores records; it does not open or import a .3dm.
A caller owns applying a returned state to its document.  .3dm import is
explicitly deferred.

Restore policy for layers added or removed after a save is OURS (the help is
silent): records are matched by ``Id``; existing live layers receive the
saved settings, new live layers are retained in their current order, and
saved layers absent from the live document are ignored.  No layer is silently
created or deleted.
"""
from __future__ import annotations

import copy


LAYER_FIELDS = (
    "Id", "Name", "Color", "Visible", "Locked", "PlotColor", "PlotWeight",
)


class LayerStateManager:
    """Ordered named, deep-copied layer-state snapshots."""

    __slots__ = ("_order", "_states")

    def __init__(self):
        self._order = []
        self._states = {}

    def __len__(self):
        return len(self._order)

    def list(self):
        """Return saved state names in save order."""
        return list(self._order)

    def save(self, name, layer_records):
        """Save an ordered layer-record sequence under ``name``.

        Names are unique (OURS); overwriting requires deleting first.  Both the
        input sequence and each returned record are independent deep copies.
        """
        key = self._key(name)
        if not key:
            raise ValueError("a layer state needs a non-blank name")
        if key in self._states:
            raise ValueError(f"a layer state called {name!r} already exists")
        records = _copy_records(layer_records)
        self._states[key] = (str(name), records)
        self._order.append(str(name))
        return copy.deepcopy(records)

    def restore(self, name, layer_records=None):
        """Return a saved state, optionally merged into live records.

        With no live records, returns the complete saved sequence.  With live
        records, applies saved fields to matching ``Id`` values while retaining
        live-only records and order.  The result never aliases either input.
        """
        key = self._key(name)
        if key not in self._states:
            raise ValueError(f"there is no layer state called {name!r}")
        saved = self._states[key][1]
        if layer_records is None:
            return copy.deepcopy(saved)
        live = _copy_records(layer_records)
        saved_by_id = {record["Id"]: record for record in saved}
        return [
            _merged_record(record, saved_by_id.get(record["Id"]))
            for record in live
        ]

    def rename(self, old, new):
        """Rename a saved state, preserving insertion order."""
        old_key = self._key(old)
        if old_key not in self._states:
            raise ValueError(f"there is no layer state called {old!r}")
        new_name = str(new)
        new_key = self._key(new_name)
        if not new_key:
            raise ValueError("a layer state needs a non-blank name")
        if new_key != old_key and new_key in self._states:
            raise ValueError(f"a layer state called {new!r} already exists")
        display, records = self._states.pop(old_key)
        self._states[new_key] = (new_name, records)
        for index, name in enumerate(self._order):
            if self._key(name) == old_key:
                self._order[index] = new_name
                break
        return new_name

    def delete(self, name):
        """Delete a saved state; return ``True`` on success."""
        key = self._key(name)
        if key not in self._states:
            raise ValueError(f"there is no layer state called {name!r}")
        self._states.pop(key)
        self._order = [n for n in self._order if self._key(n) != key]
        return True

    def to_dict(self):
        """Return a deterministic, JSON-serialisable representation."""
        return {
            "order": list(self._order),
            "states": {
                name: copy.deepcopy(self._states[self._key(name)][1])
                for name in self._order
            },
        }

    @classmethod
    def from_dict(cls, data):
        """Build a manager from :meth:`to_dict` output."""
        if not isinstance(data, dict) or set(data) != {"order", "states"}:
            raise ValueError("layer states require exactly 'order' and 'states'")
        order = data["order"]
        states = data["states"]
        if not isinstance(order, list) or not isinstance(states, dict):
            raise ValueError("layer state order/states have the wrong shape")
        result = cls()
        if len(order) != len(set(order)):
            raise ValueError("layer state names must be unique")
        for name in order:
            if name not in states:
                raise ValueError(f"layer state {name!r} is missing its records")
            result.save(name, states[name])
        if set(states) != set(order):
            raise ValueError("states contains a name absent from order")
        return result

    @staticmethod
    def _key(name):
        return str(name).strip().casefold()


def _copy_records(records):
    if not isinstance(records, (list, tuple)):
        raise ValueError("layer records must be an ordered list")
    copied = copy.deepcopy(list(records))
    seen = set()
    for record in copied:
        if not isinstance(record, dict) or set(record) != set(LAYER_FIELDS):
            raise ValueError(
                "each layer record must contain exactly "
                + ", ".join(LAYER_FIELDS))
        if record["Id"] in seen:
            raise ValueError("layer record Id values must be unique")
        seen.add(record["Id"])
        # JSON has no tuple type; restore the measured public shape for colors.
        for field in ("Color", "PlotColor"):
            if isinstance(record[field], list):
                record[field] = tuple(record[field])
    return copied


def _merged_record(live, saved):
    if saved is None:
        return copy.deepcopy(live)
    merged = copy.deepcopy(live)
    for field in LAYER_FIELDS:
        merged[field] = copy.deepcopy(saved[field])
    return merged


def rules():
    """Return provenance for behavior that is not fully specified by help."""
    return {
        "save_current_layer_settings":
            "MEASURED (commands/layer.htm#LayerStateManager)",
        "record_fields":
            "MEASURED (NarwhalModel.layer_records: Id, Name, Color, Visible, "
            "Locked, PlotColor, PlotWeight)",
        "missing_and_new_layers":
            "OURS (help is silent: match Id; retain new; ignore missing)",
        "three_dm_import":
            "DEFERRED (not implemented by this pure model)",
    }

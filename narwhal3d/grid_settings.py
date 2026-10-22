"""Pure, immutable model for the reference CAD application Document Properties > Grid.

MEASURED from ``documentproperties/grid`` (the reference CAD application 8 help): the grid covers a
specified portion of an infinite construction plane; Grid line count is the
number of minor lines from the CPlane origin to an edge and has a maximum of
100,000; minor spacing, major interval, snap spacing, Show grid lines, Show
grid axes, Show world axis icon, and Apply to (active/all viewports) are the
available properties.

OURS: the Python representation uses lower-snake-case keys and the scope
values ``"active"`` and ``"all"``.  This module plans settings application only;
it does not draw a grid or mutate a document.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from numbers import Real
from typing import Any, Iterable, Mapping

ACTIVE = "active"
ALL = "all"
APPLY_TO = (ACTIVE, ALL)
MAX_GRID_LINE_COUNT = 100_000


@dataclass(frozen=True)
class GridSettings:
    """Validated settings for one construction-plane grid."""

    # OURS initial values: the help specifies fields and bounds, not this profile's defaults.
    snap_spacing: float = 1.0
    minor_spacing: float = 1.0
    major_interval: int = 10
    grid_line_count: int = 200
    show_grid: bool = True
    show_grid_axes: bool = True
    show_world_axes: bool = True
    apply_to: str = ACTIVE

    def __post_init__(self) -> None:
        for name in ("snap_spacing", "minor_spacing"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{name} must be a positive finite number")
            if not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")

        for name in ("major_interval", "grid_line_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be a positive integer")
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.grid_line_count > MAX_GRID_LINE_COUNT:
            raise ValueError(
                f"grid_line_count must be between 1 and {MAX_GRID_LINE_COUNT}"
            )

        for name in ("show_grid", "show_grid_axes", "show_world_axes"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exactly True or False")
        if type(self.apply_to) is not str or self.apply_to not in APPLY_TO:
            raise ValueError("apply_to must be exactly 'active' or 'all'")

    def copy(self, **changes: Any) -> "GridSettings":
        """Return a validated independent copy, optionally with replacements."""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh, deterministic plain-data representation."""
        return {
            "snap_spacing": self.snap_spacing,
            "minor_spacing": self.minor_spacing,
            "major_interval": self.major_interval,
            "grid_line_count": self.grid_line_count,
            "show_grid": self.show_grid,
            "show_grid_axes": self.show_grid_axes,
            "show_world_axes": self.show_world_axes,
            "apply_to": self.apply_to,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "GridSettings":
        """Build settings from a complete :meth:`to_dict` mapping."""
        if not isinstance(values, Mapping):
            raise ValueError("grid settings must be restored from a mapping")
        expected = set(cls.__dataclass_fields__)
        if set(values) != expected:
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            details = []
            if missing:
                details.append(f"missing {', '.join(missing)}")
            if extra:
                details.append(f"unknown {', '.join(extra)}")
            raise ValueError("invalid grid settings mapping: " + "; ".join(details))
        return cls(**{key: values[key] for key in cls.__dataclass_fields__})

    def application_plan(
        self,
        viewport_ids: Iterable[Any],
        *,
        active_viewport_id: Any | None = None,
    ) -> tuple[tuple[Any, "GridSettings"], ...]:
        """Return a deterministic ``(viewport_id, settings)`` application plan.

        Input order is preserved, duplicate IDs are rejected, and the returned
        settings are copied so a consumer cannot mutate this configuration by
        retaining a plan entry.  Active scope requires ``active_viewport_id``
        and applies only to that supplied viewport; all scope applies to every
        supplied viewport.
        """
        ids = tuple(viewport_ids)
        try:
            if len(set(ids)) != len(ids):
                raise ValueError("viewport_ids must contain unique IDs")
        except TypeError as exc:
            raise ValueError("viewport IDs must be hashable") from exc

        if self.apply_to == ALL:
            targets = ids
        else:
            if active_viewport_id is None:
                raise ValueError("active viewport ID is required for active scope")
            if active_viewport_id not in ids:
                raise ValueError("active viewport ID is not in viewport_ids")
            targets = (active_viewport_id,)
        return tuple((viewport_id, self.copy()) for viewport_id in targets)

    # A descriptive alias keeps callers from having to know the method's
    # implementation-oriented name.
    plan_for_viewports = application_plan


class GridSettingsStore:
    """Pure document/viewport store used by the ``Grid`` command.

    The store is deliberately separate from drawing.  A document owns one
    settings value per viewport; applying ``active`` or ``all`` changes those
    values only. ``apply`` records one in-memory snapshot for model-level undo;
    viewport redraw and UI application remain separate responsibilities.
    """

    def __init__(self, viewport_settings: Mapping[Any, GridSettings] | None = None,
                 *, active_viewport_id: Any | None = None):
        supplied = dict(viewport_settings or {})
        if not supplied:
            raise ValueError("at least one viewport is required")
        if len(set(supplied)) != len(supplied):
            raise ValueError("viewport IDs must be unique")
        if any(not isinstance(value, GridSettings) for value in supplied.values()):
            raise ValueError("viewport settings must be GridSettings values")
        if active_viewport_id is None:
            active_viewport_id = next(iter(supplied))
        if active_viewport_id not in supplied:
            raise ValueError("active viewport ID is not in viewport settings")
        self._settings = supplied
        self.active_viewport_id = active_viewport_id
        self._undo: list[dict[Any, GridSettings]] = []

    @property
    def viewport_ids(self) -> tuple[Any, ...]:
        return tuple(self._settings)

    def sync_viewports(self, viewport_ids: Iterable[Any], *, active_viewport_id: Any | None = None) -> None:
        """Keep live pane keys in the store without resetting existing settings.

        Layouts may recreate Blender ``Area`` objects, so callers provide the stable
        pane keys on each sync. Existing keys retain their settings; newly visible
        panes receive the default profile. A document therefore never shares one
        mutable fallback setting between panes.
        """
        ids = tuple(viewport_ids)
        try:
            if len(set(ids)) != len(ids):
                raise ValueError("viewport_ids must contain unique IDs")
        except TypeError as exc:
            raise ValueError("viewport IDs must be hashable") from exc
        if not ids:
            raise ValueError("at least one viewport is required")
        if active_viewport_id is None:
            active_viewport_id = self.active_viewport_id
        if active_viewport_id not in ids:
            active_viewport_id = ids[0]
        default = GridSettings()
        self._settings = {key: self._settings.get(key, default) for key in ids}
        self.active_viewport_id = active_viewport_id

    def settings_for(self, viewport_id: Any) -> GridSettings:
        try:
            return self._settings[viewport_id]
        except KeyError as exc:
            raise ValueError(f"unknown viewport ID: {viewport_id!r}") from exc

    def snapshot(self) -> dict[Any, GridSettings]:
        return dict(self._settings)

    def apply(self, settings: GridSettings) -> tuple[Any, ...]:
        """Apply validated settings and return the changed viewport IDs."""
        plan = settings.application_plan(
            self.viewport_ids, active_viewport_id=self.active_viewport_id)
        changed = tuple(viewport_id for viewport_id, value in plan
                        if self._settings[viewport_id] != value)
        if changed:
            self._undo.append(self.snapshot())
            self._settings.update(plan)
        return changed

    def apply_snap_spacing(self, spacing: float, apply_to: str = ACTIVE) -> tuple[Any, ...]:
        """Change only snap spacing in the requested viewport scope."""
        probe = GridSettings(snap_spacing=spacing, apply_to=apply_to)
        targets = (self.viewport_ids if apply_to == ALL else (self.active_viewport_id,))
        changed = tuple(viewport_id for viewport_id in targets
                        if self._settings[viewport_id].snap_spacing != probe.snap_spacing)
        if changed:
            self._undo.append(self.snapshot())
            for viewport_id in changed:
                current = self._settings[viewport_id]
                self._settings[viewport_id] = current.copy(
                    snap_spacing=probe.snap_spacing, apply_to=apply_to)
        return changed

    def undo(self) -> bool:
        """Restore the latest store snapshot, if one exists."""
        if not self._undo:
            return False
        self._settings = self._undo.pop()
        return True

    @property
    def undo_count(self) -> int:
        return len(self._undo)


def invalidate_grid_caches(batch_cache: dict, extent_cache: dict) -> None:
    """Invalidate the renderer's mutable caches after a grid settings change."""
    batch_cache.clear()
    extent_cache["key"] = None


__all__ = [
    "ACTIVE",
    "ALL",
    "APPLY_TO",
    "MAX_GRID_LINE_COUNT",
    "GridSettings",
    "GridSettingsStore",
    "invalidate_grid_caches",
]

"""Pure predicates and expansion rules for property-based selection commands.

This module deliberately has no Blender or document mutation dependencies.  The
command layer supplies the document's group rows and object attributes, while
these functions define exact-name matching and eligibility consistently for
SelGroup and the later property-selection commands.
"""
from __future__ import annotations


NORMAL_MODE = 0


def exact_group_indices(groups, name: str) -> list[int]:
    """Return every group row whose name equals ``name`` exactly.

    Group-name lookup is case-sensitive (MEASURED: SelGroup's named lookup), and
    duplicate rows are returned so the command can reject an ambiguous document
    rather than silently choosing one.
    """
    target = str(name)
    matches = []
    for index, group in enumerate(groups):
        if (getattr(group, "Name", "") or "") == target:
            matches.append(index)
    return matches


def selectable_mode(mode, normal_mode=NORMAL_MODE) -> bool:
    """Whether an object mode is eligible for property selection.

    Hidden and locked objects are not selectable by SelGroup.  ``normal_mode``
    is injectable to keep this predicate independent of rhino3dm enums.
    """
    return mode == normal_mode


def name_matches(name, pattern: str) -> bool:
    """Return whether an object name matches a SelName pattern.

    MEASURED: ``SelName``'s scriptable form supports only ``*`` (any string)
    and ``?`` (one character).  Object-name lookup in this app is
    case-insensitive, so matching folds case but does not add any fuzzy rules.
    ``fnmatchcase`` is used rather than platform ``fnmatch`` so the result is
    deterministic on every host.
    """
    import fnmatch

    return fnmatch.fnmatchcase(str(name).lower(), str(pattern).lower())


def named_object_names(objects, pattern: str, *, normal_mode=NORMAL_MODE) -> list[str]:
    """Return eligible object names matching ``pattern``, in document order.

    Each document row is an object identity.  Names are intentionally not
    deduplicated: duplicate Rhino display names therefore select every eligible
    object carrying that name, while hidden and locked rows remain ineligible.
    """
    if pattern is None or str(pattern) == "":
        return []
    names = []
    for obj in objects:
        attrs = getattr(obj, "Attributes", None)
        if attrs is None or not selectable_mode(getattr(attrs, "Mode", None), normal_mode):
            continue
        name = getattr(attrs, "Name", None)
        if name and name_matches(name, pattern):
            names.append(name)
    return names


def _wildcard_regex(pattern: str):
    """Compile Rhino's measured text wildcard syntax.

    MEASURED (`commands/selection_commands.htm`, SelKey/SelValue/
    SelKeyValue): ``?``, ``*`` and ``~`` are listed as wildcard characters.
    In the Microsoft-style syntax linked by that page, ``~`` quotes the next
    wildcard character; a trailing ``~`` is literal.  Matching is case
    insensitive, as with this application's existing property selectors.
    """
    import re

    pieces = ["^"]
    text = str(pattern)
    index = 0
    while index < len(text):
        char = text[index]
        if char == "~" and index + 1 < len(text):
            index += 1
            pieces.append(re.escape(text[index]))
        elif char == "*":
            pieces.append(".*")
        elif char == "?":
            pieces.append(".")
        else:
            pieces.append(re.escape(char))
        index += 1
    pieces.append("$")
    return re.compile("".join(pieces), re.IGNORECASE | re.DOTALL)


def text_matches(text, pattern: str) -> bool:
    """Return whether one user-text string matches a Sel* pattern."""
    return _wildcard_regex(pattern).match(str(text)) is not None


def _user_text_rows(attrs):
    """Read live ObjectAttributes user strings as ordered ``(key, value)`` rows."""
    try:
        rows = attrs.GetUserStrings2() or []
    except Exception:  # noqa: BLE001 -- malformed attributes are ineligible
        return ()
    result = []
    for row in rows:
        try:
            key, value = row
        except (TypeError, ValueError):
            continue
        result.append((str(key), str(value)))
    return result


def user_text_object_names(objects, kind: str, pattern: str,
                           value_pattern: str | None = None,
                           *, normal_mode=NORMAL_MODE) -> list[str]:
    """Return visible/unlocked named objects matching live user-text rows.

    ``kind`` is ``"key"``, ``"value"`` or ``"keyvalue"``.  The latter tests
    both patterns against the *same* row (AND), never independent rows (or OR).
    Object identity, not row count, controls deduplication; separate document
    objects with the same display name remain separate rows for selection.
    """
    if kind not in {"key", "value", "keyvalue"}:
        raise ValueError(f"unknown user-text selection kind {kind!r}")
    if kind == "keyvalue" and value_pattern is None:
        return []
    names = []
    seen_objects = set()
    for obj in objects:
        attrs = getattr(obj, "Attributes", None)
        if attrs is None or not selectable_mode(getattr(attrs, "Mode", None), normal_mode):
            continue
        name = getattr(attrs, "Name", None) or ""
        if not name or id(obj) in seen_objects:
            continue
        rows = _user_text_rows(attrs)
        if kind == "key":
            matched = any(text_matches(key, pattern) for key, _ in rows)
        elif kind == "value":
            matched = any(text_matches(value, pattern) for _, value in rows)
        else:
            assert value_pattern is not None
            matched = any(text_matches(key, pattern) and
                          text_matches(value, value_pattern)
                          for key, value in rows)
        if matched:
            seen_objects.add(id(obj))
            names.append(name)
    return names


def group_member_names(objects, group_index: int, *, normal_mode=NORMAL_MODE):
    """Return visible/unlocked member names once, in document order.

    Membership is tested only against the named group index.  In particular,
    this does not traverse other groups, including overlapping groups.
    """
    names = []
    seen_ids = set()
    seen_names = set()
    for obj in objects:
        attrs = getattr(obj, "Attributes", None)
        if attrs is None or not selectable_mode(getattr(attrs, "Mode", None), normal_mode):
            continue
        try:
            memberships = attrs.GetGroupList2() or []
        except Exception:  # noqa: BLE001 -- malformed attributes are ineligible
            continue
        if group_index not in {int(value) for value in memberships}:
            continue
        name = getattr(attrs, "Name", None) or ""
        if not name:
            continue
        object_id = getattr(attrs, "Id", None)
        key = str(object_id) if object_id is not None else ("name", name)
        if key in seen_ids or name in seen_names:
            continue
        seen_ids.add(key)
        seen_names.add(name)
        names.append(name)
    return names

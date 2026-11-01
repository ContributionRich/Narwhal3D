"""Host payload/lifetime glue for the persistent Rust object registry.

Rust owns membership/order/names/display associations, dirty marks and snapshot
plans. Python only copies exact host objects and carries their immutable references
alongside the existing document/metadata Undo snapshot. No second history stack.
"""
from __future__ import annotations

from collections.abc import MutableMapping
import importlib.util
from pathlib import Path
import sys
import weakref


def _load(leaf):
    name = 'narwhal3d.' + leaf
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(leaf + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


native = _load('native_object_registry')
observer_api = _load('native_document_observer')


class DisplayNames(MutableMapping):
    """Compatibility view; there is no independently writable Python name table."""
    def __init__(self, binding):
        self._binding = weakref.ref(binding)

    def binding(self):
        binding = self._binding()
        if binding is None:
            raise native.RegistryError('retired_display_view')
        return binding

    def _rows(self):
        binding = self.binding()
        if binding.suspended:
            return list(binding.restore_current.names.items())
        binding.sync()
        return [(row['id'], row['display']) for row in binding.native.rows() if row['display'] is not None]

    def __iter__(self):
        return iter(key for key, _ in self._rows())

    def __len__(self):
        return len(self._rows())

    def __getitem__(self, key):
        if not native._uuid(str(key)):
            raise KeyError(key)
        binding = self.binding()
        if binding.suspended:
            return binding.restore_current.names[str(key)]
        row = binding.row(str(key))
        if row is None or row['display'] is None:
            raise KeyError(key)
        return row['display']

    def __setitem__(self, key, value):
        binding = self.binding()
        if not binding.suspended:
            if binding.row(str(key)) is None:
                raise KeyError(key)
            binding.native.bind_display(str(key), value)

    def __delitem__(self, key):
        self[key]
        binding = self.binding()
        if not binding.suspended:
            binding.native.bind_display(str(key), None)

    def items(self):
        return self._rows()

    def values(self):
        return [value for _, value in self._rows()]

    def clear(self):
        binding = self.binding()
        if not binding.suspended:
            for key, _ in self._rows():
                binding.native.bind_display(key, None)

    def update(self, *args, **kwargs):
        if not self.binding().suspended:
            super().update(*args, **kwargs)


class Binding:
    def __init__(self, model, file):
        self.model = weakref.ref(model)
        self.file = file
        self.observer = observer_api.load_observer()
        self.native = native.Registry()
        self.native.track()
        self.suspended = False
        self.restore_current = None
        self.restore_changes = None
        # Host-owned immutable geometry references, keyed by Rust-issued payload IDs.
        # This is not the object-membership index: the native snapshot plan selects it.
        self.payloads = {}
        self.attribute_holder = None
        self.names = DisplayNames(self)
        try:
            self.sync()
        except BaseException:
            self.native.close()
            raise

    def close(self):
        self.native.close()
        self.payloads.clear()

    def sync(self):
        if self.suspended:
            return self.native.info()
        return self.native.sync_index(self.observer.index(self.file))

    def mark(self, ids, moved=False):
        if not self.suspended:
            self.native.mark(ids, moved=moved)

    def mark_all(self):
        if not self.suspended:
            self.native.mark_all()

    def dirty(self):
        return dict(self.native.dirty()['rows'])

    def row(self, object_id):
        row = self.native.get(object_id)
        present = self.file.Objects.FindId(object_id) is not None
        # A display association is keyed by UUID, not by mutable object name or
        # geometry. Check membership via openNURBS' native UUID index; only a real
        # membership mismatch needs the complete name/index observation.
        if (row is not None) != present:
            self.sync()
            row = self.native.get(object_id)
        return row

    def count(self):
        host_count = len(self.file.Objects)
        if self.suspended:
            return host_count
        info = self.native.info()
        if info['count'] != host_count:
            info = self.sync()
        return info['count']

    def find_name(self, name):
        self.sync()
        return self.native.find_name(name)

    def snapshot(self, build, *, rebuild=False):
        if self.suspended:
            return self.restore_current
        data = self.observer.observe(self.file)
        ticket = self.native.capture(data, rebuild=rebuild)
        try:
            rows = self.native.rows(ticket=ticket.handle)
            names = {row['id']: row['display'] for row in rows if row['display'] is not None}
            entries = {}
            copied = 0
            model = self.model()
            for row in rows:
                key = row['payload']
                entry = self.payloads.get(key)
                if entry is None:
                    if self.attribute_holder is None:
                        self.attribute_holder = observer_api.r3.File3dm()
                    obj = self.file.Objects.FindId(row['id'])
                    if obj is None:
                        raise native.RegistryError('host_payload_missing')
                    entry = model._entry(obj, names)
                    entry['_registry_payload'] = key
                    copied += 1
                if entry['id'] != row['id']:
                    raise native.RegistryError('host_payload_owner_mismatch')
                entries[row['id']] = entry
            out = build(entries, names)
            ticket.begin_apply()
            ticket.commit_observed(self.observer.observe(self.file))
            out.registry = self.native.snapshot()
            self.payloads = {entry['_registry_payload']: entry for entry in entries.values()}
            model.last_snapshot = ('full' if copied == len(entries) and copied else 'shared', copied)
            return out
        except BaseException:
            if not ticket.closed:
                if ticket._applying[0]:
                    self.native.invalidate()
                else:
                    ticket.abort()
            raise
        finally:
            self.attribute_holder = None

    def validate_snapshot(self, snapshot):
        saved = getattr(snapshot, 'registry', None)
        if not isinstance(saved, native.Snapshot) or saved.owner is not self.native or saved.closed:
            raise native.RegistryError('invalid_snapshot')

    def restore(self, snapshot, apply):
        self.validate_snapshot(snapshot)
        model = self.model()
        current = model.snapshot()
        ticket = self.native.prepare_restore(snapshot.registry)
        try:
            rows = self.native.rows(ticket=ticket.handle)
            entries = snapshot.by_id
            if len(entries) != len(rows) or any(
                row['id'] not in entries or entries[row['id']].get('_registry_payload') != row['payload']
                or entries[row['id']]['id'] != row['id'] for row in rows
            ):
                raise native.RegistryError('invalid_host_snapshot_payload')
            changes = self.native.restore_changes(ticket.handle)
        except BaseException:
            ticket.abort()
            raise
        ticket.begin_apply()
        self.suspended, self.restore_current, self.restore_changes = True, current, changes
        try:
            result = apply(snapshot)
            ticket.commit_observed(self.observer.observe(self.file))
            model._order_dirty = self.native.order_pending()
            self.payloads = {entry['_registry_payload']: entry for entry in snapshot.by_id.values()}
            return result
        except BaseException:
            self.native.invalidate()
            raise
        finally:
            self.suspended, self.restore_current, self.restore_changes = False, None, None

"""File3dm compatibility and serialization boundary for Rust-owned document strings.

Python is necessary here for rhino3dm descriptors, model callbacks and file adoption.
Rust owns native-session keys, edits, generations and immutable snapshot contents.
Unowned File3dms retain their original native behavior. Missing default application
core selects the complete legacy File3dm route at adoption, never midway through an
existing native session. A raw native table changed behind the facade is refused
at serialization rather than silently merged or overwritten.
"""
from __future__ import annotations

import importlib.util
import operator
from pathlib import Path
import sys
import weakref

import rhino3dm as r3

_NAME = 'narwhal3d.native_document_session'
native = sys.modules.get(_NAME)
if native is None:
    _spec = importlib.util.spec_from_file_location(_NAME, Path(__file__).with_name('native_document_session.py'))
    native = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = native
    _spec.loader.exec_module(native)


class MetadataError(RuntimeError):
    pass


def _hooks():
    # model.py is also loaded by file path in compatibility tests. Install once on
    # the native class, not once per Python module alias.
    hooks = getattr(r3.File3dm, '_narwhal_document_metadata_hooks', None)
    if hooks is not None:
        return hooks
    hooks = {'strings': r3.File3dm.Strings, 'write': r3.File3dm.Write,
             'encode': r3.File3dm.Encode, 'files': weakref.WeakKeyDictionary()}

    def strings(file):
        binding = hooks['files'].get(file)
        if binding is None:
            return hooks['strings'].__get__(file, type(file))
        binding.check()
        return binding.table

    def write(file, *args, **kwargs):
        binding = hooks['files'].get(file)
        if binding is not None:
            binding.materialize()
        return hooks['write'](file, *args, **kwargs)

    def encode(file, *args, **kwargs):
        binding = hooks['files'].get(file)
        if binding is not None:
            binding.materialize()
        return hooks['encode'](file, *args, **kwargs)

    r3.File3dm.Strings = property(strings)
    r3.File3dm.Write = write
    r3.File3dm.Encode = encode
    r3.File3dm._narwhal_document_metadata_hooks = hooks
    return hooks


def _raw(file):
    return _hooks()['strings'].__get__(file, type(file))


def _text(value):
    if not isinstance(value, str):
        raise TypeError('document user text requires strings')
    # MEASURED: the native StringTable treats NUL as a terminator. Keep that
    # compatibility conversion outside the stricter Rust UTF-8 scalar contract.
    value = value.split('\0', 1)[0]
    value.encode('utf-8')
    return value


class StringTable:
    """Live compatibility view; a retired backing file never routes into a new one."""
    def __init__(self, binding):
        self._binding = binding

    def __len__(self):
        self._binding.check()
        return self._binding.session.info()['count']

    def __iter__(self):
        self._binding.check()
        return iter(self._binding.session.rows())

    def __getitem__(self, key):
        self._binding.check()
        if isinstance(key, str):
            reply = self._binding.session.get(_text(key), exact=True)
            return reply['value'] if reply['found'] else ''
        index = operator.index(key)
        if index < 0 or index > 2**64 - 1:
            raise IndexError('document string index out of range')
        reply = self._binding.session.at(index)
        if not reply['found']:
            raise IndexError('document string index out of range')
        return tuple(reply['row'])

    def __setitem__(self, key, value):
        self._binding.change(_text(key), _text(value))

    def Delete(self, key):
        self._binding.change(_text(key), None)

    def DocumentUserTextCount(self):
        self._binding.check()
        return self._binding.session.request('document_user_text_count')['count']


class Binding:
    def __init__(self, model, file, session):
        self.model = weakref.ref(model)
        self.file = weakref.ref(file)
        self.session = session
        self.retired = False
        self.editing = False
        self.table = StringTable(self)
        self.projected = tuple(_raw(file))
        self.projected_state = session.info()['state_id']

    @property
    def backend(self):
        return 'rust'

    def check(self):
        if self.retired or self.file() is None:
            raise MetadataError('metadata_backing_file_retired')

    def change(self, key, value, before_change=None):
        self.check()
        if self.editing:
            raise MetadataError('metadata_reentrant_change')
        prepared = self.session.prepare(key, value)
        if not prepared['changed']:
            return False
        ticket = prepared['ticket']
        self.editing = True
        try:
            if before_change is not None:
                before_change()
            model = self.model()
            if model is not None:
                model._will_change()
            self.check()
            self.session.commit(ticket)
            return True
        except BaseException:
            try:
                self.session.abort(ticket)
            except native.SessionError:
                pass  # A stale commit already consumed it; never retry the edit.
            raise
        finally:
            self.editing = False

    def snapshot(self):
        self.check()
        return self.session.snapshot()

    def validate_snapshot(self, snapshot):
        self.check_projection()
        self.session.validate_snapshot(snapshot)

    def check_projection(self):
        self.check()
        if tuple(_raw(self.file())) != self.projected:
            raise MetadataError('unmediated_native_metadata_write')

    def restore(self, snapshot):
        self.check()
        if not isinstance(snapshot, native.Snapshot):
            raise MetadataError('native_metadata_snapshot_required')
        self.session.restore(snapshot)

    def materialize(self):
        self.check()
        file = self.file()
        table = _raw(file)
        current = tuple(table)
        if current != self.projected:
            raise MetadataError('unmediated_native_metadata_write')
        state = self.session.info()['state_id']
        if state == self.projected_state:
            return
        rows = tuple(self.session.rows())
        try:
            for key, _ in current:
                table.Delete(key)
            for key, value in rows:
                table[key] = value
            if tuple(table) != rows:
                raise MetadataError('metadata_projection_mismatch')
        except BaseException:
            for key, _ in list(table):
                table.Delete(key)
            for key, value in current:
                table[key] = value
            raise
        self.projected, self.projected_state = rows, state


class LegacyBinding:
    """Complete compatibility route chosen only when no default native library exists."""
    backend = 'legacy-file3dm'

    def __init__(self, model, file):
        self.file = weakref.ref(file)
        self.model = weakref.ref(model)

    def change(self, key, value, before_change=None):
        # Delegate legacy ordinal key semantics to openNURBS, not a second Python
        # implementation. This route exists only when the native session is absent.
        table = self.file().Strings
        probe = r3.File3dm()
        for name, text in table:
            probe.Strings[name] = text
        if value is None:
            probe.Strings.Delete(key)
        else:
            probe.Strings[key] = value
        if list(probe.Strings) == list(table):
            return False
        if before_change is not None:
            before_change()
        model = self.model()
        if model is not None:
            model._will_change()
        if value is None:
            table.Delete(key)
        else:
            table[key] = value
        return True

    def snapshot(self):
        return list(self.file().Strings)

    def validate_snapshot(self, rows):
        if isinstance(rows, native.Snapshot):
            raise native.SessionError('snapshot_owner_mismatch')

    def restore(self, rows):
        table = self.file().Strings
        if list(table) != rows:
            for key, _ in list(table):
                table.Delete(key)
            for key, value in rows:
                table[key] = value

    def materialize(self):
        pass


def adopt(model, file, previous=None, *, preserve=False):
    """Prepare a backing-file adoption; preserve=True is internal round-trip only."""
    hooks = _hooks()
    if isinstance(previous, Binding) and previous.editing:
        raise MetadataError('metadata_reentrant_adoption')
    # Instrumentation proxies delegate Strings to the already-owned native file.
    if not isinstance(file, r3.File3dm):
        if previous is not None and getattr(file, 'Strings', None) is getattr(previous, 'table', object()):
            return previous
        raise MetadataError('metadata_requires_file3dm')
    existing = hooks['files'].get(file)
    if existing is previous and existing is not None and not existing.retired:
        return existing
    if existing is not None and not existing.retired:
        if existing.model() is not None:
            raise MetadataError('metadata_file_already_owned')
        # An escaped File3dm can outlive its model. Transfer its materialized values
        # into a fresh document session, never steal a live model's authority.
        existing.materialize()
    if previous is not None:
        previous.materialize()
    if preserve and isinstance(previous, Binding):
        session = previous.session
        if tuple(_raw(file)) != previous.projected:
            raise MetadataError('roundtrip_metadata_changed')
        binding = Binding(model, file, session)
    else:
        library = native.load_library()
        if library is None:
            binding = LegacyBinding(model, file)
        else:
            session = native.Session(list(_raw(file)), library=library)
            binding = Binding(model, file, session)
    if isinstance(previous, Binding):
        previous.retired = True
    if existing is not None and existing is not previous:
        existing.retired = True
    if isinstance(binding, Binding):
        hooks['files'][file] = binding
    elif existing is not None:
        hooks['files'].pop(file, None)
    return binding

#!/usr/bin/env python3
"""Candidate-backed registry lifetime/protocol tests. No live constructor hooks."""
from __future__ import annotations
import argparse
import ctypes
import gc
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--library',required=True)
parser.add_argument('--mutations',action='store_true')
args=parser.parse_args()
os.environ['NARWHAL_APPLICATION_CORE']=str(Path(args.library).resolve())
os.environ['NARWHAL_HISTORY_CORE']=os.environ['NARWHAL_APPLICATION_CORE']
pkg=types.ModuleType('narwhal3d');pkg.__path__=[str(ROOT/'narwhal3d')]
sys.modules['narwhal3d']=pkg
spec=importlib.util.spec_from_file_location('narwhal3d.native_object_registry',ROOT/'narwhal3d/native_object_registry.py')
n=importlib.util.module_from_spec(spec);sys.modules[spec.name]=n;spec.loader.exec_module(n)


def row(i,name='same',display=None):
    return dict(id=f'00000000-0000-0000-0000-{i:012x}',name=name,display=display,payload=i)


class RegistryTests(unittest.TestCase):
    def registry(self,rows=()):
        r=n.Registry(rows);self.addCleanup(r.close);return r

    def test_real_queries_order_names_payloads_and_noop(self):
        r=self.registry([row(2),row(1)])
        self.assertEqual(r.rows(),[row(2),row(1)])
        self.assertEqual(r.find_name('SAME'),row(2)['id'])
        self.assertEqual(r.get(row(1)['id']),row(1))
        t=r.prepare([row(2),row(1)]);t.begin_apply();t.commit()
        self.assertEqual(r.info()['revision'],0)
        t=r.prepare([row(1),row(2)]);t.begin_apply();t.commit()
        self.assertEqual(r.info()['revision'],1)
        self.assertEqual(r.rows(),[row(1),row(2)])

    def test_batch_edits_send_only_changed_rows(self):
        r=self.registry([row(i) for i in range(1,2001)])
        original=r._function;requests=[]
        def call(data,length,out,capacity):
            requests.append(json.loads(bytes(data[:length])))
            return original(data,length,out,capacity)
        r._function=call
        t=r.prepare_edits([dict(kind='replace',row=row(10,'new'),append=True),
                           dict(kind='delete',id=row(12)['id'])],revision=0)
        t.begin_apply();t.commit()
        self.assertEqual([v['op'] for v in requests],['prepare_edits','begin_apply','commit'])
        self.assertEqual(len(requests[0]['edits']),2)
        self.assertEqual(r.info()['count'],1999)
        self.assertEqual(r.get(row(10)['id'])['name'],'new')
        self.assertEqual(r.rows()[-1]['id'],row(10)['id'])

    def test_batch_pages_not_per_object_calls(self):
        r=self.registry([row(i) for i in range(1,2100)])
        original=r._function;calls=[]
        def call(data,length,out,capacity):
            calls.append(json.loads(bytes(data[:length]))['op'])
            return original(data,length,out,capacity)
        r._function=call
        self.assertEqual(len(r.rows()),2099)
        # Three pages, with one bounded output-size negotiation for each page.
        self.assertLessEqual(len(calls),6)
        self.assertEqual(set(calls),{'rows'})

    def test_prepare_abort_and_failed_host_application_invalidate(self):
        r=self.registry([row(1)])
        t=r.prepare([]);t.abort()
        self.assertEqual(r.rows(),[row(1)])
        t=r.prepare([]);t.begin_apply()
        with self.assertRaisesRegex(n.RegistryError,'possibly_applied'):t.abort()
        del t;gc.collect()
        self.assertFalse(r.info()['authoritative'])
        with self.assertRaisesRegex(n.RegistryError,'authority_invalid'):r.rows()
        t=r.prepare([],rebuild=True);t.begin_apply();t.commit()
        self.assertTrue(r.info()['authoritative'])
        self.assertEqual(r.rows(),[])

    def test_snapshot_lifetimes_and_wrong_owner_refusal(self):
        r=self.registry([row(1)]);other=self.registry()
        snap=r.snapshot()
        with self.assertRaisesRegex(n.RegistryError,'invalid_snapshot'):other.prepare_restore(snap)
        t=r.prepare([]);t.begin_apply();t.commit()
        t=r.prepare_restore(snap);t.begin_apply();t.commit()
        self.assertEqual(r.rows(),[row(1)])
        snap.close()
        with self.assertRaisesRegex(n.RegistryError,'invalid_snapshot'):r.prepare_restore(snap)

    def test_stale_revision_and_duplicate_uuid_do_not_mutate(self):
        r=self.registry([row(1)])
        with self.assertRaisesRegex(n.RegistryError,'duplicate_uuid'):r.prepare([row(1),row(1)])
        with self.assertRaisesRegex(n.RegistryError,'stale_revision'):r.prepare([],revision=100)
        self.assertEqual(r.rows(),[row(1)])
        self.assertEqual(r.info()['revision'],0)

    def test_registry_and_metadata_use_one_existing_native_undo_record(self):
        from narwhal3d.undo import UndoStack
        r=self.registry([row(1)])
        metadata=n.native.Session([('note','before')]);self.addCleanup(metadata.close)
        history=UndoStack();self.addCleanup(history.clear)
        def snapshot():return (r.snapshot(),metadata.snapshot())
        def restore(pair):
            metadata.validate_snapshot(pair[1])
            ticket=r.prepare_restore(pair[0]);ticket.begin_apply()
            metadata.restore(pair[1]);ticket.commit()
        history.begin('combined');history.capture(snapshot());history.begin('inner')
        t=r.prepare_edits([dict(kind='replace',row=row(1,'changed'),append=False)])
        t.begin_apply();t.commit()
        metadata.commit(metadata.prepare('note','after')['ticket'])
        self.assertFalse(history.end());self.assertTrue(history.end())
        self.assertEqual(history.undo_names(),['combined'])
        history.undo(snapshot(),restore=restore)
        self.assertEqual(r.get(row(1)['id'])['name'],'same')
        self.assertEqual(metadata.get('note',exact=True)['value'],'before')
        history.redo(snapshot(),restore=restore)
        self.assertEqual(r.get(row(1)['id'])['name'],'changed')
        self.assertEqual(metadata.get('note',exact=True)['value'],'after')
        history.begin('no capture');self.assertFalse(history.end())
        self.assertEqual(history.undo_names(),['combined'])
        history.begin('cancel');history.abandon()
        self.assertEqual(history.undo_names(),['combined'])

    def test_history_restore_admission_failure_does_not_consume_record(self):
        from narwhal3d.undo import UndoStack
        r=self.registry([row(1)]);history=UndoStack();self.addCleanup(history.clear)
        history.begin('edit');history.capture(r.snapshot())
        t=r.prepare([]);t.begin_apply();t.commit();history.end()
        def refuse(snapshot):raise RuntimeError('host admission refused')
        with self.assertRaisesRegex(RuntimeError,'host admission refused'):
            history.undo(r.snapshot(),restore=refuse,failure_state=lambda error:'unchanged')
        self.assertEqual(history.undo_names(),['edit'])
        self.assertEqual(history.redo_names(),[])
        self.assertEqual(r.rows(),[])

    def test_abandoned_prepare_aborts_without_advancing(self):
        r=self.registry([row(1)]);t=r.prepare([]);del t;gc.collect()
        self.assertEqual(r.info(),dict(revision=0,count=1,authoritative=True,pending=False))

    def test_unknown_commit_reply_is_not_retried_and_requires_invalidation(self):
        r=self.registry([row(1)]);t=r.prepare([]);t.begin_apply()
        real=r._function;calls=[]
        def lost(data,length,out,capacity):
            calls.append(json.loads(bytes(data[:length]))['op'])
            real(data,length,out,capacity)
            out[0]=ord('!')
            return 1
        r._function=lost
        with self.assertRaisesRegex(n.RegistryError,'invalid_reply'):t.commit()
        self.assertEqual(calls,['commit'])
        r._function=real
        self.assertFalse(r.info()['authoritative'])
        with self.assertRaisesRegex(n.RegistryError,'outcome_unknown'):r.rows()
        r.invalidate()
        self.assertFalse(r.info()['authoritative'])
        t._finalizer.detach()  # lost ticket explicitly superseded by invalidation

    def test_missing_and_wrong_abi_are_explicit(self):
        with self.assertRaises(n.RegistryUnavailable):n.Registry(library=object())
        class Function:
            def __call__(self):return 99
        calls=[]
        class Call:
            def __call__(self,data,length,out,capacity):
                calls.append(1)
                payload=json.dumps(dict(status='ok',owner=999,revision=0,count=0,
                                        authoritative=True,pending=False)).encode()
                ctypes.memmove(out,payload,len(payload))
                return len(payload)
        bad=types.SimpleNamespace(narwhal_object_registry_abi=Function(),
                                  narwhal_object_registry_json=Call())
        with self.assertRaisesRegex(n.RegistryUnavailable,'wrong ABI'):n.Registry(library=bad)
        self.assertEqual(calls,[])

    def test_duplicate_rows_and_uncorrelated_lookup_refuse(self):
        r=self.registry([row(1)])
        real=r._function
        self.addCleanup(setattr,r,'_function',real)
        def reply(payload):
            def call(data,length,out,capacity):
                packed=json.dumps(dict(status='ok',**payload)).encode()
                ctypes.memmove(out,packed,len(packed));return len(packed)
            return call
        r._function=reply(dict(revision=0,rows=[row(1),row(1)],next=2))
        with self.assertRaisesRegex(n.RegistryError,'invalid_reply'):r.rows()
        r._function=real;r.invalidate()
        t=r.prepare([row(1)],rebuild=True);t.begin_apply();t.commit()
        r._function=reply(dict(revision=2,row=row(2)))
        with self.assertRaisesRegex(n.RegistryError,'invalid_reply'):r.get(row(1)['id'])
        with self.assertRaisesRegex(n.RegistryError,'invalid_reply'):
            n._validate('rows',dict(status='ok',revision=0,rows=[],next=100001))

    def test_interruption_after_native_commit_quarantines_owner(self):
        r=self.registry([row(1)]);t=r.prepare([]);t.begin_apply()
        real=r._function;calls=[]
        def interrupted(data,length,out,capacity):
            calls.append(1);real(data,length,out,capacity);raise KeyboardInterrupt()
        r._function=interrupted
        with self.assertRaises(KeyboardInterrupt):t.commit()
        r._function=real
        self.assertEqual(calls,[1])
        self.assertFalse(r.info()['authoritative'])
        with self.assertRaisesRegex(n.RegistryError,'outcome_unknown'):r.rows()
        r.invalidate();t._finalizer.detach()

    def test_invalid_reply_fields_are_never_success(self):
        replies=[None,{},dict(status='ok',revision=True,count=0,authoritative=True,pending=False),
                 dict(status='ok',revision=0,count=0,authoritative=True,pending=False,extra=1),
                 dict(status='error',code=1)]
        for reply in replies:
            with self.subTest(reply=reply),self.assertRaisesRegex(n.RegistryError,'invalid_reply'):
                n._validate('info',reply)

    def test_oversize_mutation_reply_never_retries(self):
        calls=[]
        def too_large(data,length,out,capacity):calls.append(1);return capacity+1
        with self.assertRaisesRegex(n.RegistryError,'mutation_reply_overflow'):
            n._call(too_large,dict(op='create',rows=[]))
        self.assertEqual(len(calls),1)


def mutations():
    global n
    original=n
    source=(ROOT/'narwhal3d/native_object_registry.py').read_text()
    variants=[
        ('bool-as-revision','type(value) is int','isinstance(value, int)',
         'test_invalid_reply_fields_are_never_success'),
        ('lost-outcome-not-quarantined','self._uncertain = True','self._uncertain = False',
         'test_unknown_commit_reply_is_not_retried_and_requires_invalidation'),
        ('false-authority','if self._uncertain:','if False:',
         'test_unknown_commit_reply_is_not_retried_and_requires_invalidation'),
        ('retry-mutating-call','if op not in _READ:','if False:',
         'test_oversize_mutation_reply_never_retries'),
        ('wrong-abi-admitted','if abi() != 1:','if False:',
         'test_missing_and_wrong_abi_are_explicit'),
        ('duplicate-rows','if len(set(ids)) != len(ids) or seen.intersection(ids):','if False:',
         'test_duplicate_rows_and_uncorrelated_lookup_refuse'),
        ('wrong-lookup-id',"if row is not None and row['id'] != str(object_id):",'if False:',
         'test_duplicate_rows_and_uncorrelated_lookup_refuse'),
        ('interruption-ignored','except BaseException:','except Exception:',
         'test_interruption_after_native_commit_quarantines_owner'),
    ]
    for label,old,new,test in variants:
        if old not in source:raise RuntimeError('stale mutant '+label)
        module=types.ModuleType('registry09_mutant')
        module.__file__=str(ROOT/'narwhal3d/native_object_registry.py')
        exec(compile(source.replace(old,new),module.__file__,'exec'),module.__dict__)
        n=module
        try:
            result=unittest.TestResult();RegistryTests(test).run(result)
        finally:
            n=original
        if not result.failures or result.errors:
            raise AssertionError(f'{label}: expected assertion failure, got failures={result.failures}, errors={result.errors}')
        print('RED assertion:',label)
    restored=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(RegistryTests))
    if not restored.wasSuccessful():raise SystemExit(1)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RegistryTests))
    if not result.wasSuccessful():raise SystemExit(1)
    if args.mutations:mutations()

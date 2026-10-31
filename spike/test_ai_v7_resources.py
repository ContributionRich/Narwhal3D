#!/usr/bin/env python3
"""Deterministic fixture-build lock deadline tests; never launches a compiler."""
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch
import test_ai_accepted_review_native as bridge


def deadline_probe(function):
    with patch.object(bridge.time,'monotonic',side_effect=[0,61]), \
         patch.object(bridge.time,'sleep'), \
         patch.object(bridge.fcntl,'flock',side_effect=[BlockingIOError(),RuntimeError('watchdog sentinel')]), \
         patch.object(bridge,'compile_command') as compiler:
        try:function(Path('/tmp/narwhal-review-lock-fixture'))
        except Exception as error:kind=type(error)
        else:kind=None
        compiler.assert_not_called()
        return kind


class Resources(unittest.TestCase):
    def test_busy_lock_stops_before_compiler(self):
        self.assertIs(deadline_probe(bridge.build_bridge),TimeoutError)
    def test_retry_can_acquire_without_outer_lock(self):
        with patch.object(bridge.time,'monotonic',side_effect=[0,1]),patch.object(bridge.time,'sleep'), \
             patch.object(bridge.fcntl,'flock',side_effect=[BlockingIOError(),None]) as flock, \
             patch.object(bridge,'_source_hash',side_effect=RuntimeError('admitted')), \
             patch.object(bridge,'compile_command') as compiler:
            with self.assertRaisesRegex(RuntimeError,'admitted'):bridge.build_bridge(Path('/tmp/narwhal-review-lock-fixture'))
            self.assertEqual(flock.call_count,2);compiler.assert_not_called()
    def test_deadline_mutation_is_detected(self):
        source=inspect.getsource(bridge.build_bridge)
        old='if time.monotonic() >= deadline:';self.assertEqual(source.count(old),1)
        scope=dict(bridge.__dict__);exec(source.replace(old,'if False:',1),scope)
        self.assertIs(deadline_probe(scope['build_bridge']),RuntimeError)
        self.assertIs(deadline_probe(bridge.build_bridge),TimeoutError)
        print('KILLED lock-deadline mutation; restored GREEN')


if __name__=='__main__':unittest.main(verbosity=2)

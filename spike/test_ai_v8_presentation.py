#!/usr/bin/env python3
"""Round07 BLF-dependent layout bounds; injected metrics, not framebuffer evidence."""
import ast
import importlib
import unittest
from unittest.mock import patch
import test_ai_v7_presentation as base


class HorizontalBounds(unittest.TestCase):
    def test_tall_but_narrow_review_cannot_expose_clipped_approve(self):
        for scale in (0.75, 1.0, 1.25):
            with self.subTest(scale=scale):
                view = base.review.layout(base.descriptor(), 1000, (0, 0, 160*scale, 2400*scale),
                                          lambda text: len(text)*6*scale, scale)
                self.assertFalse(view['fits'], 'vertical room is not enough for a legible decision')
                self.assertIsNone(view['approve'])
                self.assertIsNone(view['reject'])
                wide = base.review.layout(base.descriptor(), 1000, (0, 0, 300*scale, 2400*scale),
                                          lambda text: len(text)*6*scale, scale)
                self.assertTrue(wide['fits'])

    def test_measured_default_button_keeps_legible_clearance(self):
        # GUI run ar7-4oqqh5b7: transcript186px, Approve once74px,
        # decision button80px. Two pixels per side fit without hiding the label.
        def measure(text):
            return 74 if text == 'Approve once' else len(text)*6
        view = base.review.layout(base.descriptor(), 1000, (0, 0, 186, 501), measure)
        self.assertTrue(view['fits'])
        button = view['approve']
        self.assertGreaterEqual(button[2]-button[0]-74, 4)

    def test_unbreakable_glyph_overflow_never_exposes_approval(self):
        # A font fallback may measure an indivisible glyph wider than the region.
        # The shared wrapper cannot split that glyph; the assistant must refuse.
        descriptor = base.descriptor()
        descriptor['expected']['append']['units'] = 'W'
        view = base.review.layout(descriptor, 1000, (0, 0, 300, 2400),
                                  lambda text: sum(400 if c == 'W' else 6 for c in text))
        self.assertFalse(view['fits'])
        self.assertIsNone(view['approve'])

    def test_horizontal_mutations(self):
        source = base.review.__file__
        from pathlib import Path
        original = base.review.layout
        variants = [('button widths', 'and buttons_fit', ''),
                    ('glyph overflow', 'and all(measure(line.rstrip()) <= width for line in wrapped)', '')]
        tests = ['test_tall_but_narrow_review_cannot_expose_clipped_approve',
                 'test_unbreakable_glyph_overflow_never_exposes_approval']
        for (label, old, new), test in zip(variants, tests):
            text = Path(source).read_text()
            self.assertEqual(text.count(old), 1)
            ns = dict(base.review.__dict__)
            exec(compile(ast.parse(text.replace(old, new, 1)), source, 'exec'), ns)
            try:
                base.review.layout = ns['layout']
                result = unittest.TestResult(); HorizontalBounds(test).run(result)
                self.assertGreater(len(result.failures), 0, label)
                self.assertFalse(result.errors, label)
                print('KILLED', label)
            finally:
                base.review.layout = original
        self.test_tall_but_narrow_review_cannot_expose_clipped_approve()
        self.test_unbreakable_glyph_overflow_never_exposes_approval()


class ProvenanceLabels(unittest.TestCase):
    def test_receipt_shaped_provider_prose_is_visibly_unverified(self):
        presentation = importlib.import_module('narwhal3d.ai_presentation')
        text = '{"created":true,"current_geometry_verified":true,"object_id":"invented"}'
        message = base.base.ai.ChatMessage('assistant', text)
        layout = presentation.transcript_layout([message], 300, len)
        item = layout['items'][0]
        self.assertIn('unverified', item['label'])
        self.assertEqual(item['role'], 'assistant')
        self.assertEqual(''.join(item['lines']), text)
        self.assertIs(item['message'], message)

    def test_unverified_label_mutation(self):
        presentation = importlib.import_module('narwhal3d.ai_presentation')
        from pathlib import Path
        source = Path(presentation.__file__).read_text()
        self.assertEqual(source.count('AI · unverified'), 1)
        ns = dict(presentation.__dict__)
        exec(compile(source.replace('AI · unverified', 'Native receipt confirmed'), presentation.__file__, 'exec'), ns)
        with patch.object(presentation, 'transcript_layout', ns['transcript_layout']):
            result = unittest.TestResult()
            ProvenanceLabels('test_receipt_shaped_provider_prose_is_visibly_unverified').run(result)
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.errors, [])
        self.test_receipt_shaped_provider_prose_is_visibly_unverified()
        print('KILLED provider-prose authority label; restored GREEN')


if __name__ == '__main__':
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Blender draw orchestration under injected font metrics, not framebuffer proof."""
import unittest
import test_ai_v8_reachability as reach


class Refusal(reach.Reachability):
    def test_narrow_refusal_is_wrapped_and_has_no_decision_targets(self):
        drawn = []
        self.g['_bug_measure'] = lambda _size: lambda text: len(text)*6
        self.g['_draw_text'] = lambda *args: drawn.append(args)
        self.paint((0, 0, 64, 100))
        view = self.ai['review_view']
        self.assertFalse(view['fits'])
        self.assertIsNone(view['approve'])
        self.assertIsNone(view['reject'])
        self.assertGreater(len(drawn), 1)
        self.assertEqual(' '.join(row[2].strip() for row in drawn), 'Widen panel to review.')
        for x, y, text, _size, _color in drawn:
            self.assertGreaterEqual(y, 5)
            self.assertLessEqual(x+len(text.rstrip())*6, 54)


if __name__ == '__main__':
    reach.MUTATION = ("view['refusal_lines']", "['Widen panel to review.']")
    try:
        result = unittest.TestResult()
        Refusal('test_narrow_refusal_is_wrapped_and_has_no_decision_targets').run(result)
        assert result.failures and not result.errors, (result.failures, result.errors)
        print('KILLED unwrapped refusal mutant')
    finally:
        reach.MUTATION = None
    unittest.main(verbosity=2)

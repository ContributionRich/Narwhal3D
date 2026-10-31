#!/usr/bin/env python3
"""Bounded UI-boundary tests; no Blender, provider, VM, native build or asset dump.

Executes the actual draw/event functions with a recording GPU boundary. This does
not replace real-window visual acceptance. Mutations never edit live source.
"""
import ast
import io
from pathlib import Path
import resource
import sys
import types
import unittest

# This host's Python reserves ~238 MiB of virtual address space at startup;
# that is not resident memory. Keep enough address space for its bounded AST.
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
ROOT = Path(__file__).resolve().parents[1]
PKG = '_v44_panels'
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules[PKG] = pkg
SOURCES = {name: (ROOT / 'narwhal3d' / (name + '.py')).read_text()
           for name in ('textfield', 'ai_assistant', 'ai_presentation', 'bugreport',
                        'rightpanel', 'ui')}
UI_AST = ast.parse(SOURCES['ui'])
UI_FUNCTIONS = {n.name: ast.get_source_segment(SOURCES['ui'], n) for n in UI_AST.body
                if isinstance(n, ast.FunctionDef) and n.name in (
                    '_draw_ai_panel', '_draw_ai_transcript', '_ai_transcript_event', '_ai_review_event',
                    'ai_panel_click', '_shell_chrome_event', '_draw_bug_panel',
                    '_draw_display_panel', '_show_bug_category_menu', '_draw_centered_panel_text')}
del UI_AST


def load(name, mutation=None):
    source = SOURCES[name]
    if mutation and mutation[0] == name:
        old, new = mutation[1:]
        assert source.count(old) == 1, 'nonunique mutation anchor'
        source = source.replace(old, new)
    mod = types.ModuleType(PKG + '.' + name)
    mod.__file__ = str(ROOT / 'narwhal3d' / (name + '.py'))
    mod.__package__ = PKG
    sys.modules[mod.__name__] = mod
    setattr(pkg, name, mod)
    exec(compile(source, mod.__file__, 'exec'), mod.__dict__)
    return mod


MUTATION = None


class PanelTests(unittest.TestCase):
    def setUp(self):
        load('textfield')
        self.ai = load('ai_assistant', MUTATION)
        self.p = load('ai_presentation', MUTATION)
        self.br = load('bugreport', MUTATION)
        self.rp = load('rightpanel', MUTATION)
        self.measure = lambda t: len(t) * 6.0

    def messages(self):
        return [self.ai.ChatMessage(role, text) for role, text in (
            ('assistant', 'You: literal <tag>\nsecond line'),
            ('user', 'Assistant: not a role'), ('status', 'Tool read declined'))]

    def test_roles_alignment_and_literal_text(self):
        layout = self.p.transcript_layout(self.messages(), 208, self.measure)
        a, u, s = layout['items']
        self.assertEqual([i['label'] for i in (a, u, s)], ['AI · unverified', 'You', ''])
        self.assertGreater(u['left'], a['left'])
        self.assertIn('<tag>', ''.join(a['lines']))
        self.assertEqual(''.join(u['lines']), 'Assistant: not a role')
        self.assertEqual(s['bubble'][1], s['top'])
        self.assertGreater(a['bubble'][1], a['top'])

    def test_both_approved_palettes(self):
        self.assertEqual(self.p.PALETTES['light']['user'], (226, 237, 255))
        self.assertEqual(self.p.PALETTES['dark']['canvas'], (41, 41, 41))
        for palette in self.p.PALETTES.values():
            self.assertNotEqual(palette['user'], palette['assistant'])
            self.assertNotEqual(palette['notice'], palette['assistant'])

    def test_empty_is_not_a_demo_conversation(self):
        layout = self.p.transcript_layout([], 208, self.measure)
        self.assertEqual(layout, {'items': [], 'height': 0})

    def test_long_transcript_both_ends_and_collapsed_view(self):
        messages = [self.ai.ChatMessage('assistant', str(i) + '\n' + 'x' * 160)
                    for i in range(100)]
        layout = self.p.transcript_layout(messages, 179, self.measure)
        bottom, offset = self.p.visible_items(layout, (0, 0, 179, 180))
        top, _ = self.p.visible_items(layout, (0, 0, 179, 180), layout['height'])
        self.assertEqual(offset, 0)
        self.assertIs(bottom[-1][0], layout['items'][-1])
        self.assertIs(top[0][0], layout['items'][0])
        self.assertLess(len(bottom), 10)
        self.assertEqual(self.p.visible_items(layout, (0, 0, 179, 0))[0], [])
        self.assertEqual(self.p.scroll_key('END', 999, layout['height'], 180), 0)
        self.assertEqual(self.p.scroll_key('HOME', 0, layout['height'], 180),
                         layout['height'] - 180)

    def test_narrow_wrapping_and_dpi(self):
        for width, scale in ((100, 1), (179, 1), (358, 2)):
            layout = self.p.transcript_layout(self.messages(), width, self.measure, scale)
            for item in layout['items']:
                a, _, b, _ = item['bubble']
                self.assertGreaterEqual(a, 0)
                self.assertLessEqual(b, width)
                self.assertTrue(all(self.measure(line) <= b-a-2*item['padding']+.001
                                    for line in item['lines']))

    def test_panel_metrics_and_composer_hit(self):
        for scale in (1, 2):
            lay = self.ai.panel_layout((0, 0, 208*scale, 700*scale), scale)
            for key, height in (('title', 23), ('connection', 27), ('context', 0),
                                ('input', 86), ('status', 29)):
                self.assertAlmostEqual(lay[key][3]-lay[key][1], height*scale)
            for ident, rect in lay['buttons']:
                self.assertAlmostEqual(rect[3]-rect[1], 32*scale)
                self.assertEqual(self.ai.panel_hit(lay, (rect[0]+rect[2])/2,
                                                   (rect[1]+rect[3])/2), ('button', ident))
            st = self.ai.AssistantState(input_text='hello\nworld', focus=True)
            view = self.ai.input_view(st, lay['input'], self.measure, scale)
            self.assertEqual(view['x0'], lay['input'][0]+10*scale)
            self.ai.input_key(st, 'enter', shift=True)
            self.assertIn('\n', st.input_text)

    def test_inspector_and_display_wrapping(self):
        self.assertEqual([t.icon for t in self.rp.TABS], [
            'generated-panel-' + n + '.png' for n in
            ('layers', 'properties', 'display', 'bug-report', 'ai-assistant')])
        for width in (160, 208, 400):
            lay = self.rp.display_panel_layout((0, 0, width, 700),
                                               ['Top', 'Front', 'Right'], ['Wireframe', 'Shaded', 'Rendered'])
            self.assertEqual(len(lay['modes']), 3)
            for kind, name, x0, y0, x1, y1 in lay['scope']+lay['modes']+lay['viewports']:
                self.assertEqual(y1-y0, 30)
                self.assertGreaterEqual(x0, 11)
                self.assertLessEqual(x1, width-11)
                self.assertEqual(self.rp.display_panel_hit((x0+x1)/2, (y0+y1)/2, lay), (kind, name))
            if width == 160:
                self.assertEqual(len({r[3] for r in lay['modes']}), 3)
            if width == 400:
                self.assertEqual(len({r[3] for r in lay['modes']}), 1)

    def test_report_preserves_categories_and_attachments(self):
        lay = self.br.layout((0, 0, 208, 700), measure=self.measure)
        self.assertNotIn('chips', lay)
        self.assertEqual({c for c, _ in lay['checks']}, {'shot', 'file', 'log'})
        self.assertEqual({b for b, _ in lay['buttons']}, {'submit'})
        rect = lay['category']
        self.assertEqual(rect[0] >= 11 and rect[2] <= 197, True)
        self.assertEqual(rect[3]-rect[1], 31)
        self.assertEqual(self.br.hit(lay, (rect[0]+rect[2])/2, (rect[1]+rect[3])/2), ('category', None))
        submit = lay['buttons'][0][1]
        self.assertEqual((submit[0], submit[2]), (rect[0], rect[2]))

    def test_compact_layout_has_no_overlapping_controls(self):
        for scale in (1, 2):
            for height in (100, 180, 250, 300, 700):
                lay = self.ai.panel_layout((0, 0, 208*scale, height*scale), scale)
                boxes = [rect for name, rect in lay.items() if name != 'buttons']
                boxes += [rect for _, rect in lay['buttons']]
                for index, a in enumerate(boxes):
                    self.assertGreaterEqual(a[1], -0.001)
                    self.assertLessEqual(a[3], height*scale+.001)
                    if a[1] == a[3]:
                        continue
                    for b in boxes[index+1:]:
                        if b[1] == b[3]:
                            continue
                        self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or
                                        a[3] <= b[1] or b[3] <= a[1])

    def test_eviction_keeps_the_visible_message_anchor(self):
        messages = [self.ai.ChatMessage('assistant', f'{i:03}') for i in range(100)]
        old = self.p.transcript_layout(messages, 208, self.measure)
        box = (0, 0, 208, 200)
        visible, _ = self.p.visible_items(old, box, 400)
        anchor, _, before = visible[0]
        new = self.p.transcript_layout(messages[1:]+[self.ai.ChatMessage('assistant', 'new')],
                                       208, self.measure)
        offset = self.p.anchored_offset(old, new, box, box, 400)
        shown, _ = self.p.visible_items(new, box, offset)
        after = next(top for item, _, top in shown if item['message'] is anchor['message'])
        self.assertAlmostEqual(before, after)
        self.assertEqual(self.p.anchored_offset(old, new, box, box, 0), 0)

    def ui(self):
        import importlib
        design = importlib.import_module(PKG + '.design')
        icons = importlib.import_module(PKG + '.ui_icon_data')
        log = []
        gpu = types.ModuleType('gpu')
        clip = [0, 0, 1000, 1000]
        gpu.state = types.SimpleNamespace(
            scissor_get=lambda: tuple(clip),
            scissor_set=lambda *v: clip.__setitem__(slice(None), v),
            scissor_test_set=lambda value: None,
            blend_get=lambda: 'ALPHA', blend_set=lambda value: None)
        sys.modules['gpu'] = gpu
        sys.modules['blf'] = types.SimpleNamespace(size=lambda *_: None,
            dimensions=lambda _font, text: (self.measure(text), 8.0))
        state = self.ai.AssistantState(messages=self.messages())
        context = {'_DEFAULT_FONT_ID': 0, '_ai': {'state': state, 'lay': self.ai.panel_layout((0, 0, 208, 700)),
                           'region': (400, 10)},
            'ai_assistant': self.ai, 'ai_presentation': self.p, 'ui_icon_data': icons,
            'design': design, 'bugreport': self.br, 'textfield': pkg.textfield,
            '_ui_scale': lambda: 1, '_text_width': lambda text, fs: self.measure(text),
            '_bug_measure': lambda fs: self.measure,
            '_fit_panel_text': lambda text, width, fs: text,
            '_inside_box': lambda x,y,b: b[0] <= x < b[2] and b[1] <= y < b[3],
            'ai_panel_open': lambda: True, '_tag_redraw_bands': lambda: None,
            '_draw_text': lambda x,y,text,fs,color: log.append(('text', text, x, y, color)),
            '_draw_round_rect': lambda *args: log.append(('round', args)),
            '_draw_rect': lambda *args: log.append(('rect', args)),
            '_draw_shell_icon': lambda *args: log.append(('icon', args)),
            'selected_display_names': lambda: [],
            '_tool_scroll_event': lambda event: False,
            '_shell_scroll_at': lambda *args: False}
        for name, source in UI_FUNCTIONS.items():
            if MUTATION and MUTATION[0] == 'ui' and MUTATION[1] in source:
                source = source.replace(MUTATION[1], MUTATION[2])
            exec(compile(source, str(ROOT/'narwhal3d/ui.py'), 'exec'), context)
        return context, log, clip

    def test_actual_draw_roles_mark_clipping_and_cache(self):
        ctx, log, clip = self.ui()
        shader = types.SimpleNamespace(bind=lambda: None)
        for theme in ('light', 'dark'):
            ctx['design'].set_theme(theme)
            log.clear()
            ctx['_draw_ai_transcript'](shader, (0,0,208,700), ctx['_ai']['state'], 1)
            texts = [r[1] for r in log if r[0] == 'text']
            self.assertIn('AI · unverified', texts)
            self.assertIn('You', texts)
            self.assertNotIn('Status', texts)
            self.assertEqual([r[1][0] for r in log if r[0] == 'icon'], ['narwhal-ai-mark.png'])
            self.assertEqual(clip, [0,0,1000,1000])
        old = ctx['_ai']['chat_layout']
        ctx['_draw_ai_transcript'](shader, (0,0,208,700), ctx['_ai']['state'], 1)
        self.assertIs(old, ctx['_ai']['chat_layout'])
        ctx['design'].set_theme('light')

    def test_actual_panel_no_false_connection_or_demo(self):
        ctx, log, _ = self.ui()
        state = ctx['_ai']['state']
        state.messages.clear()
        shader = types.SimpleNamespace(bind=lambda: None)
        ctx['_draw_ai_panel'](shader, types.SimpleNamespace(x=0, y=0), {'body': (0,0,208,700)})
        texts = [r[1] for r in log if r[0] == 'text']
        self.assertIn('Choose a model in Settings', texts)
        self.assertFalse(state.messages)
        self.assertNotIn('AI · unverified', texts)

    def test_actual_report_draw_and_category_popup(self):
        ctx, log, _ = self.ui()
        ctx['_bug'] = {'state': self.br.ReportState(), 'region': (10, 20)}
        ctx['sessionlog'] = types.SimpleNamespace(available=lambda: True)
        popup = []
        ctx['shell_popup'] = types.SimpleNamespace(register=lambda: None,
            open=lambda *args, **kw: popup.append((args, kw)))
        shader = types.SimpleNamespace(bind=lambda: None)
        ctx['_draw_bug_panel'](shader, types.SimpleNamespace(x=10, y=20), {'body': (0, 0, 208, 700)})
        texts = [r[1] for r in log if r[0] == 'text']
        self.assertIn('Bug Report', texts)
        self.assertIn('Describe the problem...', texts)
        self.assertIn('Submit report', texts)
        self.assertNotIn('Clear', texts)
        ctx['_show_bug_category_menu']()
        rows, anchor, title, choose = popup[0][0]
        self.assertEqual([(r['action'], r['label']) for r in rows], list(self.br.CATEGORIES))
        self.assertEqual(anchor, (21, 20+ctx['_bug']['lay']['category'][1]))
        for ident, _ in self.br.CATEGORIES:
            choose(ident, None)
            self.assertEqual(ctx['_bug']['state'].category, ident)
        choose('invalid', None)
        self.assertEqual(ctx['_bug']['state'].category, 'missing')
        ctx['_bug']['state'] = self.br.ReportState()
        choose('idea', None)
        self.assertEqual(ctx['_bug']['state'].category, 'bug')

    def test_real_button_label_baselines_are_centered(self):
        ctx, log, _ = self.ui()
        shader = types.SimpleNamespace(bind=lambda: None)
        ctx['_draw_ai_panel'](shader, types.SimpleNamespace(x=0, y=0), {'body': (0,0,300,700)})
        labels = {'send': 'Send', 'new': 'New Chat', 'settings': 'Settings'}
        for ident, rect in ctx['_ai']['lay']['buttons']:
            row = next(r for r in log if r[0] == 'text' and r[1] == labels[ident])
            self.assertAlmostEqual(row[2] + self.measure(row[1])/2, (rect[0]+rect[2])/2)
            self.assertAlmostEqual(row[3] + 4, (rect[1]+rect[3])/2)
        log.clear()
        rect = (11, 11, 289, 43)
        ctx['_draw_centered_panel_text'](rect, 'Submit report', 11, (1,1,1,1))
        row = log[0]
        self.assertAlmostEqual(row[2] + self.measure(row[1])/2, 150)
        self.assertAlmostEqual(row[3] + 4, 27)

    def test_status_survives_new_chat_without_claiming_a_live_socket(self):
        st = self.ai.AssistantState(provider=self.ai.set_provider('openai_compatible', 'fixture'))
        self.assertEqual(self.ai.connection_label(st), 'Ready to send')
        st.extra['last_successful_provider'] = (self.ai.metadata_permission(st.provider), st.provider.model)
        st.new_chat()
        self.assertEqual(self.ai.connection_label(st), 'Ready · Last request succeeded')
        st.provider = self.ai.set_provider('openai_compatible', 'other')
        self.assertEqual(self.ai.connection_label(st), 'Ready to send')
        st.status = 'error'
        self.assertEqual(self.ai.connection_label(st), 'Request failed')

    def test_actual_event_routes_scroll_and_focus(self):
        ctx, _, _ = self.ui()
        state = ctx['_ai']['state']
        ctx['_ai']['chat_layout'] = {'height': 5000}
        rect = ctx['_ai']['lay']['transcript']
        event = types.SimpleNamespace(type='WHEELUPMOUSE', value='PRESS',
                                      mouse_x=450, mouse_y=10+(rect[1]+rect[3])/2)
        self.assertTrue(ctx['_shell_chrome_event'](event))
        self.assertEqual(ctx['_ai']['chat_offset'], 48)
        event.mouse_x = 0
        self.assertFalse(ctx['_ai_transcript_event'](event))
        event.mouse_x = 450
        state.focus = True
        self.assertEqual(ctx['ai_panel_click'](event.mouse_x, event.mouse_y), 'transcript')
        self.assertFalse(state.focus)
        event.type = 'HOME'
        self.assertTrue(ctx['_ai_transcript_event'](event))
        self.assertEqual(ctx['_ai']['chat_offset'], 5000-(rect[3]-rect[1]))
        event.type = 'END'
        self.assertTrue(ctx['_ai_transcript_event'](event))
        self.assertEqual(ctx['_ai']['chat_offset'], 0)
        event.type = 'TAB'
        self.assertTrue(ctx['_ai_transcript_event'](event))
        self.assertTrue(state.focus)
        self.assertFalse(ctx['_ai']['chat_focus'])
        ctx['_ai']['chat_focus'] = True
        event.type, event.mouse_x = 'LEFTMOUSE', 0
        self.assertFalse(ctx['_ai_transcript_event'](event))
        self.assertFalse(ctx['_ai']['chat_focus'])

    def test_pending_review_uses_actual_helper_before_transcript_browsing(self):
        ctx, _, _ = self.ui()
        state = ctx['_ai']['state']
        pending = {}
        ctx['_ai'].update(action_pending=pending, review_drawn_pending=pending,
                          review_view={'fits': True}, chat_layout={'height': 5000}, chat_offset=48)
        rect = ctx['_ai']['lay']['transcript']
        event = types.SimpleNamespace(type='WHEELUPMOUSE', value='PRESS',
                                      mouse_x=450, mouse_y=10+(rect[1]+rect[3])/2)
        self.assertTrue(ctx['_shell_chrome_event'](event))
        self.assertEqual(ctx['_ai']['chat_offset'], 48, 'pending review must not scroll hidden chat')
        state.focus = True
        event.type = 'TAB'
        self.assertTrue(ctx['_ai_transcript_event'](event))
        self.assertEqual(pending.get('keyboard_focus'), 'reject')
        self.assertFalse(state.focus)


MUTATIONS = [
    ('ai_presentation', 'role = message.role', "role = 'assistant'"),
    ('ai_presentation', 'width - inset - outer_width if role == "user" else inset', 'inset'),
    ('ai_presentation', '"assistant": "AI · unverified"', '"assistant": "Narwhal AI"'),
    ('ai_presentation', 'origin = y1 + max(0.0, layout["height"] - height) - offset', 'origin = y1'),
    ('ai_presentation', '"canvas": (41, 41, 41)', '"canvas": (0, 0, 0)'),
    ('ai_assistant', "'buttons': 32*s", "'buttons': 24*s"),
    ('ai_assistant', "'input': 86*s", "'input': 72*s"),
    ('ai_assistant', "if required() <= available:", "if True:"),
    ('ai_presentation', "origin - current['top'] - target", "offset"),
    ('ui', "if not inside and event.type in ('LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'):", "if False:"),
    ('rightpanel', 'DISPLAY_ROW_H = 30', 'DISPLAY_ROW_H = 26'),
    ('rightpanel', 'columns = min(columns, max(1, len(mode_names)))', 'columns = 1'),
    ('bugreport', 'title[1] - gap - 31*s', 'title[1] - gap - 20*s'),
        ('ai_assistant', "'context': 0", "'context': 28*s"),
    ('ui', '_draw_shell_icon(ui_icon_data.AI_REPLY_ICON, avatar, 17 * sc)', 'pass'),
    ('ui', 'gpu.state.scissor_set(*clip)', 'pass'),
    ('ui', 'if _ai_transcript_event(event):', 'if False:'),
    ('ui', 'if _ai_review_event(event):', 'if False:'),
    ('ui', 'state.category = action', 'state.category = "bug"'),
    ('ui', '"submit": "Submit report"', '"submit": "Report"'),
    ('ui', '(rect[1]+rect[3]-height)/2', 'rect[1]+7'),
]


def run():
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PanelTests))
    return result


if __name__ == '__main__':
    result = run()
    if not result.wasSuccessful():
        for case, error in result.failures + result.errors:
            print(case.id(), error[-1800:])
        raise SystemExit(1)
    print(f'{result.testsRun} panel UI-boundary tests PASS (not real-window acceptance)')
    if '--mutations' in sys.argv:
        for index, MUTATION in enumerate(MUTATIONS):
            source = SOURCES[MUTATION[0]]
            if source.count(MUTATION[1]) != 1:
                raise SystemExit(f'mutation {index+1}: missing/nonunique anchor, not a RED result')
            result = run()
            if result.wasSuccessful():
                raise SystemExit(f'mutation {index+1} survived')
            print(f'mutation {index+1}: RED')
        MUTATION = None
        result = run()
        if not result.wasSuccessful():
            raise SystemExit('restored tests failed')
        print(f'{len(MUTATIONS)} isolated mutations RED -> restored GREEN')

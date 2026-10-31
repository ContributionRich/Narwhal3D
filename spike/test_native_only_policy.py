#!/usr/bin/env python3
"""Native-only closure policy. Python tests the existing Python publication boundary.

No Blender, native build, archive import or executable reference engine. Mutations
use in-memory source/subjects or small disposable source trees, never live files.
"""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import audit_public_tree as audit
import create_public_branch as public

DATA_FILES = ('spike/test_ui_core_golden.py', 'spike/test_rust_history_trace.py')
RETIRED = {'LegacyUndoStack', 'LegacyBinding', '_LegacyTextField', 'PopupModel', 'Reference', '_handover',
           '_legacy', '_LegacyCommandHistory', '_bare_value', '_NUMBER_RE', '_LENGTH_RE', '_FEET_INCH_RE'}
PRODUCT_FILES = ('undo', 'native_history', 'document_metadata', 'native_document_session',
                 'textfield', 'native_textfield', 'shell_popup', 'native_popup',
                 'command_transcript', 'native_transcript', 'command_history', 'numparse', 'native_pure')


def source(relative):
    return (ROOT / relative).read_text()


def assert_retired_absent(test, text):
    tree = ast.parse(text)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names.update(node.name for node in ast.walk(tree)
                 if isinstance(node, (ast.ClassDef, ast.FunctionDef)))
    names.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    test.assertFalse(names & RETIRED, names & RETIRED)
    test.assertIsNone(audit.runtime_archive_problem(text.encode()))


def assert_data_only(test, text):
    """Permit literal DATA and its standard decoding, not an oracle hidden in a fixture."""
    tree = ast.parse(text)
    allowed_calls = {'json.loads', 'zlib.decompress', 'base64.b85decode'}
    for statement in tree.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            test.assertIsInstance(statement.value.value, str)
        elif isinstance(statement, ast.Import):
            test.assertTrue(all(a.name in {'json', 'zlib', 'base64'} and a.asname is None
                                for a in statement.names))
        else:
            test.assertIsInstance(statement, ast.Assign)
            test.assertTrue(all(isinstance(t, ast.Name) for t in statement.targets))
            for node in ast.walk(statement.value):
                test.assertIsInstance(node, (ast.Call, ast.Attribute, ast.Name, ast.Load, ast.Constant))
                if isinstance(node, ast.Call):
                    test.assertIn(ast.unparse(node.func), allowed_calls)
                    test.assertFalse(node.keywords)


def prefix_namespace():
    """Load only the visibility driver's CLI helpers; never its Blender phase."""
    text = source('spike/test_selection_visibility_07.py')
    prefix = text.split("if '--layout-only' in sys.argv:", 1)[0]
    ns = {'__file__': str(ROOT / 'spike/test_selection_visibility_07.py')}
    exec(compile(prefix, ns['__file__'], 'exec'), ns)
    return ns


class PolicyTests(unittest.TestCase):
    def test_archive_and_build_scratch_are_not_python_analysis_inputs(self):
        import json
        excluded = json.loads(source('pyrightconfig.json'))['exclude']
        for path in ('legacy', 'tmp', 'native/**/target'):
            self.assertIn(path, excluded)

    def test_root_archive_is_ignored(self):
        self.assertIn('/legacy/', source('.gitignore').splitlines())
        # Exercise Git's actual matching semantics without touching its index.
        with tempfile.TemporaryDirectory(prefix='native-policy-') as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True, timeout=10)
            (root / '.gitignore').write_text(source('.gitignore'))
            result = subprocess.run(['git', '--no-pager', '-C', str(root), 'check-ignore', '--no-index', '--stdin'],
                                    input='legacy/retired-python-old/closure/MANIFEST.json\n',
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('legacy/retired-python-old/', result.stdout)

    def test_source_and_app_payload_paths_are_rejected(self):
        for path in ('legacy/x.py', 'legacy/retired-python-old/ui/notes.md',
                     'narwhal3d/legacy/x.py', 'app/narwhal3d/legacy/x.py',
                     'usr/lib/narwhal3d/legacy/x.py', 'Contents/Resources/app/legacy/x.py',
                     'runtime/linux-x86_64/bin/retired-python-old/core.so',
                     'app/Legacy.zip', 'spike/fixtures/textfield_168212d4.py'):
            with self.subTest(path=path):
                self.assertIsNotNone(audit.retirement_path_problem(path))
                self.assertTrue(audit.audit_entries({path: ('100644', b'safe\n')}, require_manifest=False))
        for path in ('narwhal3d/native_textfield.py', *DATA_FILES):
            self.assertIsNone(audit.retirement_path_problem(path))

    def test_runtime_archive_imports_and_artifacts_are_rejected(self):
        for blob in (b'from legacy import old\n', b'import legacy.old\n',
                     b'from . import legacy\n', b'p = root / "legacy" / "retired-python-old"\n',
                     b'p = "legacy/retired-python-old/undo.py"\n',
                     b'\x7fELF\0legacy/retired-python-old/native.so\0'):
            with self.subTest(blob=blob):
                self.assertIsNotNone(audit.runtime_archive_problem(blob))
                self.assertTrue(audit.audit_entries({'app/narwhal3d/native/payload.so': ('100644', blob)},
                                                   require_manifest=False))
        self.assertIsNone(audit.runtime_archive_problem(b'from . import native_history\n'))
        self.assertIsNone(audit.runtime_archive_problem(b'\x7fELF\0normal artifact\0'))

    def test_product_has_no_retired_engine_or_archive_resolver(self):
        for name in PRODUCT_FILES:
            with self.subTest(module=name):
                assert_retired_absent(self, source('narwhal3d/' + name + '.py'))
        # Include every live module, not just the retired ownership seams.
        for path in (ROOT / 'narwhal3d').rglob('*'):
            self.assertIsNone(audit.retirement_path_problem(path.relative_to(ROOT).as_posix()), str(path))
            if path.suffix == '.py':
                self.assertIsNone(audit.runtime_archive_problem(path.read_bytes()), str(path))
        self.assertFalse((ROOT / 'spike/fixtures/textfield_168212d4.py').exists())
        for name in ('test_rust_history_abi', 'test_rust_history_profile', 'test_ui_core_editor'):
            assert_retired_absent(self, source('spike/' + name + '.py'))

    def test_admission_cannot_return_optional_backend(self):
        for name, functions in (
            ('native_document_session', ('load_library',)),
            ('native_history', ('try_create', '_configure')),
            ('native_textfield', ('library',)), ('native_popup', ('library', 'model')),
            ('native_transcript', ('library',)), ('native_pure', ('_required_parse_binding',)),
        ):
            tree = ast.parse(source('narwhal3d/' + name + '.py'))
            for function in functions:
                node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
                for ret in (n for n in ast.walk(node) if isinstance(n, ast.Return)):
                    self.assertFalse(ret.value is None or isinstance(ret.value, ast.Constant)
                                     and ret.value.value is None, (name, function, 'optional backend'))
        tree = ast.parse(source('narwhal3d/undo.py'))
        facade = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UndoStack')
        init = next(n for n in facade.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        assignment = next(n for n in init.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Attribute) and t.attr == '_backend' for t in n.targets))
        self.assertEqual(ast.unparse(assignment.value), '_native_backend(limit)')

    def test_public_fixtures_are_data_not_reference_engines(self):
        for path in DATA_FILES:
            with self.subTest(path=path):
                assert_data_only(self, source(path))
                self.assertIn(path, public.TEST_FILES)
                self.assertIn(path, audit.REQUIRED)
        retired = 'spike/fixtures/textfield_168212d4.py'
        self.assertNotIn(retired, public.TEST_FILES)
        self.assertNotIn(retired, audit.REQUIRED)
        for path in ('native/pure_core/src/numparse_owned.rs', 'spike/test_numparse_owned.py'):
            self.assertIn(path, public.NATIVE_FILES + public.TEST_FILES)
            self.assertIn(path, audit.REQUIRED)
        self.assertIn('docs/reviews/2026-10-31-native-only.md', public.ROOT_FILES)

    def test_numeric_json_data_has_reviewed_provenance(self):
        path = 'spike/fixtures/numparse_golden.json'
        blob = (ROOT / path).read_bytes()
        provenance = audit.FROZEN_JSON_FILES[path]
        self.assertEqual(provenance['origin'], '50267dd6d4504b14052d1ab7e54675f3c8750637')
        self.assertEqual(hashlib.sha256(blob).hexdigest(), provenance['sha256'])
        self.assertEqual({key: len(rows) for key, rows in json.loads(blob).items()}, provenance['rows'])
        self.assertIn(path, public.TEST_FILES)
        self.assertIn(path, audit.REQUIRED)
        self.assertEqual(json.loads(audit.frozen_json_scan(path, blob)), provenance)
        with self.assertRaisesRegex(ValueError, 'provenance hash'):
            audit.frozen_json_scan(path, blob + b' ')
        # Exercise schema validation independently from byte provenance.
        wrong = json.dumps({'15.1.0': 'not rows', '16.0.0': []}).encode()
        with patch.dict(audit.FROZEN_JSON_FILES, {path: {**provenance, 'sha256': hashlib.sha256(wrong).hexdigest()}}):
            with self.assertRaisesRegex(ValueError, 'row count'):
                audit.frozen_json_scan(path, wrong)

    def test_finished_lane_public_dependencies(self):
        import build_linux_native as packaging
        selected = set(public.TEST_FILES + public.NATIVE_FILES + public.LAUNCH_FILES)
        self.assertEqual(len(packaging.UI_CORE_EXPORTS), 18)
        self.assertEqual(len(packaging.UI_CORE_REQUIRED_SOURCE), 21)
        for path in ('native/ui_core/' + name for name in packaging.UI_CORE_REQUIRED_SOURCE):
            self.assertIn(path, selected)
            self.assertIn(path, audit.REQUIRED)
        for stem in ('abi', 'model', 'consumers', 'stress', 'gui', 'unicode', 'mutations', 'benchmark'):
            path = 'spike/test_ui_transcript_' + stem + '.py'
            self.assertIn(path, selected)
            self.assertIn(path, audit.REQUIRED)
        for stem in ('exit', 'reachability', 'presentation', 'long_session', 'plans', 'lifetime',
                     'lifetime_gui', 'settings', 'visible', 'x11', 'native'):
            path = 'spike/test_ai_v8_' + stem + '.py'
            self.assertIn(path, selected)
            self.assertIn(path, audit.REQUIRED)
        for path in ('launch/check_native.py', 'spike/test_desktop_native_startup.py',
                     'spike/test_numparse_owned.py', 'spike/test_pure_core_packaging.py'):
            self.assertIn(path, selected)
            self.assertIn(path, audit.REQUIRED)

    def test_owner_code_normalization_is_syntactic_and_file_scoped(self):
        # Construct policy-negative tokens so this test source receives the same audit.
        subjects = {
            'narwhal3d/native_transcript.py': b'class subject:\n    pass\n'.replace(b'subject', b'Owner'),
            'spike/test_ui_transcript_model.py': b'with manager() as subject:\n    pass\n'.replace(b'subject', b'owner'),
            'native/ui_core/src/transcript_ffi.rs': (
                b'struct Snapshot {\n    subject: u64,\n}\n'
                b'fn reply(handle: u64, subject: &Transcript) -> Reply { todo!() }\n'
                b'fn store() { let s = Snapshot {\n    subject: handle,\n}; }\n').replace(b'subject', b'owner'),
        }
        for path, blob in subjects.items():
            with self.subTest(path=path):
                self.assertEqual(audit.audit_entries({path: ('100644', blob)}, require_manifest=False), [])
                self.assertEqual(len(audit.code_provenance_scan(path, blob)), len(blob))
                other = 'unapproved.py' if path.endswith('.py') else 'unapproved.rs'
                self.assertTrue(audit.audit_entries({other: ('100644', blob)}, require_manifest=False))

    def test_owner_comments_strings_and_unapproved_syntax_remain_private(self):
        for path, sources in {
            'narwhal3d/native_transcript.py': (
                b'# class subject:\n', b'text = "class subject:"\n',
                b'class subject:\n    """subject: private attribution"""\n',
                b'class subject:  # subject: private attribution\n    pass\n',
                b'subject: Base\n'),
            'spike/test_ui_transcript_model.py': (
                b'# with manager() as subject:\n', b'text = "as subject:"\n',
                b'with manager() as subject:  # subject: private attribution\n    pass\n',
                b'def unrelated(subject: int):\n    pass\n'),
            'native/ui_core/src/transcript_ffi.rs': (
                b'// subject: u64,\n', b'/* struct Snapshot {\n subject: u64,\n} */',
                b'/* outer /* nested */ struct Snapshot { subject: u64, } */',
                b'const X: &str = "struct Snapshot { subject: u64, }";',
                b'const X: &str = r###"struct Snapshot { subject: u64, }"###;',
                b'const X: &[u8] = br#"fn reply(handle: u64, subject: &Transcript) -> Reply { }"#;',
                b'struct Snapshot { subject: u64, // subject: private attribution\n}',
                b'struct Other { subject: u64, }', b'struct Snapshot { subject: i32, }',
                b'fn other(handle: u64, subject: &Transcript) -> Reply { }',
                b'fn store() { let s = Snapshot { subject: other_handle, }; }'),
        }.items():
            for blob in sources:
                blob = blob.replace(b'subject', b'Owner' if path == 'narwhal3d/native_transcript.py' else b'owner')
                with self.subTest(path=path, blob=blob):
                    problems = audit.audit_entries({path: ('100644', blob)}, require_manifest=False)
                    self.assertTrue(any('private/legacy marker' in p for p in problems), problems)

    def test_ui_documentation_describes_mandatory_native_policy(self):
        text = source('native/ui_core/README.md')
        self.assertIn('Editor, popup and transcript require native support', text)
        self.assertIn('all 18 editor/popup/transcript exports', text)
        self.assertIn('invalidates an older source-pinned receipt', text)
        for retired_claim in ('complete original Python implementation remains the default',
                              'use one-way atomic legacy handover', 'Other Unicode versions use a named complete legacy path',
                              'Python hands over oversized text operations'):
            self.assertNotIn(retired_claim, text)

    def test_script_helper_import_closure_is_not_a_phantom_spike_file(self):
        entries = {'spike/test_ui_core_gate.py': (
            '100644', b'from test_release_ui_core import add_transcript_fixture\n')}
        required = audit.required_public_paths(entries)
        self.assertIn('scripts/test_release_ui_core.py', required)
        self.assertNotIn('spike/test_release_ui_core.py', required)
        problems = audit.audit_entries(entries)
        self.assertIn('missing required public file: scripts/test_release_ui_core.py', problems)

    def test_finished_lane_harness_order_and_modes(self):
        text = source('run_tests.sh')
        ui = text.index('# UI core source qualification')
        for marker in ('scripts/check_pure_core.py', 'scripts/check_application_core.py --stage-dev',
                       'export NARWHAL_PURE_CORE=', 'export NARWHAL_APPLICATION_CORE='):
            self.assertLess(text.index(marker), ui)
        self.assertNotIn('--mutations', text)
        self.assertIn('for suite in abi model consumers stress;', text)
        self.assertIn('spike/test_desktop_native_startup.py', text)
        self.assertIn('spike/test_ai_v8_exit.py', text)
        self.assertIn('spike/test_ai_v8_long_session.py', text)
        gui = text.index('# End UI core GUI drivers.')
        self.assertGreater(text.index('spike/test_ui_transcript_gui.py --launch'), gui)
        self.assertIn('for close_case in close_review close_request;', text)
        self.assertIn('--case "$close_case" --ui-library "$UI_CORE_LIBRARY" --ui-sha256 "$UI_CORE_SHA256"', text)
        self.assertLess(text.index('scripts/check_ui_core.py --library', gui), text.index('for close_case'))
        # New drivers use the same actual receipt target as the fixture builder.
        helper = ast.parse(source('spike/test_ai_accepted_review_native.py'))
        target = next(n.value.args[0].value for n in helper.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'TARGET' for t in n.targets))
        self.assertIn('AI_REVIEW_TARGET="' + target + '"', text)
        self.assertNotIn('test_ui_transcript_mutations.py', text)
        self.assertNotIn('spike/test_ai_v8_native.py', text)

    def test_audit_data_literal_is_not_a_file_exemption(self):
        path = DATA_FILES[0]
        # A task-like token in compressed data is not provenance prose.
        token = 'g' + '-9'
        safe = ('import json\nDATA = json.loads(' + repr(token) + ')\n').encode()
        self.assertEqual(audit.audit_entries({path: ('100644', safe)}, require_manifest=False), [])
        for extra in (b'\ndef reference(): return 1\n', b'\nDATA = dangerous()\n',
                      ('\n# ' + token + '\n').encode()):
            self.assertTrue(audit.audit_entries({path: ('100644', safe + extra)}, require_manifest=False))
        unsafe = b'import os\nDATA = os.system("do not execute")\n'
        self.assertTrue(audit.audit_entries({path: ('100644', unsafe)}, require_manifest=False))

    def test_public_selection_rejects_nested_archive_without_copying_root_archive(self):
        with tempfile.TemporaryDirectory(prefix='native-publish-') as tmp:
            root = Path(tmp)
            for path in ('narwhal3d', 'packaging', 'licenses', '.github', 'artwork', 'native/vendor/tracedraw', 'legacy'):
                (root / path).mkdir(parents=True)
            (root / 'legacy/old.py').write_text('local only\n')
            lists = {key: () for key in ('ROOT_FILES', 'LAUNCH_FILES', 'WELCOME_FILES', 'NATIVE_FILES',
                                        'RUNTIME_FILES', 'PATCH_FILES', 'SCRIPT_FILES', 'TEST_FILES')}
            with patch.multiple(public, ROOT=root, **lists):
                self.assertEqual(public.public_files(), [])
                contamination = root / 'narwhal3d/legacy'
                contamination.mkdir()
                (contamination / 'old.py').write_text('local only\n')
                with self.assertRaisesRegex(public.PublicBranchError, 'retired payload'):
                    public.public_files()
                (contamination / 'old.py').unlink()
                # Explicit allowlist additions cannot bypass recursive selection checks.
                with patch.object(public, 'ROOT_FILES', ('legacy/old.py',)):
                    with self.assertRaisesRegex(public.PublicBranchError, 'retired payload'):
                        public.public_files()

    def test_gui_event_and_framebuffer_assertions_are_unchanged(self):
        # Frozen AST digest from the pre-change source: no archive dependency.
        names = {'check', 'finish', 'window', 'event', 'tap', 'snapshot', 'unchanged',
                 'screen_point', 'png', 'capture', 'roi', 'near_color', 'visible',
                 'row_point', 'open_at', 'hover', 'shielded_since', 'accept', 'cancel', 'run', 'tick'}
        nodes = [n for n in ast.parse(source('spike/test_selection_visibility_07.py')).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        digest = hashlib.sha256('\n'.join(ast.dump(n, include_attributes=False) for n in nodes).encode()).hexdigest()
        self.assertEqual(digest, '449a2a95e68d1b3e7ea32de1277769d6b1ce52641b0558aaddea42affe38dee5')

    def test_required_cores_precede_blender_callers(self):
        text = source('run_tests.sh')
        first = text.index('run "Rust selection adapter/UI"')
        for marker in ('export NARWHAL_UI_CORE_LIBRARY=', 'export NARWHAL_APPLICATION_CORE=',
                       'export NARWHAL_HISTORY_CORE='):
            self.assertLess(text.index(marker), first)
        self.assertEqual(text.count('scripts/check_application_core.py --stage-dev'), 1)
        self.assertEqual(text.count('spike/test_native_only_policy.py'), 1)

    def test_retired_scroll_mutation_refuses_before_launch_or_import(self):
        ns = prefix_namespace()
        for mode, function in (('--launch', 'launch'), ('--layout-only', 'layout_only')):
            with self.subTest(mode=mode), patch.object(sys, 'argv', ['visibility', mode, '--mutation', 'no_scroll_tolerance']), \
                    patch.object(Path, 'mkdir', side_effect=AssertionError('scratch before refusal')), \
                    patch.object(subprocess, 'run', side_effect=AssertionError('launch before refusal')), \
                    patch('importlib.util.spec_from_file_location', side_effect=AssertionError('import before refusal')):
                with self.assertRaisesRegex(RuntimeError, 'retired mutation mode no_scroll_tolerance'):
                    ns[function]()
        layout = ast.unparse(next(n for n in ast.parse(source('spike/test_selection_visibility_07.py')).body
                                 if isinstance(n, ast.FunctionDef) and n.name == 'layout_only'))
        self.assertIn('module.NativePopup(', layout)
        self.assertNotIn('PopupModel', layout)


class MutationTests(unittest.TestCase):
    def test_policy_mutants_are_assertion_red_and_restored_green(self):
        cases = (
            ('syntactic identifier recognition removed', patch.object(audit, 'code_provenance_scan', side_effect=lambda p, b: b),
             'test_owner_code_normalization_is_syntactic_and_file_scoped'),
            ('whole-file privacy exemption', patch.object(audit, 'code_provenance_scan', return_value=b''),
             'test_owner_comments_strings_and_unapproved_syntax_remain_private'),
            ('optional fallback documentation restored', patch.dict(globals(), source=lambda p: original(p) + '\ncomplete original Python implementation remains the default\n' if p == 'native/ui_core/README.md' else original(p)),
             'test_ui_documentation_describes_mandatory_native_policy'),
            ('script helper resolved to wrong subtree', patch.object(audit, 'TEST_SUPPORT_IMPORTS', {}),
             'test_script_helper_import_closure_is_not_a_phantom_spike_file'),
            ('numeric DATA hash bypass', patch.object(audit, 'frozen_json_scan', side_effect=lambda p, b: b),
             'test_numeric_json_data_has_reviewed_provenance'),
            ('transcript source omitted', patch.object(public, 'NATIVE_FILES', tuple(p for p in public.NATIVE_FILES if p != 'native/ui_core/src/transcript.rs')),
             'test_finished_lane_public_dependencies'),
            ('transcript fallback restored', patch.dict(globals(), source=lambda p: original(p) + '\n_legacy = []\n' if p == 'narwhal3d/command_transcript.py' else original(p)),
             'test_product_has_no_retired_engine_or_archive_resolver'),
            ('close-request coverage omitted', patch.dict(globals(), source=lambda p: original(p).replace('for close_case in close_review close_request;', 'for close_case in close_review;') if p == 'run_tests.sh' else original(p)),
             'test_finished_lane_harness_order_and_modes'),
            ('global mutation mode restored', patch.dict(globals(), source=lambda p: original(p) + '\nrun "bad" "$PYTHON" test.py --mutations\n' if p == 'run_tests.sh' else original(p)),
             'test_finished_lane_harness_order_and_modes'),
            ('archive path admitted', patch.object(audit, 'retirement_path_problem', return_value=None),
             'test_source_and_app_payload_paths_are_rejected'),
            ('DATA exemption hides code', patch.object(audit, 'frozen_data_scan', return_value=b''),
             'test_audit_data_literal_is_not_a_file_exemption'),
            ('publisher archive admitted', patch.object(audit, 'retirement_path_problem', return_value=None),
             'test_public_selection_rejects_nested_archive_without_copying_root_archive'),
            ('runtime archive admitted', patch.object(audit, 'runtime_archive_problem', return_value=None),
             'test_runtime_archive_imports_and_artifacts_are_rejected'),
            ('root ignore removed', patch.dict(globals(), source=lambda p: original(p).replace('/legacy/\n', '') if p == '.gitignore' else original(p)),
             'test_root_archive_is_ignored'),
            ('data fixture unregistered', patch.object(public, 'TEST_FILES', tuple(p for p in public.TEST_FILES if p != DATA_FILES[0])),
             'test_public_fixtures_are_data_not_reference_engines'),
            ('alternate engine restored', patch.dict(globals(), source=lambda p: original(p) + '\nclass LegacyUndoStack: pass\n' if p == 'narwhal3d/undo.py' else original(p)),
             'test_product_has_no_retired_engine_or_archive_resolver'),
            ('optional history admission', patch.dict(globals(), source=lambda p: original(p).replace('return NativeUndoStack(limit, library=library)', 'return None') if p == 'narwhal3d/native_history.py' else original(p)),
             'test_admission_cannot_return_optional_backend'),
            ('executable golden oracle', patch.dict(globals(), source=lambda p: original(p) + '\ndef reference(): return 1\n' if p == DATA_FILES[0] else original(p)),
             'test_public_fixtures_are_data_not_reference_engines'),
            ('framebuffer witness weakened', patch.dict(globals(), source=lambda p: original(p).replace('def visible(', 'def invisible(') if p == 'spike/test_selection_visibility_07.py' else original(p)),
             'test_gui_event_and_framebuffer_assertions_are_unchanged'),
            ('scroll mutant becomes no-op', patch.dict(globals(), source=lambda p: original(p).replace("    raise RuntimeError(\n        'retired mutation mode", "    return (\n        'retired mutation mode") if p == 'spike/test_selection_visibility_07.py' else original(p)),
             'test_retired_scroll_mutation_refuses_before_launch_or_import'),
        )
        original = source
        for label, mutation, test in cases:
            with self.subTest(mutant=label):
                with mutation:
                    result = unittest.TestResult()
                    PolicyTests(test).run(result)
                self.assertTrue(result.failures, (label, result.errors))
                self.assertFalse(result.errors, (label, result.errors))
                restored = unittest.TestResult()
                PolicyTests(test).run(restored)
                self.assertTrue(restored.wasSuccessful(), (label, restored.errors, restored.failures))
                print('RED mutant / GREEN restored:', label)


if __name__ == '__main__':
    unittest.main()

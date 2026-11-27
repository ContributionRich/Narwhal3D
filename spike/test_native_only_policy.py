#!/usr/bin/env python3
"""Retained visibility/host checks and mutation injection transport.

Rust owns migrated source assertions and publication/audit policy. The source-pinned
CLI builds offline when needed. No Blender phase or retired engine is imported.
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
import source_tools as audit
import source_tools as public

DATA_FILES = ('native/ui_core/tests/data/host_ui31.json',)
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



def prefix_namespace():
    """Load only the visibility driver's CLI helpers; never its Blender phase."""
    text = source('spike/test_selection_visibility_07.py')
    prefix = text.split("if '--layout-only' in sys.argv:", 1)[0]
    ns = {'__file__': str(ROOT / 'spike/test_selection_visibility_07.py')}
    exec(compile(prefix, ns['__file__'], 'exec'), ns)
    return ns


class PolicyTests(unittest.TestCase):
    def native_case(self, name):
        # Transport existing source-injection controls to native assertions; no policy here.
        paths = audit._rpc('policy_inputs', name=name)
        try:
            result = audit._rpc('policy_check', name=name, sources={p: source(p) for p in paths})
        except audit.AuditError as error:
            self.fail(str(error))
        self.assertTrue(result['ok'], result['failures'])

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
        self.native_case('product_has_no_retired_engine_or_archive_resolver')

    def test_retired_public_oracles_are_data_and_rust_only(self):
        self.native_case('retired_public_oracles_are_data_and_rust_only')

    def test_history_acceptance_is_native_and_host_replay_is_retained(self):
        self.native_case('history_acceptance_is_native_and_host_replay_is_retained')

    def test_blend_host_has_no_embedded_numeric_reference(self):
        self.native_case('blend_host_has_no_embedded_numeric_reference')

    def test_reference_decoders_cannot_restore_retired_engines(self):
        self.native_case('reference_decoders_cannot_restore_retired_engines')

    def test_width_format_macro_python_engines_are_retired(self):
        self.native_case('width_format_macro_python_engines_are_retired')

    def test_string_planner_does_not_replay_public_token_builder(self):
        self.native_case('string_planner_does_not_replay_public_token_builder')

    def test_admission_cannot_return_optional_backend(self):
        self.native_case('admission_cannot_return_optional_backend')

    def test_public_fixtures_are_data_not_reference_engines(self):
        for path in DATA_FILES:
            with self.subTest(path=path):
                try:
                    data = json.loads(source(path))
                except ValueError as error:
                    self.fail(f'captured data must be non-executable JSON: {error}')
                self.assertIsInstance(data, dict)
                self.assertIn(path, public.NATIVE_FILES)
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
        self.native_case('finished_lane_public_dependencies')

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
        for path in ('native/application_core/src/live_objects.rs',
                     'native/application_core/src/object_registry.rs',
                     'native/application_core/src/object_registry_tests.rs',
                     'native/application_core/tests/object_registry_protocol.rs'):
            subjects[path] = (b'fn check(subject: u64) {}\nfn close(subject: &Value) {}\n'
                              b'let r = Request { subject: owner_id };\n').replace(b'subject', b'owner')
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
            'native/application_core/src/object_registry.rs': (
                b'// subject: u64\n', b'/* subject: u64 */',
                b'const NOTE: &str = "subject: private";',
                b'const NOTE: &str = r#"subject: private"#;',
                b'fn check(subject: u64) {} // subject: private\n',
                b'struct Other { subject: Person }'),
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
        self.native_case('ui_documentation_describes_mandatory_native_policy')

    def test_script_helper_import_closure_is_not_a_phantom_spike_file(self):
        entries = {'spike/test_ui_core_gate.py': (
            '100644', b'from test_release_ui_core import add_transcript_fixture\n')}
        required = audit.required_public_paths(entries)
        self.assertIn('scripts/test_release_ui_core.py', required)
        self.assertNotIn('spike/test_release_ui_core.py', required)
        problems = audit.audit_entries(entries)
        self.assertIn('missing required public file: scripts/test_release_ui_core.py', problems)

    def test_finished_lane_harness_order_and_modes(self):
        self.native_case('finished_lane_harness_order_and_modes')

    def test_audit_data_literal_is_not_a_file_exemption(self):
        path = 'spike/ui_golden_data.py'
        token = 'g' + '-9'
        safe = b'import json\nDATA = json.loads("[]")\n'
        self.assertEqual(audit.frozen_data_scan(path, safe), safe)
        self.assertEqual(audit.audit_entries({path: ('100644', safe)}, require_manifest=False), [])
        marked = safe + ('\n# ' + token + '\n').encode()
        self.assertEqual(audit.frozen_data_scan(path, marked), marked)
        self.assertTrue(audit.audit_entries({path: ('100644', marked)}, require_manifest=False))
        for retired in ('spike/test_ui_core_golden.py', 'spike/test_rust_history_trace.py'):
            self.assertIsNotNone(audit.retirement_path_problem(retired))
            self.assertFalse((ROOT / retired).exists())
            self.assertNotIn(retired, public.TEST_FILES)

    def test_public_selection_rejects_nested_archive_without_copying_root_archive(self):
        with tempfile.TemporaryDirectory(prefix='native-publish-') as tmp:
            root = Path(tmp)
            for path in ('narwhal3d', 'packaging', 'licenses', '.github', 'artwork', 'native/vendor/tracedraw', 'native/picture_trace', 'legacy'):
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
        # Re-pinned once: `run` now answers DisableOsnap's measured "New value" prompt with
        # Toggle (research_preview_14 R7). No event, framebuffer or check line changed.
        self.assertEqual(digest, '330c7606fc7e1eb6e058fd3b2473a12a6074ca6f78da40fdba247506e75694fa')

    def test_required_cores_precede_blender_callers(self):
        self.native_case('required_cores_precede_blender_callers')

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
        # Three native producer controls now live in source_tools/tests/mutations.rs.
        cases = (
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
            ('automation suite omitted', patch.dict(globals(), source=lambda p: original(p).replace('for suite in box plan v4 v6 v7 v8; do', 'for suite in box plan v4 v6 v7; do') if p == 'run_tests.sh' else original(p)),
             'test_finished_lane_harness_order_and_modes'),
            ('global mutation mode restored', patch.dict(globals(), source=lambda p: original(p) + '\nrun "bad" "$PYTHON" test.py --mutations\n' if p == 'run_tests.sh' else original(p)),
             'test_finished_lane_harness_order_and_modes'),
            ('archive path admitted', patch.object(audit, 'retirement_path_problem', return_value=None),
             'test_source_and_app_payload_paths_are_rejected'),
            ('DATA exemption hides code', patch.object(audit, 'frozen_data_scan', return_value=b''),
             'test_audit_data_literal_is_not_a_file_exemption'),
            ('runtime archive admitted', patch.object(audit, 'runtime_archive_problem', return_value=None),
             'test_runtime_archive_imports_and_artifacts_are_rejected'),
            ('root ignore removed', patch.dict(globals(), source=lambda p: original(p).replace('/legacy/\n', '') if p == '.gitignore' else original(p)),
             'test_root_archive_is_ignored'),
            ('data fixture unregistered', patch.object(public, 'NATIVE_FILES', tuple(p for p in public.NATIVE_FILES if p != DATA_FILES[0])),
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

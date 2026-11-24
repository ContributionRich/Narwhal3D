#!/usr/bin/env python3
"""Audit a Narwhal 3D source tree or Git ref before public release.

This is a technical provenance gate, not legal advice. It prevents known private paths,
legacy product assets, commercial extraction tools, unexpected images, and personal
identifiers from entering the public root commit.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import tokenize

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "native/automation_test_driver/Cargo.lock",
    "native/automation_test_driver/Cargo.toml",
    "native/automation_test_driver/README.md",
    "native/automation_test_driver/src/additional.rs",
    "native/automation_test_driver/src/additional_catalog_provenance_v1.json",
    "native/automation_test_driver/src/additional_catalog_v1.json",
    "native/automation_test_driver/src/anchor_overrides_v1.json",
    "native/automation_test_driver/src/catalog.json",
    "native/automation_test_driver/src/catalog.rs",
    "native/automation_test_driver/src/discovery.rs",
    "native/automation_test_driver/src/fixtures.rs",
    "native/automation_test_driver/src/lib.rs",
    "native/automation_test_driver/src/main.rs",
    "native/automation_test_driver/src/overrides.rs",
    "native/automation_test_driver/tests/cli.rs",
    "native/automation_test_driver/tests/registration.rs",
    'native/pure_core/tests/interpolate32_properties.rs',
    'native/welcome/Cargo.toml',
    'native/welcome/Cargo.lock',
    'native/welcome/src/main.rs',
    'native/welcome/src/native_stacking.rs',
    'native/welcome/src/theme.rs',
    'packaging/windows/portable/Cargo.toml',
    'packaging/windows/portable/Cargo.lock',
    'packaging/windows/portable/src/cache.rs',
    'packaging/windows/portable/src/lib.rs',
    'packaging/windows/portable/src/main.rs',
    'packaging/windows/portable/src/pack.rs',
    'packaging/windows/portable/src/payload.rs',
    'packaging/windows/portable/src/payload_tests.rs',
    'packaging/windows/portable/src/update.rs',
    'packaging/windows/portable/src/windows.rs',

    'native/welcome/src/linux_recovery.rs',
    'packaging/windows/portable/src/install.rs',
    'packaging/windows/portable/src/shortcut.rs',
    'packaging/windows/portable/src/startup.rs',

    'spike/test_document_correspondence.py',
    'spike/test_document_native_encoding_identity.py',
    'spike/test_document_save_repair.py',
    'spike/test_boolean_delete_input.py',
    'spike/test_x11_errors.py',
    'spike/test_document_save_never_blocked.py',
    'spike/test_save_queue.py',
    'spike/test_crash_recovery.py',
    'spike/test_brep_pick_culling.py',
    'spike/test_extrude_mixed_profiles.py',
    'spike/test_fillet_chain_edges.py',
    'spike/test_group_copy.py',
    'narwhal3d/x11_errors.py',
    'spike/test_document_runtime_identity.py',
    'native/document_observer/lifetime_fixture.py',
    'native/document_observer/qualify_lifetime.py',

    'spike/test_right_dock_rmb_gui.py',
    'spike/test_project_to_cplane_delete.py',
    'spike/test_project_to_cplane_gui.py',
    'spike/test_offset_fit_native.py',
    'spike/test_line_samples.py',
    'spike/test_polyline_samples.py',
    'spike/test_polycurve_samples.py',
    'spike/test_offset_compact.py',
    'spike/test_offset_report127.py',
    'spike/test_trim_split_once.py',
    'spike/test_trim_report128.py',
    'spike/test_snap_source_memo.py',
    'spike/test_trim_live_performance_gui.py',
    'spike/test_report121_124.py',
    'spike/test_report121_124_gui.py',
    'spike/test_extrusion_creases.py',
    'spike/test_document_admission.py',
    'spike/test_document_shape_summary.py',
    'spike/test_document_worker_bounds.py',
    'spike/test_document_exact_work.py',
    'native/document_observer/test_master_lifetime.py',
    'native/document_observer/test_master_lifetime_admission.py',
    'native/document_observer/test_master_lifetime_product.py',
    'spike/snap_clip_abi_witness.c',

    ".gitignore",
    "LICENSE",
    "README.md",
    "CONTRIBUTING.md",
    "STATUS.md",
    "COMPATIBILITY.md",
    "ROADMAP.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "SUPPORT.md",
    "GOVERNANCE.md",
    "CODE_OF_CONDUCT.md",
    "THIRD_PARTY_NOTICES",
    "docs/readme-command-loft.gif",
    "docs/images/bugreport.png",
    "docs/gaps.md",
    "docs/github-project.md",
    "docs/contributing-with-ai.md",
    "docs/windows-build.md",
    "docs/macos-build.md",
    "artwork/icons/README.md",
    "narwhal3d.sh",
    "run_tests.sh",
    "uishot.sh",
    "Build-Narwhal3D-macOS.command",
    "narwhal3d/commands.py",
    "scripts/create_public_branch.py",
    "scripts/collect_windows_build_info.ps1",
    "scripts/build_windows_preview.ps1",
    "scripts/build_brep_writer_source.sh",
    "scripts/build_brep_writer_windows.ps1",
    "scripts/verify_brep_writer.py",
    "scripts/build_document_core.py",
    "scripts/verify_document_core.py",
    "scripts/check_pure_core.py",
    "scripts/build_selection_core.py",
    "scripts/verify_selection_core.py",
    "scripts/build_macos_source.sh",
    "scripts/release_policy.py",
    "scripts/assemble_windows_release.py",
    "scripts/verify_windows_release.py",
    "spike/test_windows_collector.py",
    "spike/test_windows_release.py",
    "spike/test_windows_build.py",
    "scripts/validate_windows_icon.py",
    "spike/test_package_completeness.py",
    "spike/test_fast_checks.py",
    "scripts/fast_checks.sh",
    "spike/test_release_policy.py",
    "spike/test_macos_source.py",
    "launch/narwhal3d.desktop.in",
    "launch/narwhal3d-3dm.xml",
    "launch/narwhal3d-project.xml",
    "launch/narwhal3d.png",
    "packaging/linux-x86_64.lock.json",
    "packaging/linux-x86_64-dev.lock.json",
    "licenses/GPL-3.0-or-later.txt",
    "licenses/LGPL-2.1-or-later.txt",
    "licenses/OCCT-exception-1.0.txt",
    "licenses/OCP-Apache-2.0.txt",
    "licenses/AppImage-MIT.txt",
    "licenses/rhino3dm-MIT.txt",
    "native/brep_writer/main.cpp",
    "native/brep_writer/README.md",
    "native/ui_core/Cargo.toml", "native/ui_core/Cargo.lock", "native/ui_core/LICENSE",
    "native/ui_core/README.md", "native/ui_core/POPUP.md", "native/ui_core/gen_unicode.py",
    "native/ui_core/tests/host_ui31_integrity.rs",
    "native/ui_core/tests/data/host_ui31.json",
    "native/ui_core/tests/data/host_ui31.provenance.json",
    "native/ui_core/tests/editor27_golden.rs",
    "native/ui_core/tests/unicode28_golden.rs",
    "native/ui_core/tests/popup28_native.rs",
    "native/ui_core/tests/support/popup28_fixture.rs",
    "native/ui_core/tests/data/popup28_golden.bin",
    "native/ui_core/tests/data/popup28_golden.provenance.json",

    "native/ui_core/tests/data/unicode28_golden.txt",
    "native/ui_core/tests/data/unicode28_golden.provenance.json",

    "native/ui_core/tests/support/editor27_fixture.rs",
    "native/ui_core/tests/support/golden27_sha256.rs",
    "native/ui_core/tests/data/editor27_golden.bin",
    "native/ui_core/tests/data/editor27_golden.provenance.json",

    "native/ui_core/narwhal_ui_core.h", "native/ui_core/src/lib.rs",
    "native/ui_core/src/editor.rs", "native/ui_core/src/unicode16.rs",
    "native/ui_core/src/allocation_tests.rs", "native/ui_core/src/layout_algorithm.rs",
    "native/ui_core/src/layout_contract.rs", "native/ui_core/src/popup.rs",
    "native/ui_core/src/popup_ffi.rs", "native/ui_core/src/popup_tests.rs",
    "native/ui_core/TRANSCRIPT.md", "native/ui_core/transcript_unicode.py",
    "native/ui_core/src/transcript.rs", "native/ui_core/src/transcript_ffi.rs",
    "native/ui_core/src/transcript_tests.rs",
    "narwhal3d/native_transcript.py", "narwhal3d/command_transcript.py",
    "narwhal3d/command_history.py", "narwhal3d/state.py",
    "launch/check_native.py", "spike/test_desktop_native_startup.py",
    "spike/test_release_harness.py",
    "narwhal3d/textfield.py", "narwhal3d/native_textfield.py",
    "narwhal3d/native_popup.py", "narwhal3d/shell_popup.py", "narwhal3d/ui.py",
    "narwhal3d/bridge.py", "narwhal3d/ai_assistant.py", "narwhal3d/ai_action_review.py",
    "scripts/check_ui_core.py", "scripts/test_release_ui_core.py",
    "scripts/build_linux_native.py", "scripts/build_macos_native.py",
    "scripts/assemble_linux_release.py", "scripts/verify_linux_release.py",
    "scripts/assemble_macos_release.py", "scripts/test_macos_runtime.py",
    "spike/test_ui_core_editor.py", "spike/test_ui_core_callers.py",
    "spike/test_ui_core_layout.py", "spike/test_ui_core_mutations.py",
    "spike/ui_golden_data.py", "spike/test_native_only_policy.py",
    "spike/test_ui_transcript_abi.py", "spike/test_ui_transcript_model.py",
    "spike/test_ui_transcript_consumers.py", "spike/test_ui_transcript_stress.py",
    "spike/test_ui_transcript_gui.py", "spike/test_ui_transcript_unicode.py",
    "spike/test_ui_transcript_mutations.py", "spike/test_ui_transcript_benchmark.py",
    "spike/test_command_history.py", "spike/fixtures/numparse_golden.json",
    "spike/test_pure_core_packaging.py",
    "spike/test_ui_core_benchmark.py", "spike/test_ui_core_review_gui.py",
    "spike/test_ui_core_popup.py", "spike/test_ui_core_popup_mutations.py",
    "spike/test_ui_core_popup_gui.py", "spike/test_ui_core_popup_failure.py",
    "spike/test_ui_core_popup_failure_gui.py", "spike/test_ui_core_gate.py",
    "spike/test_textfield.py", "spike/test_shell_input.py", "spike/test_shell_popup.py",
    "spike/test_bugreport.py", "spike/test_usage_monitor.py", "spike/test_printdialog.py",
    "spike/test_print_active_view.py", "spike/test_print_export_units.py",
    "narwhal3d/workspace_overlay.py", "spike/test_workspace_overlay.py", "spike/test_workspace_dialogs_gui.py",
    "spike/test_selection_visibility_07.py", "spike/test_gumball_release_profile_07.py",
    "spike/test_move_regression_07.py",
    "spike/test_ai_accepted_review_native.py",
    "spike/test_ai_v7_visible.py", "spike/test_ai_v7_roundtrip.py",
    "spike/test_ai_v7_presentation.py", "spike/test_ai_v7_resources.py",
    "spike/test_ai_v8_exit.py", "spike/test_ai_v8_reachability.py",
    "spike/test_ai_v8_presentation.py", "spike/test_ai_v8_long_session.py",
    "spike/test_ai_v8_plans.py", "spike/test_ai_v8_lifetime.py",
    "spike/test_ai_v8_lifetime_gui.py", "spike/test_ai_v8_settings.py",
    "spike/test_ai_v8_visible.py", "spike/test_ai_v8_x11.py",
    "spike/test_ai_v9_lifecycle_gui.py",
    "docs/reviews/2026-10-31-ui-regressions.md",
    "docs/reviews/2026-10-31-native-only.md",
    "native/application_core/tests/history30_golden.rs",
    "native/application_core/tests/data/history30_trace.json",
    "native/application_core/tests/data/history30_trace.provenance.json", "spike/test_numparse_owned.py",
    "native/pure_core/src/numparse_owned.rs",
    "native/application_core/Cargo.toml", "native/application_core/Cargo.lock",
    "native/application_core/README.md", "native/application_core/OPENNURBS_LICENSE",
    "native/application_core/include/narwhal_application_core.h",
    "native/application_core/src/lib.rs", "native/application_core/src/session.rs",
    "native/application_core/src/table.rs", "native/application_core/src/wire.rs",
    "native/application_core/src/ordinal_map.rs", "native/application_core/tests/protocol.rs",
    "native/application_core/HISTORY.md", "native/application_core/src/history.rs",
    "native/application_core/src/history_tests.rs", "native/application_core/tests/history_protocol.rs",
    "native/application_core/history_mutations.rs", "narwhal3d/native_history.py",
    "native/application_core/generate_ordinal.rs", "native/application_core/mutation_check.rs",
    "narwhal3d/native_document_session.py", "narwhal3d/document_metadata.py",
    "scripts/check_application_core.py", "scripts/test_release_application_core.py",
    "native/document_core/Cargo.toml",
    "native/document_core/README.md",
    "native/document_core/src/main.rs",
    "native/document_core/src/proxy.rs",
    "narwhal3d/document_proxy.py", "narwhal3d/document_compat.py",
    "spike/test_document_compatibility.py", "spike/fixtures/document_schema1_historical.json",
    "native/document_core/tests/document_core.rs",
    "narwhal3d/native_document.py",
    "narwhal3d/narwhal_document.py",
    "narwhal3d/document_worker.py",
    "spike/test_narwhal_document.py",
    "spike/test_document_core_artifacts.py",
    "native/pure_core/Cargo.toml",
    "native/pure_core/README.md",
    "native/pure_core/src/lib.rs",
    "native/pure_core/src/stl.rs",
    "native/pure_core/src/obj.rs",
    "native/pure_core/src/dxf.rs",
    "native/pure_core/src/numparse.rs",
    "native/pure_core/src/calc.rs",
    "native/pure_core/src/bearing.rs",
    "native/pure_core/src/coords.rs",
    "native/pure_core/src/macro_parse.rs",
    "native/pure_core/src/macro_plan.rs",
    "native/pure_core/src/svgpath.rs",
    "native/pure_core/src/joinchain.rs",
    "native/pure_core/src/vector_export.rs",
    "native/pure_core/src/command_search.rs",
    "native/pure_core/src/coil.rs",
    "native/pure_core/src/bspline.rs",
    "native/pure_core/src/earclip.rs",
    "native/pure_core/src/nearest.rs",
    "native/pure_core/src/display.rs",
    "native/pure_core/src/fit.rs",
    "native/pure_core/src/arclength.rs",
    "native/pure_core/src/interpolate.rs",
    "native/pure_core/src/tween.rs",
    "native/pure_core/src/curvex.rs",
    "native/pure_core/src/curvex_polyline_cases.rs",
    "native/pure_core/src/nearest_polyline_cases.rs",
    "native/pure_core/src/nurbs_record.rs",
    "native/pure_core/src/line_samples.rs",
    "native/pure_core/src/surface.rs",
    "native/pure_core/src/cage.rs",
    "spike/test_native_cage_safety.py", "spike/test_cage_boundary.py",
    "spike/test_polygon_behavior.py", "spike/test_polygon_behavior_gui.py", "spike/test_drawing_behavior_gui.py",
    "native/pure_core/src/gridsnap.rs",
    "native/geometry_core/Cargo.toml",
    "native/geometry_core/Cargo.lock",
    "native/geometry_core/build.rs",
    "native/geometry_core/cpp/occt_adapter.cpp",
    "native/geometry_core/include/narwhal_geometry_core.h",
    "native/geometry_core/src/height_field.rs",
    "native/geometry_core/src/lib.rs",
    "native/geometry_core/src/surfacing.rs",
    'native/geometry_core/src/exact_conversion.rs',
    'native/geometry_core/cpp/exact_conversion.inc',
    'native/geometry_core/src/cap.rs',
    'native/geometry_core/cpp/cap_adapter.inc',
    'native/geometry_core/cpp/exact_budget.inc',
    'native/automation_core/tests/join_cap_policy.rs',
    'narwhal3d/cap_worker.py',
    'spike/test_cap_surfaces.py',
    'spike/test_ai_join_cap_host.py',
    'spike/test_cap_command.py',
    'native/geometry_core/src/join.rs',
    'native/geometry_core/cpp/join_adapter.inc',
    'spike/test_join_surfaces.py',
    'spike/test_join_surface_command.py',
    'spike/test_join_surface_command_mutation.py',
    "native/geometry_core/cpp/sweep_adapter.inc",
    "native/geometry_core/src/freeform.rs",
    "native/geometry_core/src/freeform_math.rs",
    "native/geometry_core/src/preparation.rs",
    "native/geometry_core/cpp/freeform_adapter.inc",
    "native/geometry_core/cpp/preparation_adapter.inc",
    "narwhal3d/surfacing_backend.py",
    "spike/test_sweep1_command_integration.py",
    "spike/test_sweep1_command_gui.py",
    "spike/test_sweep1_mouse_inputs.py",
    "spike/test_sweep1_interaction_checks.py",
    "spike/test_sweep1_interaction_gui.py",
    "spike/test_undo.py",
    "spike/test_clear_undo.py",
    "spike/test_surfacing_parallel.py",
    "spike/test_surfacing_probe.py",
    "spike/test_surfacing_integration.py",
    "spike/test_surfacing_freeform.py",
    "spike/test_surfacing_freeform_probe.py",
    "spike/test_surfacing_freeform_mutations.py",
    "spike/test_surfacing_interior.py",
    "spike/test_surfacing_step_roundtrip.py",
    "spike/test_surfacing_readiness_mutations.py",
    "spike/test_surfacing_preparation.py",
    "spike/test_surfacing_preparation_mutations.py",
    "native/platform_core/Cargo.toml",
    "native/platform_core/include/narwhal_platform_core.h",
    "native/platform_core/src/lib.rs",
    "narwhal3d/native_geometry.py",
    "narwhal3d/native_platform.py",
    "spike/test_native_geometry.py",
    "spike/test_local_brep_move.py",
    "spike/test_native_platform.py",
    "spike/test_run_suite.py",
    "scripts/run_suite.sh",
    "spike/test_ai_assistant.py",
    "spike/test_ai_transport.py",
    "spike/test_ai_workflow.py",
    "spike/test_ai_workspace.py", "spike/test_ai_workspace_native.py", "spike/test_ai_workspace_gui.py",
    "spike/test_ai_responses.py", "spike/test_ai_responses_native.py", "spike/test_ai_responses_settings.py",
    "narwhal3d/ai_workspace.py",
    'narwhal3d/ai_modeling.py',
    'narwhal3d/display_prepare.py',
    'native/automation_core/src/curve_inspection.rs',
    'native/automation_core/src/export_action.rs',
    'native/automation_core/src/modeling.rs',
    'native/automation_core/src/visual.rs',
    'native/automation_core/tests/modeling_policy.rs',
    'native/automation_core/tests/modeling_recovery.rs',
    'native/automation_core/tests/provider_recovery.rs',
    'spike/test_ai_modeling_host.py',
    'spike/test_ai_nurbs_surface_host.py',
    'native/automation_core/tests/tool_budget.rs',
    'spike/test_async_display_host.py',
    "spike/test_ai_workflow_native.py",
    "spike/test_ai_workflow_transport.py",
    "spike/test_ai_ui_workflow.py",
    "spike/test_ai_cad.py",
    "spike/test_purge.py",
    "spike/test_ai_box.py", "narwhal3d/ai_box.py",
    "spike/test_ai_cad_native.py",
    "native/automation_core/Cargo.toml",
    "native/automation_core/Cargo.lock",
    "native/automation_core/src/main.rs",
    "native/automation_core/src/protocol.rs",
    "native/automation_core/src/lib.rs",
    "native/automation_core/src/json.rs",
    "native/automation_core/src/provider.rs",
    "native/automation_core/src/conversation.rs",
    "native/automation_core/src/catalog.rs",
    "native/automation_core/src/actions.rs",
    "native/automation_core/src/approval_lifecycle.rs",
    "native/automation_core/src/evidence_contract.rs",
    "native/automation_core/src/plan_review.rs",
    "native/automation_core/src/diagnostics.rs",
    "native/automation_core/src/plan.rs",
    "native/automation_core/src/plan_schema.rs",
    "native/automation_core/src/primitives.rs",
    "native/automation_core/src/verification.rs",
    "native/automation_core/src/policy.rs",
    "native/automation_core/src/workspace.rs",
    "native/automation_core/src/box_action.rs",
    "scripts/narwhal_mcp.py",
    "narwhal3d/automation_tools.py",
    "narwhal3d/automation_bridge.py",
    "narwhal3d/automation_host.py",
    "narwhal3d/ai_settings_popup.py",
    "narwhal3d/extrude_preview.py",
    "spike/test_automation_bridge.py",
    "spike/test_automation_mcp.py",
    "spike/test_automation_live.py",
    "spike/test_automation_native.py",
    "spike/test_automation_host.py",
    "spike/test_automation_startup.py",
    "spike/test_automation_context.py",
    "spike/test_automation_modern.py",
    "spike/test_automation_lifecycle.py",
    "spike/test_ai_settings_popup.py",
    "spike/test_ai_settings_popup_gui.py",
    "spike/test_extrude_preview.py",
    "spike/test_extrude_preview_gpu.py",
    "spike/test_fillet_preview_latency.py",
    "spike/test_preview_responsiveness_gui.py",
    "spike/test_trim_box_gui.py",
    "spike/test_native_gridsnap.py",
    "native/selection_core/Cargo.toml",
    "native/selection_core/src/lib.rs",
    "spike/test_pure_core_gate.py",
    "spike/test_native_stl.py",
    "spike/test_native_obj.py",
    "spike/test_native_dxf.py",
    "spike/test_native_joinchain.py",
    "spike/test_native_vector_export.py",
    "spike/test_native_command_search.py",
    "spike/test_native_coil.py",
    "spike/test_native_intersect.py",
    "spike/test_pure_core_curve_hits_gate.py",
    "spike/test_pure_core_curve_record_hits_gate.py",
    "spike/test_polyline_curve_hits.py",
    "spike/test_pure_core_polyline_nearest_gate.py",
    "spike/test_polyline_nearest.py",
    "spike/test_pure_core_nurbs_record_gate.py",
    "spike/test_pure_core_line_samples_gate.py",
    "spike/test_pure_core_polyline_samples_gate.py",
    "spike/test_pure_core_polycurve_samples_gate.py",
    "spike/test_native_coil_preview.py",
    "spike/test_preview_curve_native.py",
    "spike/test_native_tessellate.py",
    "spike/test_native_unit_table.py",
    "spike/test_native_nearest.py",
    "spike/test_pure_core_nearest_batch_gate.py",
    "spike/test_rebuild_performance.py",
    "narwhal3d/nearest.py",
    "narwhal3d/native_nearest.py",
    "narwhal3d/nurbs_data.py",
    "narwhal3d/rebuild.py",
    "spike/test_native_curve_samples.py",
    "spike/test_nurbs_data.py",
    "spike/test_native_display_samples.py",
    "spike/test_native_rebuild_fit.py",
    "spike/test_native_arclength.py",
    "spike/test_native_interpolate.py",
    "spike/test_native_tween.py",
    "spike/test_curve_options.py",
    "spike/test_helix.py",
    "spike/test_spiral.py",
    "spike/test_coil_preview.py",
    "spike/test_curve_through_polyline.py",
    "spike/test_conic.py",
    "spike/test_parabola.py",
    "spike/test_parabola3pt.py",
    "spike/test_hyperbola.py",
    "spike/test_catenary.py",
    "spike/test_curve_through_pt.py",
    "spike/test_tween_curves.py",
    "spike/test_visibility.py",
    "spike/test_isolate.py",
    "spike/test_preselect.py",
    "spike/test_sel_dup.py",
    "spike/test_research13_selection.py",
    "spike/test_set_group_name.py",
    "spike/test_measure.py",
    "spike/test_transform_cmds.py",
    "spike/test_line_modes.py",
    "spike/test_what.py",
    "spike/test_curve_tools.py",
    "spike/test_insertknot.py",
    "spike/test_join.py",
    "spike/test_length_cmd.py",
    "spike/test_pyramid_preview.py",
    "spike/test_solid_previews.py",
    "spike/test_t117.py",
    "spike/test_research13_commands.py",
    "spike/test_handlecurve.py",
    "spike/test_curve_through_srf_control_pt.py",
    "spike/test_sequential_object_steps.py",
    "spike/test_rust_selection_core.py",
    "spike/test_native_selection.py",
    "spike/test_selection_core_packaging.py",
    "spike/test_pick_candidates.py",
    "spike/test_pick_click_local.py",
    "spike/test_gumball_native_pick.py",
    "spike/test_osnap_native_parity.py",
    "spike/test_selection_menu.py",
    "spike/test_selection_menu_gui.py",
    "spike/test_ai_ui_workflow_gui.py",
    "spike/test_ui_bundle.py",
    "spike/test_ui_v44_icons.py", "spike/test_ui_v44_panels.py", "spike/test_welcome_v44.py",
    "narwhal3d/ai_presentation.py", "narwhal3d/ui_icon_data.py",
    "docs/UI_DESIGNER_BUNDLE.md",
    "scripts/build_ui_bundle.py",
    "scripts/ui_bundle_export.py",
    "scripts/ui_bundle_shots.py",
    "scripts/ui_bundle/index.html",
    "scripts/ui_bundle/app.js",
    "scripts/ui_bundle/style.css",
    "scripts/ui_bundle/dialogs.js",
    "scripts/ui_bundle/dialogs.css",
    "scripts/ui_bundle/welcome.js",
    "scripts/ui_bundle/welcome.css",
    "scripts/ui_bundle/test_preview.cjs",
    "scripts/ui_bundle/fonts/Inter.woff2",
    "scripts/ui_bundle/fonts/Ubuntu-Light.ttf",
    "scripts/ui_bundle/fonts/OFL-1.1.txt",
    "scripts/ui_bundle/fonts/UFL.txt",
    "scripts/ui_bundle/fonts/README.md",
    "scripts/ui_bundle/references/workspace-light.png",
}
FORBIDDEN_EXACT = {
    "AGENTS" + ".md",
    "POLICY.md",
    "coco" + "cad.sh",
    "rhi" + "no" + "alt.sh",
    "narwhal3d/kernel/brep_writer.cpp",
    "launch/build_brep_writer.sh",
    "spike/ui_report.py",
}
FORBIDDEN_PREFIXES = (
    "legacy/",
    "bug_reports/",
    "docs/measured/",
    "ExamplePhotos/",
    "More" + "_Data/",
    "vendor/",
    "tools/",
    "artifacts/",
)
FORBIDDEN_GLOBS = (
    "spike/" + "rhi" + "no_*.py",
    "spike/gen_*.py",
    "spike/probe*.py",
    "spike/ui_probe*.py",
    "spike/measure_" + "rhi" + "no_*.py",
    "spike/docs_dump.txt",
)
# Exact approved v4.4 asset namespace; no wildcard for generated artwork.
V44_ICON_STEMS = (
    'generated-add-clipping-plane',
    'generated-back-view',
    'generated-back-view-of-cplane',
    'generated-bottom-view',
    'generated-bottom-view-of-cplane',
    'generated-copy',
    'generated-disable-clipping-plane',
    'generated-edit-filletedge',
    'generated-extrude-face',
    'generated-extrude-face-to-a-boundary',
    'generated-front-view',
    'generated-front-view-of-cplane',
    'generated-group',
    'generated-hatch',
    'generated-hatch-base',
    'generated-hatch-scaling',
    'generated-hide',
    'generated-hide-control-points',
    'generated-horizontal-dimension',
    'generated-invert-hide',
    'generated-invert-lock',
    'generated-layout-properties',
    'generated-left-view',
    'generated-left-view-of-cplane',
    'generated-linear-dimension',
    'generated-lock',
    'generated-move-face',
    'generated-move-face-to-a-boundary',
    'generated-move-vertical',
    'generated-move-x',
    'generated-new-layout',
    'generated-new-layout-4-details',
    'generated-panel-ai-assistant',
    'generated-panel-bug-report',
    'generated-panel-display',
    'generated-panel-layers',
    'generated-panel-properties',
    'generated-perspective-view',
    'generated-plan-view-of-cplane',
    'generated-preview-animation',
    'generated-previous-cplane',
    'generated-record-animation',
    'generated-render',
    'generated-render-preview',
    'generated-right-view',
    'generated-right-view-of-cplane',
    'generated-rotate-cplane',
    'generated-set-cplane-by-3-points',
    'generated-set-cplane-by-x-axis',
    'generated-set-cplane-by-z-axis',
    'generated-set-cplane-elevation',
    'generated-set-cplane-origin',
    'generated-set-cplane-perpendicular-to-curve',
    'generated-set-cplane-to-object',
    'generated-set-cplane-to-surface',
    'generated-set-cplane-to-view',
    'generated-set-cplane-vertical',
    'generated-set-cplane-world-back',
    'generated-set-cplane-world-bottom',
    'generated-set-cplane-world-front',
    'generated-set-cplane-world-left',
    'generated-set-cplane-world-right',
    'generated-set-cplane-world-top',
    'generated-shade',
    'generated-show',
    'generated-show-layers-in-detail',
    'generated-show-selected-objects',
    'generated-show-selected-objects-in-active-detail',
    'generated-swap-hidden-and-visible-objects',
    'generated-swap-locked-and-unlocked-objects',
    'generated-toggle-shade-selected-mode',
    'generated-top-view',
    'generated-top-view-of-cplane',
    'generated-ungroup',
    'generated-unlock',
    'generated-unlock-selected-objects',
    'generated-variable-radius-fillet',
    'generated-vertical-dimension',
    'generated-zoom-dynamic-ctrl-rmb',
    'generated-zoom-extents',
    'generated-zoom-selected',
    'generated-zoom-window',
    'narwhal-ai-mark',
)
ALLOWED_IMAGES = (
    # Approved Library handoff icon only, not a generated-artwork wildcard.
    "artwork/icons/generated-panel-library.svg", "artwork/icons/generated-panel-library.png",
    "narwhal3d/icons/generated-panel-library.svg", "narwhal3d/icons/generated-panel-library.png",
    *(f"narwhal3d/icons/{stem}.{extension}" for stem in V44_ICON_STEMS for extension in ("png", "svg")),
    "launch/narwhal3d.png",
    "docs/readme-command-loft.gif",
    "docs/images/bugreport.png",
    "docs/images/workspace-light.png",
    "docs/images/workspace-dark.png",
    "scripts/ui_bundle/references/workspace-light.png",
    "artwork/icons/*.png",
    "artwork/Logo/narwhal.png",
    "artwork/Logo/narwhal_vector.svg",
    "narwhal3d/icons/narwhal-*.png",
    "blender_patches/narwhal3d_app_icon.svg",
    "packaging/windows/*.ico",
    *(f'{folder}/{stem}.{extension}'
      for folder, extension in (('artwork/icons', 'svg'), ('narwhal3d/icons', 'png'))
      for stem in ('v4-panel-properties', 'v4-panel-display', 'v4-panel-ai-assistant',
                   'v4-viewport-maximize', 'v4-viewport-restore')),
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico", ".svg"}
LEGACY_PATH_PATTERN = re.compile(r"rhi" + r"no(?!3dm)", re.IGNORECASE)
LEGACY_TEXT_PATTERN = re.compile(rb"\brhi" + rb"no(?!3dm)")
LEGACY_TEXT_ALLOWED = {"COMPATIBILITY.md"}
# Assemble signatures from fragments so this policy file is subject to its own scan.
FORBIDDEN_TEXT = (
    b"/home/" + b"nathaniel",
    b"coco " + b"cad",
    b"coco" + b"cad",
    b"rhi" + b"no alternative",
    b"rhi" + b"noalternative",
    b"rhi" + b"nocore.dll",
    b"vendor/rhi" + b"no-help",
    b"owner" + b":",
    b"owner" + b"'s",
    b"owner-" + b"reported",
    b"owner bug" + b" log",
    b"agents" + b".md",
    b"docs/current" + b"_round.md",
    b"docs/kernel" + b".md",
    b"docs/geometry",
    b"coco" + b"ville",
    b"coco" + b" production",
    b"coco" + b"-cut",
    b"coco" + b"_cut",
    b"example" + b"photos/",
    b"more_" + b"data/",
    b"bug_" + b"reports/",
    b"rhi" + b"no_addon",
    b"rhi" + b"noalt_",
    b"rhi" + b"noalt-",
    b"whale orn cut" + b"test fix.cdr",
    b"complex setting" + b".3dm",
    b"flow ring" + b".3dm",
)
FORBIDDEN_PATTERNS = (
    re.compile(rb"\b(?:t|g|f|q)-\d+\b"),
    re.compile(rb"\bbug report\s*#\d+\b"),
    re.compile(rb"\bround\s+\d+\b"),
    re.compile(rb"\b(?:front" + rb"ier|lower tier|geometry agent|hand" + rb"off)\b"),
    re.compile(rb"spike/(?:probe|rhi" + rb"no_|gen_|measure_)"),
)
SECRET_MARKERS = (
    b"-----BEGIN PRIVATE " + b"KEY-----",
    b"gh" + b"p_",
    b"github_" + b"pat_",
    b"AK" + b"IA",
)
# These policy files necessarily name exclusions/signatures. They remain subject to path,
# symlink, Python-compilation, binary and secret checks.
PROVENANCE_SCAN_EXEMPT = {
    ".gitignore",
    "scripts/audit_public_tree.py",
    # Exact upstream source has a local Rust variable named `owner`; its bytes are
    # independently pinned by SOURCE_MANIFEST.json and spike/test_public_project.py.
    "native/vendor/tracedraw/core/src/document.rs",
    # VectorCraft upstream source (native/picture_trace) also has local Rust variables named `owner`.
    "native/picture_trace/crates/vectorcraft-pathops/src/pathfinder.rs",
    "native/picture_trace/crates/vectorcraft-trace/src/logo/pipeline.rs",
}
REQUIRED_EXECUTABLES = {
    "narwhal3d.sh",
    "run_tests.sh",
    "uishot.sh",
    "scripts/bootstrap_dev_linux.sh",
    "scripts/setup_dev_native.sh",
    "scripts/build_blender_linux.sh",
    "scripts/build_macos_source.sh",
    "Build-Narwhal3D-macOS.command",
    "scripts/build_readme_demo.sh",
}
REQUIRED_CONTENT = {
    "narwhal3d/commands.py": (
        b"class Command:",
        b"def _build_registry",
        b"COMMANDS: dict[str, Command] = _build_registry()",
    ),
    "scripts/create_public_branch.py": (
        b"BRANCH = \"narwhal3d-public\"",
        b"for directory in (\"narwhal3d\", \"packaging\", \"licenses\", \".github\", \"artwork\")",
        b"--require-root-commit",
    ),
}


class AuditError(RuntimeError):
    """The requested tree could not be enumerated safely."""


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, cwd=root, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"").decode("utf-8", "replace").strip()
        raise AuditError(f"command failed: {' '.join(args)}{': ' + detail if detail else ''}") from exc


def git_entries(root: Path, ref: str) -> dict[str, tuple[str, bytes]]:
    """Return ``path -> (mode, blob)`` for every file in *ref*."""
    listing = _run(root, "git", "--no-pager", "ls-tree", "-rz", ref).stdout
    entries: dict[str, tuple[str, bytes]] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, _object = meta.decode("ascii").split()
        if kind != "blob":
            continue
        path = raw_path.decode("utf-8", "surrogateescape")
        blob = _run(root, "git", "--no-pager", "show", f"{ref}:{path}").stdout
        entries[path] = (mode, blob)
    return entries


def filesystem_entries(root: Path) -> dict[str, tuple[str, bytes]]:
    """Return regular files and symlinks below *root*, excluding Git metadata."""
    entries: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if path.is_symlink():
            entries[rel] = ("120000", os.readlink(path).encode("utf-8", "surrogateescape"))
        elif path.is_file():
            entries[rel] = ("100755" if os.access(path, os.X_OK) else "100644", path.read_bytes())
    return entries


def retirement_path_problem(path: str) -> str | None:
    """Reject retired payloads at source or extracted application-relative paths."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if any(part.casefold() == "legacy" or part.casefold().startswith("retired-python-")
           or part.casefold() in {"legacy.zip", "legacy.tar", "legacy.tar.gz", "legacy.tgz"}
           for part in parts):
        return "retired archive is local-only"
    if parts and parts[-1] in {"textfield_168212d4.py", "test_ui_core_golden.py", "test_rust_history_trace.py",
                                  "test_ai_box_native.py", "test_ai_plan_native.py", "test_ai_v4_native.py", "test_ai_v6_review.py", "test_ai_v7_native.py", "test_ai_v8_native.py"}:
        return "retired executable reference"
    return None


def runtime_archive_problem(blob: bytes) -> str | None:
    """Catch archive dependencies in runtime source and embedded artifact paths.

    This is a bounded contamination check, not a general Python sandbox. String
    constants are inspected as well as imports so split path construction is caught.
    """
    if re.search(rb"legacy[/\\\\]retired-python-", blob.lower()):
        return "runtime artifact references the retirement archive"
    try:
        tree = ast.parse(blob)
    except (SyntaxError, UnicodeError, ValueError):
        return None  # Binary artifacts and compile errors are checked separately.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or "", *(alias.name for alias in node.names)])
            if any("legacy" in name.split(".") or "retired-python-" in name for name in names):
                return "runtime imports the retirement archive"
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            value = node.value.decode("utf-8", "replace") if isinstance(node.value, bytes) else node.value
            if retirement_path_problem(value):
                return "runtime resolves a retirement archive path"
    return None


def path_problem(path: str) -> str | None:
    posix = PurePosixPath(path)
    retired = retirement_path_problem(path)
    if retired:
        return retired
    if path.startswith("/") or ".." in posix.parts or "\\" in path:
        return "unsafe path spelling"
    if LEGACY_PATH_PATTERN.search(path):
        return "legacy product name in public path"
    if path in FORBIDDEN_EXACT:
        return "forbidden private or excluded component"
    if any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return "forbidden private directory"
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in FORBIDDEN_GLOBS):
        return "commercial research/mining tool"
    if posix.suffix.lower() in IMAGE_SUFFIXES and not any(
        fnmatch.fnmatchcase(path, pattern) for pattern in ALLOWED_IMAGES
    ):
        return "unexpected image or artwork"
    return None


# This source gate explicitly prepends scripts/ before importing its test helper.
TEST_SUPPORT_IMPORTS = {"test_release_ui_core": "scripts/test_release_ui_core.py"}


def required_public_paths(entries: dict[str, tuple[str, bytes]]) -> set[str]:
    """Add harness source references and imported test fixtures, never publish them.

    Conditional suites count too: a public checkout must not depend on whether the
    auditing host happens to have a display, compiler or staged native library.
    Comments are not executable dependencies; policy-negative literals in Python
    tests are not dependencies either. Their contents still face the normal scan.
    """
    required = set(REQUIRED)
    pending = ["run_tests.sh", "uishot.sh", *sorted(p for p in REQUIRED if p.startswith("spike/"))]
    visited = set()
    source_path = re.compile(r"\b(?:spike|scripts|native|launch)/[\w./-]+\.(?:py|sh|rs|toml)(?![\w.])")
    while pending:
        path = pending.pop()
        if path in visited or path not in entries:
            continue
        visited.add(path)
        blob = entries[path][1]
        dependencies = set()
        if path.endswith(".sh"):
            text = "\n".join(line for line in blob.decode("utf-8", "replace").splitlines()
                             if not line.lstrip().startswith("#"))
            dependencies.update(source_path.findall(text))
        elif path.endswith(".py"):
            try:
                tree = ast.parse(blob, filename=path)
            except (SyntaxError, UnicodeError, ValueError):
                continue  # audit_entries separately rejects invalid Python.
            for node in ast.walk(tree):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    if name.startswith("test_"):
                        dependencies.add(TEST_SUPPORT_IMPORTS.get(name, "spike/" + name.replace(".", "/") + ".py"))
        required.update(dependencies)
        pending.extend(dependencies - visited)
    return required


def legacy_scan_bytes(path: str, blob: bytes) -> bytes:
    """Ignore only exact required upstream trademark sentences, never file contents."""
    folded = blob.lower()
    if path in {'native/application_core/src/ordinal_map.rs',
                'native/application_core/generate_ordinal.rs'}:
        sentence = (b'OpenNURBS, Rhi' + b'noceros, and Rhi' +
                    b'no3D are registered trademarks of Robert').lower()
        folded = folded.replace(sentence, b'upstream trademark attribution')
    if path == 'native/application_core/OPENNURBS_LICENSE':
        sentence = (b'Rhi' + b'noceros is a registered trademark of Robert McNeel & Associates.').lower()
        folded = folded.replace(sentence, b'upstream trademark attribution')
    return folded


# Authored pre-retirement DXF captures, never a directory/extension exemption.
# Exact byte identity preserves incidental layer names such as Round\n70 as
# DATA rather than development prose. Altered bytes and new paths fail closed.
FROZEN_DXF23_FILES = {
    'native/pure_core/tests/fixtures/dxf_writer23/all-aci.dxf': (390, '843523192f3d587d899204ceac8226c230d2e2e08839e356d0ad4bef8850d41e'),
    'native/pure_core/tests/fixtures/dxf_writer23/capture.json': (776317, '876dcfe488123da6acf024bfe822b199f80bb0d69c38bc7c1a7490f2025cda3e'),
    'native/pure_core/tests/fixtures/dxf_writer23/color-sentinel.dxf': (251, '3e0016963a64fdcb5ea1d90dfd2ee535d8129e237dee807b47d5b608ffc41f71'),
    'native/pure_core/tests/fixtures/dxf_writer23/decoder-mixed.dxf': (501, '1584d536b978cad11a6b0f06584913001db8aa2c4a258ef0708dda2d3a34a053'),
    'native/pure_core/tests/fixtures/dxf_writer23/decoder-no-layers.dxf': (501, '90c7545e49cddd8a10f34f22bf9e95ce82bde076161493c7b39a1a3c2d7968b7'),
    'native/pure_core/tests/fixtures/dxf_writer23/empty.dxf': (159, 'cf1a7b7cde17bc93c39b01059679081a4acb43caf66230ee28064e4b57356628'),
    'native/pure_core/tests/fixtures/dxf_writer23/key-alias.dxf': (243, '18581469c206c63ddacfba2e78832723209868488bd4ebba97f84ebf75d70ab4'),
    'native/pure_core/tests/fixtures/dxf_writer23/latin1.dxf': (21160, '2bf91cb6c6fca7397d9cfb39736e5d7d1aab1332c1d5592b5f7d7c5dd7431849'),
    'native/pure_core/tests/fixtures/dxf_writer23/mixed-closed.dxf': (839, '36ea3cdc540d419d586fbe5edd0e818245887eb9b8a925ff5827e33065d494f7'),
    'native/pure_core/tests/fixtures/dxf_writer23/poly-one.dxf': (261, 'da8b696d4a2ad16f6a64046932e22ea32b1fbfafbba42afff7866b6763afef70'),
    'native/pure_core/tests/fixtures/dxf_writer23/poly-open.dxf': (327, '9342871a2ed507354d00cd79dd92ff3cea13db2ec8cfe227e4bdd7eab7ed9fb7'),
    'native/pure_core/tests/fixtures/dxf_writer23/poly-zero.dxf': (233, '29e051129042aa124c3963a59ce7534fffc5cc635fd5ed3c8677b03e47d07965'),
    'native/pure_core/tests/fixtures/dxf_writer23/random-coordinates.bin': (96000, 'e8adbec63e233e6a0f4c9c565a6bc11e9bd9708223241989aba965f94486ccef'),
    'native/pure_core/tests/fixtures/dxf_writer23/random-floats.dxf': (232384, '5a264a2353eae0548aa7f352df0e901a104a30d5c00a8b1706bb0c897ce6337b'),
    'native/pure_core/tests/fixtures/dxf_writer23/short-poly-chain.dxf': (346, '336410586647bfd0bb908da03a999e3a374cfa5e02d9b011864db95c99d108d9'),
    'native/pure_core/tests/fixtures/dxf_writer23/signed-exponents.dxf': (284, '6fabf605d20a1634840a64fa9fa30937b4fa6a73ae78d426035d2301265db58b'),
    'native/pure_core/tests/fixtures/dxf_writer23/spline-closed.dxf': (413, '9b2267502fd4334293658f19f36f851b6691136e3142cafd8a4b02708b7e465d'),
    'native/pure_core/tests/fixtures/dxf_writer23/unicode-frame.utf8': (252, 'a4723aed2947696694fc023e344512c277a150514ca0f88547a04891ddb7da46'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Angstroms.dxf': (860, '6a9485d66dcc1ce9269e3c19f441dd71c451a82a3e7828246a6647af5215fee4'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Centimeters.dxf': (859, 'fab67912b5afff37080ed12ab9822a40d23f86fb6c9172826f3964b8c05d1a39'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Decimeters.dxf': (860, '64166f2719e194c823c228a3d0906833cdc711540cb8fe8229e7dde7e4b77220'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Dekameters.dxf': (860, 'c6ad3eada2ffc6754ff7630275526e67f2f5b80bc062a826fada92c7422965de'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Feet.dxf': (859, '259b98feb0796a84ce9dc83a6428c2a0e81ea66fc373a5808f28751c3eb20514'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Gigameters.dxf': (860, '5efbc74dc4fb5ebddb463769f13b69fb1ecba292af439285055be99e44463cf4'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Hectometers.dxf': (860, 'e6193b633d761fbc98de4f596e7410f1d966a0dd61ba3cd75e53dfc516f05844'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Inches.dxf': (859, '9a878680ebb30fe99545d5107a58463a7fde480050a414e7af88c32ebb796a8b'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Kilometers.dxf': (859, 'dee9481b21287a0596b4d94b045c677e05dc66fbf82916bada371d8644fc292c'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Meters.dxf': (859, 'a7bc1fdb36c98eecb2da62fdc516d20df6f6d7a5159cf80699509837fcd1d616'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Microinches.dxf': (859, 'b2221737fc356763dd60f355494dd839caa1965ca2fe2f06e7f80c9571322043'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Microns.dxf': (860, '3ba7476912d9b9e9316e310125e7c642feda2b7109978c3e9b0cd4359d42571d'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Miles.dxf': (859, '871b0bb13456cbcc8a6081452870bce5005e6cd51c9b3508b493b6dd34122c39'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Millimeters.dxf': (859, 'ae645fe81d84783f2b7eb35ce39e606a02318b4f667d054cc2251e6a288ef39b'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Mils.dxf': (859, '3a1e89a148138773ccd26f8af50af6f2c05f237b6397c17f2b5de342fa5ff8c8'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Nanometers.dxf': (860, '8beb4a33432bac0956bee9b04cacc606c663c1cf3c7a94d98c1d329c414b8105'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-UnknownUnit.dxf': (839, 'f206e2815e4b6bdebb392118d91d83057973dbacbfc360f6c415cb3b36bed2f4'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-Yards.dxf': (860, 'b76ea24d7de97e2c30a35b9cfc8f9a13732d01b1a0f5a3ec0b8f57a3e1fd0b20'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-default.dxf': (839, '4adef1038f4e569cdbe3f6d1b20eafdb6872a28d7d35ca65fd3ce15a571bd7ba'),
    'native/pure_core/tests/fixtures/dxf_writer23/units-empty.dxf': (839, 'f206e2815e4b6bdebb392118d91d83057973dbacbfc360f6c415cb3b36bed2f4'),
}


def frozen_dxf23_scan(path: str, blob: bytes) -> bytes:
    if path not in FROZEN_DXF23_FILES:
        return blob
    size, digest = FROZEN_DXF23_FILES[path]
    if len(blob) != size or hashlib.sha256(blob).hexdigest() != digest:
        raise ValueError("DXF23 frozen capture size/provenance hash mismatch")
    return json.dumps({"kind": "independently captured DXF writer data",
                       "source_sha256": "6450fa373ebc366e23053a71fbd6efeb6cde94922ab9c752d926e55a10cdacbc",
                       "size": size, "sha256": digest}, sort_keys=True).encode()


FROZEN_PREVIEW24_FILES = {
    'native/pure_core/tests/data/preview24_linux.tsv': (1661185, 'ce39c9964a96ab57b1dc42ef04bd697d2e38776cdc7abce2e6271cfb9a7b9049'),
    'native/pure_core/tests/data/preview24_windows_msvc.tsv': (1661185, 'ff1fffae85ec59d0016b61eedb97cc316e3d279b4da90155195fb12815cdddbe'),
}


def frozen_preview24_scan(path: str, blob: bytes) -> bytes:
    if path not in FROZEN_PREVIEW24_FILES:
        return blob
    size, digest = FROZEN_PREVIEW24_FILES[path]
    if len(blob) != size or hashlib.sha256(blob).hexdigest() != digest:
        raise ValueError("preview frozen capture size/provenance hash mismatch")
    return json.dumps({"kind": "independent original preview float-bit data",
                       "size": size, "sha256": digest}, sort_keys=True).encode()


FROZEN_SELECTION_FILES = {
    'native/selection_core/tests/data/selection26_aabb250.bin': (531049, 'b5a1656e3f91ce600885c21e9a5dc018b254da65b24a7dc8783639da26640978'),
    'native/selection_core/tests/data/selection26_clip250.bin': (793139, '50408158bfd753fd8bad78405ec9b7c197745bd0baf0fc3f76e6f344f58ae49b'),
    'native/selection_core/tests/data/selection26_curves100.bin': (409495, 'fa0b5b48f941af852c4f6234a6f726bf15c0bffe8a4c71b09e1366c3044469f7'),
    'native/selection_core/tests/data/selection26_near250.bin': (642771, 'd4839ed1d7983ef16c2f2794b149182b5e933dd24e3b314fd52f0f1fbc60813d'),
    'native/selection_core/tests/data/selection26_point200.bin': (329687, '349d4122a5d84c80002e290d5b28516e1118e5cb9897a71b404d2d4f97f590b1'),
    'native/selection_core/tests/data/selection26_poly250.bin': (629002, '765adce5170f360c4a2d4eb37c129ce36e2e73bea4b8224f658f4914ca25d59a'),
    'native/selection_core/tests/data/selection26_screen500.bin': (351274, '19132f31470cc836f4ef84770d5fb42cbd72d01177ce7550614285d7ef055487'),
    'native/selection_core/tests/data/selection26_snap200.bin': (344349, '322dae45a2cd429be6090b31c8e4ab71bd46b0433a8fe52bc8e90e693f159f99'),
    'native/selection_core/tests/data/selection26_world250.bin': (191088, 'a31bddc5263593552f74e55be3c587981a4956b0c03a5c35ab518c92ad5a2631'),
    'native/selection_core/tests/data/selection26_worldboxes250.bin': (533858, '103ffe0f2277f901a01bb366a28c5e05679c230b33e875527ccb5b1e93dc108a'),
}


def frozen_selection_scan(path: str, blob: bytes) -> bytes:
    if path not in FROZEN_SELECTION_FILES:
        return blob
    size, digest = FROZEN_SELECTION_FILES[path]
    if len(blob) != size or hashlib.sha256(blob).hexdigest() != digest:
        raise ValueError("selection frozen capture size/provenance hash mismatch")
    return json.dumps({"kind": "independent original selection input/result data",
                       "size": size, "sha256": digest}, sort_keys=True).encode()


FROZEN_NEAREST_FILES = {
    'native/pure_core/tests/fixtures/nearest25_callbacks_linux.tsv': (648806, '43bc3ab6fb1e74fd25ffa6be7ff1f41a8aa4447484cd90788a4fcede0b5b70f4'),
    'native/pure_core/tests/fixtures/nearest25_callbacks_windows_msvc.tsv': (648806, '43bc3ab6fb1e74fd25ffa6be7ff1f41a8aa4447484cd90788a4fcede0b5b70f4'),
    'native/pure_core/tests/fixtures/nearest25_host_linux.jsonl': (1432199, '573d49ff3d06ca3df75d42d385a59278d38c0366af750964a7d99887e34d1a2e'),
    'native/pure_core/tests/fixtures/nearest25_host_windows_msvc.jsonl': (1432199, '1b087b9b906e21da09871a9fbc6265d5026ef567ff563f99b6b3c0c87902aeba'),
    'native/pure_core/tests/fixtures/nurbs_host_windows_msvc.txt': (468138, '5681c6a1c32ef5582c3eb1064e99b6150245daa9046bdae2665a19dba6041aa8'),
}


def frozen_nearest_scan(path: str, blob: bytes) -> bytes:
    if path not in FROZEN_NEAREST_FILES:
        return blob
    size, digest = FROZEN_NEAREST_FILES[path]
    if len(blob) != size or hashlib.sha256(blob).hexdigest() != digest:
        raise ValueError("nearest frozen capture size/provenance hash mismatch")
    return json.dumps({"kind": "independent original nearest SDK answers/transcripts",
                       "size": size, "sha256": digest}, sort_keys=True).encode()


FROZEN_JSON_FILES = {
    "spike/fixtures/numparse_golden.json": {
        "origin": "50267dd6d4504b14052d1ab7e54675f3c8750637",
        "kind": "pre-retirement numeric parser DATA; no executable oracle",
        "sha256": "9016730b186ee2f394449b0eaa0c6761c7b5b101b66e89331f005ca0ab2203dd",
        "rows": {"15.1.0": 2544, "16.0.0": 2624},
    },
}


def frozen_json_scan(path: str, blob: bytes) -> bytes:
    """Classify exact hash/schema-checked DATA, not escaped inputs as task prose.

    The raw artifact still receives the secret scan. Any byte drift is a failure,
    not a blanket JSON exemption or permission to hide new code/private payloads.
    """
    if path not in FROZEN_JSON_FILES:
        return blob
    provenance = FROZEN_JSON_FILES[path]
    if hashlib.sha256(blob).hexdigest() != provenance["sha256"]:
        raise ValueError("frozen JSON DATA provenance hash mismatch")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate frozen DATA key")
            result[key] = value
        return result
    data = json.loads(blob, object_pairs_hook=pairs)
    if not isinstance(data, dict) or set(data) != set(provenance["rows"]):
        raise ValueError("unexpected frozen DATA Unicode versions")
    for version, count in provenance["rows"].items():
        rows = data[version]
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError("frozen DATA row count mismatch")
        for row in rows:
            if not (isinstance(row, list) and len(row) == 3 and isinstance(row[0], str)
                    and (row[1] is None or isinstance(row[1], str))
                    and isinstance(row[2], list) and len(row[2]) == 2):
                raise ValueError("invalid numeric DATA row")
            value, error = row[2]
            if not ((type(value) in (int, float) and math.isfinite(value) and error is None)
                    or (value is None and isinstance(error, str))):
                raise ValueError("invalid numeric DATA result")
    return json.dumps(provenance, sort_keys=True).encode()


FROZEN_DATA_FILES = {}  # No executable Python DATA fixtures remain allowlisted.


def frozen_data_scan(path: str, blob: bytes) -> bytes:
    """All Python source receives the full privacy/provenance scan, without DATA exemptions."""
    return blob


def _rust_code_mask(blob: bytes) -> bytes:
    """Preserve offsets while masking Rust comments and string literals for matching."""
    code = bytearray(blob)
    i = 0
    while i < len(blob):
        start = i
        if blob[i:i + 2] == b'//':
            end = blob.find(b'\n', i)
            i = len(blob) if end < 0 else end
        elif blob[i:i + 2] == b'/*':
            depth, i = 1, i + 2
            while i < len(blob) and depth:
                if blob[i:i + 2] == b'/*':
                    depth, i = depth + 1, i + 2
                elif blob[i:i + 2] == b'*/':
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif raw := re.match(rb'(?:br|cr|r)(#*)"', blob[i:]):
            end = blob.find(b'"' + raw[1], i + raw.end())
            i = len(blob) if end < 0 else end + 1 + len(raw[1])
        elif blob[i:i + 1] == b'"':
            i += 1
            while i < len(blob):
                if blob[i:i + 1] == b'\\':
                    i += 2
                elif blob[i:i + 1] == b'"':
                    i += 1
                    break
                else:
                    i += 1
        else:
            i += 1
            continue
        code[start:i] = b' ' * (min(i, len(blob)) - start)
    return bytes(code)


def code_provenance_scan(path: str, blob: bytes) -> bytes:
    """Normalize only approved syntactic identifiers, never comments or strings."""
    spans = []
    if path == 'native/ui_core/src/transcript_ffi.rs':
        code = _rust_code_mask(blob)
        patterns = (
            rb'\bstruct\s+Snapshot\s*\{\s*(owner)\s*:\s*u64\s*,',
            rb'\bfn\s+reply\s*\(\s*handle\s*:\s*u64\s*,\s*(owner)\s*:\s*&Transcript\s*\)\s*->\s*Reply\s*\{',
            rb'\bSnapshot\s*\{\s*(owner)\s*:\s*handle\s*,',
        )
        spans = [match.span(1) for pattern in patterns for match in re.finditer(pattern, code)]
    elif path in {
        'native/application_core/src/live_objects.rs',
        'native/application_core/src/object_registry.rs',
        'native/application_core/src/object_registry_tests.rs',
        'native/application_core/tests/object_registry_protocol.rs',
    }:
        code = _rust_code_mask(blob)
        pattern = rb'\b(owner)\s*:\s*(?:u64|&Value|owner_id)\b'
        spans = [match.span(1) for match in re.finditer(pattern, code)]
    elif path in {'narwhal3d/native_transcript.py', 'spike/test_ui_transcript_model.py'}:
        try:
            tree = ast.parse(blob)
            text = blob.decode('utf-8')
            lines = text.splitlines(keepends=True)
            offsets = [0]
            for line in lines:
                offsets.append(offsets[-1] + len(line.encode('utf-8')))
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
            classes = {node.lineno for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                       and node.name == 'Owner' and not node.bases and not node.keywords}
            bindings = {(item.optional_vars.lineno, item.optional_vars.col_offset)
                        for node in ast.walk(tree) if isinstance(node, ast.With)
                        for item in node.items if isinstance(item.optional_vars, ast.Name)
                        and item.optional_vars.id == 'owner'}
            for index, token in enumerate(tokens[1:-1], 1):
                if token.type != tokenize.NAME or tokens[index + 1].string != ':':
                    continue
                line, column = token.start
                byte_column = len(lines[line - 1][:column].encode('utf-8'))
                declaration = (path == 'narwhal3d/native_transcript.py' and token.string == 'Owner'
                               and line in classes and tokens[index - 1].string == 'class')
                binding = (path == 'spike/test_ui_transcript_model.py' and token.string == 'owner'
                           and (line, byte_column) in bindings and tokens[index - 1].string == 'as')
                if declaration or binding:
                    start = offsets[line - 1] + byte_column
                    spans.append((start, start + len(token.string)))
        except (SyntaxError, UnicodeError, tokenize.TokenError, ValueError):
            return blob  # Malformed source receives the ordinary strict scan.
    result = bytearray(blob)
    for start, end in spans:
        result[start:end] = b'value'  # Same five-byte width; surrounding prose stays intact.
    return bytes(result)


def audit_entries(entries: dict[str, tuple[str, bytes]], require_manifest: bool = True) -> list[str]:
    problems: list[str] = []
    paths = set(entries)
    if require_manifest:
        for missing in sorted(required_public_paths(entries) - paths):
            problems.append(f"missing required public file: {missing}")
    for path, (mode, blob) in sorted(entries.items()):
        problem = path_problem(path)
        if problem:
            problems.append(f"{path}: {problem}")
        # Apply to package runtime paths too, without treating test-negative literals
        # or retirement documentation as executable archive dependencies.
        if "narwhal3d" in PurePosixPath(path).parts or "runtime" in PurePosixPath(path).parts:
            archive_problem = runtime_archive_problem(blob)
            if archive_problem:
                problems.append(f"{path}: {archive_problem}")
        if mode == "120000":
            problems.append(f"{path}: symlinks are not permitted in the public source root")
            continue
        if path in REQUIRED_EXECUTABLES and mode != "100755":
            problems.append(f"{path}: required launcher or test harness is not executable")
        for marker in REQUIRED_CONTENT.get(path, ()):
            if marker not in blob:
                problems.append(f"{path}: missing required structural marker {marker!r}")
        if PurePosixPath(path).suffix == ".py":
            try:
                compile(blob, path, "exec")
            except (SyntaxError, UnicodeError, ValueError) as exc:
                problems.append(f"{path}: Python source does not compile: {exc}")
        if b"\0" in blob and path not in FROZEN_DXF23_FILES and path not in FROZEN_SELECTION_FILES:
            continue
        try:
            scan = frozen_data_scan(path, frozen_json_scan(path, frozen_dxf23_scan(path, frozen_preview24_scan(path, frozen_selection_scan(path, frozen_nearest_scan(path, blob))))))
        except (SyntaxError, UnicodeError, ValueError) as exc:
            problems.append(f"{path}: invalid frozen DATA fixture: {exc}")
            scan = blob
        folded = scan.lower()
        if path not in PROVENANCE_SCAN_EXEMPT:
            provenance = code_provenance_scan(path, scan).lower()
            if path in {"native/automation_core/src/conversation.rs",
                        "native/automation_core/src/primitives.rs",
                        "native/automation_core/src/approval_lifecycle.rs",
                        "native/automation_core/src/plan_review.rs",
                        "native/automation_core/src/modeling.rs",
                        "native/automation_core/src/export_action.rs"}:
                # A typed request-token field/parameter is code, not private
                # attribution. Keep comments, strings and secret scans intact.
                provenance = re.sub(rb"(?m)^[ \t]*(?:pub )?owner: &?token,[ \t]*$", b"", folded)
                provenance = re.sub(rb"(?m)^([ \t]*let )owner: token(?=\s*=)", rb"\1request_token", provenance)
                provenance = re.sub(rb'(?m)^([ \t]*(?:pub(?:\(crate\))? )?fn [a-z_]+\([^"\n]*?)owner: (&?token)(?=[,)])',
                                    rb'\1request_token: \2', provenance)
                provenance = re.sub(rb"(?m)^[ \t]*owner: token\.owner,[ \t]*$", b"", provenance)
            if path == "native/automation_core/tests/modeling_policy.rs":
                provenance = re.sub(rb"(?m)^[ \t]*owner: token \{[ \t]*$", b"", provenance)
            for marker in FORBIDDEN_TEXT:
                if marker in provenance:
                    problems.append(
                        f"{path}: contains forbidden private/legacy marker {marker!r}")
            for pattern in FORBIDDEN_PATTERNS:
                match = pattern.search(folded)
                if match:
                    problems.append(
                        f"{path}: contains forbidden development marker {match.group(0)!r}")
            if path not in LEGACY_TEXT_ALLOWED:
                match = LEGACY_TEXT_PATTERN.search(legacy_scan_bytes(path, scan))
                if match:
                    problems.append(
                        f"{path}: contains forbidden legacy product marker {match.group(0)!r}")
        for marker in SECRET_MARKERS:
            if marker.lower() in (blob.lower() if path in FROZEN_DXF23_FILES or path in FROZEN_PREVIEW24_FILES or path in FROZEN_SELECTION_FILES or path in FROZEN_NEAREST_FILES else folded):
                problems.append(f"{path}: contains possible secret marker {marker!r}")
    return problems


def require_root_commit(root: Path, ref: str) -> str | None:
    line = _run(root, "git", "rev-list", "--parents", "-n", "1", ref).stdout.decode().strip()
    fields = line.split()
    if len(fields) != 1:
        return f"{ref} is not a root commit: {line}"
    return None


def self_check() -> None:
    safe = {path: ("100644", b"safe\n") for path in REQUIRED}
    for path in REQUIRED_EXECUTABLES:
        safe[path] = ("100755", b"#!/bin/sh\nexit 0\n")
    for path, name in FROZEN_DATA_FILES.items():
        safe[path] = ("100644", f'import json\n{name} = json.loads("[]")\n'.encode())
    for path in FROZEN_JSON_FILES:
        safe[path] = ("100644", (ROOT / path).read_bytes())
    safe["narwhal3d/commands.py"] = (
        "100644",
        b"class Command:\n    pass\n\ndef _build_registry():\n    return {}\n\n"
        b"COMMANDS: dict[str, Command] = _build_registry()\n",
    )
    safe["scripts/create_public_branch.py"] = (
        "100644",
        b"BRANCH = \"narwhal3d-public\"\n"
        b"for directory in (\"narwhal3d\", \"packaging\", \"licenses\", \".github\", \"artwork\"):\n"
        b"    pass\nROOT_GATE = \"--require-root-commit\"\n",
    )
    safe["narwhal3d/icons/narwhal-box.png"] = ("100644", b"\x89PNG\r\n\x1a\n\0")
    safe["narwhal3d/cdr_reader.py"] = ("100644", b"def read_cdr(path):\n    return path\n")
    safe["narwhal3d/cdr_writer.py"] = ("100644", b"def write_cdr(path):\n    return path\n")
    safe["spike/test_cdr.py"] = ("100644", b"print('public CDR test')\n")
    safe["dependency-name.txt"] = ("100644", b"rhi" + b"no3dm is required\n")
    safe["COMPATIBILITY.md"] = (
        "100644", b"Target: Rhi" + b"noceros 3D\nhttps://www.rhi" + b"no3d.com/\n")
    safe["native/automation_core/src/primitives.rs"] = (
        "100644", b"pub struct Proposal {\n    pub owner: Token,\n}\n")
    safe["native/automation_core/src/conversation.rs"] = (
        "100644", b"fn scope(\n    owner: &Token,\n) {}\n")
    safe["native/automation_core/src/approval_lifecycle.rs"] = (
        "100644", b"pub(crate) fn get(&self, owner: &Token, call_id: &str) {}\n"
        b"    let owner: Token = parse(\"public\");\n    owner: token.owner,\n")
    for path in ("native/automation_core/src/modeling.rs",
                 "native/automation_core/src/export_action.rs"):
        safe[path] = ("100644", b"pub struct Ticket {\n    pub owner: Token,\n}\n")
    safe["native/automation_core/tests/modeling_policy.rs"] = (
        "100644", b"let forged = Ticket {\n    owner: Token {\n        conversation: 2,\n    },\n};\n")
    if audit_entries(safe):
        raise AuditError("self-check rejected a safe synthetic manifest")
    cases = (
        ("private path", {"bug_" + "reports/report.md": ("100644", b"x")}),
        ("legacy product", {"notes.txt": ("100644", b"Coco " + b"CAD")}),
        ("former tracer name", {"native/picture_trace/Cargo.toml": ("100644", b"members = [\"crates/coco" + b"-cutline\"]\n")}),
        ("personal path", {"notes.txt": ("100644", b"/home/" + b"nathaniel/model.3dm")}),
        ("private attribution", {"notes.py": ("100644", b"# " + b"Owner's words\n")}),
        ("Rust private attribution", {"native/automation_core/src/primitives.rs":
            ("100644", b"// Owner: private notes\n    pub owner: Token,\n")}),
        ("Rust private string", {"native/automation_core/src/conversation.rs":
            ("100644", b'const NOTE: &str = "Owner: private";\n')}),
        ("Rust field trailing private notes", {"native/automation_core/src/primitives.rs":
            ("100644", b"    pub owner: Token, // Owner: private\n")}),
        ("Rust field with secret", {"native/automation_core/src/primitives.rs":
            ("100644", b"    pub owner: Token,\n// gh" + b"p_private\n")}),
        ("private document", {"notes.py": ("100644", b"# See docs/" + b"KERNEL.md\n")}),
        ("task identifier", {"notes.py": ("100644", b"# " + b"G-112 implementation\n")}),
        ("numbered report", {"notes.py": ("100644", b"# bug report " + b"#74\n")}),
        ("handoff lore", {"notes.py": ("100644", b"# frontier hand" + b"off\n")}),
        ("old namespace", {"notes.py": ("100644", b"import rhi" + b"no_addon\n")}),
        ("legacy product word", {"notes.py": ("100644", b"# rhi" + b"no behavior\n")}),
        ("truncated command registry",
         {"narwhal3d/commands.py": ("100644", b"PLACEHOLDER_FOR_SIZE\n")}),
        ("non-executable test harness", {"run_tests.sh": ("100644", b"safe\n")}),
        ("invalid Python", {"broken.py": ("100644", b"def broken(:\n")}),
        ("unexpected image", {"shot.png": ("100644", b"png")}),
        ("unapproved generated icon", {"narwhal3d/icons/generated-private-screenshot.png": ("100644", b"png")}),
        ("commercial miner", {"spike/rhi" + "no_help.py": ("100644", b"x")}),
        ("symlink", {"escape": ("120000", b"../private")}),
        ("retired archive", {"legacy/retired-python-old/source.py": ("100644", b"safe\n")}),
        ("retired packaged archive", {"app/narwhal3d/legacy/source.py": ("100644", b"safe\n")}),
        ("runtime archive import", {"narwhal3d/retired_loader.py": ("100644", b"from legacy import source\n")}),
    )
    for path in ("native/automation_core/src/modeling.rs",
                 "native/automation_core/src/export_action.rs",
                 "native/automation_core/tests/modeling_policy.rs"):
        for blob in (b"// Owner: private notes\n",
                     b'const NOTE: &str = "Owner: private";\n'):
            cases += (("modeling private attribution " + path,
                       {path: ("100644", blob)}),)
    for retired in ('test_ai_box_native.py', 'test_ai_plan_native.py', 'test_ai_v4_native.py', 'test_ai_v6_review.py', 'test_ai_v7_native.py', 'test_ai_v8_native.py'):
        for prefix in ("spike/", "app/spike/"):
            cases += (("retired automation orchestration " + prefix + retired,
                       {prefix + retired: ("100644", b"pass\n")}),)
    for label, addition in cases:
        candidate = dict(safe)
        candidate.update(addition)
        if not audit_entries(candidate):
            raise AuditError(f"self-check failed to reject {label}")
    # The source closure is mandatory even with every conditional harness removed.
    # Delete each input independently: an omitted license/test-only module is just
    # as incomplete as an omitted production module or ctypes adapter.
    closure = sorted(path for path in REQUIRED if path.startswith("native/ui_core/")
                     or path.startswith("native/automation_test_driver/")
                     or path.startswith("spike/test_ui_core_")
                     or path.startswith("spike/test_ui_transcript_")
                     or path.startswith("spike/test_ai_v8_") or path in {
                         "spike/fixtures/numparse_golden.json", "spike/test_pure_core_packaging.py",
                         "launch/check_native.py", "spike/test_desktop_native_startup.py",
                         "narwhal3d/native_textfield.py", "narwhal3d/native_popup.py",
                         "scripts/check_ui_core.py", "scripts/test_release_ui_core.py",
                         "spike/test_shell_popup.py", "spike/test_textfield.py",
                         "native/application_core/tests/history30_golden.rs",
                         "native/application_core/tests/data/history30_trace.json",
                         "native/application_core/tests/data/history30_trace.provenance.json",
                         "spike/test_native_only_policy.py",
                         "spike/test_ai_accepted_review_native.py",
                         'native/geometry_core/src/exact_conversion.rs',
                         'native/geometry_core/cpp/exact_conversion.inc',
                         'native/geometry_core/src/cap.rs',
                         'native/geometry_core/cpp/cap_adapter.inc',
                         'native/geometry_core/cpp/exact_budget.inc',
                         'native/automation_core/tests/join_cap_policy.rs',
                         'narwhal3d/cap_worker.py',
                         'spike/test_cap_surfaces.py',
                         'spike/test_ai_join_cap_host.py',
                         'spike/test_cap_command.py',
                         'native/pure_core/src/curvex.rs',
                         'native/pure_core/src/nearest.rs',
                         'native/pure_core/src/nurbs_record.rs',
                         'native/pure_core/src/curvex_polyline_cases.rs',
                         'native/pure_core/src/nearest_polyline_cases.rs',
                         'spike/test_polyline_curve_hits.py',
                         'spike/test_polyline_nearest.py',
                         'spike/test_polyline_samples.py',
                         'spike/test_pure_core_polyline_samples_gate.py',
                         'spike/test_polycurve_samples.py',
                         'spike/test_pure_core_polycurve_samples_gate.py',
                         'spike/test_pure_core_curve_record_hits_gate.py',
                         'spike/test_pure_core_polyline_nearest_gate.py',
                         'native/geometry_core/src/join.rs',
                         'native/geometry_core/cpp/join_adapter.inc',
                         'spike/test_join_surfaces.py',
                         'spike/test_join_surface_command.py',
                         'spike/test_join_surface_command_mutation.py',
                         'narwhal3d/ai_modeling.py',
                         'narwhal3d/display_prepare.py',
                         'native/automation_core/src/curve_inspection.rs',
                         'native/automation_core/src/export_action.rs',
                         'native/automation_core/src/modeling.rs',
                         'native/automation_core/src/visual.rs',
                         'native/automation_core/tests/modeling_policy.rs',
                         'native/automation_core/tests/modeling_recovery.rs',
                         'native/automation_core/tests/provider_recovery.rs',
                         'spike/test_ai_modeling_host.py',
    'spike/test_ai_nurbs_surface_host.py',
    'native/automation_core/tests/tool_budget.rs',
                         'spike/test_async_display_host.py',
                     })
    for path in closure:
        candidate = dict(safe)
        del candidate[path]
        expected = f"missing required public file: {path}"
        if expected not in audit_entries(candidate):
            raise AuditError(f"self-check failed to reject missing closure input: {path}")
    # Exercise recursive import discovery with names outside the static manifest.
    candidate = dict(safe)
    candidate["spike/test_ui_core_popup_failure.py"] = (
        "100644", b"import test_public_closure_fixture\n")
    candidate["spike/test_public_closure_fixture.py"] = (
        "100644", b"from test_public_closure_leaf import value\n")
    candidate["spike/test_public_closure_leaf.py"] = ("100644", b"value = 1\n")
    if audit_entries(candidate):
        raise AuditError("self-check rejected complete recursive test closure")
    for path in ("spike/test_public_closure_fixture.py", "spike/test_public_closure_leaf.py"):
        mutant = dict(candidate)
        del mutant[path]
        if f"missing required public file: {path}" not in audit_entries(mutant):
            raise AuditError(f"self-check failed to reject missing imported test: {path}")
    print(f"audit_public_tree.py self-check: OK ({len(closure)} closure deletions + 2 import deletions)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository/tree root")
    parser.add_argument("--git-ref", help="audit blobs stored in this Git ref")
    parser.add_argument("--require-root-commit", action="store_true", help="require --git-ref to have no parent")
    parser.add_argument("--self-check", action="store_true", help="exercise built-in allow/reject cases")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    root = args.root.resolve()
    if args.require_root_commit and not args.git_ref:
        raise AuditError("--require-root-commit requires --git-ref")
    entries = git_entries(root, args.git_ref) if args.git_ref else filesystem_entries(root)
    problems = audit_entries(entries)
    if args.git_ref and args.require_root_commit:
        root_problem = require_root_commit(root, args.git_ref)
        if root_problem:
            problems.insert(0, root_problem)
    if problems:
        print(f"PUBLIC TREE AUDIT FAILED: {len(problems)} problem(s)", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"PUBLIC TREE AUDIT PASSED: {len(entries)} file(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        raise SystemExit(f"PUBLIC TREE AUDIT ERROR: {exc}") from exc

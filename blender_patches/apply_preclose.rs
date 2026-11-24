//! Opt-in native pre-close mailbox. No Python or application model is accessed
//! during GHOST event dispatch; a main-thread application timer owns approval.
use std::{env, fs, path::Path};
const HEADER: &str = "windowmanager/WM_narwhal_close.hh";
const HEADER_TEXT: &str = r#"/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
namespace blender {
bool WM_narwhal_defer_close_get();
void WM_narwhal_defer_close_set(bool enabled);
bool WM_narwhal_close_requested_get();
void WM_narwhal_close_requested_set(bool requested);
}
"#;
const EDITS: &[(&str, &str, &str)] = &[
    (
        "windowmanager/intern/wm_window.cc",
        "#include \"WM_api.hh\"",
        "#include \"WM_api.hh\"\n#include \"WM_narwhal_close.hh\"",
    ),
    (
        "windowmanager/intern/wm_window.cc",
        "void wm_quit_with_optional_confirmation_prompt(bContext *C, wmWindow *win)\n{",
        r#"// Process-local, main-thread-only state: never saved in a .blend or enabled by default.
static bool narwhal_defer_close = false;
static bool narwhal_close_requested = false;
bool WM_narwhal_defer_close_get() { return narwhal_defer_close; }
void WM_narwhal_defer_close_set(bool enabled)
{
  narwhal_defer_close = enabled;
  if (!enabled) { narwhal_close_requested = false; }
}
bool WM_narwhal_close_requested_get() { return narwhal_close_requested; }
void WM_narwhal_close_requested_set(bool requested) { narwhal_close_requested = requested; }

void wm_quit_with_optional_confirmation_prompt(bContext *C, wmWindow *win)
{
  // NARWHAL_PRECLOSE_V1: before any exit handler is installed or Python is torn down.
  // Repeated close requests coalesce. Approval explicitly disables this opt-in guard.
  if (WM_narwhal_defer_close_get()) {
    WM_narwhal_close_requested_set(true);
    return;
  }"#,
    ),
    (
        "makesrna/intern/rna_wm.cc",
        "#ifdef RNA_RUNTIME",
        "#ifdef RNA_RUNTIME\n#  include \"WM_narwhal_close.hh\"",
    ),
];
// rna_wm.cc has two RNA_RUNTIME sections. Include only in the first; callbacks
// belong in the runtime implementation section, not the RNA definition pass.
const RUNTIME_ANCHOR: &str =
    "namespace blender {\n\nstatic wmOperator *rna_OperatorProperties_find_operator";
const CALLBACKS: &str = r#"namespace blender {

static bool rna_WindowManager_narwhal_defer_close_get(PointerRNA *)
{ return WM_narwhal_defer_close_get(); }
static void rna_WindowManager_narwhal_defer_close_set(PointerRNA *, bool enabled)
{ WM_narwhal_defer_close_set(enabled); }
static bool rna_WindowManager_narwhal_close_requested_get(PointerRNA *)
{ return WM_narwhal_close_requested_get(); }
static void rna_WindowManager_narwhal_close_requested_set(PointerRNA *, bool requested)
{ WM_narwhal_close_requested_set(requested); }

static wmOperator *rna_OperatorProperties_find_operator"#;
const PROP_ANCHOR: &str =
    "  prop = RNA_def_property(srna, \"operators\", PROP_COLLECTION, PROP_NONE);";
const PROPS: &str = r#"  prop = RNA_def_property(srna, "narwhal_defer_close", PROP_BOOLEAN, PROP_NONE);
  RNA_def_property_boolean_funcs(prop, "rna_WindowManager_narwhal_defer_close_get",
                                "rna_WindowManager_narwhal_defer_close_set");
  RNA_def_property_flag(prop, PROP_SKIP_SAVE);
  RNA_def_property_ui_text(prop, "Defer application close", "Opt-in main-thread close approval mailbox");

  prop = RNA_def_property(srna, "narwhal_close_requested", PROP_BOOLEAN, PROP_NONE);
  RNA_def_property_boolean_funcs(prop, "rna_WindowManager_narwhal_close_requested_get",
                                "rna_WindowManager_narwhal_close_requested_set");
  RNA_def_property_flag(prop, PROP_SKIP_SAVE);
  RNA_def_property_ui_text(prop, "Application close requested", "Coalesced close request awaiting application approval");

  prop = RNA_def_property(srna, "operators", PROP_COLLECTION, PROP_NONE);"#;
fn replace(text: &mut String, old: &str, new: &str, first: bool) -> Result<(), String> {
    if text.contains(new) && old != new {
        return Ok(());
    }
    if !text.contains(old) || (!first && text.matches(old).count() != 1) {
        return Err(format!("missing or ambiguous preclose anchor: {old}"));
    }
    *text = text.replacen(old, new, 1);
    Ok(())
}
fn apply(root: &Path, check: bool) -> Result<(), String> {
    let paths = [
        "windowmanager/intern/wm_window.cc",
        "makesrna/intern/rna_wm.cc",
    ];
    let mut files = Vec::new();
    for path in paths {
        let mut text = fs::read_to_string(root.join(path)).map_err(|e| e.to_string())?;
        for &(file, old, new) in EDITS {
            if file == path {
                replace(&mut text, old, new, old == "#ifdef RNA_RUNTIME")?;
            }
        }
        if path.ends_with("rna_wm.cc") {
            replace(&mut text, RUNTIME_ANCHOR, CALLBACKS, false)?;
            replace(&mut text, PROP_ANCHOR, PROPS, false)?;
        }
        if check && fs::read_to_string(root.join(path)).unwrap() != text {
            return Err(format!("unapplied preclose patch: {path}"));
        }
        files.push((path, text));
    }
    if check {
        if fs::read_to_string(root.join(HEADER)).ok().as_deref() != Some(HEADER_TEXT) {
            return Err("missing preclose header".into());
        }
    } else {
        if root.join(HEADER).exists()
            && fs::read_to_string(root.join(HEADER)).unwrap() != HEADER_TEXT
        {
            return Err("preclose header conflict".into());
        }
        for (path, text) in files {
            fs::write(root.join(path), text).map_err(|e| e.to_string())?;
        }
        fs::write(root.join(HEADER), HEADER_TEXT).map_err(|e| e.to_string())?;
    }
    Ok(())
}
fn main() {
    let args: Vec<_> = env::args().collect();
    if args.len() < 2 || args.len() > 3 {
        panic!("apply_preclose SOURCE/source/blender [--check]");
    }
    apply(
        Path::new(&args[1]),
        args.get(2).is_some_and(|a| a == "--check"),
    )
    .unwrap();
    println!("preclose patch: OK");
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn replacement_is_idempotent_and_missing_anchor_refuses() {
        let mut text = "before OLD after".to_string();
        replace(&mut text, "OLD", "NEW", false).unwrap();
        let done = text.clone();
        replace(&mut text, "OLD", "NEW", false).unwrap();
        assert_eq!(text, done);
        assert!(replace(&mut text, "absent", "other", false).is_err());
    }
}

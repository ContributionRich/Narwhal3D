//! Actual patched C++ function qualification without a linked GUI executable.
//! This is NOT an interactive/title-bar integration test. A candidate GUI run is
//! separately required before shipping. Rust owns tooling; C++ shim is host-only.
use std::{env, fs, path::PathBuf, process::Command};
fn root() -> PathBuf {
    PathBuf::from(file!())
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}
fn source() -> String {
    let base = PathBuf::from(
        env::var("NARWHAL_BLENDER_SOURCE").expect("point at the patched candidate source"),
    );
    let text =
        fs::read_to_string(base.join("source/blender/windowmanager/intern/wm_window.cc")).unwrap();
    let start = text
        .find("static bool narwhal_defer_close = false;")
        .expect("preclose patch required");
    let end = text[start..].find("/** \\} */").unwrap() + start;
    text[start..end].to_string()
}
fn cpp(function: &str) -> String {
    format!(
        r#"#include <cassert>
namespace blender {{
struct bContext {{}}; struct wmWindow {{}}; struct wmWindowManager {{}};
static wmWindow win; static wmWindow *context_win = &win;
static int scheduled = 0, confirmed = 0; static bool unsaved = false;
static struct {{ int uiflag = 0; }} U;
static struct {{ bool background = false; }} G;
static constexpr int USER_SAVE_PROMPT = 1;
wmWindow *CTX_wm_window(bContext *) {{ return context_win; }}
void CTX_wm_window_set(bContext *, wmWindow *w) {{ context_win = w; }}
int CTX_data_main(bContext *) {{ return 0; }}
wmWindowManager *CTX_wm_manager(bContext *) {{ return nullptr; }}
bool wm_file_or_session_data_has_unsaved_changes(int, wmWindowManager *) {{ return unsaved; }}
void wm_window_raise(wmWindow *) {{}}
void wm_confirm_quit(bContext *) {{ ++confirmed; }}
void wm_exit_schedule_delayed(bContext *) {{ ++scheduled; }}
{function}
}}
int main() {{
 using namespace blender; bContext ctx;
 assert(!WM_narwhal_defer_close_get());
 wm_quit_with_optional_confirmation_prompt(&ctx, &win);
 assert(scheduled == 1); // Stock default unchanged.
 WM_narwhal_defer_close_set(true);
 wm_quit_with_optional_confirmation_prompt(&ctx, &win);
 wm_quit_with_optional_confirmation_prompt(&ctx, &win);
 assert(WM_narwhal_close_requested_get());
 assert(scheduled == 1); // No native exit handler scheduled while deferred.
 WM_narwhal_close_requested_set(false); // Cancel/consume, retain protection.
 assert(WM_narwhal_defer_close_get());
 assert(!WM_narwhal_close_requested_get());
 WM_narwhal_close_requested_set(true);
 WM_narwhal_defer_close_set(false); // Explicit approval only.
 assert(!WM_narwhal_close_requested_get());
 wm_quit_with_optional_confirmation_prompt(&ctx, &win);
 assert(scheduled == 2);
 U.uiflag = USER_SAVE_PROMPT; unsaved = true;
 wm_quit_with_optional_confirmation_prompt(&ctx, &win);
 assert(confirmed == 1 && scheduled == 2);
 G.background = true;
 wm_quit_with_optional_confirmation_prompt(&ctx, &win);
 assert(scheduled == 3);
 assert(context_win == &win);
}}
"#
    )
}
fn run(source: &str, label: &str) -> bool {
    let scratch = PathBuf::from(env::var("NARWHAL_SHUTDOWN_ARTIFACTS").unwrap());
    fs::create_dir_all(&scratch).unwrap();
    let src = scratch.join(format!("preclose-{label}.cc"));
    let bin = scratch.join(format!("preclose-{label}"));
    fs::write(&src, source).unwrap();
    let compile = Command::new("c++")
        .args(["-std=c++17", "-O0"])
        .arg(src)
        .arg("-o")
        .arg(&bin)
        .output()
        .unwrap();
    assert!(
        compile.status.success(),
        "C++ qualification compile failed: {}",
        String::from_utf8_lossy(&compile.stderr)
    );
    let result = Command::new(bin).output().unwrap();
    let text = format!(
        "status={}\n{}\n{}",
        result.status,
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
    fs::write(scratch.join(format!("preclose-{label}.log")), &text).unwrap();
    if label.starts_with("red-") {
        assert!(
            text.to_lowercase().contains("assertion"),
            "native mutation must fail an assertion: {text}"
        );
        println!("MUTATION RED: native preclose {label}");
    }
    result.status.success()
}
#[test]
fn actual_hook_default_defer_cancel_approve_and_mutation() {
    let function = source();
    assert!(run(&cpp(&function), "green"));
    assert_eq!(
        function
            .matches("if (WM_narwhal_defer_close_get())")
            .count(),
        1
    );
    let broken = function.replacen("if (WM_narwhal_defer_close_get())", "if (false)", 1);
    assert!(
        !run(&cpp(&broken), "red-bypass"),
        "mutation must fail the native schedule assertion"
    );
    let stale = function.replacen(
        "if (!enabled) { narwhal_close_requested = false; }",
        "if (false) { narwhal_close_requested = false; }",
        1,
    );
    assert_ne!(stale, function);
    assert!(!run(&cpp(&stale), "red-stale-request"));
    assert!(run(&cpp(&function), "restored-green"));
}
#[test]
fn packaged_patch_and_build_provenance_are_wired() {
    let root = root();
    let build = fs::read_to_string(root.join("scripts/build_blender_linux.sh")).unwrap();
    assert!(build.contains("PRECLOSE_PATCH=\"$PATCH_DIR/apply_preclose.rs\""));
    assert!(build.contains("\"$PRECLOSE_TOOL_DIR/apply_preclose\" \"$SOURCE_DIR/source/blender\""));
    assert!(build.contains("(\"blender_patches/apply_preclose.rs\", Path(sys.argv[13]))"));
    for file in [
        "scripts/assemble_linux_release.py",
        "scripts/verify_linux_release.py",
    ] {
        assert!(
            fs::read_to_string(root.join(file))
                .unwrap()
                .contains("\"blender_patches/apply_preclose.rs\"")
        );
    }
}

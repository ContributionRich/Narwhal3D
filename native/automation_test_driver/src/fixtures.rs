use crate::{Status, classify, positive};
use std::path::Path;
pub const LEGACY_TEST: &str = "test_old_library_reports_explicit_unavailability";
#[derive(Debug, Clone, Copy)]
pub enum FixtureExpectation {
    Absent,
    Current,
    Legacy,
}
pub fn fixture_command(
    python: &Path,
    root: &Path,
    executable: &Path,
    cli: Option<&Path>,
    environment: Option<&Path>,
) -> Vec<String> {
    let mut command = vec![
        "timeout".into(),
        "--signal=TERM".into(),
        "--kill-after=2s".into(),
        "20s".into(),
        "env".into(),
        "-u".into(),
        "NARWHAL_AI_LEGACY_LIBRARY".into(),
    ];
    if let Some(path) = environment {
        command.push(format!("NARWHAL_AI_LEGACY_LIBRARY={}", path.display()));
    }
    command.extend([
        python.display().to_string(),
        "-B".into(),
        root.join("spike/test_ai_accepted_review.py")
            .display()
            .to_string(),
        "--native-executable".into(),
        executable.display().to_string(),
        "--test".into(),
        LEGACY_TEST.into(),
    ]);
    if let Some(path) = cli {
        command.extend(["--legacy-library".into(), path.display().to_string()]);
    }
    command
}
pub fn fixture_result(expect: FixtureExpectation, code: i32, out: &str) -> bool {
    let known = out.contains(LEGACY_TEST) && out.contains("Ran 1 test");
    if !known {
        return false;
    }
    match expect {
        FixtureExpectation::Absent => {
            code == 0 && out.contains("skipped=1") && out.contains("--legacy-library")
        }
        FixtureExpectation::Current => {
            classify(code, out, false) == Status::Killed
                && out.contains("accepted_review_call_required")
                && !out.contains("skipped=")
        }
        FixtureExpectation::Legacy => {
            code == 0 && positive(out, false) && !out.contains("skipped=")
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixture_controls_cannot_skip_explicit_libraries() {
        let skipped =
            format!("{LEGACY_TEST} ... skipped '--legacy-library'\nRan 1 test\nOK (skipped=1)");
        assert!(fixture_result(FixtureExpectation::Absent, 0, &skipped));
        assert!(!fixture_result(FixtureExpectation::Current, 0, &skipped));
        assert!(!fixture_result(FixtureExpectation::Legacy, 0, &skipped));
        let failed = format!(
            "FAIL: {LEGACY_TEST}\nAssertionError: accepted_review_call_required\nRan 1 test\nFAILED (failures=1)"
        );
        assert!(fixture_result(FixtureExpectation::Current, 1, &failed));
        assert!(!fixture_result(FixtureExpectation::Legacy, 1, &failed));
        assert!(!fixture_result(
            FixtureExpectation::Current,
            1,
            "ImportError accepted_review_call_required"
        ));
    }
    #[test]
    fn cli_and_environment_precedence_commands() {
        let p = Path::new("/python");
        let root = Path::new("/root");
        let exe = Path::new("/target/debug/deps/test");
        let legacy = Path::new("/legacy.so");
        let current = Path::new("/current.so");
        let absent = fixture_command(p, root, exe, None, None);
        assert!(
            absent
                .windows(2)
                .any(|w| w == ["-u", "NARWHAL_AI_LEGACY_LIBRARY"])
        );
        assert!(
            !absent
                .iter()
                .any(|s| s.starts_with("NARWHAL_AI_LEGACY_LIBRARY="))
        );
        let env = fixture_command(p, root, exe, None, Some(legacy));
        assert!(env.contains(&"NARWHAL_AI_LEGACY_LIBRARY=/legacy.so".into()));
        assert!(!env.contains(&"--legacy-library".into()));
        let cli = fixture_command(p, root, exe, Some(legacy), Some(current));
        assert!(cli.contains(&"NARWHAL_AI_LEGACY_LIBRARY=/current.so".into()));
        assert!(
            cli.windows(2)
                .any(|w| w == ["--legacy-library", "/legacy.so"])
        );
    }
}

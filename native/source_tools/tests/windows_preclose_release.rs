//! Build-recipe admission only; real Windows compilation/RNA remains a release gate.
use std::{fs, path::PathBuf};

fn source_root() -> PathBuf {
    std::env::var_os("NARWHAL_SOURCE_TEST_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .to_owned()
        })
}

fn admit(script: &str) -> Result<(), &'static str> {
    for required in [
        "\"apply_preclose.rs\"",
        "function Invoke-PreclosePatch([switch]$Check)",
        "if (-not $Check) {",
        "'windowmanager\\intern\\wm_window.cc', 'makesrna\\intern\\rna_wm.cc'",
        "$OriginalText.Replace(\"`r`n\", \"`n\")",
        "[IO.File]::WriteAllText($SourceFile, $CanonicalText, (New-Object Text.UTF8Encoding($false)))",
        "@('run', '1.95.0', 'rustc', '--edition', '2024',",
        "(Join-Path $PatchDirectory 'apply_preclose.rs'), '-o', $PrecloseTool)",
        "if ($Check) { $PrecloseArguments += '--check' }",
        "Run-Native $PrecloseTool $PrecloseArguments",
        "\nInvoke-PreclosePatch\n",
        "\nInvoke-PreclosePatch -Check\n$ProvenanceArguments",
        "b'narwhal_defer_close', b'narwhal_close_requested'",
        "for name in ('narwhal_defer_close', 'narwhal_close_requested'):",
        "require(name in bpy.types.WindowManager.bl_rna.properties",
        "require(isinstance(original, bool)",
        "require(getattr(manager, name) is (not original)",
        "finally:\n        setattr(manager, name, original)",
    ] {
        if !script.contains(required) {
            return Err(required);
        }
    }
    let patch = script.find("\nInvoke-PreclosePatch\n").unwrap();
    let compile = script
        .find("$RuntimeBuildRunner =")
        .ok_or("runtime build runner")?;
    let check = script.find("\nInvoke-PreclosePatch -Check\n").unwrap();
    let resume = script
        .find("'require current compiled runtime before resuming packaging'")
        .ok_or("resume admission")?;
    let record = script
        .find("$RecordArguments =")
        .ok_or("record admission")?;
    if !(patch < compile && compile < check && check < resume && resume < record) {
        return Err("patch/build/check/record order");
    }
    Ok(())
}

#[test]
fn windows_preclose_build_and_resume_are_fail_closed() {
    let script =
        fs::read_to_string(source_root().join("scripts/build_windows_preview.ps1")).unwrap();
    assert_eq!(admit(&script), Ok(()));
}

#[test]
fn windows_preclose_old_recipe_and_missing_api_are_rejected() {
    let script =
        fs::read_to_string(source_root().join("scripts/build_windows_preview.ps1")).unwrap();
    for missing in [
        "\"apply_preclose.rs\"",
        "\nInvoke-PreclosePatch\n",
        "\nInvoke-PreclosePatch -Check\n",
        "b'narwhal_defer_close', b'narwhal_close_requested'",
        "require(name in bpy.types.WindowManager.bl_rna.properties",
        "require(getattr(manager, name) is (not original)",
        "finally:\n        setattr(manager, name, original)",
    ] {
        assert!(script.contains(missing));
        assert!(
            admit(&script.replacen(missing, "", 1)).is_err(),
            "mutation survived: {missing}"
        );
    }
}

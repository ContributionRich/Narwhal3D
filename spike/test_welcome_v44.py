"""Tiny source contracts, NOT compiled or visual acceptance.

Python is test orchestration only: checks the Rust integration without building
under a bounded-memory validation profile. Mutations touch isolated temporary copies
of the two welcome sources, never the working tree. No imports from the product,
network, assets, manifests, binaries, or GUI. Run with python3 -B.
"""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native/welcome/src"


def contracts(directory):
    main = (directory / "main.rs").read_text().split("#[cfg(test)]\nmod tests")[0]
    theme = (directory / "theme.rs").read_text().split("#[cfg(test)]\nmod tests")[0]
    compact = "".join(main.split())
    style = "".join(theme.split())
    failures = []

    def require(name, condition):
        if not condition:
            failures.append(name)

    require("dimensions", "[640.0,470.0]" in compact and ".corner_radius(12)" in compact)
    require("layout", all(token in compact for token in (
        "bounds.top()+58.0", "bounds.right()-205.0", "bounds.bottom()-60.0",
        'id_salt("welcome-content")', 'id_salt("welcome-sidebar")',
        'Grid::new("welcome-units").num_columns(2)', "[width,42.0]",
        "[220.0,40.0]", ".desired_height(6.0)", "Vec2::splat(33.0)",
    )))
    require("version hierarchy", all(token in main for token in (
        'RichText::new("Version")', 'RichText::new("Installed version")',
        'unwrap_or("Not provided")', 'self.update_status.label()',
    )) and 'ui.heading("News")' not in main)
    require("all states", all(f'Self::{state} =>' in main for state in (
        "Idle", "Checking", "Unavailable", "Unsafe", "Current", "Available", "Updating",
    )))
    require("genuine action", 'self.update_status==UpdateStatus::Available&&ui.button("Downloadupdate")' in compact
            and 'self.start_install(release)' in compact)
    require("checked outcome", 'Ok(classify_update(&releases,current,platform,overridden))' in compact
            and 'self.update_status=check.status;' in compact
            and 'self.available_release=check.release;' in compact
            and 'Checked(Ok(None))' not in compact)
    classifier = main.split("fn classify_update(", 1)[1].split("fn check_for_update(", 1)[0]
    require("honest comparison", 'return outcome(UpdateStatus::Current)' in classifier
            and classifier.count('return outcome(UpdateStatus::Unavailable)') >= 4
            and 'trusted_asset_url(&release.asset.browser_download_url, overridden)' in classifier
            and '_ => outcome(UpdateStatus::Unsafe)' in classifier)
    require("event transitions", all(token in compact for token in (
        'update_status:UpdateStatus::Idle', 'self.update_status=UpdateStatus::Checking;',
        'self.update_status=UpdateStatus::Updating;', 'self.update_status=UpdateStatus::Unsafe;',
        'self.update_status=UpdateStatus::Idle;', 'self.available_release=None;',
    )))
    require("native routes", all(token in compact for token in (
        'self.submit_and_hide("new",Some(unit),ui.ctx())',
        'self.submit_and_hide("open-dialog",None,ui.ctx())',
        'self.submit_and_hide("open",Some(path.to_string_lossy().as_ref()),ui.ctx(),)',
        'self.hide_welcome(ui.ctx())', 'progress_state(self.ready,self.started.elapsed())',
        'NARWHAL3D_DISABLE_UPDATE_CHECK', 'NARWHAL3D_VERSION',
    )))
    require("theme", style.count('status_current:rgb(0x32,0xA6,0x78)') == 2 and all(token in style for token in (
        'status_current:rgb(0x32,0xA6,0x78)', 'status_available:rgb(0xE3,0xA2,0x38)',
        'status_working:rgb(0x50,0x8F,0xE8)', 'status_neutral:rgb(0x89,0x95,0xA7)',
        'widget.corner_radius=egui::CornerRadius::same(7)',
        'style.spacing.interact_size.y=34.0', 'NARWHAL3D_THEME',
    )))
    return failures


class WelcomeV44(unittest.TestCase):
    def test_source_contracts(self):
        self.assertEqual(contracts(SOURCE), [])

    def test_isolated_mutations_are_red_and_original_stays_green(self):
        originals = {name: (SOURCE / name).read_text() for name in ("main.rs", "theme.rs")}
        mutations = (
            ("main.rs", "[640.0, 470.0]", "[580.0, 438.0]", "dimensions"),
            ("main.rs", 'RichText::new("Version")', 'RichText::new("News")', "version hierarchy"),
            ("main.rs", 'self.update_status == UpdateStatus::Available', 'self.update_status != UpdateStatus::Available', "genuine action"),
            ("main.rs", "return outcome(UpdateStatus::Unavailable)", "return outcome(UpdateStatus::Current)", "honest comparison"),
            ("main.rs", "self.update_status = UpdateStatus::Checking;", "self.update_status = UpdateStatus::Current;", "event transitions"),
            ("main.rs", ".desired_height(6.0)", ".desired_height(18.0)", "layout"),
            ("main.rs", 'self.submit_and_hide("new", Some(unit), ui.ctx())', 'self.submit_and_hide("new", None, ui.ctx())', "native routes"),
            ("theme.rs", "status_current: rgb(0x32, 0xA6, 0x78)", "status_current: rgb(0, 0, 0)", "theme"),
        )
        with tempfile.TemporaryDirectory(prefix="welcome-v44-source-") as temporary:
            directory = Path(temporary)
            for filename, old, new, failure in mutations:
                with self.subTest(mutation=failure):
                    self.assertTrue(old in originals[filename], f"missing mutation anchor: {failure}")
                    for name, text in originals.items():
                        (directory / name).write_text(text.replace(old, new, 1) if name == filename else text)
                    self.assertIn(failure, contracts(directory))
        self.assertEqual(contracts(SOURCE), [])
        for name, text in originals.items():
            self.assertTrue((SOURCE / name).read_text() == text, f"source changed: {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

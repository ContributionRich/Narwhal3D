//! The only Python below is actual interpreter/ctypes facade qualification.
//! All expected objects come from the pre-conversion independent typed fixture.
use std::path::PathBuf;
use std::process::Command;
#[test]
#[ignore = "explicit actual-host qualification requires candidate library override"]
fn actual_host_facades_types_registry_and_failure_paths() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let python = std::env::var_os("NARWHAL_CONTENT_HOST_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("tools/blender-patched/5.2/python/bin/python3.13"));
    let library = std::env::var_os("NARWHAL_UI_CORE_LIBRARY")
        .expect("host acceptance requires explicit candidate NARWHAL_UI_CORE_LIBRARY");
    let code = r#"
import contextlib, importlib, importlib.abc, importlib.util, io, json, pathlib, sys, types
root=pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root))
f=json.loads((root/'native/ui_core/tests/data/content13_golden.json').read_text())
pkg=types.ModuleType('narwhal3d');pkg.__path__=[str(root/'narwhal3d')];sys.modules['narwhal3d']=pkg
commands=types.ModuleType('narwhal3d.commands');commands.COMMANDS=dict.fromkeys(f['cases'][0]['registry']);sys.modules[commands.__name__]=commands
from narwhal3d import native_ui_content as transport

def exact(actual, expected):
    assert type(actual) is type(expected), (type(actual),type(expected))
    if isinstance(actual,dict):
        assert list(actual)==list(expected), 'dict key order/type or contents changed'
        for key in actual:exact(actual[key],expected[key])
    elif isinstance(actual,(tuple,list)):
        assert len(actual)==len(expected)
        for a,b in zip(actual,expected):exact(a,b)
    else:assert actual==expected,(actual,expected)

modules={name:importlib.import_module('narwhal3d.'+name) for name in f['exports']}
for name,tree in f['exports'].items():
    for key,expected in transport._decode(tree).items():exact(getattr(modules[name],key),expected)
m=modules['menubar_resource_data'];exact(m.MENUS,transport._decode(f['import_menus']));exact(m.REPORT,transport._decode(f['import_report']))
original_tabs=m.toolbar_data.TABS
for case in f['cases']:
    m.toolbar_data.TABS=transport._decode(case['tabs']) if 'tabs' in case else original_tabs
    exact(m._toolbar_groups(set(case['registry'])),transport._decode(case['groups']))
    exact(m._expanded_menus(frozenset(case['registry'])),transport._decode(case['menus']))
m.toolbar_data.TABS=list(original_tabs)
exact(m._toolbar_groups(set(f['cases'][0]['registry'])),transport._decode(f['cases'][0]['groups']))
exact(m._expanded_menus(set(f['cases'][0]['registry'])),transport._decode(f['cases'][0]['menus']))
assert list(m._walk_commands([('item','New','', 'new'),('popup','nested','',[('item','Line','','line')],None)]))==['new','line']
m.toolbar_data.TABS=original_tabs
# Live labels follow the snapshot each time the menu facade is reloaded.
for case in (f['cases'][1],f['cases'][4]):
    commands.COMMANDS=dict.fromkeys(case['registry']);m=importlib.reload(m)
    exact(m.MENUS,transport._decode(case['menus']))
# An unavailable command registry retains the diagnostic and the empty-registry menu.
del sys.modules['narwhal3d.commands']
if hasattr(pkg,'commands'):del pkg.commands
class BrokenRegistry(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='narwhal3d.commands':raise RuntimeError('captured unavailable registry')
block=BrokenRegistry();sys.meta_path.insert(0,block)
try:
    out=io.StringIO()
    with contextlib.redirect_stdout(out):m=importlib.reload(m)
    assert '[narwhal3d] menu generation cannot read the command registry: RuntimeError: captured unavailable registry' in out.getvalue()
    exact(m.MENUS,transport._decode(f['cases'][1]['menus']))
finally:sys.meta_path.remove(block)
# Actual script-style loading remains Blender-free; no package import workaround needed.
for name in ('toolbar_data','toolbar_macro_data','flyout_icon_data'):
    spec=importlib.util.spec_from_file_location('direct_'+name,root/'narwhal3d'/(name+'.py'))
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    for key,expected in transport._decode(f['exports'][name]).items():exact(getattr(obj,key),expected)
assert 'bpy' not in sys.modules
for path in ('/nonexistent-content-library',''):
    import os
    os.environ['NARWHAL_UI_CORE_LIBRARY']=path;transport._binding.cache_clear()
    try:transport.resource('toolbar_data')
    except RuntimeError:pass
    else:raise AssertionError('missing configured native content silently accepted')
print('actual ctypes facades, full typed content/order, six menu cases, live reload, warning, direct builds and missing-library refusal: PASS')
"#;
    let output = Command::new("timeout")
        .args(["90s"])
        .arg(python)
        .args(["-I", "-B", "-c", code])
        .arg(root)
        .env("NARWHAL_UI_CORE_LIBRARY", library)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
        .expect("actual host Python launch");
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    print!("{}", String::from_utf8_lossy(&output.stdout));
}

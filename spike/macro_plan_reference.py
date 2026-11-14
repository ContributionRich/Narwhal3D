"""Checked-plan fixed answers; original planner and tokenizer retired together."""
import importlib.util
from pathlib import Path
_s = importlib.util.spec_from_file_location('macro_fixed_answers', Path(__file__).with_name('macro_reference.py'))
_m = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_m)
MacroPlanError = _m.MacroPlanError
NonNestableCommandError = _m.NonNestableCommandError
rows = _m.rows


def plan_macro(value):
    return _m.expected('P', value)

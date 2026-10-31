"""Visible accepted-review presentation only; Rust retains all action authority.

Python is necessary for Blender's font measurement/layout and displaying native
fields. This module cannot approve, consume, execute or validate geometry.
"""
from . import textfield


def lines(review, remaining_ms):
    action = review['action']
    args = action['arguments']
    rows = [action['operation'].title() + ' — review one new solid',
            'Prototype: OFF in normal catalogs',
            'Center (world XYZ): ' + ', '.join(str(v) for v in args['center']),
            'Radius: ' + str(args['radius'])]
    if 'height' in args:
        rows.append('Height (+world Z): ' + str(args['height']))
    rows += ['Units: ' + review['expected']['append']['units'],
             'Additive only. Selected objects are not modified.',
             'One Undo step. No batch approval.',
             'Document / selection / topology revision: unavailable.',
             'Current geometry is not independently verified.',
             'Review expires in ' + str((max(0, remaining_ms) + 999) // 1000) + ' s.']
    return rows


def layout(review, remaining_ms, rect, measure, scale=1.0):
    x0, y0, x1, y1 = rect
    padding, line_h, button_h = 10*scale, 15*scale, 30*scale
    width = max(1, x1-x0-2*padding)
    wrapped = []
    for row in lines(review, remaining_ms):
        wrapped.extend(row[a:b] for a,b in textfield.wrap_spans(row,width,measure))
    # BLF can leave an indivisible glyph wider than the wrap region. Vertical
    # room alone also says nothing about the two side-by-side decision labels.
    button_width = (x1-x0)/2 - padding - 3*scale
    buttons_fit = max(measure('Approve once'), measure('Reject')) + 4*scale <= button_width
    fits = (len(wrapped)*line_h + 4*padding + button_h <= y1-y0
            and buttons_fit and all(measure(line.rstrip()) <= width for line in wrapped))
    mid = (x0+x1)/2
    return {'lines':wrapped, 'line_h':line_h, 'x':x0+padding, 'top':y1-padding,
            'fits':fits, 'approve':(x0+padding,y0+padding,mid-3*scale,y0+padding+button_h) if fits else None,
            'reject':(mid+3*scale,y0+padding,x1-padding,y0+padding+button_h) if fits else None}


def outcome_text(result):
    output = result.get('output', {})
    if output.get('created') is True and not result.get('is_error', True):
        return 'Native creation receipt confirmed. One Undo step. Current geometry is not independently reverified.'
    if output.get('may_have_published') is True or output.get('outcome') == 'unknown':
        return 'Publication outcome UNKNOWN. An object may exist. Inspect the document; no automatic retry.'
    state = output.get('review_state')
    if state == 'expired':
        return 'Review expired. No action was approved for publication.'
    if state == 'denied':
        return 'Review rejected. Nothing created by this action.'
    if state == 'invalidated':
        return 'Review invalidated by changed scope. No automatic retry.'
    return 'Action failed before publication or was rolled back. No successful creation receipt.'


def plan_text(output):
    """Bounded native preflight display; never interpret model prose as evidence."""
    if not isinstance(output, dict) or output.get('status') != 'plan_preflight':
        return None
    labels = {
        'executor_not_integrated': 'executor not integrated',
        'upstream_step_blocked': 'an earlier step is blocked',
        'planned_outputs_not_available': 'earlier geometry does not exist yet',
        'separate_explicit_action_approval_required': 'separate action approval required',
        'workspace_units_missing_or_changed': 'inspect current document units',
        'unit_scale_unavailable': 'unit scale unavailable',
        'geometry_intent_requires_clarification': 'clarify visual study versus manufacturing',
        'fresh_geometry_and_scope_verification_required': 'fresh geometry and scope evidence required',
        'object_not_inspected_in_current_turn': 'inspect the referenced object',
        'command_idle_not_verified': 'command idle state not verified',
        'destination_layer_unavailable': 'destination layer unavailable',
        'clarification_required': 'answer the pending questions',
    }
    rows = ['PLAN ONLY — no geometry created or verified.']
    for step in output.get('steps', ())[:16]:
        blockers = ', '.join(labels.get(code, code) for code in step.get('blockers', ())[:8]) or 'separate execution still required'
        rows.append(str(step['id']) + ': ' + str(step['operation']) + ' — ' + blockers)
        if step.get('unsupported_need'):
            rows.append('Unavailable: ' + step['unsupported_need'])
    rows += ['Question: ' + q for q in output.get('questions', ())[:4]]
    rows.append('Requested verification predicates have not been checked.')
    return '\n'.join(rows)

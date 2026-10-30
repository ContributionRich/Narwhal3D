"""Unregistered NEXT-RELEASE primitive publisher prototypes; live tools remain OFF.

Python only reaches Blender/OCCT and existing publication/Undo APIs. Rust owns the
arguments, approval, one-use consumption and geometric acceptance comparisons.
Main must review real geometry/rollback/Undo receipts before any UI activation.
"""
from . import ai_assistant


def context(app, document_session, *, record=0):
    from .ai_box import context as append_context
    ai_assistant.WorkflowSession._main_thread()
    stack = app.ctx.undo
    return {'append': append_context(app, document_session, record=record),
            'undo_top_serial': stack.top_serial,
            'undo_next_serial': stack.next_serial - (1 if record else 0)}


def publish_prototype(app, workflow, proposal, document_session, scope_check):
    """Development-only. Consume the exact Rust approval before constructing anything.

    No model tool or command is registered for this function. Failed publication
    cannot reuse the approval. Existing guarded publication rolls back only this
    object's bindings/allocations and retains unrelated external edits.
    """
    from . import bridge, kernel
    from .kernel import occt_backend as occt
    ai_assistant.WorkflowSession._main_thread()
    scope_check()
    model, file, ctx = app.model, app.model.file, app.ctx
    current = context(app, document_session)
    consumed = workflow.take_primitive_prototype(proposal, document_session, current)
    if consumed.get('status') != 'primitive_prototype_consumed':
        raise ai_assistant.WorkflowUnavailable('primitive_approval_required')
    expected, action = consumed['context'], consumed['action']
    record = 0

    def check():
        scope_check()
        if app.model is not model or model.file is not file or app.ctx is not ctx:
            raise ai_assistant.WorkflowUnavailable('primitive_document_changed')
        if context(app, document_session, record=record) != expected:
            raise ai_assistant.WorkflowUnavailable('primitive_context_stale')

    check()
    args = action['arguments']
    if action['operation'] == 'sphere':
        geom = bridge.make_sphere(args['center'], args['radius'])
        name = 'Sphere'
    elif action['operation'] == 'cylinder':
        geom = bridge.make_cylinder(args['center'], args['radius'], args['height'])
        name = 'Cylinder'
    elif action['operation'] == 'cone':
        geom = bridge.make_cone(args['center'], args['radius'], args['height'], solid=True)
        name = 'Cone'
    else:
        raise ai_assistant.WorkflowUnavailable('primitive_not_implemented')
    recipe = kernel.recipe_of(geom)
    shape = occt.from_recipe(recipe)
    facts = {'proxy_solid': bool(geom.IsSolid), 'airtight': occt.is_airtight(shape),
             'face_types': occt.face_types(shape), 'volume': occt.volume(shape),
             'bounds': [list(p) for p in occt.tight_bbox(shape)], 'recipe_kind': recipe['kind']}
    workflow.check_primitive_geometry(action, facts)
    key = kernel.register_master(geom, shape)
    try:
        check()
        record = ctx.undo.begin('AI ' + name + ' prototype')
        oid, display_name = ctx.add_geometry(geom, name, publication_check=check)
    except Exception:
        kernel.discard_master_binding(key, shape)
        raise
    finally:
        if record and ctx.undo.current_serial == record:
            ctx.undo.end()
    return {'object_id': oid, 'name': display_name, 'operation': action['operation'],
            'created': True, 'prototype_only': True, 'live_enabled': False,
            'units': expected['append']['units'], 'geometry': facts,
            'undo': 'AI ' + name + ' prototype'}

"""Blender/main-thread integration for one explicitly approved additive box.

Rust validates and consumes the approval. Existing analytic kernel producers and
NarwhalContext own geometry/publication/Undo; no model text is a command string.
The operation is world-aligned and never edits or deletes selected geometry.
"""
from . import ai_assistant


def context(app, document_session, *, record=0):
    from .ai_workspace import WorkspaceSnapshots
    ai_assistant.WorkflowSession._main_thread()
    model = app.model
    layer = model.file.Layers[model.current_layer_index]
    layer_id = str(layer.Id)
    flags = WorkspaceSnapshots(app).layer_flags([layer_id])[layer_id]
    return {'document_session': document_session, 'layer_id': layer_id,
            'units': model.unit_system(), 'tolerance': model.absolute_tolerance(),
            'layer_available': flags.get('available') is True and flags.get('visible') is True
                               and flags.get('locked') is False,
            'command_idle': app.active_command is None,
            'undo_ready': app.ctx.undo.enabled and app.ctx.undo.current_serial == record}


def publish(app, approved, scope_check):
    """Publish only Rust-approved arguments; recheck the live append destination.

    A real OCCT master gives the existing scoped publication rollback a unique
    binding to remove if display creation fails. Nothing is approximated to a mesh.
    """
    from . import bridge, cplane, kernel
    from .kernel import occt_backend as occt
    ai_assistant.WorkflowSession._main_thread()
    if approved.get('status') != 'approved_box':
        raise ai_assistant.WorkflowUnavailable('box_approval_required')
    expected, args = approved['context'], approved['arguments']
    model, file, ctx = app.model, app.model.file, app.ctx
    record = 0

    def check():
        scope_check()
        if app.model is not model or model.file is not file or app.ctx is not ctx:
            raise ai_assistant.WorkflowUnavailable('box_document_changed')
        if context(app, expected['document_session'], record=record) != expected:
            raise ai_assistant.WorkflowUnavailable('box_context_stale')

    check()
    x, y, z = args['origin']
    dx, dy, dz = args['size']
    geom = bridge.make_box((x, y, z), (x+dx, y+dy, z), dz,
                           plane=cplane.CPlane(origin=(x, y, z)))
    shape = occt.from_recipe(kernel.recipe_of(geom))
    if not geom.IsSolid or not occt.is_airtight(shape) or occt.face_types(shape) != {'PLANE': 6}:
        raise ai_assistant.WorkflowUnavailable('box_geometry_verification_failed')
    volume = occt.volume(shape)
    key = kernel.register_master(geom, shape)
    try:
        check()
        record = ctx.undo.begin('AI Box')
        oid, name = ctx.add_geometry(geom, 'Box', publication_check=check)
    except Exception:
        kernel.discard_master_binding(key, shape)
        raise
    finally:
        if record and ctx.undo.current_serial == record:
            ctx.undo.end()
    return {'object_id': oid, 'name': name, 'operation': 'box', 'created': True,
            'origin': args['origin'], 'size': args['size'], 'units': expected['units'],
            'closed_solid': True, 'face_types': {'PLANE': 6}, 'volume': volume,
            'undo': 'AI Box'}

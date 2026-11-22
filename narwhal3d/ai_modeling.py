"""Exact-CAD SDK adapter for Rust-approved modeling batches.

Python is unavoidable here: Blender/rhino3dm document wrappers, OCCT producers,
selection and Undo are Python APIs. Rust owns schemas, dependencies, authorization,
resource limits, replay prevention and canonical result acceptance. No text command,
eval, shell, file access or alternative agent/planner lives in this adapter.
"""
from . import ai_assistant


class ModelingReader:
    """Opt-in MCP host adapter. Metadata consent alone cannot construct this reader."""
    modeling_tools = frozenset(('narwhal.model', 'narwhal.model_result', 'narwhal.inspect', 'narwhal.measure', 'narwhal.frame', 'narwhal.curve_samples', 'narwhal.compare_curves', 'narwhal.export', 'narwhal.export_result'))
    def __init__(self, app, version, document_session, scope_check):
        from .automation_tools import ApplicationReader
        self._reader = ApplicationReader(app, version)
        self._app, self._document_session, self._scope_check = app, document_session, scope_check
        state = ai_assistant.AssistantState(provider=ai_assistant.set_provider('openai_compatible', 'mcp-local-reviewed', 'http://localhost/v1'))
        self._workflow = ai_assistant.WorkflowSession(state, document_session, allow_reads=True)
        self._export_timer = None

    def validate(self, tool, arguments):
        from .automation_tools import ReadError, validate_arguments
        if tool not in self.modeling_tools:
            return validate_arguments(tool,arguments)
        try:
            self._workflow._exchange(dict(op='validate_modeling_mcp', name=tool, arguments=arguments))
        except ai_assistant.WorkflowUnavailable:
            raise ReadError('invalid_arguments') from None

    def read(self, tool, arguments):
        from .automation_tools import ReadError
        self._scope_check()
        if tool not in self.modeling_tools:
            return self._reader.read(tool, arguments)
        self.validate(tool, arguments)
        if tool == 'narwhal.export_result':
            try:
                return self._workflow._exchange(dict(op='external_export_result',binding=self._workflow._binding(self._document_session),**arguments))
            except ai_assistant.WorkflowUnavailable:
                raise ReadError('object_not_found') from None
        if tool == 'narwhal.export':
            return self._stage_export(arguments)
        if tool in ('narwhal.curve_samples','narwhal.compare_curves'):
            try:
                result = (sample_curve(self._app,**arguments) if tool == 'narwhal.curve_samples' else
                          compare_curves_approx(self._app,self._workflow,**arguments))
                self._scope_check()
                return result
            except Exception:
                raise ReadError('read_failed') from None
        if tool == 'narwhal.frame':
            try:
                result = frame_objects(self._app,self._workflow,**arguments)
                self._scope_check()
                return result
            except Exception:
                raise ReadError('read_failed') from None
        if tool == 'narwhal.inspect':
            return inspect_object(self._app, arguments['object_id'])
        if tool == 'narwhal.measure':
            return measure_objects(self._app, arguments['left'], arguments['right'])
        workflow = self._workflow
        doc = self._document_session
        binding = workflow._binding(doc)
        if tool == 'narwhal.model_result':
            try:
                return workflow._exchange(dict(op='external_modeling_result', binding=binding, **arguments))
            except ai_assistant.WorkflowUnavailable:
                raise ReadError('object_not_found') from None
        from . import ui
        if ui._ai.get('external_export_pending') or ui._ai.get('modeling_pending') or ui._ai.get('review_batch') or ui._ai['state'].status == 'sending':
            return dict(status='busy', applied=False)
        try:
            descriptor = workflow._exchange(dict(op='external_modeling', binding=binding,
                context=context(self._app, doc), **arguments))
        except ai_assistant.WorkflowUnavailable:
            return dict(status='refused', applied=False, code='modeling_replayed_or_unavailable',
                        guidance='Poll model_result using the original request_id; do not retry applied mutations.')
        pending = dict(external=True, descriptor=descriptor, workflow=workflow,
                       document_session=doc, scope_check=self._scope_check)
        ui._ai['modeling_pending'] = pending
        ui.bpy.ops.narwhal.ai_modeling_approval('INVOKE_DEFAULT')
        return dict(status='awaiting_local_review', request_id=arguments['request_id'],
                    applied=False, poll_tool='narwhal.model_result')

    def _stage_export(self,arguments):
        from . import ui
        if self._export_timer is not None or any(ui._ai.get(key) for key in (
                'modeling_pending','review_batch','visual_pending','external_export_pending')):
            return dict(status='busy',exported=False)
        binding=self._workflow._binding(self._document_session)
        try:
            descriptor=self._workflow._exchange(dict(op='external_export',binding=binding,
                request_id=arguments['request_id'],request=arguments['export'],context=context(self._app,self._document_session)))
        except ai_assistant.WorkflowUnavailable:
            return dict(status='refused',exported=False,code='export_replayed_or_unavailable',
                        guidance='Poll export_result with the original request_id; never retry an export after timeout.')
        pending=dict(reader=self,request_id=arguments['request_id'])
        ui._ai['external_export_pending']=pending
        def run_export():
            try:
                self._scope_check()
                export_with_local_destination(self._app,self._workflow,None,arguments['request_id'],
                    self._document_session,self._scope_check,external=True)
            except Exception:
                try:
                    self._workflow._exchange(dict(op='abort_external_export',binding=binding,request_id=arguments['request_id']))
                except Exception:
                    pass  # a disconnected/closed native session has no surviving authority
            finally:
                self._export_timer=None
                if ui._ai.get('external_export_pending') is pending:
                    ui._ai.pop('external_export_pending',None)
            return None
        self._export_timer=run_export
        try:
            ui.bpy.app.timers.register(run_export,first_interval=.2)
        except Exception:
            self._export_timer=None
            if ui._ai.get('external_export_pending') is pending:
                ui._ai.pop('external_export_pending',None)
            self._workflow._exchange(dict(op='abort_external_export',binding=binding,request_id=arguments['request_id']))
            return self._workflow._exchange(dict(op='external_export_result',binding=binding,request_id=arguments['request_id']))
        return descriptor

    def close(self):
        if self._export_timer is not None:
            from . import ui
            if ui.bpy.app.timers.is_registered(self._export_timer):
                ui.bpy.app.timers.unregister(self._export_timer)
            self._export_timer=None
            pending=ui._ai.get('external_export_pending')
            if pending and pending.get('reader') is self:
                ui._ai.pop('external_export_pending',None)
        self._workflow.close()


def context(app, document_session):
    # Encode is the SDK's authoritative persisted document image, not a viewport
    # mesh or metadata revision. The digest stays local, never enters tool context.
    import hashlib
    from .ai_box import context as append_context
    ai_assistant.WorkflowSession._main_thread()
    append = append_context(app, document_session)
    image = app.model.file.Encode()
    fingerprint = hashlib.sha256(image.encode('ascii')).hexdigest()
    return dict(document_session=document_session, fingerprint=fingerprint,
                units=append['units'], tolerance=append['tolerance'],
                command_idle=append['command_idle'], undo_ready=append['undo_ready'],
                layer_available=append['layer_available'], undo_top_serial=app.ctx.undo.top_serial)


def inspect_geometry(geom, tolerance, *, probe_profile=False):
    from . import bridge, kernel
    from .kernel import occt_backend as occt
    import rhino3dm as r3
    if geom is None or not geom.IsValid:
        raise ValueError('invalid_geometry')
    box = geom.GetBoundingBox()
    facts = dict(valid=True, type=type(geom).__name__,
                 bounds=[[box.Min.X, box.Min.Y, box.Min.Z], [box.Max.X, box.Max.Y, box.Max.Z]],
                 solid=False, airtight=False)
    if isinstance(geom, r3.Curve):
        facts.update(closed=bool(geom.IsClosed), planar=bool(geom.IsPlanar(tolerance)), degree=geom.Degree)
        if probe_profile:
            nurbs = geom.ToNurbsCurve()
            if len(nurbs.Points) <= 64 and len(nurbs.Knots) <= 70:
                facts['nurbs'] = dict(available=True, degree=nurbs.Degree, closed=bool(nurbs.IsClosed),
                    points=[[p.X,p.Y,p.Z,p.W] for p in nurbs.Points], knots=list(nurbs.Knots),
                    coordinates='homogeneous_xw_yw_zw_w', knot_convention='openNURBS_omit_two_superfluous_end_knots')
            else:
                facts['nurbs'] = dict(available=False, code='curve_detail_limit', control_point_count=len(nurbs.Points))
        if probe_profile and facts['closed'] and facts['planar']:
            # SDK-only exact downstream check: closure alone admits crossing loops.
            # The temporary extrusion is never published or substituted for a curve.
            probe = None
            try:
                from . import analysis
                plane = analysis.curve_plane(geom)
                if plane is None:
                    raise ValueError('curve_plane_unavailable')
                probe = bridge.extrude_curve(geom, max(1.0,tolerance*100),
                    direction=list(plane[1]), solid=True)
                shape = bridge._occt_operand(probe)
                facts['profile_usable_for_solid'] = bool(shape is not None and occt.is_airtight(shape))
            except Exception:
                facts['profile_usable_for_solid'] = False
            finally:
                if probe is not None:
                    key, master = kernel.master_key_of(probe), kernel.master_of(probe)
                    if key and master is not None:
                        kernel.discard_master_binding(key, master)
    recipe = kernel.recipe_of(geom)
    if isinstance(geom, (r3.Brep, r3.Extrusion)):
        if recipe is None or kernel.honesty_problem(geom):
            raise ValueError('exact_geometry_required')
        shape = kernel.master_of(geom)
        if shape is None:
            shape = occt.from_recipe(recipe)
        facts.update(bounds=[list(p) for p in occt.tight_bbox(shape)],
                     airtight=occt.is_airtight(shape), solid=occt.is_airtight(shape),
                     volume=occt.volume(shape), face_types=occt.face_types(shape),
                     recipe_kind=recipe['kind'])
    return facts


def inspect_object(app, object_id):
    ai_assistant.WorkflowSession._main_thread()
    obj = app.model.file.Objects.FindId(object_id)
    if obj is None:
        return dict(object_id=object_id, code='object_not_found', available=False)
    layer = app.model.file.Layers[obj.Attributes.LayerIndex]
    return dict(object_id=object_id, name=obj.Attributes.Name,
                attributes=dict(layer_id=str(layer.Id), color=list(obj.Attributes.ObjectColor),
                                color_source=str(obj.Attributes.ColorSource), visible=bool(obj.Attributes.Visible)),
                **inspect_geometry(obj.Geometry, app.model.absolute_tolerance(), probe_profile=True))


def _inspection_curve(app, object_id):
    import rhino3dm as r3
    ai_assistant.WorkflowSession._main_thread()
    obj = app.model.file.Objects.FindId(object_id)
    if obj is None:
        raise ValueError('object_not_found')
    if not isinstance(obj.Geometry,r3.Curve) or not obj.Geometry.IsValid:
        raise ValueError('curve_required')
    return obj.Geometry


def sample_curve(app, object_id, sample_count, from_fraction, to_fraction):
    """Read exact SDK evaluations, not a refit or replacement engine."""
    import math
    curve = _inspection_curve(app,object_id)
    domain = curve.Domain
    rows=[]
    for i in range(sample_count):
        fraction = from_fraction+(to_fraction-from_fraction)*i/(sample_count-1)
        t = domain.T0+(domain.T1-domain.T0)*fraction
        p, tangent, curvature = curve.PointAt(t),curve.TangentAt(t),curve.CurvatureAt(t)
        rows.append([fraction,[p.X,p.Y,p.Z],[tangent.X,tangent.Y,tangent.Z],
                     curvature.Length() if math.isfinite(curvature.Length()) else None])
    nurbs=curve.ToNurbsCurve()
    return dict(object_id=object_id,available=True,degree=curve.Degree,closed=bool(curve.IsClosed),
        planar=bool(curve.IsPlanar(app.model.absolute_tolerance())),control_point_count=len(nurbs.Points),
        sample_count=sample_count,parameterization='normalized_curve_domain_not_arc_length',
        sample_layout='[fraction, point_xyz, unit_tangent_xyz, curvature_magnitude]',samples=rows,
        units=app.model.unit_system(),geometry_changed=False)


def compare_curves_approx(app, workflow, left, right, sample_count):
    """SDK point-to-exact-wire queries; sampled maximum is not a global bound."""
    from . import bridge
    from .kernel import occt_backend as occt
    curves=[_inspection_curve(app,oid) for oid in (left,right)]
    recipes=[bridge.curve_recipe(c) for c in curves]
    if any(r is None for r in recipes):
        raise ValueError('exact_curve_conversion_unavailable')
    wires=[occt.wire_from_curve(r) for r in recipes]
    distances=[]
    for curve,target in zip(curves,reversed(wires)):
        row=[]
        for i in range(sample_count):
            t=curve.Domain.T0+(curve.Domain.T1-curve.Domain.T0)*i/(sample_count-1)
            p=curve.PointAt(t)
            distance=occt.distance_to_shape(target,(p.X,p.Y,p.Z))
            if not 0 <= distance < float('inf'):
                raise ValueError('exact_curve_distance_failed')
            row.append(distance)
        distances.append(row)
    summary=workflow._exchange(dict(op='curve_comparison_summary',forward=distances[0],reverse=distances[1]))
    return dict(summary,available=True,left=left,right=right,units=app.model.unit_system(),
        control_point_counts=[len(c.ToNurbsCurve().Points) for c in curves],geometry_changed=False)


def export_with_local_destination(app, workflow, token, call_id, document_session, scope_check, *, external=False):
    """SDK/file-picker glue; Rust grants one attempt and constructs its receipt.

    The provider never supplies or receives a path. Local destination choice is
    mandatory even in Build mode. Atomic no-clobber publication refuses existing
        files, including a destination created by another process during export.
    """
    import os
    import tempfile
    from pathlib import Path
    from . import filedialog, export_commands
    ai_assistant.WorkflowSession._main_thread()
    binding=workflow._binding(document_session)
    scope_check()
    authority=workflow._exchange(dict(op='begin_external_export',binding=binding,request_id=call_id,current=context(app,document_session))
        if external else dict(op='consume_export',token=token,binding=binding,call_id=call_id))
    if 'result' in authority:
        return authority['result']
    request=authority['request']
    captured_model, captured_file=app.model,app.model.file
    expected=context(app,document_session)
    outcome, size='failed',0

    def check():
        scope_check()
        if app.model is not captured_model or app.model.file is not captured_file or context(app,document_session)!=expected:
            raise ValueError('export_context_stale')
    try:
        check()
        objects=[captured_file.Objects.FindId(oid) for oid in request['object_ids']]
        if any(obj is None for obj in objects):
            raise ValueError('export_object_unavailable')
        extension='.step' if request['format']=='step' else '.3dm'
        status,path=filedialog.ask_save_path('AI export — choose a NEW destination',
            os.path.join(filedialog.start_dir(),'AI-export'+extension),
            (('STEP exact geometry' if extension=='.step' else '3dm selected objects','*'+extension),))
        check()
        if status==filedialog.CANCEL:
            outcome='declined'
        elif status!=filedialog.OK:
            outcome='unavailable'
        elif not path or Path(path).suffix.lower()!=extension:
            outcome='failed'
        else:
            destination=Path(path)
            if os.path.lexists(destination):
                outcome='existing_destination'
            else:
                # Existing exporters may atomically replace their output. Keep that
                # output private, then link it into place without ANY overwrite race.
                with tempfile.TemporaryDirectory(prefix='.narwhal-ai-export-',dir=destination.parent) as folder:
                    staged=Path(folder)/('export'+extension)
                    check()
                    ok,_=export_commands.export_objects(captured_model,objects,str(staged))
                    check()
                    if ok and 0<staged.stat().st_size<=1073741824:
                        os.chmod(staged,0o600)
                        try:
                            os.link(staged,destination)
                        except FileExistsError:
                            outcome='existing_destination'
                        else:
                            size=staged.stat().st_size
                            outcome='exported'
    except Exception as exc:
        outcome='stale_context' if exc.args and exc.args[0]=='export_context_stale' else 'failed'
        size=0
    completion=dict(op='finish_external_export',binding=binding,request_id=call_id,current=context(app,document_session)) if external else dict(
        op='finish_export',token=token,binding=binding,call_id=call_id)
    return workflow._exchange(dict(**completion,outcome=outcome,file_bytes=size))['result']


def frame_objects(app, workflow, object_ids, view_direction=None):
    """Host selection/view integration only; perspective fit is computed in Rust."""
    import bpy
    from . import bridge, ui
    from .commands import ZoomCmd
    ai_assistant.WorkflowSession._main_thread()
    area = ui.active_pane_area()
    if bpy.app.background or ui.app_state is not app or area is None:
        raise ValueError('view_unavailable')
    objects = [app.model.file.Objects.FindId(oid) for oid in object_ids]
    if any(obj is None for obj in objects):
        raise ValueError('object_not_found')
    names = [app.model.blender_name_for(oid) for oid in object_ids]
    displays = [bpy.data.objects.get(name) if name else None for name in names]
    if any(obj is None or not bridge.display_visible(obj) for obj in displays):
        raise ValueError('display_unavailable')
    bounds = [inspect_geometry(obj.Geometry,app.model.absolute_tolerance())['bounds'] for obj in objects]
    orientation = (workflow._exchange(dict(op='modeling_view',direction=view_direction))
                   if view_direction is not None else None)
    rv3d = area.spaces.active.region_3d
    if orientation is not None:
        # Blender requires its own rotation type; basis computation/validation is native.
        from mathutils import Matrix
        rv3d.view_rotation = Matrix(orientation['rotation_rows']).to_quaternion()
        rv3d.view_perspective = 'PERSP'
    app.ctx.select_by_names(names,already_expanded=True)
    bpy.context.view_layer.update()
    view_preferences = bpy.context.preferences.view
    smooth = view_preferences.smooth_view
    try:
        view_preferences.smooth_view = 0
        ZoomCmd()._zoom(selected=True)
    finally:
        view_preferences.smooth_view = smooth
    rv3d = area.spaces.active.region_3d
    if rv3d.view_perspective == 'PERSP':
        matrix = rv3d.window_matrix
        fit = workflow._exchange(dict(op='modeling_frame',bounds=bounds,
            projection=[abs(matrix[0][0]),abs(matrix[1][1])]))
        rv3d.view_location = fit['center']
        rv3d.view_distance = max(rv3d.view_distance,fit['distance'])
    area.tag_redraw()
    return dict(framed=True,object_ids=object_ids,scope='active_viewport',document_mutated=False,
                    view_direction=view_direction)


def capture_view(app):
    """SDK-only cropped active-pane capture. Native consent precedes this call."""
    import bpy
    import base64
    import tempfile
    from pathlib import Path
    from . import ui
    ai_assistant.WorkflowSession._main_thread()
    area = ui.active_pane_area()
    if bpy.app.background or ui.app_state is not app or area is None or area.type != 'VIEW_3D':
        raise ValueError('viewport_capture_unavailable')
    image = None
    settings = bpy.context.scene.render.image_settings
    original = settings.file_format, settings.color_mode, settings.quality
    try:
        with tempfile.TemporaryDirectory(prefix='narwhal-ai-viewport-') as folder:
            path = Path(folder)/'viewport.png'
            with bpy.context.temp_override(area=area):
                if bpy.ops.screen.screenshot_area(filepath=str(path)) != {'FINISHED'}:
                    raise ValueError('viewport_capture_unavailable')
            image = bpy.data.images.load(str(path), check_existing=False)
            settings.file_format, settings.color_mode, settings.quality = 'JPEG', 'RGB', 30
            width, height = image.size
            for limit in (320,240,160):
                factor = min(1.0,limit/max(width,height))
                image.scale(max(1,int(width*factor)),max(1,int(height*factor)))
                jpg = Path(folder)/'viewport.jpg'
                image.save_render(str(jpg), scene=bpy.context.scene)
                data = jpg.read_bytes()
                if len(data)<=9216:
                    return dict(kind='viewport_image',mime_type='image/jpeg',data=base64.b64encode(data).decode('ascii'),
                        width=image.size[0],height=image.size[1],scope='active_viewport_only',geometry_validity_verified=False)
            raise ValueError('viewport_image_budget')
    finally:
        settings.file_format, settings.color_mode, settings.quality = original
        if image is not None:
            bpy.data.images.remove(image)


def measure_objects(app, left, right):
    """Thin exact OCCT distance query; never measures a tessellated proxy."""
    from . import bridge
    import math
    ai_assistant.WorkflowSession._main_thread()
    objects = [app.model.file.Objects.FindId(oid) for oid in (left, right)]
    if any(obj is None for obj in objects):
        return dict(available=False, code='object_not_found')
    shapes = [bridge._occt_operand(obj.Geometry) for obj in objects]
    if any(shape is None for shape in shapes):
        return dict(available=False, code='exact_shape_required',
                    supported='analytic solids/surfaces with registered masters or rebuildable recipes')
    distance = bridge._occt().minimum_distance(*shapes)
    if not math.isfinite(distance) or distance < 0:
        return dict(available=False, code='exact_distance_failed')
    return dict(available=True, left=left, right=right, distance=distance,
                units=app.model.unit_system(), within_tolerance=distance <= app.model.absolute_tolerance(),
                method='occt_exact_shape_distance', unified_solid_verified=False)


def _construct(action, resolve, tolerance=1e-6):
    from . import bridge, cplane, kernel
    import rhino3dm as r3
    import math
    op = action['op']
    if op == 'attributes':
        return resolve(action['source']).Duplicate()
    if op == 'point':
        return r3.Point(r3.Point3d(*action['point']))
    if op == 'circle':
        return bridge.make_circle(action['center'], action['radius'])
    if op == 'curve':
        return bridge.make_interpolated_curve(action['points'], degree=3, closed=action['closed'])
    if op == 'nurbs':
        curve = r3.NurbsCurve(3, True, action['degree']+1, len(action['points']))
        for i, point in enumerate(action['points']):
            curve.Points[i] = r3.Point4d(*point)
        for i, knot in enumerate(action['knots']):
            curve.Knots[i] = knot
        if bool(curve.IsClosed) != action['closed']:
            raise ValueError('modeling_curve_unverified')
        return curve
    if op == 'box':
        x, y, z = action['origin']
        dx, dy, dz = action['size']
        return bridge.make_box((x, y, z), (x + dx, y + dy, z), dz, plane=cplane.CPlane(origin=(x, y, z)))
    if op == 'sphere':
        return bridge.make_sphere(action['center'], action['radius'])
    if op in ('cylinder', 'cone'):
        producer = bridge.make_cylinder if op == 'cylinder' else bridge.make_cone
        return producer(action['center'], action['radius'], action['height'])
    if op == 'extrude':
        direction = r3.Vector3d(*action['direction'])
        direction.Unitize()
        return bridge.extrude_curve(resolve(action['source']), action['distance'],
                                    direction=[direction.X, direction.Y, direction.Z], solid=action['solid'])
    if op == 'bevel':
        result = bridge.bevel_edges_occt(resolve(action['source']), action['size'], chamfer=action['chamfer'])
        if result is None or kernel.recipe_of(result) is None:
            raise ValueError('analytic_bevel_unavailable')
        return result
    if op in ('thickness', 'outline'):
        source = resolve(action['source'])
        recipe = kernel.recipe_of(source)
        if op == 'outline':
            if isinstance(source, r3.Curve):
                curve = source
            elif recipe and recipe['kind'] == 'extrusion':
                curve = bridge.curve_from_recipe(recipe['profile'])
                if recipe.get('xform'):
                    matrix = r3.Transform.Identity()
                    for row in range(4):
                        for col in range(4):
                            setattr(matrix, 'M' + str(row) + str(col), recipe['xform'][4*row+col])
                    curve = bridge.transform_geometry(curve, matrix)
            else:
                raise ValueError('outline_requires_curve_or_extrusion_recipe')
            projected, _ = bridge.project_to_cplane_geometry(curve, cplane.CPlane())
            if projected is None:
                raise ValueError('exact_projection_unavailable')
            return projected
        if not recipe or recipe['kind'] != 'extrusion' or recipe.get('xform'):
            raise ValueError('thickness_requires_untransformed_extrusion_recipe')
        from .kernel import occt_backend as occt
        recipe = dict(recipe)
        direction = r3.Vector3d(*recipe['dir'])
        direction.Unitize()
        distance = action['distance']
        recipe['dir'] = [direction.X*distance, direction.Y*distance, direction.Z*distance]
        return bridge.occt_backed(occt.from_recipe(recipe), recipe)
    if op == 'revolve':
        profile=resolve(action['source'])
        if not isinstance(profile,r3.Curve):
            raise ValueError('curve_source_required')
        result = bridge.make_revolve(profile, action['axis_start'], action['axis_end'],
                                     action['start_degrees'], action['end_degrees'])
        if result is None or kernel.master_of(result) is None or kernel.recipe_of(result) is None:
            raise ValueError('analytic_revolve_unavailable')
        return result
    if op == 'pipe':
        rail = resolve(action['source'])
        if not isinstance(rail,r3.Curve):
            raise ValueError('curve_source_required')
        # A closed rail has no end boundaries to cap (OCCT correctly refuses that
        # operation). Requested solidity is still independently verified below.
        result = bridge.pipe_occt(rail, action['radius'], cap=action['cap'] and not rail.IsClosed)
        if result is None or kernel.master_of(result) is None or kernel.recipe_of(result) is None:
            raise ValueError('analytic_pipe_unavailable')
        return result
    if op == 'loft':
        return bridge.loft_brep([resolve(s) for s in action['sections']], style='Normal')
    if op == 'boolean':
        left,right=resolve(action['left']),resolve(action['right'])
        if not all(inspect_geometry(g,1e-7)['airtight'] for g in (left,right)):
            raise ValueError('boolean_requires_airtight_solids')
        result = bridge.boolean_occt(left,right,
                                     {'union': 'UNION', 'difference': 'DIFFERENCE', 'intersection': 'INTERSECT'}[action['kind']])
        if result is None or kernel.recipe_of(result) is None:
            raise ValueError('analytic_boolean_unavailable')
        return result
    if op == 'transform':
        geom = resolve(action['source']).Duplicate()
        # The SDK builds/composes transforms; bridge moves masters AND recipes.
        center = r3.Point3d(*action['center'])
        scale = r3.Transform.Scale(r3.Plane(center, r3.Vector3d(0, 0, 1)), *action['scale'])
        rotate = r3.Transform.Rotation(math.radians(action['rotation_degrees']), r3.Vector3d(*action['axis']), center)
        translate = r3.Transform.Translation(*action['translation'])
        moved = bridge.transform_geometry(geom, r3.Transform.Multiply(translate, r3.Transform.Multiply(rotate, scale)))
        return moved if moved is not None else geom
    if op == 'join':
        # Exact Join is NOT a boolean: one connected group that consumes every
        # requested source. A group may remain an OPEN surface (cap it later);
        # disconnected or partially-consumed sources are refused, never dropped.
        # Stable canonical code (no raw kernel detail / path reaches the provider).
        sources = [resolve(sr) for sr in action['sources']]
        items = [(str(action['sources'][i]), sources[i]) for i in range(len(sources))]
        result = bridge.join_breps(items, 2 * tolerance)
        if len(result) != 1 or len(result[0][0]) != len(sources):
            raise ValueError('join_connected_group_required')
        return result[0][1]
    if op == 'cap':
        # Exact planar cap to one airtight solid. Declines are surfaced as a
        # stable canonical code -- the native reason stays local (actionable for
        # operators) but its raw text (which may embed paths) never crosses the SDK.
        geom = resolve(action['source'])
        out = bridge.cap_planar_holes_occt(geom, tolerance=tolerance)
        if out is None:
            reason = bridge.last_occt_decline()
            if reason:
                # Keep the actionable detail local-only; log where operators can read it.
                try:
                    import logging
                    logging.getLogger('narwhal3d.cap').warning('cap refused locally: %s', reason)
                except Exception:
                    pass
            raise ValueError('cap_planar_closure_refused')
        return out
    raise ValueError('unsupported_modeling_operation')


def publish(app, workflow, descriptor, document_session, scope_check):
    """Synchronous adapter for owned headless tests; the UI uses publish_steps."""
    job = publish_steps(app, workflow, descriptor, document_session, scope_check)
    while True:
        try:
            progress = next(job)
            if progress.get('phase') == 'display_preparing':
                import time
                time.sleep(0.01)  # headless adapter only; UI callers poll on their timer
        except StopIteration as done:
            return done.value


def publish_steps(app, workflow, descriptor, document_session, scope_check):
    """Consume once before work. Prepare all geometry before document publication.

    A producer error publishes nothing. Publication uses the existing document/Undo
    model and rolls back only our tagged additions and unchanged replacements; it
    never restores a whole document over an external callback's changes.
    """
    from . import bridge, kernel
    ai_assistant.WorkflowSession._main_thread()
    ticket = descriptor['ticket']
    scope_check()
    consumed = workflow.modeling_exchange('consume_modeling', document_session,
                                         ticket=ticket, current=context(app, document_session))
    if consumed['status'] != 'modeling_consumed':
        raise ai_assistant.WorkflowUnavailable('modeling_approval_required')
    batch = consumed['batch']
    model, file, ctx = app.model, app.model.file, app.ctx
    if batch['steps'][0]['action']['op'] == 'undo':
        try:
            name = ctx.undo_last()
            if name is None:
                raise ValueError('undo_unavailable')
            output = dict(applied=True, objects=[], undo_performed=True, label='Undo ' + name)
            workflow.modeling_exchange('modeling_publication', document_session, ticket=ticket, output=output, is_error=False)
        except Exception:
            workflow.modeling_exchange('modeling_publication', document_session, ticket=ticket,
                output=dict(code='undo_outcome_unknown', applied=None, retry_safe=False), is_error=True)
        return workflow.modeling_exchange('modeling_result', document_session, ticket=ticket)['result']
    expected = consumed['context']
    prepared, facts, originals, added, replaced = {}, {}, {}, [], []
    display_meshes = {}
    attribute_changes, attribute_edits = {}, []
    serial = 0
    committed = False
    token = str(ticket['owner']['conversation']) + ':' + str(ticket['serial'])
    baseline = model.snapshot()
    baseline_encoded = file.Encode()
    counters_before = dict(ctx.counters)
    counter_additions, created_names = {}, set()
    allocations = {'objects': [], 'meshes': []}
    make_display = ctx._make_display_object
    code = 'modeling_preparation_failed'
    active_step = None

    def check():
        scope_check()
        if app.model is not model or model.file is not file or app.ctx is not ctx:
            raise ValueError('modeling_document_changed')
        from .ai_box import context as append_context
        live = append_context(app, document_session, record=serial)
        if (live['units'] != expected['units'] or live['tolerance'] != expected['tolerance']
                or not live['layer_available'] or not live['command_idle'] or not live['undo_ready']):
            raise ValueError('modeling_context_changed')

    def tracked_display(geom, *args, **kwargs):
        from .display_prepare import DisplayPreparationError
        key = kernel.master_key_of(geom)
        if kernel.master_of(geom) is not None:
            if key not in display_meshes:
                raise DisplayPreparationError('display_not_prepared')
            kwargs['prepared_display'] = display_meshes[key]
        if geom.GetUserString('NarwhalAI.Batch') != token:
            return make_display(geom, *args, **kwargs)
        check()
        owned = kwargs.setdefault('allocations', {'objects': [], 'meshes': []})
        try:
            result = make_display(geom, *args, **kwargs)
            check()
            return result
        finally:
            # Retain allocation-time RNA handles even when construction raises.
            for kind in allocations:
                allocations[kind].extend(owned[kind])

    def resolve(source):
        if source in prepared:
            return prepared[source]
        obj = file.Objects.FindId(source)
        if obj is None:
            raise ValueError('object_not_found')
        originals.setdefault(source, (obj.Geometry.Duplicate(), obj.Geometry.Encode()))
        return obj.Geometry

    try:
        for step in batch['steps']:
            active_step = step['id']
            check()
            if step['replace']:
                obj = file.Objects.FindId(step['replace'])
                from .ai_workspace import WorkspaceSnapshots
                layer = file.Layers[obj.Attributes.LayerIndex] if obj is not None else None
                flags = WorkspaceSnapshots(app).layer_flags([str(layer.Id)])[str(layer.Id)] if layer else {}
                if obj is None or str(obj.Attributes.Mode).endswith('Locked') or flags.get('locked') is not False:
                    raise ValueError('replacement_unavailable')
                resolve(step['replace'])
            action = step['action']
            if action['op'] == 'attributes':
                changes = []
                if action.get('name') is not None:
                    if any(str(o.Attributes.Id) != step['replace'] and o.Attributes.Name.casefold() == action['name'].casefold() for o in file.Objects):
                        raise ValueError('object_name_collision')
                    changes.append(('Name', action['name']))
                if action.get('layer_id') is not None:
                    target = next((l for l in file.Layers if str(l.Id) == action['layer_id']), None)
                    from .ai_workspace import WorkspaceSnapshots
                    flags = WorkspaceSnapshots(app).layer_flags([action['layer_id']]).get(action['layer_id'], {})
                    if target is None or flags.get('locked') is not False:
                        raise ValueError('target_layer_unavailable')
                    changes.append(('LayerIndex', target.Index))
                if action.get('color') is not None:
                    import rhino3dm as r3
                    changes.extend([('ObjectColor', tuple(action['color'])+(255,)),
                                    ('ColorSource', r3.ObjectColorSource.ColorFromObject)])
                if action.get('visible') is not None:
                    changes.append(('Visible', action['visible']))
                attribute_changes[step['id']] = changes
            geom = _construct(action, resolve, expected['tolerance'])
            facts[step['id']] = inspect_geometry(geom, expected['tolerance'])
            if step['action']['op'] in ('circle', 'curve', 'nurbs') and step['action'].get('closed', True):
                if not facts[step['id']].get('closed'):
                    raise ValueError('closed_curve_required')
            if step['action']['op'] in ('box', 'sphere', 'cylinder', 'cone', 'boolean', 'bevel', 'thickness', 'cap') or (
                    step['action']['op'] == 'extrude' and step['action']['solid']) or (
                    step['action']['op'] == 'pipe' and step['action']['cap']) or (
                    step['action']['op'] == 'revolve' and step['action']['solid']):
                if not facts[step['id']]['airtight'] or facts[step['id']]['volume'] <= 0:
                    raise ValueError('solid_validation_failed')
            prepared[step['id']] = geom
            # Yield only during preparation. Publication remains one short atomic
            # document transaction; a cancel between steps cannot leave half a design.
            yield dict(phase='preparing', step=step['id'])
        # OCCT may spend unbounded native time/memory meshing a perfectly valid
        # NURBS master. Do that on serialized copies, before any document/Undo edit.
        # Include surviving objects: rebuild_display otherwise remeshes those inline.
        from . import display_prepare
        import time
        code = 'modeling_display_preparation_failed'
        targets = [(s['id'], prepared[s['id']]) for s in batch['steps'] if s['publish']]
        replacing = {s['replace'] for s in batch['steps'] if s['replace']}
        targets.extend((None, obj.Geometry) for obj in file.Objects if str(obj.Attributes.Id) not in replacing)
        display_bytes = 0
        deadline = time.monotonic() + 120.0
        for active_step, geom in targets:
            check()
            key, master = kernel.master_key_of(geom), kernel.master_of(geom)
            if master is None or key in display_meshes:
                continue
            job = display_prepare.DisplayJob(master)
            try:
                while True:
                    check()
                    if context(app, document_session) != expected:
                        raise ValueError('modeling_context_stale')
                    if time.monotonic() > deadline:
                        raise display_prepare.DisplayPreparationError('display_batch_timeout')
                    mesh = job.poll()
                    if mesh is not None:
                        display_bytes += len(mesh.data)
                        if display_bytes > display_prepare.MAX_BATCH_OUTPUT:
                            raise display_prepare.DisplayPreparationError('display_batch_size')
                        display_meshes[key] = mesh
                        break
                    yield dict(phase='display_preparing', step=active_step or 'existing display')
            finally:
                job.close()
        check()
        if context(app, document_session) != expected:
            raise ValueError('modeling_context_stale')
        code = 'modeling_publication_failed'
        serial = ctx.undo.begin('AI ' + batch['label'])
        ctx.note_change()
        ctx._make_display_object = tracked_display
        output_objects = []
        for step in batch['steps']:
            if not step['publish']:
                continue
            active_step = step['id']
            check()
            geom = prepared[step['id']]
            geom.SetUserString('NarwhalAI.Component', step['name'])
            geom.SetUserString('NarwhalAI.Batch', token)
            if step['replace']:
                oid = step['replace']
                obj = file.Objects.FindId(oid)
                if obj is None or obj.Geometry.Encode() != originals[oid][1]:
                    raise ValueError('replacement_changed')
                # Register before Delete+Add: an SDK add failure can leave no
                # object at all. Missing targets are ours to restore ONLY when
                # this attempt failed, not after a later callback deleted one.
                attempt = dict(oid=oid, encoded=geom.Encode(), attributes=obj.Attributes, failed=False)
                replaced.append(attempt)
                try:
                    if not model.replace_geometry(oid, geom):
                        raise ValueError('replacement_failed')
                except Exception:
                    attempt['failed'] = True
                    raise
                attrs = file.Objects.FindId(oid).Attributes
                for field, value in attribute_changes.get(step['id'], []):
                    attribute_edits.append((oid, field, getattr(attrs, field), value))
                    setattr(attrs, field, value)
                name = attrs.Name
            else:
                oid, name = ctx.add_geometry(geom, step['name'], publication_check=check)
                added.append(oid)
                created_names.add(name)
                counter_additions[step['name']] = counter_additions.get(step['name'], 0) + 1
            output_objects.append(dict(step=step['id'], object_id=oid, name=name, **facts[step['id']]))
        check()
        ctx.rebuild_display()
        check()
        # Inspect persisted geometry, not merely successful producer return values.
        for row in output_objects:
            row.update(inspect_object(app, row['object_id']))
        # SDK writes must be read back before history is finalized. The native
        # general receipt preflight checks geometry, while its attribute policy
        # is enforced again only when the canonical receipt is recorded.
        for oid, field, before, owned in attribute_edits:
            obj = file.Objects.FindId(oid)
            if obj is None or getattr(obj.Attributes, field) != owned:
                raise ValueError('modeling_attributes_unverified')
        output = dict(applied=True, label=batch['label'], objects=output_objects,
                      units=expected['units'], undo='AI ' + batch['label'], undo_serial=serial,
                      assembly=len(output_objects) > 1, visual_quality_evaluated=False)
        workflow._exchange(dict(op='check_modeling_receipt', batch=batch, output=output,
                                        tolerance=expected['tolerance'], require_planar=consumed['require_planar']))
        try:
            ctx.undo.end()
        except Exception:
            # An exception AFTER the history backend accepted end() must not
            # turn a finalized transaction back into a rollback candidate.
            if ctx.undo.current_serial != 0 or ctx.undo.top_serial != serial:
                raise
        serial = 0
        committed = True
    except Exception as failure:
        reason = failure.args[0] if failure.args and isinstance(failure.args[0], str) else ''
        reason = reason if reason in ('request_requires_planar_2d_geometry_not_relief_solids',
            'modeling_dimensions_unverified', 'modeling_curve_unverified', 'modeling_attributes_unverified', 'modeling_planar_profile_unverified', 'object_not_found',
            'analytic_boolean_unavailable', 'analytic_bevel_unavailable', 'analytic_pipe_unavailable', 'analytic_revolve_unavailable', 'curve_source_required', 'boolean_requires_airtight_solids',
                        'outline_requires_curve_or_extrusion_recipe', 'thickness_requires_untransformed_extrusion_recipe',
                        'replacement_unavailable', 'replacement_changed', 'target_layer_unavailable', 'object_name_collision',
                        'modeling_context_stale', 'modeling_context_changed', 'modeling_document_changed',
            'solid_validation_failed', 'exact_geometry_required',
            'join_connected_group_required', 'cap_planar_closure_refused') else 'exact_operation_or_publication_failed'
        from .display_prepare import DisplayPreparationError
        if isinstance(failure, DisplayPreparationError):
            # Stable refusal codes, not worker stderr/paths, may reach the provider.
            reason = str(failure).split(':', 1)[0]
        unchanged = False
        if code == 'modeling_display_preparation_failed' and not serial:
            # No publication has begun. In particular, never run tag-based rollback
            # against historical objects whose persisted ticket tags may coincide.
            unchanged = (ctx._same_publication_document(baseline, model.snapshot())
                         if model.file is file else file.Encode() == baseline_encoded)
            output = dict(code=code, reason=reason, failed_step=active_step,
                          applied=False if unchanged else None, rollback_confirmed=unchanged,
                          retry_safe=False, guidance='Display preparation stopped before publication; inspect current objects. Never replay this ticket.')
            workflow.modeling_exchange('modeling_publication', document_session, ticket=ticket, output=output, is_error=True)
            return workflow.modeling_exchange('modeling_result', document_session, ticket=ticket)['result']
        try:
            # Work against the captured file, never whichever document is active
            # now. Only this ticket's insertions and unchanged replacements belong
            # to us; unrelated callback edits and their allocations must survive.
            for obj in list(file.Objects):
                oid = str(obj.Attributes.Id)
                if obj.Geometry.GetUserString('NarwhalAI.Batch') == token and oid not in originals:
                    file.Objects.Delete(oid)
            for oid, field, before, owned in reversed(attribute_edits):
                obj = file.Objects.FindId(oid)
                if obj is not None and getattr(obj.Attributes, field) == owned:
                    setattr(obj.Attributes, field, before)
            for attempt in reversed(replaced):
                oid = attempt['oid']
                obj = file.Objects.FindId(oid)
                if (obj is None and attempt['failed']) or (
                        obj is not None and obj.Geometry.Encode() == attempt['encoded']):
                    attrs = attempt['attributes'] if obj is None else obj.Attributes
                    if obj is not None:
                        file.Objects.Delete(oid)
                    if str(file.Objects.Add(originals[oid][0], attrs)) != oid:
                        raise ValueError('rollback_identity_changed')
            ctx._remove_publication_allocations(allocations)
            for base, count in counter_additions.items():
                previous = counters_before.get(base, 0)
                if ctx.counters.get(base) == previous + count:
                    if base in counters_before:
                        ctx.counters[base] = previous
                    else:
                        ctx.counters.pop(base, None)
            from . import selection_state
            entry = selection_state._state.get(model)
            if entry is not None:
                for field in ('created', 'batch'):
                    if field in entry:
                        entry[field][:] = [name for name in entry[field] if name not in created_names]
            unchanged = (ctx._same_publication_document(baseline, model.snapshot())
                         if model.file is file else file.Encode() == baseline_encoded)
            if serial:
                if unchanged or model.file is not file:
                    # An old-file snapshot must never enter a replacement file's
                    # history, even if unrelated old-file edits make rollback unknown.
                    ctx.undo.abandon()
                else:
                    ctx.undo.end()
                serial = 0
                if app.model is model and model.file is file and app.ctx is ctx:
                    ctx.rebuild_display()
        except Exception:
            unchanged = False
        if serial and ctx.undo.current_serial == serial:
            if model.file is not file:
                ctx.undo.abandon()
            else:
                ctx.undo.end()
        output = dict(code=code, reason=reason, failed_step=active_step, applied=False if unchanged else None,
                      rollback_confirmed=unchanged, retry_safe=False,
                      guidance='Inspect current objects, then submit a corrected new batch with new call IDs. Never replay this ticket.')
        workflow.modeling_exchange('modeling_publication', document_session, ticket=ticket, output=output, is_error=True)
    finally:
        if ctx._make_display_object is tracked_display:
            ctx._make_display_object = make_display
        published = {s['id'] for s in batch['steps'] if s['publish']} if committed else set()
        for sid, geom in prepared.items():
            if sid not in published:
                key, master = kernel.master_key_of(geom), kernel.master_of(geom)
                if key and master is not None:
                    kernel.discard_master_binding(key, master)
    if committed:
        # Irreversible boundary: the document AND history are now committed.
        # Only reconcile the receipt; never rerun geometry or roll it back here.
        result = None
        try:
            try:
                workflow.modeling_exchange('modeling_publication', document_session,
                    ticket=ticket, output=output, is_error=False)
            except Exception:
                try:
                    result = workflow.modeling_exchange('modeling_result', document_session, ticket=ticket)['result']
                except ai_assistant.WorkflowUnavailable as failure:
                    if str(failure) != 'modeling_result_pending':
                        raise
                    # The native gate says no receipt was accepted. Retry recording
                    # the same receipt once, NOT consuming or executing the batch.
                    try:
                        workflow.modeling_exchange('modeling_publication', document_session,
                            ticket=ticket, output=output, is_error=False)
                    except Exception:
                        pass  # query also covers acceptance followed by a lost reply
            if result is None:
                result = workflow.modeling_exchange('modeling_result', document_session, ticket=ticket)['result']
        except Exception:
            raise ai_assistant.WorkflowUnavailable(
                'modeling_committed_acknowledgment_unknown_inspect_no_replay') from None
        try:
            ctx.select_by_names([o['name'] for o in output_objects], already_expanded=True)
        except Exception:
            pass  # selection is presentation, not part of the committed transaction
        return result
    return workflow.modeling_exchange('modeling_result', document_session, ticket=ticket)['result']

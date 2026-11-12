"""Exact portable library assets; host orchestration, not a geometry kernel.

OURS: the base is the world-XY bounding box's bottom centre. Source coordinates
are scaled into destination units BEFORE the base is placed on the supplied
plane. Tessellation is used only in an isolated thumbnail scene, never saved as
asset geometry. Main owns command-idle admission and UI cache invalidation.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import uuid

import rhino3dm as r3

from . import bridge, kernel, narwhal_document
from .model import NarwhalModel

SUPPORTED_FILTERS = (
    ('Supported geometry', '*.narwhal *.3dm *.step *.stp *.dxf *.svg *.cdr *.pdf *.ai *.obj *.stl'),
    ('Narwhal project', '*.narwhal'), ('3D model', '*.3dm'),
    ('STEP', '*.step *.stp'), ('Drawing', '*.dxf *.svg *.cdr *.pdf *.ai'),
    ('Mesh', '*.obj *.stl'),
)
_PICTURE = 'Narwhal3D:LibraryPicture'
_MAX_IMAGE = 32 * 1024 * 1024
_ZERO = uuid.UUID(int=0)


class LibraryGeometryError(RuntimeError):
    """An asset cannot be preserved or published without loss."""


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise LibraryGeometryError('Library document operations require the main thread')


def _absolute_file(value, label):
    path = Path(os.fspath(value))
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise LibraryGeometryError(f'{label} must be an existing absolute regular file: {path}')
    return path


def _check_geometry(geometry, name):
    supported = (r3.Point, r3.Curve, r3.Surface, r3.Brep, r3.Extrusion, r3.Mesh)
    if not isinstance(geometry, supported):
        raise LibraryGeometryError(f'{name}: {type(geometry).__name__} resources are not supported yet')
    problem = kernel.honesty_problem(geometry)
    if problem:
        raise LibraryGeometryError(f'{name}: {problem}')


def _copy_geometry(geometry, xform=None):
    copied = geometry.Duplicate()
    if copied is None:
        raise LibraryGeometryError('Exact geometry duplication failed')
    # The existing Copy route does the same: Duplicate + transform_geometry gives
    # the copy a fresh master binding without changing the source's master/recipe.
    copied = bridge.transform_geometry(copied, xform or r3.Transform.Identity())
    _check_geometry(copied, 'Copied geometry')
    return copied


def _clone_subset(source, ids=None):
    candidate = NarwhalModel()
    candidate.file = r3.File3dm.Decode(source.file.Encode())
    candidate.path = source.path
    if candidate.file is None:
        raise LibraryGeometryError('Could not copy the source document')
    for item in list(candidate.file.Objects):
        oid = str(item.Attributes.Id)
        if ids is not None and oid not in ids:
            candidate.file.Objects.Delete(oid)
            continue
        _check_geometry(item.Geometry, item.Attributes.Name or oid)
        candidate.replace_geometry(oid, _copy_geometry(item.Geometry))
    if not candidate.object_count():
        raise LibraryGeometryError('No supported objects were selected or imported')
    return candidate


def _read(path, target=None):
    extension = path.suffix.lower()
    if extension == '.narwhal':
        candidate = narwhal_document.load_model(path)
        # Unlike commit_model this only installs the candidate bindings; the
        # registry API explicitly retains prior model/history owners.
        narwhal_document.commit_masters(candidate)
        return candidate
    if extension == '.3dm':
        candidate = NarwhalModel.load(str(path))
        for item in candidate.file.Objects:
            _check_geometry(item.Geometry, item.Attributes.Name or str(item.Attributes.Id))
        return candidate
    from . import import_commands
    if extension not in import_commands.READERS:
        raise LibraryGeometryError(f'Importing {extension or "an extensionless file"} is not supported')
    candidate = NarwhalModel()
    if target is not None:
        candidate.file.Settings.ModelUnitSystem = target.file.Settings.ModelUnitSystem
    imported = import_commands.read_foreign(str(path), candidate)
    layers = import_commands._LayerIndexCache(candidate)
    for geometry, name, layer in imported.items:
        _check_geometry(geometry, name)
        index = layers.index(layer, imported.layers.get(layer, (0, 0, 0))) if layer else 0
        oid, _ = candidate.add_object(geometry, name, index)
        import_commands._apply_plot_width(candidate, oid, imported.plot_widths.get(id(geometry)))
    # Existing readers explicitly describe approximation/omission policies. Keep
    # their report visible rather than claiming unsupported contents were exact.
    candidate.library_import_notes = tuple(imported.notes)
    return candidate


def _image_data(item, model):
    geometry = item.Geometry
    attrs = item.Attributes
    for index in (attrs.MaterialIndex, model.file.Layers[attrs.LayerIndex].RenderMaterialIndex):
        if 0 <= index < len(model.file.Materials):
            material = model.file.Materials[index]
            for getter in ('GetBumpTexture', 'GetEnvironmentTexture', 'GetTransparencyTexture'):
                texture = getattr(material, getter)()
                if texture is not None and texture.FileName:
                    raise LibraryGeometryError(f'{attrs.Name}: {getter} resource preservation is not supported')
    stored = geometry.GetUserString(_PICTURE)
    if stored:
        try:
            record = json.loads(stored)
            if len(record['data']) > 4 * ((_MAX_IMAGE + 2) // 3):
                raise ValueError('image exceeds 32 MiB')
            data = base64.b64decode(record['data'], validate=True)
            if not data or len(data) > _MAX_IMAGE or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('invalid image digest/size')
            if record['suffix'] not in ('.png', '.jpg', '.jpeg'):
                raise ValueError('unsupported image format')
            return record, data
        except (KeyError, TypeError, ValueError) as exc:
            raise LibraryGeometryError(f'{item.Attributes.Name}: invalid embedded Picture: {exc}') from exc
    frame = bridge.picture_frame(geometry)
    path = frame.get('image') if frame else model.object_texture_path(item)
    # Refuse external non-bitmap material resources rather than dropping them.
    a = item.Attributes
    indices = [a.MaterialIndex, model.file.Layers[a.LayerIndex].RenderMaterialIndex]
    for index in indices:
        if 0 <= index < len(model.file.Materials):
            material = model.file.Materials[index]
            for getter in ('GetBumpTexture', 'GetEnvironmentTexture', 'GetTransparencyTexture'):
                texture = getattr(material, getter)()
                if texture is not None and texture.FileName:
                    raise LibraryGeometryError(f'{a.Name}: {getter} resource preservation is not supported')
            bitmap = material.GetBitmapTexture()
            if bitmap is not None and bitmap.FileName and not path:
                raise LibraryGeometryError(f'{a.Name}: bitmap resource cannot be found: {bitmap.FileName}')
    if not path:
        return None
    path = Path(path)
    if not path.is_file() or path.stat().st_size > _MAX_IMAGE:
        raise LibraryGeometryError(f'{a.Name}: missing or oversized Picture resource: {path}')
    from . import picture
    info = picture.read_image_info(str(path))
    data = path.read_bytes()
    return {'suffix': '.png' if info.format == 'PNG' else '.jpg', 'sha256': hashlib.sha256(data).hexdigest(),
            'data': base64.b64encode(data).decode('ascii')}, data


def _cache_image(record, data):
    # Persistent content-addressed resource, not the source or an import temporary.
    # Bytes also remain embedded on the geometry for subsequent capture/export.
    folder = Path(os.path.expanduser('~/.cache/narwhal3d/library-pictures'))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (record['sha256'] + record['suffix'])
    if not path.is_file() or path.read_bytes() != data:
        with tempfile.NamedTemporaryFile(dir=folder, suffix='.tmp', delete=False) as stream:
            stream.write(data)
            temporary = stream.name
        os.replace(temporary, path)
    return str(path)


def _pictures(model):
    for item in model.file.Objects:
        embedded = _image_data(item, model)
        if embedded is None:
            continue
        record, data = embedded
        geometry = item.Geometry
        path = _cache_image(record, data)
        frame = bridge.picture_frame(geometry)
        if frame is None:
            from .commands import NarwhalContext
            frame = NarwhalContext(model)._texture_frame(geometry, item)
            if frame is None:
                raise LibraryGeometryError(f'{item.Attributes.Name}: non-planar bitmap mapping is not supported')
        frame = dict(frame, image=path)
        from . import picture
        geometry.SetUserString(picture.KEY, picture.frame_to_json(frame))
        geometry.SetUserString(_PICTURE, json.dumps(record, separators=(',', ':')))
        for index in (item.Attributes.MaterialIndex,
                      model.file.Layers[item.Attributes.LayerIndex].RenderMaterialIndex):
            if 0 <= index < len(model.file.Materials):
                model.file.Materials[index].SetBitmapTexture(path)


def _bounds(model):
    box = None
    for item in model.file.Objects:
        # OCCT tight bounds, never display triangles, for analytic masters.
        master = kernel.master_of(item.Geometry)
        if master is not None:
            from .kernel import occt_backend
            low, high = occt_backend.tight_bbox(master)
            part = r3.BoundingBox(r3.Point3d(*low), r3.Point3d(*high))
        else:
            part = item.Geometry.GetBoundingBox()
        box = part if box is None else r3.BoundingBox.Union(box, part)
    if box is None or not box.IsValid:
        raise LibraryGeometryError('Asset has no finite bounding box')
    return box


def _thumbnail(model, path):
    """Render actual display geometry in a private Blender scene, not the viewport."""
    import bpy
    from mathutils import Vector
    from .commands import _picture_display_material
    objects, meshes, curves, cameras, materials = [], [], [], [], []
    scene = bpy.data.scenes.new('Library thumbnail')
    has_pictures = False
    wires_only = all(isinstance(item.Geometry, r3.Curve) for item in model.file.Objects)
    display_points = []
    try:
        box = _bounds(model)
        low = Vector((box.Min.X, box.Min.Y, box.Min.Z))
        high = Vector((box.Max.X, box.Max.Y, box.Max.Z))
        centre = (low + high) * 0.5
        extent = max((high - low).length, 1e-6)
        for item in model.file.Objects:
            bm = bridge.geometry_to_bmesh(item.Geometry)
            try:
                mesh = bpy.data.meshes.new('Library thumbnail display')
                meshes.append(mesh)
                bm.to_mesh(mesh)
            finally:
                bm.free()
            display_points.extend(vertex.co.copy() for vertex in mesh.vertices)
            if not len(mesh.polygons) and not len(mesh.edges):
                raise LibraryGeometryError(
                    f'{item.Attributes.Name}: isolated point display thumbnails are not supported yet')
            if not len(mesh.polygons):
                curve = bpy.data.curves.new('Library thumbnail wires', 'CURVE')
                curves.append(curve)
                curve.dimensions = '3D'
                curve.bevel_depth = extent / 180
                curve.bevel_resolution = 0
                for edge in mesh.edges:
                    spline = curve.splines.new('POLY')
                    spline.points.add(1)
                    for point, index in zip(spline.points, edge.vertices):
                        point.co = (*mesh.vertices[index].co, 1)
                data = curve
            else:
                data = mesh
            ob = bpy.data.objects.new('Library thumbnail object', data)
            objects.append(ob)
            scene.collection.objects.link(ob)
            ob.color = (0.035, 0.16, 0.38, 1) if not mesh.polygons else (0.65, 0.65, 0.65, 1)
            frame = bridge.picture_frame(item.Geometry)
            if frame:
                has_pictures = True
                data.materials.append(_picture_display_material(frame['image']))
            else:
                material = bpy.data.materials.new('Library thumbnail surface')
                material.diffuse_color = ob.color
                materials.append(material)
                data.materials.append(material)
        camera = bpy.data.cameras.new('Library thumbnail camera')
        cameras.append(camera)
        ob = bpy.data.objects.new('Library thumbnail camera', camera)
        objects.append(ob)
        scene.collection.objects.link(ob)
        direction = Vector((1.3, -1.7, 1.2)).normalized()
        # Flat drawings should read as drawings, not foreshortened 3D objects.
        # This controls only the isolated Blender camera, never CAD geometry.
        if wires_only:
            dimensions = high-low
            if dimensions.z <= extent*1e-6:
                direction = Vector((0, 0, 1))
            elif dimensions.y <= extent*1e-6:
                direction = Vector((0, -1, 0))
            elif dimensions.x <= extent*1e-6:
                direction = Vector((1, 0, 0))
        ob.location = centre + direction * extent * 2
        orientation = (centre - ob.location).to_track_quat('-Z', 'Y')
        ob.rotation_euler = orientation.to_euler()
        camera.type = 'ORTHO'
        # Fit camera-space display bounds, not the world bounding-box diagonal.
        projected = [orientation.inverted() @ (point-centre) for point in display_points]
        camera.ortho_scale = max(max(abs(point.x), abs(point.y)) for point in projected)*2.35
        camera.clip_start = extent / 1000
        camera.clip_end = extent * 10
        scene.camera = ob
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.display.shading.light = 'FLAT' if wires_only else 'STUDIO'
        scene.display.shading.color_type = 'TEXTURE' if has_pictures else 'OBJECT'
        scene.display.shading.studio_light = 'studio.sl'
        scene.display.shading.show_shadows = False
        scene.display.shading.show_cavity = False
        scene.display.shading.background_type = 'VIEWPORT'
        scene.display.shading.background_color = (0.72, 0.79, 0.88)
        scene.world = bpy.data.worlds.new('Library thumbnail world')
        scene.world.color = (0.055, 0.055, 0.055)
        scene.render.resolution_x = scene.render.resolution_y = 256
        scene.render.resolution_percentage = 100
        scene.view_settings.view_transform = 'Standard'
        scene.render.film_transparent = True
        scene.render.image_settings.color_mode = 'RGBA'
        scene.render.image_settings.file_format = 'PNG'
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True, scene=scene.name)
        if not Path(path).is_file():
            raise LibraryGeometryError('Blender did not write the asset thumbnail')
    finally:
        world = scene.world
        for ob in objects:
            bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.scenes.remove(scene)
        for data in meshes:
            bpy.data.meshes.remove(data)
        for data in curves:
            bpy.data.curves.remove(data)
        for data in cameras:
            bpy.data.cameras.remove(data)
        for data in materials:
            bpy.data.materials.remove(data)
        if world is not None:
            bpy.data.worlds.remove(world)


def _store(candidate, store, name, category_id):
    _pictures(candidate)
    with tempfile.TemporaryDirectory(prefix='narwhal-library-') as directory:
        source = Path(directory) / 'asset.narwhal'
        thumbnail = Path(directory) / 'thumbnail.png'
        # Robust Save repairs only this private candidate, never the live model.
        if not narwhal_document.save_model(candidate, source):
            raise LibraryGeometryError('The canonical asset writer failed')
        if candidate.last_save_warnings:
            raise LibraryGeometryError('Asset preservation refused: ' + '; '.join(candidate.last_save_warnings))
        _thumbnail(candidate, thumbnail)
        asset = store.add_asset(name, category_id, str(source), str(thumbnail))
        _absolute_file(asset['source_file'], 'Stored source_file')
        _absolute_file(asset['thumbnail'], 'Stored thumbnail')
        _remember_asset(asset, candidate)
        return asset


def _selected_candidate(ctx):
    _main_thread()
    ids = set()
    for selected in ctx.selected_objects():
        oid = ctx.model.object_id_by_name(selected.name)
        if oid is None:
            raise LibraryGeometryError(f'{selected.name}: selection is not a document object')
        ids.add(str(oid))
    if not ids:
        raise LibraryGeometryError('Select geometry before adding a library asset')
    return _clone_subset(ctx.model, ids)


def capture_selection(ctx, store, name, category_id):
    return _store(_selected_candidate(ctx), store, name, category_id)


class PreparedCapture:
    """Frozen save bytes cross the worker boundary; CAD/Blender objects do not."""
    def __init__(self, candidate, store, name, category_id):
        self.candidate, self.store = candidate, store
        self.name, self.category_id = name, category_id
        self.directory = tempfile.TemporaryDirectory(prefix='narwhal-library-capture-')
        self.source = Path(self.directory.name) / 'asset.narwhal'
        self.thumbnail = Path(self.directory.name) / 'thumbnail.png'
        self.staged = None
        try:
            _pictures(candidate)
            notes = []
            narwhal_document._prepare_for_save(candidate, notes)
            if notes:
                raise LibraryGeometryError('Asset preservation refused: ' + '; '.join(notes))
            self.staged = narwhal_document._stage(candidate, self.source)
            _thumbnail(candidate, self.thumbnail)
        except BaseException:
            self.cleanup()
            raise

    def finish(self):
        # No host APIs or candidate access here: native validation/packaging and
        # Rust catalogue publication operate only on immutable staged files.
        try:
            narwhal_document._validate_and_pack(self.staged)
            return self.store.add_asset(self.name, self.category_id, self.source, self.thumbnail)
        finally:
            self.cleanup()

    def admit(self, asset):
        _main_thread()
        _remember_asset(asset, self.candidate)

    def cleanup(self):
        if self.staged is not None:
            self.staged.cleanup()
        self.directory.cleanup()


def prepare_capture(ctx, store, name, category_id):
    """Main-thread snapshot/render; caller runs finish off-thread, admit on main."""
    return PreparedCapture(_selected_candidate(ctx), store, name, category_id)



def import_asset(ctx, store, path, name, category_id):
    _main_thread()
    path = _absolute_file(Path(path).expanduser().absolute(), 'Import source')
    candidate = _read(path, ctx.model)
    for note in getattr(candidate, 'library_import_notes', ()):
        from .state import app_state
        app_state.log('Library import: ' + note)
    return _store(_clone_subset(candidate), store, name, category_id)


def refresh_preview(ctx, store, asset):
    """Re-render an immutable source; Rust atomically publishes only its thumbnail."""
    _main_thread()
    source = _absolute_file(asset['source_file'], 'Asset source')
    candidate = _read(source, ctx.model)
    with tempfile.TemporaryDirectory(prefix='narwhal-library-preview-') as directory:
        thumbnail = Path(directory) / 'thumbnail.png'
        _thumbnail(candidate, thumbnail)
        return store.refresh_thumbnail(asset['id'], thumbnail)


def _admit_table_resources(model):
    """Refuse table state the existing single-history restore cannot replay."""
    default_layer = r3.Layer()
    for layer in model.file.Layers:
        if layer.ParentLayerId != _ZERO:
            raise LibraryGeometryError(
                f'{layer.Name}: nested layer restoration is not supported by the existing Undo snapshot')
        for field in ('Expanded', 'IgesLevel', 'LinetypeIndex', 'UserStringCount'):
            if getattr(layer, field) != getattr(default_layer, field):
                raise LibraryGeometryError(
                    f'{layer.Name}: layer {field} restoration is not supported by the existing Undo snapshot')
    for group in model.file.Groups:
        if group.UserStringCount:
            raise LibraryGeometryError(
                f'{group.Name}: group user-text restoration is not supported by the existing Undo snapshot')
    for item in model.file.Objects:
        if item.Attributes.LinetypeIndex >= 0:
            raise LibraryGeometryError(
                f'{item.Attributes.Name}: custom linetype resources are not supported yet')


# A bounded cache of private host models, not another document/catalogue authority.
_asset_cache = OrderedDict()


def _remember_asset(asset, candidate):
    source = _absolute_file(asset['source_file'], 'Stored source_file')
    stamp = source.stat()
    if stamp.st_size <= 4 * 1024 * 1024:
        key = (str(source), stamp.st_mtime_ns, stamp.st_size)
        _asset_cache[key] = candidate
        _asset_cache.move_to_end(key)
        while len(_asset_cache) > 2:
            _asset_cache.popitem(last=False)


def _cached_asset(path, modified, size):
    key = (path, modified, size)
    if key not in _asset_cache:
        candidate = _read(Path(path))
        _pictures(candidate)
        _admit_table_resources(candidate)
        _asset_cache[key] = candidate
        while len(_asset_cache) > 2:
            _asset_cache.popitem(last=False)
    _asset_cache.move_to_end(key)
    return _asset_cache[key]


def clear_asset_cache():
    _asset_cache.clear()


def asset_is_cached(asset):
    source = _absolute_file(asset['source_file'], 'Asset source_file')
    stamp = source.stat()
    return (str(source),stamp.st_mtime_ns,stamp.st_size) in _asset_cache


def insert_loaded_asset(ctx, asset, prepared, point=None, plane=None):
    """Publish a fully admitted cold-load candidate, never a second disk read."""
    _main_thread()
    if prepared.path != str(Path(asset['source_file'])):
        raise LibraryGeometryError('Prepared asset source changed')
    narwhal_document.commit_masters(prepared)
    _pictures(prepared)
    _admit_table_resources(prepared)
    _remember_asset(asset, prepared)
    return _insert_prepared(ctx,asset,prepared,point,plane)


def insert_asset(ctx, asset, point=None, plane=None):
    _main_thread()
    # No nested record: a failed asset must never abandon another command's Undo.
    if ctx.undo.recording or not ctx.undo.enabled:
        raise LibraryGeometryError('Library insertion requires an idle, Undo-enabled document')
    source = _absolute_file(asset['source_file'], 'Asset source_file')
    _absolute_file(asset['thumbnail'], 'Asset thumbnail')
    if source.suffix.lower() not in ('.narwhal', '.3dm'):
        raise LibraryGeometryError('Stored geometry must be a canonical .narwhal or .3dm file')
    stamp = source.stat()
    prepared = (_cached_asset(str(source), stamp.st_mtime_ns, stamp.st_size)
                if stamp.st_size <= 4 * 1024 * 1024 else _read(source))
    return _insert_prepared(ctx,asset,prepared,point,plane)


def _insert_prepared(ctx, asset, prepared, point, plane):
    _main_thread()
    if ctx.undo.recording or not ctx.undo.enabled:
        raise LibraryGeometryError('Library insertion requires an idle, Undo-enabled document')
    candidate = _clone_subset(prepared)
    _pictures(candidate)
    _admit_table_resources(candidate)
    _admit_table_resources(ctx.model)
    box = _bounds(candidate)
    factor = r3.UnitSystem.UnitScale(candidate.file.Settings.ModelUnitSystem,
                                     ctx.model.file.Settings.ModelUnitSystem)
    if not math.isfinite(factor) or factor <= 0:
        raise LibraryGeometryError('Cannot convert these document units')
    base = r3.Point3d((box.Min.X + box.Max.X) * 0.5 * factor,
                      (box.Min.Y + box.Max.Y) * 0.5 * factor, box.Min.Z * factor)
    if plane is None:
        plane = ctx.active_cplane()
    if plane is None:
        destination = r3.Plane.WorldXY()
    elif isinstance(plane, r3.Plane):
        destination = r3.Plane(plane.Origin, plane.XAxis, plane.YAxis)
    else:
        # CPlane is host metadata; openNURBS constructs the actual frame.
        destination = r3.Plane(r3.Point3d(*plane.origin),
                               r3.Vector3d(*plane.xaxis), r3.Vector3d(*plane.yaxis))
    if point is not None:
        destination.Origin = point if isinstance(point, r3.Point3d) else r3.Point3d(*point)
    scaled = r3.Transform.Scale(r3.Point3d(0, 0, 0), factor)
    placement = r3.Transform.PlaneToPlane(r3.Plane(base, r3.Vector3d(0, 0, 1)), destination)
    if not placement.IsValid:
        raise LibraryGeometryError('Invalid insertion point or plane')
    plan = []
    attribute_holder = r3.File3dm()
    for item in candidate.file.Objects:
        geometry = _copy_geometry(item.Geometry, scaled)
        geometry = bridge.transform_geometry(geometry, placement)
        _check_geometry(geometry, item.Attributes.Name)
        attrs = candidate._object_registry.observer.copy_attributes(
            candidate.file, str(item.Attributes.Id), attribute_holder)
        attrs.Id = _ZERO
        plan.append((geometry, attrs))
    before = ctx.model.snapshot()
    counters = dict(ctx.counters)
    selected = [ob.name for ob in ctx.selected_objects()]
    ctx.begin_undo('Insert library asset')
    try:
        ctx.note_change()
        file = ctx.model.file
        material_map = {}
        for index, material in enumerate(candidate.file.Materials):
            copied = r3.CommonObject.Decode(material.Encode())
            copied.Id = _ZERO
            material_map[index] = file.Materials.Add(copied)
        layer_map = {}
        layer_ids = {}
        for index, layer in enumerate(candidate.file.Layers):
            copied = r3.CommonObject.Decode(layer.Encode())
            copied.Id = _ZERO
            copied.ParentLayerId = _ZERO
            copied.RenderMaterialIndex = material_map.get(layer.RenderMaterialIndex, -1)
            target = file.Layers.Add(copied)
            layer_map[index] = target
            layer_ids[str(layer.Id)] = file.Layers[target].Id
        for index, layer in enumerate(candidate.file.Layers):
            file.Layers[layer_map[index]].ParentLayerId = layer_ids.get(str(layer.ParentLayerId), _ZERO)
        groups = {}
        for index, group in enumerate(candidate.file.Groups):
            copied = r3.Group()
            copied.Name = group.Name
            groups[index] = len(file.Groups)
            file.Groups.Add(copied)
        assembly = None
        if len(plan) > 1:
            group = r3.Group()
            group.Name = asset.get('name', 'Library asset')
            assembly = len(file.Groups)
            file.Groups.Add(group)
        names = []
        layers = ctx.model.layers()
        for geometry, attrs in plan:
            base_name = attrs.Name or 'Asset'
            attrs.Name = ctx.next_name(base_name)
            while ctx.model.object_id_by_name(attrs.Name) is not None:
                attrs.Name = ctx.next_name(base_name)
            attrs.LayerIndex = layer_map[attrs.LayerIndex]
            attrs.MaterialIndex = material_map.get(attrs.MaterialIndex, -1)
            old_groups = list(attrs.GetGroupList2() or ())
            attrs.RemoveFromAllGroups()
            for group in old_groups:
                attrs.AddToGroup(groups[group])
            if assembly is not None:
                attrs.AddToGroup(assembly)
            oid = str(file.Objects.Add(geometry, attrs))
            if oid == str(_ZERO):
                raise LibraryGeometryError(f'{attrs.Name}: document refused exact geometry')
            item = file.Objects.FindId(oid)
            if ctx._make_file_object_display(item, layers) is None:
                raise LibraryGeometryError(f'{attrs.Name}: display publication failed')
            ctx.model.register_blender_object(oid, attrs.Name)
            names.append(attrs.Name)
        ctx.model.snapshot()  # admission before committing the one existing record
        ctx.end_undo()
        return names
    except Exception:
        # Do not discard the only recovery record if host restoration itself
        # fails. A successfully restored document needs no new history entry.
        ctx.model.restore(before)
        ctx.undo.abandon()
        ctx.rebuild_display()
        ctx.select_by_names(selected)
        ctx.counters = counters
        raise

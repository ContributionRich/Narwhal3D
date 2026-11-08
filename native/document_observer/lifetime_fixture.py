"""Artwork-free exact fixtures and strict observer discovery for lifetime tests.

Python orchestrates existing OCCT/openNURBS producers; it implements no geometry.
An explicit candidate is selected with NARWHAL_TEST_OBSERVER_LIBRARY/RECEIPT (or
CLI equivalents). Every factory load still runs production load_candidate(); no
qualification flag, receipt, kernel cache, or native admission check is bypassed.
"""
from __future__ import annotations

import atexit
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TARGET_NAME = 'LifetimeBody'
SYNTHETIC_COUNT = 8
BODY_VOLUME = 480.0 + 6.0 * math.pi


def bootstrap():
    """Import the public app's pure modules without importing its Blender entrypoint."""
    package = sys.modules.get('narwhal3d')
    if package is None:
        package = types.ModuleType('narwhal3d')
        package.__path__ = [str(ROOT / 'narwhal3d')]
        sys.modules['narwhal3d'] = package
    elif str(ROOT / 'narwhal3d') not in getattr(package, '__path__', ()):
        raise RuntimeError('lifetime tests require a fresh process using this source tree')


def observer_arguments(parser):
    parser.add_argument('--library', default=os.environ.get('NARWHAL_TEST_OBSERVER_LIBRARY'),
                        help='explicit qualified observer; default is production discovery')
    parser.add_argument('--receipt', default=os.environ.get('NARWHAL_TEST_OBSERVER_RECEIPT'),
                        help='qualified build.json; defaults beside --library')


def windows_refuses(loader, check):
    """Exercise the real gates without loading or calling an unsupported Windows DLL."""
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('unqualified native lifetime entry point was called')

    module = types.SimpleNamespace(observe=forbidden, index=forbidden,
                                   copy_attributes=forbidden,
                                   master_lifetime_abi=forbidden)
    for name in ('lease_bind', 'lease_key', 'lease_unbind', 'lease_hold',
                 'lease_hold_key', 'lease_drain', 'file_owner', 'lease_stats'):
        setattr(module, name, forbidden)
    with patch.object(loader, '_platform_tag', return_value='windows-x86_64'):
        check(not loader._lifetime_allowed({}, '0' * 64, 'windows-x86_64'),
              'Windows cannot admit lifetime cleanup without qualified proof')
        check(loader.Observer(module, lifetime=True).master_lifetime() is None,
              'Windows cannot force lifetime through the observer constructor')
    check(not calls, 'Windows refusal calls no native lifetime entry points')


def admitted_observer(args, check):
    """Select a candidate, but retain every production host/source/ABI proof check."""
    from narwhal3d import native_document_observer as loader

    windows_refuses(loader, check)
    if sys.platform != 'linux':
        print('NOT_QUALIFIED: conservative strong ownership; native workload not activated')
        return None, None
    if args.receipt and not args.library:
        raise ValueError('--receipt requires --library')
    if args.library:
        library = Path(args.library).resolve()
        receipt = Path(args.receipt).resolve() if args.receipt else library.with_name('build.json')

        def discover():
            return loader.load_candidate(library, receipt)

        observer = discover()
        # Only test discovery is redirected. Constructors still execute all the
        # production candidate checks, including source-bound lifetime admission.
        routing = patch.object(loader, 'load_observer', side_effect=discover)
        routing.start()
        atexit.register(routing.stop)
    else:
        observer = loader.load_observer()
        module = sys.modules[loader._MODULE]
        library = Path(module.__file__).resolve()
        receipt = library.with_name('build.json')
    check(observer.master_lifetime() is not None,
          'real pinned Linux observer passed lifetime admission')
    module = sys.modules[loader._MODULE]
    evidence = json.loads(receipt.read_text())
    digest = hashlib.sha256(library.read_bytes()).hexdigest()
    check(not loader._lifetime_allowed(evidence, digest, 'windows-x86_64'),
          'even qualified Linux evidence cannot activate Windows cleanup')
    check(loader._lifetime_allowed(evidence, digest, 'linux-x86_64'),
          'candidate source, binary, host and qualification proof all match')
    with tempfile.TemporaryDirectory(prefix='lifetime-admission-negative-') as folder:
        path = Path(folder) / 'build.json'
        missing = copy.deepcopy(evidence)
        missing.pop('master_lifetime', None)
        path.write_text(json.dumps(missing))
        check(loader.load_candidate(library, path).master_lifetime() is None,
              'ordinary observer receipt cannot activate lifetime cleanup')
        wrong = copy.deepcopy(evidence)
        wrong['host']['sha256'] = '0' * 64
        path.write_text(json.dumps(wrong))
        try:
            loader.load_candidate(library, path)
        except loader.ObservationUnavailable:
            check(True, 'wrong host is refused by production candidate admission')
        else:
            check(False, 'wrong host is refused by production candidate admission')
    # Leave the selected, fully admitted capability as the real factory result.
    return observer, module


def child_observer_arguments(args):
    result = []
    if args.library:
        result += ['--library', str(Path(args.library).resolve())]
    if args.receipt:
        result += ['--receipt', str(Path(args.receipt).resolve())]
    return result


def make_file(private_fixture=None):
    """Return a fresh in-memory exact CAD document, or explicitly requested legacy data.

    Defaults never read private paths or save a proxy as manufacturing geometry.
    The Boolean/extrusion display proxies retain complete exact reconstruction
    recipes and registered OCCT masters. All generated primitives are analytic.
    """
    import rhino3dm as r3
    from narwhal3d import bridge, kernel, model
    from narwhal3d.kernel import occt_backend as occt

    if private_fixture is not None:
        file = r3.File3dm.Read(str(private_fixture))
        if file is None:
            raise ValueError('could not read explicit private fixture')
        return file
    document = model.NarwhalModel()  # normal qualified host ownership hooks
    box = bridge.make_box((0, 0, 0), (10, 8, 0), 6)
    side = bridge.make_cylinder((10, 4, 0), 2, 6)
    union = bridge.boolean_occt(box, side, 'UNION')
    if union is None: raise AssertionError(bridge.last_occt_decline())
    drill = bridge.make_cylinder((3, 4, 0), 1, 6)
    body = bridge.boolean_occt(union, drill, 'DIFFERENCE')
    if body is None: raise AssertionError(bridge.last_occt_decline())
    circle = bridge.make_circle((18, 0, 0), 1.25)
    circular_extrusion = bridge.extrude_curve(circle, 4, direction=(0, 0, 1), solid=True)
    profile = bridge.make_polyline([(0, 14, 0), (3, 14, 0), (4, 16, 0),
                                   (3, 18, 0), (0, 18, 0), (-1, 16, 0)],
                                  degree=3, closed=True)
    freeform = bridge.extrude_curve(profile, 3, direction=(0, 0, 1), solid=True)
    objects = (
        (TARGET_NAME, body), ('CircularExtrusion', circular_extrusion),
        ('NurbsExtrusion', freeform), ('ExactCircle', circle),
        ('CubicProfile', profile),
        ('AnalyticCylinder', bridge.make_cylinder((24, 0, 0), .75, 3)),
        ('AnalyticSphere', bridge.make_sphere((24, 6, 0), 2)),
        ('ReferencePoint', r3.Point(r3.Point3d(0, 0, 0))),
    )
    for name, geometry in objects:
        document.add_object(geometry, name)
    for geometry in (body, circular_extrusion, freeform):
        master = kernel.master_of(geometry)
        if master is None or not occt.is_valid(master) or not occt.is_airtight(master):
            raise AssertionError('synthetic solid requires a valid airtight analytic master')
        if not kernel.recipe_of(geometry):
            raise AssertionError('synthetic master requires its complete reconstruction recipe')
    body_types = occt.face_types(kernel.master_of(body))
    if not {'PLANE', 'CYLINDER'} <= set(body_types):
        raise AssertionError('synthetic Boolean must retain planar and cylindrical faces')
    if abs(occt.volume(kernel.master_of(body)) - BODY_VOLUME) > 1e-8:
        raise AssertionError('synthetic Boolean disagrees with independent closed-form volume')
    if not set(occt.face_types(kernel.master_of(freeform))) - {'PLANE'}:
        raise AssertionError('cubic extrusion must not become faceted manufacturing geometry')
    if len(document.file.Objects) != SYNTHETIC_COUNT or list(document.file.EmbeddedFilePaths()):
        raise AssertionError('synthetic fixture must have eight CAD objects and no artwork')
    return document.file


def target_object(file, owner=None):
    """Select the public synthetic body; retain the old private-fixture target option."""
    for wanted in (TARGET_NAME, 'Polysurface31'):
        for obj in file.Objects:
            if owner is not None:
                owner(obj)
            if obj.Attributes.Name == wanted:
                return obj
    raise ValueError('fixture has neither LifetimeBody nor the explicit legacy Polysurface31 target')

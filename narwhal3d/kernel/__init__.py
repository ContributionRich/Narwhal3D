"""Geometry-kernel interface with verifiable representation provenance.

A faceted result must never be mistaken for an analytic solid: downstream measurement,
boolean operations, and manufacturing exports require an explicit distinction between
exact surfaces and tessellated approximations. Geometry therefore carries provenance,
and the declared value is checked against the shape itself.

TWO LAYERS, because one is not enough
-------------------------------------
1. **Declared** -- every producer tags its result :data:`ANALYTIC` or
   :data:`MESH_DERIVED`. `rhino3dm` geometry supports `SetUserString`, and the tag
   survives a `.3dm` write/read cycle, so provenance is retained when a model is saved.
   The tag records how a solid was made, which shape alone cannot establish.

2. **Cross-checked** -- :func:`looks_tessellated` measures the shape independently, so a
   wrong or missing tag is caught rather than trusted. Representative measurements are::

       analytic box        6 faces   {planar: 6}
       analytic cylinder   3 faces   {Surface: 1, planar: 2}
       mesh-derived cube  12 faces   {planar: 12}

   A tessellation of curved geometry has many facet-like faces. A genuine analytic solid
   either has few faces or includes a higher-degree curved surface. Thus a dense set of
   facet-like faces catches a faceted cylinder claiming to be analytic while allowing a
   real box.

The declaration alone would trust the producer, while shape inspection alone cannot
separate a legitimate many-sided prism from a tessellation. Together,
:func:`honesty_problem` catches missing and false declarations.

DESIGN CONSTRAINTS
------------------
* This module imports neither `bpy` nor `OCP`; it remains usable without Blender or a
  loaded geometry backend.
* Command code calls `kernel.*`; only `kernel.occt_backend` imports OCP. This keeps the
  backend swappable and isolates failures in the native kernel.
* Operations raise :class:`KernelError` with a user-facing message rather than exposing
  a raw backend exception.
"""
from __future__ import annotations

#: Geometry whose faces are real surfaces -- planes, cylinders, spheres, NURBS. This is
#: what Rhino produces and what a STEP export can carry as surfaces. Exportable for
#: manufacture.
ANALYTIC = "analytic"

#: Geometry derived from a tessellation. Watertight as a MESH, but every curved face is
#: faceted, so a STEP export carries facets rather than surfaces. Usable for display and
#: for 3D printing; NOT equivalent to Rhino's output, and must never be presented as if
#: it were.
MESH_DERIVED = "mesh-derived"

#: No tag. Treated as a PROBLEM for solids, not as a default -- see
#: :func:`honesty_problem`. Untagged solids cannot safely establish how they were made.
UNKNOWN = "unknown"

REPRESENTATIONS = (ANALYTIC, MESH_DERIVED, UNKNOWN)
# ANALYTIC_MASTER is defined further down (it needs REPRESENTATION_KEY); it is a fourth
# legitimate value and `all_representations()` is the one place that lists every one.

#: The user-string key. Namespaced because it travels inside the user's `.3dm` and must
#: not collide with Rhino's own keys or a plug-in's.
REPRESENTATION_KEY = "Narwhal3D:Representation"

#: Faces at or above this count, with a facet-like FRACTION at or above
#: :data:`TESSELLATION_PLANAR_FRACTION`, read as a tessellation.
#:
#: Chosen from measurements of representative producers:
#:
#:     analytic box                     6 faces,   6 planar  (100 %)
#:     analytic cylinder                3 faces,   2 planar  ( 67 %)
#:     extrusion of a circle            3 faces,   2 planar  ( 67 %)
#:     box with 12 edges filleted      26 faces,   6 planar  ( 23 %)
#:     boolean UNION      (mesh route) 514 faces, 512 planar ( 99.6 %)
#:     boolean DIFFERENCE (mesh route) 304 faces, 302 planar ( 99.3 %)
#:     boolean INTERSECT  (mesh route) 354 faces, 354 planar (100 %)
#:
#: 24 sits above every honest producer and far below every faceted one. It is what keeps
#: a 6-face box -- which IS 100 % bilinear, being six planes -- from reading as a soup.
TESSELLATION_FACE_THRESHOLD = 24

#: How FACET-LIKE a dense solid must be before it reads as a tessellation.
#:
#: THIS CONSTANT'S MEASUREMENT HAS BEEN WRONG TWICE, and both corrections are recorded
#: here because the mistake is instructive: a gate can encode the very bug it is meant
#: to catch.
#:
#: 1. Requiring *every* face to be planar misses mesh-derived booleans measured at
#:    512/514 and 302/304 planar faces -- each carrying a couple of
#:    stray spline patches from `Brep.CreateFromMesh` -- and **both would have slipped
#:    through the guarantee**. A facet soup with two curved scraps in it is still a
#:    facet soup. So it became a FRACTION.
#: 2. The SECOND version measured that fraction with `IsPlanar()`. The audit then
#:    measured `edgefillet.fillet_edges` -- a MESH fillet, declared as such in its own
#:    docstring -- at **492 faces of which only 64 are planar (13 %)**, and the gate
#:    called it ANALYTIC. The bevel's quads are WARPED, so they are not planar, and
#:    planarity simply cannot see them.
#:
#: The fix is to stop asking "is this face flat?" and ask what the surface IS.
#: `Brep.CreateFromMesh` emits every facet as a **BILINEAR** patch -- degree 1 in u AND
#: degree 1 in v -- while any genuinely curved
#: analytic surface is degree >= 2 in the direction that curves:
#:
#:     HONEST                                faces  facet-like
#:     make_box                                  6   6 (100 %)  but 6 < 24, not dense
#:     make_cylinder     {(2,1):1, (1,1):2}      3   2 ( 67 %)  curved wall is deg 2x1
#:     make_sphere       {(2,2):1}               1   0 (  0 %)
#:     make_cone         {(2,1):1, (1,1):1}      2   1 ( 50 %)
#:     OCCT filleted box 6 PLANE+12 CYLINDER+8 SPHERE
#:                                              26   6 ( 23 %)  dense BUT honest
#:
#:     MESH ROUTE                            faces  facet-like
#:     boolean UNION     {(1,1):514}          514  514 (100 %)
#:     boolean DIFFERENCE{(1,1):304}          304  304 (100 %)
#:     boolean INTERSECT {(1,1):354}          354  354 (100 %)
#:     fillet_edges(box) {(1,1):492}          492  492 (100 %)  <-- the 13 % MISS
#:     chamfer_edges(box){(1,1): 32}           32   32 (100 %)
#:     cap_planar_holes  {(1,1):212}          212  212 (100 %)
#:
#: Every mesh-route producer is 100 % facet-like and every honest one is <= 67 %, so 0.8
#: separates them with room on both sides. This is strictly STRONGER than the planar
#: version -- it flags everything planarity flagged, plus the fillet it missed.
TESSELLATION_PLANAR_FRACTION = 0.8


class KernelError(Exception):
    """A geometry operation failed, with a message fit to show the user.

    Exists so a kernel failure is reportable rather than a raw C++ abort or a bare
    `RuntimeError` from somewhere deep in a backend.
    """


def tag(geom, representation):
    """Record how `geom` was made. Returns True if the tag was stored.

    Call this in EVERY producer, at the point of creation, where the answer is actually
    known. Inferring it later is the thing this module exists to avoid.
    """
    if representation not in (ANALYTIC, MESH_DERIVED):
        raise KernelError(
            f"representation must be {ANALYTIC!r} or {MESH_DERIVED!r}, "
            f"not {representation!r}")
    setter = getattr(geom, "SetUserString", None)
    if not callable(setter):
        return False
    try:
        return bool(setter(REPRESENTATION_KEY, representation))
    except Exception:  # noqa: BLE001
        return False


def representation_of(geom):
    """The DECLARED representation of `geom`, or :data:`UNKNOWN`.

    Only reads the tag -- it does not guess from the shape. Guessing is
    :func:`looks_tessellated`'s job, and keeping the two separate is what lets
    :func:`honesty_problem` compare them.
    """
    getter = getattr(geom, "GetUserString", None)
    if not callable(getter):
        return UNKNOWN
    try:
        value = getter(REPRESENTATION_KEY)
    except Exception:  # noqa: BLE001
        return UNKNOWN
    # ANALYTIC_MASTER belongs here too. Leaving it out made every OCCT boolean result read
    # as UNKNOWN even though it was correctly tagged -- the tag was written and then
    # filtered away on the way back out.
    return value if value in (ANALYTIC, MESH_DERIVED, ANALYTIC_MASTER) else UNKNOWN


def face_summary(brep):
    """``(face_count, planar_count)`` for a brep, or ``(0, 0)``.

    The measurement :func:`looks_tessellated` is built on. `Surface.IsPlanar()` is the
    discriminator -- MEASURED to separate a cylinder (one non-planar face) from a
    tessellation of one (all planar).
    """
    faces = getattr(brep, "Faces", None)
    if faces is None:
        return (0, 0)
    try:
        total = len(faces)
    except Exception:  # noqa: BLE001
        return (0, 0)
    planar = 0
    for i in range(total):
        try:
            surface = faces[i].UnderlyingSurface()
            if surface.IsPlanar():
                planar += 1
        except Exception:  # noqa: BLE001
            continue
    return (total, planar)


def surface_is_bilinear(surface):
    """True when `surface` is degree 1 in BOTH u and v -- i.e. a flat-or-warped quad.

    This is the signature of a mesh facet. `Brep.CreateFromMesh` emits every facet as a
    bilinear patch, whereas a genuinely
    curved analytic surface is degree >= 2 in whichever direction curves -- a cylinder
    wall is 2x1, a sphere 2x2. See :data:`TESSELLATION_PLANAR_FRACTION` for the full
    table and for why planarity alone was not enough.

    `rhino3dm` exposes this as ``Degree(direction)`` on a NURBS surface (MEASURED: the
    members are ``Degree``, ``OrderU``, ``OrderV``; there is no ``DegreeU``). A
    non-NURBS surface is converted first, and anything that cannot answer returns False
    -- an unmeasurable face must not be *assumed* to be a facet.
    """
    if surface is None:
        return False
    fn = getattr(surface, "Degree", None)
    if callable(fn):
        try:
            return int(fn(0)) <= 1 and int(fn(1)) <= 1
        except Exception:  # noqa: BLE001
            pass
    try:
        ns = surface.ToNurbsSurface()
        return int(ns.Degree(0)) <= 1 and int(ns.Degree(1)) <= 1
    except Exception:  # noqa: BLE001
        return False


def facet_like_summary(brep):
    """``(face_count, facet_like_count)`` -- the measurement the gate actually uses.

    A face is FACET-LIKE when it is planar **or** bilinear. The union of the two is
    deliberate: it is a superset of the old planar-only measurement, so this cannot flag
    fewer things than before (it caught `fillet_edges`, which planarity missed at 13 %),
    and a trimmed plane that happens to carry a higher-degree surface still counts via
    `IsPlanar`.
    """
    faces = getattr(brep, "Faces", None)
    if faces is None:
        return (0, 0)
    try:
        total = len(faces)
    except Exception:  # noqa: BLE001
        return (0, 0)
    facet_like = 0
    for i in range(total):
        try:
            surface = faces[i].UnderlyingSurface()
        except Exception:  # noqa: BLE001
            continue
        planar = False
        try:
            planar = bool(surface.IsPlanar())
        except Exception:  # noqa: BLE001
            planar = False
        if planar or surface_is_bilinear(surface):
            facet_like += 1
    return (total, facet_like)


def looks_tessellated(brep, threshold=TESSELLATION_FACE_THRESHOLD,
                      planar_fraction=TESSELLATION_PLANAR_FRACTION):
    """True when `brep`'s SHAPE says it is a tessellation, whatever its tag claims.

    Independent of :func:`representation_of` on purpose: this is the check that catches
    a producer which forgot to tag, or tagged wrongly. See
    :data:`TESSELLATION_PLANAR_FRACTION` for the measured basis -- and for the two ways
    this check was previously too weak (requiring *every* face planar, which let two real
    booleans through; then measuring by planarity, which called a 492-face MESH fillet
    analytic because its quads are warped).

    The measurement is :func:`facet_like_summary`: planar **or bilinear**. Face COUNT
    still has to clear `threshold`, because a legitimate 6-face box is six planes and so
    100 % facet-like.

    A curved solid must retain its analytic curved faces rather than replacing them with
    many planar facets. A dense collection of facet-like faces violates that requirement,
    and this check detects it.
    """
    total, facet_like = facet_like_summary(brep)
    if total < threshold or total <= 0:
        return False
    return (facet_like / float(total)) >= planar_fraction


#: A display PROXY whose real geometry is a registered analytic master.
#:
#: WHY A THIRD VALUE EXISTS. rhino3dm cannot construct a multi-face brep, so an analytic
#: 9-face OCCT boolean result cannot be expressed as an
#: `r3.Brep`. The document therefore holds two things for such a solid:
#:
#:   * the **master** -- the OCCT shape, which is the geometry: what gets measured,
#:     booleaned again, and exported to STEP for a machine shop;
#:   * a **proxy** -- a triangulated `r3.Brep` used ONLY to draw, snap and hit-test, which
#:     is exactly what Rhino's render mesh is.
#:
#: Tagging that proxy `ANALYTIC` would be a lie (it measures as facets) and tagging it
#: `MESH_DERIVED` would be a different lie (the geometry is not mesh-derived at all). So it
#: declares what it actually is, and :func:`honesty_problem` VERIFIES the claim by
#: requiring the master to be registered -- the tag cannot be used to launder a facet soup
#: that has no master behind it.
ANALYTIC_MASTER = "analytic-master"

#: The user-string key holding a proxy's master id.
MASTER_KEY = "Narwhal3D:KernelShape"

#: The user-string key holding a primitive's construction RECIPE (JSON).
RECIPE_KEY = "Narwhal3D:Recipe"

#: id -> master shape. Process-local and deliberately NOT persisted: a reloaded document
#: rebuilds masters from recipes, or says it cannot. Keeping a stale id alive across a
#: reload would be the quiet failure this whole module exists to prevent.
#:
#: IDs are UUIDs, not a counter. Sequential per-process IDs such as ``m1`` and ``m2``
#: restart in every new process. A document holding a
#: fillet (stored key ``m1``) and a boolean was reopened; rehydrating the boolean registered
#: a FRESH ``m1``, so the fillet's STALE key resolved to the boolean's shape. The fillet then
#: reported ``{PLANE:7, CYLINDER:1}`` -- the union's faces -- and `describe()` called it
#: healthy. STEP export would have shipped the wrong part. A stale key must be able to resolve
#: to NOTHING, never to something else, and only a globally unique id guarantees that.
_MASTERS = {}


def record_recipe(geom, recipe):
    """Store how `geom` was CONSTRUCTED, so it can be rebuilt in another kernel.

    The recipe is recorded at the point of creation, where the answer is actually known --
    a cylinder's radius cannot be
    recovered from a trimmed brep afterwards, which is the whole problem.
    """
    import json

    setter = getattr(geom, "SetUserString", None)
    if not callable(setter):
        return False
    try:
        return bool(setter(RECIPE_KEY, json.dumps(recipe, sort_keys=True)))
    except Exception:  # noqa: BLE001
        return False


def recipe_of(geom):
    """The construction recipe of `geom`, or None."""
    import json

    getter = getattr(geom, "GetUserString", None)
    if not callable(getter):
        return None
    try:
        raw = getter(RECIPE_KEY)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, dict) else None


def register_master(proxy, shape):
    """Bind an analytic `shape` to its display `proxy`, and tag the proxy.

    Returns the master id. The proxy is tagged :data:`ANALYTIC_MASTER` here rather than by
    the caller, so a registered master and its declaration cannot drift apart.
    """
    import uuid

    key = f"m-{uuid.uuid4().hex}"
    _MASTERS[key] = shape
    setter = getattr(proxy, "SetUserString", None)
    if callable(setter):
        try:
            setter(MASTER_KEY, key)
            setter(REPRESENTATION_KEY, ANALYTIC_MASTER)
        except Exception:  # noqa: BLE001
            pass
    return key


def master_key_of(geom):
    """The master id stored on `geom`, or None. (The id, not the shape.)"""
    getter = getattr(geom, "GetUserString", None)
    if not callable(getter):
        return None
    try:
        return getter(MASTER_KEY) or None
    except Exception:  # noqa: BLE001
        return None


def master_of(geom):
    """The analytic master behind a display proxy, or None."""
    getter = getattr(geom, "GetUserString", None)
    if not callable(getter):
        return None
    try:
        key = getter(MASTER_KEY)
    except Exception:  # noqa: BLE001
        return None
    if not key:
        return None
    return _MASTERS.get(key)


def drop_master(geom):
    """Detach `geom` from its master and DEMOTE it to :data:`MESH_DERIVED`.

    For when a master cannot follow its proxy -- the kernel went missing, or a transform
    failed. The proxy then really is nothing but triangles, so it must say so: leaving the
    `ANALYTIC_MASTER` tag on would be a solid claiming surfaces it no longer has, and
    :func:`honesty_problem` would (correctly) start failing the suite.

    The document object may instead be the exact polysurface written by openNURBS. That
    object is analytic on its own, so it is tagged `ANALYTIC`, not demoted -- the tag
    follows what the geometry measures as (`looks_tessellated`), as everywhere else.
    """
    setter = getattr(geom, "SetUserString", None)
    if not callable(setter):
        return False
    key = master_key_of(geom)
    if key is not None:
        _MASTERS.pop(key, None)
    try:
        setter(MASTER_KEY, "")
        setter(REPRESENTATION_KEY, MESH_DERIVED if looks_tessellated(geom) else ANALYTIC)
        return True
    except Exception:  # noqa: BLE001
        return False


def forget_masters():
    """Drop every registered master (a new/closed document). Tests use it too."""
    _MASTERS.clear()


#: The user-string key on a master-backed document object saying which FORM the .3dm
#: will hold -- "exact" (openNURBS wrote the real polysurface) or "triangulated" (the helper
#: was unavailable and the display proxy stands in). Set by `bridge._proxy_from_occt`.
DOCUMENT_FORM_KEY = "Narwhal3D:DocumentForm"


def faceted_in_file(geom):
    """True when `geom` is a master-backed solid whose DOCUMENT object -- what the .3dm
    stores and what real Rhino opens -- is triangles, although its geometry is analytic.

    This is the state produced when the openNURBS helper is unavailable. The master still
    measures, booleans and exports STEP exactly, but saving writes facets. It must never be
    silent, so `describe()` and `RhinoModel.save` both read this.

    The marker decides; a document object with no marker falls back to what it measures as
    (`looks_tessellated`).
    """
    if representation_of(geom) != ANALYTIC_MASTER:
        return False
    getter = getattr(geom, "GetUserString", None)
    marker = None
    if callable(getter):
        try:
            marker = getter(DOCUMENT_FORM_KEY) or None
        except Exception:  # noqa: BLE001
            marker = None
    if marker == "triangulated":
        return True
    if marker == "exact":
        return False
    return looks_tessellated(geom)


def honesty_problem(geom):
    """What is wrong with `geom`'s provenance, or None if nothing is.

    THE never-again check. Two failure modes, both of which actually happened:

    * **untagged** -- a solid with no declared provenance. A faceted solid without a
      mesh-derived tag would otherwise be treated as analytic by every layer above it.
    * **claims analytic, measures tessellated** -- a producer that tags `ANALYTIC`
      while handing back a facet soup. A tag alone could not catch this, which is why
      there are two layers.

    Non-solids are not required to carry a tag: a curve or a single surface has no
    "was this tessellated?" question to answer, and demanding a tag everywhere would
    train people to add it without thinking.
    """
    if not getattr(geom, "IsSolid", False):
        return None
    declared = representation_of(geom)
    tessellated = looks_tessellated(geom)
    if declared == UNKNOWN:
        total, planar = face_summary(geom)
        return (f"solid has no {REPRESENTATION_KEY} tag "
                f"({total} faces, {planar} planar) -- every producer of a solid must "
                f"declare {ANALYTIC!r} or {MESH_DERIVED!r}")
    if declared == ANALYTIC and tessellated:
        total, planar = face_summary(geom)
        return (f"solid is tagged {ANALYTIC!r} but measures as a tessellation "
                f"({total} faces, all {planar} planar) -- this is the error that must "
                f"never ship: a faceted solid presented as analytic")
    if declared == ANALYTIC_MASTER and master_of(geom) is None:
        total, planar = face_summary(geom)
        # The new tag must not become a loophole. A proxy may only claim an analytic
        # master if one is actually REGISTERED; otherwise the claim is unverifiable and
        # the thing in front of us is just a facet soup with a reassuring label.
        return (f"solid is tagged {ANALYTIC_MASTER!r} but NO master is registered for it "
                f"({total} faces, {planar} planar) -- a proxy may only claim an analytic "
                f"master when one actually exists, or the tag becomes a way to launder "
                f"facets")
    return None


def describe(geom):
    """A short phrase for a command's message or the UI. Never silently reassuring.

    Used so the user is told, in the place they are looking, which kind of geometry they
    just made -- rather than finding out at the machine shop.
    """
    declared = representation_of(geom)
    if declared == ANALYTIC:
        return "analytic (exportable as surfaces)"
    if declared == MESH_DERIVED:
        return "mesh-derived (faceted; curved faces are approximated)"
    if declared == ANALYTIC_MASTER:
        if master_of(geom) is None:
            return "BROKEN: claims an analytic master, but none is registered"
        if faceted_in_file(geom):
            return ("analytic, but FACETED in the file (no openNURBS brep writer: a saved "
                    ".3dm holds triangles; STEP export is still exact)")
        return "analytic (exportable as surfaces; drawn via a display mesh)"
    return "representation unknown"


def require_analytic(geom, what="this operation"):
    """Raise :class:`KernelError` unless `geom` is declared and measures analytic.

    For paths where an approximation is not acceptable -- above all **STEP export**,
    which is the file a shop machines from. Refusing loudly is the entire point: a STEP
    full of facets that *looks* like a solid is the worst possible outcome, because
    nothing downstream will question it.
    """
    problem = honesty_problem(geom)
    if problem is not None:
        raise KernelError(f"{what} needs analytic geometry: {problem}")
    # A verified proxy IS analytic geometry -- its master is the thing that gets exported,
    # so refusing it here would block the very STEP export this function exists to protect.
    if representation_of(geom) == ANALYTIC_MASTER and master_of(geom) is not None:
        return
    if representation_of(geom) != ANALYTIC:
        raise KernelError(
            f"{what} needs analytic geometry, but this is {describe(geom)}. "
            "Use an exact solid producer or export a supported analytic format.")
    return True


def audit(geoms):
    """``[(index, problem)]`` for every geometry with a provenance problem.

    For a document-wide gate: "no solid in this document misrepresents itself".
    """
    out = []
    for i, geom in enumerate(geoms):
        problem = honesty_problem(geom)
        if problem is not None:
            out.append((i, problem))
    return out


def rules():
    """The facts this module asserts, so a test cannot drift from the docstring."""
    return {
        "key": REPRESENTATION_KEY,
        "representations": REPRESENTATIONS,
        "threshold": TESSELLATION_FACE_THRESHOLD,
        "planar_fraction": TESSELLATION_PLANAR_FRACTION,
        "tag_survives_3dm": True,   # verified by a .3dm write/read cycle
    }

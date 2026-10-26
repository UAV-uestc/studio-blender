"""BIM helpers for the Bonsai mode: whole buildings as real IFC elements in few calls.

Built on Bonsai's own generators (the same calls its wall/slab tools make), so walls,
slabs and openings stay parametric and editable in Bonsai's UI afterwards. Lengths in
metres; points are (x, y) on the storey.
"""

import math
import os

import bpy
from mathutils import Matrix, Vector


def _bonsai():
    """Bonsai's modules, enabling the extension if needed (first load takes ~20 s)."""
    try:
        import bonsai.tool  # noqa: F401
    except ImportError:
        pass
    if not any(k.endswith("bonsai") for k in bpy.context.preferences.addons.keys()):
        import addon_utils
        name = next((m.__name__ for m in addon_utils.modules() if m.__name__.endswith(".bonsai")), None)
        if not name:
            raise RuntimeError("Bonsai isn't installed: Edit > Preferences > Get Extensions, "
                               "search Bonsai, Install.")
        addon_utils.enable(name, default_set=True)
    import bonsai.core.geometry
    import bonsai.core.root
    import bonsai.tool as tool
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.util.element
    import ifcopenshell.util.representation
    return tool, bonsai.core, ifcopenshell


def _f():
    tool, _, _ = _bonsai()
    f = tool.Ifc.get()
    if not f:
        raise RuntimeError("No IFC project yet: call h.bim_project() first.")
    return f


def _ctx(f, kind="Body"):
    import ifcopenshell.util.representation as rep
    if kind == "Body":
        return rep.get_context(f, "Model", "Body", "MODEL_VIEW")
    if kind == "Axis":
        return rep.get_context(f, "Plan", "Axis", "GRAPH_VIEW")
    if kind == "Footprint":
        return rep.get_context(f, "Plan", "FootPrint", "SKETCH_VIEW")
    return None


def bim_project(name="Project", storeys=(("Ground Floor", 0.0),), site="Site", building="Building"):
    """Create the IFC project (or reuse the open one) with these storeys (name, elevation m).
    Returns {storey name: entity}."""
    tool, core, ifcopenshell = _bonsai()
    import ifcopenshell.api as api
    u = bpy.context.scene.unit_settings
    if u.system != "METRIC" or abs(u.scale_length - 1.0) > 1e-9:
        # A print job may have left the scene in millimetres: Bonsai works in metres.
        u.system, u.scale_length, u.length_unit = "METRIC", 1.0, "METERS"
    if not tool.Ifc.get():
        bpy.ops.bim.create_project()
    f = tool.Ifc.get()
    f.by_type("IfcProject")[0].Name = name
    for e, n in ((f.by_type("IfcSite"), site), (f.by_type("IfcBuilding"), building)):
        if e and e[0].Name in ("My Site", "My Building"):
            e[0].Name = n
    have = {s.Name: s for s in f.by_type("IfcBuildingStorey")}
    if "My Storey" in have and storeys and storeys[0][0] not in have:
        have[storeys[0][0]] = have.pop("My Storey")
        have[storeys[0][0]].Name = storeys[0][0]
    bld = f.by_type("IfcBuilding")[0]
    for sname, elev in storeys:
        st = have.get(sname)
        if not st:
            st = api.root.create_entity(f, ifc_class="IfcBuildingStorey", name=sname)
            api.aggregate.assign_object(f, products=[st], relating_object=bld)
            api.geometry.edit_object_placement(f, product=st)
            obj = bpy.data.objects.new(f"IfcBuildingStorey/{sname}", None)
            tool.Ifc.link(st, obj)
            bld_obj = tool.Ifc.get_object(bld)
            for c in (bld_obj.users_collection if bld_obj else [bpy.context.scene.collection]):
                c.objects.link(obj)
            have[sname] = st
        st.Elevation = elev
        obj = tool.Ifc.get_object(st)
        if obj:
            obj.location.z = elev
            core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
    for e in [f.by_type("IfcProject")[0], *f.by_type("IfcSite"), *f.by_type("IfcBuilding"),
              *have.values()]:
        obj = tool.Ifc.get_object(e)
        if obj:
            obj.name = f"{e.is_a()}/{e.Name}"
    bpy.context.view_layer.update()
    return have


def _us(f):
    """Project length unit in metres (IFC values are stored in it)."""
    import ifcopenshell.util.unit as uu
    return uu.calculate_unit_scale(f)


def _storey(storey):
    f = _f()
    if hasattr(storey, "is_a"):
        return storey
    for s in f.by_type("IfcBuildingStorey"):
        if storey is None or s.Name == storey:
            return s
    raise ValueError(f"No storey {storey!r}: " + ", ".join(s.Name for s in f.by_type("IfcBuildingStorey")))


def bim_material(name, category="concrete", color=None):
    """An IfcMaterial (reused by name). category: concrete, steel, wood, brick, glass, ..."""
    tool, _, _ = _bonsai()
    import ifcopenshell.api as api
    f = _f()
    for m in f.by_type("IfcMaterial"):
        if m.Name == name:
            return m
    m = api.material.add_material(f, name=name, category=category)
    if color:
        style = api.style.add_style(f, name=name)
        api.style.add_surface_style(f, style=style, ifc_class="IfcSurfaceStyleShading", attributes={
            "SurfaceColour": {"Name": None, "Red": color[0], "Green": color[1], "Blue": color[2]},
            "Transparency": color[3] if len(color) > 3 else 0.0})
        api.style.assign_material_style(f, material=m, style=style, context=_ctx(f))
    return m


def bim_type(name, ifc_class="IfcWallType", layers=(("Concrete", 0.2),), profile=None):
    """A type with a material layer set (walls, slabs, roofs, coverings): layers = (material,
    thickness m) outside to inside / top to bottom. For columns/beams pass profile=(w, d) or
    ("CIRCLE", r) with ifc_class IfcColumnType / IfcBeamType and layers=((material, 0),).
    Reused by name."""
    tool, _, _ = _bonsai()
    import ifcopenshell.api as api
    f = _f()
    for t in f.by_type(ifc_class):
        if t.Name == name:
            return t
    t = api.root.create_entity(f, ifc_class=ifc_class, name=name)
    if profile:
        mat = bim_material(layers[0][0])
        pset = api.material.add_material_set(f, name=name, set_type="IfcMaterialProfileSet")
        if profile[0] == "CIRCLE":
            prof = f.create_entity("IfcCircleProfileDef", ProfileName=name, ProfileType="AREA",
                                   Radius=profile[1] / _us(f))
        else:
            prof = f.create_entity("IfcRectangleProfileDef", ProfileName=name, ProfileType="AREA",
                                   XDim=profile[0] / _us(f), YDim=profile[1] / _us(f))
        api.material.add_profile(f, profile_set=pset, material=mat, profile=prof)
        api.material.assign_material(f, products=[t], type="IfcMaterialProfileSet", material=pset)
        return t
    lset = api.material.add_material_set(f, name=name, set_type="IfcMaterialLayerSet")
    for mname, thick in layers:
        layer = api.material.add_layer(f, layer_set=lset, material=bim_material(mname))
        api.material.edit_layer(f, layer=layer, attributes={"LayerThickness": thick / _us(f),
                                                            "Name": mname})
    api.material.assign_material(f, products=[t], type="IfcMaterialLayerSet", material=lset)
    return t


def _wall_type(wall_type):
    if hasattr(wall_type, "is_a"):
        return wall_type
    if isinstance(wall_type, (int, float)):
        return bim_type(f"Wall {round(wall_type * 1000)}", "IfcWallType", (("Concrete", wall_type),))
    return bim_type(wall_type or "Wall 200", "IfcWallType")


def _new(ifc_class, name, storey, matrix, rtype=None, predefined_type=None):
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    f = _f()
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj.matrix_world = matrix
    bpy.context.view_layer.update()
    el = core.root.assign_class(tool.Ifc, tool.Collector, tool.Root, obj=obj, ifc_class=ifc_class,
                                predefined_type=predefined_type, should_add_representation=False)
    st = _storey(storey)
    if el.is_a("IfcSpatialElement"):
        api.aggregate.assign_object(f, products=[el], relating_object=st)
    else:
        api.spatial.assign_container(f, products=[el], relating_structure=st)
    tool.Collector.assign(obj)
    if rtype is not None:
        api.type.assign_type(f, related_objects=[el], relating_type=rtype)
    core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
    return obj, el


def _show(obj, el, rep):
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    api.geometry.assign_representation(_f(), product=el, representation=rep)
    core.geometry.switch_representation(tool.Ifc, tool.Geometry, obj=obj, representation=rep)
    obj.data.update()
    bpy.context.view_layer.update()


def bim_walls(points, storey=None, height=3.0, wall_type=0.2, closed=True, base=0.0, name="Wall"):
    """Layered walls along a polyline of (x, y) points on a storey (wall_type: thickness m, a
    type name or a type from h.bim_type). Corners are joined. The layers sit on the left of the
    direction of travel: list the points counter-clockwise for an outer wall's thickness to go
    inward. Returns the wall objects."""
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    f = _f()
    rtype = _wall_type(wall_type)
    st = _storey(storey)
    elev = st.Elevation or 0.0
    lp = tool.Model.get_material_layer_parameters(rtype)
    pts = [Vector((p[0], p[1], 0)) for p in points]
    segs = list(zip(pts, pts[1:] + ([pts[0]] if closed and len(pts) > 2 else [])))
    walls = []
    n0 = len(f.by_type("IfcWall"))
    for i, (a, b) in enumerate(segs):
        d = b - a
        m = Matrix.Rotation(math.atan2(d.y, d.x), 4, "Z")
        m.translation = Vector((a.x, a.y, elev + base))
        obj, el = _new("IfcWall", f"{name} {n0 + i + 1}", st, m, rtype)
        api.geometry.assign_representation(f, product=el, representation=api.geometry.add_axis_representation(
            f, context=_ctx(f, "Axis"), axis=[(0.0, 0.0), (d.length, 0.0)]))
        _show(obj, el, api.geometry.add_wall_representation(
            f, context=_ctx(f), thickness=lp["thickness"], direction_sense=lp["direction_sense"],
            offset=lp["offset"], length=d.length, height=height))
        pset = api.pset.add_pset(f, product=el, name="EPset_Parametric")
        api.pset.edit_pset(f, pset=pset, properties={"Engine": "Bonsai.DumbLayer2"})
        ifc_mat = __import__("ifcopenshell.util.element").util.element.get_material(el)
        if ifc_mat and hasattr(ifc_mat, "LayerSetDirection"):
            ifc_mat.LayerSetDirection = "AXIS2"
        walls.append(obj)
    from bonsai.bim.module.model.wall import DumbWallJoiner
    j = DumbWallJoiner()
    pairs = list(zip(walls, walls[1:])) + ([(walls[-1], walls[0])] if closed and len(walls) > 2 else [])
    for w1, w2 in pairs:
        try:
            j.connect(w1, w2)
        except Exception as e:  # noqa: BLE001
            print(f"join {w1.name}/{w2.name} failed: {e}")
    return walls


def bim_slab(points, storey=None, slab_type=0.25, top=0.0, name="Slab", ifc_class="IfcSlab",
             predefined_type="FLOOR", holes=()):
    """A layered slab from a closed (x, y) outline, its top at storey elevation + top (a floor
    slab hangs below the storey level). slab_type: thickness m, type name or type. holes: closed
    outlines cut through it as real openings (stairwells, shafts)."""
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    f = _f()
    if hasattr(slab_type, "is_a"):
        rtype = slab_type
    elif isinstance(slab_type, (int, float)):
        rtype = bim_type(f"Slab {round(slab_type * 1000)}", "IfcSlabType", (("Concrete", slab_type),))
    else:
        rtype = bim_type(slab_type, "IfcSlabType")
    st = _storey(storey)
    lp = tool.Model.get_material_layer_parameters(rtype)
    # Bonsai extrudes a slab up from its placement: place it one thickness down, so its top
    # is the finished floor level (walls of the storey stand on it).
    m = Matrix.Translation(((st.Elevation or 0.0) + top - lp["thickness"]) * Vector((0, 0, 1)))
    obj, el = _new(ifc_class, f"{name} {len(f.by_type(ifc_class)) + 1}", st, m, rtype,
                   predefined_type)
    _show(obj, el, api.geometry.add_slab_representation(
        f, context=_ctx(f), depth=lp["thickness"], polyline=[tuple(p[:2]) for p in points]))
    pset = api.pset.add_pset(f, product=el, name="EPset_Parametric")
    api.pset.edit_pset(f, pset=pset, properties={"Engine": "Bonsai.DumbLayer3"})
    ifc_mat = __import__("ifcopenshell.util.element").util.element.get_material(el)
    if ifc_mat and hasattr(ifc_mat, "LayerSetDirection"):
        ifc_mat.LayerSetDirection = "AXIS3"
    if holes:
        z = (st.Elevation or 0.0) + top
        for hole in holes:
            bim_opening(obj, hole, z - lp["thickness"] - 0.1, z + 0.1)
    return obj


def bim_opening(element, outline, bottom, top):
    """Cut a void through an element (a stairwell or shaft in a slab, a recess in a wall): an
    IfcOpeningElement extruded from a closed (x, y) outline in world metres, from z bottom to
    top. Returns the opening entity."""
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    import ifcopenshell.util.shape_builder as sb
    f = _f()
    obj = bpy.data.objects[element] if isinstance(element, str) else element
    el = tool.Ifc.get_entity(obj)
    us = _us(f)
    op = api.root.create_entity(f, ifc_class="IfcOpeningElement", name=f"Void in {el.Name}")
    api.geometry.edit_object_placement(f, product=op, matrix=Matrix.Translation((0, 0, bottom)))
    b = sb.ShapeBuilder(f)
    curve = b.polyline([(p[0] / us, p[1] / us) for p in outline], closed=True)
    rep = b.get_representation(_ctx(f), [b.extrude(curve, magnitude=(top - bottom) / us)])
    api.geometry.assign_representation(f, product=op, representation=rep)
    api.feature.add_feature(f, feature=op, element=el)
    ctx = tool.Geometry.get_active_representation_context(obj)
    tool.Geometry.recut_host(obj, tool.Geometry.get_representation_by_context(el, ctx))
    bpy.context.view_layer.update()
    return op


def _in_wall(kind, wall, at, width, height, sill, operation):
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    from bonsai.bim.module.model.opening import FilledOpeningGenerator
    f = _f()
    wobj = bpy.data.objects[wall] if isinstance(wall, str) else wall
    wel = tool.Ifc.get_entity(wobj)
    st = __import__("ifcopenshell.util.element").util.element.get_container(wel)
    lp = tool.Model.get_material_layer_parameters(wel)
    m = wobj.matrix_world.copy()
    m.translation = wobj.matrix_world @ Vector((at, 0, sill))
    ifc_class = "IfcDoor" if kind == "door" else "IfcWindow"
    n = sum(1 for e in f.by_type(ifc_class)) + 1
    obj, el = _new(ifc_class, f"{kind.title()} {n}", st, m)
    if kind == "door":
        el.OperationType = operation or "SINGLE_SWING_LEFT"
        rep = api.geometry.add_door_representation(
            f, context=_ctx(f), overall_height=height, overall_width=width,
            operation_type=el.OperationType,
            lining_properties={"LiningDepth": lp["thickness"]})
    else:
        el.PartitioningType = operation or "SINGLE_PANEL"
        rep = api.geometry.add_window_representation(
            f, context=_ctx(f), overall_height=height, overall_width=width,
            partition_type=el.PartitioningType,
            lining_properties={"LiningDepth": lp["thickness"]})
    el.OverallWidth, el.OverallHeight = width / _us(f), height / _us(f)
    _show(obj, el, rep)
    bpy.context.view_layer.update()
    err = FilledOpeningGenerator().generate(obj, wobj, target=obj.matrix_world.translation.copy(),
                                            preserve_placement=True)
    if err:
        print(f"{obj.name}: {err}")
    return obj


def bim_door(wall, at, width=0.9, height=2.1, operation="SINGLE_SWING_LEFT"):
    """A door in a wall object, `at` metres along it from its start, with a real opening."""
    return _in_wall("door", wall, at, width, height, 0.0, operation)


def bim_window(wall, at, width=1.2, height=1.2, sill=0.9, partition="SINGLE_PANEL"):
    """A window in a wall object, `at` metres along it, sill height above the wall base."""
    return _in_wall("window", wall, at, width, height, sill, partition)


def bim_member(start, end, member_type, storey=None, ifc_class=None, name=None, rotate=0.0):
    """A column, beam or brace from world point start to end (x, y, z) with a profile type
    from h.bim_type(..., profile=...): ifc_class defaults from the type (IfcColumn, IfcBeam,
    IfcMember). rotate turns the profile about the member's axis (degrees)."""
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    import ifcopenshell.util.element as ue
    f = _f()
    rtype = member_type if hasattr(member_type, "is_a") else next(
        t for t in f.by_type("IfcElementType") if t.Name == member_type)
    ifc_class = ifc_class or rtype.is_a()[:-4]
    a, b = Vector(start), Vector(end)
    d = b - a
    z = d.normalized()
    x = Vector((1, 0, 0)) if abs(z.x) < 0.9 else Vector((0, 1, 0))
    x = (x - z * x.dot(z)).normalized()
    m = Matrix((x, z.cross(x), z)).transposed().to_4x4() @ Matrix.Rotation(math.radians(rotate), 4, "Z")
    m.translation = a
    n = sum(1 for _ in f.by_type(ifc_class)) + 1
    obj, el = _new(ifc_class, name or f"{ifc_class[3:]} {n}", _storey(storey), m, rtype)
    prof = ue.get_material(rtype).MaterialProfiles[0].Profile
    _show(obj, el, api.geometry.add_profile_representation(f, context=_ctx(f), profile=prof,
                                                           depth=d.length))
    return obj


def bim_space(points, storey=None, name="Room", height=2.7, long_name=None):
    """An IfcSpace (room) from a closed (x, y) outline: gives the floor area in quantities and
    room tags in plans."""
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    import ifcopenshell.util.shape_builder as sb
    f = _f()
    st = _storey(storey)
    m = Matrix.Translation(Vector((0, 0, st.Elevation or 0.0)))
    obj, el = _new("IfcSpace", name, st, m)
    if long_name:
        el.LongName = long_name
    b = sb.ShapeBuilder(f)
    us = _us(f)
    curve = b.polyline([(p[0] / us, p[1] / us) for p in points], closed=True)
    _show(obj, el, b.get_representation(_ctx(f), [b.extrude(curve, magnitude=height / us)]))
    return obj


def bim_element(obj, ifc_class, storey=None, predefined_type=None, name=None):
    """Make a modelled mesh an IFC element (furniture, stairs, railings, custom parts):
    its mesh becomes the element's body. Returns the element."""
    tool, core, _ = _bonsai()
    import ifcopenshell.api as api
    obj = bpy.data.objects[obj] if isinstance(obj, str) else obj
    if name:
        obj.name = name
    el = core.root.assign_class(tool.Ifc, tool.Collector, tool.Root, obj=obj, ifc_class=ifc_class,
                                predefined_type=predefined_type)
    api.spatial.assign_container(_f(), products=[el], relating_structure=_storey(storey))
    tool.Collector.assign(obj)
    return el


def bim_qto():
    """Compute base quantities (lengths, areas, volumes) for every element and space."""
    tool, _, _ = _bonsai()
    import ifc5d.qto
    f = _f()
    els = set(f.by_type("IfcElement")) | set(f.by_type("IfcSpace"))
    rules = ifc5d.qto.rules["IFC4QtoBaseQuantities"]
    res = ifc5d.qto.quantify(f, els, rules)
    ifc5d.qto.edit_qtos(f, res, rules=rules)
    return len(res)


def _q(el):
    import ifcopenshell.util.element as ue
    out = {}
    for name, props in ue.get_psets(el, qtos_only=True).items():
        out.update({k: v for k, v in props.items() if isinstance(v, (int, float)) and k != "id"})
    return out


def bim_report(qto=True):
    """Storeys, element counts, quantities and problems: the check before replying."""
    tool, _, _ = _bonsai()
    import ifcopenshell.util.element as ue
    f = _f()
    if qto:
        bim_qto()
    lines = [f"{f.schema} project {f.by_type('IfcProject')[0].Name!r}"]
    problems = []
    for st in sorted(f.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0):
        els = ue.get_decomposition(st)
        counts = {}
        for e in els:
            counts[e.is_a()] = counts.get(e.is_a(), 0) + 1
        lines.append(f"{st.Name} @ {st.Elevation or 0:.2f} m: " +
                     (", ".join(f"{n} {c[3:]}" for c, n in sorted(counts.items())) or "empty"))
        sums = {}
        for e in els:
            q = _q(e)
            k = e.is_a()[3:]
            for key in ("Length", "NetSideArea", "NetArea", "NetFloorArea", "NetVolume"):
                if key in q:
                    sums[(k, key)] = sums.get((k, key), 0) + q[key]
        if sums:
            lines.append("  " + ", ".join(f"{k} {key} {v:.2f}" for (k, key), v in sorted(sums.items())))
    for e in f.by_type("IfcElement"):
        if e.is_a("IfcOpeningElement") or e.is_a("IfcFeatureElement"):
            continue
        if not ue.get_container(e) and not ue.get_aggregate(e):
            problems.append(f"{e.Name}: not in a storey")
        if not e.Representation:
            problems.append(f"{e.Name}: no geometry")
    loose = [o.name for o in bpy.context.scene.objects
             if o.type == "MESH" and not tool.Ifc.get_entity(o) and not o.name.startswith(("IfcOpening",))
             and "Ifc" not in o.name]
    if loose:
        problems.append(f"not IFC elements (not exported): {', '.join(loose[:10])}")
    lines += ["problems: " + ("; ".join(problems[:15]) if problems else "none")]
    return "\n".join(lines)


def bim_save(path=None):
    """Write the .ifc (default: next to the .blend, or the path the project was loaded from)."""
    tool, _, _ = _bonsai()
    f = _f()
    props = tool.Blender.get_bim_props()
    path = path or props.ifc_file or os.path.splitext(bpy.data.filepath or os.path.join(
        bpy.app.tempdir, "project"))[0] + ".ifc"
    bpy.ops.bim.save_project(filepath=path, should_save_as=True)
    return path


def _bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not pts:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    return (Vector([min(p[i] for p in pts) for i in range(3)]),
            Vector([max(p[i] for p in pts) for i in range(3)]))


def _drawing_objs(tool, f, storey=None):
    import ifcopenshell.util.element as ue
    els = ue.get_decomposition(storey) if storey else f.by_type("IfcElement")
    return [o for o in (tool.Ifc.get_object(e) for e in els
                        if not e.is_a("IfcOpeningElement") and not e.is_a("IfcSpace"))
            if o and o.type == "MESH"]


def _annotation(tool, drawing, kind, points=None, text=None, at=None):
    """A DIMENSION chain through world points, or a TEXT at a point, in a drawing."""
    import bonsai.core.drawing as cd
    obj = cd.add_annotation(tool.Ifc, tool.Collector, tool.Drawing, drawing=drawing,
                            object_type=kind, relating_type=None)
    if kind == "TEXT":
        obj.matrix_world.translation = Vector(at)
        bpy.context.view_layer.update()
        el = tool.Ifc.get_entity(obj)
        import bonsai.core.geometry as cg
        cg.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
        for item in el.Representation.Representations[0].Items:
            if item.is_a("IfcTextLiteral"):
                item.Literal = text
        return obj
    cu = obj.data
    cu.splines.clear()
    inv = obj.matrix_world.inverted()
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for p, q in zip(sp.points, points):
        p.co = (*(inv @ Vector(q)), 1)
    bpy.ops.bim.update_representation(obj=obj.name)
    return obj


def bim_dimension(points, drawing=None):
    """A dimension chain through world points (x, y, z) in the active (or named) drawing:
    each segment gets its length written on it."""
    tool, _, _ = _bonsai()
    f = _f()
    dr = _find_drawing(f, drawing)
    return _annotation(tool, dr, "DIMENSION", points=points)


def _find_drawing(f, name=None):
    import bonsai.tool as tool
    drs = [e for e in f.by_type("IfcAnnotation") if e.ObjectType == "DRAWING"]
    if name:
        drs = [d for d in drs if d.Name == name]
    else:
        cam = bpy.context.scene.camera
        drs = [d for d in drs if tool.Ifc.get_object(d) == cam] or drs[-1:]
    if not drs:
        raise ValueError("No such drawing: make one with h.bim_drawing().")
    return drs[0]


def _side_chains(tool, f, objs, lo, hi, z, off):
    """Dimension chains along the four sides of the plan: openings in walls on that side,
    then the overall length further out."""
    import ifcopenshell.util.element as ue
    chains = []
    for axis, side, sign in ((0, lo[1], -1), (0, hi[1], 1), (1, lo[0], -1), (1, hi[0], 1)):
        other = 1 - axis
        cuts = {lo[axis], hi[axis]}
        for o in objs:
            el = tool.Ifc.get_entity(o)
            if not el or not el.is_a("IfcWall"):
                continue
            a, b = _bbox([o])
            if abs((a[other] + b[other]) / 2 - side) > 0.6 or (b[axis] - a[axis]) < (b[other] - a[other]):
                continue
            for rel in getattr(el, "HasOpenings", []) or []:
                for fill in rel.RelatedOpeningElement.HasFillings or []:
                    fe = fill.RelatedBuildingElement
                    fo = tool.Ifc.get_object(fe)
                    if fo:
                        # The nominal opening (placement + overall width), not the frame.
                        w = (getattr(fe, "OverallWidth", None) or 0) * _us(f)
                        if w:
                            ends = (fo.matrix_world.translation, fo.matrix_world @ Vector((w, 0, 0)))
                            cuts |= {round(e[axis], 3) for e in ends}
                        else:
                            fa, fb = _bbox([fo])
                            cuts |= {round(fa[axis], 3), round(fb[axis], 3)}
        def pt(t, d):
            p = [0, 0, z]
            p[axis], p[other] = t, side + sign * d
            return tuple(p)
        cuts = sorted(cuts)
        if len(cuts) > 2:
            chains.append([pt(t, off) for t in cuts])
        chains.append([pt(lo[axis], off * 2.2), pt(hi[axis], off * 2.2)])
    return chains


def bim_drawing(view="PLAN", storey=None, scale=None, dims=True, labels=True, at=None, name=None):
    """A drawing as SVG via Bonsai: view PLAN (of a storey), ELEVATION_N/S/E/W or SECTION_X/Y
    (cut through `at`, default the middle). dims=True adds dimension chains (openings + overall
    on each side of a plan; storey levels on elevations/sections), labels=True room names and
    areas on plans. scale like 100 for 1:100 (default by size). Saves the IFC first. Returns
    the .svg path; the drawing stays in Bonsai's Drawings list for the user to edit."""
    tool, core, _ = _bonsai()
    import bonsai.core.drawing as cd
    import ifcopenshell.util.element as ue
    f = _f()
    if not tool.Ifc.get_path():
        bim_save()
    view = view.upper()
    st = _storey(storey) if view == "PLAN" else None
    objs = _drawing_objs(tool, f, st)
    walls = [o for o in objs if (e := tool.Ifc.get_entity(o)) and e.is_a("IfcWall")]
    lo, hi = _bbox(walls or objs)
    c = (lo + hi) / 2
    size = hi - lo
    cur = bpy.context.scene.cursor
    pad = 4.0
    if view == "PLAN":
        target, hint = "PLAN_VIEW", st.id()
        cur.location = (c.x, c.y, 0)
        w, h = size.x + 2 * pad, size.y + 2 * pad
    else:
        kind, _, d = view.partition("_")
        d = d[:1]
        target = "ELEVATION_VIEW" if kind == "ELEVATION" else "SECTION_VIEW"
        if target == "ELEVATION_VIEW":
            hint = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}[d]
            loc = {"N": (c.x, hi.y + 3, c.z), "S": (c.x, lo.y - 3, c.z),
                   "E": (hi.x + 3, c.y, c.z), "W": (lo.x - 3, c.y, c.z)}[d]
        else:
            hint = "NORTH" if d == "Y" else "EAST"
            # Default cut a little off the middle, where a partition often runs.
            loc = (c.x, at if at is not None else lo.y + size.y * 0.4, c.z) if d == "Y" else \
                (at if at is not None else lo.x + size.x * 0.4, c.y, c.z)
        cur.location = loc
        horiz = size.x if hint in ("NORTH", "SOUTH") else size.y
        w, h = horiz + 2 * pad, size.z + 2 * pad
    cd.add_drawing(tool.Ifc, tool.Collector, tool.Drawing, target_view=target, location_hint=hint)
    dr = [e for e in f.by_type("IfcAnnotation") if e.ObjectType == "DRAWING"][-1]
    cam = tool.Ifc.get_object(dr)
    if name:
        cam.name = dr.Name = name
    props = tool.Drawing.get_camera_props(cam.data)
    scale = scale or next(s for s in (20, 50, 100, 200, 500, 1000) if max(w, h) * 1000 / s <= 400)
    props.diagram_scale = f"1:{scale}|1/{scale}"
    props.width, props.height = w, h
    if target != "PLAN_VIEW":
        cam.data.clip_end = (size.y if hint in ("NORTH", "SOUTH") else size.x) + 6
    bpy.context.view_layer.update()
    core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=cam)
    bpy.ops.bim.activate_drawing(drawing=dr.id())
    off = scale / 100.0
    if dims and target == "PLAN_VIEW":
        z = (st.Elevation or 0.0)
        for ch in _side_chains(tool, f, walls or objs, lo, hi, z, off):
            _annotation(tool, dr, "DIMENSION", points=ch)
    elif dims:
        levels = sorted({round(s.Elevation or 0.0, 3) for s in f.by_type("IfcBuildingStorey")} |
                        {round(lo.z, 3), round(hi.z, 3)})
        m = cam.matrix_world
        left = m @ Vector((-(w / 2) + pad * 0.5, 0, -3))
        pts = []
        for zv in levels:
            p = left.copy()
            p.z = zv
            pts.append(tuple(p))
        _annotation(tool, dr, "DIMENSION", points=pts)
    if labels and target == "PLAN_VIEW":
        for sp in [e for e in ue.get_decomposition(st) if e.is_a("IfcSpace")]:
            so = tool.Ifc.get_object(sp)
            if not so:
                continue
            a, b = _bbox([so])
            q = _q(sp)
            area = q.get("NetFloorArea") or q.get("GrossFloorArea")
            txt = sp.LongName or sp.Name
            if area:
                txt += f"\n{area:.1f} m²"
            _annotation(tool, dr, "TEXT", text=txt, at=((a.x + b.x) / 2, (a.y + b.y) / 2, st.Elevation or 0.0))
    bpy.ops.bim.create_drawing()
    path = tool.Drawing.get_document_uri(dr) if hasattr(tool.Drawing, "get_document_uri") else None
    if not path or not os.path.exists(str(path)):
        base = os.path.dirname(tool.Ifc.get_path())
        path = os.path.join(base, "drawings", dr.Name + ".svg")
    return path

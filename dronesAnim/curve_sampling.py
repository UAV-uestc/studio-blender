"""Generate editable meshes with fixed counts and uniform arc-length sampling."""

from bisect import bisect_right
from math import floor, isfinite

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator



def sampling_mesh(scene, source, spline_index):
    """Return a temporary object sampling one source spline with Geometry Nodes.

    Link an empty base mesh to scene and reference source through Object Info.
    Select the zero-based spline_index, then convert its evaluated curve points
    to vertices. Read the modifier result with sampled_positions(); the base
    mesh stays empty. The caller must release the object with remove_helper().
    """
    mesh = bpy.data.meshes.new(f"{source.name} Samples")
    helper = bpy.data.objects.new(f"{source.name} Samples {spline_index + 1}", mesh)
    scene.collection.objects.link(helper)
    helper.hide_render = True
    helper.hide_select = True
    tree = bpy.data.node_groups.new(helper.name, "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    group_out = nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "RELATIVE"
    info.inputs["Object"].default_value = source
    index = nodes.new("GeometryNodeInputIndex")
    compare = nodes.new("FunctionNodeCompare")
    compare.data_type = "INT"
    compare.operation = "EQUAL"
    compare.inputs["B"].default_value = spline_index
    separate = nodes.new("GeometryNodeSeparateGeometry")
    separate.domain = "CURVE"
    points = nodes.new("GeometryNodeCurveToPoints")
    points.mode = "EVALUATED"
    vertices = nodes.new("GeometryNodePointsToVertices")
    links.new(info.outputs["Geometry"], separate.inputs["Geometry"])
    links.new(index.outputs["Index"], compare.inputs["A"])
    links.new(compare.outputs["Result"], separate.inputs["Selection"])
    links.new(separate.outputs["Selection"], points.inputs["Curve"])
    links.new(points.outputs["Points"], vertices.inputs["Points"])
    links.new(vertices.outputs["Mesh"], group_out.inputs["Geometry"])
    modifier = helper.modifiers.new("Curve Samples", "NODES")
    modifier.node_group = tree
    return helper


def sampled_positions(helper, graph):
    """Return the helper's evaluated vertices as world-space Vector copies.

    Evaluate modifiers using the dependency graph, then transform each local
    vertex coordinate with matrix_world. Always release the temporary to_mesh()
    result; the returned coordinates remain valid after that cleanup.
    """
    evaluated = helper.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def remove_helper(helper):
    """Delete the temporary helper and its unused mesh and node groups.

    Keep data blocks that still have other users. The helper reference is no
    longer valid after this call; the referenced source curve is untouched.
    """
    mesh = helper.data
    trees = [modifier.node_group for modifier in helper.modifiers if modifier.type == "NODES"]
    bpy.data.objects.remove(helper, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    for tree in trees:
        if tree is not None and tree.users == 0:
            bpy.data.node_groups.remove(tree)


def allocate_counts(lengths, cyclic, total):
    """Return per-spline vertex counts proportional to world-space lengths.

    lengths and cyclic are matching lists of positive lengths and closed flags.
    Reserve at least two vertices per open spline and three per closed spline,
    then redistribute the budget and assign rounding leftovers by remainder.
    Counts sum to total; raise ValueError if total cannot cover the minimums.
    """
    minimums = [3 if closed else 2 for closed in cyclic]
    if total < sum(minimums):
        raise ValueError(f"这些样条至少需要 {sum(minimums)} 个顶点（开放至少 2，闭合至少 3）")
    # Constrained proportional allocation, redistributing short-spline minimums.
    counts = [0] * len(lengths)
    remaining = list(range(len(lengths)))
    budget = total
    while remaining:
        length_sum = sum(lengths[i] for i in remaining)
        quotas = {i: budget * lengths[i] / length_sum for i in remaining}
        constrained = [i for i in remaining if quotas[i] < minimums[i]]
        if not constrained:
            for i in remaining:
                counts[i] = floor(quotas[i])
            residual = budget - sum(counts[i] for i in remaining)
            ranked = sorted(remaining, key=lambda i: (-(quotas[i] - counts[i]), i))
            for i in ranked[:residual]:
                counts[i] += 1
            break
        for i in constrained:
            counts[i] = minimums[i]
            budget -= counts[i]
            remaining.remove(i)
    return counts


def uniform_samples(positions, count, closed):
    """Return count points at equal arc-length steps along a sampled polyline.

    positions contains ordered coordinates with at least two distinct points.
    Remove consecutive duplicates, accumulate segment lengths, and interpolate
    each target distance within its segment. Open splines include both ends;
    closed splines include the closing segment without repeating the first point.
    Spacing follows the evaluated polyline, not straight-line point distances.
    """
    points = [positions[0]]
    for point in positions[1:]:
        if (point - points[-1]).length > 1e-9:
            points.append(point)
    if closed and (points[-1] - points[0]).length > 1e-9:
        points.append(points[0])
    distances = [0.0]
    for first, second in zip(points, points[1:]):
        distances.append(distances[-1] + (second - first).length)
    length = distances[-1]
    segments = count if closed else count - 1
    result = []
    for sample in range(count):
        distance = length * sample / segments
        index = min(max(0, bisect_right(distances, distance) - 1), len(points) - 2)
        fraction = (distance - distances[index]) / (distances[index + 1] - distances[index])
        result.append(points[index].lerp(points[index + 1], fraction))
    return result


def spacing_count(length, spacing, closed):
    """Return a vertex count for positive spline length and target arc spacing.

    Round the segment count down so actual spacing can exceed the target. Open
    splines include both endpoints; closed splines have at least three vertices.
    Minimum counts can make spacing smaller on short splines. Snap near-integer
    ratios within 1e-6 to avoid losing a segment to evaluation roundoff.
    """
    ratio = length / spacing
    # Avoid dropping a whole segment due to curve evaluation roundoff.
    nearest = round(ratio)
    if abs(ratio - nearest) <= 1e-6:
        ratio = nearest
    segments = floor(ratio)
    return max(3, segments) if closed else max(1, segments) + 1


class DRONESANIM_OT_curves_to_mesh(Operator):
    bl_idname = "drones_anim.curves_to_mesh"
    bl_label = "按总数量生成 Mesh"
    bl_description = "沿所选曲线均分顶点，按总数量或间距生成可编辑网格"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        items=(("COUNT", "总数量", ""), ("DISTANCE", "间距", "")),
        default="COUNT", options={"HIDDEN", "SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        """Allow conversion in Object Mode when at least one curve is selected."""
        return context.mode == "OBJECT" and any(obj.type == "CURVE" for obj in context.selected_objects)

    def execute(self, context):
        """Sample selected curves and create one static, editable edge mesh.

        Allocate vertices by total count or target spacing, preserving separate
        spline chains and loops. Clean up sampling helpers on success or failure;
        remove partial output on error. Select the finished mesh without linking
        it to the source curves or creating drones.
        """
        sources = sorted(
            (obj for obj in context.selected_objects if obj.type == "CURVE"), key=lambda obj: obj.name
        )
        props = context.scene.drones_anim_panel
        helpers, lengths, cyclic, polylines = [], [], [], []
        output = None
        output_mesh = None
        try:
            graph = context.evaluated_depsgraph_get()
            for source in sources:
                if not source.data.splines:
                    raise ValueError(f"{source.name}：曲线没有样条")
                for index, spline in enumerate(source.data.splines):
                    helper = sampling_mesh(context.scene, source, index)
                    helpers.append(helper)
                    context.view_layer.update()
                    positions = sampled_positions(helper, graph)
                    closed = spline.use_cyclic_u
                    if len(positions) < 2:
                        raise ValueError(f"{source.name} / 样条 {index + 1}：有效采样点不足")
                    length = sum((b - a).length for a, b in zip(positions, positions[1:]))
                    if closed:
                        length += (positions[0] - positions[-1]).length
                    if not isfinite(length) or length <= 1e-6:
                        raise ValueError(f"{source.name} / 样条 {index + 1}：长度为零或无法采样")
                    lengths.append(length)
                    cyclic.append(closed)
                    polylines.append(positions)
            if self.mode == "COUNT":
                counts = allocate_counts(lengths, cyclic, props.curve_point_count)
            else:
                counts = [
                    spacing_count(length, props.curve_point_spacing, closed)
                    for length, closed in zip(lengths, cyclic)
                ]
            if sum(counts) > 10000:
                raise ValueError("顶点总数超过 10000，请增大间距或减少数量")
            vertices, edges = [], []
            for polyline, count, closed in zip(polylines, counts, cyclic):
                positions = uniform_samples(polyline, count, closed)
                offset = len(vertices)
                vertices.extend(positions)
                edges.extend((offset + index, offset + index + 1) for index in range(count - 1))
                if closed:
                    edges.append((offset + count - 1, offset))
            output_mesh = bpy.data.meshes.new("Curve Sample Mesh")
            output_mesh.from_pydata(vertices, edges, [])
            output_mesh.update()
            output = bpy.data.objects.new("Curve Sample Mesh", output_mesh)
            context.collection.objects.link(output)
        except Exception as exc:
            if output is not None:
                bpy.data.objects.remove(output, do_unlink=True)
            if output_mesh is not None and output_mesh.users == 0:
                bpy.data.meshes.remove(output_mesh)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            for helper in helpers:
                remove_helper(helper)
        for obj in context.selected_objects:
            obj.select_set(False)
        output.select_set(True)
        context.view_layer.objects.active = output
        context.view_layer.update()
        message = f"已从 {len(sources)} 条曲线生成 {len(vertices)} 个可编辑顶点"
        if self.mode == "DISTANCE":
            gaps = [length / (count if closed else count - 1)
                    for length, count, closed in zip(lengths, counts, cyclic)]
            short = sum(gap < props.curve_point_spacing - 1e-6 for gap in gaps)
            message += f"；实际弧长间距 {min(gaps):.3f}–{max(gaps):.3f} m"
            if short:
                message += f"；{short} 条样条过短，按最少端点/闭合点数生成"
            self.report({"WARNING"} if short else {"INFO"}, message)
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}

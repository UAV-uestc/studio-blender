"""Independent object-color animation and LED material helpers."""


def get_drones(scene):
    """Only manage this add-on's drones, including nested collections."""
    return [
        obj
        for obj in scene.objects
        if obj.type == "MESH" and "drones_anim_vertex_index" in obj
    ]


def color_curves(drone):
    animation = drone.animation_data
    if animation is None or animation.action is None:
        return []
    action = animation.action
    if getattr(action, "is_action_layered", False):
        slot = animation.action_slot
        if slot is None:
            return []
        curves = []
        for layer in action.layers:
            for strip in layer.strips:
                if strip.type == "KEYFRAME":
                    bag = strip.channelbag(slot)
                    if bag is not None:
                        curves.extend(bag.fcurves)
    else:
        curves = action.fcurves
    return [curve for curve in curves if curve.data_path == "color"]


def insert_color(drone, color, frame, *, step, frame_start, hold=False):
    """Keep Apply abrupt; optionally hold the new color until the next key."""
    # A duplicated object may share an action with its source.
    animation = drone.animation_data
    if animation and animation.action and animation.action.users > 1:
        animation.action = animation.action.copy()
    curves = color_curves(drone)
    frames = [frame]
    if step and frame > frame_start:
        previous = list(drone.color)
        for curve in curves:
            previous[curve.array_index] = curve.evaluate(frame - 1)
        drone.color = previous
        drone.keyframe_insert(data_path="color", frame=frame - 1)
        frames.insert(0, frame - 1)
    drone.color = (*color[:3], 1.0)
    drone.keyframe_insert(data_path="color", frame=frame)
    for curve in color_curves(drone):
        for point in curve.keyframe_points:
            if point.co.x in frames:
                point.interpolation = (
                    "CONSTANT" if hold and point.co.x == frame else "LINEAR"
                )
                point.handle_left.y = point.co.y
                point.handle_right.y = point.co.y
        curve.update()


def update_emission(scene, strength):
    materials = {
        drone.data.materials[0]
        for drone in get_drones(scene)
        if drone.data.materials and drone.data.materials[0] is not None
    }
    for material in materials:
        if material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type == "EMISSION":
                node.inputs["Strength"].default_value = strength
            elif node.type == "BSDF_PRINCIPLED":
                socket = node.inputs.get("Emission Strength")
                if socket is not None:
                    socket.default_value = strength

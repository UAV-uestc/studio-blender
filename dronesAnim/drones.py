"""Local drone creation helpers adapted from Skybrush's takeoff grid and meshes."""

import bmesh
import bpy
from mathutils import Matrix


def get_drone_collection(scene):
    collection = bpy.data.collections.get("Drones")
    if collection is None:
        collection = bpy.data.collections.new("Drones")
    if collection not in scene.collection.children_recursive:
        scene.collection.children.link(collection)
    return collection


def create_drone_mesh(radius=0.1):
    mesh = bpy.data.meshes.new("Drone Anim Mesh Template")
    bm = bmesh.new()
    try:
        bmesh.ops.create_icosphere(
            bm, subdivisions=2, radius=radius, matrix=Matrix(), calc_uvs=True
        )
        bm.to_mesh(mesh)
    finally:
        bm.free()

    led = bpy.data.materials.new("Drone Anim LED")
    if bpy.app.version < (6, 0, 0):
        led.use_nodes = True
    nodes = led.node_tree.nodes
    links = led.node_tree.links
    nodes.clear()
    info = nodes.new("ShaderNodeObjectInfo")
    emission = nodes.new("ShaderNodeEmission")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(info.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    led.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    mesh.materials.append(led)
    return mesh


def drone_names(count):
    index = 1
    while count:
        name = f"Drone {index}"
        if bpy.data.objects.get(name) is None:
            yield name
            count -= 1
        index += 1


def create_drone(location, *, name, template_mesh, collection, frame_start):
    """Copy the template mesh so each drone has independent geometry and pyro."""
    drone = bpy.data.objects.new(name, template_mesh.copy())
    drone.location = location
    drone.color = (1.0, 1.0, 1.0, 1.0)
    pyro = bpy.data.materials.new(f"Pyro of {name}")
    if bpy.app.version < (6, 0, 0):
        pyro.use_nodes = True
    pyro.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    drone.data.materials.append(pyro)
    collection.objects.link(drone)
    drone.keyframe_insert(data_path="color", frame=frame_start)
    return drone

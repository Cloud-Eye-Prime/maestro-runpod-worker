"""blender_guide_v1 scene path (scene_v2): grey-box guide animatic plus
depth, edge and mask passes.

Pure helpers (is_guide, guide_frames, validate_guide) need no Blender and are
shared with handler.py. build_guide / render_guide run INSIDE Blender and are
called from scene_builder.main when the job carries schema blender_guide_v1.
The job is the output of tools_local/blender_guide_20261101.build_job in the
lab repo: stage cuboids, cast parts, per-frame tracks, a locked camera.

Outputs in out_dir, one file per frame (frame numbers start at 1):
  frame_NNNN.png   the animatic (composite)
  depth_NNNN.png   Z normalised near=white, far=black over the camera clip
  canny_NNNN.png   soft edge pass: Sobel of depth and mask, then a blur
  mask_NNNN.png    the cast parts flagged "mask" (object index 1)
"""
import base64
import math
import os
import tempfile

GUIDE_SCHEMA = "blender_guide_v1"
CAPABILITY = "scene_v2"
GUIDE_MAX_FRAMES = 81
KNOWN_PASSES = ("animatic", "depth", "canny", "mask")
MASK_INDEX = 1
DEPTH_NEAR_M = 0.5
DEPTH_FAR_M = 12.0


def is_guide(scene):
    """True when scene is a blender_guide_v1 job."""
    return isinstance(scene, dict) and scene.get("schema") == GUIDE_SCHEMA


def guide_frames(scene, requested):
    """Frame count for a guide job: the scene's own, then the request, capped at 81."""
    n = scene.get("frames") if isinstance(scene.get("frames"), int) else requested
    return max(1, min(int(n), GUIDE_MAX_FRAMES))


def validate_guide(scene):
    """Return an error string, or None when the guide job is buildable."""
    for key in ("camera", "stage", "cast", "tracks"):
        if key not in scene:
            return "guide scene is missing %r" % key
    cam = scene["camera"]
    for key in ("lens_mm", "sensor_mm", "location", "look_at"):
        if key not in cam:
            return "guide camera is missing %r" % key
    passes = scene.get("passes", list(KNOWN_PASSES))
    for p in passes:
        if p not in KNOWN_PASSES:
            return "unknown pass %r" % (p,)
    for item in scene["stage"]:
        if "center" not in item or "size" not in item:
            return "stage item %r needs center and size" % (item.get("name"),)
    for actor in scene["cast"]:
        name = actor.get("name")
        if not name or name not in scene["tracks"]:
            return "cast member %r has no track" % (name,)
        if not actor.get("parts"):
            return "cast member %r has no parts" % (name,)
    return None


def _prim(bpy, kind, size, loc):
    if kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=loc)
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=0.5, minor_radius=0.12, location=loc)
    elif kind in ("capsule", "chain"):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=loc)
    else:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    obj = bpy.context.object
    obj.scale = tuple(size)
    return obj


def _empty(bpy, name, loc):
    obj = bpy.data.objects.new(name, None)
    obj.location = tuple(loc)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def build_guide(bpy, mathutils, scene_dict):
    """Create camera, stage, cast and keyframes in the open Blender scene."""
    sc = bpy.context.scene
    spec = scene_dict["camera"]
    bpy.ops.object.camera_add(location=tuple(spec["location"]))
    cam = bpy.context.object
    cam.data.lens = spec["lens_mm"]
    cam.data.sensor_width = spec["sensor_mm"]
    cam.data.clip_start, cam.data.clip_end = DEPTH_NEAR_M * 0.5, DEPTH_FAR_M * 2
    look = [a - b for a, b in zip(spec["look_at"], spec["location"])]
    cam.rotation_euler = mathutils.Vector(look).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam

    bpy.ops.object.light_add(type="SUN", location=(2.0, -2.0, 5.0))
    bpy.context.object.data.energy = 3.0

    for item in scene_dict["stage"]:
        _prim(bpy, "cube", item["size"], item["center"]).name = item["name"]

    for actor in scene_dict["cast"]:
        name = actor["name"]
        root = _empty(bpy, name, (0, 0, 0))
        head_part = next((p for p in actor["parts"] if p.get("id") == "head"), None)
        head_loc = head_part["loc"] if head_part else (0, 0, 0)
        head = _empty(bpy, name + ".head_pivot", head_loc)
        head.parent = root
        for part in actor["parts"]:
            follows = part.get("follows") == "head"
            links = int(part.get("links", 1)) if part["prim"] == "chain" else 1
            for k in range(links):
                loc = list(part["loc"])
                size = list(part["size"])
                if part["prim"] == "chain":
                    size = [size[0] * 2.0, size[1] * 2.0, size[2] / float(links)]
                    loc[2] = part["loc"][2] - k * size[2]
                obj = _prim(bpy, part["prim"], size, loc)
                obj.name = "%s.%s.%d" % (name, part["id"], k)
                if part.get("tilt_deg"):
                    obj.rotation_euler = (math.radians(part["tilt_deg"]), 0, 0)
                if follows:
                    obj.parent = head
                    obj.location = tuple(a - b for a, b in zip(loc, head_loc))
                else:
                    obj.parent = root
                if actor.get("mask"):
                    obj.pass_index = MASK_INDEX
        for row in scene_dict["tracks"][name]:
            f = row["frame"] + 1
            root.location = tuple(row["loc"])
            root.rotation_euler = (0, 0, math.radians(row["yaw_deg"]))
            head.rotation_euler = (0, 0, math.radians(row["head_yaw_deg"]))
            root.keyframe_insert("location", frame=f)
            root.keyframe_insert("rotation_euler", frame=f)
            head.keyframe_insert("rotation_euler", frame=f)


def build_compositor(bpy, out_dir, passes, backplate_path=None):
    """Wire depth, edge and mask File Output slots; composite = animatic."""
    sc = bpy.context.scene
    sc.use_nodes = True
    sc.render.use_compositing = True
    layer = sc.view_layers[0]
    layer.use_pass_z = True
    layer.use_pass_object_index = True
    tree = sc.node_tree
    tree.nodes.clear()
    new = tree.nodes.new
    rl = new("CompositorNodeRLayers")
    comp = new("CompositorNodeComposite")

    if backplate_path:
        sc.render.film_transparent = True
        img = new("CompositorNodeImage")
        img.image = bpy.data.images.load(backplate_path)
        over = new("CompositorNodeAlphaOver")
        tree.links.new(img.outputs["Image"], over.inputs[1])
        tree.links.new(rl.outputs["Image"], over.inputs[2])
        tree.links.new(over.outputs["Image"], comp.inputs["Image"])
    else:
        tree.links.new(rl.outputs["Image"], comp.inputs["Image"])

    fout = new("CompositorNodeOutputFile")
    fout.base_path = out_dir
    fout.format.file_format = "PNG"
    fout.format.color_mode = "BW"
    fout.file_slots.clear()
    wanted = [p for p in passes if p != "animatic"]
    for p in wanted:
        fout.file_slots.new(p + "_")

    rng = new("CompositorNodeMapRange")
    rng.inputs["From Min"].default_value = DEPTH_NEAR_M
    rng.inputs["From Max"].default_value = DEPTH_FAR_M
    rng.inputs["To Min"].default_value = 1.0
    rng.inputs["To Max"].default_value = 0.0
    rng.use_clamp = True
    tree.links.new(rl.outputs["Depth"], rng.inputs["Value"])

    idm = new("CompositorNodeIDMask")
    idm.index = MASK_INDEX
    tree.links.new(rl.outputs["IndexOB"], idm.inputs["ID value"])

    edge_d = new("CompositorNodeFilter")
    edge_d.filter_type = "SOBEL"
    tree.links.new(rng.outputs["Value"], edge_d.inputs["Image"])
    edge_m = new("CompositorNodeFilter")
    edge_m.filter_type = "SOBEL"
    tree.links.new(idm.outputs["Alpha"], edge_m.inputs["Image"])
    mix = new("CompositorNodeMixRGB")
    mix.blend_type = "ADD"
    tree.links.new(edge_d.outputs["Image"], mix.inputs[1])
    tree.links.new(edge_m.outputs["Image"], mix.inputs[2])
    blur = new("CompositorNodeBlur")
    blur.filter_type = "GAUSS"
    blur.size_x = blur.size_y = 2
    tree.links.new(mix.outputs["Image"], blur.inputs["Image"])

    sources = {"depth": rng.outputs["Value"], "canny": blur.outputs["Image"],
               "mask": idm.outputs["Alpha"]}
    for p in wanted:
        tree.links.new(sources[p], fout.inputs[p + "_"])


def decode_backplate(b64):
    """Write a base64 plate to a temp png; returns its path."""
    handle, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(handle, "wb") as f:
        f.write(base64.b64decode(b64))
    return path

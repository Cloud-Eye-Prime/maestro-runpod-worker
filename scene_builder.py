"""Runs INSIDE Blender's bundled Python (invoked via `blender -b --python
scene_builder.py -- <job_args_path> <out_dir>`).

Builds either the built-in test scene (scene missing or {"test": true}: a
lit sphere on a plane, camera at a 3/4 angle) or a minimal interpretation
of a Maestro scene dict layered on the same base scene:
  - register  -> camera height/lens
  - register  -> key light colour/energy
  - element   -> world background colour
Then sets engine/samples/resolution/frame range and renders PNGs to out_dir.
"""
import json
import os
import sys

import bpy

REGISTER_CAMERA = {
    "qian": {"height": 3.0, "lens": 28.0},
    "zhen": {"height": 1.6, "lens": 35.0},
    "kan":  {"height": 1.4, "lens": 40.0},
    "gen":  {"height": 1.6, "lens": 32.0},
    "xun":  {"height": 1.8, "lens": 85.0},
    "li":   {"height": 1.7, "lens": 40.0},
    "dui":  {"height": 1.5, "lens": 50.0},
    "kun":  {"height": 1.6, "lens": 50.0},
}
DEFAULT_REGISTER = "gen"

REGISTER_LIGHT = {
    "qian": {"color": (0.85, 0.90, 1.00), "energy": 1000.0},
    "zhen": {"color": (1.00, 0.95, 0.85), "energy": 1500.0},
    "kan":  {"color": (0.70, 0.85, 1.00), "energy": 600.0},
    "gen":  {"color": (1.00, 1.00, 1.00), "energy": 800.0},
    "xun":  {"color": (0.90, 1.00, 0.90), "energy": 700.0},
    "li":   {"color": (1.00, 0.98, 0.90), "energy": 1200.0},
    "dui":  {"color": (1.00, 0.90, 0.70), "energy": 900.0},
    "kun":  {"color": (1.00, 0.85, 0.70), "energy": 750.0},
}

ELEMENT_BACKGROUND = {
    "water": (0.05, 0.10, 0.20),
    "wood":  (0.05, 0.20, 0.08),
    "fire":  (0.25, 0.06, 0.03),
    "earth": (0.20, 0.15, 0.08),
    "metal": (0.15, 0.15, 0.18),
}
DEFAULT_ELEMENT = "earth"


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.lights, bpy.data.cameras, bpy.data.worlds):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def _build_objects(scene_dict):
    register = str(scene_dict.get("register") or DEFAULT_REGISTER).lower()
    element = str(scene_dict.get("element") or DEFAULT_ELEMENT).lower()
    cam_spec = REGISTER_CAMERA.get(register, REGISTER_CAMERA[DEFAULT_REGISTER])
    light_spec = REGISTER_LIGHT.get(register, REGISTER_LIGHT[DEFAULT_REGISTER])
    bg_color = ELEMENT_BACKGROUND.get(element, ELEMENT_BACKGROUND[DEFAULT_ELEMENT])

    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))

    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0, 0, 1.0))
    subject = bpy.context.object

    bpy.ops.object.light_add(type="AREA", location=(3.0, -3.0, 4.0))
    key_light = bpy.context.object
    key_light.data.energy = float(light_spec["energy"])
    key_light.data.color = light_spec["color"]
    key_light.data.size = 2.0

    height = float(cam_spec["height"])
    bpy.ops.object.camera_add(location=(4.0, -4.0, height))
    camera = bpy.context.object
    camera.data.lens = float(cam_spec["lens"])
    direction = subject.location - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera

    world = bpy.data.worlds.new("MaestroWorld")
    world.use_nodes = True
    bg_node = world.node_tree.nodes.get("Background")
    if bg_node is not None:
        bg_node.inputs[0].default_value = (bg_color[0], bg_color[1], bg_color[2], 1.0)
    bpy.context.scene.world = world


def _configure_render(job):
    scene = bpy.context.scene
    scene.render.engine = job["engine"]
    scene.render.resolution_x = job["width"]
    scene.render.resolution_y = job["height"]
    scene.render.resolution_percentage = 100
    scene.frame_start = 1
    scene.frame_end = job["frames"]
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_file_extension = False

    device = "CPU"
    if job["engine"] == "CYCLES":
        scene.cycles.samples = job["samples"]
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for candidate in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = candidate
            except TypeError:
                continue
            prefs.get_devices()
            enabled = 0
            for dev in prefs.devices:
                if dev.type == candidate:
                    dev.use = True
                    enabled += 1
            if enabled > 0:
                device = candidate
                break
        scene.cycles.device = "GPU" if device != "CPU" else "CPU"
    else:
        scene.eevee.taa_render_samples = job["samples"]

    print("[maestro] device=%s" % device)
    print("[maestro] blender_version=%s" % bpy.app.version_string)


def main():
    argv = sys.argv
    sep = argv.index("--")
    job_args_path = argv[sep + 1]
    out_dir = argv[sep + 2]
    os.makedirs(out_dir, exist_ok=True)

    with open(job_args_path) as f:
        job = json.load(f)

    scene_dict = job.get("scene") or {}

    _clear_scene()
    _build_objects(scene_dict)
    _configure_render(job)

    for frame in range(1, job["frames"] + 1):
        bpy.context.scene.frame_set(frame)
        bpy.context.scene.render.filepath = os.path.join(out_dir, "frame_%04d.png" % frame)
        bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()

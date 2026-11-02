"""Offline tests for the scene_v2 (blender_guide_v1) path. bpy is absent
outside Blender: the pure helpers are tested directly, the Blender-building
functions against a recording fake."""
import copy
import sys
import types

if "bpy" not in sys.modules:
    _bpy_stub = types.ModuleType("bpy")
    _bpy_stub.types = types.SimpleNamespace(
        RenderSettings=types.SimpleNamespace(
            bl_rna=types.SimpleNamespace(properties={"engine": types.SimpleNamespace(enum_items=[])})))
    _bpy_stub.context = types.SimpleNamespace()
    _bpy_stub.data = types.SimpleNamespace()
    _bpy_stub.ops = types.SimpleNamespace()
    sys.modules["bpy"] = _bpy_stub
if "mathutils" not in sys.modules:
    sys.modules["mathutils"] = types.ModuleType("mathutils")

import guide_scene  # noqa: E402
import handler  # noqa: E402
import scene_builder  # noqa: E402,F401


def _guide(frames=81):
    rows = [{"frame": i, "loc": [0, 3, 0], "yaw_deg": 180 - i, "head_yaw_deg": 0} for i in range(frames)]
    return {
        "schema": "blender_guide_v1", "frames": frames, "fps": 16, "width": 1280, "height": 720,
        "camera": {"lens_mm": 35, "sensor_mm": 36, "location": [0, 0, 1.4], "look_at": [0, 4, 1.2]},
        "passes": ["animatic", "depth", "canny", "mask"],
        "stage": [{"name": "table", "center": [0, 4, 0.5], "size": [1, 1, 1]}],
        "cast": [{"name": "dragon_b", "mask": True,
                  "parts": [{"id": "torso", "prim": "capsule", "size": [1, 1, 1], "loc": [0, 0, 1]},
                            {"id": "head", "prim": "sphere", "size": [1, 1, 1], "loc": [0, 0, 2]}]}],
        "tracks": {"dragon_b": rows},
    }


def test_guide_allows_81_frames_the_old_path_still_caps_at_48():
    clean, err = handler.validate_input({"scene": _guide(), "frames": 5})
    assert err is None and clean["frames"] == 81
    clean, err = handler.validate_input({"scene": {"test": True}, "frames": 81})
    assert err is None and clean["frames"] == 48


def test_guide_over_81_is_capped():
    assert guide_scene.guide_frames({"frames": 200}, 1) == 81


def test_guide_job_may_arrive_as_the_input_itself():
    clean, err = handler.validate_input(_guide())
    assert err is None and clean["scene"]["schema"] == "blender_guide_v1"


def test_old_default_and_sphere_scene_unchanged():
    clean, err = handler.validate_input({})
    assert err is None and clean["scene"] == {"test": True} and clean["frames"] == 1
    clean, err = handler.validate_input({"scene": {"register": "xun"}, "frames": 3})
    assert clean["scene"] == {"register": "xun"} and clean["frames"] == 3


def test_malformed_guide_is_refused_with_a_reason():
    s = _guide()
    del s["tracks"]
    assert "tracks" in handler.validate_input({"scene": s})[1]
    s = _guide()
    s["passes"] = ["bogus"]
    assert "unknown pass" in handler.validate_input({"scene": s})[1]
    s = _guide()
    s["tracks"] = {}
    assert "no track" in handler.validate_input({"scene": s})[1]


def test_diag_reports_scene_v2_capability(monkeypatch):
    monkeypatch.setattr(handler, "_run_diag_blender", lambda out: types.SimpleNamespace(stderr=""))
    out = handler.handler({"input": {"diag": True}})
    assert out["ok"] is True and out["capabilities"] == ["scene_v2"]


class _Node:
    def __init__(self, kind):
        self.kind = kind
        self.inputs = _Sockets()
        self.outputs = _Sockets()
        self.file_slots = _Slots()
        self.format = types.SimpleNamespace()


class _Sockets(dict):
    def __missing__(self, key):
        self[key] = types.SimpleNamespace(default_value=None)
        return self[key]

    def __getitem__(self, key):
        if isinstance(key, int):
            return dict.__getitem__(self, ("i", key)) if ("i", key) in self else self.setdefault(("i", key), types.SimpleNamespace())
        return dict.__getitem__(self, key) if key in self else self.__missing__(key)


class _Slots(list):
    def new(self, name):
        self.append(name)

    def clear(self):
        del self[:]


class _Nodes(list):
    def new(self, kind):
        n = _Node(kind)
        self.append(n)
        return n


class _Tree:
    def __init__(self):
        self.nodes = _Nodes()
        self.links = types.SimpleNamespace(new=lambda a, b: None)


def test_compositor_wires_one_slot_per_extra_pass():
    scene = types.SimpleNamespace(
        use_nodes=False, node_tree=_Tree(), view_layers=[types.SimpleNamespace()],
        render=types.SimpleNamespace(use_compositing=False, film_transparent=False))
    bpy = types.SimpleNamespace(context=types.SimpleNamespace(scene=scene))
    guide_scene.build_compositor(bpy, "/tmp/out", ["animatic", "depth", "canny", "mask"])
    kinds = [n.kind for n in scene.node_tree.nodes]
    out = next(n for n in scene.node_tree.nodes if n.kind == "CompositorNodeOutputFile")
    assert list(out.file_slots) == ["depth_", "canny_", "mask_"]
    assert "CompositorNodeIDMask" in kinds and "CompositorNodeBlur" in kinds
    assert scene.view_layers[0].use_pass_z and scene.view_layers[0].use_pass_object_index


def test_validate_guide_is_pure_and_does_not_mutate():
    s = _guide()
    before = copy.deepcopy(s)
    assert guide_scene.validate_guide(s) is None
    assert s == before


# ---- video packaging (scene_v2: one mp4 per pass)
import base64  # noqa: E402
import os  # noqa: E402
import zipfile  # noqa: E402


def _fake_ffmpeg(size_by_name):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = cmd[-1]
        name = os.path.splitext(os.path.basename(out))[0]
        with open(out, "wb") as f:
            f.write(b"x" * size_by_name.get(name, 1000))
        return types.SimpleNamespace(returncode=0, stderr="")
    run.calls = calls
    return run


def test_ffmpeg_cmd_is_h264_yuv420p_crf12_at_the_scene_fps():
    cmd = guide_scene.ffmpeg_cmd("/o", "mask", 16, "/v/mask.mp4")
    assert cmd[cmd.index("-crf") + 1] == "12"
    assert cmd[cmd.index("-framerate") + 1] == "16"
    assert "libx264" in cmd and "format=yuv420p" in cmd[cmd.index("-vf") + 1]
    assert cmd[cmd.index("-i") + 1].replace("\\", "/") == "/o/mask_%04d.png"
    assert guide_scene.ffmpeg_cmd("/o", "animatic", 16, "/v/a.mp4")[
        guide_scene.ffmpeg_cmd("/o", "animatic", 16, "/v/a.mp4").index("-i") + 1
    ].replace("\\", "/") == "/o/frame_%04d.png"


def test_encode_one_video_per_pass_and_small_total_stays_mp4(tmp_path):
    run = _fake_ffmpeg({})
    made = guide_scene.encode_passes(str(tmp_path), str(tmp_path / "v"),
                                     ["animatic", "depth", "canny", "mask"], 16, run=run)
    assert [n for n, _ in made] == ["animatic", "depth", "canny", "mask"]
    paths, how = guide_scene.package_videos(made, str(tmp_path / "g.zip"))
    assert how == "mp4" and len(paths) == 4


def test_encode_failure_raises_with_stderr(tmp_path):
    def bad(cmd, **kw):
        return types.SimpleNamespace(returncode=1, stderr="no such file")
    try:
        guide_scene.encode_passes(str(tmp_path), str(tmp_path / "v"), ["depth"], 16, run=bad)
    except RuntimeError as e:
        assert "depth" in str(e) and "no such file" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_over_cap_goes_to_one_zip_and_zip_over_cap_is_refused(tmp_path):
    made = guide_scene.encode_passes(str(tmp_path), str(tmp_path / "v"), ["depth", "mask"], 16,
                                     run=_fake_ffmpeg({"depth": 4000, "mask": 4000}))
    paths, how = guide_scene.package_videos(made, str(tmp_path / "g.zip"), cap=5000)
    assert how == "zip" and len(paths) == 1
    assert sorted(zipfile.ZipFile(paths[0]).namelist()) == ["depth.mp4", "mask.mp4"]
    import random
    rnd = random.Random(1)
    noisy = guide_scene.encode_passes(str(tmp_path), str(tmp_path / "w"), ["depth"], 16, run=_fake_ffmpeg({}))
    with open(noisy[0][1], "wb") as f:
        f.write(bytes(rnd.getrandbits(8) for _ in range(6000)))
    try:
        guide_scene.package_videos(noisy, str(tmp_path / "h.zip"), cap=5000)
    except RuntimeError as e:
        assert "over the 5000 cap" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_handler_returns_four_mp4s_base64_for_a_guide_job(monkeypatch):
    def fake_blender(job_args_path, out_dir):
        for i in range(1, 4):
            open(os.path.join(out_dir, "frame_%04d.png" % i), "wb").write(b"png")
        return types.SimpleNamespace(returncode=0, stdout="[maestro] device=OPTIX\n", stderr="")
    monkeypatch.setattr(handler, "_run_blender", fake_blender)
    monkeypatch.setattr(handler.subprocess, "run", _fake_ffmpeg({"mask": 1234}))
    out = handler.handler({"input": {"scene": _guide(frames=3), "return": "base64"}})
    assert out["ok"] is True and out["frames_rendered"] == 4
    names = [a["name"] for a in out["artifacts"]]
    assert names == ["animatic.mp4", "depth.mp4", "canny.mp4", "mask.mp4"]
    mask = next(a for a in out["artifacts"] if a["name"] == "mask.mp4")
    assert len(base64.b64decode(mask["b64"])) == 1234


def test_guide_engine_defaults_to_cycles_and_old_default_unchanged():
    assert handler.validate_input({"scene": _guide()})[0]["engine"] == "CYCLES"
    assert handler.validate_input({})[0]["engine"] == "CYCLES"
    assert handler.validate_input({"scene": _guide(), "engine": "EEVEE"})[0]["engine"] == "EEVEE"

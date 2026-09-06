"""Offline tests for handler.py. Blender is stubbed via monkeypatched
subprocess.run -- no GPU, no real render, runs anywhere pytest runs."""
import base64
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handler


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def _stub_blender(n_frames=1, device="OPTIX", version="4.2.23", returncode=0, stderr=""):
    def _run(cmd, capture_output, text, timeout):
        job_args_path = cmd[-2]
        out_dir = cmd[-1]
        with open(job_args_path) as f:
            json.load(f)  # confirms handler wrote valid job args
        if returncode == 0:
            for i in range(1, n_frames + 1):
                path = os.path.join(out_dir, "frame_%04d.png" % i)
                with open(path, "wb") as pf:
                    pf.write(b"\x89PNG\r\n\x1a\nfakepngdata")
        stdout = "[maestro] blender_version=%s\n[maestro] device=%s\n" % (version, device)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return _run


def test_default_input_renders_test_scene(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(n_frames=1)(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    out = handler.handler({"input": {}})
    assert out["ok"] is True
    assert out["frames_rendered"] == 1
    assert out["device"] == "OPTIX"
    assert out["blender"] == "4.2.23"
    assert len(out["artifacts"]) == 1
    assert "b64" in out["artifacts"][0]
    base64.b64decode(out["artifacts"][0]["b64"])


def test_explicit_test_marker_multiple_frames(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(n_frames=3)(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    out = handler.handler({"input": {"scene": {"test": True}, "frames": 3}})
    assert out["ok"] is True
    assert out["frames_rendered"] == 3
    names = [a["name"] for a in out["artifacts"]]
    assert names == sorted(names)


def test_frames_clamped_above_max():
    clean, err = handler.validate_input({"frames": 999})
    assert err is None
    assert clean["frames"] == handler.MAX_FRAMES


def test_frames_clamped_below_min():
    clean, err = handler.validate_input({"frames": -5})
    assert err is None
    assert clean["frames"] == 1


def test_resolution_clamped_to_pixel_cap():
    clean, err = handler.validate_input({"width": 3840, "height": 2160})
    assert err is None
    assert clean["width"] * clean["height"] <= handler.MAX_PIXELS
    assert clean["width"] < 3840


def test_samples_clamped_above_max():
    clean, err = handler.validate_input({"samples": 100000})
    assert err is None
    assert clean["samples"] == handler.MAX_SAMPLES


def test_samples_clamped_below_min():
    clean, err = handler.validate_input({"samples": 0})
    assert err is None
    assert clean["samples"] == 1


def test_invalid_engine_rejected():
    clean, err = handler.validate_input({"engine": "POVRAY"})
    assert clean is None
    assert "engine" in err


def test_invalid_return_mode_rejected():
    clean, err = handler.validate_input({"return": "ftp"})
    assert clean is None
    assert err is not None


def test_url_return_requires_upload_url():
    clean, err = handler.validate_input({"return": "url"})
    assert clean is None
    assert "upload_url" in err


def test_scene_must_be_object():
    clean, err = handler.validate_input({"scene": "not-a-dict"})
    assert clean is None
    assert "scene" in err


def test_frames_wrong_type_rejected():
    clean, err = handler.validate_input({"frames": "lots"})
    assert clean is None
    assert "frames" in err


def test_url_return_uploads_and_reports_url(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(n_frames=1)(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    monkeypatch.setattr(handler.requests, "put", lambda url, data: _FakeResp(200))
    out = handler.handler({"input": {"return": "url", "upload_url": "https://example.invalid/put"}})
    assert out["ok"] is True
    assert out["artifacts"][0]["url"] == "https://example.invalid/put"


def test_upload_failure_is_reported(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(n_frames=1)(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    monkeypatch.setattr(handler.requests, "put", lambda url, data: _FakeResp(500))
    out = handler.handler({"input": {"return": "url", "upload_url": "https://example.invalid/put"}})
    assert out["ok"] is False
    assert "upload failed" in out["error"]


def test_blender_nonzero_exit_reported(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(returncode=1, stderr="boom")(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    out = handler.handler({"input": {}})
    assert out["ok"] is False
    assert "boom" in out["error"]


def test_no_frames_rendered_reported(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(n_frames=0)(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    out = handler.handler({"input": {}})
    assert out["ok"] is False
    assert "no frames" in out["error"]


def test_render_timeout_reported(monkeypatch):
    def _raise(job_args_path, out_dir):
        raise subprocess.TimeoutExpired(cmd="blender", timeout=1800)
    monkeypatch.setattr(handler, "_run_blender", _raise)
    out = handler.handler({"input": {}})
    assert out["ok"] is False
    assert "timed out" in out["error"]


def test_unknown_device_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(n_frames=1, device="TPU")(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    out = handler.handler({"input": {}})
    assert out["ok"] is True
    assert out["device"] == "CPU"


def test_never_crashes_on_garbage_input():
    out = handler.handler({"input": {"frames": "not-a-number"}})
    assert out["ok"] is False
    assert "error" in out


def test_never_crashes_on_missing_input_key():
    out = handler.handler({})
    assert isinstance(out, dict)
    assert "ok" in out

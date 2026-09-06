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


def _stub_blender(n_frames=1, device="OPTIX", version="4.2.23", returncode=0, stderr="",
                   device_reason="prefs.get_devices() found 1 OPTIX device(s)"):
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
        stdout = "[maestro] blender_version=%s\n[maestro] device=%s\n[maestro] device_reason=%s\n" % (
            version, device, device_reason)
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
    assert "OPTIX" in out["device_reason"]
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


def test_eevee_reports_cpu_with_reason(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(
        n_frames=1, device="CPU",
        device_reason="engine is EEVEE, not CYCLES; Cycles CUDA/OPTIX device selection does not apply")(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    out = handler.handler({"input": {"engine": "EEVEE"}})
    assert out["ok"] is True
    assert out["device"] == "CPU"
    assert "EEVEE" in out["device_reason"]


def test_cycles_cpu_fallback_reports_reason(monkeypatch):
    monkeypatch.setattr(handler, "_run_blender", lambda p, o: _stub_blender(
        n_frames=1, device="CPU",
        device_reason="no GPU device available for OPTIX or CUDA (OPTIX: 0 devices in prefs.devices; CUDA: 0 devices in prefs.devices)")(
        [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
        True, True, handler.RENDER_TIMEOUT_SEC))
    out = handler.handler({"input": {"engine": "CYCLES"}})
    assert out["ok"] is True
    assert out["device"] == "CPU"
    assert "OPTIX" in out["device_reason"] and "CUDA" in out["device_reason"]


def test_missing_device_reason_line_has_fallback_message(monkeypatch):
    def _run(p, o):
        with open(p) as f:
            json.load(f)
        path = os.path.join(o, "frame_0001.png")
        with open(path, "wb") as pf:
            pf.write(b"\x89PNG\r\n\x1a\nfakepngdata")
        stdout = "[maestro] blender_version=4.2.23\n[maestro] device=OPTIX\n"
        return subprocess.CompletedProcess(
            [handler.BLENDER_BIN, "-b", "--python", handler.SCENE_BUILDER, "--", p, o],
            0, stdout=stdout, stderr="")
    monkeypatch.setattr(handler, "_run_blender", _run)
    out = handler.handler({"input": {}})
    assert out["ok"] is True
    assert out["device_reason"] == "no device_reason reported by scene_builder.py"


def test_diag_mode_bypasses_render(monkeypatch):
    monkeypatch.setattr(
        handler.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="diag output", stderr=""))

    def _fake_diag_blender(out_path):
        with open(out_path, "w") as f:
            json.dump({"blender_version": "4.2.23", "devices": {"OPTIX": [{"name": "A5000", "type": "OPTIX", "use": True}]}}, f)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")
    monkeypatch.setattr(handler, "_run_diag_blender", _fake_diag_blender)

    out = handler.handler({"input": {"diag": True}})
    assert out["ok"] is True
    assert "diag" in out
    assert out["diag"]["blender_devices"]["devices"]["OPTIX"][0]["type"] == "OPTIX"
    assert out["diag"]["nvidia_smi"] == "diag output"


def test_diag_mode_survives_missing_output_file(monkeypatch):
    monkeypatch.setattr(
        handler.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""))
    monkeypatch.setattr(
        handler, "_run_diag_blender",
        lambda out_path: subprocess.CompletedProcess([], 1, stdout="", stderr="blender crashed"))

    out = handler.handler({"input": {"diag": True}})
    assert out["ok"] is True
    assert out["diag"]["blender_devices"] is None
    assert "crashed" in out["diag"]["blender_stderr_tail"]


def test_never_crashes_on_garbage_input():
    out = handler.handler({"input": {"frames": "not-a-number"}})
    assert out["ok"] is False
    assert "error" in out


def test_never_crashes_on_missing_input_key():
    out = handler.handler({})
    assert isinstance(out, dict)
    assert "ok" in out

"""RunPod serverless handler for the Maestro Blender render worker.

Contract:
  input:  {"scene": {...}, "frames": int (1-48), "width": int, "height": int,
           "engine": "CYCLES"|"EEVEE", "samples": int,
           "return": "base64"|"url", "upload_url": optional str}
  output: {"ok": true, "frames_rendered": int, "seconds": float,
           "device": "CUDA"|"OPTIX"|"CPU", "device_reason": "<string>",
           "artifacts": [...], "blender": "<version string>"}
  error:  {"ok": false, "error": "<string>"}

  device_reason is additive (not in the original fixed contract): why
  "device" reads what it does. CYCLES + a GPU found: which backend and
  how many devices. CYCLES + none found: which backends were tried and
  why each failed. Any non-CYCLES engine: "device" is always "CPU"
  because Cycles device selection does not apply and the contract has
  no GPU value for other engines -- device_reason says so, so a caller
  never mistakes that CPU for "no GPU was available".

  diag mode (not a render): input {"diag": true} skips validate_input
  entirely and returns {"ok": true, "diag": {"nvidia_smi": str,
  "cuda_libs": [str], "blender_devices": {<backend>: [...]} | null,
  "blender_stderr_tail": str}} -- nvidia-smi output, the CUDA/nvidia
  driver libs visible in the container, and Blender's own
  prefs.devices for OPTIX/CUDA/NONE. No scene, no render, near-zero
  cost: the fast way to tell "no libcuda in the container" apart from
  "Cycles found no device" apart from "the driver refused this
  Blender build's kernels".
"""
import base64
import glob
import json
import os
import re
import subprocess
import tempfile
import time

import requests
import runpod

SCENE_BUILDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene_builder.py")
BLENDER_BIN = "blender"

MAX_FRAMES = 48
MAX_PIXELS = 1920 * 1080
MAX_SAMPLES = 256
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_SAMPLES = 64
DEFAULT_FRAMES = 1
RENDER_TIMEOUT_SEC = 1800

VALID_ENGINES = ("CYCLES", "EEVEE")
VALID_RETURNS = ("base64", "url")
VALID_DEVICES = ("CUDA", "OPTIX", "CPU")

_VERSION_RE = re.compile(r"\[maestro\] blender_version=(\S+)")
_DEVICE_RE = re.compile(r"\[maestro\] device=(\S+)")
_DEVICE_REASON_RE = re.compile(r"\[maestro\] device_reason=(.*)")

DIAG_TIMEOUT_SEC = 60


def _as_int(value, default):
    """Coerce value to int, or report why it cannot be. Returns (int_or_None, error_or_None)."""
    if value is None:
        return default, None
    if isinstance(value, bool):
        return None, "must be an integer"
    if isinstance(value, int):
        return value, None
    if isinstance(value, float) and value.is_integer():
        return int(value), None
    return None, "must be an integer"


def validate_input(data):
    """Validate and clamp job input. Returns (clean_dict, None) or (None, error_string)."""
    if not isinstance(data, dict):
        return None, "input must be an object"

    scene = data.get("scene")
    if scene is None:
        scene = {"test": True}
    if not isinstance(scene, dict):
        return None, "scene must be an object"

    frames, err = _as_int(data.get("frames"), DEFAULT_FRAMES)
    if err:
        return None, "frames %s" % err
    frames = max(1, min(frames, MAX_FRAMES))

    width, err = _as_int(data.get("width"), DEFAULT_WIDTH)
    if err:
        return None, "width %s" % err
    height, err = _as_int(data.get("height"), DEFAULT_HEIGHT)
    if err:
        return None, "height %s" % err
    if width < 1 or height < 1:
        return None, "width and height must be positive"
    if width * height > MAX_PIXELS:
        scale = (MAX_PIXELS / float(width * height)) ** 0.5
        width = max(1, int(width * scale))
        height = max(1, int(height * scale))

    samples, err = _as_int(data.get("samples"), DEFAULT_SAMPLES)
    if err:
        return None, "samples %s" % err
    samples = max(1, min(samples, MAX_SAMPLES))

    engine = data.get("engine") or "CYCLES"
    if engine not in VALID_ENGINES:
        return None, "engine must be one of %s" % (VALID_ENGINES,)

    return_mode = data.get("return") or "base64"
    if return_mode not in VALID_RETURNS:
        return None, "return must be one of %s" % (VALID_RETURNS,)

    upload_url = data.get("upload_url")
    if return_mode == "url" and not upload_url:
        return None, "upload_url is required when return is 'url'"

    return {
        "scene": scene,
        "frames": frames,
        "width": width,
        "height": height,
        "engine": engine,
        "samples": samples,
        "return": return_mode,
        "upload_url": upload_url,
    }, None


def _extract(pattern, text, default):
    match = pattern.search(text or "")
    return match.group(1) if match else default


def _run_blender(job_args_path, out_dir):
    cmd = [BLENDER_BIN, "-b", "--python", SCENE_BUILDER, "--", job_args_path, out_dir]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=RENDER_TIMEOUT_SEC)


def _run_diag_blender(out_path):
    cmd = [BLENDER_BIN, "-b", "--python", SCENE_BUILDER, "--", "--diag", out_path]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=DIAG_TIMEOUT_SEC)


def run_diag():
    """No render, no scene, near-zero cost: nvidia-smi, the CUDA driver
    libs visible to the container, and Blender's own prefs.devices for
    each compute_device_type -- the fast path to tell "no libcuda in the
    container" apart from "Cycles found no device" apart from "the
    driver refused this Blender build's kernels"."""
    try:
        proc = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=30)
        nvidia_smi = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
    except Exception as e:
        nvidia_smi = "nvidia-smi failed: %s: %s" % (type(e).__name__, str(e))

    try:
        proc = subprocess.run(
            "ls /usr/lib/x86_64-linux-gnu | grep -i -E 'cuda|nvidia'",
            shell=True, capture_output=True, text=True, timeout=30,
        )
        cuda_libs = [line for line in (proc.stdout or "").splitlines() if line]
    except Exception as e:
        cuda_libs = ["error: %s: %s" % (type(e).__name__, str(e))]

    with tempfile.TemporaryDirectory() as work_dir:
        out_path = os.path.join(work_dir, "diag.json")
        blender_devices = None
        blender_stderr_tail = ""
        try:
            result = _run_diag_blender(out_path)
            blender_stderr_tail = (result.stderr or "")[-2000:]
        except subprocess.TimeoutExpired:
            blender_stderr_tail = "blender diag call timed out"
        if os.path.exists(out_path):
            with open(out_path) as f:
                blender_devices = json.load(f)

    return {
        "ok": True,
        "diag": {
            "nvidia_smi": nvidia_smi,
            "cuda_libs": cuda_libs,
            "blender_devices": blender_devices,
            "blender_stderr_tail": blender_stderr_tail,
        },
    }


def handler(job):
    start = time.time()
    try:
        input_data = job.get("input") or {}
        if input_data.get("diag") is True:
            return run_diag()

        clean, err = validate_input(input_data)
        if err:
            return {"ok": False, "error": err}

        with tempfile.TemporaryDirectory() as work_dir:
            out_dir = os.path.join(work_dir, "frames")
            os.makedirs(out_dir, exist_ok=True)
            job_args_path = os.path.join(work_dir, "job_args.json")
            with open(job_args_path, "w") as f:
                json.dump(clean, f)

            try:
                result = _run_blender(job_args_path, out_dir)
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "blender render timed out"}

            if result.returncode != 0:
                tail = (result.stderr or "")[-2000:]
                return {"ok": False, "error": "blender exited %d: %s" % (result.returncode, tail)}

            stdout = result.stdout or ""
            device = _extract(_DEVICE_RE, stdout, "CPU")
            if device not in VALID_DEVICES:
                device = "CPU"
            device_reason = _extract(_DEVICE_REASON_RE, stdout, "no device_reason reported by scene_builder.py")
            blender_version = _extract(_VERSION_RE, stdout, "unknown")

            frame_files = sorted(glob.glob(os.path.join(out_dir, "*.png")))
            if not frame_files:
                return {"ok": False, "error": "no frames rendered"}

            artifacts = []
            for path in frame_files:
                name = os.path.basename(path)
                if clean["return"] == "base64":
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                    artifacts.append({"name": name, "b64": b64})
                else:
                    with open(path, "rb") as f:
                        data = f.read()
                    resp = requests.put(clean["upload_url"], data=data)
                    if resp.status_code >= 300:
                        return {"ok": False, "error": "upload failed for %s: HTTP %d" % (name, resp.status_code)}
                    artifacts.append({"name": name, "url": clean["upload_url"]})

            return {
                "ok": True,
                "frames_rendered": len(artifacts),
                "seconds": round(time.time() - start, 3),
                "device": device,
                "device_reason": device_reason,
                "artifacts": artifacts,
                "blender": blender_version,
            }
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e))}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

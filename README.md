# maestro-runpod-worker

A standalone Blender render worker for RunPod Serverless. Renders either
a built-in test scene or a minimal interpretation of a Maestro scene dict.
Not a copy of the seat's Blender pipeline -- this is a separate,
self-contained image driven by the fixed handler contract below.

## Handler contract

Input:

```json
{
  "scene": {"...": "manifest scene dict, or omit/{\"test\": true} for the built-in test scene"},
  "frames": 1,
  "width": 1280,
  "height": 720,
  "engine": "CYCLES",
  "samples": 64,
  "return": "base64",
  "upload_url": null
}
```

- `frames` is clamped to 1-48.
- `width * height` is clamped to at most 1920x1080 (scaled down, aspect preserved).
- `samples` is clamped to 1-256.
- `engine` is `"CYCLES"` or `"EEVEE"`.
- `return` is `"base64"` or `"url"`; `"url"` requires `upload_url` (a presigned PUT URL).
- Invalid input never crashes the worker; it returns `{"ok": false, "error": "..."}`.

Output (success):

```json
{
  "ok": true,
  "frames_rendered": 1,
  "seconds": 12.3,
  "device": "OPTIX",
  "artifacts": [{"name": "frame_0001.png", "b64": "..."}],
  "blender": "4.2.23"
}
```

Output (error): `{"ok": false, "error": "<string>"}`

For `CYCLES`, the worker tries OptiX then CUDA and falls back to CPU;
`device` reports which one actually rendered.

## Calling the endpoint

```bash
curl -X POST "https://api.runpod.ai/v2/<ENDPOINT_ID>/runsync" \
  -H "Authorization: Bearer <RUNPOD_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"input": {"frames": 1}}'
```

Omitting `scene` (or passing `{"test": true}`) renders the built-in test
scene -- a lit sphere on a plane, camera at a 3/4 angle -- so a probe
needs no manifest.

## Expected costs

Per the pod proof in `runpod_proof_20260903/RESULTS.md`: an RTX 3090
rendered 24 Cycles frames at 1280x720 in about 12 minutes (~6.9s/frame
steady-state after the first-frame kernel compile). At RunPod's Secure
Cloud RTX 3090 rate (~$0.50/hr at proof time), a 24-frame render costs on
the order of $0.10. Serverless per-second billing on a comparable GPU
should land in the same range for equivalent frame counts; actual cost
depends on the live RunPod rate and cold-start time for the endpoint.

## Image

Built by `.github/workflows/build.yml` on every push to `main` and
pushed to `ghcr.io/cloud-eye-prime/maestro-runpod-worker` tagged `latest`
and the short commit SHA.

## Offline tests

```bash
pip install pytest requests runpod
pytest test_handler_offline.py -v
```

Blender is stubbed via monkeypatched `subprocess.run` -- no GPU, no
Blender install, and no network needed to run the suite.

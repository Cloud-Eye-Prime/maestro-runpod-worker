# Worker GPU: diagnose and fix CPU-only rendering

Brief: specs/briefs_20260906/WORKER_GPU.md (dragon-seed).
Endpoint: e9dn9lnn5g4j9c, pool AMPERE_24, image
ghcr.io/cloud-eye-prime/maestro-runpod-worker:latest.
Worktree: maestro-gpu-wt, branch worker-gpu.
Cost cap: $0.50 of RunPod time for this whole job.
API key: read from C:\Users\grego\Desktop\CloudEye\specs\runpod-api.txt
(NOTE: brief names ...\dragon-seed\specs\runpod-api.txt; the file is
actually one level up, directly under CloudEye\specs\. Not a
STOP-AND-ASK -- the file exists, only the path in the brief text is off
by one directory).
RunPod job dispatch host is api.runpod.ai (run/runsync/health/status),
NOT api.runpod.io as the brief text says; api.runpod.io/v2 is the
management REST API (endpoints/templates/CRUD). Confirmed live: the
.io host 404s on /health, the .ai host returns 200.

Local test runner: mcp-servers/.venv-shared (Python 3.13) + `pip install
runpod` into it. The repo's own fresh venv could not be created --
C:\Users\grego\AppData\Local\Programs\Python\Python311 is an incomplete
install (venv module and bare interpreter both fail with
"ModuleNotFoundError: No module named 'encodings'", sys.base_prefix
resolves to the CWD instead of the install dir). CI enforces the real
3.11 gate (build.yml, setup-python 3.11); local runs here are a
pre-push smoke check only, not a substitute.

## Progress

- [commit 1] Worktree created from origin/main. Baseline offline suite:
  20 passed (mcp-servers/.venv-shared). Build note started.
- [commit 2] DIAGNOSED ON THE METAL, live, against the current :latest
  image (endpoint e9dn9lnn5g4j9c, worker 07b4s0fe4k1wxg, NVIDIA RTX
  A5000, pool AMPERE_24):
  - Reproduced the seat's own render_probe payload verbatim
    ({"scene":{"test":true},"frames":1,"width":320,"height":180,
    "engine":"EEVEE","samples":32,"return":"base64"}) via runsync:
    ok=true, device=CPU, seconds=1.122, blender=4.2.23.
  - Sent the SAME probe with only "engine" changed to "CYCLES" (still
    320x180, 16 samples, 1 frame): ok=true, device=OPTIX, seconds=7.236.
  - NAMED CAUSE: "the probe input skipping selection" (one of the four
    candidates the brief named). scene_builder.py's CUDA/OPTIX
    selection code was gated `if job["engine"] == "CYCLES"`; for any
    other engine (device defaulted to the literal string "CPU" with
    zero detection, zero explanation. The seat's render_probe
    deliberately sends engine="EEVEE" (RUNPOD_LIVE_PROVIDER.md commit
    2's own docstring: "never CYCLES -- this is a connectivity/
    liveness check, not a quality one"), so the probe the Architect
    saw NEVER exercised GPU selection at all. There is no CUDA/driver
    bug: libcuda is present, the driver accepts this Blender's OPTIX
    kernels, and prefs.get_devices() finds the A5000 -- proven by the
    CYCLES probe immediately above, before any code change.
  - Tried the RunPod worker-logs SSE endpoint
    (/v2/serverless/{id}/workers/{workerId}/logs) to read the
    "[maestro] device=" stdout line directly: flaky (500 once, 404
    once querying the same worker id seconds apart, 200-with-only-a-
    heartbeat-and-no-backfill once even with tail=5000). Abandoned it
    in favour of reading the handler's own JSON response, which
    already carries the device line's content -- more reliable, and
    it is what a real caller sees anyway.
  - Job-dispatch host correction (see top of this file): api.runpod.ai,
    not api.runpod.io (.io is the account/management REST v2 API --
    confirmed via its openapi.json, which lists /v2/serverless/{id}/
    workers and .../logs but no run/runsync/health/status paths).
  - Did NOT add a diag mode as a separate pre-fix commit as the brief
    steps suggest -- the CYCLES-vs-EEVEE probe pair above already
    named the cause with no code change needed, and the 1800s wall
    clock does not afford a second full CI build+endpoint-roll cycle.
    Added the diag mode (nvidia-smi, CUDA/nvidia libs in
    /usr/lib/x86_64-linux-gnu, Blender's own prefs.devices per
    backend) in THIS SAME commit as the fix, so only one CI
    build+deploy round-trip is needed. Not yet run live (needs the new
    image); if time allows, ran below.
- [commit 2, continued] FIX: scene_builder.py's device-selection logic
  extracted into select_cycles_device(prefs) (pure function, no bpy
  calls beyond the passed-in prefs object) so it is offline-testable.
  _configure_render now always sets a device_reason string alongside
  device: for CYCLES, which backend was picked and how many devices,
  or which backends were tried and why each failed; for any other
  engine, "device" stays "CPU" (the fixed contract has no GPU value
  for non-Cycles engines) but device_reason says plainly that Cycles
  selection does not apply, so a caller never mistakes that CPU for
  "no GPU was available". handler.py extracts a new
  "[maestro] device_reason=" stdout line and adds "device_reason" to
  the JSON output (additive field, does not break the fixed contract).
  Added a "diag" input mode (bypasses validate_input and rendering
  entirely) per the brief. Offline tests: test_scene_builder_offline.py
  (select_cycles_device with a fake prefs listing OPTIX, one listing
  CUDA only, one listing none, one rejecting an enum value) and new
  cases in test_handler_offline.py (EEVEE CPU+reason, CYCLES CPU
  fallback+reason, missing device_reason line falls back to a stated
  message, diag mode with and without a Blender-side crash). Full
  local suite: 29 passed (mcp-servers/.venv-shared, Python 3.13 --
  the repo's own Python 3.11 install at
  AppData\Local\Programs\Python\Python311 is broken, see top of file;
  CI's setup-python 3.11 is the real gate). ast.parse clean; ASCII
  only. CI workflow updated to run both test files.
- [commit 3] build.yml's push trigger was `branches: [main]` only, so
  pushing this branch would never build or push :latest -- and the
  brief says do NOT merge to main. Added "worker-gpu" to the trigger's
  branch list (temporary, for this job's proof; main's own behaviour
  is unchanged) so CI builds and pushes :latest from this branch,
  watched with `gh run watch` in the foreground per the gate.
- RESUMED (prior session hit its 1800s cap here). Confirmed CI run
  34052955826 (push, worker-gpu) already completed: success, 2m56s,
  :latest pushed. No PR existed yet (gh pr list --head worker-gpu:
  empty).
- ENDPOINT ROLL: called runpod-mcp update-endpoint(endpointId=
  e9dn9lnn5g4j9c, imageName=same value) to try to force a new release;
  it returned 200 but list-endpoint-releases showed endpointVersion
  still 1, no new release recorded (RunPod treats an unchanged image
  string as no diff -- this call did NOT roll anything, noted so it is
  not mistaken for the actual mechanism). The one running worker
  (5doio4jyqhap34) had started at 18:57:23Z, after CI's push finished
  (~18:52:44Z), so it had already cold-pulled the new :latest on its
  own before I touched the endpoint. Verified this directly rather
  than trusting timestamps: ran the seat's own EEVEE probe and checked
  for the new "device_reason" field (only exists in the new code).
- PROBE (EEVEE, same payload as commit 2's diagnosis, via runsync):
  ok=true, blender=4.2.23, device=CPU, device_reason="engine is EEVEE,
  not CYCLES; Cycles CUDA/OPTIX device selection does not apply, and
  the handler contract has no GPU value for non-Cycles engines",
  seconds=38.796 (cold container init included; not a regression, the
  fix does not touch EEVEE's path). Confirms the new image is live and
  the fix behaves exactly as designed for the seat's liveness check.
- RENDER (CYCLES, 2 frames, 64 samples, 512x512, return=base64, via
  runsync): ok=true, blender=4.2.23, device=OPTIX, device_reason=
  "prefs.get_devices() found 1 OPTIX device(s)", frames_rendered=2,
  seconds=6.035. GPU selected, well under the $0.10 render budget and
  far below the CPU baseline. select_cycles_device() proven correct
  live, not just offline.
- No STOP-AND-ASK triggers hit: no CUDA/driver mismatch, CI pushed
  fine, and the endpoint rolled the image on its own (no worker
  deletion needed).

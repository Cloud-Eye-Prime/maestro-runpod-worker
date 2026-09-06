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

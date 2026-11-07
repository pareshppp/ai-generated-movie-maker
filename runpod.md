# Runpod: LTX-2.5 ComfyUI pod

The operations reference for the GPU pod that runs ComfyUI + LTX-2.5. Last updated 2026-11-07.
Phase plan: [PLAN.md](PLAN.md). Code: `mm/comfy.py` (API client), `infra/` (pod scripts), `workflows/` (ComfyUI API workflows).

---

## 1. Quick reference

| What | Name / ID | Details | Cost |
|---|---|---|---|
| Pod | `ltx25-comfyui` / `sbydop2vxljwda` | RTX 6000 Ada 48 GB, 16 vCPU, 62 GB RAM, Secure Cloud, US-WA-1, host CUDA 13.2 | **$0.99/hr while running** |
| Network volume | `ltx25-models` / `h70f1jyt5h` | 150 GB STANDARD, US-WA-1, mounted at `/workspace` | ~$10.50/mo until deleted |
| Unrelated older volume | `okay_blue_cicada` / `98olwtdp3d` | 10 GB, AP-JP-1, not used by this project | small; delete if not needed |

| Service | Access | Auth |
|---|---|---|
| ComfyUI (UI + API) | https://sbydop2vxljwda-8188.proxy.runpod.net | Basic auth: user `comfy`, password = `COMFY_PASS` in local `.env` |
| JupyterLab | https://sbydop2vxljwda-8888.proxy.runpod.net | Token = pod env `JUPYTER_PASSWORD` (see console) |
| SSH (direct; scp/rsync work) | `ssh root@195.26.233.33 -p <port>` | Account SSH key (`~/.ssh/id_ed25519`) |
| SSH (proxy; shell only) | `ssh sbydop2vxljwda-64411182@ssh.runpod.io` | Same key |
| FileBrowser | **disabled** | — |

- **The SSH port changes on every container start, restart or reset.** Get the current one from the Runpod console, or with MCP `get-pod` (`ssh.direct.command`).
- Image: `runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda12.8`, from official template `cw3nka7d08`. The ComfyUI install on the volume has been **upgraded to v0.44.2** (see §4.3). torch is 2.10.0+cu128.
- Local `.env` (gitignored, mode 600) holds `COMFY_USER`, `COMFY_PASS` and other secrets. Never commit it.

---

## 2. Everyday tasks

### Start / stop (save money)
- **Stop** when idle: Runpod console → Pods → Stop, or MCP `pod-action {"action":"stop"}`. GPU billing ends. Everything on `/workspace` (models, ComfyUI, auth setup) stays, because it's on the network volume.
- **Start**: console → Start, or `pod-action {"action":"start"}`. A start can fail if that host's GPU has been rented out in the meantime. If so, create a new pod on the same volume (§3).
- The network volume bills until it is deleted, whether or not the pod is running.

### Use ComfyUI from the laptop
```bash
python -m mm.comfy stats                      # server info (checks that auth works)
python -m mm.comfy smoke [--seed N]           # T2V smoke test -> renders/smoke/*.mp4
python -m mm.comfy run workflows/X_api.json   # any API-format workflow
```
- Server URL: `$COMFY_URL`. The default is the pod's proxy URL.
- Credentials are read from `.env` automatically.

### Add / re-download models
```bash
scp -P <port> infra/download_models.sh root@195.26.233.33:/workspace/
ssh -p <port> root@195.26.233.33 'HF_TOKEN=hf_... bash /workspace/download_models.sh [--dev] [--no-nodes]'
```
- The script is idempotent: complete files are skipped.
- The HF token needs the LTX-2.5 terms accepted at https://huggingface.co/Lightricks/LTX-2.5.
- Prefer passing the token over stdin (`read -r HF_TOKEN`) so it never appears on a command line.
- Restart ComfyUI after adding custom nodes.

### Restart ComfyUI
- MCP `pod-action {"action":"restart"}` or console → Restart. This restarts the whole container, which takes about 30 s to come up. The SSH port changes.
- ComfyUI-Manager's restart endpoint isn't reachable through the API.

### Rotate the ComfyUI password
1. Generate a new password and its bcrypt hash locally: `python3 -c "import bcrypt,secrets; p=secrets.token_urlsafe(18); print(p); print(bcrypt.hashpw(p.encode(), bcrypt.gensalt(12)).decode())"`
2. Copy `infra/pod/setup_auth.sh` and `infra/pod/boot.sh` to `/workspace/infra-pod/` on the pod, then run `bash /workspace/infra-pod/setup_auth.sh '<hash>' comfy`.
3. Update `COMFY_PASS` in `.env`, then restart the pod.

---

## 3. Rebuild on a new pod (same volume)

Use this when the pod is lost, its GPU is gone, or you need another GPU type. Everything that matters is on volume `h70f1jyt5h`, so a new pod in **US-WA-1** comes up fully configured.

1. Check stock in US-WA-1: MCP `list-gpu-types` with `include:["AVAILABILITY"]`, `product:["POD"]`. A network volume only attaches to Secure Cloud pods in its own data center.
2. Create the pod (MCP `create-pod` body):
   ```json
   {"name": "ltx25-comfyui", "templateId": "cw3nka7d08", "cloud": "SECURE",
    "gpu": {"id": "NVIDIA RTX 6000 Ada Generation", "count": 1, "minCudaVersion": "12.8"},
    "dataCenterIds": ["US-WA-1"],
    "mounts": {"network": [{"volumeId": "h70f1jyt5h", "path": "/workspace"}]},
    "ports": ["8188/http", "8888/http", "22/tcp"],
    "entrypoint": ["bash", "/workspace/runpod-slim/boot.sh"], "cmd": [],
    "startSsh": true, "startJupyter": true}
   ```
   - Any 48 GB+ GPU in US-WA-1 works. L40S is the next choice.
   - `minCudaVersion: "12.8"` lets the pod land on CUDA 13.x hosts, which is where the stock is.
3. Verify:
   - `python -m mm.comfy stats` shows `comfyui_version` **0.44.2**.
   - The proxy URL without credentials returns **401**.
   - `python -m mm.comfy smoke` produces a clip.
4. Update the pod ID and URLs in this file, and `DEFAULT_URL` in `mm/comfy.py`. Each new pod has a new ID, so it gets a new proxy URL.

**Moving to another data center** means creating a new volume there and copying the data across. Alternatively, recreate it: run `download_models.sh --dev`, redo the ComfyUI upgrade (§4.3), then run `setup_auth.sh`.

---

## 4. How the pod is set up

### 4.1 Boot chain and request path
```
docker-init -> bash /workspace/runpod-slim/boot.sh      (pod entrypoint override)
                 ├─ PATH=/workspace/runpod-slim/shims:$PATH   (no-op `filebrowser`, so FileBrowser never starts)
                 ├─ caddy run (Caddyfile) &               -> 0.0.0.0:8188, basic_auth, reverse_proxy 127.0.0.1:8189
                 └─ exec /start.sh                         (image's own startup: SSH, Jupyter, ComfyUI)
                      └─ python main.py --listen 0.0.0.0 --port 8188 ... --listen 127.0.0.1 --port 8189
                                                           (values from comfyui_args.txt come last and win)

Browser / mm.comfy -> Runpod proxy (Cloudflare) -> Caddy :8188 (401 unless authed) -> ComfyUI 127.0.0.1:8189
```
- WebSockets pass through Caddy; a test upgrade returned 101.
- Caddy logs go to `/caddy.log` in the container. This file is lost on restart.

### 4.2 Files on the volume (`/workspace/runpod-slim/`)
| Path | Purpose | Source in repo |
|---|---|---|
| `boot.sh` | Entrypoint wrapper (above) | `infra/pod/boot.sh` |
| `bin/caddy` | Caddy 2.10.2, SHA-512 checked against the release checksums | installed by `infra/pod/setup_auth.sh` |
| `Caddyfile` | `basic_auth` (bcrypt hash only) + reverse proxy; mode 600 | written by `setup_auth.sh` |
| `shims/filebrowser` | No-op that disables FileBrowser | written by `setup_auth.sh` |
| `comfyui_args.txt` | `--listen 127.0.0.1`, `--port 8189` | written by `setup_auth.sh` |
| `ComfyUI/` | ComfyUI v0.44.2 git checkout plus `.venv-cu128` | image first boot + in-place upgrade |
| `ComfyUI/models/` | LTX-2.5 weights (§5) | `infra/download_models.sh` |
| `ComfyUI/custom_nodes/ComfyUI-LTXVideo` | Lightricks' nodes and example workflows | `download_models.sh` |

### 4.3 ComfyUI version: must be v0.32 or later for LTX-2.5 (currently v0.44.2)
- **Symptom on the image's v0.30.0:** `CLIPTextEncode` fails with `ValueError: not enough values to unpack (expected 4, got 1)`. v0.30.0 loads `gemma4-12b-with-proj` as a plain Gemma4 model without the LTX projection.
- **Where it was fixed upstream:** LTX-2.5 support landed in v0.32 (#15499). An LTX-2.5 VAE-decoder dtype fix landed in v0.44 (#17487).
- **Upgrade, done in place on the volume:**
  ```bash
  cd /workspace/runpod-slim/ComfyUI && git checkout v0.44.2
  .venv-cu128/bin/python -m pip install -r requirements.txt torch==2.10.0+cu128 torchvision==0.25.0+cu128
  ```
  The torch pin keeps the image's CUDA stack. Then restart the pod.
- **Image versions:** the newest Runpod image checked on 2026-11-07 was `runpod/comfyui:2.1.0-comfyuiv0.35.0-*`. That is new enough for LTX-2.5 but lacks the v0.44 fix.
- ⚠️ **Changing the pod image undoes the upgrade.** `/start.sh` rsyncs ComfyUI core from the image when the image's bundle manifest (`.runpod-bundle-version`) differs. Models, custom_nodes, user data and the venv are kept. After any image change, check `comfyui_version` and redo the upgrade if needed.

---

## 5. Models on the volume (~58 GB, from `Lightricks/LTX-2.5`)

| Folder | File | Size |
|---|---|---|
| diffusion_models | `ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` (iteration) | 21.5 GB |
| diffusion_models | `ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors` (final renders) | 21.5 GB |
| text_encoders | `gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` | 15.4 GB |
| vae | `ltx-2.5-video-vae-bf16.safetensors`, `ltx-2.5-audio-vae-bf16.safetensors` | 1.5 + 0.4 GB |
| latent_upscale_models | `ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0`, `...-temporal-upscaler-x2-bf16-1.0` | 1.0 + 0.3 GB |
| model_patches | `ltx-2.5-duration-head-bf16.safetensors` | ~0 |

- The repo's folder layout matches ComfyUI's `models/` folders, so `hf download --local-dir models/` puts files in place.
- **Not downloaded:**
  - bf16 transformers: 42 GB each, too big for 48 GB of VRAM alongside everything else.
  - nvfp4: needs a Blackwell GPU.
  - `loras/ltx-2.5-22b-distilled-lora-450-bf16`: 8.9 GB, not needed yet.
- The official example workflows are in `custom_nodes/ComfyUI-LTXVideo/example_workflows/2.5/` (T2V/I2V, A2V, T2A, IC-LoRA, V2V upscale, HDR).

---

## 6. Workflows and smoke test

- `workflows/t2v_smoke_api.json`: the official `LTX-2.5_T2V_I2V_Single_Stage_Distilled` example, flattened from its subgraphs into the API format. The original is in `workflows/reference/`.
  - Changes from the original: int8 transformer and text encoder; image conditioning removed; cloud Gemma-API prompt enhancer removed.
  - Settings: 960×544, 121 frames (= 1 + 8k, 5 s at 24 fps), 8-step distilled manual sigmas, `euler_ancestral`, cfg 1.0.
  - Output: tiled VAE decode + audio VAE decode → `CreateVideo` → `SaveVideo`.
- **Smoke result (2026-11-07):** H.264 960×544, 24 fps, 121 frames (5.04 s), AAC 48 kHz audio (mean -24 dB). Clean anime cel-shading that followed the prompt. Took **154 s end to end, cold**, including loading ~38 GB of weights from the network volume.
- Validate a workflow before queueing by checking its node inputs against the pod's node definitions, which `GET /object_info` returns. ComfyUI's error messages on `/prompt` are less clear.

---

## 7. Gotchas and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `CLIPTextEncode`: "not enough values to unpack (expected 4, got 1)" | ComfyUI older than v0.32 can't load the LTX-2.5 Gemma4 encoder | Upgrade ComfyUI (§4.3) |
| API calls get `403` "error code: 1010" | Cloudflare at the Runpod proxy blocks Python-urllib's default User-Agent | Send a custom User-Agent (`mm/comfy.py` uses `mm-comfy/0.1`) |
| Pod `args` set, but the wrapper never runs | Image ENTRYPOINT is `/start.sh`; a CMD just becomes its arguments | Override **`entrypoint`**, not `args`/`cmd` |
| Proxy URL returns 502 for minutes after a config change | Nothing listening on 8188 (Caddy not started) or ComfyUI still booting | Check `/proc/1/cmdline`, `ps`, `/caddy.log`; normal boot is about 30 s |
| `update-pod` returns at once, but the old container keeps running | The reset happens asynchronously, sometimes more than once | Poll the proxy URL or `get-pod` (`runtime.uptime`) |
| SSH "Connection refused" | Port changed after a restart | Re-read with `get-pod` |
| SSH "Permission denied" from Claude Code's shell | Key is passphrase-protected; the shell's `SSH_AUTH_SOCK` agent is empty | `ssh-add` in a terminal; use an agent socket in `/tmp/ssh-*/agent.*` that lists the key |
| `create-pod` 400 "no instances available" | Stock moved between the read and the create | Re-read stock; pick a data center with stock *before* creating the volume |
| `hf download --token` warning | The token is visible in process listings | Export `HF_TOKEN` instead (the script does this now) |

---

## 8. Decisions and history

- **2026-11-07: GPU choice.** RTX 6000 Ada 48 GB (Ada; int8 distilled + dev both fit) at $0.99/hr Secure.
  - Rejected alternatives:
    - RTX A6000 ($0.59/hr): Ampere, no fp8, slower.
    - L40S ($1.09/hr): kept as the fallback.
    - RTX PRO 6000 96 GB ($2.49/hr): only needed for bf16.
  - Stock was LOW everywhere, and only hosts with CUDA 13.2 had capacity.
- **2026-11-07: data center.** The first try was US-IL-1. The create returned 400 because stock had moved, so the empty US-IL-1 volume (`yrsyxhp8sq`) was deleted and everything moved to **US-WA-1**.
- **2026-11-07: image.** Built our own pod from the official Runpod ComfyUI template rather than the community serverless template (below). ComfyUI was then upgraded on the volume.
- **2026-11-07: auth.** Caddy basic auth in front of ComfyUI, chosen over an SSH tunnel (keeps the URL usable from scripts) and over a ComfyUI login plugin (third-party code). FileBrowser was dropped.
- **Later:** a serverless endpoint built `FROM runpod/worker-comfyui` on the same volume, a pinned image, and `ltx-pipelines` / `ltx-trainer` for LoRA training (Phase 2).

---

## 9. Agent tooling

- Claude Code plugin `runpod@runpod` v2.0.0 (user scope), from marketplace `runpod/runpod-plugins-official`.
- MCP server `https://mcp.runpod.io/` (OAuth). If it shows *Needs authentication*, run `/mcp` → runpod → **Sign in with Runpod**.
- Useful MCP calls:
  - `get-pod sbydop2vxljwda`: state, SSH port, port mappings.
  - `pod-action` with start, stop or restart.
  - `stream-pod-logs`: boot logs.
  - `list-gpu-types` with `include:["AVAILABILITY"]` and `product:["POD"]`: stock.
  - `update-pod`: entrypoint and ports. This resets the container.

---

## Appendix: community serverless template (not used)
- Hub template `vavo/LTX2.5-serverless` (https://github.com/vavo/LTX2.5-serverless, MIT). Image `notrius/ltx-2.5-serverless:cu130`. The Docker Hub owner differs from the GitHub owner.
- Targets the Blackwell 180 GB class (B200) on CUDA 13.0 / 12.8. The README's minimum is 48 GB for distilled-int8.
- Container disk 120 GB. Network volume of 100 GB or more, mounted at `/runpod-volume` and aliased to `/workspace`. Workers min 0 / max 1. The first worker downloads the models.
- Env: `RUN_MODE=worker`, `PERSIST_WORKSPACE=true`, `LTX25_PRELOAD_VARIANT=distilled-int8`, `LTX25_PRELOAD_PROMPT_ENHANCER=true`, `HUGGINGFACE_ACCESS_TOKEN`. `AWS_*` are unset, so outputs come back inline as base64 (capped by `MAX_INLINE_VIDEO_MB=50`).
- API: POST `/run` or `/runsync` with `{"input": {"workflow": {...API-format...}, "images": [{"name": "source.png", "image": "data:image/png;base64,..."}]}}`. Health check: `{"input": {"health_check": true}}`. Outputs: `output.videos[]`, `output.images[]`.

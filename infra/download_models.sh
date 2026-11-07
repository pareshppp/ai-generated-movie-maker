#!/usr/bin/env bash
# Download LTX-2.5 weights (official Lightricks/LTX-2.5) into the ComfyUI models dir on the
# network volume, and install the ComfyUI-LTXVideo custom nodes. You can run it more than once:
# files that are already complete are skipped, and the nodes repo is pulled, not re-cloned.
#
# Run inside the pod (SSH or a Jupyter terminal):
#   export HF_TOKEN=hf_...      # read token; accept the terms at https://huggingface.co/Lightricks/LTX-2.5 first
#   bash download_models.sh             # distilled int8 set (~41 GB)
#   bash download_models.sh --dev       # + dev int8 transformer for final renders (+21.5 GB)
#   bash download_models.sh --no-nodes  # weights only
#
# The repo's folder layout (diffusion_models/, text_encoders/, vae/, ...) matches ComfyUI's
# models/ layout, so files are downloaded straight into place.
set -euo pipefail

COMFY_DIR="${COMFY_DIR:-/workspace/runpod-slim/ComfyUI}"
MODELS_DIR="$COMFY_DIR/models"
VENV_PY="${VENV_PY:-$COMFY_DIR/.venv-cu128/bin/python}"
REPO="Lightricks/LTX-2.5"

WITH_DEV=0
WITH_NODES=1
for arg in "$@"; do
  case "$arg" in
    --dev) WITH_DEV=1 ;;
    --no-nodes) WITH_NODES=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# Sizes (GB) are from the HF repo tree, 2026-11-07.
FILES=(
  "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"   # 21.50
  "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"           # 15.37
  "vae/ltx-2.5-video-vae-bf16.safetensors"                                              #  1.47
  "vae/ltx-2.5-audio-vae-bf16.safetensors"                                              #  0.36
  "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"       #  1.00
  "latent_upscale_models/ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors"      #  0.26
  "model_patches/ltx-2.5-duration-head-bf16.safetensors"                                #  ~0
)
if [[ $WITH_DEV -eq 1 ]]; then
  FILES+=("diffusion_models/ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors") # 21.50
fi
# Not downloaded: the bf16 transformers (42 GB each, too big for 48 GB VRAM) and nvfp4 (needs Blackwell).

[[ -d "$COMFY_DIR" ]] || { echo "ComfyUI not found at $COMFY_DIR (set COMFY_DIR)" >&2; exit 1; }
[[ -x "$VENV_PY" ]] || VENV_PY="$(command -v python3)"
[[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN is not set (gated repo)" >&2; exit 1; }

# hf CLI (huggingface_hub >= 0.34); hf_xet makes large downloads much faster.
if ! "$VENV_PY" -c "import huggingface_hub, sys; sys.exit(0 if tuple(map(int, huggingface_hub.__version__.split('.')[:2])) >= (0, 34) else 1)" 2>/dev/null; then
  "$VENV_PY" -m pip install -q -U "huggingface_hub[hf_xet]"
fi
HF="$(dirname "$VENV_PY")/hf"
[[ -x "$HF" ]] || HF="$(command -v hf)"

echo "== Free space on volume:"; df -h "$MODELS_DIR" | tail -1

echo "== Downloading ${#FILES[@]} files from $REPO into $MODELS_DIR"
"$HF" download "$REPO" "${FILES[@]}" \
  --local-dir "$MODELS_DIR"   # hf reads HF_TOKEN from the environment

echo "== Verifying"
missing=0
for f in "${FILES[@]}"; do
  if [[ -s "$MODELS_DIR/$f" ]]; then
    printf '  ok  %6s  %s\n' "$(du -h "$MODELS_DIR/$f" | cut -f1)" "$f"
  else
    echo "  MISSING  $f"; missing=1
  fi
done
[[ $missing -eq 0 ]] || { echo "some files are missing" >&2; exit 1; }

if [[ $WITH_NODES -eq 1 ]]; then
  NODES_DIR="$COMFY_DIR/custom_nodes/ComfyUI-LTXVideo"
  echo "== ComfyUI-LTXVideo nodes"
  if [[ -d "$NODES_DIR/.git" ]]; then
    git -C "$NODES_DIR" pull --ff-only
  else
    git clone --depth 1 https://github.com/Lightricks/ComfyUI-LTXVideo "$NODES_DIR"
  fi
  if [[ -f "$NODES_DIR/requirements.txt" ]]; then
    # Keep the image's torch stack: don't let requirements up/downgrade torch.
    TORCH_PIN="$("$VENV_PY" -c 'import torch; print(torch.__version__)')"
    "$VENV_PY" -m pip install -q -r "$NODES_DIR/requirements.txt" "torch==$TORCH_PIN"
  fi
  echo "Restart ComfyUI to load the new nodes (Manager -> Restart, or restart the pod)."
fi

echo "== Done. Free space now:"; df -h "$MODELS_DIR" | tail -1

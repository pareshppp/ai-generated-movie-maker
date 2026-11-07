#!/usr/bin/env python3
"""Fetch model files from Hugging Face into ComfyUI's models/ tree, by named set.

Run inside the pod with the ComfyUI venv python (it has huggingface_hub):
    HF_TOKEN=hf_... /workspace/runpod-slim/ComfyUI/.venv-cu128/bin/python fetch_models.py keyframe
    ... fetch_models.py --list

Idempotent: a file is skipped when the target already exists with the expected size.
Each entry: (target subdir under models/, repo id, path in repo, license note).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

MODELS = Path(os.environ.get("COMFY_MODELS", "/workspace/runpod-slim/ComfyUI/models"))

SETS: dict[str, list[tuple[str, str, str, str]]] = {
    # Keyframe image-model comparison (Phase 1). Licenses checked 2026-11-07; non-commercial OK for this project.
    "keyframe": [
        ("checkpoints", "cagliostrolab/animagine-xl-4.0", "animagine-xl-4.0-opt.safetensors", "OpenRAIL++"),
        ("checkpoints", "Laxhar/noobai-XL-1.1", "NoobAI-XL-v1.1.safetensors", "FAIPL-1.0-SD + no commercial use"),
        ("diffusion_models", "circlestone-labs/Anima", "split_files/diffusion_models/anima-aesthetic-v1.1.safetensors", "circlestone non-commercial"),
        ("diffusion_models", "circlestone-labs/Anima", "split_files/diffusion_models/anima-turbo-v1.1.safetensors", "circlestone non-commercial"),
        ("text_encoders", "circlestone-labs/Anima", "split_files/text_encoders/qwen_3_06b_base.safetensors", "circlestone non-commercial"),
        ("vae", "circlestone-labs/Anima", "split_files/vae/qwen_image_vae.safetensors", "Qwen-Image VAE"),
        ("diffusion_models", "Comfy-Org/Qwen-Image-2.1", "diffusion_models/qwen_image_2.1_int8_convrot.safetensors", "Qwen Research License"),
        ("text_encoders", "Comfy-Org/Qwen-Image-2.1", "text_encoders/qwen3vl_8b_int8_convrot.safetensors", "Qwen Research License"),
        ("vae", "Comfy-Org/Qwen-Image-2.1", "vae/qwen_image_2.1_vae_bf16.safetensors", "Qwen Research License"),
        ("loras", "Comfy-Org/Qwen-Image-2.1", "loras/p_q_image_2.1_8_steps_v0.1_comfyui_bf16.safetensors", "Qwen Research License"),
    ],
}


def fetch(entries, token: str | None) -> int:
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    staging = MODELS / ".hf-staging"
    failed = 0
    for subdir, repo, path, lic in entries:
        target = MODELS / subdir / Path(path).name
        try:
            info = api.get_paths_info(repo, [path])[0]
            size = getattr(info, "size", None)
            if getattr(info, "lfs", None):
                size = info.lfs.size
            if target.exists() and size and target.stat().st_size == size:
                print(f"skip  {subdir}/{target.name} (present, {size / 1e9:.2f} GB)")
                continue
            print(f"get   {repo}:{path} -> {subdir}/  [{lic}]", flush=True)
            local = hf_hub_download(repo, path, local_dir=staging, token=token)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(local, target)
            print(f"ok    {subdir}/{target.name} ({target.stat().st_size / 1e9:.2f} GB)")
        except Exception as e:  # keep going; report at the end
            failed += 1
            print(f"FAIL  {repo}:{path}: {e}", file=sys.stderr)
    shutil.rmtree(staging, ignore_errors=True)
    return failed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sets", nargs="*")
    p.add_argument("--list", action="store_true")
    a = p.parse_args()
    if a.list or not a.sets:
        for name, entries in SETS.items():
            print(name)
            for subdir, repo, path, lic in entries:
                print(f"  {subdir:18s} {repo}:{path}  [{lic}]")
        return 0
    entries = [e for s in a.sets for e in SETS[s]]
    failed = fetch(entries, os.environ.get("HF_TOKEN"))
    print(f"done, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

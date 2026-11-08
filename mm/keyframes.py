"""Keyframe (still image) generation via ComfyUI, with per-model API graph builders.

    python -m mm.keyframes compare [--models animagine,noobai,...] [--shots closeup,...]
        -> renders/keyframes/<model>/<shot>_s<seed>.png, timings.csv, index.html, per-shot contact sheets

Models (Phase 1 comparison; licenses noted, non-commercial is OK for this project):
    animagine   Animagine XL 4.0 Opt (SDXL)            OpenRAIL++
    noobai      NoobAI-XL 1.1 eps (SDXL)               FAIPL-1.0-SD + no commercial use
    anima       Anima Aesthetic v1.1 (2B, Cosmos)      circlestone non-commercial
    anima_turbo Anima Turbo v1.1 (cfg 1, 10 steps)     circlestone non-commercial
    qwen21      Qwen-Image-2.1 int8 (25 steps)         Qwen Research License
    qwen21_8s   Qwen-Image-2.1 int8 + 8-step LoRA      Qwen Research License
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from mm.comfy import ROOT, ComfyClient


@dataclass
class ModelSpec:
    key: str
    label: str
    kind: str                      # sdxl | anima | qwen21
    weights: str                   # ckpt (sdxl) or unet name
    steps: int
    cfg: float
    sampler: str
    scheduler: str
    prompt_style: str              # tags | natural
    prefix: str = ""
    suffix: str = ""
    negative: str = ""
    lora: str | None = None
    license: str = ""
    extra: dict = field(default_factory=dict)


ANIME_STYLE_NL = "Anime illustration, 2D cel-shaded Japanese animation style, clean line art, flat colors. "

MODELS: dict[str, ModelSpec] = {m.key: m for m in [
    ModelSpec("animagine", "Animagine XL 4.0 Opt", "sdxl", "animagine-xl-4.0-opt.safetensors",
              28, 5.0, "euler_ancestral", "normal", "tags",
              suffix=", masterpiece, high score, great score, absurdres",
              negative="lowres, bad anatomy, bad hands, text, error, missing finger, extra digits, fewer digits, cropped, "
                       "worst quality, low quality, low score, bad score, average score, signature, watermark, username, blurry",
              license="OpenRAIL++"),
    ModelSpec("noobai", "NoobAI-XL 1.1", "sdxl", "NoobAI-XL-v1.1.safetensors",
              28, 5.0, "euler_ancestral", "normal", "tags",
              prefix="masterpiece, best quality, newest, absurdres, highres, ",
              negative="worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, "
                       "mutated hands, watermark, blurry",
              license="FAIPL-1.0-SD + no commercial use"),
    ModelSpec("anima", "Anima Aesthetic v1.1", "anima", "anima-aesthetic-v1.1.safetensors",
              30, 4.0, "er_sde", "simple", "tags",
              prefix="masterpiece, best quality, safe, ",
              negative="worst quality, low quality, artist name, blurry, jpeg artifacts, chromatic aberration",
              license="circlestone non-commercial"),
    ModelSpec("anima_turbo", "Anima Turbo v1.1", "anima", "anima-turbo-v1.1.safetensors",
              10, 1.0, "euler", "simple", "tags",
              prefix="masterpiece, best quality, safe, ",
              negative="worst quality, low quality, blurry",
              license="circlestone non-commercial"),
    ModelSpec("qwen21", "Qwen-Image-2.1 int8", "qwen21", "qwen_image_2.1_int8_convrot.safetensors",
              25, 1.0, "euler", "simple", "natural", prefix=ANIME_STYLE_NL,
              negative="photorealistic, 3D render, photo, blurry, low quality, watermark, text",
              license="Qwen Research License"),
    ModelSpec("qwen21_8s", "Qwen-Image-2.1 int8 + 8-step LoRA", "qwen21", "qwen_image_2.1_int8_convrot.safetensors",
              8, 1.0, "euler", "simple", "natural", prefix=ANIME_STYLE_NL,
              negative="photorealistic, 3D render, photo, blurry, low quality, watermark, text",
              lora="p_q_image_2.1_8_steps_v0.1_comfyui_bf16.safetensors",
              license="Qwen Research License"),
]}


def build_graph(m: ModelSpec, prompt: str, width: int, height: int, seed: int, prefix: str) -> dict:
    """Return a ComfyUI API-format graph for one still image."""
    g: dict[str, dict] = {}
    if m.kind == "sdxl":
        g["ckpt"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": m.weights}}
        model, clip, vae = ["ckpt", 0], ["ckpt", 1], ["ckpt", 2]
        g["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": prompt}}
        g["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": m.negative}}
        pos, neg = ["pos", 0], ["neg", 0]
    elif m.kind == "anima":
        g["unet"] = {"class_type": "UNETLoader", "inputs": {"unet_name": m.weights, "weight_dtype": "default"}}
        g["clip"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b_base.safetensors",
                                                            "type": "stable_diffusion", "device": "default"}}
        g["vae"] = {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}}
        model, clip, vae = ["unet", 0], ["clip", 0], ["vae", 0]
        g["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": prompt}}
        g["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": m.negative}}
        pos, neg = ["pos", 0], ["neg", 0]
    elif m.kind == "qwen21":
        g["unet"] = {"class_type": "UNETLoader", "inputs": {"unet_name": m.weights, "weight_dtype": "default"}}
        g["clip"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                                                            "type": "qwen_image", "device": "default"}}
        g["vae"] = {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}}
        base_model = ["unet", 0]
        if m.lora:
            g["lora"] = {"class_type": "LoraLoaderModelOnly",
                         "inputs": {"model": base_model, "lora_name": m.lora, "strength_model": 1.0}}
            base_model = ["lora", 0]
        g["cache"] = {"class_type": "QwenImage21Cache", "inputs": {"model": base_model, "device": "auto", "dtype": "default"}}
        model, vae = ["cache", 0], ["vae", 0]
        g["te"] = {"class_type": "TextEncodeQwenImage21",
                   "inputs": {"clip": ["clip", 0], "prompt": prompt, "negative_prompt": m.negative, "resolution": 1024}}
        pos, neg = ["te", 0], ["te", 1]
    else:
        raise ValueError(m.kind)

    g["latent"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
    g["sample"] = {"class_type": "KSampler", "inputs": {
        "model": model, "positive": pos, "negative": neg, "latent_image": ["latent", 0],
        "seed": seed, "steps": m.steps, "cfg": m.cfg, "sampler_name": m.sampler, "scheduler": m.scheduler,
        "denoise": 1.0}}
    g["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": vae}}
    g["save"] = {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": prefix}}
    return g


def make_prompt(m: ModelSpec, shot: dict) -> str:
    return f"{m.prefix}{shot[m.prompt_style]}{m.suffix}"


def contact_sheet(images: list[Path], cols: int, out: Path, cell_w: int = 448) -> None:
    """Grid of images (row-major) via ffmpeg xstack; no labels (see index.html for those).
    Each input is scaled/padded independently, so mixed sizes and pixel formats (rgb24/rgba) are fine."""
    cell_h = cell_w * 9 // 16
    args = ["ffmpeg", "-v", "error", "-y"]
    for p in images:
        args += ["-i", str(p)]
    chains, labels = [], []
    for i in range(len(images)):
        chains.append(f"[{i}:v]format=rgb24,scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                      f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:white[c{i}]")
        labels.append(f"[c{i}]")
    if len(images) == 1:
        fc = chains[0] + ";[c0]null[out]"
    else:
        layout = "|".join(f"{(i % cols) * (cell_w + 4)}_{(i // cols) * (cell_h + 4)}" for i in range(len(images)))
        fc = ";".join(chains) + f";{''.join(labels)}xstack=inputs={len(images)}:layout={layout}:fill=white[out]"
    subprocess.run(args + ["-filter_complex", fc, "-map", "[out]", "-frames:v", "1", str(out)], check=True)


def write_index(out_dir: Path, cfg: dict, models: list[ModelSpec], rows: list[dict]) -> None:
    by = {(r["model"], r["shot"], r["seed"]): r for r in rows}
    h = ["<!doctype html><meta charset=utf-8><title>Keyframe model comparison</title>",
         "<style>body{font:14px system-ui;margin:16px}table{border-collapse:collapse}td,th{border:1px solid #ccc;"
         "padding:4px;vertical-align:top}img{width:320px;display:block}small{color:#666}</style>",
         "<h1>Keyframe model comparison</h1><table><tr><th>shot / seed</th>"]
    for m in models:
        h.append(f"<th>{html.escape(m.label)}<br><small>{m.steps} steps, cfg {m.cfg}, {m.sampler}<br>"
                 f"{html.escape(m.license)}</small></th>")
    h.append("</tr>")
    for shot in cfg["shots"]:
        for seed in cfg["seeds"]:
            h.append(f"<tr><td><b>{shot['id']}</b><br>seed {seed}</td>")
            for m in models:
                r = by.get((m.key, shot["id"], seed))
                if r and r.get("file"):
                    rel = Path(r["file"]).relative_to(out_dir)
                    h.append(f"<td><a href='{rel}'><img src='{rel}'></a><small>{r['seconds']}s</small></td>")
                else:
                    h.append(f"<td><small>{html.escape(r.get('error', 'missing') if r else 'missing')}</small></td>")
            h.append("</tr>")
    h.append("</table>")
    (out_dir / "index.html").write_text("\n".join(h))


def style_variants(model_key: str, cfg: dict, styles: list[str]) -> list[ModelSpec]:
    """One ModelSpec per style in a prompts file's 'styles' block (same model/seeds, style prefix + negative swapped)."""
    base = MODELS[model_key]
    return [replace(base, key=f"{model_key}_{st}", label=f"{base.label} · {st}", prompt_style="natural",
                    prefix=cfg["styles"][st]["keyframe_prefix"], suffix="",
                    negative=cfg["styles"][st]["keyframe_negative"]) for st in styles]


def compare(models: list[ModelSpec], shot_ids: list[str] | None, out_dir: Path, prompts: Path) -> None:
    cfg = json.loads(prompts.read_text())
    chars = cfg.get("characters", {})     # optional {name: description}; shots reference them as {name}
    for s in cfg["shots"]:
        for k in ("natural", "motion"):
            if k in s:
                s[k] = s[k].format(**chars) if chars else s[k]
    shots = [s for s in cfg["shots"] if not shot_ids or s["id"] in shot_ids]
    out_dir.mkdir(parents=True, exist_ok=True)
    c = ComfyClient()
    rows = []
    for m in models:                      # model-major order keeps each model loaded across its jobs
        for shot in shots:
            for seed in cfg["seeds"]:
                prompt = make_prompt(m, shot)
                if shot.get("negative_extra"):    # per-shot negative additions (e.g. "people, crowd")
                    m_shot = replace(m, negative=f"{m.negative}, {shot['negative_extra']}")
                else:
                    m_shot = m
                g = build_graph(m_shot, prompt, cfg["width"], cfg["height"], seed, f"kf/{m.key}/{shot['id']}_s{seed}")
                t0 = time.time()
                row = {"model": m.key, "shot": shot["id"], "seed": seed, "steps": m.steps, "cfg": m.cfg}
                try:
                    pid = c.queue(g)
                    entry = c.wait(pid, poll=2)
                    files = c.outputs(entry)
                    dest = out_dir / m.key
                    path = c.download(files[0], dest)
                    final = dest / f"{shot['id']}_s{seed}.png"
                    path.replace(final)
                    row.update(seconds=round(time.time() - t0, 1), file=str(final))
                    print(f"{m.key:12s} {shot['id']:12s} s{seed}  {row['seconds']:6.1f}s  {final}", flush=True)
                except Exception as e:
                    row.update(seconds=round(time.time() - t0, 1), error=str(e)[:300])
                    print(f"{m.key:12s} {shot['id']:12s} s{seed}  FAILED: {e}", flush=True)
                rows.append(row)

    with open(out_dir / "timings.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "shot", "seed", "steps", "cfg", "seconds", "file", "error"])
        w.writeheader()
        w.writerows(rows)
    for shot in shots:                    # one sheet per shot: rows = seeds, cols = models
        imgs, ok = [], True
        for seed in cfg["seeds"]:
            for m in models:
                p = out_dir / m.key / f"{shot['id']}_s{seed}.png"
                ok &= p.exists()
                imgs.append(p)
        if ok:
            contact_sheet(imgs, len(models), out_dir / f"sheet_{shot['id']}.png")
    write_index(out_dir, cfg, models, rows)
    print(f"index: {out_dir / 'index.html'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mm.keyframes")
    sub = p.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("compare")
    cp.add_argument("--models", default=",".join(MODELS))
    cp.add_argument("--shots", default="")
    cp.add_argument("--out", type=Path, default=ROOT / "renders" / "keyframes")
    cp.add_argument("--prompts", type=Path, default=ROOT / "prompts" / "keyframe_eval.json")
    sp = sub.add_parser("styles", help="same shots in each style of a prompts file's 'styles' block")
    sp.add_argument("--prompts", type=Path, default=ROOT / "prompts" / "donghua_eval.json")
    sp.add_argument("--model", default="qwen21_8s")
    sp.add_argument("--styles", default="", help="comma list; default: all styles in the file")
    sp.add_argument("--shots", default="")
    sp.add_argument("--out", type=Path, default=ROOT / "renders" / "keyframes_styles")
    a = p.parse_args(argv)
    shots = [s for s in a.shots.split(",") if s]
    if a.cmd == "compare":
        compare([MODELS[k] for k in a.models.split(",")], shots, a.out, a.prompts)
    else:
        cfg = json.loads(a.prompts.read_text())
        styles = [s for s in a.styles.split(",") if s] or list(cfg["styles"])
        compare(style_variants(a.model, cfg, styles), shots, a.out, a.prompts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

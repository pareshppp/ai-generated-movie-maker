"""Phase 1 LTX-2.5 experiments: T2V vs I2V, and one-factor-at-a-time parameter sweeps.

    python -m mm.experiments t2v_vs_i2v --kf-models anima,qwen21 [--shots closeup,two_shot] [--seed 42]
    python -m mm.experiments sweep --shot closeup --kf-model anima [--factors size,seconds,strength,model,cfg]

Outputs under renders/exp/<name>/: <variant>.mp4, frames_<variant>.png (first/middle/last frame strip),
results.csv (render seconds, seconds per output second, $ at the pod rate), index.html (videos side by side).
Keyframes come from renders/keyframes/<model>/<shot>_s<seed>.png (run `python -m mm.keyframes compare` first).
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

from mm.comfy import ROOT, ComfyClient
from mm.ltx import ClipSpec, render

POD_USD_PER_HR = 0.99          # RTX 6000 Ada, Secure, US-WA-1 (runpod.md)
PROMPTS = ROOT / "prompts" / "keyframe_eval.json"
KF_DIR = ROOT / "renders" / "keyframes"


def frame_strip(video: Path, out: Path, width: int = 320) -> None:
    """First, middle and last frame side by side."""
    n = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                            "stream=nb_read_packets", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, check=True).stdout.strip() or 1)
    sel = f"eq(n\\,0)+eq(n\\,{n // 2})+eq(n\\,{max(n - 1, 0)})"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
                    f"select='{sel}',scale={width}:-1,tile=3x1:padding=4:color=white", "-frames:v", "1", "-vsync", "0",
                    str(out)], check=True)


def run_variants(name: str, variants: list[tuple[str, ClipSpec, dict]]) -> Path:
    out = ROOT / "renders" / "exp" / name
    out.mkdir(parents=True, exist_ok=True)
    c = ComfyClient()
    uploaded: dict[str, str] = {}
    rows = []
    for label, spec, meta in variants:
        if meta.get("keyframe"):
            kf = Path(meta["keyframe"])
            if str(kf) not in uploaded:
                uploaded[str(kf)] = c.upload_image(kf, subfolder=f"mm/{name}")
            spec = replace(spec, image=uploaded[str(kf)])
        spec = replace(spec, prefix=f"exp/{name}/{label}")
        row = {"variant": label, **meta, **{k: v for k, v in asdict(spec).items() if k not in ("prompt", "negative", "prefix")},
               "frames": spec.frames}
        try:
            path, secs = render(c, spec, out / f"{label}.mp4")
            frame_strip(path, out / f"frames_{label}.png")
            row.update(render_s=round(secs, 1), s_per_output_s=round(secs / spec.seconds, 1),
                       usd=round(secs / 3600 * POD_USD_PER_HR, 4), file=path.name)
            print(f"{label:28s} {secs:6.0f}s  ({row['s_per_output_s']} s per output s, ${row['usd']})", flush=True)
        except Exception as e:
            row.update(error=str(e)[:300])
            print(f"{label:28s} FAILED: {e}", flush=True)
        rows.append(row)

    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("variant", "render_s", "s_per_output_s", "usd"), k))
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    h = [f"<!doctype html><meta charset=utf-8><title>{html.escape(name)}</title>",
         "<style>body{font:14px system-ui;margin:16px}.g{display:flex;flex-wrap:wrap;gap:12px}"
         ".c{width:480px}video{width:480px}small{color:#555}</style>",
         f"<h1>{html.escape(name)}</h1><div class=g>"]
    for r in rows:
        info = ", ".join(f"{k}={r[k]}" for k in ("model", "width", "height", "seconds", "img_strength", "cfg", "seed",
                                                  "render_s", "usd") if k in r and r[k] not in (None, ""))
        body = (f"<video src='{r['file']}' controls loop muted preload=metadata></video>" if r.get("file")
                else f"<p>FAILED: {html.escape(r.get('error', ''))}</p>")
        h.append(f"<div class=c><b>{html.escape(r['variant'])}</b><br>{body}<br><small>{html.escape(info)}</small></div>")
    h.append("</div>")
    (out / "index.html").write_text("\n".join(h))
    print(f"report: {out / 'index.html'}")
    return out


def shots(ids: list[str] | None) -> list[dict]:
    d = json.loads(PROMPTS.read_text())
    return [s for s in d["shots"] if not ids or s["id"] in ids]


def t2v_vs_i2v(kf_models: list[str], shot_ids: list[str] | None, seed: int, kf_seed: int) -> None:
    variants = []
    for shot in shots(shot_ids):
        base = ClipSpec(prompt=shot["motion"], seed=seed)
        variants.append((f"{shot['id']}__t2v", base, {"shot": shot["id"], "mode": "t2v"}))
        for m in kf_models:
            kf = KF_DIR / m / f"{shot['id']}_s{kf_seed}.png"
            variants.append((f"{shot['id']}__i2v_{m}", base, {"shot": shot["id"], "mode": "i2v", "kf_model": m,
                                                              "keyframe": str(kf)}))
    run_variants("t2v_vs_i2v", variants)


def sweep(shot_id: str, kf_model: str, kf_seed: int, seed: int, factors: list[str]) -> None:
    shot = shots([shot_id])[0]
    kf = str(KF_DIR / kf_model / f"{shot_id}_s{kf_seed}.png")
    base = ClipSpec(prompt=shot["motion"], seed=seed)             # 960x544, 5 s, distilled, strength 0.9
    meta = {"shot": shot_id, "kf_model": kf_model, "keyframe": kf}
    grid = {
        "size": [("720p", dict(width=1280, height=720))],
        "seconds": [("8s", dict(seconds=8.0)), ("10s", dict(seconds=10.0))],
        "strength": [("str0.7", dict(img_strength=0.7)), ("str1.0", dict(img_strength=1.0))],
        "model": [("dev30", dict(model="dev", steps=30))],
        "cfg": [("dev30_cfg2.5", dict(model="dev", steps=30, cfg=2.5)), ("dev30_cfg5", dict(model="dev", steps=30, cfg=5.0))],
    }
    variants = [("base", base, meta)]
    for f in factors:
        variants += [(lbl, replace(base, **kw), meta) for lbl, kw in grid[f]]
    run_variants(f"sweep_{shot_id}_{kf_model}", variants)


LIBRARY = ROOT / "prompts" / "library.json"

# Which eval shot (keyframe) each library fragment is tested on, and the scene text it is combined with.
LIB_SHOT = {"camera": "environment", "motion": "closeup", "audio": None}
AUDIO_SHOT = {"ambient_city": "closeup", "ambient_nature": "environment", "rain": "action_wide",
              "dialogue": "closeup", "sfx_door": "two_shot", "silence": "two_shot"}


def library_test(categories: list[str], kf_model: str, kf_seed: int, seed: int, size: tuple[int, int],
                 seconds: float) -> None:
    lib = json.loads(LIBRARY.read_text())
    style = lib["style"]["default"]
    by_id = {s["id"]: s for s in shots(None)}
    variants = []
    for cat in categories:
        for key, frag in lib[cat].items():
            shot = by_id[LIB_SHOT[cat] or AUDIO_SHOT[key]]
            text = frag["text"].replace("<LINE>", "Wait for me, I'm coming with you.")
            if cat == "camera":
                prompt = f"{style} {shot['natural']} {text} Sound of gentle waves and cicadas."
            elif cat == "motion":
                prompt = f"{style} {shot['natural']} {text} Static camera. Sound of wind."
            else:
                prompt = f"{style} {shot['natural']} Subtle natural movement. Static camera. {text}"
            spec = ClipSpec(prompt=prompt, negative=lib["negative"]["default"], width=size[0], height=size[1],
                            seconds=seconds, seed=seed)
            variants.append((f"{cat}__{key}", spec, {"shot": shot["id"], "category": cat, "fragment": key,
                                                     "kf_model": kf_model,
                                                     "keyframe": str(KF_DIR / kf_model / f"{shot['id']}_s{kf_seed}.png")}))
    run_variants("library_" + "_".join(categories), variants)


def style_test(prompts: Path, kf_root: Path, kf_model: str, styles: list[str] | None, modes: list[str],
               shot_ids: list[str] | None, seeds: list[int], kf_seed: int, size: tuple[int, int], seconds: float,
               name: str | None = None) -> None:
    """Same shots rendered per style (prompts file 'styles' block): I2V from that style's keyframe and/or T2V.

    Keyframe = the prompts file's optional "keyframe_picks" {style: {shot_id: path relative to kf_root}},
    else kf_root/<kf_model>_<style>/<shot>_s<kf_seed>.png. One take per seed in `seeds`."""
    cfg = json.loads(prompts.read_text())
    styles = styles or list(cfg["styles"])
    picks = cfg.get("keyframe_picks", {})
    variants = []
    for shot in cfg["shots"]:
        if shot_ids and shot["id"] not in shot_ids:
            continue
        for st in styles:
            sty = cfg["styles"][st]
            for seed in seeds:
                neg = sty["ltx_negative"] + (f", {shot['ltx_negative_extra']}" if shot.get("ltx_negative_extra") else "")
                spec = ClipSpec(prompt=sty["ltx_prefix"] + shot["motion"], negative=neg,
                                width=size[0], height=size[1], seconds=seconds, seed=seed)
                meta = {"shot": shot["id"], "style": st}
                tag = f"__s{seed}" if len(seeds) > 1 else ""
                if "i2v" in modes:
                    pick = picks.get(st, {}).get(shot["id"])
                    kf = kf_root / pick if pick else kf_root / f"{kf_model}_{st}" / f"{shot['id']}_s{kf_seed}.png"
                    if not kf.exists():
                        raise FileNotFoundError(kf)
                    variants.append((f"{shot['id']}__{st}__i2v{tag}", spec,
                                     {**meta, "mode": "i2v", "kf_model": kf_model, "keyframe": str(kf)}))
                if "t2v" in modes:
                    variants.append((f"{shot['id']}__{st}__t2v{tag}", spec, {**meta, "mode": "t2v"}))
    run_variants(name or f"styles_{prompts.stem}_{'_'.join(modes)}", variants)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mm.experiments")
    sub = p.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("t2v_vs_i2v")
    a1.add_argument("--kf-models", required=True)
    a1.add_argument("--shots", default="")
    a2 = sub.add_parser("sweep")
    a2.add_argument("--shot", required=True)
    a2.add_argument("--kf-model", required=True)
    a2.add_argument("--factors", default="size,seconds,strength,model,cfg")
    a3 = sub.add_parser("library")
    a3.add_argument("--categories", default="camera,motion,audio")
    a3.add_argument("--kf-model", default="qwen21_8s")
    a3.add_argument("--size", default="960x544")
    a3.add_argument("--seconds", type=float, default=8.0)
    a4 = sub.add_parser("styles", help="same shots per style (I2V from per-style keyframes and/or T2V)")
    a4.add_argument("--prompts", type=Path, default=ROOT / "prompts" / "donghua_eval.json")
    a4.add_argument("--kf-root", type=Path, default=ROOT / "renders" / "keyframes_styles")
    a4.add_argument("--kf-model", default="qwen21_8s")
    a4.add_argument("--styles", default="")
    a4.add_argument("--modes", default="i2v")
    a4.add_argument("--shots", default="")
    a4.add_argument("--size", default="1280x720")
    a4.add_argument("--seconds", type=float, default=8.0)
    a4.add_argument("--seeds", default="", help="comma list of LTX seeds, one take each (default: --seed)")
    a4.add_argument("--name", default=None, help="output dir name under renders/exp")
    for sp in (a1, a2, a3, a4):
        sp.add_argument("--seed", type=int, default=42)
        sp.add_argument("--kf-seed", type=int, default=1001)
    a = p.parse_args(argv)
    if a.cmd == "styles":
        w, h = map(int, a.size.lower().split("x"))
        style_test(a.prompts, a.kf_root, a.kf_model, [s for s in a.styles.split(",") if s], a.modes.split(","),
                   [s for s in a.shots.split(",") if s], [int(x) for x in a.seeds.split(",") if x] or [a.seed],
                   a.kf_seed, (w, h), a.seconds, a.name)
    elif a.cmd == "t2v_vs_i2v":
        t2v_vs_i2v(a.kf_models.split(","), [s for s in a.shots.split(",") if s], a.seed, a.kf_seed)
    elif a.cmd == "sweep":
        sweep(a.shot, a.kf_model, a.kf_seed, a.seed, a.factors.split(","))
    else:
        w, h = map(int, a.size.lower().split("x"))
        library_test(a.categories.split(","), a.kf_model, a.kf_seed, a.seed, (w, h), a.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

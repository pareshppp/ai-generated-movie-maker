"""LTX-2.5 video+audio graph builder (T2V / I2V, distilled / dev) and a single-clip CLI.

    python -m mm.ltx --prompt "..." [--image kf.png] [--model distilled|dev] [--seconds 5] [--size 960x544]

Graph = official LTX-2.5 single-stage example (workflows/reference/LTX-2.5_T2V_I2V_Single_Stage_Distilled.json),
flattened: int8 weights, no cloud prompt enhancer; I2V conditions frame 0 via LTXVImgToVideoInplace.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from mm.comfy import ROOT, ComfyClient

DISTILLED_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
UNET = {"distilled": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
        "dev": "ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors"}
DEFAULT_NEGATIVE = ("photorealistic, 3D render, CGI, live action, blurry, low quality, distorted face, deformed hands, "
                    "extra limbs, flickering, jitter, watermark, text, subtitles")


@dataclass
class ClipSpec:
    prompt: str
    negative: str = DEFAULT_NEGATIVE
    image: str | None = None          # ComfyUI input name (after upload) -> I2V; None -> T2V
    img_strength: float = 0.9         # 1.0 pins frame 0 exactly; lower lets the model reinterpret it
    img_compression: int = 18         # LTXVPreprocess: simulates video compression on the still (example uses 18)
    model: str = "distilled"          # distilled | dev
    width: int = 960
    height: int = 544
    seconds: float = 5.0
    fps: float = 24.0
    seed: int = 42
    sampler: str = "euler_ancestral"
    cfg: float | None = None          # default: 1.0 distilled, 3.5 dev
    steps: int = 30                   # dev only (distilled uses fixed 8-step sigmas)
    prefix: str = "ltx/clip"

    @property
    def frames(self) -> int:
        """LTX needs 1 + 8k frames (same formula as the official example)."""
        return 1 + int(self.fps * self.seconds // 8) * 8


def build_graph(s: ClipSpec) -> dict:
    cfg = s.cfg if s.cfg is not None else (1.0 if s.model == "distilled" else 3.5)
    g: dict[str, dict] = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET[s.model], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "type": "ltxv", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx-2.5-video-vae-bf16.safetensors"}},
        "avae": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx-2.5-audio-vae-bf16.safetensors"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": s.prompt}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": s.negative}},
        "cond": {"class_type": "LTXVConditioning", "inputs": {"positive": ["pos", 0], "negative": ["neg", 0],
                                                              "frame_rate": float(s.fps)}},
        "vlat": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": s.width, "height": s.height,
                                                                  "length": s.frames, "batch_size": 1}},
        "alat": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"audio_vae": ["avae", 0], "frames_number": s.frames,
                                                                  "frame_rate": int(round(s.fps)), "batch_size": 1}},
    }
    video_latent = ["vlat", 0]
    if s.image:
        g["img"] = {"class_type": "LoadImage", "inputs": {"image": s.image}}
        g["scale"] = {"class_type": "ImageScale", "inputs": {"image": ["img", 0], "upscale_method": "lanczos",
                                                             "width": s.width, "height": s.height, "crop": "center"}}
        g["prep"] = {"class_type": "LTXVPreprocess", "inputs": {"image": ["scale", 0], "img_compression": s.img_compression}}
        g["i2v"] = {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["vae", 0], "image": ["prep", 0],
                                                                      "latent": video_latent, "strength": s.img_strength,
                                                                      "bypass": False}}
        video_latent = ["i2v", 0]
    g["av"] = {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": video_latent, "audio_latent": ["alat", 0]}}

    if s.model == "distilled":
        g["sigmas"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": DISTILLED_SIGMAS}}
    else:
        g["sigmas"] = {"class_type": "LTXVScheduler", "inputs": {"steps": s.steps, "max_shift": 2.05, "base_shift": 0.95,
                                                                 "stretch": True, "terminal": 0.1, "latent": video_latent}}
    g["guider"] = {"class_type": "CFGGuider", "inputs": {"model": ["unet", 0], "positive": ["cond", 0],
                                                         "negative": ["cond", 1], "cfg": cfg}}
    g["ksel"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": s.sampler}}
    g["noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": s.seed}}
    g["sample"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["ksel", 0], "sigmas": ["sigmas", 0],
        "latent_image": ["av", 0]}}
    g["split"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sample", 0]}}
    g["vdec"] = {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["split", 0], "vae": ["vae", 0], "tile_size": 512,
                                                            "overlap": 64, "temporal_size": 128, "temporal_overlap": 32}}
    g["adec"] = {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["split", 1], "audio_vae": ["avae", 0]}}
    g["video"] = {"class_type": "CreateVideo", "inputs": {"images": ["vdec", 0], "audio": ["adec", 0],
                                                          "fps": float(s.fps), "bit_depth": 8}}
    g["save"] = {"class_type": "SaveVideo", "inputs": {"video": ["video", 0], "filename_prefix": s.prefix,
                                                       "format": "auto", "codec": "auto"}}
    return g


def render(c: ComfyClient, s: ClipSpec, dest: Path) -> tuple[Path, float]:
    """Queue one clip, wait, download to `dest` (a file path). Returns (path, seconds)."""
    t0 = time.time()
    entry = c.wait(c.queue(build_graph(s)), poll=5)
    files = [f for f in c.outputs(entry) if f["filename"].endswith((".mp4", ".webm", ".mov"))]
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = c.download(files[0], dest.parent)
    tmp.replace(dest)
    return dest, time.time() - t0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mm.ltx")
    p.add_argument("--prompt", required=True)
    p.add_argument("--negative", default=DEFAULT_NEGATIVE)
    p.add_argument("--image", type=Path, help="local keyframe -> I2V")
    p.add_argument("--img-strength", type=float, default=0.9)
    p.add_argument("--model", choices=["distilled", "dev"], default="distilled")
    p.add_argument("--size", default="960x544")
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=ROOT / "renders" / "ltx" / "clip.mp4")
    a = p.parse_args(argv)
    c = ComfyClient()
    w, h = map(int, a.size.lower().split("x"))
    image = c.upload_image(a.image) if a.image else None
    s = ClipSpec(prompt=a.prompt, negative=a.negative, image=image, img_strength=a.img_strength, model=a.model,
                 width=w, height=h, seconds=a.seconds, seed=a.seed)
    path, secs = render(c, s, a.out)
    print(f"saved {path} in {secs:.0f}s  ({asdict(s)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

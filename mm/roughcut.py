"""Rough cut: concatenate picked clips (video + audio) into one file with hard cuts.

    python -m mm.roughcut OUT.mp4 CLIP1.mp4 CLIP2.mp4@5.0 ... [--fade 0.04] [--lufs -16]

CLIP@SECONDS keeps only the first SECONDS of a clip (e.g. cut before late-clip face drift).

All clips must share resolution and fps (true for one render batch). A few-ms audio fade at
each clip's edges avoids clicks at the cuts. Clips without an audio stream get silence.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def probe(p: Path) -> tuple[float, bool]:
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                                     "-of", "json", str(p)], capture_output=True, check=True, text=True).stdout)
    return float(out["format"]["duration"]), any(s["codec_type"] == "audio" for s in out["streams"])


def parse_clip(arg: str) -> tuple[Path, float | None]:
    path, _, end = arg.rpartition("@") if "@" in arg else (arg, "", "")
    return Path(path), (float(end) if end else None)


def roughcut(out: Path, clips: list[str], fade: float = 0.04, lufs: float | None = None) -> Path:
    """clips: paths, optionally 'path@seconds' to keep only the head of a clip.
    lufs: per-clip loudnorm target (LTX clip loudness varies ~25 LU between shots); None = untouched."""
    args = ["ffmpeg", "-v", "error", "-y"]
    parsed = [parse_clip(c) for c in clips]
    for c, end in parsed:
        args += (["-t", str(end)] if end else []) + ["-i", str(c)]
    chains, pairs = [], []
    for i, (c, end) in enumerate(parsed):
        dur, has_audio = probe(c)
        dur = min(dur, end) if end else dur
        chains.append(f"[{i}:v]setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
        src = f"[{i}:a]" if has_audio else f"anullsrc=r=48000:cl=stereo,atrim=0:{dur},"
        norm = f"loudnorm=I={lufs}:TP=-1.5:LRA=11," if lufs is not None and has_audio else ""
        chains.append(f"{src}{norm}aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur},asetpts=PTS-STARTPTS,"
                      f"afade=t=in:d={fade},afade=t=out:st={max(dur - fade, 0)}:d={fade}[a{i}]")
        pairs.append(f"[v{i}][a{i}]")
    fc = ";".join(chains) + f";{''.join(pairs)}concat=n={len(clips)}:v=1:a=1[v][a]"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18",
                           "-preset", "medium", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
                   check=True)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mm.roughcut")
    p.add_argument("out", type=Path)
    p.add_argument("clips", nargs="+", help="clip.mp4 or clip.mp4@seconds")
    p.add_argument("--fade", type=float, default=0.04)
    p.add_argument("--lufs", type=float, default=None, help="normalize each clip to this loudness, e.g. -16")
    a = p.parse_args(argv)
    print(roughcut(a.out, a.clips, a.fade, a.lufs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

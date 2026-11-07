#!/usr/bin/env python3
"""Transcribe the audio of rendered clips with Whisper, to check generated dialogue (runs on the pod).

    /workspace/runpod-slim/ComfyUI/.venv-cu128/bin/python asr.py OUTPUT_DIR_OR_FILES... [--expect-json map.json]

--expect-json: {"<file stem substring>": "expected line", ...} -> prints a word-overlap score per clip.
Model: openai/whisper-large-v3-turbo (downloaded to the HF cache on the volume on first use).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("HF_HOME", "/workspace/hf-cache")


def load_audio(path: Path, sr: int = 16000):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def words(s: str) -> list[str]:
    return re.findall(r"[a-z']+", s.lower())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--expect-json", type=Path)
    p.add_argument("--model", default="openai/whisper-large-v3-turbo")
    a = p.parse_args()
    files = []
    for x in a.paths:
        files += sorted(x.rglob("*.mp4")) if x.is_dir() else [x]
    expect = json.loads(a.expect_json.read_text()) if a.expect_json else {}

    import torch
    from transformers import pipeline
    asr = pipeline("automatic-speech-recognition", model=a.model, torch_dtype=torch.float16,
                   device=0 if torch.cuda.is_available() else -1)
    results = []
    for f in files:
        audio = load_audio(f)
        text = asr({"raw": audio, "sampling_rate": 16000}, generate_kwargs={"language": "en"})["text"].strip()
        exp = next((v for k, v in expect.items() if k in f.stem), None)
        score = (round(difflib.SequenceMatcher(None, words(exp), words(text)).ratio(), 2) if exp else None)
        results.append({"file": f.name, "transcript": text, "expected": exp, "match": score})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

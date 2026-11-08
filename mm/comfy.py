"""Minimal ComfyUI HTTP client (stdlib only).

    python -m mm.comfy smoke                 # queue workflows/t2v_smoke_api.json, download result
    python -m mm.comfy run WORKFLOW.json     # same, any API-format workflow
    python -m mm.comfy stats                 # /system_stats

Server URL: $COMFY_URL (default: the Runpod proxy URL of the dev pod).
Basic auth: $COMFY_USER / $COMFY_PASS (read from the environment or the repo's .env).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://qxo2nouo34ye5y-8188.proxy.runpod.net"  # pod recreated per session: update this (runpod.md §3)


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines; existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class ComfyClient:
    def __init__(self, base_url: str | None = None, timeout: float = 60,
                 user: str | None = None, password: str | None = None):
        load_dotenv()
        self.base = (base_url or os.environ.get("COMFY_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex
        # The Runpod proxy (Cloudflare) 403s Python-urllib's default UA (error 1010).
        self.headers = {"Content-Type": "application/json", "User-Agent": "mm-comfy/0.1"}
        user = user or os.environ.get("COMFY_USER")
        password = password or os.environ.get("COMFY_PASS")
        if user and password:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            self.headers["Authorization"] = f"Basic {token}"

    def _req(self, method: str, path: str, body: dict | None = None) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:2000]}") from None

    def get_json(self, path: str):
        return json.loads(self._req("GET", path))

    def system_stats(self) -> dict:
        return self.get_json("/system_stats")

    def queue(self, workflow: dict) -> str:
        res = json.loads(self._req("POST", "/prompt", {"prompt": workflow, "client_id": self.client_id}))
        if res.get("node_errors"):
            raise RuntimeError(f"node errors: {json.dumps(res['node_errors'], indent=1)}")
        return res["prompt_id"]

    def wait(self, prompt_id: str, poll: float = 5, max_wait: float = 3600) -> dict:
        t0 = time.time()
        while time.time() - t0 < max_wait:
            hist = self.get_json(f"/history/{prompt_id}")
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error" or not status.get("completed", True):
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    raise RuntimeError(f"execution failed: {json.dumps(msgs, indent=1)[:3000]}")
                return entry
            q = self.get_json("/queue")
            running = any(item[1] == prompt_id for item in q.get("queue_running", []))
            print(f"  [{time.time() - t0:5.0f}s] {'running' if running else 'queued'}", flush=True)
            time.sleep(poll)
        raise TimeoutError(f"prompt {prompt_id} not done after {max_wait}s")

    def outputs(self, entry: dict) -> list[dict]:
        files = []
        for node_out in entry.get("outputs", {}).values():
            for key in ("videos", "images", "gifs", "audio"):
                files.extend(node_out.get(key, []))
        return files

    def download(self, f: dict, dest_dir: Path) -> Path:
        q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                                    "type": f.get("type", "output")})
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f["filename"]
        dest.write_bytes(self._req("GET", f"/view?{q}"))
        return dest

    def upload_image(self, path: Path, subfolder: str = "mm", overwrite: bool = True) -> str:
        """POST /upload/image (multipart). Returns the name to use in LoadImage ('subfolder/file')."""
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("subfolder", subfolder), ("overwrite", "true" if overwrite else "false"), ("type", "input")):
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.name}\"\r\n"
                     f"Content-Type: image/png\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        headers = {**self.headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
        req = urllib.request.Request(self.base + "/upload/image", data=b"".join(parts), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"upload -> {e.code}: {e.read().decode(errors='replace')[:500]}") from None
        return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]

    def run(self, workflow: dict, dest_dir: Path) -> list[Path]:
        pid = self.queue(workflow)
        print(f"queued prompt {pid} on {self.base}")
        t0 = time.time()
        entry = self.wait(pid)
        print(f"done in {time.time() - t0:.0f}s")
        return [self.download(f, dest_dir) for f in self.outputs(entry)]


def validate(graph: dict, object_info: dict) -> list[str]:
    """Offline check of an API graph against /object_info: unknown nodes, missing/unknown inputs, bad combo values.
    LoadImage's image list and dynamic V3 inputs (autogrow/matchtype) are not checked."""
    problems = []
    for nid, n in graph.items():
        spec = object_info.get(n["class_type"])
        if not spec:
            problems.append(f"{nid}: unknown node {n['class_type']}")
            continue
        req, opt = spec["input"].get("required", {}), spec["input"].get("optional", {})
        for k, v in req.items():
            if k not in n["inputs"] and not str(v[0]).startswith("COMFY_"):
                problems.append(f"{nid} {n['class_type']}: missing {k}")
        for k, val in n["inputs"].items():
            if k not in req and k not in opt:
                problems.append(f"{nid} {n['class_type']}: unknown input {k}")
                continue
            choices = (req.get(k) or opt.get(k))[0]
            if (isinstance(choices, list) and not isinstance(val, list) and val not in choices
                    and n["class_type"] != "LoadImage"):
                problems.append(f"{nid} {n['class_type']}: {k}={val!r} not in options")
            elif isinstance(val, list) and (len(val) != 2 or str(val[0]) not in graph):
                problems.append(f"{nid} {n['class_type']}: {k} links to missing node {val}")
    return problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mm.comfy")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    sub.add_parser("smoke")
    r = sub.add_parser("run")
    r.add_argument("workflow", type=Path)
    for sp in (sub.choices["smoke"], r):
        sp.add_argument("--out", type=Path, default=ROOT / "renders" / "smoke")
        sp.add_argument("--seed", type=int)
    a = p.parse_args(argv)

    c = ComfyClient()
    if a.cmd == "stats":
        print(json.dumps(c.system_stats(), indent=1))
        return 0
    wf_path = ROOT / "workflows" / "t2v_smoke_api.json" if a.cmd == "smoke" else a.workflow
    wf = json.loads(wf_path.read_text())
    if a.seed is not None:
        for node in wf.values():
            if node["class_type"] == "RandomNoise":
                node["inputs"]["noise_seed"] = a.seed
    for path in c.run(wf, a.out):
        print(f"saved {path} ({path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

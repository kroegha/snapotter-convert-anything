"""Phase 3: drive a running SnapOtter over its REST API and time every AI tool.

Runs in a container on the compose network (see Verify-Phase3.ps1):
    /opt/venv/bin/python -I /repo/scripts/blackwell/verify_tools.py \
        --base http://snapotter:1349 --env /secrets/.env --out /out/<mode> \
        [--setup] [--install]

--setup    First run only: signs in with SNAPOTTER_ADMIN_PASSWORD, rotates the
           forced first-login password to a new random one (written back into
           the .env as SNAPOTTER_ADMIN_PASSWORD, never printed), and creates an
           API key saved to api-key.txt beside the .env.
--install  Installs every AI bundle from the manifest's bundleRepo and waits.

Then runs each GPU tool twice on the repo's fixtures (cold, then warm), saves
the outputs under --out, and writes results.json with per-run timings and
wall-clock windows (for matching against an nvidia-smi log).
"""
import argparse
import io
import json
import os
import secrets
import sys
import time

import requests
from PIL import Image, ImageDraw

FIX = "/repo/tests/fixtures"
BUNDLES = [
    "background-removal", "face-detection", "object-eraser-colorize",
    "inpaint-hq", "upscale-enhance", "photo-restoration", "transcription",
]


def mask_for(path):
    with Image.open(path) as im:
        w, h = im.size
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rectangle([w * 0.4, h * 0.4, w * 0.6, h * 0.6], fill=255)
    buf = io.BytesIO()
    m.save(buf, "PNG")
    return buf.getvalue()


# (label, endpoint, files factory, settings) — label names the framework the
# tool is expected to exercise, so the GPU table reads per runtime.
def tool_matrix():
    portrait = f"{FIX}/image/valid/portrait-color.jpg"
    bw = f"{FIX}/image/valid/portrait-bw.jpeg"
    faces = f"{FIX}/image/valid/multi-face.webp"
    speech = f"{FIX}/audio/valid/speech-10s.wav"
    video = f"{FIX}/video/valid/hero.mov"
    f = lambda p: {"file": (os.path.basename(p), open(p, "rb"))}  # noqa: E731
    return [
        ("remove-background (onnx)", "image/remove-background", lambda: f(portrait), {}),
        ("upscale x4 Real-ESRGAN (torch)", "image/upscale", lambda: f(portrait), {"scale": 4, "model": "auto"}),
        ("enhance-faces GFPGAN (torch)", "image/enhance-faces", lambda: f(portrait), {"model": "gfpgan"}),
        ("enhance-faces CodeFormer", "image/enhance-faces", lambda: f(portrait), {"model": "codeformer"}),
        ("noise-removal quality (torch)", "image/noise-removal", lambda: f(portrait), {"tier": "quality"}),
        ("colorize DDColor (onnx)", "image/colorize", lambda: f(bw), {"model": "ddcolor"}),
        ("restore-photo (torch+onnx)", "image/restore-photo", lambda: f(bw), {}),
        ("erase-object (onnx/torch)", "image/erase-object",
         lambda: {"image": ("portrait.jpg", open(portrait, "rb")), "mask": ("mask.png", mask_for(portrait))}, None),
        ("ai-canvas-expand (diffusers)", "image/ai-canvas-expand", lambda: f(portrait), {"extendRight": 128}),
        ("blur-faces (mediapipe, CPU by design)", "image/blur-faces", lambda: f(faces), {}),
        ("transcribe-audio (ctranslate2)", "audio/transcribe-audio", lambda: f(speech), {"language": "en"}),
        ("auto-subtitles (ctranslate2)", "video/auto-subtitles", lambda: f(video), {"language": "auto"}),
    ]


class Client:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def url(self, p):
        return self.base + p

    def wait_job(self, job_id, timeout=1800):
        deadline = time.time() + timeout
        with self.s.get(self.url(f"/api/v1/jobs/{job_id}/progress"), stream=True, timeout=timeout) as r:
            r.raise_for_status()
            for raw in r.iter_lines(decode_unicode=True):
                if time.time() > deadline:
                    raise TimeoutError(job_id)
                if not raw or not raw.startswith("data:"):
                    continue
                ev = json.loads(raw[5:].strip())
                phase = ev.get("phase") or ev.get("status")
                if phase in ("complete", "completed"):
                    return ev
                if phase == "failed":
                    raise RuntimeError(ev.get("error") or json.dumps(ev)[:300])
        raise RuntimeError("progress stream ended without a terminal frame")


def read_env(path):
    out = {}
    for line in open(path, encoding="ascii"):
        if "=" in line:
            k, v = line.rstrip("\r\n").split("=", 1)
            out[k] = v
    return out


def write_env_value(path, key, value):
    lines = open(path, encoding="ascii").read().splitlines()
    lines = [f"{key}={value}" if l.startswith(f"{key}=") else l for l in lines]
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def login(base, user, password):
    r = requests.post(base + "/api/auth/login", json={"username": user, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()


def setup(base, env_path):
    env = read_env(env_path)
    data = login(base, "craig", env["SNAPOTTER_ADMIN_PASSWORD"])
    token = data["token"]
    # Default policy: upper, lower, digit and special. token_urlsafe gives
    # letters, digits, "-" and "_"; the suffix guarantees each class. No "$",
    # which compose would treat as interpolation in .env.
    new = secrets.token_urlsafe(24) + "Aa9-"
    r = requests.post(base + "/api/auth/change-password", headers={"Authorization": f"Bearer {token}"},
                      json={"currentPassword": env["SNAPOTTER_ADMIN_PASSWORD"], "newPassword": new}, timeout=30)
    if r.status_code >= 400:
        sys.exit(f"password change failed: HTTP {r.status_code} {r.text[:200]}")
    write_env_value(env_path, "SNAPOTTER_ADMIN_PASSWORD", new)
    token = login(base, "craig", new)["token"]
    r = requests.post(base + "/api/v1/api-keys", headers={"Authorization": f"Bearer {token}"},
                      json={"name": "claude-local"}, timeout=30)
    r.raise_for_status()
    key_path = os.path.join(os.path.dirname(env_path), "api-key.txt")
    with open(key_path, "w", encoding="ascii") as fh:
        fh.write(r.json()["key"])
    print("setup: first-login password rotated (stored in .env); API key saved to api-key.txt", flush=True)


def install_all(c):
    for bid in BUNDLES:
        states = {b["id"]: b for b in c.s.get(c.url("/api/v1/features"), timeout=30).json()["bundles"]}
        if states.get(bid, {}).get("status") == "installed":
            print(f"= {bid} already installed", flush=True)
            continue
        t0 = time.time()
        r = c.s.post(c.url(f"/api/v1/admin/features/{bid}/install"), timeout=60)
        if r.status_code >= 400:
            sys.exit(f"install {bid}: HTTP {r.status_code} {r.text[:300]}")
        while True:
            time.sleep(5)
            b = {x["id"]: x for x in c.s.get(c.url("/api/v1/features"), timeout=30).json()["bundles"]}[bid]
            if b["status"] == "installed":
                print(f"+ {bid} installed in {time.time() - t0:.0f}s", flush=True)
                break
            if b["status"] in ("error", "failed"):
                sys.exit(f"install {bid} failed: {json.dumps(b)[:400]}")


def run_matrix(c, out):
    os.makedirs(out, exist_ok=True)
    results = []
    for label, endpoint, files, settings in tool_matrix():
        for attempt in ("cold", "warm"):
            data = {} if settings is None else {"settings": json.dumps(settings)}
            start = time.time()
            rec = {"tool": label, "run": attempt, "start": start}
            try:
                r = c.s.post(c.url(f"/api/v1/tools/{endpoint}"), files=files(), data=data, timeout=1800)
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                body = r.json()
                if r.status_code == 202 or body.get("async"):
                    ev = c.wait_job(body["jobId"])
                    body = {**body, **(ev.get("result") or {})}
                rec["seconds"] = round(time.time() - start, 2)
                dl = body.get("downloadUrl")
                if dl:
                    blob = c.s.get(c.url(dl), timeout=300).content
                    name = f"{endpoint.replace('/', '_')}-{attempt}-{os.path.basename(dl)}"
                    with open(os.path.join(out, name), "wb") as fh:
                        fh.write(blob)
                    rec["output"] = name
                    rec["bytes"] = len(blob)
                rec["ok"] = True
            except Exception as e:  # keep going; report every tool
                rec["seconds"] = round(time.time() - start, 2)
                rec["ok"] = False
                rec["error"] = str(e)[:400]
            rec["end"] = time.time()
            results.append(rec)
            status = "OK " if rec["ok"] else "ERR"
            print(f"[{status}] {label:42s} {attempt:4s} {rec['seconds']:8.2f}s {rec.get('error', '')}", flush=True)
    with open(os.path.join(out, "results.json"), "w") as fh:
        json.dump(results, fh, indent=1)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://snapotter:1349")
    ap.add_argument("--env", default="/secrets/.env")
    ap.add_argument("--out", required=True)
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--install", action="store_true")
    a = ap.parse_args()
    if a.setup:
        setup(a.base, a.env)
    env = read_env(a.env)
    token = login(a.base, "craig", env["SNAPOTTER_ADMIN_PASSWORD"])["token"]
    c = Client(a.base, token)
    if a.install:
        install_all(c)
    res = run_matrix(c, a.out)
    bad = [r for r in res if not r["ok"]]
    print(f"MATRIX DONE: {len(res) - len(bad)}/{len(res)} runs OK", flush=True)


if __name__ == "__main__":
    main()

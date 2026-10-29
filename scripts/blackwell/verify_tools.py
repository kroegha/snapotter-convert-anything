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


def run_smoke(c, out):
    """Non-AI smoke test (PRD criterion 5) plus live probes for the fork's
    security fixes: each probe must be refused with a 4xx."""
    os.makedirs(out, exist_ok=True)
    img = f"{FIX}/image/valid/portrait-color.jpg"
    f = lambda p, field="file": {field: (os.path.basename(p), open(p, "rb"))}  # noqa: E731
    # S5: bytes with no signature the validator knows, accepted on the .tga /
    # RAW name alone. Before the fix ImageMagick auto-detected the content and
    # ran it as MVG (here: read /etc/passwd through the text: coder).
    mvg = (b"push graphic-context\nviewbox 0 0 64 64\n"
           b"image over 0,0 64,64 'text:/etc/passwd'\npop graphic-context\n")
    # An SVG renamed .tga is detected as SVG by content and takes the normal,
    # sanitised SVG path: it should convert, which shows the name is ignored.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>'
    playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://169.254.169.254/latest/meta-data.ts\n#EXT-X-ENDLIST\n"
    cases = [
        ("image convert jpg->webp", "image/convert", lambda: f(img), {"format": "webp"}, "ok"),
        ("image compress", "image/compress", lambda: f(img), {"quality": 70}, "ok"),
        ("video compress (light, 720p)", "video/compress-video", lambda: f(f"{FIX}/video/valid/media-30s.mp4"),
         {"quality": "light", "resolution": "720p"}, "ok"),
        ("pdf merge (2 files)", "pdf/merge-pdf",
         lambda: [("file", ("a.pdf", open(f"{FIX}/document/valid/test-3page.pdf", "rb"))),
                  ("file", ("b.pdf", open(f"{FIX}/document/valid/alt-2page.pdf", "rb")))], None, "ok"),
        ("image OCR (fast)", "image/ocr", lambda: f(f"{FIX}/image/valid/ocr-clean.png"), {"quality": "fast"}, "ok"),
        ("pdf OCR (fast)", "pdf/ocr-pdf", lambda: f(f"{FIX}/document/valid/ocr-scanned.pdf"), {"quality": "fast"}, "ok"),
        ("probe S5: MVG script named .tga", "image/convert",
         lambda: {"file": ("evil.tga", mvg)}, {"format": "png"}, "refused"),
        ("probe S5: MVG script named .cr2", "image/convert",
         lambda: {"file": ("evil.cr2", mvg)}, {"format": "png"}, "refused"),
        ("control: SVG named .tga (sanitised path)", "image/convert",
         lambda: {"file": ("plain.tga", svg)}, {"format": "png"}, "ok"),
        ("probe S7: HLS playlist upload", "video/compress-video",
         lambda: {"file": ("evil.m3u8", playlist)}, {"quality": "light"}, "refused"),
    ]
    results = []
    for label, endpoint, files, settings, expect in cases:
        data = {} if settings is None else {"settings": json.dumps(settings)}
        start = time.time()
        rec = {"case": label, "expect": expect}
        try:
            r = c.s.post(c.url(f"/api/v1/tools/{endpoint}"), files=files(), data=data, timeout=900)
            rec["status"] = r.status_code
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code < 400 and (r.status_code == 202 or body.get("async")):
                try:
                    ev = c.wait_job(body["jobId"])
                    body = {**body, **(ev.get("result") or {})}
                except RuntimeError as e:
                    rec["status"] = 422
                    rec["error"] = str(e)[:200]
            if expect == "ok":
                rec["ok"] = rec["status"] < 400 and bool(body.get("downloadUrl") or body.get("text") or body)
            else:
                rec["ok"] = 400 <= rec["status"] < 500
                rec.setdefault("error", (r.text or "")[:200])
        except Exception as e:
            rec["ok"] = False
            rec["error"] = str(e)[:300]
        rec["seconds"] = round(time.time() - start, 2)
        results.append(rec)
        print(f"[{'OK ' if rec['ok'] else 'ERR'}] {label:42s} HTTP {rec.get('status', '-')} {rec['seconds']:6.2f}s "
              f"{rec.get('error', '') if not rec['ok'] or expect == 'refused' else ''}", flush=True)
    with open(os.path.join(out, "smoke.json"), "w") as fh:
        json.dump(results, fh, indent=1)
    bad = [r for r in results if not r["ok"]]
    print(f"SMOKE DONE: {len(results) - len(bad)}/{len(results)} OK", flush=True)


CLIPS = "/clips"


def video_matrix():
    """(label, endpoint, files factory, settings, vmaf-reference clip or None).

    Every tool that encodes H.264 through videoCodecArgs, on the phase 2 test
    clips (PRD section 9). Tools whose output frames match the input (compress
    and convert at original resolution) carry the clip as the VMAF reference.
    """
    phone, long4k, screen = (f"{CLIPS}/{n}" for n in ("phone.mp4", "long4k.mp4", "screen.mp4"))
    srt = f"{FIX}/video/formats/tiny.srt"
    f = lambda p: {"file": (os.path.basename(p), open(p, "rb"))}  # noqa: E731
    imgs = [f"{FIX}/image/valid/{n}" for n in ("sample-photo.jpg", "portrait-color.jpg", "test-portrait.jpg")]
    return [
        ("compress light / phone", "video/compress-video", lambda: f(phone), {"quality": "light"}, phone),
        ("compress balanced / phone", "video/compress-video", lambda: f(phone), {"quality": "balanced"}, phone),
        ("compress strong / phone", "video/compress-video", lambda: f(phone), {"quality": "strong"}, phone),
        ("convert mp4 high / phone", "video/convert-video", lambda: f(phone), {"format": "mp4", "quality": "high"}, phone),
        ("convert mp4 balanced / phone", "video/convert-video", lambda: f(phone), {"format": "mp4", "quality": "balanced"}, phone),
        ("convert mp4 small / phone", "video/convert-video", lambda: f(phone), {"format": "mp4", "quality": "small"}, phone),
        ("convert mov / phone", "video/convert-video", lambda: f(phone), {"format": "mov"}, phone),
        ("convert mkv / phone", "video/convert-video", lambda: f(phone), {"format": "mkv"}, phone),
        ("trim 5-35 s / phone", "video/trim-video", lambda: f(phone), {"startS": 5, "endS": 35}, None),
        ("resize 720p / phone", "video/resize-video", lambda: f(phone), {"preset": "720p"}, None),
        ("burn subtitles / phone", "video/burn-subtitles",
         lambda: [("file", ("phone.mp4", open(phone, "rb"))), ("file", ("tiny.srt", open(srt, "rb")))], {}, None),
        ("merge phone + hero", "video/merge-videos",
         lambda: [("file", ("phone.mp4", open(phone, "rb"))), ("file", ("hero.mov", open(f"{FIX}/video/valid/hero.mov", "rb")))], None, None),
        ("gif to video", "video/gif-to-video", lambda: f(f"{FIX}/image/valid/animated.gif"), {"format": "mp4"}, None),
        ("images to video 1080p", "video/images-to-video",
         lambda: [("file", (os.path.basename(p), open(p, "rb"))) for p in imgs], {"resolution": "1080p"}, None),
        ("compress balanced / long 4K (10 min)", "video/compress-video", lambda: f(long4k), {"quality": "balanced"}, long4k),
        ("resize 1080p / long 4K (10 min)", "video/resize-video", lambda: f(long4k), {"preset": "1080p"}, None),
        ("convert mp4 balanced / screen (10 min)", "video/convert-video", lambda: f(screen), {"format": "mp4"}, screen),
    ]


def score_vmaf(vmaf_bin, dist, ref):
    """Pooled VMAF of dist against ref; long inputs are subsampled harder."""
    import re
    import subprocess
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", ref],
                           capture_output=True, text=True)
    dur = float(probe.stdout.strip() or 0)
    sub = 10 if dur > 120 else 2
    r = subprocess.run([vmaf_bin, "-hide_banner", "-i", dist, "-i", ref, "-lavfi",
                        "[0:v]format=yuv420p,setpts=PTS-STARTPTS[d];[1:v]format=yuv420p,setpts=PTS-STARTPTS[r];"
                        f"[d][r]libvmaf=n_threads=16:n_subsample={sub}", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"VMAF score: ([0-9.]+)", r.stderr)
    return float(m.group(1)) if m else None


def run_one(c, out, label, endpoint, files, settings, tag):
    data = {} if settings is None else {"settings": json.dumps(settings)}
    start = time.time()
    rec = {"tool": label, "run": tag, "start": start}
    try:
        r = c.s.post(c.url(f"/api/v1/tools/{endpoint}"), files=files(), data=data, timeout=7200)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        if r.status_code == 202 or body.get("async"):
            ev = c.wait_job(body["jobId"], timeout=7200)
            body = {**body, **(ev.get("result") or {})}
        rec["seconds"] = round(time.time() - start, 2)
        dl = body.get("downloadUrl")
        if dl:
            name = f"{label.replace(' ', '_').replace('/', '-')}-{tag}{os.path.splitext(dl)[1]}"
            with c.s.get(c.url(dl), timeout=1800, stream=True) as resp:
                resp.raise_for_status()
                with open(os.path.join(out, name), "wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
            rec["output"] = name
            rec["bytes"] = os.path.getsize(os.path.join(out, name))
        rec["ok"] = True
    except Exception as e:  # keep going; report every tool
        rec["seconds"] = round(time.time() - start, 2)
        rec["ok"] = False
        rec["error"] = str(e)[:400]
    rec["end"] = time.time()
    return rec


def run_video(c, out, tag, vmaf_bin):
    """PRD phase 2 criteria 2-4: every H.264 tool on the test clips, plus one
    AI job running alongside a long video job."""
    import threading
    os.makedirs(out, exist_ok=True)
    results = []
    for label, endpoint, files, settings, ref in video_matrix():
        rec = run_one(c, out, label, endpoint, files, settings, tag)
        if rec["ok"] and ref and vmaf_bin and rec.get("output"):
            rec["vmaf"] = score_vmaf(vmaf_bin, os.path.join(out, rec["output"]), ref)
        results.append(rec)
        print(f"[{'OK ' if rec['ok'] else 'ERR'}] {label:40s} {tag:8s} {rec['seconds']:8.2f}s "
              f"{rec.get('bytes', 0) / 1e6:9.1f} MB VMAF {rec.get('vmaf', '-')} {rec.get('error', '')}", flush=True)
        with open(os.path.join(out, "results-video.json"), "w") as fh:
            json.dump(results, fh, indent=1)

    # One AI job while a long encode runs: both must finish.
    portrait = f"{FIX}/image/valid/portrait-color.jpg"
    box = {}
    long_job = threading.Thread(target=lambda: box.setdefault("video", run_one(
        c, out, "parallel: compress long 4K", "video/compress-video",
        lambda: {"file": ("long4k.mp4", open(f"{CLIPS}/long4k.mp4", "rb"))}, {"quality": "strong"}, tag)))
    long_job.start()
    time.sleep(30)
    box["ai"] = run_one(c, out, "parallel: upscale x4", "image/upscale",
                        lambda: {"file": ("portrait.jpg", open(portrait, "rb"))}, {"scale": 4, "model": "auto"}, tag)
    long_job.join()
    for rec in (box["video"], box["ai"]):
        results.append(rec)
        print(f"[{'OK ' if rec['ok'] else 'ERR'}] {rec['tool']:40s} {tag:8s} {rec['seconds']:8.2f}s {rec.get('error', '')}",
              flush=True)
    with open(os.path.join(out, "results-video.json"), "w") as fh:
        json.dump(results, fh, indent=1)
    bad = [r for r in results if not r["ok"]]
    print(f"VIDEO DONE ({tag}): {len(results) - len(bad)}/{len(results)} OK", flush=True)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://snapotter:1349")
    ap.add_argument("--env", default="/secrets/.env")
    ap.add_argument("--out", required=True)
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="run the non-AI smoke test instead of the AI matrix")
    ap.add_argument("--video", metavar="TAG", help="run the phase 2 video matrix, labelled TAG (nvenc|software)")
    ap.add_argument("--vmaf", help="ffmpeg binary with libvmaf, for --video quality scores")
    a = ap.parse_args()
    if a.setup:
        setup(a.base, a.env)
    env = read_env(a.env)
    token = login(a.base, "craig", env["SNAPOTTER_ADMIN_PASSWORD"])["token"]
    c = Client(a.base, token)
    if a.install:
        install_all(c)
    if a.smoke:
        run_smoke(c, a.out)
        return
    if a.video:
        res = run_video(c, a.out, a.video, a.vmaf)
        sys.exit(1 if any(not r["ok"] for r in res) else 0)
    res = run_matrix(c, a.out)
    bad = [r for r in res if not r["ok"]]
    print(f"MATRIX DONE: {len(res) - len(bad)}/{len(res)} runs OK", flush=True)


if __name__ == "__main__":
    main()

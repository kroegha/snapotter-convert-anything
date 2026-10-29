"""Phase 0 spike: install every amd64 GPU AI bundle's packages into one venv,
with the CUDA 13.4 pins swapped in, the way build-bundle.sh installs them.

Reads the fork's docker/feature-manifest.json (mounted at /spike/manifest.json).
Writes /out/install-report.json with per-bundle results and continues past a
failing bundle so one run surfaces every problem.
"""
import json
import os
import shlex
import subprocess
import sys

MANIFEST = "/spike/manifest.json"
OUT = "/out/install-report.json"

TORCH_OLD = "torch==2.7.0+cu126 torchvision==0.22.0+cu126 --index-url https://download.pytorch.org/whl/cu126"
TORCH_NEW = (
    "torch==2.15.0+cu134 torchvision==0.29.2+cu134 "
    "--index-url https://download.pytorch.org/whl/cu134 "
    "--extra-index-url https://pypi.org/simple"
)
PIN_SWAPS = {
    TORCH_OLD: TORCH_NEW,
    "onnxruntime-gpu==1.20.2": "onnxruntime-gpu==1.31.1",
}
CONSTRAINT_SWAPS = {"onnxruntime==1.20.1": "onnxruntime==1.31.1"}

# Install order: torch-free first, then the torch bundles, transcription last.
ORDER = [
    "background-removal",
    "face-detection",
    "object-eraser-colorize",
    "inpaint-hq",
    "upscale-enhance",
    "photo-restoration",
    "transcription",
]


def pip(args, env):
    cmd = [sys.executable, "-m", "pip", "install"] + args
    print("  > " + " ".join(shlex.quote(a) for a in cmd), flush=True)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    tail = (r.stdout + r.stderr)[-4000:]
    if r.returncode != 0:
        print(tail, flush=True)
    return r.returncode == 0, tail


def main():
    with open(MANIFEST) as f:
        m = json.load(f)

    constraints = [CONSTRAINT_SWAPS.get(c, c) for c in m.get("constraints", [])]
    with open("/tmp/constraints.txt", "w") as f:
        f.write("\n".join(constraints) + "\n")
    env = dict(os.environ, PIP_CONSTRAINT="/tmp/constraints.txt")

    report = {"constraints": constraints, "bundles": {}}

    ok, tail = pip(list(m["basePackages"]), env)
    report["basePackages"] = {"ok": ok, "tail": "" if ok else tail}

    for bid in ORDER:
        b = m["bundles"][bid]
        flags = b.get("pipFlags", {})
        pkgs = list(b["packages"].get("common", [])) + list(b["packages"].get("amd64", []))
        steps = []
        bundle_ok = True
        print(f"=== {bid} ===", flush=True)
        for raw in pkgs:
            pkg = PIN_SWAPS.get(raw, raw)
            extra = next((v for k, v in flags.items() if pkg.startswith(k)), "")
            ok, tail = pip(shlex.split(f"{extra} {pkg}".strip()), env)
            steps.append({"package": pkg, "ok": ok, "tail": "" if ok else tail})
            bundle_ok &= ok
        for pkg in b.get("postInstall", []):
            ok, tail = pip(["--force-reinstall", pkg], env)
            steps.append({"package": f"postInstall {pkg}", "ok": ok, "tail": "" if ok else tail})
            bundle_ok &= ok
        ok, tail = pip(["--force-reinstall"] + list(m["basePackages"]), env)
        steps.append({"package": "re-pin basePackages", "ok": ok, "tail": "" if ok else tail})
        bundle_ok &= ok
        report["bundles"][bid] = {"ok": bundle_ok, "steps": steps}

    # Mirror build-bundle.sh: keep the GPU ORT, drop a CPU ORT pulled in alongside.
    ok, tail = pip(["--no-deps", "--force-reinstall", "onnxruntime-gpu==1.31.1"], env)
    report["ort_gpu_reinstall"] = {"ok": ok, "tail": "" if ok else tail}
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "onnxruntime"], capture_output=True)
    ok, tail = pip(["--no-deps", "--force-reinstall", "onnxruntime-gpu==1.31.1"], env)

    chk = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
    report["pip_check"] = (chk.stdout + chk.stderr).strip()
    frz = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True)
    report["freeze"] = frz.stdout.splitlines()

    with open(OUT, "w") as f:
        json.dump(report, f, indent=1)
    failed = [k for k, v in report["bundles"].items() if not v["ok"]]
    print("INSTALL SUMMARY: " + ("all bundles installed" if not failed else "FAILED: " + ", ".join(failed)), flush=True)


if __name__ == "__main__":
    main()

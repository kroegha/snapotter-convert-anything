"""Phase 3 report: join tool timings with the nvidia-smi log and compare modes.

Usage: python3 -I gpu_report.py <phase3-dir> <tz-offset-hours>
Reads <dir>/gpu/results.json, <dir>/gpu/nvidia-smi.csv and, when present,
<dir>/cpu/results.json, and writes <dir>/report.md: per tool, warm and cold
seconds on GPU, peak GPU utilisation and memory during the GPU run, and the CPU
time and speed-up when a CPU run exists.
"""
import csv
import datetime as dt
import json
import os
import sys


def load(path):
    return json.load(open(path)) if os.path.exists(path) else []


def smi_samples(path, tz_hours):
    tz = dt.timezone(dt.timedelta(hours=tz_hours))
    out = []
    if not os.path.exists(path):
        return out
    for row in csv.reader(open(path, encoding="utf-8", errors="replace")):
        if len(row) < 3:
            continue
        try:
            t = dt.datetime.strptime(row[0].strip(), "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=tz).timestamp()
            out.append((t, float(row[1]), float(row[2])))
        except ValueError:
            continue
    return out


def main():
    root, tz_hours = sys.argv[1], float(sys.argv[2])
    gpu = load(os.path.join(root, "gpu", "results.json"))
    cpu = load(os.path.join(root, "cpu", "results.json"))
    smi = smi_samples(os.path.join(root, "gpu", "nvidia-smi.csv"), tz_hours)
    idle_mem = min((m for _, _, m in smi), default=0.0)

    def key(r):
        return (r["tool"], r["run"])

    cpu_by = {key(r): r for r in cpu}
    lines = [
        "| Tool | GPU cold (s) | GPU warm (s) | Peak GPU util | Peak VRAM above idle | CPU warm (s) | Speed-up | Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    tools = []
    for r in gpu:
        if r["tool"] not in tools:
            tools.append(r["tool"])
    for tool in tools:
        cold = next((r for r in gpu if key(r) == (tool, "cold")), None)
        warm = next((r for r in gpu if key(r) == (tool, "warm")), None)
        window = [s for s in smi if cold and warm and cold["start"] <= s[0] <= warm["end"]]
        util = max((u for _, u, _ in window), default=None)
        vram = max((m for _, _, m in window), default=None)
        cw = cpu_by.get((tool, "warm"))
        speed = (cw["seconds"] / warm["seconds"]) if (cw and cw.get("ok") and warm and warm.get("ok") and warm["seconds"]) else None
        ok = all(r and r.get("ok") for r in (cold, warm))
        err = "" if ok else (cold or {}).get("error") or (warm or {}).get("error") or ""
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            tool,
            f"{cold['seconds']:.1f}" if cold else "-",
            f"{warm['seconds']:.1f}" if warm else "-",
            f"{util:.0f}%" if util is not None else "-",
            f"{(vram - idle_mem) / 1024:.1f} GB" if vram is not None else "-",
            f"{cw['seconds']:.1f}" if cw and cw.get("ok") else ("-" if not cw else "ERR"),
            f"{speed:.1f}x" if speed else "-",
            "OK" if ok else f"FAIL: {err[:80]}",
        ))
    with open(os.path.join(root, "report.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

"""Phase 2 report: NVENC vs software per video tool.

Usage: python3 -I video_report.py <phase3/video dir> <tz-offset-hours>
Reads nvenc/results-video.json, software/results-video.json and
nvenc/nvidia-smi.csv; writes report.md with time, speed-up, size ratio, VMAF
for both runs and the peak NVENC encoder utilisation during each tool.
"""
import csv
import datetime as dt
import json
import os
import sys


def load(path):
    return json.load(open(path)) if os.path.exists(path) else []


def smi(path, tz_hours):
    tz = dt.timezone(dt.timedelta(hours=tz_hours))
    rows = []
    if not os.path.exists(path):
        return rows
    for row in csv.reader(open(path, encoding="utf-8", errors="replace")):
        if len(row) < 4:
            continue
        try:
            t = dt.datetime.strptime(row[0].strip(), "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=tz).timestamp()
            rows.append((t, float(row[1]), float(row[2])))
        except ValueError:
            continue
    return rows


def main():
    root, tz = sys.argv[1], float(sys.argv[2])
    nv = load(os.path.join(root, "nvenc", "results-video.json"))
    sw = {r["tool"]: r for r in load(os.path.join(root, "software", "results-video.json"))}
    samples = smi(os.path.join(root, "nvenc", "nvidia-smi.csv"), tz)
    lines = [
        "| Tool | NVENC (s) | Software (s) | Speed-up | Size NVENC / software | VMAF NVENC | VMAF software | Peak encoder util | Result |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in nv:
        s = sw.get(r["tool"])
        win = [x for x in samples if r["start"] <= x[0] <= r["end"]]
        enc = max((e for _, _, e in win), default=None)
        ok = r.get("ok") and (s is None or s.get("ok"))
        speed = s["seconds"] / r["seconds"] if (s and s.get("ok") and r.get("ok") and r["seconds"]) else None
        size = r["bytes"] / s["bytes"] if (s and r.get("bytes") and s.get("bytes")) else None
        fmt = lambda v, f: f.format(v) if v is not None else "-"  # noqa: E731
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["tool"], fmt(r.get("seconds"), "{:.1f}"), fmt(s.get("seconds") if s else None, "{:.1f}"),
            fmt(speed, "{:.1f}x"), fmt(size, "{:.2f}"), fmt(r.get("vmaf"), "{:.1f}"),
            fmt(s.get("vmaf") if s else None, "{:.1f}"), fmt(enc, "{:.0f}%"),
            "OK" if ok else "FAIL: " + ((r.get("error") or (s or {}).get("error") or "")[:80]),
        ))
    with open(os.path.join(root, "report.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

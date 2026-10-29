"""NVENC quality calibration (PRD phase 2, criterion 3).

For each test clip excerpt and each CRF the tools use, encode with the software
encoder at that CRF (the app's software row: -crf C -preset medium) and with
NVENC at CQ = C + offset for a range of offsets, under each NVENC config. Score
every output with VMAF against the excerpt itself. A (config, offset) passes a
CRF when, on every clip, NVENC scores no more than 1 VMAF point below software
and the file is no more than 15% larger.

Runs inside the NVENC image with the GPU. VMAF comes from a separate ffmpeg
build that has libvmaf (the previous image's static binary), passed as --vmaf.

Usage:
  python3 -I calibrate_nvenc.py --clips /clips --out /out --vmaf /tools/ffmpeg-vmaf [--codec h264]
Writes <out>/calibration-<codec>.json (every run) and <out>/calibration-<codec>.md.
"""
import argparse
import json
import os
import re
import subprocess
import time

CRFS = [18, 20, 23, 28, 33]  # every H.264 CRF a SnapOtter tool passes
OFFSETS = range(0, 9)
EXCERPTS = [  # (file, start s, length s): photo pan + film for the camera clips
    ("phone.mp4", 20, 20),
    ("long4k.mp4", 10, 20),
    ("screen.mp4", 0, 20),
]
SOFTWARE = {"h264": "libx264", "hevc": "libx265"}
NVENC = {"h264": "h264_nvenc", "hevc": "hevc_nvenc"}
CONFIGS = {
    # What rateControlArgs emits today.
    "p5": ["-rc", "vbr", "-b:v", "0", "-preset", "p5"],
    # Slowest preset plus the quality features Blackwell's NVENC supports.
    "hq": ["-rc", "vbr", "-b:v", "0", "-preset", "p7", "-tune", "hq", "-multipass", "fullres",
           "-rc-lookahead", "32", "-spatial-aq", "1", "-temporal-aq", "1", "-bf", "3", "-b_ref_mode", "middle"],
}


def run(cmd):
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:8])}...: {r.stderr[-400:]}")
    return time.time() - t, r.stderr


def frame_rate(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                        "-of", "csv=p=0", src], capture_output=True, text=True)
    num, den = r.stdout.strip().strip(",").split("/")
    return float(num) / float(den)


def encode(src, ss, dur, out, video_args):
    """Encode the excerpt; retry once when the output is short (one 4K libx264
    run returned 0 with a truncated file)."""
    want = int(dur * frame_rate(src) * 0.98)
    for _ in range(2):
        secs, _ = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(ss), "-t", str(dur),
                       "-i", src, "-an", *video_args, "-pix_fmt", "yuv420p", out])
        r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", out],
                           capture_output=True, text=True)
        if int((r.stdout.strip() or "0").rstrip(",")) >= want:
            return secs, os.path.getsize(out)
        print(f"short output ({r.stdout.strip()} of {want} frames), retrying", flush=True)
    raise RuntimeError(f"encode of {src} kept producing short output")


def vmaf(vmaf_bin, dist, src, ss, dur):
    _, err = run([vmaf_bin, "-hide_banner", "-i", dist, "-ss", str(ss), "-t", str(dur), "-i", src,
                  "-lavfi", "[0:v]format=yuv420p,setpts=PTS-STARTPTS[d];[1:v]format=yuv420p,setpts=PTS-STARTPTS[r];"
                  "[d][r]libvmaf=n_threads=16:n_subsample=2", "-f", "null", "-"])
    m = re.search(r"VMAF score: ([0-9.]+)", err)
    if not m:
        raise RuntimeError("no VMAF score in output")
    return float(m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vmaf", required=True)
    ap.add_argument("--codec", default="h264", choices=sorted(SOFTWARE))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tmp = os.path.join(a.out, "tmp")
    os.makedirs(tmp, exist_ok=True)
    results_path = os.path.join(a.out, f"calibration-{a.codec}.json")
    results = json.load(open(results_path)) if os.path.exists(results_path) else []
    done = {(r["clip"], r["crf"], r["config"], r["offset"]) for r in results}

    def record(rec):
        results.append(rec)
        with open(results_path, "w") as fh:
            json.dump(results, fh, indent=1)
        print(json.dumps(rec), flush=True)

    for clip, ss, dur in EXCERPTS:
        src = os.path.join(a.clips, clip)
        for crf in CRFS:
            if (clip, crf, "software", 0) not in done:
                out = os.path.join(tmp, "o.mp4")
                secs, size = encode(src, ss, dur, out, ["-c:v", SOFTWARE[a.codec], "-crf", str(crf), "-preset", "medium"])
                record({"clip": clip, "crf": crf, "config": "software", "offset": 0, "seconds": round(secs, 2),
                        "bytes": size, "vmaf": round(vmaf(a.vmaf, out, src, ss, dur), 3)})
            for name, args in CONFIGS.items():
                for off in OFFSETS:
                    if (clip, crf, name, off) in done:
                        continue
                    cq = min(51, crf + off)
                    out = os.path.join(tmp, "o.mp4")
                    secs, size = encode(src, ss, dur, out, ["-c:v", NVENC[a.codec], "-cq", str(cq), *args])
                    record({"clip": clip, "crf": crf, "config": name, "offset": off, "seconds": round(secs, 2),
                            "bytes": size, "vmaf": round(vmaf(a.vmaf, out, src, ss, dur), 3)})

    summarize(results, a.out, a.codec)


def summarize(results, out, codec):
    sw = {(r["clip"], r["crf"]): r for r in results if r["config"] == "software"}
    clips = sorted({r["clip"] for r in results})
    lines = [f"# NVENC calibration ({codec})", "",
             "Pass = on every clip, VMAF >= software - 1 and size <= 1.15x software. "
             "Chosen offset = the largest passing offset whose worst clip is within 0.5 VMAF of software.", "",
             "| CRF | Config | Passing offsets | Chosen offset | Worst dVMAF | Worst size ratio | Mean NVENC/software time |",
             "|---|---|---|---|---|---|---|"]
    choice = {}
    for crf in CRFS:
        for name in CONFIGS:
            passing, stats = [], {}
            for off in OFFSETS:
                rows = [r for r in results if r["crf"] == crf and r["config"] == name and r["offset"] == off]
                if len(rows) != len(clips):
                    continue
                dv = [r["vmaf"] - sw[(r["clip"], crf)]["vmaf"] for r in rows]
                sr = [r["bytes"] / sw[(r["clip"], crf)]["bytes"] for r in rows]
                tr = [r["seconds"] / sw[(r["clip"], crf)]["seconds"] for r in rows]
                stats[off] = (min(dv), max(sr), sum(tr) / len(tr))
                if min(dv) >= -1.0 and max(sr) <= 1.15:
                    passing.append(off)
            # Equal quality: the largest offset (smallest files) whose worst clip
            # stays within 0.5 VMAF of software -- half the allowed point, as a
            # margin for content unlike the test clips.
            safe = [o for o in passing if stats[o][0] >= -0.5]
            pick = max(safe) if safe else (min(passing) if passing else None)
            if pick is not None and (crf not in choice or stats[pick][1] < choice[crf][2]):
                choice[crf] = (name, pick, stats[pick][1])
            s = stats.get(pick) if pick is not None else None
            lines.append(f"| {crf} | {name} | {', '.join(map(str, passing)) or 'none'} | "
                         f"{pick if pick is not None else '-'} | {s[0]:+.2f} | {s[1]:.2f} | {s[2]:.2f} |" if s else
                         f"| {crf} | {name} | none | - | - | - | - |")
    lines += ["", "Chosen per CRF (smallest size ratio among configs whose pick passes):", ""]
    for crf in CRFS:
        lines.append(f"- CRF {crf}: " + (f"{choice[crf][0]}, offset +{choice[crf][1]}" if crf in choice else "no config passes"))
    with open(os.path.join(out, f"calibration-{codec}.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()

---
name: snapotter
description: Convert, compress, edit or analyse image, video, audio, PDF and archive files on Craig's MSI Raider through the local SnapOtter stack (254 tools, NVENC video, GPU AI) and its snapotter CLI. Use this first, instead of local ffmpeg, ImageMagick, Python libraries or reading the file yourself, whenever a file needs converting, compressing, resizing, background removal, upscaling, OCR, transcription, PDF merge/split, zipping or similar and the file is on the Raider or can be put there.
---

# SnapOtter on the Raider

SnapOtter runs in Docker on the Raider (`http://127.0.0.1:1349`, RTX 5090). The
`snapotter` CLI drives it. Files never leave the Raider: inputs are read from
its disk and results are written next to them. Use it rather than doing the
job with local ffmpeg, ImageMagick or your own vision: it is GPU-accelerated
and gives Craig consistent results.

## Where to run it

- **Claude Code on the Raider:** run `snapotter ...` in the shell. The shim is
  `C:\Users\cfish\.local\bin\snapotter.cmd` (on PATH). From Git Bash call it as
  `snapotter.cmd`.
- **A cloud session linked to the Raider:** run the commands through the
  Raider's shell tool, which is Windows PowerShell 5.1. Wrap every call so
  PowerShell doesn't turn stderr into `NativeCommandError` noise, and read
  the exit code straight after:

      cmd /c "snapotter run compress-video clip.mp4 --set quality=strong --json --quiet 2>&1"; "exit=$LASTEXITCODE"

  Input files must be on the Raider: copy any cloud-side file to a Raider
  folder first, and bring the result back only if you need to look at it.
- **No Raider link:** SnapOtter is unavailable; say so rather than improvising.

## How to use it

1. **Find the tool.** `snapotter tools --search "<words>" --json` (or
   `--section image|video|audio|pdf|files`). Never guess tool ids.
2. **Read its options.** `snapotter describe <tool> --json` lists inputs,
   settings, choices, defaults and which are required. An input with
   `"multiple": false` takes one file: for several files add `--batch` (one
   job per file). Globs work: `photos\*` for "the photos in that folder".
3. **Run it.** `snapotter run <tool> <files...> --set name=value --json --quiet`
   - `--set` repeats; values are parsed as JSON (`true`, `2`, `[1,2]`), else text.
   - Generators that take no file (e.g. `qr-generate`) need `--out <file>`.
   - `pipeline steps.json <input>` chains image tools on one file.
4. **Check the result, then report.** Each job prints one JSON line on stdout:
   `ok`, `output`, `bytes`, `jobId`, `result`, `seconds` (server time; wall
   time is a little longer). `--batch` also prints `snapotter: batch: N/M OK`
   on stderr. Report the `output` path and any `result` fields that matter:
   `degraded` and `warnings` always; OCR `actualQuality` and `pages`;
   transcription `language`. For OCR and transcription, read the output
   `.txt` and show the text.

## Outputs

- Results land beside the input. Take the name from `output`, never assume
  it: most tools add a suffix (`clip_compressed.mp4`, `scan_ocr.txt`), but
  transcription writes `<name>.txt` and `remove-background` writes
  `<name>_mask.png`, which is the transparent cut-out (RGBA PNG), not a mask.
- An existing file is never overwritten: the CLI writes `name (1).ext`
  instead. Use `--force` only when the user asked to replace it.
- `--out <file or folder>` puts the result elsewhere (a folder with `--batch`).

## Choosing settings

- **"Compress strongly":** `quality=strong`. If the result is still too big,
  add `resolution=720p` or `480p` and say you did. A clip already under about
  300 kbps won't shrink much; tell the user rather than pushing quality down.
- **OCR (`ocr` for images, `ocr-pdf` for PDFs):** leave `quality` out or set
  `fast`; never request `balanced` or `best` (see Limits). Set `language`
  when known (auto, en, de, fr, es, zh, ja, ko; Korean needs an accurate tier,
  so it fails here). `pages` takes `1-3` or `1,3,5`; at most 50 pages per run,
  so split longer PDFs.
- **Background removal:** the default model suits people and products; use
  `model=birefnet-portrait` for portraits, `birefnet-hr-matting` for hair or
  fur. Photos without a clear subject (landscapes, scenes) come back almost
  fully transparent while still reporting `ok`: look at those results, or
  warn the user, before calling them done.

## Long jobs from a cloud session

The Raider shell call times out after about 60 s. From a cloud session,
always detach video jobs, PDFs over about 20 pages, and batches of more than
8 files; anything else runs inline (images take about 4 s each, a 30 s
480p clip about 2 s).

    snapotter run compress-video big.mp4 --set quality=strong --detach --json --quiet
    snapotter wait <jobId> --timeout 45 --out <folder of the input> --json --quiet

`wait` exits 6 when the job is still running at the timeout: call it again,
then report once it exits 0. `--detach --batch` prints one job id per file;
wait on each. On the Raider itself, plain `run` is fine.

## Exit codes

0 ok · 1 error · 2 bad usage (unknown tool, bad choice, missing required
option: the message names the fix, so correct and retry) · 3 the server
rejected the input (report its message; do not retry unchanged) · 4 the job
failed · 5 the stack is not reachable · 6 still running (`wait` again) ·
130 interrupted.

On exit 5, run `snapotter status --json`. If the stack is down, tell Craig;
restarting it is `docker compose up -d` in `C:\Users\cfish\win-projects\snapotter`,
and needs his go-ahead.

## Limits to tell the user about when relevant

- **OCR:** only the fast (Tesseract) tier works on this build; the accurate
  tiers report `unsupported-host`. Expect garbled text on photos of screens.
- **NVENC:** the GPU allows 8 encode sessions and the stack runs one media
  job at a time, so big video batches queue.
- **Screen recordings:** NVENC output is about 1.7x larger than the software
  encoder for screen content; if the smallest file matters, say so.
- **Tiny video/GIF frames** (under 145 px wide) fall back to the software
  encoder automatically: slower, same result.
- The key (`C:\Users\cfish\.config\snapotter\key`) can only run tools. Never
  read or print it, and never use the admin account for tool runs.

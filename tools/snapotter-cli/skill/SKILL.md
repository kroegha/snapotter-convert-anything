---
name: snapotter
description: Convert, compress, edit or analyse image, video, audio, PDF and archive files on Craig's MSI Raider with the local SnapOtter stack (254 tools, NVENC video, GPU AI) through the snapotter CLI. Use whenever a file needs converting, compressing, resizing, background removal, upscaling, OCR, transcription, PDF merge/split, zipping or similar, and the file is on the Raider or can be put there.
---

# SnapOtter on the Raider

SnapOtter runs in Docker on the Raider (`http://127.0.0.1:1349`, RTX 5090). The
`snapotter` CLI drives it. Files never leave the Raider: inputs are read from
its disk and results are written next to them.

## Where to run it

- **Claude Code on the Raider:** run `snapotter ...` in the shell. The shim is
  `C:\Users\cfish\.local\bin\snapotter.cmd` (on PATH).
- **A cloud session linked to the Raider:** run the same commands through the
  Raider's shell tool (PowerShell). If `snapotter` is not found there, call
  `C:\Users\cfish\.local\bin\snapotter.cmd` directly. Input files must be on the
  Raider: commit any cloud-side file to a Raider folder first, and stage the
  result back if you need to look at it.
- **No Raider link:** SnapOtter is unavailable; say so rather than improvising.

## How to use it

1. **Find the tool.** `snapotter tools --search "<words>" --json` (or
   `--section image|video|audio|pdf|files`). Never guess tool ids.
2. **Read its options.** `snapotter describe <tool> --json` lists inputs,
   settings, choices, defaults and which are required.
3. **Run it.** `snapotter run <tool> <files...> --set name=value --json --quiet`
   - `--set` repeats; values are parsed as JSON (`true`, `2`, `[1,2]`), else text.
   - Several inputs to a one-file tool: add `--batch` (one job per file).
   - Generators that take no file (e.g. `qr-generate`) need `--out <file>`.
   - `pipeline steps.json <input>` chains image tools on one file.
4. **Report** the `output` path from the JSON, plus any `result` extras
   (OCR engine, transcript language, page count). For text results (OCR,
   transcription) read the output `.txt` and show the text.

Always use `--json`; each result is one JSON object with `ok`, `output`,
`bytes`, `jobId`, `result`, `seconds`.

## Outputs

- Results land beside the input with SnapOtter's suffix (`clip_compressed.mp4`,
  `scan_ocr.txt`). An existing file is never overwritten: the CLI picks
  `name (1).ext`. Use `--force` only when the user asked to replace it.
- `--out <file or folder>` puts the result elsewhere.
- `remove-background` names its result `<name>_mask.png`; that file is the
  transparent cut-out (RGBA PNG), not a mask.

## Long jobs (video, large PDFs, AI on big batches)

The Raider shell call from a cloud session times out after about 60 s. For
anything likely to take longer (any video over a minute, 4K, big batches):

    snapotter run compress-video big.mp4 --set quality=strong --detach --json
    snapotter wait <jobId> --out <folder of the input> --json --quiet

Repeat `wait` until it returns. On the Raider itself, plain `run` is fine (it
waits, reconnecting to the progress stream as needed).

## Exit codes

0 ok · 1 error · 2 bad usage (unknown tool, bad choice, missing required
option: the message names the fix, so correct and retry) · 3 the server
rejected the input (report its message; do not retry unchanged) · 4 the job
failed · 5 the stack is not reachable · 130 interrupted.

On exit 5, run `snapotter status --json`. If the stack is down, tell Craig;
restarting it is `docker compose up -d` in
`C:\Users\cfish\win-projects\snapotter`, and needs his go-ahead.

## Limits to tell the user about when relevant

- **OCR:** only the fast (Tesseract) tier works on this build; the accurate
  tiers report `unsupported-host`. Check `result.actualQuality`.
- **NVENC:** the GPU allows 8 encode sessions; the stack runs one media job at
  a time, so big video batches queue.
- **Screen recordings:** NVENC output is about 1.7x larger than the software
  encoder for screen content; for a smallest-file screen capture, say so.
- **Tiny video/GIF frames** (under 145 px wide) fall back to the software
  encoder automatically; slower, same result.
- The key (`C:\Users\cfish\.config\snapotter\key`) can only run tools. Never
  print it, and never use the admin account for tool runs.

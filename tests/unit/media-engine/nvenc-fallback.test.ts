import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  rateControlArgs,
  softwareFallbackArgs,
} from "../../../packages/media-engine/src/encoders.js";

// Fork (Blackwell phase 2): an NVENC encode that fails at run time (frames
// under NVENC's 145 px minimum width, a lost session, a driver fault) retries
// once on the software encoder at the CRF the job asked for.

describe("softwareFallbackArgs", () => {
  const nvenc = (cq: string) => [
    "-c:v",
    "h264_nvenc",
    "-rc",
    "vbr",
    "-cq",
    cq,
    "-b:v",
    "0",
    "-preset",
    "p5",
  ];

  it("swaps an h264_nvenc run for libx264 at the CRF the calibrated cq came from", () => {
    for (const crf of [18, 20, 23, 28, 33]) {
      const cq = rateControlArgs("h264_nvenc", crf)[3];
      expect(
        softwareFallbackArgs(["-i", "in.gif", ...nvenc(cq), "-pix_fmt", "yuv420p", "out.mp4"]),
      ).toEqual([
        "-i",
        "in.gif",
        "-c:v",
        "libx264",
        "-crf",
        String(crf),
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "out.mp4",
      ]);
    }
  });

  it("maps hevc_nvenc 1:1 onto libx265", () => {
    const args = [
      "-i",
      "a",
      "-c:v",
      "hevc_nvenc",
      "-rc",
      "vbr",
      "-cq",
      "28",
      "-b:v",
      "0",
      "-preset",
      "p5",
      "o",
    ];
    expect(softwareFallbackArgs(args)).toEqual([
      "-i",
      "a",
      "-c:v",
      "libx265",
      "-crf",
      "28",
      "-preset",
      "medium",
      "o",
    ]);
  });

  it("returns null for anything videoCodecArgs did not build", () => {
    expect(softwareFallbackArgs(["-i", "a", "-c:v", "libx264", "-crf", "20", "o"])).toBeNull();
    expect(softwareFallbackArgs(["-i", "a", "-c:v", "h264_nvenc", "-cq", "20", "o"])).toBeNull();
    expect(
      softwareFallbackArgs(["-i", "a", "-c:v", "av1_nvenc", "-rc", "vbr", "-cq", "30", "o"]),
    ).toBeNull();
    expect(softwareFallbackArgs(["-i", "a", "-c", "copy", "o"])).toBeNull();
  });
});

describe("runFfmpeg NVENC retry", () => {
  const original = process.env.FFMPEG_PATH;
  afterEach(() => {
    if (original === undefined) delete process.env.FFMPEG_PATH;
    else process.env.FFMPEG_PATH = original;
    vi.resetModules();
  });

  /** A fake ffmpeg: fails like NVENC on h264_nvenc, logs its argv, else succeeds. */
  function fakeFfmpeg(): { bin: string; log: string } {
    const dir = mkdtempSync(join(tmpdir(), "fake-ffmpeg-"));
    const bin = join(dir, "ffmpeg");
    const log = join(dir, "argv.log");
    writeFileSync(
      bin,
      `#!/bin/sh\necho "$*" >> "${log}"\ncase "$*" in *h264_nvenc*) echo "[h264_nvenc @ 0x1] Frame Dimension less than the minimum supported value." >&2; exit 187;; esac\nexit 0\n`,
    );
    chmodSync(bin, 0o755);
    return { bin, log };
  }

  it("retries a failed NVENC encode once on libx264", async () => {
    const { bin, log } = fakeFfmpeg();
    process.env.FFMPEG_PATH = bin;
    vi.resetModules();
    const { runFfmpeg } = await import("../../../packages/media-engine/src/ffmpeg.js");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const cq = rateControlArgs("h264_nvenc", 20)[3];
    await runFfmpeg([
      "-i",
      "in.gif",
      "-c:v",
      "h264_nvenc",
      "-rc",
      "vbr",
      "-cq",
      cq,
      "-b:v",
      "0",
      "-preset",
      "p5",
      "out.mp4",
    ]);
    const runs = readFileSync(log, "utf8").trim().split("\n");
    expect(runs).toHaveLength(2);
    expect(runs[1]).toContain("-c:v libx264 -crf 20 -preset medium");
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });

  it("does not retry a failure that is not NVENC's", async () => {
    const { bin, log } = fakeFfmpeg();
    process.env.FFMPEG_PATH = bin;
    vi.resetModules();
    const { runFfmpeg } = await import("../../../packages/media-engine/src/ffmpeg.js");
    writeFileSync(
      bin,
      `#!/bin/sh\necho "$*" >> "${log}"\necho "moov atom not found" >&2\nexit 1\n`,
    );
    const cq = rateControlArgs("h264_nvenc", 20)[3];
    await expect(
      runFfmpeg([
        "-i",
        "bad.mp4",
        "-c:v",
        "h264_nvenc",
        "-rc",
        "vbr",
        "-cq",
        cq,
        "-b:v",
        "0",
        "-preset",
        "p5",
        "o.mp4",
      ]),
    ).rejects.toThrow(/moov atom not found/);
    expect(readFileSync(log, "utf8").trim().split("\n")).toHaveLength(1);
  });
});

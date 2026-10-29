/**
 * Fork security fix (review finding S7): every ffmpeg/ffprobe input is local,
 * so inputs are opened with -protocol_whitelist file,pipe. Modern ffmpeg
 * already confines a local playlist's nested opens to file/crypto/data, so
 * this is defence in depth that also makes the policy explicit.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import {
  INPUT_PROTOCOL_WHITELIST,
  probeMedia,
  REFERENCE_CONTAINER_RE,
  withInputProtocolWhitelist,
} from "../../../packages/media-engine/src/index.js";

describe("withInputProtocolWhitelist", () => {
  it("puts the whitelist in front of every -i", () => {
    expect(withInputProtocolWhitelist(["-i", "a.mp4", "-i", "b.png", "-filter_complex", "x", "out.mp4"])).toEqual([
      "-protocol_whitelist",
      INPUT_PROTOCOL_WHITELIST,
      "-i",
      "a.mp4",
      "-protocol_whitelist",
      INPUT_PROTOCOL_WHITELIST,
      "-i",
      "b.png",
      "-filter_complex",
      "x",
      "out.mp4",
    ]);
  });

  it("keeps per-input options next to their -i and does not double up", () => {
    const once = withInputProtocolWhitelist(["-ss", "0", "-i", "in", "out.png"]);
    expect(once).toEqual(["-ss", "0", "-protocol_whitelist", "file,pipe", "-i", "in", "out.png"]);
    expect(withInputProtocolWhitelist(once)).toEqual(once);
  });

  it("leaves output-only argument lists alone", () => {
    expect(withInputProtocolWhitelist(["-f", "lavfi", "out.mp4"])).toEqual(["-f", "lavfi", "out.mp4"]);
  });
});

describe("REFERENCE_CONTAINER_RE", () => {
  it.each(["hls", "hls,applehttp", "concat", "dash"])("matches %s", (name) => {
    expect(REFERENCE_CONTAINER_RE.test(name)).toBe(true);
  });
  it.each(["mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "mp3", "wav", "image2"])("does not match %s", (name) => {
    expect(REFERENCE_CONTAINER_RE.test(name)).toBe(false);
  });
});

function hasFfprobe(): boolean {
  try {
    execFileSync(process.env.FFPROBE_PATH || "ffprobe", ["-version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

const dirs: string[] = [];
afterAll(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true });
});

describe.skipIf(!hasFfprobe())("probeMedia with a real ffprobe", () => {
  it("never follows a playlist's http:// segment", async () => {
    const dir = mkdtempSync(join(tmpdir(), "protocol-whitelist-"));
    dirs.push(dir);
    const playlist = join(dir, "in-upload.m3u8");
    writeFileSync(
      playlist,
      "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://169.254.169.254/latest/meta-data.ts\n#EXT-X-ENDLIST\n",
    );
    const result = await probeMedia(playlist, { timeoutMs: 15_000 }).then(
      (info) => ({ ok: true as const, info }),
      (err: unknown) => ({ ok: false as const, err }),
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      // ffprobe refused the segment's protocol rather than fetching it.
      expect(String((result.err as Error).message)).toMatch(/Protocol 'http' not on whitelist 'file,pipe'/);
    }
  });
});

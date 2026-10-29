/**
 * Fork security fix (review finding S5): a file whose type was accepted on its
 * filename alone (validation `nameOnly`: a .tga without a TGA header, or a RAW
 * extension on bytes no RAW signature matches) must never reach an
 * ImageMagick invocation that picks the coder by sniffing the content. Text,
 * SVG or PostScript renamed to .tga / .dng would otherwise be decoded by the
 * TXT, rsvg or Ghostscript delegates, bypassing the magic-byte allowlist and
 * the SVG sanitiser.
 *
 * Shim decoders on a fresh PATH record their argv, so the assertions hold
 * whatever delegates this host's ImageMagick has.
 */
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, afterEach, describe, expect, it } from "vitest";
import { decodeToSharpCompat } from "../../../apps/api/src/lib/format-decoders.js";

const ORIGINAL_PATH = process.env.PATH;
const dirs: string[] = [];

afterEach(() => {
  process.env.PATH = ORIGINAL_PATH;
});

afterAll(() => {
  process.env.PATH = ORIGINAL_PATH;
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
});

function useShims(shims: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "decoder-unverified-"));
  dirs.push(dir);
  for (const [name, body] of Object.entries(shims)) {
    const file = join(dir, name);
    writeFileSync(file, `#!/bin/sh\n${body}\n`);
    chmodSync(file, 0o755);
  }
  process.env.PATH = dir;
  return dir;
}

const MAGICK_VERSION = 'if [ "$1" = "--version" ]; then echo "Version: ImageMagick 7"; exit 0; fi';

/** An ImageMagick shim that records each decode's argv, then rejects the file. */
function recordingMagick(logFile: string): string {
  return `${MAGICK_VERSION}
printf '%s\\n' "$@" >> "${logFile}"
echo "magick: improper image header" >&2
exit 1`;
}

const SVG_AS_TGA = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>',
);

describe("unverified uploads never reach content-sniffing ImageMagick (S5)", () => {
  it("TGA: ImageMagick is told the coder explicitly (tga:), never left to sniff", async () => {
    const dir = useShims({});
    const log = join(dir, "magick.log");
    useShimsInto(dir, { magick: recordingMagick(log) });

    await decodeToSharpCompat(SVG_AS_TGA, "tga", "tga").catch(() => undefined);

    const argv = readFileSync(log, "utf-8").split("\n");
    const input = argv.find((a) => a.includes("tga-in-"));
    expect(input, `magick argv: ${argv.join(" ")}`).toMatch(/^tga:/);
  });

  it("RAW: a name-only file stops after the RAW decoders reject it", async () => {
    const dir = useShims({});
    const log = join(dir, "magick.log");
    useShimsInto(dir, {
      dcraw_emu: 'echo "Cannot decode file raw-in.dng" >&2\nexit 1',
      exiftool: "exit 0",
      magick: recordingMagick(log),
    });

    const err = await decodeToSharpCompat(SVG_AS_TGA, "raw", "dng", {
      contentUnverified: true,
    }).catch((e) => e);

    expect(err).toBeInstanceOf(Error);
    expect(existsSync(log), "ImageMagick must not be invoked for an unverified RAW").toBe(false);
  });

  it("RAW: a verified file still gets the ImageMagick last resort", async () => {
    const dir = useShims({});
    const log = join(dir, "magick.log");
    useShimsInto(dir, {
      dcraw_emu: 'echo "Cannot decode file raw-in.dng" >&2\nexit 1',
      exiftool: "exit 0",
      magick: recordingMagick(log),
    });

    await decodeToSharpCompat(SVG_AS_TGA, "raw", "dng").catch(() => undefined);

    expect(existsSync(log)).toBe(true);
  });
});

function useShimsInto(dir: string, shims: Record<string, string>): void {
  for (const [name, body] of Object.entries(shims)) {
    const file = join(dir, name);
    writeFileSync(file, `#!/bin/sh\n${body}\n`);
    chmodSync(file, 0o755);
  }
}

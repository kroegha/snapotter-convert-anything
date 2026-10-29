/**
 * Fork security fix (review finding S7): ffmpeg and ffprobe open inputs through
 * libavformat's protocol layer, and with no whitelist an uploaded playlist
 * (an #EXTM3U file renamed .mp4) makes the HLS demuxer fetch http:// segments,
 * a blind SSRF that fires during the very first probe. Every input is local,
 * so only the file and pipe protocols are allowed.
 *
 * -protocol_whitelist is a per-input option, so it goes in front of every -i.
 * Nested opens (segments a demuxer requests) inherit it.
 */
export const INPUT_PROTOCOL_WHITELIST = "file,pipe";

export function withInputProtocolWhitelist(args: readonly string[]): string[] {
  const out: string[] = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "-i" && args[i - 1] !== INPUT_PROTOCOL_WHITELIST) {
      out.push("-protocol_whitelist", INPUT_PROTOCOL_WHITELIST);
    }
    out.push(args[i]);
  }
  return out;
}

/**
 * Containers that only reference other media (playlists, concat lists). An
 * upload is the media itself, so a probe that lands on one of these is refused
 * before any tool runs.
 */
export const REFERENCE_CONTAINER_RE = /(^|,)(hls|applehttp|concat|dash|ffconcat)($|,)/;

import { describe, expect, it } from "vitest";
import { usesCudaDevice } from "../../../packages/media-engine/src/ffmpeg.js";

// Fork (Blackwell phase 2): ffmpeg runs that open the NVIDIA driver skip the
// RLIMIT_AS shim, because cuInit fails under `ulimit -v` with
// CUDA_ERROR_OUT_OF_MEMORY. Everything else keeps the cap.
describe("usesCudaDevice", () => {
  it("is true for NVENC encoders", () => {
    for (const enc of ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]) {
      expect(usesCudaDevice(["-i", "in.mp4", "-c:v", enc, "-cq", "28", "out.mp4"])).toBe(true);
    }
  });

  it("is true for CUVID decoders and the cuda hwaccel", () => {
    expect(usesCudaDevice(["-c:v", "hevc_cuvid", "-i", "in.mp4", "out.mp4"])).toBe(true);
    expect(usesCudaDevice(["-hwaccel", "cuda", "-i", "in.mp4", "out.mp4"])).toBe(true);
    expect(usesCudaDevice(["-init_hw_device", "cuda=gpu", "-i", "in.mp4", "out.mp4"])).toBe(true);
  });

  it("is false for software and VAAPI encodes, so they keep the memory cap", () => {
    expect(usesCudaDevice(["-i", "in.mp4", "-c:v", "libx264", "-crf", "20", "out.mp4"])).toBe(
      false,
    );
    expect(
      usesCudaDevice([
        "-vaapi_device",
        "/dev/dri/renderD128",
        "-i",
        "in",
        "-c:v",
        "h264_vaapi",
        "o",
      ]),
    ).toBe(false);
    expect(usesCudaDevice(["-i", "in.mp4", "-c:a", "aac", "out.m4a"])).toBe(false);
  });

  it("ignores codec names that only appear as file names or other values", () => {
    expect(usesCudaDevice(["-i", "h264_nvenc", "-metadata", "title=h264_nvenc", "out.mp4"])).toBe(
      false,
    );
  });
});

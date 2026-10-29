"""Phase 0 spike: prove the CUDA 13.4 stack runs natively on the RTX 5090.

Each check runs in its own subprocess so a crash or a CUDA context problem in
one cannot mask the others, and ONNX Runtime is tested without torch imported
first (the way remove_bg.py loads it). Writes /out/gpu-report.json.
"""
import json
import subprocess
import sys
import textwrap

SHIM = textwrap.indent(textwrap.dedent("""
    import sys, types
    try:
        import torchvision.transforms.functional_tensor  # noqa
    except ImportError:
        import torchvision.transforms.functional as _F
        _s = types.ModuleType("torchvision.transforms.functional_tensor")
        _s.rgb_to_grayscale = _F.rgb_to_grayscale
        sys.modules["torchvision.transforms.functional_tensor"] = _s
"""), " " * 8)

CHECKS = {
    "abi_numpy_stack": """
        import numpy, scipy, cv2, PIL, skimage, sklearn, pandas
        print({m.__name__: m.__version__ for m in (numpy, scipy, cv2, PIL, skimage, sklearn, pandas)})
    """,
    "torch_cuda": """
        import torch
        assert torch.cuda.is_available(), "torch.cuda.is_available() is False"
        cap = torch.cuda.get_device_capability(0)
        arch = torch.cuda.get_arch_list()
        assert "sm_120" in arch, f"sm_120 missing from {arch}"
        a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
        b = (a @ a).float().sum().item()
        bf = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
        (bf @ bf).sum().item()
        conv = torch.nn.Conv2d(3, 64, 3).cuda().half()
        conv(torch.randn(1, 3, 512, 512, device="cuda", dtype=torch.float16))
        print({"torch": torch.__version__, "cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
               "device": torch.cuda.get_device_name(0), "capability": cap, "arch_list": arch})
    """,
    "torchvision_cuda_ops": """
        import torch, torchvision
        boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=torch.float32, device="cuda")
        scores = torch.tensor([0.9, 0.8, 0.7], device="cuda")
        keep = torchvision.ops.nms(boxes, scores, 0.5)
        assert keep.is_cuda and keep.tolist() == [0, 2], keep
        print({"torchvision": torchvision.__version__, "nms": keep.tolist()})
    """,
    "onnxruntime_cuda_without_torch": """
        import numpy as np, onnx, onnxruntime as ort
        from onnx import helper, TensorProto, numpy_helper
        W = numpy_helper.from_array(np.random.rand(16, 3, 3, 3).astype(np.float32), "W")
        node = helper.make_node("Conv", ["X", "W"], ["Y"], pads=[1, 1, 1, 1])
        g = helper.make_graph([node], "g", [helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, 3, 256, 256])],
                              [helper.make_tensor_value_info("Y", TensorProto.FLOAT, [1, 16, 256, 256])], [W])
        model = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
        model.ir_version = 9
        sess = ort.InferenceSession(model.SerializeToString(), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        used = sess.get_providers()
        assert used[0] == "CUDAExecutionProvider", f"CUDA EP not active: {used}"
        y = sess.run(None, {"X": np.random.rand(1, 3, 256, 256).astype(np.float32)})[0]
        print({"onnxruntime": ort.__version__, "device": ort.get_device(), "providers": used, "out": list(y.shape)})
    """,
    "snapotter_gpu_detection": """
        import sys
        sys.path.insert(0, "/repo-ai")
        import gpu
        fw = gpu.gpu_frameworks_available()
        print(fw)
        assert fw["torch"] and fw["onnx"], fw
    """,
    "basicsr_realesrgan_cuda": SHIM + """
        import torch
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer  # noqa
        net = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4).cuda().half().eval()
        with torch.no_grad():
            y = net(torch.rand(1, 3, 64, 64, device="cuda", dtype=torch.float16))
        assert list(y.shape) == [1, 3, 256, 256], y.shape
        print({"rrdbnet_out": list(y.shape)})
    """,
    "lpips_cuda": SHIM + """
        import torch, lpips
        m = lpips.LPIPS(net="alex", pretrained=False, pnet_rand=True, verbose=False).cuda()
        d = m(torch.rand(1, 3, 64, 64, device="cuda"), torch.rand(1, 3, 64, 64, device="cuda"))
        print({"lpips": float(d.item())})
    """,
    "codeformer_import": SHIM + """
        import importlib, importlib.metadata as md
        dist = md.distribution("codeformer-pip")
        tops = sorted({str(f).split("/")[0] for f in dist.files if str(f).endswith(".py") and "/" in str(f)})
        imported = []
        for t in tops:
            try:
                importlib.import_module(t); imported.append(t)
            except Exception as e:
                imported.append(f"{t}: {type(e).__name__}: {e}")
        print({"codeformer_modules": imported})
    """,
    "diffusers_transformers_cuda": """
        import torch, diffusers, transformers
        from diffusers import UNet2DModel
        u = UNet2DModel(sample_size=32, in_channels=3, out_channels=3, layers_per_block=1,
                        block_out_channels=(32, 64), down_block_types=("DownBlock2D", "DownBlock2D"),
                        up_block_types=("UpBlock2D", "UpBlock2D")).cuda().half()
        with torch.no_grad():
            out = u(torch.randn(1, 3, 32, 32, device="cuda", dtype=torch.float16), 10).sample
        from transformers import CLIPTextConfig, CLIPTextModel
        t = CLIPTextModel(CLIPTextConfig(hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                                         num_attention_heads=2, vocab_size=1000)).cuda()
        t(torch.randint(0, 1000, (1, 8), device="cuda"))
        print({"diffusers": diffusers.__version__, "transformers": transformers.__version__, "unet_out": list(out.shape)})
    """,
    "rembg_import": """
        import rembg
        print({"rembg": getattr(rembg, "__version__", "?")})
    """,
    "mediapipe_import": """
        import mediapipe as mp
        print({"mediapipe": mp.__version__})
    """,
    "ctranslate2_pypi_wheel": """
        import ctranslate2, faster_whisper
        n = ctranslate2.get_cuda_device_count()
        types = sorted(ctranslate2.get_supported_compute_types("cuda")) if n else []
        print({"ctranslate2": ctranslate2.__version__, "faster_whisper": faster_whisper.__version__,
               "cuda_devices": n, "cuda_compute_types": types})
        assert n > 0, "PyPI CTranslate2 sees no CUDA device (expected: no sm_120 kernels)"
    """,
}


def run(name, code):
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=600)
    out = (r.stdout.strip().splitlines() or [""])[-1]
    err = r.stderr.strip()[-1500:]
    status = "PASS" if r.returncode == 0 else "FAIL"
    print(f"[{status}] {name}: {out if status == 'PASS' else err.splitlines()[-1] if err else out}", flush=True)
    return {"status": status, "stdout": r.stdout.strip()[-2000:], "stderr": "" if status == "PASS" else err}


def main():
    report = {name: run(name, code) for name, code in CHECKS.items()}
    with open("/out/gpu-report.json", "w") as f:
        json.dump(report, f, indent=1)
    failed = [k for k, v in report.items() if v["status"] != "PASS"]
    print("GPU SUMMARY: " + ("all checks passed" if not failed else "FAILED: " + ", ".join(failed)), flush=True)


if __name__ == "__main__":
    main()

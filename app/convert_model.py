"""
RenderBatch model converter (runs as a separate process so PyTorch never loads into the app).

Finds .pth / .safetensors / .pt / .ckpt files in models\\denoise and models\\upscale, converts each
to the ONNX format the app runs on (fixed tile + fp16 copy), checks the result against the
original model, and deletes the original only when the check passes.
A model in the wrong folder (e.g. a 4x upscaler in denoise) is placed in the right one.

Prints one JSON object per line so the app can show progress.
usage: python convert_model.py <models_root> [cpu_threads]
"""
import json
import os
import re
import sys
import time
import traceback

EXTS = (".pth", ".safetensors", ".pt", ".ckpt")
FAILED_FILE = ".conversion_failed.json"
CONV_ARCHS = {"ESRGAN", "Compact", "RealESRGAN Compact", "SPAN", "RealCUGAN", "SAFMN", "PLKSR", "RealPLKSR"}
LIGHT_ARCHS = {"Compact", "RealESRGAN Compact", "SPAN", "SAFMN", "PLKSR", "RealPLKSR"}  # small nets: bigger tiles


def say(**kw):
    print(json.dumps(kw), flush=True)


def pending(root):
    """Model files waiting for conversion: [(path, folder_kind)], skipping ones that failed before (unchanged)."""
    failed = {}
    try:
        with open(os.path.join(root, FAILED_FILE), "r", encoding="utf-8") as fh:
            failed = json.load(fh)
    except (OSError, ValueError):
        pass
    out = []
    for kind in ("denoise", "upscale", "grain"):
        d = os.path.join(root, kind)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if f.lower().endswith(EXTS) and os.path.isfile(p):
                st = os.stat(p)
                if failed.get(p) == [st.st_size, int(st.st_mtime)]:
                    continue
                out.append((p, kind))
    return out


def remember_failure(root, path):
    fp = os.path.join(root, FAILED_FILE)
    try:
        with open(fp, "r", encoding="utf-8") as fh:
            failed = json.load(fh)
    except (OSError, ValueError):
        failed = {}
    try:
        st = os.stat(path)
        failed[path] = [st.st_size, int(st.st_mtime)]
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(failed, fh, indent=1)
    except OSError:
        pass


def safe_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "model"
    return stem


def convert(path, folder_kind, root):
    import numpy as np
    import onnx
    import torch
    from spandrel import ModelLoader

    say(event="stage", file=os.path.basename(path), text="Reading model")
    try:
        desc = ModelLoader().load_from_file(path)
    except Exception as e:
        raise RuntimeError("This file can't be read as an image model. The download may be incomplete, "
                           f"or it's a type of model the app doesn't support ({type(e).__name__}).")
    arch = getattr(desc.architecture, "name", str(desc.architecture))
    if getattr(desc, "input_channels", 3) != 3 or getattr(desc, "output_channels", 3) != 3:
        raise RuntimeError(f"{arch} model is not a color (RGB) image model.")
    purpose = str(getattr(desc, "purpose", "SR"))
    if purpose in ("Inpainting", "FaceSR"):
        raise RuntimeError(f"This is a {purpose} model, not a denoiser or upscaler.")
    scale = int(desc.scale)
    if scale == 1:
        kind = folder_kind if folder_kind in ("denoise", "grain") else "denoise"
    else:
        kind = "upscale"

    model = desc.model.eval().float()
    req = getattr(desc, "size_requirements", None)
    multiple = max(1, int(getattr(req, "multiple_of", 1) or 1))
    minimum = int(getattr(req, "minimum", 0) or 0)
    conv = arch in CONV_ARCHS
    tile = 512 if (arch in LIGHT_ARCHS and scale <= 2) else 256
    tile = max(tile, minimum)
    tile = int(np.ceil(tile / multiple) * multiple)
    overlap = 16 if conv else 32

    name = safe_name(path)
    out_dir = os.path.join(root, kind)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name)
    if not os.path.exists(base + ".json"):
        for f in (base + ".onnx", base + "_fp16.onnx"):     # left over from an interrupted conversion
            if os.path.exists(f):
                os.remove(f)
    if os.path.exists(base + ".onnx") or os.path.exists(base + "_fp16.onnx"):
        base = os.path.join(out_dir, f"{name}_{int(time.time())}")
        name = os.path.basename(base)

    say(event="stage", file=os.path.basename(path), text=f"Converting {arch} {scale}x")
    torch.manual_seed(0)
    dummy = torch.rand(1, 3, tile, tile)
    with torch.no_grad():
        ref = model(dummy).float().numpy()
    p32 = base + ".onnx"
    kw = dict(input_names=["input"], output_names=["output"], opset_version=17, do_constant_folding=True)
    try:
        with torch.no_grad():
            try:
                torch.onnx.export(model, dummy, p32, dynamo=False, **kw)
            except TypeError:
                torch.onnx.export(model, dummy, p32, **kw)
    except Exception as e:
        for f in (p32,):
            if os.path.exists(f):
                os.remove(f)
        raise RuntimeError(f"{arch} can't be converted to the app's format ({type(e).__name__}: {str(e)[:200]}).")

    meta = {"scale": scale, "tile": tile, "overlap": overlap, "arch": arch,
            "info": f"Converted from {os.path.basename(path)}"}
    m = onnx.load(p32)
    for k, v in meta.items():
        pr = m.metadata_props.add()
        pr.key, pr.value = k, str(v)
    onnx.save(m, p32)

    say(event="stage", file=os.path.basename(path), text="Checking the converted model")
    import onnxruntime as ort
    out = ort.InferenceSession(p32, providers=["CPUExecutionProvider"]).run(None, {"input": dummy.numpy()})[0]
    err = float(np.abs(out - ref).max())
    if not np.isfinite(out).all() or err > 2e-2:
        os.remove(p32)
        raise RuntimeError(f"The converted {arch} model doesn't match the original (difference {err:.3g}), so it wasn't kept.")

    fp16_ok = False
    p16 = base + "_fp16.onnx"
    try:
        from onnxconverter_common import float16
        m16 = float16.convert_float_to_float16(onnx.load(p32), keep_io_types=True)
        onnx.save(m16, p16)
        o16 = ort.InferenceSession(p16, providers=["CPUExecutionProvider"]).run(None, {"input": dummy.numpy()})[0]
        fp16_ok = bool(np.isfinite(o16).all() and float(np.abs(o16 - ref).max()) <= 0.05)
    except Exception:
        fp16_ok = False
    if not fp16_ok and os.path.exists(p16):
        os.remove(p16)   # a half-precision copy that doesn't load or doesn't match is never kept

    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    return dict(name=name, kind=kind, moved=kind != folder_kind, arch=arch, scale=scale, fp16=fp16_ok)


def main():
    import warnings
    warnings.filterwarnings("ignore")      # export / fp16 warnings are expected and not useful to the user
    root = sys.argv[1]
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    todo = pending(root)
    say(event="count", n=len(todo))
    if not todo:
        return
    try:
        import torch
        torch.set_num_threads(max(1, threads))
    except Exception as e:
        say(event="fatal", message=f"The model converter isn't installed ({e}).")
        return
    for i, (path, folder_kind) in enumerate(todo):
        say(event="start", i=i, n=len(todo), file=os.path.basename(path))
        try:
            res = convert(path, folder_kind, root)
            os.remove(path)  # the converted copy passed the check; the original isn't usable by the app
            say(event="done", file=os.path.basename(path), **res)
        except Exception as e:
            remember_failure(root, path)
            say(event="error", file=os.path.basename(path), message=str(e) or type(e).__name__,
                detail=traceback.format_exc()[-800:])


if __name__ == "__main__":
    main()

"""
Downloads the default models and converts them to ONNX for RenderBatch.
Runs automatically in the GitHub build; you never need to run it yourself.

Each model is exported with a FIXED tile size (static shapes run fastest on DirectML),
plus an fp16 copy for speed/lower power. A small .json next to each model records
its tile size and overlap. The app falls back to fp32 automatically if fp16 misbehaves.
"""

import argparse
import json
import os
import sys
import urllib.request

import numpy as np
import onnx
import torch
from spandrel import ModelLoader

MODELS = [
    {
        "name": "SCUNet_denoise_PSNR",
        "file": "scunet_color_real_psnr.pth",
        "url": "https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_psnr.pth",
        "tile": 256, "overlap": 32,
        "info": "1x real-noise denoiser, faithful (PSNR) variant. Kai Zhang et al., MIT.",
    },
    {
        "name": "SPAN_2x_NomosUni",
        "file": "2xNomosUni_span_multijpg.safetensors",
        "url": "https://github.com/Phhofm/models/releases/download/2xNomosUni_span_multijpg/2xNomosUni_span_multijpg.safetensors",
        "tile": 512, "overlap": 16,
        "info": "2x fast universal upscaler (SPAN). Philip Hofmann, CC BY 4.0.",
    },
]


def download(url, path):
    if os.path.exists(path):
        return
    print(f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "RenderBatch-build"})
    with urllib.request.urlopen(req) as r, open(path + ".part", "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(path + ".part", path)


def add_meta(model, meta):
    for k, v in meta.items():
        p = model.metadata_props.add()
        p.key, p.value = k, str(v)


def export(spec, cache_dir, out_dir, fp16=True):
    src = os.path.join(cache_dir, spec["file"])
    download(spec["url"], src)

    desc = ModelLoader().load_from_file(src)
    model = desc.model.eval().float()
    scale, tile = desc.scale, spec["tile"]
    print(f"{spec['name']}: {desc.architecture.name if hasattr(desc.architecture, 'name') else desc.architecture}, "
          f"{scale}x, tile {tile}")

    dummy = torch.rand(1, 3, tile, tile)
    with torch.no_grad():
        ref = model(dummy).numpy()

    p32 = os.path.join(out_dir, spec["name"] + ".onnx")
    kw = dict(input_names=["input"], output_names=["output"], opset_version=17, do_constant_folding=True)
    try:
        torch.onnx.export(model, dummy, p32, dynamo=False, **kw)  # classic exporter, static shapes
    except TypeError:  # older torch without the 'dynamo' argument
        torch.onnx.export(model, dummy, p32, **kw)
    m = onnx.load(p32)
    meta = {"scale": scale, "tile": tile, "overlap": spec["overlap"], "info": spec["info"]}
    add_meta(m, meta)
    onnx.save(m, p32)
    with open(os.path.join(out_dir, spec["name"] + ".json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)

    import onnxruntime as ort
    sess = ort.InferenceSession(p32, providers=["CPUExecutionProvider"])
    out = sess.run(None, {"input": dummy.numpy()})[0]
    err = float(np.abs(out - ref).max())
    print(f"  fp32 ONNX vs PyTorch max error: {err:.2e}")
    if err > 1e-2:
        raise RuntimeError(f"{spec['name']}: ONNX export mismatch ({err})")

    if fp16:
        try:
            from onnxconverter_common import float16
            m16 = float16.convert_float_to_float16(onnx.load(p32), keep_io_types=True)
            p16 = os.path.join(out_dir, spec["name"] + "_fp16.onnx")
            onnx.save(m16, p16)
            out16 = ort.InferenceSession(p16, providers=["CPUExecutionProvider"]).run(
                None, {"input": dummy.numpy()})[0]
            err16 = float(np.nanmax(np.abs(out16 - ref)))
            if not np.isfinite(out16).all() or err16 > 0.05:
                os.remove(p16)
                print(f"  fp16 copy rejected (error {err16:.2e}), fp32 only")
            else:
                print(f"  fp16 copy OK (max error {err16:.2e})")
        except Exception as e:  # fp16 is optional
            print(f"  fp16 conversion skipped: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default="model_cache")
    ap.add_argument("--no-fp16", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.cache, exist_ok=True)
    failed = []
    for spec in MODELS:
        try:
            export(spec, a.cache, a.out, fp16=not a.no_fp16)
        except Exception as e:
            print(f"FAILED {spec['name']}: {e}")
            failed.append(spec["name"])
    if failed:
        sys.exit(f"Model export failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()

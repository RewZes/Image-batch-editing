"""
The default AI models: where they come from, and downloading them from their original sites.

A model is downloaded as the authors publish it (.pth / .safetensors) into models\\denoise or
models\\upscale; the app's model converter then turns it into the ONNX format it runs on (the file is
named after the model, so the converted model gets the name below).
"""
import os
import urllib.request

DEFAULT_MODELS = [
    {"name": "SCUNet_denoise_PSNR", "kind": "denoise", "file": "SCUNet_denoise_PSNR.pth",
     "urls": ["https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_psnr.pth"],
     "page": "https://github.com/cszn/SCUNet", "license": "MIT",
     "info": "Real-noise denoiser, faithful (PSNR) version: removes grain and fireflies without inventing texture."},
    {"name": "SCUNet_denoise_GAN", "kind": "denoise", "file": "SCUNet_denoise_GAN.pth",
     "urls": ["https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_gan.pth"],
     "page": "https://github.com/cszn/SCUNet", "license": "MIT",
     "info": "Stronger real-noise denoiser (GAN version): cleaner, but may redraw very fine texture."},
    {"name": "SPAN_2x_NomosUni", "kind": "upscale", "file": "SPAN_2x_NomosUni.safetensors",
     "urls": ["https://github.com/Phhofm/models/releases/download/2xNomosUni_span_multijpg/"
              "2xNomosUni_span_multijpg.safetensors"],
     "page": "https://openmodeldb.info/models/2x-NomosUni-span-multijpg", "license": "CC BY 4.0",
     "info": "Fast, faithful 2x upscaler for photos and renders."},
    {"name": "4x-UltraSharp", "kind": "upscale", "file": "4x-UltraSharp.pth",
     "urls": ["https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth"],
     "page": "https://openmodeldb.info/models/4x-UltraSharp",
     "license": "see the model page (listed as non-commercial)",
     "info": "Popular sharp 4x upscaler (ESRGAN). Slower; strong detail on textures."},
]
MIN_BYTES = 200_000
SOURCE_EXTS = (".pth", ".safetensors")


class Cancelled(Exception):
    pass


def by_name(name):
    return next((m for m in DEFAULT_MODELS if m["name"] == name), None)


def is_default(name):
    return by_name(name) is not None


def waiting_file(models_root, m):
    """The downloaded original file while it waits for (or goes through) conversion, else None."""
    p = os.path.join(models_root, m["kind"], m["file"])
    return p if os.path.isfile(p) else None


def missing(models_root, kind, installed):
    """Default models of this kind that aren't installed (also the ones downloaded but not converted yet)."""
    return [m for m in DEFAULT_MODELS if m["kind"] == kind and m["name"] not in installed]


def download(m, models_root, progress=None, cancel=None):
    """Downloads the model's original file from its authors' site into models\\<kind>. Returns the path.
    progress(fraction or -1); cancel() raises to stop. Raises IOError with every address tried."""
    folder = os.path.join(models_root, m["kind"])
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, m["file"])
    tmp = os.path.join(folder, "." + m["file"] + ".part")       # hidden from the converter until complete
    errors = []
    for url in m["urls"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RenderBatch"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    if cancel:
                        cancel()
                    b = r.read(1 << 20)
                    if not b:
                        break
                    fh.write(b)
                    done += len(b)
                    if progress:
                        progress(done / total if total else -1)
            size = os.path.getsize(tmp)
            if size < MIN_BYTES or (total and size != total):
                raise IOError("the download was incomplete")
            os.replace(tmp, dst)
            return dst
        except Exception as e:      # noqa: BLE001 - also Cancelled: clean up, then re-raise below
            try:
                os.remove(tmp)
            except OSError:
                pass
            if type(e).__name__ == "Cancelled":
                raise
            errors.append(f"{url}\n  {e}")
    raise IOError("\n".join(errors))

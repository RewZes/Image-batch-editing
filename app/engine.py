"""
RenderBatch engine: all image processing, no GUI code.

Pipeline per image:
  denoise (1x ONNX model, blended by strength) -> color match to a hero image
  -> mood grade (linear-light exposure / white balance, contrast, saturation, LUT)
  -> upscale (ONNX model, only when the enlargement is big enough) -> exact resize
  -> luminance unsharp mask -> save (16-bit by default)

Models run through ONNX Runtime with DirectML (any DX12 GPU: NVIDIA, AMD, Intel),
falling back to CPU if no GPU is available.
"""

import os
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")  # must be set before importing cv2

import json
import math
import re
import threading
import time
import zlib

import cv2
import numpy as np

import atmosphere

_ort = None  # ONNX Runtime is imported on first use, so the window opens faster


def get_ort():
    global _ort
    if _ort is None:
        import onnxruntime
        _ort = onnxruntime
    return _ort

IMG_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".exr", ".cxr")
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)

DEFAULTS = {
    "input_dir": "", "output_dir": "", "suffix": "_final", "fmt": "png", "bits": "16", "skip_existing": True,
    "ref_image": "", "cm_strength": 0.6,
    "exposure": 0.0, "temperature": 0.0, "tint": 0.0, "contrast": 1.0, "saturation": 1.0,
    "lut_path": "", "lut_strength": 1.0,
    "denoise_model": "", "denoise_strength": 0.7,
    "upscale_model": "", "resize_mode": "scale_factor", "width": 3840, "height": 2160, "factor": 2.0,
    "min_model_scale": 1.3, "fp16": True,
    # Resolution extras: run the upscale step several times in a row (Scale factor mode), and also save a
    # version without upscaling next to the upscaled one (for comparing upscalers)
    "upscale_multi": False, "upscale_times": 1, "save_unscaled": False,
    "sharpen_amount": 0.3, "sharpen_radius": 1.0, "sharpen_threshold": 0.01,
    # on/off switches for each stage
    "look_on": True, "match_on": False, "denoise_on": False, "resize_on": True, "sharpen_on": True,
    # texture: clarity (micro-contrast) and photographic grain
    "texture_on": False, "clarity": 0.2, "grain_amount": 0.4, "grain_size": 1.0, "grain_color": 0.25,
    "grain_type": "sensor", "grain_model": "", "_grain_seed": "",
    # Corona .cxr: per-light LightMix changes {light name: {"mult": x, "color": [r,g,b] or None, "on": None/bool}},
    # whether the file's own Post settings are applied, and which light passes are used
    "lightmix": {}, "lightmix_grouped": {}, "light_groups": [], "lightmix_mode": "individual", "cxr_post": True, "cxr_denoised": "file", "_lut_dirs": [],
    "show_images": True, "show_cxr": True,     # which file types are shown and processed
}
DEFAULTS.update(atmosphere.ATMO_DEFAULTS)      # Atmosphere: Time of day, Fog, Light rays


def source_signature(s, path):
    """Settings that change an image's source pixels (LightMix for .cxr, Time of day for all)."""
    if str(path).lower().endswith(".cxr"):
        import corona
        return corona.mix_signature(s)
    return atmosphere.source_signature(s)


def with_defaults(settings):
    """Fills in defaults; also migrates settings saved before the on/off switches existed."""
    s = dict(DEFAULTS, **settings)
    if "denoise_on" not in settings:
        s["denoise_on"] = float(settings.get("denoise_strength", 0) or 0) > 0
    if "match_on" not in settings:
        s["match_on"] = float(settings.get("cm_strength", 0) or 0) > 0
    return s


class SettingError(ValueError):
    """A problem caused by one specific setting; 'field' names it so the UI can highlight it."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


class Cancelled(Exception):
    pass


class GpuLost(RuntimeError):
    """Windows reset the graphics driver (DXGI device removed / suspended)."""


GPU_LOST_MSG = ("The graphics card stopped responding and Windows reset its driver. This usually means the GPU ran out "
                "of video memory (3ds Max, Corona or another app is using it) or one step took too long. "
                "RenderBatch reconnected to the GPU; close other GPU-heavy apps or lower GPU usage in Settings and try again.")

_GPU_LOCK = threading.RLock()   # one model on the GPU at a time: preview, Detail check and batch never overlap
_GPU_EPOCH = [0]                # bumped after a driver reset; every session made before it is dead


def is_gpu_lost(e):
    t = str(e)
    return any(k in t for k in ("887A0005", "887A0006", "887A0007", "887A0020", "DEVICE_REMOVED", "DEVICE_HUNG",
                                "device instance has been suspended", "GetDeviceRemovedReason"))


class NonFinite(Exception):
    pass


# ----------------------------------------------------------------------------- performance

PERF = {"gpu_limit": 0.75, "cpu_threads": max(1, (os.cpu_count() or 4) // 2)}


def set_performance(gpu_limit=None, cpu_threads=None):
    """gpu_limit 0.3..1.0: fraction of time the GPU may be busy (pauses between tiles keep
    the desktop responsive). cpu_threads caps CPU threads for OpenCV and CPU inference."""
    if gpu_limit is not None:
        PERF["gpu_limit"] = min(1.0, max(0.2, float(gpu_limit)))
    if cpu_threads is not None:
        PERF["cpu_threads"] = max(1, int(cpu_threads))
    try:
        cv2.setNumThreads(PERF["cpu_threads"])
    except Exception:
        pass


def lower_thread_priority():
    """Run heavy work below normal priority so the UI and the rest of Windows stay responsive."""
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetThreadPriority(k.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass


class Control:
    """Shared cancel / pause flags for a running job."""

    def __init__(self):
        import threading
        self._cancel = threading.Event()
        self._run = threading.Event()
        self._run.set()

    def cancel(self):
        self._cancel.set()
        self._run.set()

    def is_set(self):  # lets Control be used where a cancel Event was expected
        return self._cancel.is_set()

    def pause(self, paused):
        (self._run.clear if paused else self._run.set)()

    @property
    def paused(self):
        return not self._run.is_set()

    def check(self):
        """Blocks while paused; raises Cancelled when cancelled."""
        self._run.wait()
        if self._cancel.is_set():
            raise Cancelled()


# ----------------------------------------------------------------------------- files

def list_images(folder, s=None):
    """Image files in a folder; s['show_images'] / s['show_cxr'] can leave out plain images or .cxr files."""
    if not folder or not os.path.isdir(folder):
        return []
    s = s or {}
    imgs, cxr = s.get("show_images", True), s.get("show_cxr", True)
    return sorted(f for f in os.listdir(folder) if f.lower().endswith(IMG_EXTS)
                  and (cxr if f.lower().endswith(".cxr") else imgs))


_POOL = None


def par_rows(fn, x, min_rows=64):
    """Runs a per-pixel function on horizontal strips of x on several CPU cores (numpy releases the GIL),
    and puts the strips back together. fn(strip, y0) -> strip of the same height."""
    global _POOL
    n = max(1, min(int(PERF["cpu_threads"]), x.shape[0] // min_rows))
    if n <= 1:
        return fn(x, 0)
    if _POOL is None:
        from concurrent.futures import ThreadPoolExecutor
        _POOL = ThreadPoolExecutor(max(2, os.cpu_count() or 2), thread_name_prefix="rows")
    bounds = [round(x.shape[0] * k / n) for k in range(n + 1)]
    futs = [_POOL.submit(fn, x[bounds[k]:bounds[k + 1]], bounds[k]) for k in range(n)]
    parts = [f.result() for f in futs]
    return np.concatenate(parts, axis=0)


def _linear_to_srgb(x):
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055).astype(np.float32)


def _srgb_to_linear(x):
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4)).astype(np.float32)


def load_image(path, s=None, max_side=None, use_cache=True, file_mix_only=False, notes=None, progress=None,
               cancel=None):
    """Returns (rgb HxWx3 float32 0..1, alpha HxW float32 or None). Handles 8/16-bit, EXR, unicode paths.
    Corona .cxr files are LightMix-ed (file settings + s['lightmix'] changes) and tone-mapped like Corona."""
    if path.lower().endswith(".cxr"):
        import corona
        s = s or {}
        rgb = corona.render(path, s, lut_dirs=s.get("_lut_dirs") or (), max_side=max_side, progress=progress,
                            cancel=cancel, threads=max(2, PERF["cpu_threads"]), use_cache=use_cache, notes=notes,
                            file_mix_only=file_mix_only)
        return np.ascontiguousarray(rgb, dtype=np.float32), None
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Cannot read {os.path.basename(path)}")
    if img.dtype == np.uint8:
        img = img.astype(np.float32) / 255.0
    elif img.dtype == np.uint16:
        img = img.astype(np.float32) / 65535.0
    else:  # EXR / float: scene-linear -> sRGB
        img = _linear_to_srgb(img.astype(np.float32))
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    alpha = None
    if img.shape[2] == 4:
        alpha = np.clip(img[:, :, 3], 0, 1).copy()
        img = img[:, :, :3]
    rgb = np.ascontiguousarray(np.clip(img[:, :, ::-1], 0, 1), dtype=np.float32)
    if s and not file_mix_only and atmosphere.tod_active(s):
        rgb = atmosphere.tod_image(rgb, s)          # Time of day relights the image itself
    return rgb, alpha


def save_image(rgb, alpha, path, fmt="png", bits="16", jpg_quality=95):
    arr = np.clip(rgb, 0, 1)[:, :, ::-1]
    if alpha is not None and fmt != "jpg":
        arr = np.dstack([arr, np.clip(alpha, 0, 1)])
    params = []
    if bits == "32" and fmt in ("tif", "tiff"):
        out = np.ascontiguousarray(arr, dtype=np.float32)   # 32-bit float TIFF
        ok, buf = cv2.imencode(".tif", out)
        if not ok:
            raise RuntimeError(f"Cannot encode {path}")
        buf.tofile(path)
        return
    if fmt == "jpg":
        out = (arr * 255 + 0.5).astype(np.uint8)
        params = [cv2.IMWRITE_JPEG_QUALITY, jpg_quality]
    elif bits == "16":
        out = par_rows(lambda part, _y0: (part * 65535 + 0.5).astype(np.uint16), np.ascontiguousarray(arr))
    else:
        out = (arr * 255 + 0.5).astype(np.uint8)
    ok, buf = cv2.imencode("." + fmt, out, params)
    if not ok:
        raise RuntimeError(f"Cannot encode {path}")
    buf.tofile(path)


# ----------------------------------------------------------------------------- color

def lab_stats(rgb, max_side=768):
    h, w = rgb.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        rgb = cv2.resize(rgb, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(np.ascontiguousarray(rgb, np.float32), cv2.COLOR_RGB2LAB).reshape(-1, 3)
    return lab.mean(0), lab.std(0) + 1e-6


def color_match(rgb, ref_stats, strength, src_stats=None):
    """Reinhard transfer in Lab. src_stats lets a crop use the full image's statistics."""
    if strength <= 0 or ref_stats is None:
        return rgb
    mean, std = src_stats if src_stats is not None else lab_stats(rgb)
    rmean, rstd = ref_stats
    lab = cv2.cvtColor(np.ascontiguousarray(rgb, np.float32), cv2.COLOR_RGB2LAB)
    lab = (lab - mean) / std * rstd + rmean
    matched = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_LAB2RGB)
    return np.clip(rgb + (matched - rgb) * strength, 0, 1).astype(np.float32)


def load_cube(path):
    size, vals = None, []
    dmin, dmax = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tok = line.split()
            key = tok[0].upper()
            if key == "LUT_3D_SIZE":
                size = int(tok[1])
            elif key == "LUT_1D_SIZE":
                raise ValueError("1D LUTs are not supported, use a 3D .cube LUT.")
            elif key == "DOMAIN_MIN":
                dmin = [float(v) for v in tok[1:4]]
            elif key == "DOMAIN_MAX":
                dmax = [float(v) for v in tok[1:4]]
            else:
                try:
                    vals.append([float(v) for v in tok[:3]])
                except ValueError:
                    pass
    if size is None or size < 2 or len(vals) != size ** 3:
        raise ValueError(f"Invalid .cube file: {os.path.basename(path)}")
    table = np.asarray(vals, np.float32).reshape(size, size, size, 3)  # [b][g][r]
    return table, np.asarray(dmin, np.float32), np.asarray(dmax, np.float32)


_ATLAS = {}


def _lut_atlas(lut):
    """The 3D LUT laid out as one 2D image (slices side by side), so OpenCV's fast remap can do the
    interpolation: the trick GPUs use for color grading."""
    key = id(lut[0])
    a = _ATLAS.get(key)
    if a is None or a[0] is not lut[0]:
        table = lut[0]
        n = table.shape[0]
        atlas = np.ascontiguousarray(table.transpose(1, 0, 2, 3).reshape(n, n * n, 3), dtype=np.float32)  # [g][b*n+r]
        if len(_ATLAS) > 8:
            _ATLAS.clear()
        _ATLAS[key] = a = (table, atlas)
    return a[1]


def _apply_lut_part(src, lut, strength):
    """Any shape in, same shape out. OpenCV's remap takes at most 32767 rows / columns, so the pixels are
    looked up as blocks of 4096 x up to 4096."""
    shape = src.shape
    if src.ndim == 3 and shape[0] < 32767 and shape[1] < 32767:
        return _apply_lut_block(src, lut, strength)
    flat = src.reshape(-1, 3)
    n = flat.shape[0]
    out = np.empty_like(flat)
    W = 4096
    step = W * 4096
    for i in range(0, n, step):
        part = flat[i:i + step]
        m = part.shape[0]
        rows = (m + W - 1) // W
        pad = rows * W - m
        blk = np.concatenate([part, np.zeros((pad, 3), part.dtype)]) if pad else part
        res = _apply_lut_block(blk.reshape(rows, W, 3), lut, strength).reshape(-1, 3)
        out[i:i + m] = res[:m]
    return out.reshape(shape)


def _apply_lut_block(src, lut, strength):
    table, dmin, dmax = lut
    n = table.shape[0]
    atlas = _lut_atlas(lut)
    f = np.clip((src - dmin) / (dmax - dmin), 0, 1) * np.float32(n - 1)
    fb = f[..., 2]
    b0 = np.minimum(np.floor(fb), n - 2)
    wb = (fb - b0)[..., None]
    mx = np.ascontiguousarray(f[..., 0] + b0 * n, dtype=np.float32)
    my = np.ascontiguousarray(f[..., 1], dtype=np.float32)
    lo = cv2.remap(atlas, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    hi = cv2.remap(atlas, mx + np.float32(n), my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    res = lo + (hi - lo) * wb
    out = res if strength == 1 else src + (res - src) * np.float32(strength)
    return np.clip(out, 0, 1).astype(np.float32)


def apply_lut(rgb, lut, strength, chunk_rows=512):
    """3D LUT with trilinear interpolation (same result as interpolating the table directly, ~10x faster)."""
    rgb = np.ascontiguousarray(rgb, dtype=np.float32)
    return par_rows(lambda part, _y0: _apply_lut_part(part, lut, strength), rgb)


def grade(rgb, s, lut=None):
    """Mood / relight grade. Exposure and white balance in linear light (camera-like)."""
    if not s.get("look_on", True):
        return rgb
    x = rgb
    exp, temp, tint = s["exposure"], s["temperature"], s["tint"]
    if exp or temp or tint:
        lin = _srgb_to_linear(x)
        if exp:
            lin = lin * np.float32(2.0 ** exp)
        if temp or tint:
            t, ti = temp / 100 * 0.3, tint / 100 * 0.3
            gains = np.array([1 + t, 1 - ti, 1 - t], np.float32)
            gains /= float((gains * LUMA).sum())  # keep brightness
            lin = lin * gains
        x = _linear_to_srgb(lin)
    if s["contrast"] != 1:
        x = (x - 0.5) * np.float32(s["contrast"]) + 0.5
    if s["saturation"] != 1:
        luma = (x @ LUMA)[..., None]
        x = luma + (x - luma) * np.float32(s["saturation"])
    x = np.clip(x, 0, 1).astype(np.float32)
    if lut is not None and s["lut_strength"] > 0:
        x = apply_lut(x, lut, s["lut_strength"])
    return x


_S2L = None
_L2S = None


def _tables():
    global _S2L, _L2S
    if _S2L is None:
        x = np.linspace(0, 1, 4096, dtype=np.float32)
        _S2L = _srgb_to_linear(x)
        y = np.linspace(0, 16, 262144, dtype=np.float32)  # keeps highlights above 1 like grade() does
        _L2S = _linear_to_srgb(y)
    return _S2L, _L2S


class FastGrader:
    """Real-time grading for the preview: lookup tables instead of per-pixel powers and
    cached intermediate images. Visually identical to grade() (the batch uses the exact path)."""

    def __init__(self, rgb, src_stats=None):
        self.rgb = rgb
        self.src_stats = src_stats
        self._cm_key = None
        self._cm = rgb
        self._lin = None
        self._lin_of = None
        self._atmo_key = None
        self._atmo_x = None
        self._bake_key = None
        self._baked = None
        self._coords = None      # (source image, n, x_lo, x_hi, y, b weight): where each pixel reads the LUT

    def _linear(self, x):
        s2l, _ = _tables()
        return s2l[(x * 4095 + 0.5).astype(np.int16)]

    def grade(self, s, lut=None, ref_stats=None, atmo=None):
        """Returns the graded preview as uint8 RGB. atmo: fog / light rays maps of the full image."""
        x = self.rgb
        if s.get("match_on") and ref_stats is not None and s["cm_strength"] > 0:
            key = (id(ref_stats), round(s["cm_strength"], 3))
            if key != self._cm_key:
                self._cm = color_match(self.rgb, ref_stats, s["cm_strength"], self.src_stats)
                self._cm_key, self._lin = key, None
            x = self._cm
        elif self._cm_key is not None:
            self._cm_key, self._lin = None, None
        if atmo is not None:
            key = (self._cm_key, id(atmo))
            if key != self._atmo_key or self._atmo_x is None:
                h, w = x.shape[:2]
                self._atmo_x = atmosphere.apply(x, atmo, (w, h), (0, 0), fast=True).astype(np.float32)
                self._atmo_key = key
            x = self._atmo_x
        if s.get("look_on", True):
            table = self._bake(s, lut)
            if table is not None:
                x = self._apply_baked(x, table)
        if s.get("texture_on") and s.get("clarity"):
            x = clarity(np.clip(x, 0, 1).astype(np.float32), s["clarity"], max(x.shape[:2]))
        return cv2.convertScaleAbs(np.ascontiguousarray(x, dtype=np.float32), alpha=255.0)

    BAKE_N = 48

    def _bake(self, s, lut):
        """The whole per-pixel grade (exposure, white balance, contrast, saturation, LUT) as one small 3D LUT:
        rebuilt in a few ms when a slider moves, then applied to the preview with one fast lookup."""
        exp, temp, tint = s["exposure"], s["temperature"], s["tint"]
        use_lut = lut is not None and s["lut_strength"] > 0
        if not (exp or temp or tint or s["contrast"] != 1 or s["saturation"] != 1 or use_lut):
            return None
        key = (round(exp, 5), round(temp, 4), round(tint, 4), round(s["contrast"], 5), round(s["saturation"], 5),
               id(lut[0]) if use_lut else None, round(s["lut_strength"], 5) if use_lut else None)
        if key == self._bake_key:
            return self._baked
        n = self.BAKE_N
        g = np.linspace(0, 1, n, dtype=np.float32)
        b, gg, r = np.meshgrid(g, g, g, indexing="ij")
        grid = np.stack([r, gg, b], -1).reshape(-1, 1, 3)
        out = grade(grid, dict(s, look_on=True), lut if use_lut else None)
        self._baked = np.clip(out, 0, 1).reshape(n, n, n, 3).astype(np.float32)
        self._bake_key = key
        return self._baked

    def _apply_baked(self, x, table):
        n = table.shape[0]
        c = self._coords
        if c is None or c[0] is not x or c[1] != n:
            f = np.clip(x, 0, 1) * np.float32(n - 1)
            fb = f[..., 2]
            b0 = np.minimum(np.floor(fb), n - 2)
            wb = np.ascontiguousarray(np.repeat((fb - b0)[..., None], 3, axis=2), dtype=np.float32)
            mx = np.ascontiguousarray(f[..., 0] + b0 * n, dtype=np.float32)
            my = np.ascontiguousarray(f[..., 1], dtype=np.float32)
            c = self._coords = (x, n, mx, mx + np.float32(n), my, wb)
        _, _, mx0, mx1, my, wb = c
        atlas = np.ascontiguousarray(table.transpose(1, 0, 2, 3).reshape(n, n * n, 3))
        lo = cv2.remap(atlas, mx0, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        hi = cv2.remap(atlas, mx1, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return cv2.add(lo, cv2.multiply(cv2.subtract(hi, lo), wb))


# ----------------------------------------------------------------------------- resize / sharpen

def upscale_times(s):
    """How many times the upscale step runs (only in Scale factor mode, with "Upscale multiple times")."""
    if not s.get("resize_on", True) or s.get("resize_mode") != "scale_factor" or not s.get("upscale_multi"):
        return 1
    return max(1, min(8, int(s.get("upscale_times", 1) or 1)))


def output_name(name, s):
    """File name of an image's result: name + suffix (+ upscaler name when an un-upscaled copy is saved too,
    so results of different upscalers sit side by side) (+ _upscaled_xN for repeated upscaling)."""
    base = os.path.splitext(name)[0] + s["suffix"]
    if s.get("save_unscaled") and s.get("resize_on", True) and s.get("upscale_model"):
        base += "_" + re.sub(r"[^\w.-]+", "-", s["upscale_model"])
    n = upscale_times(s)
    if n > 1:
        base += f"_upscaled_x{n}"
    return base + "." + s["fmt"]


def size_problem(w, h, s):
    """A message when the result of a w x h image would be too big for this PC's memory, else None."""
    import corona
    tw, th = target_size(w, h, s)
    need = tw * th * 3 * 4 * 3          # result + working copies, 32-bit float
    ram = corona.total_ram()
    if need > ram * 0.7 or tw * th > 2_000_000_000:
        return (f"The result would be {tw}×{th} pixels and needs about {need / 1e9:.0f} GB of memory "
                f"(this PC has {ram / 1e9:.0f} GB). Lower the Scale factor or the number of times.")
    return None


def unscaled_name(name, s):
    return os.path.splitext(name)[0] + s["suffix"] + "_no-upscale." + s["fmt"]


def target_size(w, h, s):
    if not s.get("resize_on", True):
        return w, h
    mode, tw, th, f = s["resize_mode"], int(s["width"]), int(s["height"]), float(s["factor"])
    if mode == "scale_factor":
        f = f ** upscale_times(s)
        return max(1, round(w * f)), max(1, round(h * f))
    if mode == "exact":
        return tw, th
    if mode == "fit_width":
        return tw, max(1, round(h * tw / w))
    if mode == "fit_height":
        return max(1, round(w * th / h)), th
    k = min(tw / w, th / h)  # fit_inside
    return max(1, round(w * k)), max(1, round(h * k))


def resize(rgb, tw, th):
    h, w = rgb.shape[:2]
    if (w, h) == (tw, th):
        return rgb
    interp = cv2.INTER_AREA if tw * th < w * h else cv2.INTER_CUBIC
    return np.clip(cv2.resize(rgb, (tw, th), interpolation=interp), 0, 1)


def resize_alpha(alpha, tw, th):
    h, w = alpha.shape
    interp = cv2.INTER_AREA if tw * th < w * h else cv2.INTER_CUBIC
    return np.clip(cv2.resize(alpha, (tw, th), interpolation=interp), 0, 1)


def unsharp_luma(rgb, amount, radius, threshold):
    """Luminance-only sharpening (no color halos) with a soft noise threshold."""
    if amount <= 0:
        return rgb
    luma = rgb @ LUMA
    detail = luma - cv2.GaussianBlur(luma, (0, 0), radius)
    if threshold > 0:
        detail *= np.clip((np.abs(detail) - threshold) / threshold, 0, 1)
    return np.clip(rgb + (detail * amount)[..., None], 0, 1)


# ----------------------------------------------------------------------------- models

# ----------------------------------------------------------------------------- texture: clarity + grain

def _wide_blur(img, sigma):
    """Gaussian blur; a wide one is done on a smaller copy (same result, many times faster)."""
    k = int(sigma / 3)
    if k < 2:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / k)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def clarity(rgb, amount, long_side):
    """Local (micro) contrast on luminance, strongest in the midtones. The radius is relative to the
    image size, so a small preview and the final image look the same."""
    if not amount:
        return rgb
    sigma = max(1.5, 0.012 * long_side)
    luma = rgb @ LUMA
    detail = luma - _wide_blur(luma, sigma)
    w = np.clip(4.0 * luma * (1.0 - luma), 0, 1)
    return np.clip(rgb + (np.float32(amount) * detail * w)[..., None], 0, 1).astype(np.float32)


def clarity_margin(s, long_side):
    """Pixels of context clarity needs around a crop so the crop matches the full image."""
    if not (s.get("texture_on") and s.get("clarity")):
        return 0
    return int(math.ceil(3 * max(1.5, 0.012 * long_side))) + 2


GRAIN_BLOCK = 256
_GRAIN_NORM = {}


def _grain_norm(sigma):
    """Factor that brings blurred white noise back to unit strength (measured once per size)."""
    key = round(sigma, 3)
    if key not in _GRAIN_NORM:
        ref = np.random.default_rng(12345).standard_normal((256, 256)).astype(np.float32)
        _GRAIN_NORM[key] = 1.0 / float(cv2.GaussianBlur(ref, (0, 0), sigma)[32:-32, 32:-32].std())
    return _GRAIN_NORM[key]


def grain_field(seed, x0, y0, w, h, size):
    """Deterministic noise for output pixels [x0, x0+w) x [y0, y0+h): built from fixed 256 px blocks,
    so any crop of an image gets exactly the grain the full image gets there. Returns (h, w, 4):
    three color channels + one mono channel, each with unit strength."""
    sigma = 0.5 * size
    m = int(math.ceil(3 * sigma)) + 1 if sigma >= 0.3 else 0
    X0, Y0, X1, Y1 = x0 - m, y0 - m, x0 + w + m, y0 + h + m
    B = GRAIN_BLOCK
    bx0, by0 = X0 // B, Y0 // B
    bx1, by1 = (X1 - 1) // B, (Y1 - 1) // B
    canvas = np.empty(((by1 - by0 + 1) * B, (bx1 - bx0 + 1) * B, 4), np.float32)
    for by in range(by0, by1 + 1):
        for bx in range(bx0, bx1 + 1):
            rng = np.random.default_rng([seed, bx + 100000, by + 100000])
            canvas[(by - by0) * B:(by - by0 + 1) * B, (bx - bx0) * B:(bx - bx0 + 1) * B] = \
                rng.standard_normal((B, B, 4), dtype=np.float32)
    cx, cy = X0 - bx0 * B, Y0 - by0 * B
    field = canvas[cy:cy + (Y1 - Y0), cx:cx + (X1 - X0)]
    if sigma >= 0.3:
        k = _grain_norm(sigma)
        field = np.dstack([cv2.GaussianBlur(np.ascontiguousarray(field[..., c]), (0, 0), sigma) * k
                           for c in range(4)])
    return field[m:m + h, m:m + w]


def grain_seed(name):
    return zlib.crc32((name or "image").encode("utf-8")) & 0x7FFFFFFF


def apply_grain(rgb, s, origin=(0, 0)):
    """Photographic grain that follows the light, like real capture:
    sensor: photon noise grows with the square root of the light, so it shows most in shadows and
            midtones and disappears in clipped highlights;
    film:   grain peaks in the midtones and fades toward pure black and white."""
    amt = float(s.get("grain_amount", 0))
    if amt <= 0:
        return rgb
    h, w = rgb.shape[:2]
    n = grain_field(grain_seed(s.get("_grain_seed", "")), origin[0], origin[1], w, h, float(s["grain_size"]))
    c = min(max(float(s["grain_color"]), 0.0), 1.0)
    noise = (n[..., 3:4] * (1 - c) + n[..., :3] * c) / math.sqrt((1 - c) ** 2 + c ** 2)
    luma = (rgb @ LUMA)[..., None]
    if s.get("grain_type", "sensor") == "film":
        wgt = 0.15 + 0.85 * np.clip(4 * luma * (1 - luma), 0, 1) ** 0.7
        out = rgb + np.float32(0.022 * amt) * wgt * noise
    else:
        lin = _srgb_to_linear(rgb)
        out_lin = lin + np.float32(0.035 * amt) * np.sqrt(lin + 0.002) * noise
        out = _linear_to_srgb(out_lin)
        clipped = np.clip((luma - 0.93) / 0.07, 0, 1)  # blown highlights have no noise
        out = out * (1 - clipped) + rgb * clipped
    return np.clip(out, 0, 1).astype(np.float32)


def available_device():
    try:
        ort = get_ort()
    except Exception:
        return "No ONNX Runtime"
    return "GPU · DirectML" if "DmlExecutionProvider" in ort.get_available_providers() else "CPU"


def list_models(models_dir):
    """Model display names. 'name.onnx' and 'name_fp16.onnx' count as one model."""
    if not os.path.isdir(models_dir):
        return []
    names = set()
    for f in os.listdir(models_dir):
        if f.lower().endswith(".onnx"):
            base = f[:-5]
            if base.endswith("_fp16"):
                base = base[:-5]
            names.add(base)
    return sorted(names)


MODEL_KINDS = ("denoise", "upscale", "grain")
_DENOISE_HINTS = ("scunet", "denois", "nafnet", "restormer", "drunet", "dncnn", "1x")


def model_dir(root, kind):
    return os.path.join(root, kind)


def organize_models(root):
    """Keeps denoisers and upscalers in separate subfolders (models\\denoise, models\\upscale).
    Models found loose in the root folder are moved into the right one (1x = denoise)."""
    for kind in MODEL_KINDS:
        os.makedirs(model_dir(root, kind), exist_ok=True)
    moved = []
    for name in list_models(root):
        base = os.path.join(root, name)
        kind = None
        try:
            with open(base + ".json", "r", encoding="utf-8") as fh:
                kind = "denoise" if int(json.load(fh).get("scale", 0)) == 1 else "upscale"
        except (OSError, ValueError):
            pass
        if kind is None:
            kind = "denoise" if any(h in name.lower() for h in _DENOISE_HINTS) else "upscale"
        for ext in (".onnx", "_fp16.onnx", ".json"):
            src = base + ext
            if os.path.exists(src):
                dst = os.path.join(model_dir(root, kind), name + ext)
                try:
                    if not os.path.exists(dst):
                        os.replace(src, dst)
                except OSError:
                    pass
        moved.append((name, kind))
    return moved


THUMB_VERSION = 1


def thumbnail_cached(path, cache_dir, max_side=220):
    """load_thumbnail with a small JPEG cache on disk (keyed by the file's path, size and date), so reopening
    a folder shows its thumbnails at once. Returns (rgb uint8, was_cached)."""
    import hashlib
    try:
        st = os.stat(path)
        key = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{max_side}|{THUMB_VERSION}"
    except OSError:
        return load_thumbnail(path, max_side), False
    h = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()
    cpath = os.path.join(cache_dir, h[:2], h + ".jpg")
    if os.path.exists(cpath):
        img = cv2.imdecode(np.fromfile(cpath, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            return np.ascontiguousarray(img[:, :, ::-1]), True
    rgb = load_thumbnail(path, max_side)
    try:
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(rgb[:, :, ::-1]), [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            tmp = cpath + ".tmp"
            buf.tofile(tmp)
            os.replace(tmp, cpath)
    except OSError:
        pass
    return rgb, False


def prune_thumbnail_cache(cache_dir, keep=6000):
    """Keeps the thumbnail cache small: drops the oldest files beyond `keep`."""
    try:
        files = []
        for d in os.listdir(cache_dir):
            sub = os.path.join(cache_dir, d)
            if os.path.isdir(sub):
                files += [os.path.join(sub, f) for f in os.listdir(sub)]
        if len(files) <= keep:
            return
        files.sort(key=lambda f: os.path.getmtime(f))
        for f in files[:len(files) - keep]:
            os.remove(f)
    except OSError:
        pass


def load_thumbnail(path, max_side=220):
    """Fast small preview for the filmstrip (decodes at reduced size when possible)."""
    if path.lower().endswith(".cxr"):
        rgb, _ = load_image(path, max_side=max_side * 2, use_cache=False, file_mix_only=True)
        img = (rgb[:, :, ::-1] * 255).astype(np.uint8)
        h, w = img.shape[:2]
        k = max_side / max(h, w)
        if k < 1:
            img = cv2.resize(img, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(img[:, :, ::-1])
    data = np.fromfile(path, dtype=np.uint8)
    img = None
    if not path.lower().endswith(".exr"):
        img = cv2.imdecode(data, cv2.IMREAD_REDUCED_COLOR_4)
    if img is None:
        rgb, _ = load_image(path)
        img = (rgb[:, :, ::-1] * 255).astype(np.uint8)
    h, w = img.shape[:2]
    k = max_side / max(h, w)
    if k < 1:
        img = cv2.resize(img, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img[:, :, ::-1])


class OnnxModel:
    """Fixed-tile ONNX image model with tiled inference, seamless stitching and GPU throttling."""

    def __init__(self, models_dir, name, use_fp16=True):
        self.base = os.path.join(models_dir, name)
        self.name = name
        meta_path = self.base + ".json"
        self.meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as fh:
                self.meta = json.load(fh)
        self._load(use_fp16)

    def _load(self, use_fp16):
        ort = get_ort()
        p16, p32 = self.base + "_fp16.onnx", self.base + ".onnx"
        if use_fp16 and os.path.exists(p16):
            path = p16
        elif os.path.exists(p32):
            path = p32
        elif os.path.exists(p16):
            path = p16
        else:
            raise FileNotFoundError(f"Model not found: {self.name}")
        self.is_fp16 = path == p16

        so = ort.SessionOptions()
        so.enable_mem_pattern = False  # required by DirectML
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = PERF["cpu_threads"]
        so.inter_op_num_threads = 1
        providers = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider")
                     if p in ort.get_available_providers()]
        try:
            self.sess = ort.InferenceSession(path, so, providers=providers)
        except Exception:
            if path != p16 or not os.path.exists(p32):
                raise
            path = p32   # a broken half-precision copy: use the full-precision model
            self.is_fp16 = False
            self.sess = ort.InferenceSession(path, so, providers=providers)
        self.on_gpu = self.sess.get_providers()[0] == "DmlExecutionProvider"
        self.epoch = _GPU_EPOCH[0]

        inp = self.sess.get_inputs()[0]
        self.in_name = inp.name
        self.in_dtype = np.float16 if "float16" in inp.type else np.float32
        shp = inp.shape
        if isinstance(shp[2], int) and isinstance(shp[3], int):
            self.tile = int(shp[2])
        else:
            self.tile = int(self.meta.get("tile", 256))
        self.overlap = int(self.meta.get("overlap", min(32, self.tile // 8)))

        probe = self._infer(np.zeros((1, 3, self.tile, self.tile), self.in_dtype))
        self.scale = max(1, probe.shape[2] // self.tile)

    def tiles_for(self, h, w):
        step = self.tile - 2 * min(self.overlap, self.tile // 4)
        return math.ceil(h / step) * math.ceil(w / step)

    def _infer(self, x):
        if not self.on_gpu:
            return self.sess.run(None, {self.in_name: x})[0]
        with _GPU_LOCK:
            try:
                return self.sess.run(None, {self.in_name: x})[0]
            except Exception as e:
                if is_gpu_lost(e):
                    raise GpuLost(str(e)) from None
                raise

    def _reconnect(self):
        """Drops the dead GPU session and makes a new one (a new session gets a fresh DirectML device)."""
        with _GPU_LOCK:
            if self.epoch == _GPU_EPOCH[0]:
                _GPU_EPOCH[0] += 1
            self.sess = None
            import gc
            gc.collect()
            time.sleep(1.0)   # give the driver a moment to come back
            self._load(self.is_fp16)

    def run(self, rgb, control=None, progress=None):
        if self.on_gpu and self.epoch != _GPU_EPOCH[0]:   # another model saw a driver reset
            self._reconnect()
        try:
            return self._run_guarded(rgb, control, progress)
        except GpuLost:
            try:
                self._reconnect()
                return self._run_guarded(rgb, control, progress)
            except GpuLost:
                self.epoch = -1       # reconnect again next time
                raise GpuLost(GPU_LOST_MSG) from None
            except Exception as e:
                if is_gpu_lost(e):
                    self.epoch = -1
                    raise GpuLost(GPU_LOST_MSG) from None
                raise

    def _run_guarded(self, rgb, control=None, progress=None):
        try:
            return self._run(rgb, control, progress)
        except NonFinite:
            has_fp32 = os.path.exists(self.base + ".onnx")
            if not self.is_fp16 or not has_fp32:
                raise RuntimeError(f"{self.name} produced invalid values on this image.")
            self._load(False)  # fp16 overflow: fall back to full precision
            return self._run(rgb, control, progress)

    def _run(self, rgb, control, progress):
        H, W = rgb.shape[:2]
        t, s = self.tile, self.scale
        ov = min(self.overlap, t // 4)
        step = t - 2 * ov
        ny, nx = math.ceil(H / step), math.ceil(W / step)
        pad = np.pad(rgb, ((ov, ny * step - H + ov), (ov, nx * step - W + ov), (0, 0)), mode="reflect")
        out = np.empty((H * s, W * s, 3), np.float32)
        total, done = ny * nx, 0
        for iy in range(ny):
            for ix in range(nx):
                if control is not None:
                    control.check()
                y, x = iy * step, ix * step
                tile = pad[y:y + t, x:x + t].transpose(2, 0, 1)[None].astype(self.in_dtype)
                t0 = time.perf_counter()
                o = self._infer(tile)[0].transpose(1, 2, 0).astype(np.float32)
                busy = time.perf_counter() - t0
                if not np.isfinite(o).all():
                    raise NonFinite()
                core = o[ov * s:(ov + step) * s, ov * s:(ov + step) * s]
                h, w = min(step, H - y) * s, min(step, W - x) * s
                out[y * s:y * s + h, x * s:x * s + w] = core[:h, :w]
                done += 1
                if progress:
                    progress(done / total)
                limit = PERF["gpu_limit"]
                if limit < 0.999:  # duty cycle: leave the GPU idle part of the time
                    time.sleep(min(0.5, busy * (1.0 / limit - 1.0)))
        return np.clip(out, 0, 1)


# ----------------------------------------------------------------------------- pipeline

class Pipeline:
    """Holds loaded models/LUT/reference so a whole batch shares them."""

    def __init__(self, settings, models_dir, cache=None):
        self.s = with_defaults(settings)
        self.cache = cache if cache is not None else {}
        self.models_dir = models_dir
        self.lut = None
        if self.s["look_on"] and self.s["lut_path"]:
            if not os.path.isfile(self.s["lut_path"]):
                raise SettingError("lut_path", "The LUT file doesn't exist. Choose another .cube file or clear the field.")
            try:
                self.lut = load_cube(self.s["lut_path"])
            except Exception as e:
                raise SettingError("lut_path", f"LUT can't be used: {e}")
        self.ref_stats = None
        self.dn = self.up = self.grain = None
        self.atmo = None

    def prepare_atmosphere(self, src, key, path=None, notes=None):
        """Fog / light rays maps for this image (src = the full source image, after LightMix / Time of day)."""
        self.atmo = atmosphere.get_maps(src, self.s, key, self.models_dir, path, notes) \
            if atmosphere.fx_active(self.s) else None

    def load_reference(self, files):
        s = self.s
        if s["match_on"] and s["cm_strength"] > 0 and files:
            ref = s["ref_image"] or files[0]
            path = ref if os.path.isabs(ref) else os.path.join(s["input_dir"], ref)
            try:
                self.ref_stats = lab_stats(load_image(path)[0])
            except Exception as e:
                raise SettingError("ref_image", f"Reference image can't be read: {os.path.basename(path)} ({e})")

    def _model(self, name, field):
        kind = {"denoise_model": "denoise", "grain_model": "grain"}.get(field, "upscale")
        key = (kind, name, bool(self.s["fp16"]))
        if key not in self.cache:
            try:
                try:
                    self.cache[key] = OnnxModel(model_dir(self.models_dir, kind), name, self.s["fp16"])
                except GpuLost:
                    _GPU_EPOCH[0] += 1
                    time.sleep(1.0)
                    try:
                        self.cache[key] = OnnxModel(model_dir(self.models_dir, kind), name, self.s["fp16"])
                    except GpuLost:
                        raise GpuLost(GPU_LOST_MSG) from None
            except GpuLost:
                raise
            except Exception as e:
                raise SettingError(field, f"Model '{name}' can't be loaded: {e}")
        return self.cache[key]

    def load_models(self):
        s = self.s
        if s["denoise_on"] and s["denoise_strength"] > 0:
            if not s["denoise_model"]:
                raise SettingError("denoise_model", "Denoise is on but no model is selected. Pick a model or switch Denoise off.")
            self.dn = self._model(s["denoise_model"], "denoise_model")
            if self.dn.scale != 1:
                raise SettingError("denoise_model", f"'{s['denoise_model']}' is a {self.dn.scale}x model; the denoiser must be 1x.")
        if s["resize_on"] and s["upscale_model"]:
            self.up = self._model(s["upscale_model"], "upscale_model")
        if s["texture_on"] and s["grain_model"] and s["grain_amount"] > 0:
            self.grain = self._model(s["grain_model"], "grain_model")
            if self.grain.scale != 1:
                raise SettingError("grain_model", f"'{s['grain_model']}' is a {self.grain.scale}x model; grain models must be 1x.")

    def plan(self, w, h, full_size=None):
        """Output size and the number of model upscale passes for an image (or a crop of one)."""
        s = self.s
        fw, fh = full_size or (w, h)
        ftw, fth = target_size(fw, fh, s)
        tw, th = max(1, round(w * ftw / fw)), max(1, round(h * fth / fh))
        passes, cw, ch = 0, w, h
        while self.up is not None and passes < 2 and max(tw / cw, th / ch) >= s["min_model_scale"]:
            cw, ch = cw * self.up.scale, ch * self.up.scale
            passes += 1
        return tw, th, passes

    # The pipeline has two halves:
    #   AI stage:     denoise -> upscale -> resize to the final size          (slow, uses the models)
    #   finish stage: color match -> mood grade -> clarity -> sharpen -> grain (fast)
    # Keeping them apart lets the preview keep an AI result and re-apply quick edits on top instantly.

    def _rounds(self, w, h, full_size=None):
        """[(width, height, model passes)] for each upscale round. One round normally; with "Upscale multiple
        times" every round scales by the factor again (e.g. 2x, 4x, 8x), so a 4x model never goes past the
        size it's needed for."""
        s = self.s
        fw, fh = full_size or (w, h)
        n = upscale_times(s)
        if n <= 1:
            return [self.plan(w, h, full_size)]
        f = float(s["factor"])
        out, cw, ch = [], w, h
        for k in range(1, n + 1):
            ftw, fth = max(1, round(fw * f ** k)), max(1, round(fh * f ** k))
            tw, th = max(1, round(w * ftw / fw)), max(1, round(h * fth / fh))
            passes, pw, ph = 0, cw, ch
            while self.up is not None and passes < 2 and max(tw / pw, th / ph) >= s["min_model_scale"]:
                pw, ph = pw * self.up.scale, ph * self.up.scale
                passes += 1
            out.append((tw, th, passes))
            cw, ch = tw, th
        return out

    def ai_stage(self, rgb, full_size=None, control=None, progress=None, keep_pre=False):
        """Denoise, then the upscale round(s). keep_pre=True also returns the denoised image at its own
        size (for the "without upscaling" copy)."""
        s = self.s
        h, w = rgb.shape[:2]
        rounds = self._rounds(w, h, full_size)
        stages = []
        if self.dn is not None:
            stages.append(("Denoising", 8.0 * self.dn.tiles_for(h, w)))
        cw, ch = w, h
        for r, (tw, th, passes) in enumerate(rounds):
            area = 1
            for i in range(passes):
                label = "Upscaling" + (f" ({r + 1}/{len(rounds)})" if len(rounds) > 1 else (" (pass 2)" if i else ""))
                stages.append((label, 1.0 * self.up.tiles_for(ch * area, cw * area)))
                area *= self.up.scale
            stages.append(("Resizing", 0.2))
            cw, ch = tw, th
        total = sum(wt for _, wt in stages)
        done = [0.0]

        def stage(i):
            name, wt = stages[i]
            begin = done[0]
            done[0] += wt
            if progress:
                progress(begin / total, name)
            return (lambda f: progress((begin + f * wt) / total, name)) if progress else None

        k = 0
        if self.dn is not None:
            d = self.dn.run(rgb, control, stage(k))
            k += 1
            a = s["denoise_strength"]
            rgb = d if a >= 1 else np.clip(rgb + (d - rgb) * a, 0, 1)
        pre = rgb
        for tw, th, passes in rounds:
            for _ in range(passes):
                rgb = self.up.run(rgb, control, stage(k))
                k += 1
            stage(k)
            k += 1
            if control is not None:
                control.check()
            rgb = resize(rgb, tw, th)
        return (rgb, pre) if keep_pre else rgb

    def finish_stage(self, rgb, full_out_size, out_origin=(0, 0), src_stats=None, control=None, progress=None,
                     s=None, atmo=False):
        """s / atmo: this image's settings and fog / rays maps (default: the pipeline's own)."""
        s = s if s is not None else self.s
        atmo = self.atmo if atmo is False else atmo
        if progress:
            progress(0.0, "Finishing")
        if control is not None:
            control.check()
        if self.ref_stats is not None:
            rgb = color_match(rgb, self.ref_stats, s["cm_strength"], src_stats)
        if atmo is not None:           # fog and light rays are part of the scene: before the mood grade
            ox, oy = out_origin
            rgb = par_rows(lambda part, y0: atmosphere.apply(part, atmo, full_out_size, (ox, oy + y0)), rgb)
        lut = self.lut
        rgb = par_rows(lambda part, _y0: grade(part, s, lut), rgb)
        if s["texture_on"]:
            rgb = clarity(rgb, s["clarity"], max(full_out_size))
        if s["sharpen_on"]:
            rgb = unsharp_luma(rgb, s["sharpen_amount"], s["sharpen_radius"], s["sharpen_threshold"])
        if s["texture_on"] and s["grain_amount"] > 0:  # last, so the grain itself isn't sharpened
            if self.grain is not None:
                g = self.grain.run(rgb, control, (lambda f: progress(f, "Adding grain")) if progress else None)
                rgb = np.clip(rgb + (g - rgb) * min(float(s["grain_amount"]), 4.0), 0, 1)
            else:
                rgb = apply_grain(rgb, s, out_origin)
        if progress:
            progress(1.0, "Done")
        return rgb

    def process(self, rgb, alpha, full_size=None, src_stats=None, control=None, progress=None, origin=(0, 0)):
        """progress(fraction, stage_text). full_size / src_stats / origin (crop position in the source)
        let a crop behave exactly as inside the full image."""
        h, w = rgb.shape[:2]
        fw, fh = full_size or (w, h)
        ftw, fth = target_size(fw, fh, self.s)
        out_origin = (int(math.floor(origin[0] * ftw / fw + 0.5)), int(math.floor(origin[1] * fth / fh + 0.5)))
        ai_part = 0.97 if (self.dn is not None or self.up is not None) else 0.5
        p1 = (lambda f, st: progress(f * ai_part, st)) if progress else None
        p2 = (lambda f, st: progress(ai_part + f * (1 - ai_part), st)) if progress else None
        out = self.ai_stage(rgb, full_size, control, p1)
        out = self.finish_stage(out, (ftw, fth), out_origin, src_stats, control, p2)
        if alpha is not None:
            alpha = resize_alpha(alpha, out.shape[1], out.shape[0])
        return out, alpha


FINISH_MAX_SHARPEN_RADIUS = 6.0


class RegionResult:
    """The AI-stage result for part of an image, with extra context around it, ready to be finished
    (and re-finished with different quick edits) without running the models again."""

    def __init__(self, buf, rect, ex0, ey0, full_size, full_out, trim):
        self.buf, self.rect, self.ex0, self.ey0 = buf, rect, ex0, ey0
        self.full_size, self.full_out, self.trim = full_size, full_out, trim

    def covers(self, rect):
        x0, y0, x1, y1 = self.rect
        return x0 <= rect[0] + 1 and y0 <= rect[1] + 1 and x1 >= rect[2] - 1 and y1 >= rect[3] - 1


def ai_region(pipe, src, rect, control=None, progress=None):
    """Runs the AI stage on part of a full-resolution image (x0, y0, x1, y1), with enough context that
    finishing it later matches what the batch produces for those pixels, for any quick-edit settings."""
    s = pipe.s
    fh, fw = src.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in rect]
    ftw, fth = target_size(fw, fh, s)
    kx, ky = ftw / fw, fth / fh
    long_out = max(ftw, fth)
    out_margin = int(math.ceil(3 * max(1.5, 0.012 * long_out))) + 2          # clarity, whatever its amount
    out_margin += int(math.ceil(3 * max(FINISH_MAX_SHARPEN_RADIUS, s["sharpen_radius"]))) + 1
    m = int(math.ceil(out_margin / max(1e-6, min(kx, ky))))
    m += 4  # a few extra pixels so resampling at the crop edge sees the same neighbours
    if pipe.dn is not None or pipe.up is not None:
        m += 24  # AI models look at a neighbourhood too

    def snap(v, k, step, lo, hi):
        """Moves a crop edge to a spot that lands exactly on the output pixel grid, so the crop is
        resampled in phase with the full image (matters for non-integer scales like 1.5x)."""
        best, err = v, 1.0
        for i in range(0, 64):
            c = v + step * i
            if c < lo or c > hi:
                break
            e = abs(c * k - round(c * k))
            if e < err - 1e-9:
                best, err = c, e
            if e < 1e-6:
                break
        return best

    ex0 = snap(max(0, x0 - m), kx, -1, 0, fw)
    ey0 = snap(max(0, y0 - m), ky, -1, 0, fh)
    ex1 = snap(min(fw, x1 + m), kx, 1, 0, fw)
    ey1 = snap(min(fh, y1 + m), ky, 1, 0, fh)
    buf = pipe.ai_stage(src[ey0:ey1, ex0:ex1], (fw, fh), control, progress)
    # trim using the same absolute rounding as the full image's pixel grid
    ox = int(math.floor(x0 * kx + 0.5)) - int(math.floor(ex0 * kx + 0.5))
    oy = int(math.floor(y0 * ky + 0.5)) - int(math.floor(ey0 * ky + 0.5))
    ow = max(1, int(math.floor(x1 * kx + 0.5)) - int(math.floor(x0 * kx + 0.5)))
    oh = max(1, int(math.floor(y1 * ky + 0.5)) - int(math.floor(y0 * ky + 0.5)))
    return RegionResult(buf, (x0, y0, x1, y1), ex0, ey0, (fw, fh), (ftw, fth), (ox, oy, ow, oh))


def finish_region(pipe, region, src_stats=None, control=None, progress=None):
    """Applies the quick edits to an AI-stage region and trims it to the requested area."""
    fw, fh = region.full_size
    ftw, fth = region.full_out
    out_origin = (int(math.floor(region.ex0 * ftw / fw + 0.5)), int(math.floor(region.ey0 * fth / fh + 0.5)))
    out = pipe.finish_stage(region.buf, (ftw, fth), out_origin, src_stats, control, progress)
    ox, oy, ow, oh = region.trim
    return out[oy:oy + oh, ox:ox + ow]


def process_region(pipe, src, rect, src_stats=None, control=None, progress=None):
    """Runs the whole pipeline on part of a full-resolution image; matches the batch for those pixels."""
    p1 = (lambda f, st: progress(f * 0.95, st)) if progress else None
    p2 = (lambda f, st: progress(0.95 + f * 0.05, st)) if progress else None
    region = ai_region(pipe, src, rect, control, p1)
    return finish_region(pipe, region, src_stats, control, p2)


def grade_strips(rgb, s, lut, ref_stats, src_stats, check=None, rows=384, atmo=None):
    """Grades a big image in strips so a newer preview request can interrupt it early."""
    out = np.empty_like(rgb)
    H, W = rgb.shape[:2]
    for y in range(0, rgb.shape[0], rows):
        if check is not None and check():
            return None
        part = rgb[y:y + rows]
        if ref_stats is not None and s["cm_strength"] > 0:
            part = color_match(part, ref_stats, s["cm_strength"], src_stats)
        if atmo is not None:
            part = atmosphere.apply(part, atmo, (W, H), (0, y))
        out[y:y + rows] = grade(part, s, lut)
    return out


def run_batch(settings, models_dir, cache, control, on_progress, on_log):
    """on_progress(index, count, name, fraction_of_this_image, stage_text)."""
    s = with_defaults(settings)
    in_dir = s["input_dir"]
    if not in_dir or not os.path.isdir(in_dir):
        raise SettingError("input_dir", "The input folder doesn't exist. Choose the folder with your renders.")
    files = list_images(in_dir, s)
    if s.get("_files"):          # the images chosen in the app, in the app's order
        avail = set(files)
        files = [f for f in s["_files"] if f in avail]
    if not files:
        raise SettingError("input_dir", "No images to process in the input folder (check Show: Images / CXR "
                                        "at the bottom of the window).")
    out_dir = s["output_dir"] or os.path.join(in_dir, "processed")
    if os.path.normcase(os.path.abspath(out_dir)) == os.path.normcase(os.path.abspath(in_dir)) and not s["suffix"]:
        raise SettingError("suffix", "The output folder is the input folder and the name suffix is empty, "
                                     "so the originals would be overwritten. Add a suffix or pick another folder.")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        raise SettingError("output_dir", f"The output folder can't be created: {e}")

    on_progress(0, len(files), "", 0.0, "Loading models")
    p = Pipeline(s, models_dir, cache)
    p.load_models()
    p.load_reference(files)
    if p.dn:
        on_log(f"Denoiser: {p.dn.name} ({'fp16' if p.dn.is_fp16 else 'fp32'}, {'GPU' if p.dn.on_gpu else 'CPU'})")
    if p.up:
        on_log(f"Upscaler: {p.up.name} ({p.up.scale}x, {'fp16' if p.up.is_fp16 else 'fp32'}, {'GPU' if p.up.on_gpu else 'CPU'})")

    done = skipped = 0
    errors = []
    t0 = time.time()
    todo = []
    for i, name in enumerate(files):
        out_path = os.path.join(out_dir, output_name(name, s))
        if s["skip_existing"] and os.path.exists(out_path):
            skipped += 1
            on_progress(i, len(files), name, 1.0, "Skipped (already done)")
        else:
            todo.append((i, name, out_path))

    # Three stages overlap: while the GPU denoises / upscales image N, image N+1 is read from disk
    # and image N-1 is finished (grade, sharpen, grain) and saved. The GPU never waits for the disk.
    from concurrent.futures import ThreadPoolExecutor
    n = len(files)
    state = {"gpu": -1}          # index of the image the GPU is on (progress of older ones isn't shown)
    lock = threading.Lock()

    def load(i, name):
        control.check()
        path = os.path.join(in_dir, name)
        notes = []
        prog = lambda f: (on_progress(i, n, name, 0.02 * f, "Reading light passes")
                          if state["gpu"] in (-1, i) else None)     # only while nothing else is shown
        rgb, alpha = load_image(path, s, use_cache=False, notes=notes, progress=prog, cancel=control.check)
        maps = None
        if atmosphere.fx_active(s):
            maps = atmosphere.get_maps(rgb, s, (path, source_signature(s, path)), models_dir, path, notes)
        return rgb, alpha, notes, maps

    want_pre = bool(s.get("save_unscaled")) and s["resize_on"]

    def finish(i, name, out_path, ai, alpha, size, maps, t1, pre=None):
        try:
            h, w = size
            ftw, fth = target_size(w, h, s)
            si = dict(p.s, _grain_seed=name)
            if pre is not None:      # the same image without upscaling, for comparing upscalers
                pre_path = os.path.join(out_dir, unscaled_name(name, s))
                if not os.path.exists(pre_path):
                    po = p.finish_stage(pre, (w, h), (0, 0), None, control, None, s=si, atmo=maps)
                    save_image(po, alpha, pre_path, s["fmt"], s["bits"])
                    del po
                pre = None
            show = lambda f, st: (on_progress(i, n, name, 0.85 + 0.1 * f, "Finishing" if st == "Done" else st)
                                  if state["gpu"] == i else None)
            out = p.finish_stage(ai, (ftw, fth), (0, 0), None, control, show, s=si, atmo=maps)
            if alpha is not None:
                alpha = resize_alpha(alpha, out.shape[1], out.shape[0])
            if state["gpu"] == i:
                on_progress(i, n, name, 0.96, "Saving")
            save_image(out, alpha, out_path, s["fmt"], s["bits"])
            with lock:
                on_log(f"{name}: {w}x{h} → {out.shape[1]}x{out.shape[0]}  ({time.time() - t1:.1f}s)")
            on_progress(i, n, name, 1.0, "Done")
            return True
        except Cancelled:
            raise
        except Exception as e:
            with lock:
                errors.append(name)
                on_log(f"ERROR {name}: {e}")
            on_progress(i, n, name, 1.0, "Failed")
            return False

    loader = ThreadPoolExecutor(1)
    finisher = ThreadPoolExecutor(1)
    pending = None
    try:
        nxt = loader.submit(load, todo[0][0], todo[0][1]) if todo else None
        for k, (i, name, out_path) in enumerate(todo):
            try:
                control.check()
            except Cancelled:
                on_log("Cancelled.")
                break
            t1 = time.time()
            state["gpu"] = i
            on_progress(i, n, name, 0.0, "Loading")
            fut, nxt = nxt, None
            try:
                rgb, alpha, notes, maps = fut.result()
            except Cancelled:
                on_log("Cancelled.")
                break
            except Exception as e:
                errors.append(name)
                on_log(f"ERROR {name}: {e}")
                on_progress(i, n, name, 1.0, "Failed")
                if k + 1 < len(todo):
                    nxt = loader.submit(load, todo[k + 1][0], todo[k + 1][1])
                continue
            if k + 1 < len(todo):
                nxt = loader.submit(load, todo[k + 1][0], todo[k + 1][1])   # read the next one meanwhile
            for note in dict.fromkeys(notes):
                on_log(f"{name}: {note}")
            try:
                h, w = rgb.shape[:2]
                big = size_problem(w, h, s)
                if big:
                    raise RuntimeError(big)
                res = p.ai_stage(rgb, None, control,
                                 lambda f, st: on_progress(i, n, name, 0.02 + 0.83 * f, st), keep_pre=want_pre)
                ai, pre = res if want_pre else (res, None)
                del rgb, res
            except Cancelled:
                on_log("Cancelled.")
                break
            except Exception as e:
                errors.append(name)
                on_log(f"ERROR {name}: {e}")
                on_progress(i, n, name, 1.0, "Failed")
                continue
            if pending is not None:          # at most one image waiting to be saved (memory)
                try:
                    if pending.result():
                        done += 1
                except Cancelled:
                    on_log("Cancelled.")
                    pending = None
                    break
            on_progress(i, n, name, 0.85, "Finishing")
            pending = finisher.submit(finish, i, name, out_path, ai, alpha, (h, w), maps, t1, pre)
            del ai, pre
        if pending is not None:
            try:
                if pending.result():
                    done += 1
            except Cancelled:
                on_log("Cancelled.")
    finally:
        if nxt is not None:
            nxt.cancel()
        loader.shutdown(wait=True, cancel_futures=True)
        finisher.shutdown(wait=True, cancel_futures=True)

    report = f"{done} processed, {skipped} skipped, {len(errors)} errors in {time.time() - t0:.0f}s"
    on_log(report)
    return out_dir, report, done, len(errors)

"""
Atmosphere: Time of day (relight), Fog and Light rays.

Time of day changes the image itself, like LightMix does:
  .cxr files are relit through their light passes (sun / sky / interior lights each get their own
  intensity and color); other images are relit with a grade that keeps warm bright areas (lamps) lit.
Fog and Light rays need to know how far away each pixel is. The depth comes from, in this order:
  a Corona ZDepth pass in the .cxr (CGeometry_ZDepth: white = near), the AI depth model
  (Depth Anything V2 Small, downloaded once), or a rough guess from the image.
They're computed once per image as smooth "maps" at a working resolution and then applied to any
crop at any size (preview, Region, Detail check, batch), so all of them match.
"""
import math
import os
import re
import threading
from collections import OrderedDict

import cv2
import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)

DEPTH_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
DEPTH_FILE = "depth_anything_v2_small.onnx"
DEPTH_MIN_BYTES = 60_000_000      # the model is about 99 MB; anything much smaller is a broken download
DEPTH_SIDE = 700                  # long side of the image the depth model looks at (multiple of 14)
MAP_SIDE = 1024                   # working resolution of the fog / glow / rays maps
RAY_SIDE = 640                    # light rays are soft: computed smaller, then scaled up

ATMO_DEFAULTS = {
    "atmo_on": False,
    "tod_mode": "off", "tod_amount": 1.0, "tod_lights": 1.0, "tod_sky": 1.0, "tod_warmth": 0.0,
    "fog_on": False, "fog_density": 0.3, "fog_start": 0.1, "fog_height": 0.0, "fog_glow": 0.3, "fog_color": [],
    "rays_on": False, "rays_intensity": 0.6, "rays_length": 0.5, "rays_threshold": 0.6, "rays_source": "auto",
    "rays_angle": 20.0, "rays_spread": 0.15, "rays_point": [], "rays_color": [],
}
TOD_KEYS = ("tod_mode", "tod_amount", "tod_lights", "tod_sky", "tod_warmth")
FOG_KEYS = ("fog_on", "fog_density", "fog_start", "fog_height", "fog_glow", "fog_color")
RAYS_KEYS = ("rays_on", "rays_intensity", "rays_length", "rays_threshold", "rays_source", "rays_angle", "rays_spread",
             "rays_point", "rays_color")


# ----------------------------------------------------------------------------- settings helpers

def _g(s, k):
    return s.get(k, ATMO_DEFAULTS[k])


def tod_active(s):
    return bool(_g(s, "atmo_on")) and _g(s, "tod_mode") in PRESETS and float(_g(s, "tod_amount")) > 0


def fog_active(s):
    return bool(_g(s, "atmo_on")) and bool(_g(s, "fog_on")) and float(_g(s, "fog_density")) > 0


def rays_active(s):
    return bool(_g(s, "atmo_on")) and bool(_g(s, "rays_on")) and float(_g(s, "rays_intensity")) > 0


def fx_active(s):
    return fog_active(s) or rays_active(s)


def _sig(s, keys):
    out = []
    for k in keys:
        v = _g(s, k)
        if isinstance(v, float):
            v = round(v, 4)
        elif isinstance(v, (list, tuple)):
            v = tuple(round(float(x), 4) for x in v)
        out.append(v)
    return repr(tuple(out))


def source_signature(s):
    """Settings that change the source image itself (Time of day)."""
    return "tod" + _sig(s, TOD_KEYS) if tod_active(s) else ""


def fx_signature(s):
    if not fx_active(s):
        return ""
    return (_sig(s, FOG_KEYS) if fog_active(s) else "") + "|" + (_sig(s, RAYS_KEYS) if rays_active(s) else "")


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def srgb_to_linear(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4)).astype(np.float32)


def linear_to_srgb(x):
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055).astype(np.float32)


# ----------------------------------------------------------------------------- Time of day

# (intensity multiplier, color) per kind of light. Colors are LightMix colors (display values).
PRESETS = {
    "day": {"sun": (1.15, (1.0, 1.0, 1.0)), "sky": (1.15, (1.0, 1.0, 1.0)), "interior": (0.35, (1.0, 1.0, 1.0))},
    "dusk": {"sun": (0.4, (1.0, 0.55, 0.28)), "sky": (0.3, (0.8, 0.72, 1.0)), "interior": (1.5, (1.0, 0.88, 0.72))},
    "night": {"sun": (0.0, (1.0, 1.0, 1.0)), "sky": (0.04, (0.42, 0.56, 1.0)), "interior": (2.2, (1.0, 0.9, 0.78))},
}
_SUN = re.compile(r"sun|solar", re.I)
_SKY = re.compile(r"environment|\benv\b|sky|hdri|dome|background|portal|daylight|\bibl\b|window", re.I)


def light_category(name):
    """'sun', 'sky' or 'interior', guessed from the light's name in the LightMix."""
    if _SUN.search(name):
        return "sun"
    if _SKY.search(name):
        return "sky"
    return "interior"


def _warmth(w):
    """Color factor for the interior lights' warmth slider (-1 cooler .. +1 warmer)."""
    w = float(w)
    if w >= 0:
        return np.array([1.0, 1.0 - 0.14 * w, 1.0 - 0.38 * w], np.float32)
    w = -w
    return np.array([1.0 - 0.3 * w, 1.0 - 0.1 * w, 1.0], np.float32)


def tod_overrides(info, overrides, s):
    """LightMix overrides with the Time of day preset folded in. Returns (overrides, relit) where relit is
    False when the file has no sun / sky light passes (then the image is relit with the grade instead)."""
    if not tod_active(s):
        return overrides, True
    names = [n for n, _c in info.lights]
    cats = {n: light_category(n) for n in names}
    if not any(c in ("sun", "sky") for c in cats.values()):
        return overrides, False
    preset = PRESETS[_g(s, "tod_mode")]
    a = min(max(float(_g(s, "tod_amount")), 0.0), 1.0)
    out = {k: dict(v) for k, v in (overrides or {}).items()}
    for n in names:
        cat = cats[n]
        m, col = preset[cat]
        col = np.asarray(col, np.float32)
        if cat == "interior":
            m *= float(_g(s, "tod_lights"))
            col = col * _warmth(_g(s, "tod_warmth"))
        else:
            m *= float(_g(s, "tod_sky"))
        mm = 1.0 + (m - 1.0) * a
        tint = 1.0 + (col - 1.0) * a
        d = out.setdefault(n, {})
        d["mult"] = float(d.get("mult", 1.0)) * mm
        fm = info.file_mix.get(n, {"color": [1.0, 1.0, 1.0]})
        base = np.asarray(d.get("color") or fm["color"], np.float32)
        d["color"] = [float(v) for v in base * tint]
        if cat == "interior" and _g(s, "tod_mode") in ("dusk", "night") and d.get("on") is None and a > 0.5:
            d["on"] = True
    return out, True


def _light_spots(small, warm_s):
    """Tells lamps from daylight in an ordinary image. Daylight (windows, sun patches on the floor) makes
    bright areas that are large and have hard edges; lamps are small bright spots, and the light they throw
    on walls fades out softly. Works on a small copy (relative brightness, warmth); returns two soft 0..1
    masks (lamp spots, daylight) at that small size."""
    sh, sw = small.shape
    bright = (small > 0.55).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    area = stats[:, cv2.CC_STAT_AREA].astype(np.float32) / float(sw * sh)
    lg = np.log(small + 1e-3)
    grad = np.sqrt(cv2.Sobel(lg, cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(lg, cv2.CV_32F, 0, 1) ** 2) / 8.0
    ring = cv2.dilate(bright, np.ones((3, 3), np.uint8)) - bright
    rlab = cv2.dilate(lab.astype(np.float32), np.ones((3, 3), np.uint8)).astype(np.int32) * ring
    edge = np.zeros(n, np.float32)
    if n > 1:
        cnt = np.bincount(rlab.ravel(), minlength=n).astype(np.float32)
        sm = np.bincount(rlab.ravel(), weights=grad.ravel(), minlength=n).astype(np.float32)
        edge = sm / np.maximum(cnt, 1)
    big = np.zeros(n, np.float32)
    big[1:] = _smooth(0.004, 0.02, area[1:]) * _smooth(0.05, 0.14, edge[1:])   # large + hard-edged
    day = big[lab]
    # a lamp is warm or close to white-hot; a small cool highlight (a lit edge) isn't
    peak = np.zeros(n, np.float32)
    np.maximum.at(peak, lab.ravel(), small.ravel())
    wsum = np.bincount(lab.ravel(), weights=warm_s.ravel(), minlength=n).astype(np.float32)
    wmean = wsum / np.maximum(stats[:, cv2.CC_STAT_AREA].astype(np.float32), 1)
    lampish = (wmean > 0.12) | (peak > 0.97)
    spot = ((area < 0.006) & (np.arange(n) > 0) & lampish).astype(np.float32)[lab]
    grow = max(3, int(round(max(sw, sh) / 45)))
    day = cv2.dilate(day, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow)))
    day = cv2.GaussianBlur(day, (0, 0), grow / 2.5)
    spot = cv2.GaussianBlur(cv2.dilate(spot, np.ones((5, 5), np.uint8)), (0, 0), grow / 2.0)
    return np.clip(spot * 2.5, 0, 1), np.clip(day * 1.3, 0, 1)


def _tod_analysis(lin_small):
    """Whole-image facts Time of day needs (computed on a small copy): the brightness scale and where the
    lamps / the daylight are."""
    L = lin_small @ LUMA
    p = float(np.percentile(L, 99.5)) + 1e-6
    Lp = L / p
    warm = np.clip((lin_small[..., 0] - lin_small[..., 2]) / (lin_small.sum(-1) + 1e-5) * 4.0, 0, 1)
    spot, day = _light_spots(Lp.astype(np.float32), warm.astype(np.float32))
    return p, spot, day


def _small(img, side=480):
    h, w = img.shape[:2]
    k = min(1.0, side / max(h, w))
    return cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)


def _tod_pixels(lin, s, p, spot, day):
    """The per-pixel part of Time of day (spot / day: the masks at this part's size)."""
    mode = _g(s, "tod_mode")
    a = min(max(float(_g(s, "tod_amount")), 0.0), 1.0)
    sky, lit = float(_g(s, "tod_sky")), float(_g(s, "tod_lights"))
    Lp = (lin @ LUMA) / p
    warm = np.clip((lin[..., 0] - lin[..., 2]) / (lin.sum(-1) + 1e-5) * 4.0, 0, 1)
    lampness = np.maximum(warm, spot) * (1.0 - day)
    lamp = (_smooth(0.25, 0.8, Lp) * lampness)[..., None].astype(np.float32)
    lamp_col = np.array([1.0, 0.9, 0.78], np.float32) * _warmth(_g(s, "tod_warmth"))
    if mode == "night":
        amb = lin * (0.06 * sky) * np.array([0.5, 0.68, 1.15], np.float32)
        out = amb * (1 - lamp) + lin * lamp * (1.8 * lit) * lamp_col
        y = (out @ LUMA)[..., None]
        out = y + (out - y) * 0.85
    elif mode == "dusk":
        t = np.clip(Lp, 0, 1)[..., None]
        tint = (1 - t) * np.array([0.72, 0.78, 1.08], np.float32) + t * np.array([1.18, 0.92, 0.68], np.float32)
        amb = lin * (0.5 * sky) * tint
        out = amb * (1 - lamp) + lin * lamp * (1.2 * lit) * lamp_col
    else:  # day
        out = lin * (1.2 * sky) * np.array([0.98, 1.0, 1.04], np.float32) * (1 - lamp * 0.25) + lin * lamp * 0.25 * lit
    return (lin + (out - lin) * a).astype(np.float32)


def _tod_run(img, s, analysis, convert):
    """Runs the per-pixel part on strips across the CPU cores. convert: img is display RGB (else linear)."""
    import engine as E
    p, spot_s, day_s = analysis
    h, w = img.shape[:2]
    spot = cv2.resize(spot_s, (w, h), interpolation=cv2.INTER_LINEAR)
    day = cv2.resize(day_s, (w, h), interpolation=cv2.INTER_LINEAR)

    def part(x, y0):
        rows = slice(y0, y0 + x.shape[0])
        lin = srgb_to_linear(x) if convert else x
        out = _tod_pixels(lin, s, p, spot[rows], day[rows])
        return np.clip(linear_to_srgb(out), 0, 1).astype(np.float32) if convert else out
    return E.par_rows(part, np.ascontiguousarray(img, dtype=np.float32))


def tod_linear(lin, s):
    """Time of day for an ordinary image (linear RGB in and out): the scene light changes and warm bright
    areas (lamps) stay lit."""
    return _tod_run(lin, s, _tod_analysis(_small(lin)), convert=False)


def tod_image(rgb, s):
    """Time of day for an ordinary (non-.cxr) image, display RGB 0..1 in and out."""
    if not tod_active(s):
        return rgb
    analysis = _tod_analysis(srgb_to_linear(_small(np.clip(rgb, 0, 1).astype(np.float32))))
    return _tod_run(rgb, s, analysis, convert=True)


# ----------------------------------------------------------------------------- depth

def depth_model_path(models_dir):
    return os.path.join(models_dir, "depth", DEPTH_FILE)


def has_depth_model(models_dir):
    p = depth_model_path(models_dir)
    return os.path.isfile(p) and os.path.getsize(p) >= DEPTH_MIN_BYTES


def download_depth_model(models_dir, progress=None, cancel=None):
    """Downloads Depth Anything V2 Small (Apache-2.0) into models/depth. progress(fraction or -1);
    cancel() raises to stop."""
    import urllib.request
    dst = depth_model_path(models_dir)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    try:
        req = urllib.request.Request(DEPTH_URL, headers={"User-Agent": "RenderBatch"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None:
                    cancel()
                b = r.read(1 << 20)
                if not b:
                    break
                fh.write(b)
                done += len(b)
                if progress:
                    progress(done / total if total else -1)
        if os.path.getsize(tmp) < DEPTH_MIN_BYTES:
            raise RuntimeError("the download was incomplete")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    _SESS.clear()
    return dst


_SESS = {}
_LOCK = threading.Lock()
_DEPTH = OrderedDict()     # (path, mtime, size) -> (dist map, source text)
_MAPS = OrderedDict()      # (source key, fx signature, depth key) -> Maps


def _session(models_dir):
    import engine as E
    path = depth_model_path(models_dir)
    key = (path, os.path.getmtime(path))
    if key in _SESS:
        return _SESS[key]
    ort = E.get_ort()
    so = ort.SessionOptions()
    so.enable_mem_pattern = False
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.intra_op_num_threads = E.PERF["cpu_threads"]
    providers = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    sess = ort.InferenceSession(path, so, providers=providers)
    on_gpu = sess.get_providers()[0] == "DmlExecutionProvider"
    _SESS.clear()
    _SESS[key] = (sess, on_gpu)
    return _SESS[key]


def _run_depth_model(rgb, models_dir):
    """Relative inverse depth (bigger = nearer) from the AI model, at the model's resolution."""
    import engine as E
    h, w = rgb.shape[:2]
    k = DEPTH_SIDE / max(h, w)
    tw, th = max(14, int(round(w * k / 14)) * 14), max(14, int(round(h * k / 14)) * 14)
    x = cv2.resize(np.clip(rgb, 0, 1).astype(np.float32), (tw, th), interpolation=cv2.INTER_AREA)
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None], np.float32)
    for attempt in (0, 1):
        sess, on_gpu = _session(models_dir)
        feed = {sess.get_inputs()[0].name: x}
        try:
            if on_gpu:
                with E._GPU_LOCK:
                    out = sess.run(None, feed)[0]
            else:
                out = sess.run(None, feed)[0]
            break
        except Exception as e:
            _SESS.clear()
            if attempt or not E.is_gpu_lost(e):
                raise
    d = np.squeeze(np.asarray(out, np.float32))
    if d.ndim != 2:
        raise RuntimeError(f"unexpected depth output shape {np.asarray(out).shape}")
    return d


def _guided(I, p, r, eps):
    """Edge-aware upsampling helper (guided filter): p follows the edges of I."""
    def box(x):
        return cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1))
    mI, mp = box(I), box(p)
    a = (box(I * p) - mI * mp) / (box(I * I) - mI * mI + eps)
    b = mp - a * mI
    return box(a) * I + box(b)


def _map_size(w, h, side=MAP_SIDE):
    k = min(1.0, side / max(w, h))
    return max(1, round(w * k)), max(1, round(h * k))


def _depth_source_image(path, s):
    """The image depth is estimated from: the file as rendered (no LightMix / Time of day changes), so the
    depth stays the same while you relight, and preview and batch agree."""
    import engine as E
    if path.lower().endswith(".cxr"):
        import corona
        return corona.render(path, {"cxr_denoised": s.get("cxr_denoised", "file")}, max_side=1400,
                             file_mix_only=True)
    rgb, _ = E.load_image(path)
    return rgb


def get_depth(path, s, models_dir, fallback_rgb=None):
    """Distance map (0 = nearest, 1 = farthest) at map resolution, and where it came from."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime, st.st_size, has_depth_model(models_dir))
    except (OSError, TypeError):
        key = None
    with _LOCK:
        if key is not None and key in _DEPTH:
            _DEPTH.move_to_end(key)
            return _DEPTH[key]
    dist, where = None, ""
    if path and path.lower().endswith(".cxr"):
        try:
            import corona
            z = corona.zdepth(corona.CxrInfo(path))
            if z is not None:
                rgb = _depth_source_image(path, s)
                mw, mh = _map_size(rgb.shape[1], rgb.shape[0])
                z = cv2.resize(z, (mw, mh), interpolation=cv2.INTER_AREA)
                dist, where = np.clip(1.0 - z, 0, 1).astype(np.float32), "ZDepth pass"
        except Exception:
            dist = None
    if dist is None:
        try:
            rgb = _depth_source_image(path, s) if path else fallback_rgb
        except Exception:
            rgb = fallback_rgb
        if rgb is None:
            rgb = fallback_rgb
        mw, mh = _map_size(rgb.shape[1], rgb.shape[0])
        small = cv2.resize(rgb, (mw, mh), interpolation=cv2.INTER_AREA)
        guide = (small @ LUMA).astype(np.float32)
        if has_depth_model(models_dir):
            d = _run_depth_model(rgb, models_dir)
            lo, hi = np.percentile(d, 1), np.percentile(d, 99)
            near = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
            near = cv2.resize(near, (mw, mh), interpolation=cv2.INTER_CUBIC)
            near = np.clip(_guided(guide, near.astype(np.float32), max(2, mw // 180), 1e-3), 0, 1)
            # disparity -> distance: far things are squeezed together in disparity, spread them out again
            dist = np.clip(1.0 - near, 0, 1) ** 0.6
            where = "AI depth (Depth Anything V2)"
        else:
            yy = ((np.arange(mh) + 0.5) / mh)[:, None].astype(np.float32)
            Lb = cv2.GaussianBlur(guide, (0, 0), max(1.0, mw / 60))
            Lp = Lb / (np.percentile(Lb, 99.5) + 1e-6)
            dist = np.clip(0.6 * (1 - yy) + 0.45 * _smooth(0.55, 1.0, Lp), 0, 1)
            dist = np.clip(_guided(guide, dist.astype(np.float32), max(2, mw // 100), 1e-2), 0, 1)
            where = "rough guess (depth model not installed)"
    res = (dist.astype(np.float32), where)
    if key is not None:
        with _LOCK:
            _DEPTH[key] = res
            while len(_DEPTH) > 12:
                _DEPTH.popitem(last=False)
    return res


# ----------------------------------------------------------------------------- fog, glow, rays

class Maps:
    """Fog amount (0..1), fog color (linear) and added light (glow + rays, linear) for one image,
    at map resolution; apply() scales them to any crop / size."""

    def __init__(self, fog, color, add, depth_from):
        self.fog = fog
        self.color = color
        self.add = add
        self.depth_from = depth_from


def _streaks(img, cx, cy, length, falloff, n=12, passes=3):
    """Smears every pixel's light away from (cx, cy) over `length` pixels: light rays.
    (cx, cy) far outside the image gives parallel beams (sunlight), near the light gives rays fanning out.
    3 passes of 12 samples = 1728 samples, so the rays are smooth."""
    h, w = img.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    ux, uy = cx - xs, cy - ys                      # toward the light
    d = np.sqrt(ux * ux + uy * uy) + 1e-6
    ux, uy = ux / d, uy / d
    out = img
    for p in range(passes):
        L = length / (n ** p)
        acc = np.zeros_like(img)
        wsum = 0.0
        for i in range(1, n + 1):
            step = L * i / n
            wt = math.exp(-falloff * i / n) if p == 0 else 1.0
            # don't sample past the light itself
            st = np.minimum(step, d) if p == 0 else step
            acc += wt * cv2.remap(out, xs + ux * st, ys + uy * st, cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            wsum += wt
        out = acc / wsum
    return out


def _rays_emitter_cxr(path, s, name, size):
    import corona
    info = corona.CxrInfo(path)
    layers = corona.load_layers(info, s.get("cxr_denoised", "file"), max_side=max(size), use_cache=True)
    if name not in layers:
        return None
    fm = info.file_mix.get(name, {"intensity": 1.0, "color": [1, 1, 1]})
    col = np.power(np.clip(np.asarray(fm["color"], np.float32), 0, None), 2.2)
    return cv2.resize(layers[name] * (fm["intensity"] * col), size, interpolation=cv2.INTER_AREA).astype(np.float32)


def _rays(lin, Lp, dist, s, path):
    mh, mw = lin.shape[:2]
    rw, rh = _map_size(mw, mh, RAY_SIDE)
    lin_r = cv2.resize(lin, (rw, rh), interpolation=cv2.INTER_AREA)
    thr = float(_g(s, "rays_threshold"))
    src = _g(s, "rays_source") or "auto"
    em = None
    if src != "auto" and path and path.lower().endswith(".cxr"):
        try:
            layer = _rays_emitter_cxr(path, s, src, (rw, rh))
        except Exception:
            layer = None
        if layer is not None:
            el = layer @ LUMA
            p = float(np.percentile(el, 99.5)) + 1e-6
            # the light's own contribution, but only where it's strong (the sun patch, the lamp shade...)
            em = layer * _smooth(max(0.0, thr - 0.35), thr + 0.05, el / p)[..., None]
            em *= float(np.percentile(lin_r @ LUMA, 99.5)) / p
    if em is None:
        Lr = cv2.resize(Lp, (rw, rh), interpolation=cv2.INTER_AREA)
        dr = cv2.resize(dist, (rw, rh), interpolation=cv2.INTER_AREA)
        em = lin_r * (_smooth(thr - 0.25, thr + 0.05, Lr) * (0.25 + 0.75 * dr))[..., None]
    e = em @ LUMA
    tot = float((e * e).sum())
    if tot <= 1e-12:
        return np.zeros((mh, mw, 3), np.float32)
    diag = math.hypot(rw, rh)
    pt = _g(s, "rays_point")
    if isinstance(pt, (list, tuple)) and len(pt) == 2:
        cx, cy = float(pt[0]) * rw, float(pt[1]) * rh            # rays fan out from a point you picked
    else:
        ys, xs = np.mgrid[0:rh, 0:rw]
        wgt = e * e
        ex, ey = float((wgt * xs).sum() / tot), float((wgt * ys).sum() / tot)
        # the light comes from this direction (0° = from above, 90° = from the right); spread 0 puts the
        # light far away (parallel sun beams), spread 1 at the bright area itself (rays fan out from a lamp)
        ang = math.radians(float(_g(s, "rays_angle")))
        spread = min(max(float(_g(s, "rays_spread")), 0.0), 1.0)
        far = diag * (0.15 + 12.0 * (1.0 - spread) ** 2)
        cx, cy = ex + math.sin(ang) * far, ey - math.cos(ang) * far
    lf = min(max(float(_g(s, "rays_length")), 0.0), 1.0)
    length = diag * (0.08 + 0.72 * lf)
    r = _streaks(em, cx, cy, length, falloff=3.0 * (1.15 - lf))
    r *= 1.6 * float(_g(s, "rays_intensity"))
    col = _g(s, "rays_color")
    if isinstance(col, (list, tuple)) and len(col) == 3:
        c = srgb_to_linear(np.asarray(col, np.float32))
        c = c / max(float(c @ LUMA), 1e-4)
        y = (r @ LUMA)[..., None]
        r = y * c
    return cv2.resize(r, (mw, mh), interpolation=cv2.INTER_LINEAR)


def build_maps(src, s, dist, depth_from, path=None):
    H, W = src.shape[:2]
    mw, mh = _map_size(W, H)
    small = cv2.resize(np.clip(src, 0, 1).astype(np.float32), (mw, mh), interpolation=cv2.INTER_AREA)
    lin = srgb_to_linear(small)
    if dist.shape != (mh, mw):
        dist = cv2.resize(dist, (mw, mh), interpolation=cv2.INTER_LINEAR)
    L = lin @ LUMA
    Lp = L / (float(np.percentile(L, 99.5)) + 1e-6)
    fog = np.zeros((mh, mw), np.float32)
    color = np.zeros(3, np.float32)
    add = np.zeros((mh, mw, 3), np.float32)
    if fog_active(s):
        dens = float(_g(s, "fog_density"))
        start = min(max(float(_g(s, "fog_start")), 0.0), 0.95)
        t = np.clip((dist - start) / (1.0 - start), 0, 1)
        f = 1.0 - np.exp(-4.0 * dens * np.power(t, 1.1))
        hgt = float(_g(s, "fog_height"))
        if hgt > 0:   # ground fog: thinner toward the top of the frame
            yy = ((np.arange(mh) + 0.5) / mh)[:, None]
            f = f * np.exp(-3.0 * hgt * (1.0 - yy))
        fog = np.clip(f, 0, 1).astype(np.float32)
        col = _g(s, "fog_color")
        if isinstance(col, (list, tuple)) and len(col) == 3:
            c = srgb_to_linear(np.asarray(col, np.float32))
            c = c * (float(np.mean(L)) * 1.2 / max(float(c @ LUMA), 1e-4))   # as bright as the scene's light
        else:
            far = dist > np.percentile(dist, 85)
            c_far = lin[far].mean(0) if far.any() else lin.reshape(-1, 3).mean(0)
            c = 0.5 * c_far + 0.5 * lin.reshape(-1, 3).mean(0)
            y = float(c @ LUMA)
            c = c * 0.65 + y * 0.35            # haze is less saturated than what lights it
        color = c.astype(np.float32)
        glow = float(_g(s, "fog_glow"))
        if glow > 0:   # light scattered by the fog: soft halos around bright areas
            hi = lin * _smooth(0.35, 1.0, Lp)[..., None]
            sig = 0.02 * max(mw, mh)
            import engine as E
            g = E._wide_blur(hi, sig) + 0.6 * E._wide_blur(hi, sig * 3)
            add += g * (glow * (0.35 + 0.65 * dens))
    if rays_active(s):
        add += _rays(lin, Lp, dist, s, path)
    return Maps(fog, color, add.astype(np.float32), depth_from)


def get_maps(src, s, key, models_dir, path=None, notes=None):
    """Maps for the full source image src (display RGB). key identifies the source (path + its settings)."""
    if not fx_active(s):
        return None
    dist, where = get_depth(path, s, models_dir, fallback_rgb=src) if path else \
        get_depth(None, s, models_dir, fallback_rgb=src)
    k = (key, fx_signature(s), where, src.shape)
    with _LOCK:
        if k in _MAPS:
            _MAPS.move_to_end(k)
            m = _MAPS[k]
            if notes is not None:
                notes.append(f"Fog / light rays depth: {m.depth_from}")
            return m
    m = build_maps(src, s, dist, where, path)
    with _LOCK:
        _MAPS[k] = m
        while len(_MAPS) > 8:
            _MAPS.popitem(last=False)
    if notes is not None:
        notes.append(f"Fog / light rays depth: {where}")
    return m


def maps_cached(key, s, shape):
    sig = fx_signature(s)
    with _LOCK:
        return any(k[0] == key and k[1] == sig and k[3] == shape for k in _MAPS)


def apply(rgb, maps, full_size, origin=(0, 0), fast=False):
    """Fog + added light on rgb (display RGB 0..1), which is the part of an image of full_size (w, h)
    that starts at origin (x, y). The same maps fit any size, so a crop matches the full image."""
    if maps is None:
        return rgb
    h, w = rgb.shape[:2]
    FW, FH = full_size
    mh, mw = maps.fog.shape
    sx, sy = mw / FW, mh / FH
    M = np.float32([[sx, 0, (origin[0] + 0.5) * sx - 0.5], [0, sy, (origin[1] + 0.5) * sy - 0.5]])
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    F = cv2.warpAffine(maps.fog, M, (w, h), flags=flags, borderMode=cv2.BORDER_REPLICATE)[..., None]
    A = cv2.warpAffine(maps.add, M, (w, h), flags=flags, borderMode=cv2.BORDER_REPLICATE)
    if fast:
        import engine as E
        s2l, l2s = E._tables()
        lin = s2l[(np.clip(rgb, 0, 1) * 4095 + 0.5).astype(np.int16)]
        out = lin * (1 - F) + maps.color * F + A
        return l2s[np.clip(out * (262143 / 16), 0, 262143).astype(np.int32)].clip(0, 1)
    lin = srgb_to_linear(rgb)
    out = lin * (1 - F) + maps.color * F + A
    return np.clip(linear_to_srgb(out), 0, 1).astype(np.float32)

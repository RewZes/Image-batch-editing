"""
Corona .cxr support: LightMix re-mixing and a reproduction of Corona's tone mapping (the "Post" panel).

The light-select passes are mixed exactly like Corona's LightMix (intensity x color, colors gamma-2.2 decoded),
then the file's own Post operators are applied in the file's own order. Operators were matched against Corona
Image Editor exports (most within 0.1/255; see README).
"""
import base64
import os
import re
import struct
import threading
import zlib

import numpy as np

from exr_reader import ExrFile, ExrError

_DATA = None
DISPLAY_GAMMA = 2.2


def _data():
    global _DATA
    if _DATA is None:
        d = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "corona_tm.npz"))
        _DATA = {k: d[k] for k in d.files}
    return _DATA


def is_corona(path):
    return path.lower().endswith(".cxr")


# ----------------------------------------------------------------------------- file / LightMix

def _split_list(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()]


class CxrInfo:
    """Header information of a .cxr: light layers, their LightMix settings, the Post pipeline."""

    def __init__(self, path):
        self.path = path
        self.exr = ExrFile(path)
        a = self.exr.attr
        self.width, self.height = self.exr.width, self.exr.height
        elements = re.findall(r'"([^"]*)"', a("corona.elements", "") or "")
        self.lights = []            # [(display name, channel base)]
        for el in elements:
            parts = el.split("|")
            if len(parts) >= 3 and parts[2] == "LightSelect" and not parts[1].startswith("Denoised"):
                self.lights.append((parts[0], parts[1]))
        chans = set(self.exr.channel_names)
        self.lights = [(n, c) for n, c in self.lights if f"{c}.R" in chans]
        ints = [float(v) for v in _split_list(a("corona.cm.lightmixintensities", ""))]
        cols = []
        for v in _split_list(a("corona.cm.lightmixcolors", "")):
            try:
                cols.append([float(x) for x in v.split()][:3])
            except ValueError:
                cols.append([1.0, 1.0, 1.0])
        ens = [v.lower() == "true" for v in _split_list(a("corona.cm.lightmixenabledlayers", ""))]
        self.file_mix = {}
        for i, (name, _c) in enumerate(self.lights):
            self.file_mix[name] = {
                "intensity": ints[i] if i < len(ints) else 1.0,
                "color": cols[i] if i < len(cols) and len(cols[i]) == 3 else [1.0, 1.0, 1.0],
                "on": ens[i] if i < len(ens) else True,
            }
        self.has_denoised = all(f"Denoised{c}.R" in chans for _n, c in self.lights) and bool(self.lights)
        self.denoise_on = str(a("corona.denoise.blendenabled", 0)) in ("1", "True", "true")
        try:
            self.denoise_amount = float(a("corona.denoise.blendamount", 1.0))
        except (TypeError, ValueError):
            self.denoise_amount = 1.0
        self.pipeline, self.warnings = parse_pipeline(a("corona.cm.pipeline", "") or "")
        if not self.lights:
            self.warnings.append("No LightSelect passes in this file: add LightMix light selects in Corona, "
                                 "or the beauty pass is used as it is.")
        self.header = self.exr.header


def light_names(info):
    return [n for n, _ in info.lights]


def effective_overrides(lightmix, groups):
    """Per-light changes with the light groups folded in: a group multiplies its lights' intensity,
    can switch them off, and its color (if set) replaces theirs."""
    eff = {k: dict(v) for k, v in (lightmix or {}).items() if isinstance(v, dict)}
    for g in groups or []:
        gm = float(g.get("mult", 1.0) or 1.0)
        for name in g.get("lights", []):
            d = eff.setdefault(name, {})
            d["mult"] = float(d.get("mult", 1.0)) * gm
            if g.get("on") is False:
                d["on"] = False
            if g.get("color"):
                d["color"] = g["color"]
    return {k: v for k, v in eff.items()
            if abs(float(v.get("mult", 1.0)) - 1) > 1e-9 or v.get("color") or v.get("on") is False}


def effective(s):
    """Per-light changes of the LightMix mode in use: 'individual' uses s['lightmix'];
    'group' uses the groups plus s['lightmix_grouped'] for the lights that aren't in a group."""
    s = s or {}
    if s.get("lightmix_mode", "individual") != "group":
        return effective_overrides(s.get("lightmix"), None)
    groups = s.get("light_groups") or []
    grouped = {l for g in groups for l in g.get("lights", [])}
    base = {k: v for k, v in (s.get("lightmix_grouped") or {}).items() if k not in grouped}
    return effective_overrides(base, groups)


def mix_signature(s):
    """Settings that change the mixed + tone-mapped source image (LightMix, Post, passes, Time of day)."""
    import atmosphere
    lm = effective(s)
    return repr((sorted((k, repr(sorted(v.items()))) for k, v in lm.items()),
                 bool(s.get("cxr_post", True)), s.get("cxr_denoised", "file"), atmosphere.source_signature(s)))


def zdepth(info, max_side=None):
    """Corona's ZDepth pass (CGeometry_ZDepth: white = near, black = far) as HxW 0..1, or None."""
    names = [c for c in info.exr.channel_names if "zdepth" in c.lower()]
    if not names:
        return None
    pick = next((c for c in names if c.endswith((".R", ".Y", ".Z"))), names[0])
    z = info.exr.read([pick])[pick].astype(np.float32)
    z = np.nan_to_num(z, nan=0.0, posinf=1.0, neginf=0.0)
    if z.max() > 1.5:          # unclipped / world units: scale the useful range into 0..1
        lo, hi = np.percentile(z, 1), np.percentile(z, 99)
        z = (z - lo) / max(hi - lo, 1e-6)
    return np.clip(z, 0, 1)


def _ram_budget():
    """Memory the light-pass cache may use: a quarter of the PC's RAM (at least 1 GB, at most 12 GB)."""
    return int(min(max(total_ram() // 4, 1 << 30), 12 << 30))


def total_ram():
    """The PC's physical memory in bytes."""
    total = 8 << 30
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            total = int(m.ullTotalPhys)
        else:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        pass
    return int(total)


class _Cache:
    """Light passes of recently opened .cxr files, newest last; the oldest are dropped when over budget,
    so going back to a file you already looked at doesn't read it again."""

    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}        # key -> {"layers": full, "small": {max_side: layers}, "bytes": n}
        self.budget = None

    def get(self, key):
        with self.lock:
            e = self.entries.pop(key, None)
            if e is not None:
                self.entries[key] = e    # move to newest
            return e

    def put(self, key, full):
        n = sum(v.nbytes for v in full.values())
        with self.lock:
            if self.budget is None:
                self.budget = _ram_budget()
            # an older version of the same file is useless now
            for k in [k for k in self.entries if k[0] == key[0] and k != key]:
                self.entries.pop(k)
            self.entries[key] = {"layers": full, "small": {}, "bytes": n}
            while len(self.entries) > 1 and sum(e["bytes"] for e in self.entries.values()) > self.budget:
                self.entries.pop(next(iter(self.entries)))
            return self.entries[key]

    def clear(self):
        with self.lock:
            self.entries.clear()


_cache = _Cache()


def load_layers(info, denoised_mode="file", max_side=None, progress=None, cancel=None, threads=4, use_cache=True):
    """{light name: HxWx3 float32 linear} (+ '__beauty__' when there are no light selects)."""
    ch = info.exr.channel_names
    use_dn = info.has_denoised and (denoised_mode == "always" or (denoised_mode == "file" and info.denoise_on))
    amount = 1.0 if denoised_mode == "always" else info.denoise_amount
    st = os.stat(info.path)
    key = (info.path, st.st_size, st.st_mtime, use_dn, amount)
    entry = _cache.get(key) if use_cache else None
    if entry is not None:
        if not max_side:
            return entry["layers"]
        if max_side in entry["small"]:
            return entry["small"][max_side]
        full = entry["layers"]
    else:
        full = None
    if full is None:
        names = []
        for _n, c in info.lights:
            names += [f"{c}.{q}" for q in "RGB"]
            if use_dn:
                names += [f"Denoised{c}.{q}" for q in "RGB"]
        if not info.lights:
            names = [q for q in "RGB" if q in ch]
        raw = info.exr.read(names, progress=progress, cancel=cancel, threads=threads)
        full = _Layers()
        if info.lights:
            for n, c in info.lights:
                img = np.stack([raw.pop(f"{c}.{q}") for q in "RGB"], -1)
                if use_dn:
                    dn = np.stack([raw.pop(f"Denoised{c}.{q}") for q in "RGB"], -1)
                    img = img * (1 - amount) + dn * amount if amount < 1 else dn
                full[n] = np.nan_to_num(img, nan=0.0, posinf=0.0, neginf=0.0)
        else:
            full["__beauty__"] = np.stack([raw[q] for q in "RGB"], -1)
        entry = _cache.put(key, full) if use_cache else None
    if not max_side:
        return full
    import cv2
    h, w = next(iter(full.values())).shape[:2]
    k = min(1.0, max_side / max(h, w))
    small = _Layers({n: (cv2.resize(v, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
                 if k < 1 else v) for n, v in full.items()})
    if entry is not None:
        entry["small"][max_side] = small
    return small


class _Layers(dict):
    """Light passes {name: image}; also carries the last LightMix result made from them (see mix)."""
    _mix = None


def _coefficients(info, layers, overrides):
    """{layer: RGB multiplier (zeros when off)} = intensity x color^2.2."""
    co = {}
    for name in layers:
        fm = info.file_mix.get(name, {"intensity": 1.0, "color": [1, 1, 1], "on": True})
        ov = overrides.get(name, {})
        on = fm["on"] if ov.get("on") is None else bool(ov["on"])
        if not on:
            co[name] = np.zeros(3, np.float32)
            continue
        inten = fm["intensity"] * float(ov.get("mult", 1.0))
        col = ov.get("color") or fm["color"]
        col = np.power(np.clip(np.asarray(col, np.float64), 0, None), DISPLAY_GAMMA)
        co[name] = (inten * col).astype(np.float32)
    return co


def mix(info, layers, overrides=None):
    """LightMix: sum of layer * intensity * color^2.2 for enabled layers. overrides: {name: {mult, color, on}}.
    The last result per set of layers is kept: changing one light only adds that light's difference
    (instead of summing every pass again), on all CPU cores. The returned array is read-only."""
    from engine import par_rows
    overrides = overrides or {}
    if "__beauty__" in layers:
        return layers["__beauty__"].copy()
    co = _coefficients(info, layers, overrides)
    first = next(iter(layers.values()))
    st = getattr(layers, "_mix", None)      # (coefficients, result, updates since a full mix), read at once
    if st is not None and set(st[0]) == set(co):
        old = st[0]
        changed = [n for n in co if not np.array_equal(co[n], old[n])]
        if not changed:
            return st[1]
        if len(changed) <= max(1, len(co) // 3) and st[2] < 24:
            delta = [(layers[n], co[n] - old[n]) for n in changed]

            def add(part, y0):
                out = part.copy()
                for img, d in delta:
                    out += img[y0:y0 + part.shape[0]] * d
                return out
            lin = par_rows(add, st[1])
            return _keep_mix(layers, co, lin, st[2] + 1)
    terms = [(img, co[n]) for n, img in layers.items() if co[n].any()]

    def full(part, y0):
        out = np.zeros_like(part)
        for img, c in terms:
            out += img[y0:y0 + part.shape[0]] * c
        return out
    lin = par_rows(full, first)
    return _keep_mix(layers, co, lin, 0)


def _keep_mix(layers, co, lin, count):
    lin.flags.writeable = False
    if isinstance(layers, _Layers):
        layers._mix = (co, lin, count)
    return lin


# ----------------------------------------------------------------------------- Post pipeline parsing

def _decode_pipeline(text):
    try:
        b = base64.b64decode(text)
    except Exception:
        return b""
    out, i = b"", 0
    while i < len(b) - 1:
        if b[i] == 0x78 and b[i + 1] in (0x01, 0x5E, 0x9C, 0xDA):
            try:
                d = zlib.decompressobj()
                out += d.decompress(b[i:])
                i = len(b) - len(d.unused_data)
                continue
            except zlib.error:
                pass
        i += 1
    return out


def _f(p, s, n=1):
    return struct.unpack(f"<{n}f", p[s:s + 4 * n])


KNOWN = {"SimpleExposure", "PhotographicExposure", "Contrast", "Saturation", "GreenMagentaTint", "WhiteBalance",
         "WhiteBalanceImproved", "Reinhard", "AcesOt", "Filmic", "Vignette", "LinearToSrgb", "SrgbToLinear",
         "Curves", "Lut", "Tint"}


def parse_pipeline(text):
    """-> (ops in application order: [(name, params dict)], warnings). Only enabled operators are returned."""
    p = _decode_pipeline(text)
    warnings = []
    if not p:
        return [], warnings
    seen, order = set(), []
    for m in re.finditer(rb"Chaos\.(\w+?)Operator\.Data", p):
        name = m.group(1).decode()
        if name in seen or name == "UnaryColorMapping":
            continue
        seen.add(name)
        s = m.end()
        L = p[s]
        s += 1
        if L:
            s += L + 2 + 6
        order.append((name, s))
    ops = []
    for name, s in reversed(order):  # stored last-to-first
        try:
            enabled = p[s + 4] == 1
            q = s + 5
            if name in ("LinearToSrgb", "SrgbToLinear"):
                ops.append((name, {}))
                continue
            if not enabled:
                continue
            if name in ("SimpleExposure", "Contrast", "Saturation", "GreenMagentaTint", "WhiteBalance",
                        "WhiteBalanceImproved", "Reinhard", "AcesOt"):
                ops.append((name, {"v": _f(p, q)[0]}))
            elif name == "Filmic":
                h, r = _f(p, q, 2)
                ops.append((name, {"highlights": h, "shadows": r}))
            elif name == "Vignette":
                ops.append((name, {"intensity": _f(p, q)[0]}))
            elif name == "Tint":
                ops.append((name, {"color": list(_f(p, q, 3))}))
            elif name == "Lut":
                ln = p[q]
                path = p[q + 1:q + 1 + ln].decode("utf-8", "replace")
                r = q + 1 + ln
                opacity = _f(p, r)[0]
                ops.append((name, {"path": path, "opacity": opacity, "log": p[r + 4] == 1}))
            elif name == "Curves":
                r = q
                n = p[r]
                r += 1
                curves = []
                for _ in range(n):
                    k = p[r]
                    r += 1
                    pts = [_f(p, r + 8 * j, 2) for j in range(k)]
                    r += 8 * k
                    kh = p[r]
                    r += 1 + 16 * kh
                    ln = p[r]
                    r += 1
                    interp = p[r:r + ln].decode()
                    r += ln
                    curves.append((pts, interp))
                ne = p[r]
                en = list(p[r + 1:r + 1 + ne])
                ops.append((name, {"curves": curves, "enabled": en}))
            elif name == "PhotographicExposure":
                ops.append((name, {}))
            else:
                warnings.append(f"Unknown Post operator '{name}' is skipped")
        except (IndexError, struct.error):
            warnings.append(f"Post operator '{name}' couldn't be read and is skipped")
    return ops, warnings


# ----------------------------------------------------------------------------- operators

def _l2s(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.maximum(x, 0.0031308), 1 / 2.4) - 0.055)


def _s2l(v):
    return np.where(v <= 0.04045, v / 12.92, np.power((np.maximum(v, 0.04045) + 0.055) / 1.055, 2.4))


RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                    [0.0193339, 0.1191920, 0.9503041]])
BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
ACES_IN = np.array([[0.59719, 0.35458, 0.04823], [0.07600, 0.90834, 0.01566], [0.02840, 0.13383, 0.83777]])
ACES_OUT = np.array([[1.60475, -0.53108, -0.07367], [-0.10208, 1.10813, -0.00605], [-0.00327, -0.07276, 1.07602]])


def planck_xy(T):
    T = float(min(max(T, 1667.0), 25000.0))
    if T <= 4000:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    return np.array([x, y, 1 - x - y])


def wb_matrix(T, ref=6500.0):
    s = BRADFORD @ planck_xy(T)
    d = BRADFORD @ planck_xy(ref)
    return (np.linalg.inv(RGB2XYZ) @ np.linalg.inv(BRADFORD) @ np.diag(d / s) @ BRADFORD @ RGB2XYZ).astype(np.float32)


def _matmul(x, M):
    h, w = x.shape[:2]
    return (x.reshape(-1, 3) @ np.asarray(M, np.float32).T).reshape(h, w, 3)


_ZEROS = {}


def _lookup(v, x0, x1, table):
    """Piecewise-linear table lookup (like np.interp on a uniform grid from x0 to x1, ends clamped), done by
    OpenCV's multi-threaded remap: ~10x faster than np.interp on big images."""
    import cv2
    v = np.ascontiguousarray(v, dtype=np.float32)
    n = table.shape[-1]
    shape = v.shape
    k = shape[-1] * (shape[-2] if v.ndim > 1 else 1)
    if k > 32000 or v.size % k:
        k = shape[-1] if shape[-1] <= 32000 else 0
    if not k:
        return np.interp(v, np.linspace(x0, x1, n), table).astype(np.float32)
    flat = v.reshape(-1, k)
    coord = (flat - np.float32(x0)) * np.float32((n - 1) / max(x1 - x0, 1e-12))
    tab = np.ascontiguousarray(table, dtype=np.float32).reshape(1, n)
    out = np.empty_like(flat)
    for r0 in range(0, flat.shape[0], 16384):       # remap handles at most 32767 rows at a time
        c = coord[r0:r0 + 16384]
        zk = (c.shape, )
        z = _ZEROS.get(zk)
        if z is None:
            if len(_ZEROS) > 8:
                _ZEROS.clear()
            z = _ZEROS[zk] = np.zeros(c.shape, np.float32)
        out[r0:r0 + 16384] = cv2.remap(tab, c, z, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out.reshape(shape)


_TAB_N = 16384
_contrast_tabs = {}


def op_contrast(x, c):
    if abs(c - 1) < 1e-6:
        return x
    key = round(float(c), 6)
    g = _contrast_tabs.get(key)
    if g is None:
        d = _data()
        grid = np.linspace(0, 1, d["contrast_d0"].shape[0], dtype=np.float32)
        q = float(d["contrast_q"])
        k = c ** q - 1 if c >= 1 else 0.0
        if c < 1:  # below 1 wasn't measured: mirror the curve (lower contrast)
            tab = grid - (np.clip(grid + d["contrast_d0"] + ((1 / c) ** q - 1) * d["contrast_D"], 0, 1) - grid)
            w = min(1.0, (1 - c) / 0.25)
            tab = grid * (1 - w) + tab * w if c > 0.75 else tab
        else:
            base = d["contrast_d0"] * min(1.0, (c - 1) / 0.25)  # fade in the constant part near c = 1
            tab = grid + base + k * d["contrast_D"]
        tab = np.clip(tab, 0, 1).astype(np.float64)
        # the whole linear -> sRGB -> curve -> linear chain as one table over sqrt(x) (smooth, fine near black)
        u = np.linspace(0, 1, _TAB_N)
        z = _l2s(u * u)
        g = _s2l(np.interp(z, grid.astype(np.float64), tab)).astype(np.float32)
        if len(_contrast_tabs) > 32:
            _contrast_tabs.clear()
        _contrast_tabs[key] = g
    import cv2
    xc = np.clip(x, 0, 1)
    out = _lookup(cv2.sqrt(xc.reshape(xc.shape[0], -1)).reshape(xc.shape), 0.0, 1.0, g)
    return out + np.maximum(x - 1, 0)


def op_saturation(x, s):
    V = np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2])[..., None]
    D = V - np.minimum(np.minimum(x[..., 0], x[..., 1]), x[..., 2])[..., None]   # chroma; S = D / V
    # k = max(S + s, 0) / S = max(D + s V, 0) / D ; out = V - (V - x) k   (D = 0 -> V - x = 0 -> out = V)
    k = D + np.float32(s) * V
    np.maximum(k, 0, out=k)
    k /= np.maximum(D, np.float32(1e-12))
    out = V - x
    out *= k
    np.subtract(V, out, out=out)
    np.maximum(out, 0, out=out)
    return np.minimum(out, V, out=out)


def op_tint(x, t):
    a = abs(t)
    up, dn = 1 + a * (2 ** 0.5 - 1), 1 + a * (2 ** -0.5 - 1)
    er, eg = (up, dn) if t >= 0 else (dn, up)
    x = np.clip(x, 0, None)
    out = np.empty_like(x)
    np.power(x[..., 0], np.float32(er), out=out[..., 0])
    np.power(x[..., 1], np.float32(eg), out=out[..., 1])
    np.power(x[..., 2], np.float32(er), out=out[..., 2])
    return out


def op_reinhard(x, w):
    w = max(float(w), 1e-3)
    return (x * (1 + x / (w * w)) / (1 + x)).astype(np.float32)


def op_aces(x, strength):
    v = _matmul(np.clip(x, 0, None), ACES_IN)
    a = v * (v + 0.0245786) - 0.000090537
    b = v * (0.983729 * v + 0.4329510) + 0.238081
    y = np.clip(_matmul(a / b, ACES_OUT), 0, 1)
    return (x * (1 - strength) + y * strength).astype(np.float32)


_filmic_tabs = {}


def op_filmic(x, h, s):
    d = _data()
    lg = np.asarray(d["filmic_logx"], np.float64)
    key = (round(float(h), 6), round(float(s), 6))
    tab = _filmic_tabs.get(key)
    if tab is None:
        grid = np.linspace(lg[0], lg[-1], _TAB_N)
        tab = np.zeros(_TAB_N)
        if h:
            tab += h * np.interp(grid, lg, d["filmic_dh"])
        if s:
            tab += s * np.interp(grid, lg, d["filmic_ds"])
        tab = tab.astype(np.float32)
        if len(_filmic_tabs) > 32:
            _filmic_tabs.clear()
        _filmic_tabs[key] = tab
    lx = np.log(np.maximum(x, np.float32(1e-9)))
    add = _lookup(lx, float(lg[0]), float(lg[-1]), tab)
    x0 = float(np.exp(lg[0]))
    if s:
        low = x < x0     # shadows: proportional below the measured range (highlights term is flat there)
        if low.any():
            below = (h * float(d["filmic_dh"][0])) + s * float(d["filmic_ds"][0]) * x / np.float32(x0)
            add = np.where(low, below, add)
    return np.maximum(x + add, 0).astype(np.float32, copy=False)


def op_vignette(disp, intensity, y0=0, full_hw=None):
    """Display-space darkening toward the corners (fitted at 0.5 and 1.0). y0 / full_hw: disp is a strip
    of a bigger image starting at row y0."""
    if intensity <= 0:
        return disp
    h, w = full_hw or disp.shape[:2]
    key = (h, w, y0, disp.shape[0], round(float(intensity), 6))
    f = _VIG.get(key)
    if f is None:
        f = _vignette_mask(intensity, h, w, y0, disp.shape[0])
        if len(_VIG) > 64:
            _VIG.clear()
        _VIG[key] = f
    return disp if f is None else (disp * f).astype(np.float32, copy=False)


_VIG = {}


def _vignette_mask(intensity, h, w, y0, sh):
    yy, xx = np.mgrid[y0:y0 + sh, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2, (h - 1) / 2
    r = np.hypot(xx - cx, yy - cy) / np.hypot(cx, cy)
    p5, p1 = (0.7309, 0.2248, 2.1572), (0.286, 0.557, 2.2198)
    t = min((intensity - 0.5) / 0.5, 2.0)  # the fitted trend, also below 0.5 (the effect fades out ~0.17)
    r0, a, pw = [p5[k] + (p1[k] - p5[k]) * t for k in range(3)]
    r0, a = min(max(r0, 0.0), 0.999), max(a, 0.0)
    if a <= 0:
        return None
    f = 1 - min(a, 1.0) * np.power(np.clip((r - r0) / max(1 - r0, 1e-3), 0, None), pw)
    return np.clip(f, 0, 1)[..., None].astype(np.float32)


def corona_curve(pts):
    """Corona 'PiecewiseCubic': Hermite spline; inner tangents = mean of neighbouring slopes, ends (3s - m)/2."""
    pts = sorted((float(a), float(b)) for a, b in pts)
    X = np.array([p[0] for p in pts])
    Y = np.array([p[1] for p in pts])
    n = len(X)
    if n < 2:
        return lambda v: v
    s = np.diff(Y) / np.maximum(np.diff(X), 1e-9)
    m = np.zeros(n)
    if n == 2:
        m[:] = s[0]
    else:
        m[1:-1] = (s[:-1] + s[1:]) / 2
        m[0] = (3 * s[0] - m[1]) / 2
        m[-1] = (3 * s[-1] - m[-2]) / 2

    def f(v):
        v = np.asarray(v)
        vc = np.clip(v, X[0], X[-1])
        i = np.clip(np.searchsorted(X, vc, side="right") - 1, 0, n - 2)
        h = X[i + 1] - X[i]
        t = (vc - X[i]) / h
        t2, t3 = t * t, t * t * t
        y = ((2 * t3 - 3 * t2 + 1) * Y[i] + (t3 - 2 * t2 + t) * h * m[i]
             + (-2 * t3 + 3 * t2) * Y[i + 1] + (t3 - t2) * h * m[i + 1])
        return np.where(v > X[-1], Y[-1] + (v - X[-1]), y).astype(np.float32)
    return f


def _fast_curve(pts):
    """corona_curve evaluated through a fine table (smooth Hermite spline: identical to 1e-6)."""
    key = tuple((float(a), float(b)) for a, b in sorted(pts))
    hit = _CURVES.get(key)
    if hit is not None:
        return hit
    f = corona_curve(pts)
    X0, X1 = key[0][0], key[-1][0]
    tab = f(np.linspace(X0, X1, _TAB_N)).astype(np.float32)
    y1 = float(f(np.array([X1]))[0])

    def g(v):
        out = _lookup(v, X0, X1, tab)
        if len(key) >= 2:
            over = v > np.float32(X1)
            if over.any():
                out = np.where(over, np.float32(y1) + (v - np.float32(X1)), out)
        return out.astype(np.float32, copy=False)
    if len(_CURVES) > 64:
        _CURVES.clear()
    _CURVES[key] = g if len(key) >= 2 else f
    return _CURVES[key]


_CURVES = {}


def op_curves(disp, params):
    curves = params["curves"]
    en = params.get("enabled", [1] * len(curves))
    out = disp
    if curves and en[0]:
        f = _fast_curve(curves[0][0])
        out = f(out)
    for c in range(1, min(4, len(curves))):
        if c < len(en) and en[c]:
            pts = curves[c][0]
            if len(pts) == 2 and pts[0] == (0.0, 0.0) and pts[1] == (1.0, 1.0):
                continue
            f = _fast_curve(pts)
            out = out.copy()
            out[..., c - 1] = f(out[..., c - 1])
    return out


_lut_cache = {}


def load_cube(path):
    if path in _lut_cache:
        return _lut_cache[path]
    lines = [ln.strip() for ln in open(path, "r", encoding="utf-8", errors="replace")
             if ln.strip() and not ln.startswith("#")]
    size = int([ln for ln in lines if ln.upper().startswith("LUT_3D_SIZE")][0].split()[1])
    vals = np.array([[float(v) for v in ln.split()[:3]] for ln in lines if ln[0] in "0123456789-."], np.float32)
    lut = vals.reshape(size, size, size, 3)
    _lut_cache[path] = lut
    return lut


def tetra(img, lut):
    """Tetrahedral 3D LUT interpolation (lut indexed [b, g, r]). The tetrahedron is picked per pixel from
    the order of the fractional parts: corners base, base + e(max), base + 1 - e(min), base + (1, 1, 1)."""
    n = lut.shape[0]
    N = n - 1
    flat = np.ascontiguousarray(lut, dtype=np.float32).reshape(-1, 3)
    c = np.clip(img, 0, 1).reshape(-1, 3) * np.float32(N)
    i0 = np.minimum(c.astype(np.int32), N - 1)
    f = c - i0
    fr, fg, fb = f[:, 0], f[:, 1], f[:, 2]
    base = (i0[:, 2] * n + i0[:, 1]) * n + i0[:, 0]
    step = np.array([1, n, n * n], np.int32)            # r, g, b strides
    big = np.maximum(np.maximum(fr, fg), fb)
    small = np.minimum(np.minimum(fr, fg), fb)
    mid = fr + fg + fb - big - small
    # argmax / argmin with the same tie order as the 6-case version (r before g before b for max, etc.)
    amax = np.where((fr >= fg) & (fr >= fb), 0, np.where(fg > fb, 1, 2)).astype(np.int8)
    amin = np.where((fb <= fg) & (fb < fr) | ((fb <= fr) & (fb <= fg) & (amax != 2)), 2,
                    np.where((fg <= fr) & (amax != 1), 1, 0)).astype(np.int8)
    amin = np.where(amin == amax, (amax + 2) % 3, amin)
    v1 = base + step[amax]
    v2 = base + (step.sum() - step[amin])
    out = flat[base] * (1 - big)[:, None]
    out += flat[v1] * (big - mid)[:, None]
    out += flat[v2] * (mid - small)[:, None]
    out += flat[base + step.sum()] * small[:, None]
    return out.reshape(img.shape)


def find_lut(path, cxr_path, lut_dirs):
    """Corona stores an absolute path or $%LUT_ROOT%/name.cube; look for it in sensible places."""
    name = os.path.basename(path.replace("\\", "/"))
    cands = []
    if "%" not in path:
        cands.append(path)
    cands.append(os.path.join(os.path.dirname(cxr_path), name))
    for d in lut_dirs:
        if d:
            cands.append(os.path.join(d, name))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def op_lut(disp, lut, opacity):
    m = np.maximum(disp.max(-1, keepdims=True), 1.0)
    y = tetra(disp / m, lut) * m
    return (disp * (1 - opacity) + y * opacity).astype(np.float32) if opacity < 1 else y


# ----------------------------------------------------------------------------- full tone mapping

def tone_map(lin, info, lut_dirs=(), apply_post=True, notes=None):
    """Linear LightMix result -> display RGB 0..1 (float32), like Corona's VFB / Image Editor output.
    The per-pixel part runs on horizontal strips on all CPU cores."""
    from engine import par_rows
    full_hw = lin.shape[:2]
    note_box = [] if notes is not None else None

    def strip(part, y0):
        return _tone_px(part, info, lut_dirs, apply_post, note_box if y0 == 0 else None, y0, full_hw)
    disp = par_rows(strip, lin, min_rows=32)
    if notes is not None:
        notes.extend(note_box)
    if apply_post:
        a = info.exr.attr
        if str(a("corona.sharp.blur.enable", 0)) in ("1", "True", "true"):
            try:
                amount, radius = float(a("corona.sharp.amount", 1.0)), float(a("corona.sharp.radius", 0.5))
            except (TypeError, ValueError):
                amount, radius = 1.0, 0.5
            if amount > 0 and radius > 0:
                import cv2
                blur = cv2.GaussianBlur(disp, (0, 0), radius)
                disp = disp + amount * (disp - blur)
        if notes is not None and str(a("corona.bg.enabled", 0)) in ("1", "True", "true"):
            notes.append("Corona bloom & glare aren't reproduced (use the app's own effects instead)")
    return np.clip(disp, 0, 1).astype(np.float32, copy=False)


def _tone_px(lin, info, lut_dirs, apply_post, notes, y0, full_hw):
    """The per-pixel part of tone_map (all operators except sharpening) on a strip starting at row y0."""
    x = np.fmax(lin.astype(np.float32, copy=False), np.float32(0))     # also turns NaN into 0
    if not np.isfinite(x).all():
        x = np.nan_to_num(x, nan=0, posinf=0, neginf=0)
    disp = None
    if apply_post:
        for name, prm in info.pipeline:
            if name == "LinearToSrgb":
                if disp is None:
                    disp = np.power(np.clip(x, 0, None), 1 / DISPLAY_GAMMA).astype(np.float32)
                continue
            if name == "SrgbToLinear":
                if disp is not None:
                    x = np.power(np.clip(disp, 0, None), DISPLAY_GAMMA).astype(np.float32)
                    disp = None
                continue
            if name == "Curves":
                d = disp if disp is not None else np.power(x, 1 / DISPLAY_GAMMA).astype(np.float32)
                d = op_curves(d, prm)
                if disp is not None:
                    disp = d
                else:
                    x = np.power(np.clip(d, 0, None), DISPLAY_GAMMA).astype(np.float32)
                continue
            if name in ("Vignette", "Lut"):
                d = disp if disp is not None else np.power(x, 1 / DISPLAY_GAMMA).astype(np.float32)
                if name == "Vignette":
                    d = op_vignette(d, prm["intensity"], y0, full_hw)
                else:
                    lp = find_lut(prm["path"], info.path, lut_dirs)
                    if lp is None:
                        if notes is not None:
                            notes.append(f"LUT '{os.path.basename(prm['path'])}' not found: put the .cube file "
                                         "next to the .cxr or in the app's luts folder. It was skipped.")
                        continue
                    d = op_lut(d, load_cube(lp), prm["opacity"])
                if disp is not None:
                    disp = d
                else:
                    x = np.power(np.clip(d, 0, None), DISPLAY_GAMMA).astype(np.float32)
                continue
            if disp is not None:  # linear operator after a LinearToSrgb without SrgbToLinear
                x = np.power(np.clip(disp, 0, None), DISPLAY_GAMMA).astype(np.float32)
                disp = None
            v = prm.get("v")
            if name == "SimpleExposure":
                x = x * np.float32(2.0 ** v)
            elif name == "PhotographicExposure":
                a = info.exr.attr
                try:
                    iso, fst, sh = float(a("corona.cm.iso", 100)), float(a("corona.cm.fstop", 16)), float(a("corona.cm.shutterspeed", 100))
                    x = x * np.float32((iso / 100.0) * (100.0 / max(sh, 1e-3)) * (16.0 / max(fst, 1e-3)) ** 2)
                except (TypeError, ValueError):
                    pass
                if notes is not None:
                    notes.append("Photographic exposure is approximated")
            elif name == "Contrast":
                x = op_contrast(x, v)
            elif name == "Saturation":
                x = op_saturation(x, v)
            elif name == "GreenMagentaTint":
                x = op_tint(x, v)
            elif name in ("WhiteBalance", "WhiteBalanceImproved"):
                if abs(v - 6500) > 0.5:
                    x = np.clip(_matmul(x, wb_matrix(v)), 0, None)
            elif name == "Reinhard":
                x = op_reinhard(x, v)
            elif name == "AcesOt":
                x = op_aces(x, v)
            elif name == "Filmic":
                x = op_filmic(x, prm["highlights"], prm["shadows"])
            elif name == "Tint":
                x = x * np.power(np.clip(np.asarray(prm["color"], np.float32), 0, None), DISPLAY_GAMMA)
    if disp is None:
        disp = np.power(x, np.float32(1 / DISPLAY_GAMMA))
    return disp.astype(np.float32, copy=False)


def render(path, s=None, lut_dirs=(), max_side=None, progress=None, cancel=None, threads=4,
           use_cache=True, notes=None, file_mix_only=False):
    """Full .cxr -> display RGB: LightMix (file settings + overrides from s['lightmix']) + the file's Post."""
    s = s or {}
    try:
        info = CxrInfo(path)
    except ExrError as e:
        raise ValueError(str(e))
    layers = load_layers(info, s.get("cxr_denoised", "file"), max_side, progress, cancel, threads, use_cache)
    relit = True
    if file_mix_only:
        overrides = None
    else:
        import atmosphere
        overrides, relit = atmosphere.tod_overrides(info, effective(s), s)
    lin = mix(info, layers, overrides)
    if not relit:      # no sun / sky light passes to relight: Time of day falls back to the grade
        import atmosphere
        lin = atmosphere.tod_linear(lin, s)
        if notes is not None:
            notes.append("Time of day: no Sun / Environment light passes found, so the image is relit with a grade")
    if notes is not None:
        notes.extend(info.warnings)
    return tone_map(lin, info, lut_dirs, bool(s.get("cxr_post", True)), notes)


def scan_lights(paths):
    """Union of light names over the given .cxr files, in first-seen order, plus how many files have each."""
    order, count, errors = [], {}, []
    for p in paths:
        try:
            info = CxrInfo(p)
        except Exception as e:
            errors.append((os.path.basename(p), str(e)))
            continue
        for n in light_names(info):
            if n not in count:
                order.append(n)
                count[n] = 0
            count[n] += 1
    return order, count, errors

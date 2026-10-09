"""
Builds the app's built-in looks: a 3D LUT (.cube, 33 points) per look in app/data/looks, made from the usual
grading moves (tone curve, black lift / highlight roll-off, split toning by luminance, saturation by hue and
luminance, black & white channel mix). Run: python tools/make_looks.py
"""
import colorsys
import os

import numpy as np

N = 33
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "data", "looks")
W = np.array([0.2126, 0.7152, 0.0722])


def luma(c):
    return c @ W


def s_curve(x, k):
    """k > 0: more contrast (S), k < 0: flatter."""
    s = x * x * (3 - 2 * x)
    return x + k * (s - x)


def toe_shoulder(x, toe=0.0, shoulder=0.0):
    """toe crushes the deep shadows a little, shoulder rolls the highlights off softly."""
    if toe:
        x = np.where(x < 0.25, x - toe * (0.25 - x) * x * 16 * 0.25, x)
    if shoulder:
        x = np.where(x > 0.7, 0.7 + (x - 0.7) * (1 - shoulder * (x - 0.7) / 0.3 * 0.5), x)
    return x


def levels(x, lift=0.0, gain=1.0):
    return lift + (gain - lift) * x


def split(c, shadows=(0, 0, 0), mids=(0, 0, 0), highs=(0, 0, 0)):
    L = luma(c)[..., None]
    ws, wh = (1 - L) ** 2, L ** 2
    wm = 1 - ws - wh
    return c + ws * np.array(shadows) + wm * np.array(mids) + wh * np.array(highs)


def saturation(c, amount, by_luma=None):
    L = luma(c)[..., None]
    a = amount
    if by_luma is not None:            # (shadow sat, highlight sat)
        lo, hi = by_luma
        a = amount * (lo + (hi - lo) * L)
    return L + (c - L) * a


def hue_sat(c, ranges):
    """ranges: [(hue center deg, width deg, sat factor, hue shift deg)] applied smoothly."""
    flat = c.reshape(-1, 3)
    out = np.empty_like(flat)
    for i, (r, g, b) in enumerate(np.clip(flat, 0, 1)):
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        hd = h * 360
        sf, sh = 1.0, 0.0
        for center, width, fac, shift in ranges:
            d = min(abs(hd - center), 360 - abs(hd - center))
            w = max(0.0, 1 - d / width) ** 2 * min(1.0, s * 4)
            sf *= 1 + (fac - 1) * w
            sh += shift * w
        out[i] = colorsys.hsv_to_rgb(((hd + sh) % 360) / 360, min(1.0, s * sf), v)
    return out.reshape(c.shape)


def bw(c, mix=(0.30, 0.59, 0.11), tone=(0, 0, 0)):
    L = c @ np.array(mix)
    return L[..., None] * np.ones(3) + np.array(tone) * (L * (1 - L))[..., None] * 4


LOOKS = {
    "cinematic_teal_orange": lambda c: saturation(split(
        hue_sat(s_curve(levels(c, 0.015, 0.985), 0.38), [(30, 35, 1.2, 0), (120, 60, 0.7, 40), (215, 50, 1.15, -18)]),
        shadows=(-0.065, 0.02, 0.075), mids=(0.012, 0.0, -0.01), highs=(0.07, 0.022, -0.05)), 0.97),
    "cinematic_warm_drama": lambda c: saturation(split(
        toe_shoulder(s_curve(c, 0.5), toe=0.4, shoulder=0.3), shadows=(-0.018, 0.0, 0.04), mids=(0.025, 0.008, -0.018),
        highs=(0.065, 0.028, -0.045)), 0.88),
    "retro_70s_film": lambda c: saturation(split(
        levels(s_curve(c, 0.12), 0.075, 0.93), shadows=(-0.01, 0.022, -0.005), mids=(0.02, 0.01, -0.025),
        highs=(0.035, 0.025, -0.05)), 0.78, by_luma=(0.8, 1.05)),
    "professional_clean": lambda c: saturation(
        hue_sat(toe_shoulder(s_curve(c, 0.16), shoulder=0.35), [(120, 50, 0.92, 0)]), 1.03),
    "golden_hour": lambda c: hue_sat(split(
        toe_shoulder(s_curve(c, 0.2), shoulder=0.3), shadows=(0.01, -0.008, 0.012), mids=(0.03, 0.012, -0.025),
        highs=(0.06, 0.03, -0.05)), [(35, 40, 1.15, 0), (210, 60, 0.85, 0)]),
    "serene_night": lambda c: saturation(split(
        s_curve(levels(c, 0.01, 1.0), 0.22), shadows=(-0.02, 0.0, 0.05), mids=(-0.012, 0.0, 0.02),
        highs=(0.035, 0.012, -0.025)), 0.9),
    "bright_scandinavian": lambda c: saturation(split(
        toe_shoulder(levels(s_curve(c, -0.08), 0.03, 1.0), shoulder=0.25), shadows=(0.0, 0.004, 0.012),
        highs=(-0.006, 0.0, 0.008)), 0.86),
    "luxury_warm_interior": lambda c: hue_sat(split(
        toe_shoulder(s_curve(c, 0.25), toe=0.15, shoulder=0.25), mids=(0.022, 0.008, -0.02), highs=(0.02, 0.01, -0.012)),
        [(25, 35, 1.12, 0), (210, 50, 0.85, 0), (120, 50, 0.85, -8)]),
    "dark_moody": lambda c: hue_sat(saturation(split(
        toe_shoulder(levels(s_curve(c, 0.3), 0.01, 0.97), toe=0.3), shadows=(-0.005, 0.008, 0.012),
        highs=(0.03, 0.015, -0.02)), 0.8), [(110, 50, 0.7, -20)]),
    "fine_art_bw": lambda c: bw(toe_shoulder(s_curve(c, 0.32), toe=0.2, shoulder=0.25), mix=(0.34, 0.56, 0.10),
                                 tone=(0.008, 0.004, -0.004)),
    "soft_pastel_morning": lambda c: saturation(split(
        levels(s_curve(c, -0.1), 0.05, 0.985), shadows=(0.008, 0.0, 0.018), highs=(0.01, 0.004, 0.006)), 0.82),
    "warm_film_stock": lambda c: hue_sat(split(
        levels(toe_shoulder(s_curve(c, 0.14), shoulder=0.35), 0.02, 0.985), mids=(0.018, 0.008, -0.012),
        highs=(0.02, 0.012, -0.02)), [(25, 35, 1.05, 0), (110, 45, 0.85, -12), (200, 50, 0.9, -5)]),
    "nordic_cool_daylight": lambda c: saturation(split(
        toe_shoulder(s_curve(c, 0.2), shoulder=0.2), shadows=(-0.01, 0.0, 0.025), mids=(-0.01, 0.0, 0.015),
        highs=(-0.005, 0.0, 0.01)), 0.94),
}


def write_cube(path, fn, title):
    g = np.linspace(0, 1, N)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")          # red changes fastest in .cube files
    c = np.stack([r, gg, b], -1).reshape(-1, 3)
    out = np.clip(fn(c.reshape(N, N, N, 3)).reshape(-1, 3), 0, 1)
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(f'TITLE "{title}"\n# RenderBatch built-in look\nLUT_3D_SIZE {N}\n')
        for v in out:
            fh.write(f"{v[0]:.5f} {v[1]:.5f} {v[2]:.5f}\n")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, fn in LOOKS.items():
        write_cube(os.path.join(OUT, name + ".cube"), fn, name)
        print("wrote", name)

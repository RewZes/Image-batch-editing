"""
Packs a finished RenderBatch folder into downloads that each stay under a size limit:

  RenderBatch.zip              the app core (launcher, Python runtime without packages, app code,
                               small models) + setup_manifest.json
  RenderBatch_data_N.rbdata    zip archives with the rest (Python packages, big models).
                               Big files are split into chunks and joined again on first start.

The app's start.py finds the .rbdata files on first start, verifies them (sha256), unpacks and joins.

usage: python make_portable.py <RenderBatch folder> <output folder> [--limit-mb 29]
"""
import argparse
import hashlib
import json
import os
import zipfile
import zlib

CORE_PREFIXES = ("RenderBatch.exe", "README.txt", "MODELS.txt", "app/", "luts/")
CORE_EXCLUDE = ("runtime/Lib/site-packages/",)


def is_core(rel, size):
    if rel.startswith(CORE_EXCLUDE):
        return False
    if rel.startswith(CORE_PREFIXES) or rel.startswith("runtime/"):
        return True
    return rel.startswith("models/") and size < 5_000_000  # small models ship in the core


def comp_size(data):
    return len(zlib.compress(data, 9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--limit-mb", type=float, default=29.0)
    a = ap.parse_args()
    limit = int(a.limit_mb * 1024 * 1024)
    budget = int(limit * 0.93)  # leave room for zip overhead / estimate error
    os.makedirs(a.out, exist_ok=True)

    core, data = [], []
    for root, _, files in os.walk(a.src):
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, a.src).replace(os.sep, "/")
            (core if is_core(rel, os.path.getsize(p)) else data).append(rel)

    # split data files into members that each compress to < ~40% of the budget
    members, chunked = [], {}
    for rel in sorted(data):
        raw = open(os.path.join(a.src, rel), "rb").read()
        c = comp_size(raw)
        if c <= budget * 0.4:
            members.append((rel, raw, c))
            continue
        n = -(-c // int(budget * 0.4))
        step = -(-len(raw) // n)
        names = []
        for i in range(n):
            part = raw[i * step:(i + 1) * step]
            name = f"{rel}.rbchunk{i + 1:02d}"
            names.append(name)
            members.append((name, part, comp_size(part)))
        chunked[rel] = names

    # greedy bin packing, biggest first
    members.sort(key=lambda m: -m[2])
    bins = []
    for m in members:
        for b in bins:
            if b["size"] + m[2] <= budget:
                b["items"].append(m)
                b["size"] += m[2]
                break
        else:
            bins.append({"items": [m], "size": m[2]})

    packs = []
    for i, b in enumerate(bins, 1):
        name = f"RenderBatch_data_{i}.rbdata"
        path = os.path.join(a.out, name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for rel, raw, _ in sorted(b["items"]):
                z.writestr(rel, raw)
        size = os.path.getsize(path)
        if size > limit:
            raise SystemExit(f"{name} is {size / 2**20:.1f} MiB, over the limit. Lower --limit-mb.")
        packs.append({"name": name, "size": size,
                      "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest()})
        print(f"{name}: {size / 2**20:.1f} MiB, {len(b['items'])} files")

    manifest = {"packs": packs, "chunked": chunked}
    core_zip = os.path.join(a.out, "RenderBatch.zip")
    with zipfile.ZipFile(core_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in sorted(core):
            z.write(os.path.join(a.src, rel), "RenderBatch/" + rel)
        z.writestr("RenderBatch/setup_manifest.json", json.dumps(manifest, indent=1))
    size = os.path.getsize(core_zip)
    print(f"RenderBatch.zip: {size / 2**20:.1f} MiB, {len(core)} files")
    if size > limit:
        raise SystemExit("RenderBatch.zip is over the limit.")


if __name__ == "__main__":
    main()

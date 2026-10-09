"""
Packs a folder (laid out like the app folder) into RenderBatch_<prefix>_N.rbdata files that each stay
under a size limit, splitting big files into chunks that the app joins again on first start.
Prints the manifest entries (packs + chunked) as JSON.

usage: python make_addon.py <src folder> <out folder> <prefix> <skip_if path> [--limit-mb 29]
"""
import argparse
import hashlib
import json
import os
import zipfile
import zlib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("prefix")
    ap.add_argument("skip_if")
    ap.add_argument("--limit-mb", type=float, default=29.0)
    a = ap.parse_args()
    limit = int(a.limit_mb * 1024 * 1024)
    budget = int(limit * 0.93)
    os.makedirs(a.out, exist_ok=True)

    members, chunked = [], {}
    for root, _, files in os.walk(a.src):
        for f in sorted(files):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, a.src).replace(os.sep, "/")
            raw = open(p, "rb").read()
            c = len(zlib.compress(raw, 6))
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
                members.append((name, part, len(zlib.compress(part, 6))))
            chunked[rel] = names

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
        name = f"RenderBatch_{a.prefix}_{i}.rbdata"
        path = os.path.join(a.out, name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for rel, raw, _ in sorted(b["items"]):
                z.writestr(rel, raw)
        size = os.path.getsize(path)
        if size > limit:
            raise SystemExit(f"{name} is {size / 2**20:.1f} MiB, over the limit")
        packs.append({"name": name, "size": size, "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
                      "skip_if": a.skip_if,
                      "chunks": [rel for rel, _, _ in b["items"] if ".rbchunk" in rel]})
    print(json.dumps({"packs": packs, "chunked": chunked}))


if __name__ == "__main__":
    main()

"""
Builds RenderBatch_Setup_<version>.exe (NSIS) from a complete RenderBatch folder and the source code.

The setup holds the app core: launcher, Python runtime without packages, app code, small models, docs.
The Python packages and the big denoise model stay in the RenderBatch_*.rbdata files (each under the
download size limit); setup_manifest.json tells the installer how to check and unpack them. On a PC that
already has RenderBatch, the installer copies them from that folder instead.

usage: python build_installer.py <complete RenderBatch folder> <source repo> <packs folder> <out folder>
       [--manifest-from RenderBatch.zip] [--extra-manifest setup_manifest.json]

  packs folder:     the RenderBatch_*.rbdata files the manifest points to
  --manifest-from:  core zip whose setup_manifest.json lists the data packs (sizes / sha256 / chunks)
  --extra-manifest: more packs (UI, optional converter), e.g. the one from an update zip
Needs makensis (NSIS 3) on PATH.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import zipfile

SKIP_APP = {"settings.json", "__pycache__", "exrfast.so", "models"}
OPTIONAL_PREFIXES = ("RenderBatch_converter_",)      # the model converter is an add-on


def copy_app(src_app, dst_app):
    for root, dirs, files in os.walk(src_app):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not (root == src_app and d in SKIP_APP)]
        for f in files:
            if root == src_app and f in SKIP_APP or f.endswith((".pyc", ".so")):
                continue
            rel = os.path.relpath(os.path.join(root, f), src_app)
            os.makedirs(os.path.dirname(os.path.join(dst_app, rel)), exist_ok=True)
            shutil.copy2(os.path.join(root, f), os.path.join(dst_app, rel))


def skip_if_for(pack_path):
    """A file from the pack that stays where it is once installed (marks the pack as already there)."""
    with zipfile.ZipFile(pack_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/") and ".rbchunk" not in n
                 and n.startswith("runtime/")]
    names.sort(key=lambda n: (not n.endswith((".pyd", ".dll", ".py")), n))
    return names[0] if names else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("full")
    ap.add_argument("repo")
    ap.add_argument("packs")
    ap.add_argument("out")
    ap.add_argument("--manifest-from", required=True)
    ap.add_argument("--extra-manifest")
    ap.add_argument("--base-url", action="append", default=[],
                    help="web folder the .rbdata files can be downloaded from (repeatable, tried in order)")
    a = ap.parse_args()

    version = re.search(r'^VERSION = "([^"]+)"', open(os.path.join(a.repo, "app", "main.py"),
                                                      encoding="utf-8").read(), re.M).group(1)
    core = os.path.join(a.out, "core")
    shutil.rmtree(core, ignore_errors=True)
    os.makedirs(core)

    # launcher, runtime without site-packages, small models, LUT readme
    shutil.copy2(os.path.join(a.full, "RenderBatch.exe"), core)
    rt = os.path.join(a.full, "runtime")
    for root, dirs, files in os.walk(rt):
        rel_root = os.path.relpath(root, rt)
        if rel_root.replace(os.sep, "/").startswith("Lib/site-packages"):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            dst = os.path.join(core, "runtime", rel_root, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(root, f), dst)
    os.makedirs(os.path.join(core, "runtime", "Lib", "site-packages"), exist_ok=True)
    os.makedirs(os.path.join(core, "models"), exist_ok=True)
    for root, _, files in os.walk(os.path.join(a.full, "models")):
        for f in files:                              # loose in models\: the app sorts them into subfolders
            p = os.path.join(root, f)
            if os.path.getsize(p) < 5_000_000:       # the big denoiser comes in a data pack
                shutil.copy2(p, os.path.join(core, "models", f))
    os.makedirs(os.path.join(core, "luts"), exist_ok=True)
    if os.path.exists(os.path.join(a.full, "luts", "README.txt")):
        shutil.copy2(os.path.join(a.full, "luts", "README.txt"), os.path.join(core, "luts"))

    # app code and docs from the source
    copy_app(os.path.join(a.repo, "app"), os.path.join(core, "app"))
    for f in ("README.txt", "README_RU.txt", "README_RO.txt", "MODELS.txt"):
        shutil.copy2(os.path.join(a.repo, f), core)

    # setup manifest: data packs (+ UI pack, + optional converter)
    with zipfile.ZipFile(a.manifest_from) as z:
        name = next(n for n in z.namelist() if n.endswith("setup_manifest.json"))
        man = json.loads(z.read(name))
    if a.extra_manifest:
        extra = json.load(open(a.extra_manifest, encoding="utf-8"))
        known = {p["name"] for p in man["packs"]}
        man["packs"] += [p for p in extra["packs"] if p["name"] not in known]
        man["chunked"].update(extra.get("chunked", {}))
    for p in man["packs"]:
        if p["name"].startswith(OPTIONAL_PREFIXES):
            p["optional"] = True
        path = os.path.join(a.packs, p["name"])
        if not p.get("skip_if") and os.path.exists(path):
            p["skip_if"] = skip_if_for(path)
        if not p.get("optional") and not os.path.exists(path):
            raise SystemExit(f"missing pack {p['name']}")
        if os.path.exists(path):
            import hashlib
            h = hashlib.sha256(open(path, "rb").read()).hexdigest()
            if h != p["sha256"] or os.path.getsize(path) != p["size"]:
                raise SystemExit(f"{p['name']} doesn't match the manifest")
    man["base_urls"] = a.base_url
    json.dump(man, open(os.path.join(core, "setup_manifest.json"), "w", encoding="utf-8"), indent=1)
    # the app keeps the list too (to add the model converter later, and for updates)
    json.dump(man, open(os.path.join(core, "app", "data", "packs.json"), "w", encoding="utf-8"), indent=1)

    out_exe = os.path.join(a.out, f"RenderBatch_Setup_{version}.exe")
    nsi = os.path.join(a.repo, "tools", "installer", "RenderBatch.nsi")
    icon = os.path.join(a.repo, "tools", "launcher", "icon.ico")
    cmd = ["makensis", "-V2", f"-DVERSION={version}", f"-DSRC={os.path.abspath(core)}",
           f"-DOUTFILE={os.path.abspath(out_exe)}", f"-DICON={os.path.abspath(icon)}", nsi]
    subprocess.run(cmd, check=True)
    print(f"{out_exe}: {os.path.getsize(out_exe) / 2**20:.1f} MiB")
    print(json.dumps([(p["name"], p.get("skip_if"), p.get("optional", False)) for p in man["packs"]], indent=1))


if __name__ == "__main__":
    main()

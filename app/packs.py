"""
RenderBatch data packs (RenderBatch_*.rbdata): finding, downloading, checking and unpacking them.

A pack is a zip laid out like the app folder (runtime/Lib/site-packages/..., models/...). Big files are
split into .rbchunkNN parts that are joined again after unpacking. The list of packs (name, size, sha256,
which file shows a pack is already installed) is in setup_manifest.json / app/data/packs.json, together
with the web addresses they can be downloaded from (the RenderBatch GitHub repository).

Standard library only: this runs before numpy / Qt are installed.
"""
import hashlib
import json
import os
import time
import zipfile

APP_CODE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(APP_CODE_DIR, "data", "packs.json")
UA = {"User-Agent": "RenderBatch-setup"}


class Cancelled(Exception):
    pass


def load_catalog():
    try:
        with open(CATALOG, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"packs": [], "chunked": {}, "base_urls": []}


def installed(home, pack):
    return bool(pack.get("skip_if")) and os.path.exists(os.path.join(home, pack["skip_if"]))


def urls_for(pack, man):
    out = list(pack.get("urls", []))
    for base in man.get("base_urls", []) + load_catalog().get("base_urls", []):
        u = base.rstrip("/") + "/" + pack["name"]
        if u not in out:
            out.append(u)
    return out


def sha256(path, progress=None):
    h = hashlib.sha256()
    size = os.path.getsize(path) or 1
    done = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done / size)
    return h.hexdigest()


def download(pack, man, folder, progress=None, cancel=None, attempts=2):
    """Downloads one pack into folder (tries every address, a couple of times each) and checks its size and
    sha256. Returns the file path. progress(fraction); cancel() raises Cancelled to stop."""
    import urllib.request
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, pack["name"])
    if os.path.isfile(dst) and os.path.getsize(dst) == pack["size"] and sha256(dst) == pack["sha256"]:
        return dst
    errors = []
    for url in urls_for(pack, man):
        for attempt in range(attempts):
            tmp = dst + ".part"
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
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
                            progress(min(done / max(pack["size"], 1), 1.0))
                if os.path.getsize(tmp) != pack["size"]:
                    raise IOError(f"incomplete ({os.path.getsize(tmp)} of {pack['size']} bytes)")
                if sha256(tmp) != pack["sha256"]:
                    raise IOError("the file is damaged (checksum mismatch)")
                os.replace(tmp, dst)
                return dst
            except Exception as e:      # noqa: BLE001 - every network / disk problem: try the next address
                _rm(tmp)
                if type(e).__name__ == "Cancelled":
                    raise
                errors.append(f"{url}: {e}")
                time.sleep(1 + attempt)
    raise IOError(f"{pack['name']} couldn't be downloaded:\n" + "\n".join(errors[-4:]))


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def unpack(path, home, pack=None):
    if pack is not None and sha256(path) != pack["sha256"]:
        raise IOError(f"{os.path.basename(path)} is damaged (incomplete download?). Download it again.")
    with zipfile.ZipFile(path) as z:
        z.extractall(home)


def joinable(man, home):
    """Big files whose chunks are all unpacked now (chunks are deleted once joined)."""
    out = {}
    for t, parts in man.get("chunked", {}).items():
        have = [os.path.exists(os.path.join(home, pt)) for pt in parts]
        if all(have):
            out[t] = parts
        elif any(have):
            raise IOError(f"Parts of {os.path.basename(t)} are missing. Put all RenderBatch_*.rbdata files next to "
                          "the installer (or in Downloads) and try again.")
    return out


def join(man, home):
    for target, parts in joinable(man, home).items():
        dst = os.path.join(home, target)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst + ".tmp", "wb") as out:
            for part in parts:
                with open(os.path.join(home, part), "rb") as fh:
                    while True:
                        b = fh.read(1 << 20)
                        if not b:
                            break
                        out.write(b)
        os.replace(dst + ".tmp", dst)
        for part in parts:
            _rm(os.path.join(home, part))


def install(packs, man, home, step=None, cancel=None):
    """Downloads (if needed), checks, unpacks the given packs and joins big files. step(text, fraction)."""
    folder = os.path.join(home, "_downloads")
    n = max(len(packs), 1)
    for i, pk in enumerate(packs):
        path = download(pk, man, folder, cancel=cancel,
                        progress=lambda f, i=i, pk=pk: step and step(f"Downloading {pk['name']}", (i + f * 0.8) / n))
        if step:
            step(f"Unpacking {pk['name']}", (i + 0.85) / n)
        unpack(path, home)
        _rm(path)
    if step:
        step("Joining files", 1.0)
    join(man, home)
    try:
        os.rmdir(folder)
    except OSError:
        pass


def converter_packs(home):
    """(packs of the model converter add-on (PyTorch) that aren't installed yet, catalog)."""
    man = load_catalog()
    return [p for p in man.get("packs", []) if p["name"].startswith("RenderBatch_converter_")
            and not installed(home, p)], man

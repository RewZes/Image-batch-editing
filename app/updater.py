"""
Installs RenderBatch update zips from inside the app (also used by the installer's copy).

An update zip holds files laid out like the app folder (RenderBatch.exe, app/, README.txt ...), optionally
inside one top folder. Installing it:
  1. the app checks it (it must contain app/main.py with a VERSION) and unpacks it into <app>/_update,
  2. starts this file as a small helper with the bundled Python, and closes,
  3. the helper waits until the app has exited, copies the new files over the app folder (your settings,
     presets and looks are never in an update, so they stay), and starts RenderBatch again.

Files unpacked by the app carry no "downloaded from the internet" mark, so Windows doesn't warn about them.
Only the standard library is used, so the helper also runs while the app's own files are replaced.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

STAGE = "_update"
NEVER_COPY = {"settings.json", "install.json", "install.ini", "Uninstall.exe", "setup_done.json"}


# ----------------------------------------------------------------------------- reading update zips

def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _prefix(names):
    """'' or 'Some folder/' when the zip keeps everything inside one top folder."""
    for n in names:
        n = n.replace("\\", "/")
        if n.endswith("app/main.py"):
            return n[: -len("app/main.py")]
    return None


def zip_version(path):
    """The RenderBatch version inside an update zip, or None if it isn't one."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            pre = _prefix(names)
            if pre is None or pre.count("/") > 1:
                return None
            text = z.read(pre + "app/main.py").decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return None
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def find_updates(current, folders=None, seen=()):
    """Newer RenderBatch update zips in Downloads / Desktop (newest version first): [(path, version)]."""
    if folders is None:
        home = os.path.expanduser("~")
        folders = [os.path.join(home, "Downloads"), os.path.join(home, "Desktop")]
    out = []
    for d in folders:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if not (n.lower().endswith(".zip") and "renderbatch" in n.lower()):
                continue
            try:
                if os.path.getsize(p) > 600 << 20 or _seen_key(p) in seen:
                    continue
            except OSError:
                continue
            v = zip_version(p)
            if v and vtuple(v) > vtuple(current):
                out.append((p, v))
    out.sort(key=lambda pv: vtuple(pv[1]), reverse=True)
    return out


def _seen_key(path):
    try:
        return f"{os.path.basename(path)}|{int(os.path.getmtime(path))}"
    except OSError:
        return os.path.basename(path)


seen_key = _seen_key


def install_info(home):
    """{'mode': 'installed' | 'portable', ...} from install.json (written by the installer)."""
    try:
        with open(os.path.join(home, "install.json"), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            d.setdefault("mode", "portable")
            return d
    except (OSError, ValueError):
        pass
    return {"mode": "portable"}


# ----------------------------------------------------------------------------- installing

def stage(zip_path, home, progress=None):
    """Unpacks the update into <home>/_update (replacing an older unfinished one). Returns the version."""
    version = zip_version(zip_path)
    if not version:
        raise ValueError("This isn't a RenderBatch update (no app/main.py inside).")
    dst = os.path.join(home, STAGE)
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    root = os.path.realpath(dst)
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        pre = _prefix([i.filename for i in infos]) or ""
        files = [i for i in infos if not i.is_dir() and i.filename.replace("\\", "/").startswith(pre)]
        for k, info in enumerate(files):
            rel = info.filename.replace("\\", "/")[len(pre):]
            if not rel or os.path.basename(rel) in NEVER_COPY:
                continue
            target = os.path.realpath(os.path.join(dst, *rel.split("/")))
            if not target.startswith(root + os.sep):
                continue                     # never write outside the folder
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            if progress:
                progress(k + 1, len(files))
    return version


def _copy(src, dst):
    """Plain stream copy (keeps the modification time). Works everywhere, also where CopyFile2 doesn't."""
    with open(src, "rb") as a, open(dst, "wb") as b:
        shutil.copyfileobj(a, b, 1 << 20)
    st = os.stat(src)
    os.utime(dst, (st.st_atime, st.st_mtime))


def launch_helper(home, version, restart=True):
    """Starts the helper that copies <home>/_update over <home> once this process has exited."""
    helper = os.path.join(tempfile.gettempdir(), "renderbatch_apply_update.py")
    _copy(os.path.abspath(__file__), helper)
    py = sys.executable
    if os.name == "nt" and py.lower().endswith("python.exe"):
        w = py[:-len("python.exe")] + "pythonw.exe"
        py = w if os.path.isfile(w) else py
    args = [py, "-E", "-s", helper, "--apply", home, str(os.getpid()), version, "1" if restart else "0"]
    kw = {"close_fds": True, "cwd": tempfile.gettempdir()}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return subprocess.Popen(args, env=env, **kw)


def _wait_exit(pid, timeout=120):
    end = time.time() + timeout
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
        if h:
            k.WaitForSingleObject(h, int(timeout * 1000))
            k.CloseHandle(h)
        return
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def _copy_over(src_root, home):
    failed = []
    for root, _dirs, files in os.walk(src_root):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(src, src_root)
            dst = os.path.join(home, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            for attempt in range(25):                       # files can stay locked a moment after exit
                try:
                    if os.path.exists(dst):
                        os.chmod(dst, 0o666)
                    _copy(src, dst)
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                failed.append(rel)
    return failed


def _set_registry_version(version):
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RenderBatch"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
    except Exception:
        pass


def _message(text, error=False):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "RenderBatch", 0x10 if error else 0x40)
    except Exception:
        print(text)


def apply_main(home, pid, version, restart):
    _wait_exit(pid)
    src = os.path.join(home, STAGE)
    failed = _copy_over(src, home) if os.path.isdir(src) else ["(nothing was unpacked)"]
    if not failed:
        shutil.rmtree(src, ignore_errors=True)
        if install_info(home).get("mode") == "installed":
            _set_registry_version(version)
            try:
                p = os.path.join(home, "install.json")
                d = install_info(home)
                d["version"] = version
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump(d, fh, indent=1)
            except OSError:
                pass
    try:
        with open(os.path.join(home, "update.log"), "w", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  update to {version}: "
                     + ("ok" if not failed else "files not replaced: " + ", ".join(failed)) + "\n")
    except OSError:
        pass
    if failed:
        _message("The update could not replace these files (still in use?):\n\n  " + "\n  ".join(failed[:12])
                 + "\n\nClose RenderBatch and install the update again.", error=True)
    if restart:
        exe = os.path.join(home, "RenderBatch.exe")
        if os.name == "nt" and os.path.isfile(exe):
            subprocess.Popen([exe], cwd=home, close_fds=True)
        elif os.path.isfile(os.path.join(home, "app", "start.py")) and os.name == "nt":
            subprocess.Popen([sys.executable, "-E", "-s", os.path.join(home, "app", "start.py")], cwd=home)


if __name__ == "__main__" and len(sys.argv) >= 6 and sys.argv[1] == "--apply":
    apply_main(sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5] == "1")

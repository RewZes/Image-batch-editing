"""Entry point for the portable build.

1. On the first start, finishes setup: finds the RenderBatch_data_*.rbdata files (next to the app
   or in Downloads), checks them and unpacks them. Uses only the standard library, so it works
   before numpy / OpenCV / ONNX Runtime are unpacked.
2. Starts the app and reports startup errors instead of failing silently.
"""
import json
import os
import sys
import traceback

APP_CODE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("RENDERBATCH_HOME") or os.path.dirname(APP_CODE_DIR)
MANIFEST = os.path.join(HOME, "setup_manifest.json")
sys.path.insert(0, APP_CODE_DIR)


def _tr(text):
    """First-start messages in the interface language chosen in the app (if any)."""
    try:
        import i18n
        if i18n.language() == "en" and not getattr(_tr, "loaded", False):
            _tr.loaded = True
            code = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv[:-1] else None
            if code is None:
                with open(os.path.join(HOME, "settings.json"), "r", encoding="utf-8") as fh:
                    code = json.load(fh).get("ui", {}).get("language", "en")
            i18n.load(code)
        return i18n.tr(text)
    except Exception:
        return text


def _msg(text, title="RenderBatch", icon=0x40):
    text, title = _tr(text), _tr(title)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, icon)
    except Exception:
        print(text)


SETUP_ONLY = "--setup-only" in sys.argv          # run by the installer: finish setup, don't start the app
SOURCE = sys.argv[sys.argv.index("--source") + 1] if "--source" in sys.argv[:-1] else ""
AUTO = "--auto" in sys.argv          # silent install: no questions (an existing RenderBatch is used if found)
SKIP_TOP = {"app", "RenderBatch.exe", "Uninstall.exe", "install.json", "install.ini", "setup_manifest.json", "setup_done.json",
            "crash.log", "update.log", "_update", "cache", "MODELS.txt"}


def _search_dirs():
    dirs, d = [], HOME
    if SOURCE and os.path.isdir(SOURCE):            # where the installer was started from
        dirs += [SOURCE, os.path.dirname(SOURCE)]
    for _ in range(3):  # the app folder and a couple of parents (e.g. where the zip was extracted)
        dirs.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    for base in (os.path.join(os.path.expanduser("~"), "Downloads"),
                 os.path.join(os.path.expanduser("~"), "Desktop")):
        if os.path.isdir(base):
            dirs.append(base)
            try:  # one level of subfolders too
                dirs += [os.path.join(base, x) for x in os.listdir(base) if os.path.isdir(os.path.join(base, x))]
            except OSError:
                pass
    return list(dict.fromkeys(dirs))


def _is_install(d):
    """A complete RenderBatch folder (another copy we can take the Python packages and models from)."""
    try:
        return (os.path.normcase(os.path.realpath(d)) != os.path.normcase(os.path.realpath(HOME))
                and os.path.isdir(os.path.join(d, "runtime", "Lib", "site-packages", "onnxruntime"))
                and os.path.isdir(os.path.join(d, "runtime", "Lib", "site-packages", "PySide6")))
    except OSError:
        return False


def _find_existing():
    cands = []
    home = os.path.expanduser("~")
    roots = _search_dirs() + [home, os.path.join(home, "Documents"),
                              os.path.join(os.environ.get("LOCALAPPDATA", home), "Programs")]
    roots += [f"{c}:\\" for c in "CDEF"] + [f"{c}:\\Tools" for c in "CDEF"] + [f"{c}:\\Program Files" for c in "CD"]
    for r in dict.fromkeys(roots):
        for d in [r] + ([os.path.join(r, x) for x in _listdir(r) if "renderbatch" in x.lower()] if r else []):
            if _is_install(d):
                cands.append(d)
    return list(dict.fromkeys(cands))


def _listdir(d):
    try:
        return os.listdir(d)
    except OSError:
        return []


def _copy_existing(src, step=None):
    """Copies what this folder is missing from another RenderBatch folder (Python packages, models, settings,
    presets, looks, LUTs). Files this folder already has are never replaced, so the new app code stays."""
    todo = []
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        if rel_root == ".":
            dirs[:] = [x for x in dirs if x not in SKIP_TOP]
            files = [f for f in files if f not in SKIP_TOP]
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for f in files:
            rel = os.path.normpath(os.path.join(rel_root, f))
            if not os.path.exists(os.path.join(HOME, rel)):
                todo.append(rel)
    import shutil
    for i, rel in enumerate(todo):
        dst = os.path.join(HOME, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(os.path.join(src, rel), "rb") as a, open(dst, "wb") as b:
            shutil.copyfileobj(a, b, 1 << 20)
        if step and (i % 25 == 0 or i == len(todo) - 1):
            step(i + 1, len(todo))
    return len(todo)


def _pending(man):
    """Packs still needed (installed parts are skipped) and big files still to be joined."""
    packs = [pk for pk in man["packs"]
             if not (pk.get("skip_if") and os.path.exists(os.path.join(HOME, pk["skip_if"])))]
    fresh = set()
    for pk in packs:
        fresh.update(pk.get("chunks", []))
    def have(pt):
        return pt in fresh or os.path.exists(os.path.join(HOME, pt))
    # join a big file when its chunks come with the packs being unpacked (or are already here); a file that
    # is missing but has no chunks anywhere was moved by the app (models are sorted into subfolders) or belongs
    # to an optional part
    chunked = {t: parts for t, parts in man.get("chunked", {}).items()
               if any(pt in fresh for pt in parts)
               or (not os.path.exists(os.path.join(HOME, t)) and all(have(pt) for pt in parts))}
    return packs, chunked


class _Window:
    """The small setup window (tkinter: it works before the app's own packages are there)."""

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.root.title("RenderBatch")
        self.root.resizable(False, False)
        ttk.Label(self.root, text=_tr("Finishing first-time setup..."), padding=(20, 16, 20, 6)).pack()
        self.bar = ttk.Progressbar(self.root, length=380, mode="determinate", maximum=1)
        self.bar.pack(padx=20, pady=(0, 8))
        self.status = ttk.Label(self.root, text="", padding=(20, 0, 20, 16))
        self.status.pack()
        self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.update()

    def step(self, text, value=None, maximum=None):
        if maximum is not None:
            self.bar.configure(maximum=maximum)
        if value is not None:
            self.bar.configure(value=value)
        self.status.configure(text=_tr(text))
        self.root.update()

    def ask(self, text, cancel=False):
        from tkinter import messagebox
        f = messagebox.askyesnocancel if cancel else messagebox.askyesno
        return f("RenderBatch", _tr(text), parent=self.root)

    def folder(self, title):
        from tkinter import filedialog
        return filedialog.askdirectory(parent=self.root, title=_tr(title), mustexist=True)

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


def _use_existing(win, mb):
    """A RenderBatch folder already on this PC: offers to copy the missing parts from it instead of downloading.
    True = copied, False = download instead, None = cancel."""
    found = _find_existing()
    if not found:
        return False
    if not AUTO:
        ans = win.ask(f"RenderBatch is already on this PC:\n\n  {found[0]}\n\n"
                      "Copy its Python packages, models, settings, presets and looks into the new folder? "
                      "It's quicker than downloading.\n\nNo = download them from the internet "
                      f"(about {mb} MB)", cancel=True)
        if ans is None:
            return None
        if not ans:
            return False
    win.show()
    n = _copy_existing(found[0], lambda i, total: win.step(
        f"Copying from your existing RenderBatch ({i} / {total})", i, max(total, 1)))
    win.step(f"Copied {n} files")
    return True


def finish_setup():
    import packs as P
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        man = json.load(fh)

    todo, chunked = _pending(man)
    if not todo and not chunked:
        os.replace(MANIFEST, os.path.join(HOME, "setup_done.json"))
        return

    def locate(todo):
        found, dirs = {}, _search_dirs()
        for pack in todo:
            for d in dirs:
                p = os.path.join(d, pack["name"])
                if os.path.isfile(p) and os.path.getsize(p) == pack["size"]:
                    found[pack["name"]] = p
                    break
        return found

    found = locate(todo)
    win = None
    try:
        need = [pk for pk in todo if pk["name"] not in found and not pk.get("optional")]
        if need:
            win = _Window()
            mb = round(sum(pk["size"] for pk in need) / 2 ** 20)
            copied = _use_existing(win, mb)
            if copied is None:
                win.close()
                sys.exit(1)
            if copied:
                todo, chunked = _pending(man)
                found = locate(todo)
        win = win or _Window()
        win.show()
        local = [pk for pk in todo if pk["name"] in found]
        # optional add-ons (the model converter) download during the installer's setup only; after an update
        # the app gets them when they're first needed
        remote = [pk for pk in todo if pk["name"] not in found and (SETUP_ONLY or not pk.get("optional"))]
        total = max(len(local) + len(remote), 1)
        for i, pack in enumerate(local):
            win.step(f"Checking {pack['name']}", i, total)
            P.unpack(found[pack["name"]], HOME, pack)
        done = len(local)
        failed_optional = []
        for pack in remote:            # not on this PC: download from the RenderBatch repository
            def step(text, f, base=done):
                win.step(text, base + f, total)
            try:
                path = P.download(pack, man, os.path.join(HOME, "_downloads"),
                                  progress=lambda f, pk=pack: step(f"Downloading {pk['name']}", f * 0.9))
                step(f"Unpacking {pack['name']}", 0.95)
                P.unpack(path, HOME)
                P._rm(path)
            except Exception as e:     # noqa: BLE001
                if pack.get("optional"):
                    failed_optional.append(pack["name"])
                    continue
                raise RuntimeError(f"{e}\n\nCheck the internet connection and start RenderBatch again "
                                   "(it continues the setup). You can also download the RenderBatch_*.rbdata "
                                   "files from https://github.com/RewZes/image-batch-editing (data folder) "
                                   "and put them next to the installer or in Downloads.")
            done += 1
        win.step("Joining files", total, total)
        P.join(man, HOME)
        try:
            os.rmdir(os.path.join(HOME, "_downloads"))
        except OSError:
            pass
        win.step("Done", total, total)
        os.replace(MANIFEST, os.path.join(HOME, "setup_done.json"))
    except SystemExit:
        raise
    except Exception as e:
        if win:
            win.close()
        _msg(f"Setup could not finish:\n\n{e}", icon=0x10)
        sys.exit(1)
    win.close()


try:
    if os.path.exists(MANIFEST):
        finish_setup()
        if not SETUP_ONLY:
            # packages were unpacked after Python started, so register them now
            import importlib
            import site
            site.addsitedir(os.path.join(sys.prefix, "Lib", "site-packages"))
            importlib.invalidate_caches()
    if SETUP_ONLY:
        sys.exit(0)
    import main
    main.main()
except SystemExit:
    raise
except BaseException:
    log = os.path.join(HOME, "crash.log")
    text = traceback.format_exc()
    try:
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass
    _msg(text[-1500:] + "\n\nSaved to: " + log, "RenderBatch - error", 0x10)

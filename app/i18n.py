"""
Interface languages: English (built in), Russian, Romanian.

The app's texts are written in English; when another language is chosen, every text that reaches the screen
(labels, buttons, tooltips, list items, dialogs, messages, log lines, painted labels) is looked up in
data/lang_<code>.json. Texts built from values ("{0} images") are matched as templates.
"""
import json
import os
import re

LANGUAGES = [("en", "English"), ("ru", "Русский (Russian)"), ("ro", "Română (Romanian)")]
_table = {}
_patterns = []
_rev = {}          # translated text -> English, for texts that were translated before reaching Qt
_lang = "en"
ROLE = 256 + 77    # item data role that keeps a combo box / list item's English text


def language():
    return _lang


def load(code):
    """Loads a language (does nothing for English)."""
    global _table, _patterns, _lang, _rev
    _lang = code if code in dict(LANGUAGES) else "en"
    _table, _patterns, _rev = {}, [], {}
    if _lang == "en":
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"lang_{_lang}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            _table = json.load(fh)
    except (OSError, ValueError):
        _table = {}
        return
    for src, dst in list(_table.items()):   # also match texts without their surrounding spaces / line breaks
        if src.strip() != src and src.strip() not in _table:
            _table[src.strip()] = dst.strip()
    for src, dst in _table.items():
        if re.search(r"(?<!\{)\{\d+\}(?!\})", src):
            parts = re.split(r"(\{\d+\})", src)
            rx, order = "", []
            for part in parts:
                m = re.fullmatch(r"\{(\d+)\}", part)
                if m:
                    rx += "(.+?)"
                    order.append(int(m.group(1)))
                else:
                    rx += re.escape(part.replace("{{", "{").replace("}}", "}"))
            lit = len(re.sub(r"\{\d+\}", "", src))
            # templates that start / end with fixed text are more specific than ones that start with a value
            score = (not re.match(r"\{\d+\}", src), not re.search(r"\{\d+\}$", src), lit)
            _patterns.append((score, re.compile(rx + r"\Z", re.S), order, dst))
    _patterns.sort(key=lambda p: p[0], reverse=True)
    _rev = {v: k for k, v in _table.items() if v and not re.search(r"\{\d+\}", k)}


def untr(text):
    """English original of a text that is already in the current language (else the text itself)."""
    if _lang == "en" or not isinstance(text, str) or not text:
        return text
    k = _rev.get(text)
    if k is not None:
        return k
    if "\n" in text:
        back = _chunks(text.split("\n"), lambda c: _rev.get(c))
        if back is not None:
            return back
    m = re.match(r"^([^\w\s]+\s+)(.+)$", text, re.S)
    if m:
        b = untr(m.group(2))
        if b != m.group(2):
            return m.group(1) + b
    if "&&" in text and text.replace("&&", "&") in _rev:
        return _rev[text.replace("&&", "&")].replace("&", "&&")
    return text


def _chunks(lines, look):
    """A multi-line text made of known pieces (each one or more lines, e.g. a tooltip + 'Right-click to
    reset'): converts each piece, longest pieces first. None when nothing was known."""
    out, i, hit, n = [], 0, False, len(lines)
    while i < n:
        for j in range(n, i, -1):
            chunk = "\n".join(lines[i:j])
            t = look(chunk) if chunk.strip() else None
            if t is not None and t != chunk:
                out.append(t)
                hit = True
                i = j
                break
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out) if hit else None


def _fill(dst, values):
    out = dst
    for i, v in values.items():
        out = out.replace("{%d}" % i, v)
    return out.replace("{{", "{").replace("}}", "}")


def _value(v, depth=0):
    """Values inside a template: translate them too when they're known words ('upscale, sharpen',
    'denoise → mood → sharpen') or a known message."""
    if v in _table:
        return _table[v]
    for sep in (", ", " → "):
        if sep in v:
            parts = v.split(sep)
            if all(p in _table for p in parts):
                return sep.join(_table[p] for p in parts)
            if depth < 2:
                tp = [_tr(p, depth + 1) for p in parts]
                if tp != parts:
                    return sep.join(tp)
    if depth < 2 and re.search(r"[A-Za-z]{3}", v):
        return _tr(v, depth + 1)
    return v


def tr(text):
    return _tr(text, 0)


def _tr(text, depth):
    if _lang == "en" or not isinstance(text, str) or not text:
        return text
    t = _table.get(text)
    if t is not None:
        return t
    for _lit, rx, order, dst in _patterns:
        m = rx.match(text)
        if m:
            return _fill(dst, {n: _value(g, depth) for n, g in zip(order, m.groups())})
    # multi-line texts built from several known lines
    if "\n" in text and depth < 3:
        t = _chunks(text.split("\n"), lambda c: _tr(c, depth + 1) if "\n" not in c else _table.get(c))
        if t is not None:
            return t
    # "▾  Card title", "⌖  Pick on image", "▾  Texture && grain": a symbol in front of a known text
    m = re.match(r"^([^\w\s]+\s+)(.+)$", text, re.S)
    if m and depth < 3:
        t = _tr(m.group(2), depth + 1)
        if t != m.group(2):
            return m.group(1) + t
    if "&&" in text:      # "&&" = a literal "&" in a Qt button text
        t = _table.get(text.replace("&&", "&"))
        if t is not None:
            return t.replace("&", "&&")
    return text


def tr_label(text):
    """Painted labels like 'AFTER · +UPSCALE +SHARPEN'."""
    if _lang == "en" or not text:
        return text
    whole = tr(text)
    if whole != text:
        return whole
    out = []
    for part in text.split(" · "):
        if part.startswith("+"):
            out.append(" ".join("+" + tr(tok[1:]) if tok.startswith("+") else tr(tok) for tok in part.split()))
        else:
            out.append(tr(part))
    return " · ".join(out)


_installed = False
_ORIG = {}


def _keep(obj, key, fn, original, *head):
    """Remembers the English text of a widget property, so the language can change later in place."""
    try:
        d = obj.__dict__.setdefault("_i18n", {})
        d[key] = (fn, head, original)
    except Exception:
        pass


def install():
    """Routes Qt's text setters through tr(), so every widget shows the chosen language, and remembers each
    text's English original so retranslate() can switch the language of an open window in place."""
    global _installed
    if _installed:
        return
    _installed = True
    from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QComboBox, QFileDialog, QLabel, QLineEdit,
                                   QListWidgetItem, QMenu, QMessageBox, QPushButton, QRadioButton, QToolButton,
                                   QWidget, QInputDialog)
    from PySide6.QtGui import QAction

    def wrap_init(cls, setter):
        orig = cls.__init__

        def init(self, *a, **k):
            en = None
            if a and isinstance(a[0], str):
                a = list(a)
                en = untr(a[0])
                a[0] = tr(en)
            orig(self, *a, **k)
            if en:
                _keep(self, "text", setter, en)
        cls.__init__ = init

    _ORIG["label"] = QLabel.setText
    _ORIG["button"] = QAbstractButton.setText
    wrap_init(QLabel, QLabel.setText)
    for cls in (QPushButton, QCheckBox, QRadioButton, QToolButton):
        wrap_init(cls, QAbstractButton.setText)

    orig_item = QListWidgetItem.__init__

    def item_init(self, *a, **k):
        en = None
        if a and isinstance(a[0], str):
            a = list(a)
            en = untr(a[0])
            a[0] = tr(en)
        orig_item(self, *a, **k)
        if en:
            self.setData(ROLE, en)
    QListWidgetItem.__init__ = item_init

    def wrap(cls, name, key):
        orig = getattr(cls, name)
        _ORIG[(cls.__name__, name)] = orig

        def f(self, *a, **k):
            if a and isinstance(a[0], str):
                a = list(a)
                en = untr(a[0])
                a[0] = tr(en)
                _keep(self, key, orig, en)
            return orig(self, *a, **k)
        setattr(cls, name, f)

    wrap(QLabel, "setText", "text")
    wrap(QAbstractButton, "setText", "text")
    wrap(QWidget, "setToolTip", "tip")
    wrap(QWidget, "setWindowTitle", "title")
    wrap(QLineEdit, "setPlaceholderText", "placeholder")
    wrap(QAction, "setText", "text")
    wrap(QAction, "setToolTip", "tip")

    # combo boxes: each item keeps its English text in a data role
    orig_set_item = QComboBox.setItemText
    _ORIG["combo_set"] = orig_set_item

    def set_item_text(self, i, text):
        if isinstance(text, str):
            en = untr(text)
            orig_set_item(self, i, tr(en))
            self.setItemData(i, en, ROLE)
            return None
        return orig_set_item(self, i, text)
    QComboBox.setItemText = set_item_text

    orig_add = QComboBox.addItem

    def add_item(self, *a, **k):
        a = list(a)
        en = None
        for i, v in enumerate(a[:2]):
            if isinstance(v, str):
                en = untr(v)
                a[i] = tr(en)
                break
        r = orig_add(self, *a, **k)
        if en is not None:
            self.setItemData(self.count() - 1, en, ROLE)
        return r
    QComboBox.addItem = add_item
    orig_adds = QComboBox.addItems

    def add_items(self, items):
        n0 = self.count()
        en = [untr(x) for x in items]
        orig_adds(self, [tr(x) for x in en])
        for j, e in enumerate(en):
            self.setItemData(n0 + j, e, ROLE)
    QComboBox.addItems = add_items

    orig_menu = QMenu.addAction

    def menu_add(self, *a, **k):
        a = list(a)
        en = None
        for i, v in enumerate(a[:2]):
            if isinstance(v, str):
                en = untr(v)
                a[i] = tr(en)
                break
        act = orig_menu(self, *a, **k)
        if en is not None and act is not None:
            _keep(act, "text", _ORIG[("QAction", "setText")], en)
        return act
    QMenu.addAction = menu_add

    for name in ("warning", "information", "question", "critical"):
        orig = getattr(QMessageBox, name)

        def box(parent, title, text, *a, __o=orig, **k):
            return __o(parent, tr(title), tr(text), *a, **k)
        setattr(QMessageBox, name, staticmethod(box))

    for name in ("getOpenFileName", "getSaveFileName", "getExistingDirectory"):
        orig = getattr(QFileDialog, name)

        def dlg(parent=None, caption="", *a, __o=orig, **k):
            a = list(a)
            if len(a) > 1 and isinstance(a[1], str):   # file-type filter
                a[1] = ";;".join(tr(x) for x in a[1].split(";;"))
            return __o(parent, tr(caption), *a, **k)
        setattr(QFileDialog, name, staticmethod(dlg))

    orig_gt = QInputDialog.getText

    def gettext(parent, title, label, *a, **k):
        return orig_gt(parent, tr(title), tr(label), *a, **k)
    QInputDialog.getText = staticmethod(gettext)


def retranslate(app):
    """Puts every open window's texts into the current language (after load()), in place."""
    from PySide6.QtWidgets import QComboBox, QListWidget
    seen = set()

    def redo(obj):
        d = getattr(obj, "_i18n", None)
        if not d:
            return
        for key, (fn, head, en) in list(d.items()):
            try:
                fn(obj, *head, tr(en))
            except Exception:
                pass

    for w in app.allWidgets():
        redo(w)
        for act in w.actions():
            if id(act) not in seen:
                seen.add(id(act))
                redo(act)
        if isinstance(w, QComboBox):
            for i in range(w.count()):
                en = w.itemData(i, ROLE)
                if isinstance(en, str):
                    _ORIG["combo_set"](w, i, tr(en))
        elif isinstance(w, QListWidget):
            for i in range(w.count()):
                it = w.item(i)
                en = it.data(ROLE)
                if isinstance(en, str):
                    it.setText(tr(en))
        w.update()

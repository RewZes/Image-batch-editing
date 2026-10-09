"""
Corona LightMix side panel: one row per light (or per group of lights), color boxes that can be dragged from
one light to another, a compact color picker with a color-temperature slider (the image updates while you
pick), and named light groups (e.g. one per room).

Two independent setups, one per mode (the mode shown is the one that is rendered):
  Individual lights: lightmix        {light name: {"mult": x, "color": [r,g,b] or None, "on": None | False}}
  Group lights:      lightmix_grouped (same, for lights that aren't in a group)
                     light_groups    [{"name", "lights": [names], "mult", "color", "on"}]
A group multiplies its lights' intensity, can switch them off, and its color (if set) replaces theirs.
"""
import colorsys
import json

import numpy as np
from PySide6.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from ui_common import SliderRow, field, prop, wrap_checkboxes

COLOR_MIME = "application/x-renderbatch-color"
LIGHT_MIME = "application/x-renderbatch-light"


def kelvin_color(T):
    """Display color of a blackbody at T kelvin (brightest channel = 1), as LightMix colors are entered."""
    import corona
    xyz = corona.planck_xy(T)
    X, Y, Z = xyz[0] / xyz[1], 1.0, xyz[2] / xyz[1]
    rgb = np.linalg.inv(corona.RGB2XYZ) @ np.array([X, Y, Z])
    rgb = np.clip(rgb, 0, None)
    rgb = rgb / max(rgb.max(), 1e-9)
    return [float(v) for v in np.power(rgb, 1 / 2.2)]


def _hex(c):
    return QColor.fromRgbF(*[min(max(v, 0.0), 1.0) for v in c]).name()


def _grad(stops):
    parts = ", ".join(f"stop:{p:.3f} {c}" for p, c in stops)
    return (f"QSlider::groove:horizontal {{ height: 8px; border-radius: 4px; border: 1px solid rgba(0,0,0,60); "
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {parts}); }}"
            "QSlider::sub-page:horizontal { background: transparent; }"
            "QSlider::add-page:horizontal { background: transparent; }")


# ----------------------------------------------------------------------------- color picker

class ColorPicker(QDialog):
    """Compact picker: preview, hex / RGB, color temperature, hue / saturation / brightness sliders,
    and the colors used by other lights. Every change is sent out right away (live preview)."""
    preview = Signal(object)   # [r, g, b] or None (= the file's color)

    def __init__(self, parent, title, color, used=(), reset_text="↺  File color"):
        super().__init__(parent)
        self._reset_text = reset_text
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self.original = color
        self.file_color = False
        self.color = list(color) if color else [1.0, 1.0, 1.0]
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.old_sw = QLabel()
        self.old_sw.setFixedSize(34, 34)
        self.old_sw.setToolTip("Before")
        self.new_sw = QLabel()
        self.new_sw.setFixedSize(58, 34)
        self.new_sw.setToolTip("New color")
        top.addWidget(self.old_sw)
        top.addWidget(self.new_sw)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setFixedWidth(84)
        self.hex.setToolTip("Hex color, e.g. #ffb46b")
        self.rgb = []
        rl = QHBoxLayout()
        rl.setSpacing(4)
        for ch in "RGB":
            sp = QSpinBox()
            sp.setRange(0, 255)
            sp.setButtonSymbols(QSpinBox.NoButtons)
            sp.setAlignment(Qt.AlignCenter)
            sp.setFixedWidth(40)
            sp.setToolTip(ch)
            sp.valueChanged.connect(self._from_rgb)
            self.rgb.append(sp)
            rl.addWidget(sp)
        hr = QHBoxLayout()
        hr.addWidget(self.hex)
        hr.addStretch()
        col.addLayout(hr)
        col.addLayout(rl)
        top.addLayout(col)
        top.addStretch()
        lay.addLayout(top)

        self.temp = SliderRow("Temperature", 1500, 12000, 6500, 0, " K",
                              "Warm (low) to cool (high), like a real light source. 6500 K = white", (1000, 40000))
        self.temp.slider.setStyleSheet(_grad([(i / 8, _hex(kelvin_color(1500 + i * (10500 / 8)))) for i in range(9)]))
        self.temp.changed.connect(self._from_temp)
        lay.addWidget(self.temp)
        self.hue = SliderRow("Hue", 0, 359, 0, 0, "°")
        self.hue.slider.setStyleSheet(_grad([(i / 6, QColor.fromHsvF(min(i / 6, 0.999), 1, 1).name()) for i in range(7)]))
        self.sat = SliderRow("Saturation", 0, 100, 0, 0, " %")
        self.val = SliderRow("Brightness", 0, 100, 100, 0, " %")
        for w in (self.hue, self.sat, self.val):
            w.changed.connect(self._from_hsv)
            lay.addWidget(w)

        if used:
            ul = QHBoxLayout()
            ul.setSpacing(4)
            lbl = QLabel("In use:")
            lbl.setObjectName("Subtle")
            ul.addWidget(lbl)
            for c in list(dict.fromkeys(tuple(round(v, 4) for v in u) for u in used))[:10]:
                b = QToolButton()
                b.setFixedSize(20, 20)
                b.setStyleSheet(f"QToolButton {{ background: {_hex(c)}; border: 1px solid rgba(128,128,128,140); "
                                f"border-radius: 4px; }}")
                b.setToolTip(_hex(c))
                b.clicked.connect(lambda _=False, c=c: self._set(list(c), "used"))
                ul.addWidget(b)
            ul.addStretch()
            lay.addLayout(ul)

        br = QHBoxLayout()
        reset = prop(QPushButton(self._reset_text), "flat")
        reset.setToolTip("Go back to the color saved in each file")
        reset.clicked.connect(self._use_file)
        br.addWidget(reset)
        br.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = prop(QPushButton("OK"), "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        br.addWidget(cancel)
        br.addWidget(ok)
        lay.addLayout(br)

        self.hex.editingFinished.connect(self._from_hex)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.preview.emit(None if self.file_color else list(self.color)))
        self._paint_swatch(self.old_sw, color)
        self._set(self.color, "init")

    def _paint_swatch(self, lbl, c):
        border = "1px solid rgba(128,128,128,160)"
        lbl.setStyleSheet(f"background: {_hex(c) if c else '#ffffff'}; border: {border}; border-radius: 6px;")

    def _set(self, c, source):
        self.color = [min(max(float(v), 0.0), 1.0) for v in c]
        self.file_color = False
        self._busy = True
        try:
            q = QColor.fromRgbF(*self.color)
            if source != "hex":
                self.hex.setText(q.name())
            if source != "rgb":
                for sp, v in zip(self.rgb, (q.red(), q.green(), q.blue())):
                    sp.setValue(v)
            if source != "hsv":
                h, s_, v = colorsys.rgb_to_hsv(*self.color)
                if s_ > 0.001:
                    self.hue.set(round(h * 360) % 360, emit=False)
                self.sat.set(round(s_ * 100), emit=False)
                self.val.set(round(v * 100), emit=False)
            hc = QColor.fromHsvF(self.hue.value() / 360 % 1, 1, 1).name()
            vv = max(self.val.value() / 100, 0.001)
            self.sat.slider.setStyleSheet(_grad([(0, QColor.fromHsvF(0, 0, vv).name()),
                                                 (1, QColor.fromHsvF(self.hue.value() / 360 % 1, 1, vv).name())]))
            self.val.slider.setStyleSheet(_grad([(0, "#000000"), (1, QColor.fromHsvF(self.hue.value() / 360 % 1,
                                                                                       self.sat.value() / 100, 1).name())]))
            _ = hc
        finally:
            self._busy = False
        self._paint_swatch(self.new_sw, self.color)
        if source != "init":
            self._timer.start(60)

    def _from_temp(self, T):
        if not self._busy:
            self._set(kelvin_color(T), "temp")

    def _from_hsv(self, *_):
        if not self._busy:
            r, g, b = colorsys.hsv_to_rgb(self.hue.value() / 360 % 1, self.sat.value() / 100, self.val.value() / 100)
            self._set([r, g, b], "hsv")

    def _from_rgb(self, *_):
        if not self._busy:
            self._set([sp.value() / 255 for sp in self.rgb], "rgb")

    def _from_hex(self):
        q = QColor(self.hex.text().strip())
        if q.isValid():
            self._set([q.redF(), q.greenF(), q.blueF()], "hex")

    def _use_file(self):
        self.file_color = True
        self.preview.emit(None)
        self.accept()

    def reject(self):
        self._timer.stop()
        self.preview.emit(self.original)   # put the light back as it was
        super().reject()


class ColorSwatch(QToolButton):
    """A light's color (white = as saved in each file). Click: pick. Drag onto another box: copy the color."""
    colorChanged = Signal()

    def __init__(self, title, used=None, tip=None, reset_text="↺  File color"):
        super().__init__()
        self.title = title
        self.reset_text = reset_text
        self.color = None
        self.used = used or (lambda: [])
        self.setFixedSize(30, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setToolTip("Light color (white box = the color saved in each file)\nClick: pick a color\n"
                        "Drag onto another light's color box: copy this color\nRight-click: back to the file's color"
                        if tip is None else tip)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _: self.set_color(None, True))
        self._press = None
        self._paint()

    def _paint(self):
        c = self.color or [1.0, 1.0, 1.0]
        self.setStyleSheet(f"QToolButton {{ background: {_hex(c)}; border: 1px solid rgba(128,128,128,150); "
                           f"border-radius: 4px; }} QToolButton:hover {{ border: 1px solid palette(highlight); }}")

    def set_color(self, col, emit=False):
        self.color = None if col is None else [float(v) for v in col[:3]]
        self._paint()
        if emit:
            self.colorChanged.emit()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.position().toPoint() - self._press).manhattanLength() > 6:
            self._press = None
            self.setDown(False)
            drag = QDrag(self)
            md = QMimeData()
            md.setData(COLOR_MIME, json.dumps(self.color).encode())
            drag.setMimeData(md)
            drag.setPixmap(self.grab())
            drag.exec(Qt.CopyAction)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        clicked = self._press is not None and e.button() == Qt.LeftButton
        self._press = None
        super().mouseReleaseEvent(e)
        if clicked and self.rect().contains(e.position().toPoint()):
            self.open_picker()

    def open_picker(self):
        dlg = ColorPicker(self.window(), f"Color · {self.title}", self.color, self.used(), self.reset_text)
        dlg.preview.connect(lambda c: self.set_color(c, True))   # live: the image follows the picker
        dlg.exec()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(COLOR_MIME) and e.source() is not self:
            e.acceptProposedAction()

    def dropEvent(self, e):
        try:
            col = json.loads(bytes(e.mimeData().data(COLOR_MIME)).decode())
        except ValueError:
            return
        self.set_color(col, True)
        e.acceptProposedAction()


# ----------------------------------------------------------------------------- rows

class _Handle(QLabel):
    """Drag handle that carries a light name (drop it on a group)."""

    def __init__(self, name):
        super().__init__("⠿")
        self.name = name
        self.setObjectName("Subtle")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag onto a group to put this light in it")
        self._press = None

    def mousePressEvent(self, e):
        self._press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.position().toPoint() - self._press).manhattanLength() > 5:
            self._press = None
            drag = QDrag(self)
            md = QMimeData()
            md.setData(LIGHT_MIME, self.name.encode())
            drag.setMimeData(md)
            pm = QPixmap(160, 22)
            pm.fill(QColor(60, 60, 70, 220))
            p = QPainter(pm)
            p.setPen(QColor(240, 240, 240))
            p.drawText(pm.rect().adjusted(8, 0, 0, 0), Qt.AlignVCenter, self.name)
            p.end()
            drag.setPixmap(pm)
            drag.exec(Qt.MoveAction)


class LightRow(QFrame):
    """One light, two lines: [⠿ ☑ name ....... value × ▢] and the intensity slider."""
    changed = Signal()

    def __init__(self, name, note="", used=None):
        super().__init__()
        self.setObjectName("LightRow")
        self.name = name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(0)
        self.swatch = ColorSwatch(name, used)
        self.mult = SliderRow("", 0, 4, 1.0, 2, " ×",
                              "Intensity: multiplies the value saved in each file. 1 = unchanged, 0 = off",
                              (0.0, 1000.0), extra=self.swatch)
        self.mult.label.hide()
        head = QWidget()
        self.top = QHBoxLayout(head)
        self.top.setContentsMargins(0, 0, 0, 0)
        self.top.setSpacing(5)
        self.handle = _Handle(name)
        self.top.addWidget(self.handle)
        self.on = QCheckBox(name)
        self.on.setChecked(True)
        self.on.setMinimumWidth(0)
        self.on.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.on.setToolTip("Unchecked = this light is off in every file")
        self.top.addWidget(self.on, 1)
        self.tag = QLabel(note)
        self.tag.setObjectName("Subtle")
        self.tag.setStyleSheet("font-size: 8pt;")
        self.top.addWidget(self.tag)
        self.mult.layout().addWidget(head, 0, 0)
        lay.addWidget(self.mult)
        self.on.toggled.connect(lambda _: (self._enable(), self.changed.emit()))
        self.mult.changed.connect(lambda _: self.changed.emit())
        self.swatch.colorChanged.connect(self.changed.emit)

    def _enable(self):
        on = self.on.isChecked()
        self.mult.slider.setEnabled(on)
        self.mult.spin.setEnabled(on)
        self.swatch.setEnabled(on)

    def value(self):
        return {"mult": round(self.mult.value(), 4), "color": self.swatch.color,
                "on": None if self.on.isChecked() else False}

    def set(self, d):
        d = d or {}
        self.on.blockSignals(True)
        self.on.setChecked(d.get("on") is not False)
        self.on.blockSignals(False)
        self._enable()
        self.mult.set(float(d.get("mult", 1.0)), emit=False)
        self.swatch.set_color(d.get("color"))


class GroupRow(LightRow):
    """A named group of lights. Double-click the name to rename it; drop lights (⠿) on it to add them."""
    edit = Signal(str)            # group name (⋯ button)
    dropped = Signal(str, str)    # group name, light name
    renamed = Signal(str, str)    # old, new

    def __init__(self, name, lights, used=None):
        super().__init__(name, "", used)
        self.setObjectName("GroupRow")
        self.setAcceptDrops(True)
        self.handle.setText("▣")
        self.handle.setCursor(Qt.ArrowCursor)
        self.handle.mouseMoveEvent = lambda e: None
        self.handle.setToolTip("Group")
        self.on.setText("")
        self.on.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.on.setToolTip("Unchecked = every light in this group is off")
        self.title = QLabel(name)
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setToolTip("Double-click to rename")
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.title.installEventFilter(self)
        self.top.insertWidget(2, self.title, 1)
        self.top.setStretch(self.top.indexOf(self.on), 0)
        self.editor = None
        more = prop(QToolButton(), "flat")
        more.setText("⋯")
        more.setToolTip("Choose lights, rename, ungroup")
        more.clicked.connect(lambda: self.edit.emit(self.name))
        self.top.addWidget(more)
        self.mult.spin.setToolTip("Group intensity: multiplies every light in the group")
        self.swatch.setToolTip("Group color: replaces the color of every light in the group (white box = keep theirs)\n"
                               "Click: pick · drag: copy to another light or group · right-click: keep theirs")
        self.members = QLabel("")
        self.members.setObjectName("Subtle")
        self.members.setWordWrap(True)
        self.members.setStyleSheet("font-size: 8pt;")
        self.layout().addWidget(self.members)
        self.set_members(lights)

    def set_members(self, lights):
        self.members.setText(", ".join(lights) if lights else "Empty: drag lights here (⠿) or use ⋯")

    def eventFilter(self, obj, ev):
        if obj is self.title and ev.type() == QEvent.MouseButtonDblClick:
            self.start_rename()
            return True
        return False

    def start_rename(self):
        if self.editor is not None:
            return
        self.editor = QLineEdit(self.name)
        self.editor.selectAll()
        self.title.hide()
        self.top.insertWidget(self.top.indexOf(self.title), self.editor, 1)
        self.editor.setFocus()
        self.editor.editingFinished.connect(self._finish_rename)

    def _finish_rename(self):
        if self.editor is None:
            return
        new = self.editor.text().strip()
        ed, self.editor = self.editor, None
        ed.hide()
        ed.deleteLater()
        self.title.show()
        if new and new != self.name:
            self.renamed.emit(self.name, new)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(LIGHT_MIME):
            e.acceptProposedAction()
            self.setProperty("drop", True)
            self.style().polish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("drop", False)
        self.style().polish(self)

    def dropEvent(self, e):
        self.setProperty("drop", False)
        self.style().polish(self)
        self.dropped.emit(self.name, bytes(e.mimeData().data(LIGHT_MIME)).decode())
        e.acceptProposedAction()


class GroupDialog(QDialog):
    """Name a group and choose its lights."""

    def __init__(self, parent, lights, groups, group=None):
        super().__init__(parent)
        self.setWindowTitle("Edit group" if group else "Create group")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        self.name = QLineEdit(group["name"] if group else "")
        self.name.setPlaceholderText("e.g. Kitchen, Living room, Exterior")
        lay.addWidget(field("Name", self.name))
        lay.addWidget(QLabel("Lights in this group:"))
        owner = {l: g["name"] for g in groups for l in g["lights"]}
        self.list = QListWidget()
        for l in lights:
            other = owner.get(l)
            text = l if not other or (group and other == group["name"]) else f"{l}   (now in {other})"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, l)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if group and l in group["lights"] else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        note = QLabel("A light can be in one group. Moving it here takes it out of its other group.")
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self.delete = False
        if group:
            d = prop(QPushButton("Ungroup"), "danger")
            d.setToolTip("Delete the group; its lights go back to the list with their own settings")
            d.clicked.connect(self._delete)
            row.addWidget(d)
        row.addStretch()
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        ok = prop(QPushButton("Save group" if group else "Create group"), "primary")
        ok.clicked.connect(self._ok)
        row.addWidget(c)
        row.addWidget(ok)
        lay.addLayout(row)
        self.groups = groups
        self.group = group

    def _delete(self):
        self.delete = True
        self.accept()

    def _ok(self):
        n = self.name.text().strip()
        if not n:
            self.name.setFocus()
            self.name.setProperty("error", True)
            self.name.style().polish(self.name)
            return
        if any(g["name"] == n for g in self.groups if g is not self.group):
            QMessageBox.warning(self, "Group", f"There's already a group called '{n}'.")
            return
        self.accept()

    def result_value(self):
        lights = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.Checked]
        return self.name.text().strip(), lights


# ----------------------------------------------------------------------------- panel

class LightMixPanel(QFrame):
    """The right-hand Corona LightMix panel."""
    changed = Signal()
    closed = Signal()
    modeChanged = Signal(str)

    def __init__(self, mode="individual"):
        super().__init__()
        self.setObjectName("LightPanel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(460)
        self.names, self.count, self.n_cxr = [], {}, 0
        self.vals = {"individual": {}, "group": {}}   # light values, one set per mode
        self.groups = []
        self.rows, self.group_rows = {}, {}
        self.mode = self.rows_mode = mode
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QFrame()
        head.setObjectName("PanelHead")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(12, 8, 8, 6)
        hl.setSpacing(6)
        tr = QHBoxLayout()
        t = QLabel("💡  Corona LightMix")
        t.setStyleSheet("font-weight: 600;")
        tr.addWidget(t, 1)
        x = prop(QToolButton(), "flat")
        x.setText("✕")
        x.setToolTip("Hide the LightMix panel (your light settings are kept)")
        x.clicked.connect(self.closed.emit)
        tr.addWidget(x)
        hl.addLayout(tr)
        seg = QFrame()
        seg.setObjectName("Seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.mode_btns = {}
        grp = QButtonGroup(seg)
        for key, text, tip in (("individual", "Individual lights", "Every light on its own. Its own values, "
                                "separate from Group lights; this is what's rendered while it's selected."),
                               ("group", "Group lights", "Groups plus the lights that aren't in a group. Its own "
                                "values, separate from Individual lights; rendered while it's selected.")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setChecked(key == mode)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k, True))
            grp.addButton(b)
            sl.addWidget(b)
            self.mode_btns[key] = b
        hl.addWidget(seg)
        self.info = QLabel("")
        self.info.setObjectName("Subtle")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-size: 8pt;")
        hl.addWidget(self.info)
        outer.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("SidebarInner")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(12, 4, 12, 8)
        self.body.setSpacing(2)
        self.list_w = QWidget()
        self.list_lay = QVBoxLayout(self.list_w)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(2)
        self.body.addWidget(self.list_w)
        self.body.addSpacing(6)
        self.post = QCheckBox("Use each file's Post settings")
        self.post.setToolTip("On: looks exactly like Corona's frame buffer / Image Editor (tone mapping, LUT, "
                             "curves…).\nOff: plain LightMix result without Corona's tone mapping.")
        self.body.addWidget(self.post)
        self.dn = QComboBox()
        for key, text in (("file", "As saved (denoised when Corona's denoiser was on)"),
                          ("always", "Always the denoised passes"), ("never", "Never (raw passes)")):
            self.dn.addItem(text, key)
        self.dn.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.dn.setMinimumContentsLength(6)
        self.dn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.body.addWidget(field("Light passes", self.dn))
        self.body.addStretch()
        self.scroll.setWidget(inner)
        outer.addWidget(self.scroll, 1)

        foot = QFrame()
        foot.setObjectName("PanelFoot")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(12, 6, 12, 8)
        fl.setSpacing(4)
        self.create_btn = prop(QPushButton("＋  Create group"), "primary")
        self.create_btn.setToolTip("Put several lights in a named group (e.g. one per room) and control them together")
        self.create_btn.clicked.connect(lambda: self.edit_group(None))
        fl.addWidget(self.create_btn)
        br = QHBoxLayout()
        br.setSpacing(4)
        self.reset_btn = prop(QPushButton("Reset lights"), "flat")
        self.save_btn = prop(QPushButton("Save…"), "flat")
        self.load_btn = prop(QPushButton("Load…"), "flat")
        self.reset_btn.setToolTip("The lights of the mode shown back to how they're saved in the files "
                                  "(groups are kept)")
        self.save_btn.setToolTip("Save both setups and the groups as a preset")
        self.load_btn.setToolTip("Load a lights preset")
        for b in (self.reset_btn, self.save_btn, self.load_btn):
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            br.addWidget(b)
        fl.addLayout(br)
        outer.addWidget(foot)
        wrap_checkboxes(inner)
        self.reset_btn.clicked.connect(self.reset_lights)
        self.set_lights([], {}, 0, [])

    # ------------------------------------------------------------------ values

    @staticmethod
    def _clean(vals):
        return {k: v for k, v in vals.items()
                if abs(float(v.get("mult", 1.0)) - 1) > 1e-6 or v.get("color") or v.get("on") is False}

    def light_values(self, mode="individual"):
        self._save_rows()
        return self._clean(self.vals[mode])

    def group_values(self):
        self._save_rows()
        return [{"name": g["name"], "lights": list(g["lights"]), "mult": float(g.get("mult") or 1.0),
                 "color": g.get("color"), "on": g.get("on")} for g in self.groups]

    def set_values(self, individual, grouped, groups, mode=None):
        self.vals = {"individual": {k: dict(v) for k, v in (individual or {}).items() if isinstance(v, dict)},
                     "group": {k: dict(v) for k, v in (grouped or {}).items() if isinstance(v, dict)}}
        self.groups = [dict(g, lights=list(g.get("lights", []))) for g in (groups or [])
                       if isinstance(g, dict) and g.get("name")]
        self.rows, self.group_rows = {}, {}
        if mode:
            self.mode = mode
            for k, b in self.mode_btns.items():
                b.setChecked(k == mode)
        self._rebuild(save=False)

    def used_colors(self):
        self._save_rows()
        cols = [v.get("color") for m in self.vals.values() for v in m.values() if v.get("color")]
        cols += [g.get("color") for g in self.groups if g.get("color")]
        return cols

    def reset_lights(self):
        self._save_rows()
        self.vals[self.mode] = {}
        if self.mode == "group":
            for g in self.groups:
                g.update(mult=1.0, color=None, on=None)
        self._rebuild(save=False)
        self.changed.emit()

    # ------------------------------------------------------------------ building

    def set_lights(self, names, count, n_cxr, errors):
        self._save_rows()
        self.names, self.count, self.n_cxr = list(names), dict(count), n_cxr
        if not n_cxr:
            text = "No .cxr files in the input folder."
        elif not names:
            text = (f".cxr files: {n_cxr}, but no LightMix light selects in them. "
                    "Add LightSelect elements in Corona to re-light here.")
        else:
            text = f".cxr files: {n_cxr} · lights: {len(names)} · a change applies to that light in every file."
        if errors:
            text += "\n⚠ Can't read: " + ", ".join(f"{n} ({m})" for n, m in errors[:3])
        self.info.setText(text)
        self._rebuild(save=False)

    def set_mode(self, mode, emit=False):
        self._save_rows()
        self.mode = mode
        for k, b in self.mode_btns.items():
            b.setChecked(k == mode)
        self._rebuild(save=False)
        if emit:
            self.modeChanged.emit(mode)

    def _save_rows(self):
        for n, r in self.rows.items():
            self.vals[self.rows_mode][n] = r.value()
        for g in self.groups:
            r = self.group_rows.get(g["name"])
            if r:
                g.update(r.value())

    def _rebuild(self, save=True):
        if save:
            self._save_rows()
        self.rows, self.group_rows = {}, {}
        while self.list_lay.count():
            wdg = self.list_lay.takeAt(0).widget()
            if wdg is not None:
                wdg.hide()
                wdg.deleteLater()
        self.rows_mode = self.mode
        vals = self.vals[self.mode]
        owner = {l: g["name"] for g in self.groups for l in g["lights"]}
        grouped = self.mode == "group"
        if grouped:
            for g in self.groups:
                shown = [l if l in self.names else f"{l} (not in these files)" for l in g["lights"]]
                r = GroupRow(g["name"], shown, self.used_colors_now)
                r.set({k: g.get(k) for k in ("mult", "color", "on")})
                r.changed.connect(self._group_changed)
                r.edit.connect(lambda n: self.edit_group(n))
                r.dropped.connect(self._drop_into_group)
                r.renamed.connect(self._rename_group)
                self.list_lay.addWidget(r)
                self.group_rows[g["name"]] = r
            if self.groups:
                line = QFrame()
                line.setFrameShape(QFrame.HLine)
                line.setObjectName("Sep")
                self.list_lay.addWidget(line)
        for n in self.names:
            if grouped and n in owner:
                continue
            note = f"{self.count.get(n, 0)}/{self.n_cxr}" if self.count.get(n, 0) < self.n_cxr else ""
            r = LightRow(n, note, self.used_colors_now)
            if note:
                r.tag.setToolTip(f"Only {self.count.get(n, 0)} of the {self.n_cxr} .cxr files have this light")
            r.set(vals.get(n))
            r.changed.connect(lambda n=n: self._light_changed(n))
            self.list_lay.addWidget(r)
            self.rows[n] = r
        if grouped and self.names and not self.groups:
            tip = QLabel("No groups yet: “＋ Create group” below, or drag lights (⠿) onto a group.")
            tip.setObjectName("Subtle")
            tip.setWordWrap(True)
            self.list_lay.insertWidget(0, tip)
        self.create_btn.setEnabled(bool(self.names))
        for w in self.list_w.findChildren(QAbstractSpinBox) + self.list_w.findChildren(QSlider):
            w.setFocusPolicy(Qt.StrongFocus)

    def used_colors_now(self):
        return self.used_colors()

    def _light_changed(self, n):
        self.vals[self.rows_mode][n] = self.rows[n].value()
        self.changed.emit()

    def _group_changed(self):
        self._save_rows()
        self.changed.emit()

    def _drop_into_group(self, gname, light):
        self._save_rows()
        for g in self.groups:
            if light in g["lights"] and g["name"] != gname:
                g["lights"].remove(light)
        for g in self.groups:
            if g["name"] == gname and light not in g["lights"]:
                g["lights"].append(light)
        self._rebuild(save=False)
        self.changed.emit()

    def _rename_group(self, old, new):
        self._save_rows()
        if any(g["name"] == new for g in self.groups):
            QMessageBox.warning(self, "Group", f"There's already a group called '{new}'.")
            self._rebuild(save=False)
            return
        for g in self.groups:
            if g["name"] == old:
                g["name"] = new
        self._rebuild(save=False)
        self.changed.emit()

    def edit_group(self, name):
        self._save_rows()
        group = next((g for g in self.groups if g["name"] == name), None)
        dlg = GroupDialog(self.window(), self.names, self.groups, group)
        if not dlg.exec():
            return
        if dlg.delete and group:
            self.groups.remove(group)
        else:
            gname, lights = dlg.result_value()
            for g in self.groups:
                if g is not group:
                    g["lights"] = [l for l in g["lights"] if l not in lights]
            if group:
                group.update(name=gname, lights=lights)
            else:
                self.groups.append({"name": gname, "lights": lights, "mult": 1.0, "color": None, "on": None})
                if self.mode != "group":
                    self.set_mode("group", True)
        self._rebuild(save=False)
        self.changed.emit()

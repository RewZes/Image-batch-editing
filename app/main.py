"""RenderBatch: batch post-processing for renders (denoise, mood / relight grade, upscale, sharpen)."""

import importlib.util
import json
import re
import os
import subprocess
import sys
import threading
import time
import traceback

import numpy as np
from PySide6.QtCore import QEvent, QItemSelectionModel, QMimeData, QFileSystemWatcher, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QDrag, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPalette, QPen,
                           QPixmap, QShortcut)
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

try:
    # PySide reads and tokenizes the source of every module imported after it, to support an optional
    # feature (snake_case / true_property) this app doesn't use; skipping that makes startup faster.
    import shibokensupport.feature as _sbk_feature
    _sbk_feature._mod_uses_pyside = lambda module: False
except Exception:
    pass
import engine as E
import i18n
from i18n import tr
from ui_common import SliderRow, field, prop, repolish, ClickSliderStyle, D, DENSITY, wrap_checkboxes, ElidedLabel, install_spin_arrows, set_spin_default
import themes
from viewer import ImageViewer, np_to_qimage
from lightmix_panel import LightMixPanel

APP_NAME = "RenderBatch"
VERSION = "3.9.0"
if os.environ.get("RENDERBATCH_HOME"):
    APP_DIR = os.environ["RENDERBATCH_HOME"]
elif getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(APP_DIR, "models")
LUT_DIR = os.path.join(APP_DIR, "luts")
THUMB_DIR = os.path.join(APP_DIR, "cache", "thumbs")
PRESET_DIR = os.path.join(APP_DIR, "presets")
LOOKS_TIP = ("Looks change only how the image looks: mood, LUT, clarity / grain, atmosphere.\n"
             "Denoise, upscale, sharpening and output stay as they are.\n"
             "Right-click: no look (back to the default look settings)")
PRESETS_TIP = ("Presets keep every setting (also denoise, upscale, sharpen and output)\n"
               "Right-click: every setting back to its default (like Reset all)")
LOOK_DIR = os.path.join(APP_DIR, "looks")                                    # your saved looks
BUILTIN_LOOK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "looks")
# A look = only how the image looks (mood, LUT, clarity / grain, atmosphere); a preset = every setting
LOOK_PRESET_KEYS = ("look_on", "exposure", "temperature", "tint", "contrast", "saturation", "lut_path", "lut_strength",
             "texture_on", "clarity", "grain_amount", "grain_size", "grain_color", "grain_type",
             "atmo_on", "tod_mode", "tod_amount", "tod_lights", "tod_sky", "tod_warmth",
             "fog_on", "fog_density", "fog_start", "fog_height", "fog_glow", "fog_color",
             "rays_on", "rays_intensity", "rays_length", "rays_threshold", "rays_source", "rays_angle",
             "rays_spread", "rays_point", "rays_color")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
NONE = "(none)"
FIRST_IMAGE = "(first image in folder)"
IMG_FILTER = "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.exr *.cxr)"
INTERACTIVE_RES = 1280      # preview size used while you drag a slider
LIVE_MAX_MP = 3.5           # max output megapixels for the live AI preview of the visible area
ROI_MAX_MP = 12.0           # ... and of a drawn processing region (it doesn't change while you zoom, so it may be bigger)

UI_DEFAULTS = {"theme": themes.DEFAULT_THEME, "gpu_limit": 75, "cpu_threads": max(1, (os.cpu_count() or 4) // 2),
               "preview_res": 2048, "auto_side": True, "view_mode": "split", "split": 0.5, "geometry": None,
               "collapsed": [], "easy_mode": "auto", "dn_mode": "manual", "up_mode": "manual",
               "card_order": [], "hidden_cards": [], "lm_mode": "individual", "lm_open": False, "lm_width": 380,
               "show_images": True, "show_cxr": True, "density": "compact", "language": "en",
               "scale_default_303": False}
CARD_TITLES = [("source", "Source"), ("look", "Mood / relight"), ("atmosphere", "Atmosphere"),
               ("match", "Match colors"), ("denoise", "Denoise"),
               ("upscale", "Resolution / upscale"), ("sharpen", "Sharpen"), ("texture", "Texture & grain")]
CARD_MIME = "application/x-renderbatch-card"
BITS_FOR = {"png": ["16", "8"], "tif": ["16", "8", "32"], "jpg": ["8"]}
LOOK_KEYS = {"exposure", "temperature", "tint", "contrast", "saturation", "lut_path", "lut_strength",
             "cm_strength", "ref_image", "look_on", "match_on"}
SWITCHES = ("look_on", "match_on", "denoise_on", "resize_on", "sharpen_on", "texture_on")
PROCEDURAL = "(built-in grain)"
GRAIN_TYPES = [("sensor", "Digital sensor"), ("film", "Film")]
LOOK_KEYS = LOOK_KEYS | {"texture_on", "clarity"}
SHARPEN_KEYS = {"sharpen_on", "sharpen_amount", "sharpen_radius", "sharpen_threshold",
                "grain_amount", "grain_size", "grain_color", "grain_type", "grain_model"}
SOURCE_KEYS = {"lightmix", "cxr_post", "cxr_denoised",   # Corona LightMix: changes the image itself
               "atmo_on", "tod_mode", "tod_amount", "tod_lights", "tod_sky", "tod_warmth"}   # ... and Time of day
LOOK_KEYS = LOOK_KEYS | {"fog_on", "fog_density", "fog_start", "fog_height", "fog_glow", "fog_color",
                         "rays_on", "rays_intensity", "rays_length", "rays_threshold", "rays_source", "rays_angle",
                         "rays_spread", "rays_point", "rays_color"}
EASY_KEYS = LOOK_KEYS | SHARPEN_KEYS | SOURCE_KEYS   # quick edits: follow the Automatic / Manual setting
DENOISED_MODES = [("file", "As saved (denoised when Corona's denoiser was on)"),
                  ("always", "Always the denoised passes"), ("never", "Never (raw passes)")]
DENOISE_KEYS = {"denoise_on", "denoise_strength", "denoise", "denoise_model"}
MOODS = {
    "Neutral": {},
    "Warm evening": dict(exposure=-0.15, temperature=35, tint=4, contrast=1.08, saturation=1.05),
    "Golden hour": dict(exposure=0.05, temperature=55, tint=8, contrast=1.05, saturation=1.12),
    "Cool daylight": dict(exposure=0.10, temperature=-25, tint=-3, contrast=1.03, saturation=0.97),
    "Soft matte": dict(exposure=0.08, contrast=0.86, saturation=0.9),
    "Moody": dict(exposure=-0.35, temperature=10, contrast=1.14, saturation=0.82),
}
RESIZE_MODES = ["scale_factor", "fit_width", "fit_height", "fit_inside", "exact"]


_ICON = None


def app_icon():
    """The app icon: data/icon/icon_<size>.png (all sizes, with transparency). A custom icon.png placed in
    the RenderBatch folder is used instead; the drawn fallback is only for a missing icon folder."""
    global _ICON
    if _ICON is not None:
        return _ICON
    icon = QIcon()
    custom = os.path.join(APP_DIR, "icon.png")
    if os.path.isfile(custom):
        icon.addFile(custom)
    else:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "icon")
        for size in (16, 24, 32, 48, 64, 96, 128, 256):
            p = os.path.join(d, f"icon_{size}.png")
            if os.path.isfile(p):
                icon.addFile(p, QSize(size, size))
    if icon.isNull():
        pm = QPixmap(256, 256)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#1c1e24"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(8, 8, 240, 240, 52, 52)
        p.end()
        icon = QIcon(pm)
    _ICON = icon
    return icon


# ============================================================================ signals from worker threads

class Bus(QObject):
    source = Signal(int, object, object)      # token, before uint8 (full res), (W, H)
    preview = Signal(int, object, bool)       # token, after uint8, is_full_res
    patch = Signal(int, object, object)       # token, uint8 live-AI crop, (x0, y0, x1, y1)
    region = Signal(int, object)              # token, stored AI-stage result for the visible area
    thumb = Signal(str, object)
    ref_thumb = Signal(str, object)
    task = Signal(str, str, float, int)       # id, text, fraction (-1 = busy), priority
    lights = Signal(int, object)              # token, (light names, count per name, number of .cxr, errors)
    imported = Signal(str, int)               # first copied file name, number of files
    task_end = Signal(str)
    log = Signal(str)
    batch = Signal(object)
    batch_done = Signal(object)
    detail = Signal(object)
    error = Signal(str, str, bool)            # message, field (may be ""), quiet (no popup)
    convert = Signal(object)                  # one progress event from the model converter
    depth_done = Signal(str)                  # depth model download finished ("" = ok, else the error)
    model_dl = Signal(object)                 # default-model download progress / result (dict)
    device = Signal(str)


# ============================================================================ small widgets

class WheelGuard(QObject):
    """Stops the mouse wheel from changing dropdowns, number boxes and sliders by accident.
    They only react to the wheel after you click into them; otherwise the panel scrolls."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)) \
                and not obj.hasFocus():
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QScrollArea):
                w = w.parentWidget()
            if w is not None:
                sb = w.verticalScrollBar()
                sb.setValue(sb.value() - int(ev.angleDelta().y() / 120 * sb.singleStep() * 3))
            return True
        if ev.type() == QEvent.Polish and isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)):
            obj.setFocusPolicy(Qt.StrongFocus)  # the wheel doesn't give them focus either
        return False


STATE_COLORS = {"done": "#3ddc97", "current": "#ff9a3c", "error": "#ff5d6c"}
STATE_RANK = {"done": 0, "current": 1, "error": 2, "": 3}


class FilmItem(QListWidgetItem):
    """Filmstrip entry: sorts finished images first, then the one in progress, then the rest."""

    def __lt__(self, other):
        a = (STATE_RANK.get(self.data(Qt.UserRole + 1) or "", 3), self.data(Qt.UserRole + 2) or 0)
        b = (STATE_RANK.get(other.data(Qt.UserRole + 1) or "", 3), other.data(Qt.UserRole + 2) or 0)
        return a < b

class Switch(QAbstractButton):
    """On/off toggle."""

    def __init__(self, checked=True, tip=""):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.setFixedSize(38, 22)

    def sizeHint(self):
        return QSize(38, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        on = self.isChecked()
        track = pal.highlight().color() if on else pal.mid().color()
        if not self.isEnabled():
            track.setAlpha(90)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        r = QRectF(1, 3, 36, 16)
        p.drawRoundedRect(r, 8, 8)
        p.setBrush(QColor("#ffffff"))
        x = 29 if on else 9
        p.drawEllipse(QPointF(x, 11), 7, 7)


class Card(QFrame):
    """Collapsible section card, optionally with an on/off switch and an inline error line."""

    def __init__(self, title, key, collapsed=False, switch_tip=None):
        super().__init__()
        self.setObjectName("Card")
        self.key = key
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*D("card_margins"))
        outer.setSpacing(D("card_spacing"))
        head = QHBoxLayout()
        self.btn = QPushButton()
        self.btn.setObjectName("CardHeader")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.toggle)
        self.btn.setToolTip("Click to fold · drag to move this section up or down")
        self.btn.installEventFilter(self)
        self._press = None
        self.title = title          # English; the button's text is translated when it's set
        head.addWidget(self.btn, 1)
        self.switch = None
        if switch_tip is not None:
            self.switch = Switch(True, switch_tip)
            self.switch.toggled.connect(self._switched)
            head.addWidget(self.switch)
        outer.addLayout(head)
        self.err = QLabel("")
        self.err.setObjectName("ErrorLabel")
        self.err.setWordWrap(True)
        self.err.hide()
        outer.addWidget(self.err)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 2, 0, 0)
        self.lay.setSpacing(D("card_spacing") + 1)
        outer.addWidget(self.body)
        self.set_collapsed(collapsed)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._press = ev.position().toPoint()
        elif t == QEvent.MouseMove and self._press is not None:
            if (ev.position().toPoint() - self._press).manhattanLength() > 8:
                self._press = None
                self.btn.setDown(False)
                drag = QDrag(self)
                md = QMimeData()
                md.setData(CARD_MIME, self.key.encode())
                drag.setMimeData(md)
                pm = self.btn.grab()
                drag.setPixmap(pm)
                drag.setHotSpot(QPoint(20, pm.height() // 2))
                self.setProperty("dragging", True)
                repolish(self)
                drag.exec(Qt.MoveAction)
                self.setProperty("dragging", False)
                repolish(self)
                return True
        elif t == QEvent.MouseButtonRelease:
            self._press = None
        return False

    def _switched(self, on):
        self.body.setEnabled(on)
        self.btn.setProperty("off", not on)
        repolish(self.btn)

    def set_collapsed(self, c):
        self.body.setVisible(not c)
        self.btn.setText(("▸  " if c else "▾  ") + self.title.replace("&", "&&"))

    def toggle(self):
        self.set_collapsed(self.body.isVisible())

    def show_error(self, msg):
        self.setProperty("error", bool(msg))
        repolish(self)
        self.err.setText("⚠  " + msg.replace("\\", "\\\u200b").replace("/", "/\u200b") if msg else "")
        self.err.setVisible(bool(msg))
        if msg:
            self.set_collapsed(False)

    @property
    def collapsed(self):
        return not self.body.isVisible()


class ResponsiveBar(QFrame):
    """Toolbar that shortens its button labels (and finally hides a few buttons) when the window is narrow,
    so the window never has to be wider than the screen."""

    def __init__(self):
        super().__init__()
        self.items = []     # (widget, [text per level] or None, hide_from_level or None)
        self.level = 0
        self.on_level = None

    def add(self, w, texts=None, hide_from=None):
        self.items.append((w, texts, hide_from))

    def apply(self, level):
        self.level = level
        for w, texts, hide_from in self.items:
            if texts:
                w.setText(texts[min(level, len(texts) - 1)])
                if bool(w.property("tiny")) != (level >= 3):
                    w.setProperty("tiny", level >= 3)
                    repolish(w)
            if hide_from is not None:
                w.setProperty("_rb_hidden", level >= hide_from)
                if getattr(w, "_rb_wanted", True):
                    w.setVisible(level < hide_from)
        if self.on_level:
            self.on_level(level)
        # make nested layouts (e.g. the Split / Side / Before / After group) re-measure right away
        parents = {w.parentWidget() for w, _, _ in self.items if w.parentWidget() is not self}
        for p in parents:
            if p is not None and p.layout() is not None:
                p.layout().invalidate()
                p.layout().activate()
                p.updateGeometry()
        self.layout().invalidate()

    def set_wanted(self, w, on):
        """Show/hide a widget for other reasons (e.g. the LightMix button) without fighting the levels."""
        w._rb_wanted = on
        hidden = w.property("_rb_hidden") or False
        w.setVisible(on and not hidden)

    def fit(self):
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            cw = self.compact_width()
            if cw != self.minimumWidth():
                self.setMinimumWidth(cw)
            for level in range(4):
                self.apply(level)
                if self.layout().sizeHint().width() <= self.width():
                    break
            else:
                self.apply(3)
        finally:
            self._fitting = False
        self.layout().invalidate()
        self.layout().setGeometry(self.contentsRect())

    def compact_width(self):
        cur = self.level
        self.apply(3)
        w = self.layout().sizeHint().width()
        self.apply(cur)
        return w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()


def dropped_files(mime):
    """Local image / .cxr paths in a drag-and-drop."""
    out = []
    for u in mime.urls() if mime.hasUrls() else []:
        p = u.toLocalFile()
        if p and os.path.isfile(p) and p.lower().endswith(E.IMG_EXTS):
            out.append(os.path.normpath(p))
    return out


MODEL_ROLE = Qt.UserRole + 41       # model name of a dropdown item
MISSING_ROLE = Qt.UserRole + 42     # True: a default model that isn't downloaded yet


def _download_icon(color):
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 3.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(16, 5, 16, 20)
    p.drawLine(9, 14, 16, 21)
    p.drawLine(23, 14, 16, 21)
    p.drawLine(7, 27, 25, 27)
    p.end()
    return QIcon(pm)


class ModelTagDelegate(QStyledItemDelegate):
    """Dropdown rows of the model lists: a small 'default' tag on the app's default models, and the download
    progress while one is downloading or converting."""

    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def _tag(self, index):
        name = index.data(MODEL_ROLE)
        if not name:
            return ""
        st = self.combo.status.get(name)
        if st:
            return st
        import model_store
        return tr("default") if model_store.is_default(name) else ""

    def paint(self, painter, option, index):
        tag = self._tag(index)
        super().paint(painter, option, index)
        if not tag:
            return
        painter.save()
        f = QFont(option.font)
        f.setPointSizeF(max(6.5, f.pointSizeF() * 0.8))
        painter.setFont(f)
        fm = QFontMetrics(f)
        w = fm.horizontalAdvance(tag) + 12
        h = fm.height() + 2
        r = option.rect
        box = QRectF(r.right() - w - 8, r.center().y() - h / 2 + 0.5, w, h)
        pal = option.palette
        col = pal.color(QPalette.HighlightedText if option.state & QStyle.State_Selected else QPalette.Text)
        col.setAlpha(150)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(col, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box, h / 2, h / 2)
        painter.drawText(box, Qt.AlignCenter, tag)
        painter.restore()

    def sizeHint(self, option, index):
        sz = super().sizeHint(option, index)
        tag = self._tag(index)
        if tag:
            fm = QFontMetrics(option.font)
            sz.setWidth(sz.width() + fm.horizontalAdvance(tag) + 30)
        return sz


class ModelCombo(QComboBox):
    """Model dropdown: installed models, plus the default models that aren't downloaded yet (with a download
    icon; picking one starts the download). The item text is always the plain model name."""
    wanted = Signal(str)

    def __init__(self):
        super().__init__()
        self.status = {}                       # model name -> 'downloading 40%' / 'converting…'
        self.setItemDelegate(ModelTagDelegate(self))
        self.view().pressed.connect(self._pressed)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)

    def set_models(self, installed, missing_defaults, keep):
        self.blockSignals(True)
        self.clear()
        self.addItem(NONE)
        for n in installed:
            self.addItem(n)
            self.setItemData(self.count() - 1, n, MODEL_ROLE)
        icon = _download_icon(self.palette().color(QPalette.Text).name())
        for m in missing_defaults:
            self.addItem(icon, m["name"])
            i = self.count() - 1
            self.setItemData(i, m["name"], MODEL_ROLE)
            self.setItemData(i, True, MISSING_ROLE)
            self.setItemData(i, tr("Default model, not downloaded yet: click to download it from {0}").format(
                m["page"]) + "\n" + tr(m["info"]) + "\n" + tr("License: {0}").format(m["license"]), Qt.ToolTipRole)
            it = self.model().item(i)
            if it is not None:
                it.setFlags(Qt.ItemIsEnabled)          # not selectable: a click downloads instead
        idx = self.findData(keep, MODEL_ROLE) if keep else -1
        if idx >= 0 and not self.itemData(idx, MISSING_ROLE):
            self.setCurrentIndex(idx)
        else:
            self.setCurrentIndex(0)
        self.blockSignals(False)

    def set_status(self, name, text):
        if text:
            self.status[name] = text
        else:
            self.status.pop(name, None)
        self.view().viewport().update()

    def _pressed(self, index):
        if index.data(MISSING_ROLE):
            name = index.data(MODEL_ROLE)
            QTimer.singleShot(0, self.hidePopup)
            self.wanted.emit(name)


class FileDrop(QObject):
    """Makes any widget accept dropped image / .cxr files and hands the paths to a callback."""

    def __init__(self, widget, callback):
        super().__init__(widget)
        self.cb = callback
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.DragEnter, QEvent.DragMove) and dropped_files(ev.mimeData()):
            ev.acceptProposedAction()
            if t == QEvent.DragEnter:
                obj.setProperty("drop", True)
                repolish(obj)
            return True
        if t == QEvent.DragLeave:
            obj.setProperty("drop", False)
            repolish(obj)
        if t == QEvent.Drop:
            obj.setProperty("drop", False)
            repolish(obj)
            files = dropped_files(ev.mimeData())
            if files:
                ev.acceptProposedAction()
                QTimer.singleShot(0, lambda f=files: self.cb(f))
                return True
        return False


class FilmStrip(QListWidget):
    """Thumbnails: drag to reorder (several at once with Ctrl / Shift), drop files to import them."""
    reordered = Signal(list)
    filesDropped = Signal(list)
    stepKey = Signal(int)       # Up / Down: previous / next look (the strip only scrolls sideways)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Up, Qt.Key_Down) and not e.modifiers():
            self.stepKey.emit(-1 if e.key() == Qt.Key_Up else 1)
            return
        super().keyPressEvent(e)

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        # one row of thumbnails: it never scrolls up / down
        self.verticalScrollBar().valueChanged.connect(lambda v: v and self.verticalScrollBar().setValue(0))
        self.drop_x = None

    def wheelEvent(self, e):
        """The mouse wheel (or a touchpad swipe either way) scrolls the strip left / right."""
        d = e.pixelDelta()
        if not d.isNull():
            step = d.x() if abs(d.x()) > abs(d.y()) else d.y()
        else:
            a = e.angleDelta()
            step = (a.x() if abs(a.x()) > abs(a.y()) else a.y()) * 1.2   # one notch ≈ 144 px ≈ one thumbnail
        bar = self.horizontalScrollBar()
        bar.setValue(int(bar.value() - step))
        e.accept()

    def _row_at(self, pos):
        it = self.itemAt(pos)
        if it is None:
            best, row = None, self.count()
            for i in range(self.count()):
                r = self.visualItemRect(self.item(i))
                if pos.x() < r.center().x():
                    row = i
                    break
            return row
        r = self.visualItemRect(it)
        return self.row(it) + (1 if pos.x() > r.center().x() else 0)

    def dragEnterEvent(self, e):
        if e.source() is self or dropped_files(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is self or dropped_files(e.mimeData()):
            e.acceptProposedAction()
            row = self._row_at(e.position().toPoint())
            if row < self.count():
                self.drop_x = self.visualItemRect(self.item(row)).left() - 2
            elif self.count():
                self.drop_x = self.visualItemRect(self.item(self.count() - 1)).right() + 2
            self.viewport().update()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.drop_x = None
        self.viewport().update()

    def dropEvent(self, e):
        self.drop_x = None
        self.viewport().update()
        if e.source() is not self:
            files = dropped_files(e.mimeData())
            if files:
                e.acceptProposedAction()
                QTimer.singleShot(0, lambda f=files: self.filesDropped.emit(f))
            return
        self.move_selected(self._row_at(e.position().toPoint()))
        e.setDropAction(Qt.CopyAction)   # the items were moved here already; stop Qt from removing them
        e.accept()

    def move_selected(self, row):
        """Moves the selected thumbnails (keeping their order) to position 'row'."""
        sel = sorted(self.selectedItems(), key=self.row)
        if not sel:
            return
        cur = self.currentItem()
        before = sum(1 for it in sel if self.row(it) < row)
        taken = [self.takeItem(self.row(it)) for it in sel]
        row -= before
        self.blockSignals(True)
        for k, it in enumerate(taken):
            self.insertItem(row + k, it)
            it.setSelected(True)
        if cur is not None:
            self.setCurrentItem(cur, QItemSelectionModel.NoUpdate)
        self.blockSignals(False)
        self.reordered.emit([self.item(i).data(Qt.UserRole) for i in range(self.count())])

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.drop_x is not None:
            p = QPainter(self.viewport())
            p.fillRect(QRectF(self.drop_x - 1.5, 4, 3, self.viewport().height() - 8), self.palette().highlight())
            p.end()


class CardColumn(QWidget):
    """The sidebar's list of cards; cards can be dragged up and down (the order is remembered)."""
    reordered = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.cards = []
        self.drop_y = None

    def _target(self, y):
        vis = [c for c in self.cards if c.isVisible()]
        for i, c in enumerate(vis):
            if y < c.geometry().center().y():
                return i, c.geometry().top() - 5
        return len(vis), (vis[-1].geometry().bottom() + 5 if vis else 0)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(CARD_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(CARD_MIME):
            e.acceptProposedAction()
            self.drop_y = self._target(e.position().y())[1]
            self.update()

    def dragLeaveEvent(self, e):
        self.drop_y = None
        self.update()

    def dropEvent(self, e):
        key = bytes(e.mimeData().data(CARD_MIME)).decode()
        idx, _ = self._target(e.position().y())
        self.drop_y = None
        self.update()
        vis = [c for c in self.cards if c.isVisible()]
        moving = next((c for c in self.cards if c.key == key), None)
        if moving is None:
            return
        anchor = vis[idx] if idx < len(vis) else None
        order = [c for c in self.cards if c is not moving]
        pos = order.index(anchor) if anchor is not None and anchor is not moving else len(order)
        if anchor is moving:
            return
        order.insert(pos, moving)
        e.acceptProposedAction()
        self.reordered.emit([c.key for c in order])

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.drop_y is not None:
            p = QPainter(self)
            p.setPen(Qt.NoPen)
            p.setBrush(self.palette().highlight())
            p.drawRoundedRect(QRectF(12, self.drop_y - 2, self.width() - 24, 4), 2, 2)
            p.end()


class LutCombo(QComboBox):
    """Refreshes its list of LUTs every time it opens (new .cube files in the luts folder show up)."""
    opening = Signal()

    def showPopup(self):
        self.opening.emit()
        super().showPopup()


class LutRow(QWidget):
    """The LUT field. With two or more .cube files in the app's luts folder it's a dropdown of them
    (plus Browse for any other file); otherwise a path box with Browse, as before. Same API as PathRow."""
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("LUT (.cube), optional")
        self.combo = LutCombo()
        self.combo.setMinimumWidth(0)
        self.combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.combo.setToolTip("LUTs in the app's luts folder. Put .cube files there to see them here.")
        b = QPushButton("Browse")
        b.clicked.connect(self.browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.combo, 1)
        lay.addWidget(b)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.text()))
        self.combo.currentIndexChanged.connect(lambda _i: None if self._busy else self.changed.emit(self.text()))
        self.combo.opening.connect(lambda: self.rescan(keep=True))
        self._busy = False
        self._path = ""
        self.rescan()

    @staticmethod
    def folder_luts():
        out = []
        for root, _dirs, files in os.walk(LUT_DIR):
            for f in files:
                if f.lower().endswith(".cube"):
                    out.append(os.path.join(root, f))
        return sorted(out, key=lambda p: os.path.relpath(p, LUT_DIR).lower())

    def rescan(self, keep=True):
        cur = self.text() if keep else self._path
        luts = self.folder_luts()
        use_combo = len(luts) >= 2
        self._busy = True
        self.combo.clear()
        if use_combo:
            self.combo.addItem("(no LUT)", "")
            for p in luts:
                self.combo.addItem(os.path.splitext(os.path.relpath(p, LUT_DIR))[0].replace(os.sep, " / "), p)
        self.combo.setVisible(use_combo)
        self.edit.setVisible(not use_combo)
        self._busy = False
        self.set(cur, emit=False)

    def _label_for(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        if os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(BUILTIN_LOOK_DIR):
            return tr("Built-in look: {0}").replace("{0}", name.replace("_", " "))
        return tr("Other: {0}").replace("{0}", name)

    def text(self):
        if not self.combo.isHidden():
            return self.combo.currentData() or ""
        return self.edit.text().strip().strip('"')

    def set(self, t, emit=True):
        t = t or ""
        self._path = t
        self.edit.setText(t)
        if not self.combo.isHidden():
            self._busy = True
            i = -1
            for k in range(self.combo.count()):
                d = self.combo.itemData(k)
                if d == t or (d and t and os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(t))):
                    i = k
                    break
            if i < 0 and t:
                self.combo.insertItem(1, self._label_for(t), t)   # a file from elsewhere (Browse, a look)
                i = 1
            self.combo.setCurrentIndex(max(0, i))
            self._busy = False
        if emit:
            self.changed.emit(self.text())

    def browse(self):
        start = self.text() or LUT_DIR
        d, _ = QFileDialog.getOpenFileName(self, "Choose file", start, "3D LUT (*.cube)")
        if d:
            self.set(os.path.normpath(d))


class PathRow(QWidget):
    changed = Signal(str)

    def __init__(self, placeholder, pick_dir=True, filt="", label=None):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        if label:
            t = QLabel(label)
            t.setWordWrap(True)
            t.setMinimumWidth(0)
            t.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            t.setObjectName("FieldLabel")
            t.setStyleSheet("font-weight: 600;")
            outer.addWidget(t)
        lay = QHBoxLayout()
        lay.setSpacing(6)
        outer.addLayout(lay)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.pick_dir, self.filt = pick_dir, filt
        b = QPushButton("Browse")
        b.clicked.connect(self.browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(b)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.text()))

    def text(self):
        return self.edit.text().strip().strip('"')

    def set(self, t, emit=True):
        self.edit.setText(t or "")
        if emit:
            self.changed.emit(self.text())

    def browse(self):
        start = self.text() or (LUT_DIR if not self.pick_dir else "")
        if self.pick_dir:
            d = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        else:
            d, _ = QFileDialog.getOpenFileName(self, "Choose file", start, self.filt)
        if d:
            self.set(os.path.normpath(d))


class ActivityBar(QFrame):
    """Bottom status strip: shows the most important running task with a progress bar."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Activity")
        self.setFixedHeight(D("activity_h"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 3, 10, 3)
        lay.setSpacing(10)
        self.dot = QLabel("●")
        self.text = ElidedLabel("Ready")
        self.more = QLabel("")
        self.more.setObjectName("Subtle")
        self.action = QPushButton("")
        self.action.hide()
        self.bar = QProgressBar()
        self.bar.setFixedWidth(260)
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.pct = QLabel("")
        self.pct.setObjectName("Subtle")
        self.pct.setMinimumWidth(40)
        self.stop_btn = prop(QPushButton("✕  Cancel"), "flat")
        self.stop_btn.setToolTip("Stop everything that's running now: batch, Detail check, preview renders,\n"
                                 "loading of .cxr files and model conversion")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.hide()
        self.on_cancel = None
        self.stop_btn.clicked.connect(lambda: self.on_cancel and self.on_cancel())
        self.log_btn = prop(QPushButton("Activity log"), "flat")
        lay.addWidget(self.dot)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.action)
        lay.addWidget(self.more)
        lay.addWidget(self.bar)
        lay.addWidget(self.pct)
        lay.addWidget(self.stop_btn)
        lay.addWidget(self.log_btn)
        self.tasks = {}
        self._cb = None
        self.colors = themes.THEMES[themes.DEFAULT_THEME]
        self.action.clicked.connect(self._do_action)
        self._refresh()

    def set_colors(self, c):
        self.colors = c
        self._refresh()

    def set(self, tid, text, frac, prio):
        self.tasks[tid] = (text, frac, prio, time.time())
        self._refresh()

    def end(self, tid):
        self.tasks.pop(tid, None)
        self._refresh()

    def flash(self, text, action=None, callback=None, seconds=6):
        self.tasks["_msg"] = (text, None, -1, time.time())
        self._cb = callback
        self.action.setText(action or "")
        self.action.setVisible(bool(action))
        self._refresh()
        stamp = self.tasks["_msg"][3]

        def done():
            if self.tasks.get("_msg", (0, 0, 0, None))[3] == stamp:
                self.end("_msg")
                self.action.hide()
        QTimer.singleShot(seconds * 1000, done)

    def _do_action(self):
        self.action.hide()
        self.end("_msg")
        if self._cb:
            self._cb()

    CANCELLABLE = ("job", "live", "load", "preview", "convert", "download", "models")

    def _refresh(self):
        self.stop_btn.setVisible(any(t in self.tasks for t in self.CANCELLABLE))
        if not self.tasks:
            self.text.setText("Ready")
            self.more.setText("")
            self.bar.hide()
            self.pct.setText("")
            self.dot.setStyleSheet(f"color: {self.colors['ok']};")
            return
        tid, (text, frac, prio, _) = max(self.tasks.items(), key=lambda kv: (kv[1][2], kv[1][3]))
        self.text.setText(text)
        others = len([k for k in self.tasks if k not in (tid, "_msg")])
        self.more.setText(f"+{others} more" if others > 0 else "")
        if frac is None:
            self.bar.hide()
            self.pct.setText("")
            warn = text.startswith("⚠")
            self.dot.setStyleSheet(f"color: {self.colors['danger' if warn else 'ok']};")
            return
        self.dot.setStyleSheet(f"color: {self.colors['accent']};")
        self.bar.show()
        if frac < 0:
            self.bar.setRange(0, 0)
            self.pct.setText("")
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
            self.pct.setText(f"{frac * 100:.0f}%")


# ============================================================================ background workers

class PreviewWorker:
    """Grades the preview in the background; the newest request always wins, so dragging a slider
    never blocks the window. Stage 1: small image, immediately (real-time while dragging).
    Stage 2: preview size, once you pause. Stage 3: full resolution, only when zoomed in past the preview."""

    def __init__(self, bus):
        self.bus = bus
        self.cond = threading.Condition()
        self.req = None
        self.token = 0
        self.key = None
        self.src = self.src_stats = None
        self.graders = {}
        self.ref = (None, None)
        self.lut = (None, None)
        self.sig = ""
        self.src_key = None
        self.before_u8 = None
        self.recent = {}
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def cancel(self):
        """Stops the current load / render (the Cancel button); the next request starts fresh."""
        with self.cond:
            self._stop = True
            self.token += 1
            self.req = None

    def _chk(self):
        if self._stop:
            raise E.Cancelled()

    def request(self, settings, name, preview_res, need_full, force_source=False, neighbors=()):
        with self.cond:
            self.neighbors = list(neighbors)
            self.token += 1
            self._stop = False
            force = force_source or (self.req is not None and self.req[5])  # keep a pending force
            self.req = (self.token, dict(settings), name, preview_res, need_full, force)
            self.cond.notify()
        return self.token

    def _newer(self, token):
        return self.token != token

    def _wait_quiet(self, token, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self._newer(token):
                return False
            time.sleep(0.02)
        return not self._newer(token)

    def _loop(self):
        while True:
            with self.cond:
                while self.req is None:
                    if getattr(self, "neighbors", None) and getattr(self, "_last_s", None) is not None:
                        break                       # idle: read the next / previous image ahead of time
                    self.cond.wait()
                if self.req is None:
                    nb = self.neighbors.pop(0)
                    s_last = self._last_s
                else:
                    nb = None
                    token, s, name, res, need_full, force = self.req
                    self.req = None
            if nb is not None:
                self._prefetch(nb, s_last)
                continue
            self._last_s = s
            try:
                self._work(token, s, name, res, need_full, force)
            except E.Cancelled:
                self.bus.task_end.emit("preview")
                self.bus.task_end.emit("load")
            except E.SettingError as e:
                self.bus.task_end.emit("preview")
                self.bus.task_end.emit("load")
                self.bus.error.emit(str(e), e.field, True)
            except Exception as e:
                self.bus.task_end.emit("preview")
                self.bus.task_end.emit("load")
                self.bus.error.emit(f"Preview failed: {e}", "", True)

    def _remember(self, path, sig, entry=None):
        """Keeps the last few opened images (as shown) so switching back is instant; the images next to
        the current one are read ahead of time into the same memory (at most an eighth of the RAM)."""
        try:
            key = (path, sig, os.path.getmtime(path))
        except OSError:
            return
        entry = entry or (self.before_u8, self.src, self.src_stats, getattr(self, "orig", None))
        self.recent.pop(key, None)
        self.recent[key] = entry
        for k in [k for k in self.recent if k[0] == path and k != key]:
            self.recent.pop(k)            # only the latest lighting of each file

        def size(e):
            return sum(a.nbytes for a in e if isinstance(a, np.ndarray))
        import corona
        budget = corona._ram_budget() // 2
        while len(self.recent) > 6 or (len(self.recent) > 1 and sum(size(e) for e in self.recent.values()) > budget):
            self.recent.pop(next(iter(self.recent)))

    def _prefetch(self, path, s):
        """Reads an image the user is likely to open next (no screen updates); stops as soon as a real
        request arrives."""
        import atmosphere
        try:
            if not os.path.isfile(path):
                return
            sig = E.source_signature(s, path)
            if (path, sig, os.path.getmtime(path)) in self.recent or self.key == path:
                return

            def stop():
                if self.req is not None:
                    raise E.Cancelled()
            if path.lower().endswith(".cxr"):
                before, _ = E.load_image(path, s, file_mix_only=True, cancel=stop)
                before_u8 = (np.clip(before, 0, 1) * 255 + 0.5).astype(np.uint8)
                same = not __import__("corona").effective(s) and not atmosphere.tod_active(s)
                rgb = before if same and s.get("cxr_post", True) else E.load_image(path, s, cancel=stop)[0]
                orig = None
            else:
                orig, _ = E.load_image(path)
                stop()
                if atmosphere.tod_active(s):
                    before_u8 = (np.clip(orig, 0, 1) * 255 + 0.5).astype(np.uint8)
                    rgb = atmosphere.tod_image(orig, s)
                else:
                    before_u8, rgb = None, orig
            stop()
            self._remember(path, sig, (before_u8, rgb, E.lab_stats(rgb), orig))
        except E.Cancelled:
            pass
        except Exception:
            pass

    def _ref(self, s):
        if not (s["match_on"] and s["cm_strength"] > 0):
            return None
        ref_path = s.get("_ref_path", "")
        if ref_path and self.ref[0] != ref_path:
            try:
                self.ref = (ref_path, E.lab_stats(E.load_image(ref_path, s, file_mix_only=True)[0]))
            except Exception:
                raise E.SettingError("ref_image", f"Reference image can't be read: {os.path.basename(ref_path)}")
        return self.ref[1] if ref_path else None

    def _lut(self, s):
        if not (s["look_on"] and s["lut_path"]):
            return None
        if self.lut[0] != s["lut_path"]:
            if not os.path.isfile(s["lut_path"]):
                raise E.SettingError("lut_path", "The LUT file doesn't exist. Choose another .cube file or clear the field.")
            try:
                self.lut = (s["lut_path"], E.load_cube(s["lut_path"]))
            except Exception as e:
                raise E.SettingError("lut_path", f"This LUT can't be used: {e}")
        return self.lut[1]

    def grader(self, size):
        g = self.graders.get(size)
        if g is None:
            h, w = self.src.shape[:2]
            k = min(1.0, size / max(h, w))
            img = self.src if k >= 1 else E.resize(self.src, max(1, round(w * k)), max(1, round(h * k)))
            g = self.graders[size] = E.FastGrader(img, self.src_stats)
        return g

    def _relit(self, s):
        """An ordinary image with Time of day applied; 'before' stays the original."""
        import atmosphere
        if atmosphere.tod_active(s):
            self.before_u8 = (np.clip(self.orig, 0, 1) * 255 + 0.5).astype(np.uint8)
            return atmosphere.tod_image(self.orig, s)
        self.before_u8 = None
        return self.orig

    def _atmo(self, s, path, name):
        """Fog / light rays maps for the current source (the first time per image it estimates depth)."""
        import atmosphere
        if not atmosphere.fx_active(s) or self.src is None:
            return None
        busy = not atmosphere.maps_cached(self.src_key, s, self.src.shape)
        if busy:
            self.bus.task.emit("load", "Fog / light rays: estimating depth", -1, 2)
        notes = []
        try:
            return atmosphere.get_maps(self.src, s, self.src_key, MODELS_DIR, path, notes)
        except Exception as e:
            self.bus.log.emit(f"{name}: fog / light rays failed: {e}")
            return None
        finally:
            if busy:
                self.bus.task_end.emit("load")
                for n in notes:
                    self.bus.log.emit(f"{name}: {n}")

    def _work(self, token, s, name, res, need_full, force):
        s = E.with_defaults(s)
        path = os.path.join(s["input_dir"], name)
        is_cxr = path.lower().endswith(".cxr")
        import corona
        import atmosphere
        sig = E.source_signature(s, path)
        recent = self.recent.get((path, sig, os.path.getmtime(path) if os.path.exists(path) else 0))
        if self.key != path and recent is not None:
            # opened before (or read ahead) with the same settings: no need to read or mix it again
            self.before_u8, self.src, self.src_stats, self.orig = recent
            self.graders = {}
            self.key, self.sig, self.src_key = path, sig, (path, sig)
            force = True
        elif self.key != path:
            self.bus.task.emit("load", f"Loading {name}", -1, 2)
            try:
                if is_cxr:
                    notes = []
                    prog = lambda f: self.bus.task.emit("load", f"Reading light passes of {name}", f, 2)
                    before, _ = E.load_image(path, s, file_mix_only=True, notes=notes, progress=prog, cancel=self._chk)
                    for n in dict.fromkeys(notes):
                        self.bus.log.emit(f"{name}: {n}")
                    self.before_u8 = (np.clip(before, 0, 1) * 255 + 0.5).astype(np.uint8)
                    same = not corona.effective(s) and not atmosphere.tod_active(s)
                    rgb = before if same and s.get("cxr_post", True) else E.load_image(path, s, cancel=self._chk)[0]
                    self.orig = None
                else:
                    self.orig, _ = E.load_image(path)
                    rgb = self._relit(s)
            except E.Cancelled:
                raise
            except Exception as e:
                self.bus.task_end.emit("load")
                raise E.SettingError("input_dir", f"Can't open {name}: {e}")
            self.src, self.src_stats = rgb, E.lab_stats(rgb)
            self.graders = {}
            self.key, self.sig, self.src_key = path, sig, (path, sig)
            force = True
            self.bus.task_end.emit("load")
            self._remember(path, sig)
        elif not is_cxr and sig != self.sig and getattr(self, "orig", None) is not None:
            # Time of day changed on an ordinary image: quick small version first, then full resolution
            h0, w0 = self.orig.shape[:2]
            k = min(1.0, INTERACTIVE_RES / max(h0, w0))
            small = atmosphere.tod_image(E.resize(self.orig, max(1, round(w0 * k)), max(1, round(h0 * k))), s)
            g = E.FastGrader(small, E.lab_stats(small))
            self.bus.preview.emit(token, g.grade(s, self._lut(s), self._ref(s)), False)
            if not self._wait_quiet(token, 0.25):
                return
            self.bus.task.emit("load", "Relighting at full resolution", -1, 2)
            try:
                rgb = self._relit(s)
            finally:
                self.bus.task_end.emit("load")
            if self._newer(token):
                return
            self.src, self.src_stats = rgb, E.lab_stats(rgb)
            self.graders = {}
            self.sig, self.src_key = sig, (path, sig)
            force = True
        elif is_cxr and sig != self.sig:
            # LightMix changed: a quick small render first (real-time while dragging), then full resolution
            small, _ = E.load_image(path, s, max_side=INTERACTIVE_RES)
            g = E.FastGrader(small, E.lab_stats(small))
            self.bus.preview.emit(token, g.grade(s, self._lut(s), self._ref(s)), False)
            if not self._wait_quiet(token, 0.12):
                return
            self.bus.task.emit("load", "Mixing lights at full resolution", -1, 2)
            try:
                rgb, _ = E.load_image(path, s, cancel=self._chk)
            finally:
                self.bus.task_end.emit("load")
            if self._newer(token):
                return
            self.src, self.src_stats = rgb, E.lab_stats(rgb)
            self.graders = {}
            self.sig, self.src_key = sig, (path, sig)
            self._remember(path, sig)
        if force:
            h, w = self.src.shape[:2]
            before = self.before_u8 if getattr(self, "before_u8", None) is not None else \
                (np.clip(self.src, 0, 1) * 255 + 0.5).astype(np.uint8)
            self.bus.source.emit(token, before, (w, h))
        if self._newer(token):
            return

        ref_stats = self._ref(s)
        lut = self._lut(s)
        atmo = self._atmo(s, path, name)
        if self._newer(token):
            return

        H, W = self.src.shape[:2]
        sizes = [INTERACTIVE_RES]
        if res > INTERACTIVE_RES and max(H, W) > INTERACTIVE_RES:
            sizes.append(res)
        for i, size in enumerate(sizes):
            if i and not self._wait_quiet(token, 0.15):
                return
            g = self.grader(size)
            out = g.grade(s, lut, ref_stats, atmo)
            if self._newer(token):
                return
            self.bus.preview.emit(token, out, g.rgb is self.src)
        if not need_full or max(H, W) <= sizes[-1]:
            return
        if not self._wait_quiet(token, 0.3):
            return
        self.bus.task.emit("preview", "Rendering full-resolution preview", -1, 1)
        out = self.grader(max(H, W)).grade(s, lut, ref_stats, atmo)
        self.bus.task_end.emit("preview")
        if not self._newer(token):
            self.bus.preview.emit(token, out, True)


# ============================================================================ settings dialog

class SettingsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Settings")
        self.setMinimumWidth(640)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("SidebarInner")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)
        sc.setWidget(content)
        outer.addWidget(sc, 1)

        def header(t):
            l = QLabel(t)
            l.setStyleSheet("font-weight: 600; font-size: 11pt;")
            return l

        lay.addWidget(header("Appearance"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.theme_btns = {}
        for i, (name, t) in enumerate(themes.THEMES.items()):
            b = QPushButton(f"  {name}")
            b.setObjectName("ThemeCard")
            b.setCheckable(True)
            b.setFixedHeight(52)
            b.setStyleSheet(
                f"QPushButton#ThemeCard {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {t['bg']}, "
                f"stop:0.62 {t['card']}, stop:0.63 {t['accent']}, stop:1 {t['accent']}); color: {t['text']}; "
                f"border: 2px solid {t['border']}; font-weight: 600; }}"
                f"QPushButton#ThemeCard:checked {{ border: 2px solid {t['accent']}; }}"
                f"QPushButton#ThemeCard:hover {{ border: 2px solid {t['muted']}; }}")
            b.clicked.connect(lambda _=False, n=name: self._pick_theme(n))
            grid.addWidget(b, i // 3, i % 3)
            self.theme_btns[name] = b
        lay.addLayout(grid)
        self._mark_theme(win.ui["theme"])

        self.lang = QComboBox()
        for code, name in i18n.LANGUAGES:
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(win.ui.get("language", "en"))))
        self.lang.currentIndexChanged.connect(self._lang_changed)
        lay.addWidget(field("Language", self.lang))
        self.dens = QComboBox()
        self.dens.addItems(["Compact", "Comfortable (larger)"])
        self.dens.setCurrentIndex(0 if win.ui.get("density", "compact") == "compact" else 1)
        self.dens.currentIndexChanged.connect(lambda i: win.set_ui("density", "compact" if i == 0 else "comfortable"))
        lay.addWidget(field("Interface size", self.dens))
        dn = QLabel("Text and buttons change right away; spacing fully after restarting the app.")
        dn.setObjectName("Subtle")
        dn.setWordWrap(True)
        lay.addWidget(dn)

        lay.addWidget(header("Output"))
        self.fmt = QComboBox()
        self.fmt.addItems(["png", "tif", "jpg"])
        self.fmt.setCurrentText(win.fmt.currentText())
        self.bits = QComboBox()
        self.bits.setToolTip("Bits per channel. 16 keeps the most tonal detail in PNG;\n"
                             "32 (TIFF only) saves floating point; JPG is always 8")
        fb = QWidget()
        fbl = QHBoxLayout(fb)
        fbl.setContentsMargins(0, 0, 0, 0)
        fbl.addWidget(self.fmt, 1)
        fbl.addWidget(self.bits, 1)
        lay.addWidget(field("Format · bits", fb))
        self._sync_bits()
        self.fmt.currentTextChanged.connect(self._fmt_changed)
        self.bits.currentTextChanged.connect(lambda t: t and win.bits.setCurrentText(t))
        self.suffix = QLineEdit(win.suffix.text())
        self.suffix.setPlaceholderText("e.g. _final")
        self.suffix.textChanged.connect(win.suffix.setText)
        lay.addWidget(field("Name suffix", self.suffix))
        if win.errors.get("suffix"):
            self.suffix.setProperty("error", True)
            self.suffix.setToolTip(win.errors["suffix"])
        on = QLabel("The output folder is set in the Source section. \"Skip images already processed\" "
                    "is in the Batch panel.")
        on.setObjectName("Subtle")
        on.setWordWrap(True)
        lay.addWidget(on)

        lay.addWidget(header("Sidebar"))
        grid2 = QGridLayout()
        grid2.setSpacing(6)
        hidden = set(win.ui.get("hidden_cards", []))
        for i, (key, title) in enumerate(CARD_TITLES):
            cb = QCheckBox(title.replace("&", "&&"))
            cb.setChecked(key not in hidden)
            cb.toggled.connect(lambda v, k=key: win.set_card_visible(k, v))
            grid2.addWidget(cb, i // 3, i % 3)
        lay.addLayout(grid2)
        sn = QLabel("Untick a section to hide it (its settings keep working as they are; use a section's switch "
                    "to turn a step off). Drag a section's title in the sidebar to move it up or down. "
                    "The order and visibility are kept, also after Reset all.")
        sn.setObjectName("Subtle")
        sn.setWordWrap(True)
        lay.addWidget(sn)

        lay.addWidget(header("Performance"))
        self.gpu = SliderRow("GPU usage limit", 30, 100, UI_DEFAULTS["gpu_limit"], 0, "%",
                             "Pauses between tiles so the GPU is busy at most this share of the time.\n"
                             "Lower = cooler, quieter, and the PC stays responsive during a batch.")
        self.gpu.set(win.ui["gpu_limit"], emit=False)
        self.gpu.changed.connect(lambda v: win.set_ui("gpu_limit", int(v)))
        lay.addWidget(self.gpu)
        self.cpu = QSpinBox()
        self.cpu.setRange(1, os.cpu_count() or 8)
        self.cpu.setValue(win.ui["cpu_threads"])
        set_spin_default(self.cpu, UI_DEFAULTS["cpu_threads"])
        self.cpu.valueChanged.connect(lambda v: win.set_ui("cpu_threads", v))
        lay.addWidget(field("CPU threads", self.cpu, False))
        self.res = QComboBox()
        self.res.addItems(["Fast (1280 px)", "Balanced (2048 px)", "Sharp (3072 px)"])
        self.res.setCurrentIndex({1280: 0, 2048: 1, 3072: 2}.get(win.ui["preview_res"], 1))
        self.res.currentIndexChanged.connect(lambda i: win.set_ui("preview_res", (1280, 2048, 3072)[i]))
        lay.addWidget(field("Preview quality", self.res))
        note = QLabel("While you drag a slider the preview updates at 1280 px for speed, then sharpens to this size. "
                      "Full resolution is rendered automatically when you zoom in further.")
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addWidget(header("Viewer"))
        self.auto = QCheckBox("Show before and after side by side when zooming in (split view)")
        self.auto.setChecked(win.ui["auto_side"])
        self.auto.toggled.connect(lambda v: win.set_ui("auto_side", v))
        lay.addWidget(self.auto)
        self.easy = QComboBox()
        self.easy.addItems(["Automatic: the preview follows every change",
                            "Manual: changes wait for Update preview (F5)"])
        self.easy.setCurrentIndex(0 if win.ui["easy_mode"] == "auto" else 1)
        self.easy.currentIndexChanged.connect(lambda i: win.set_ui("easy_mode", "auto" if i == 0 else "manual"))
        lay.addWidget(field("Mood, color match\nand sharpen", self.easy))
        n2 = QLabel("Denoise and Upscale have their own Interactive / Manual choice inside their cards, "
                    "because they use the graphics card and take longer.")
        n2.setObjectName("Subtle")
        n2.setWordWrap(True)
        lay.addWidget(n2)
        keys = QLabel("Scroll: zoom at cursor · Drag: pan · Double-click: fit / 100% · F: fit · 1: 100% · "
                      "← →: previous / next image")
        keys.setObjectName("Subtle")
        keys.setWordWrap(True)
        lay.addWidget(keys)

        lay.addWidget(header("Updates"))
        import updater
        info = updater.install_info(APP_DIR)
        where = QLabel(f"{APP_NAME} {VERSION} · "
                       + tr("Installed (Start menu, uninstall in Windows Settings › Apps)" if info["mode"] == "installed"
                            else "Portable (everything stays in this folder)"))
        where.setWordWrap(True)
        lay.addWidget(where)
        ub = QPushButton("Install update…")
        ub.setToolTip("Pick a RenderBatch update .zip; the app installs it and restarts")
        ub.clicked.connect(win._pick_update)
        ubw = QWidget()
        ubl = QHBoxLayout(ubw)
        ubl.setContentsMargins(0, 0, 0, 0)
        ubl.addWidget(ub)
        ubl.addStretch()
        lay.addWidget(ubw)
        un = QLabel("You can also drop the update .zip onto the window. When a newer update is in your Downloads "
                    "or Desktop folder, RenderBatch offers to install it when it starts. Updates installed "
                    "this way don't get Windows' \"downloaded from the internet\" warning.")
        un.setObjectName("Subtle")
        un.setWordWrap(True)
        lay.addWidget(un)

        lay.addWidget(header("Folders"))
        row = QGridLayout()
        row.setSpacing(6)
        for i, (text, path) in enumerate((("Denoise models", os.path.join(MODELS_DIR, "denoise")),
                                          ("Upscale models", os.path.join(MODELS_DIR, "upscale")),
                                          ("Grain models", os.path.join(MODELS_DIR, "grain")),
                                          ("LUTs", LUT_DIR), ("App folder", APP_DIR))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, p=path: open_folder(p))
            row.addWidget(b, i // 3, i % 3)
        lay.addLayout(row)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(20, 10, 20, 14)
        ver = QLabel(f"{APP_NAME} {VERSION}")
        ver.setObjectName("Subtle")
        bottom.addWidget(ver)
        bottom.addStretch()
        ok = prop(QPushButton("Done"), "primary")
        ok.clicked.connect(self.accept)
        bottom.addWidget(ok)
        outer.addLayout(bottom)
        # show everything when the screen allows; otherwise scroll
        h = lay.totalHeightForWidth(620) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        h = max(h, lay.totalSizeHint().height()) + 70
        screen = win.screen().availableGeometry().height() if win.screen() else 900
        width = max(660, content.minimumSizeHint().width() + 40)
        self.resize(width, min(h, int(screen * 0.9)))

    def _lang_changed(self, _i):
        code = self.lang.currentData()
        if code == self.win.ui.get("language", "en"):
            return
        self.win.ui["language"] = code
        self.win.change_language(code)        # in place: nothing reloads, the window just changes language

    def _sync_bits(self):
        fmt = self.fmt.currentText()
        self.bits.blockSignals(True)
        self.bits.clear()
        self.bits.addItems(BITS_FOR[fmt])
        self.bits.setCurrentText(self.win.bits.currentText() if self.win.bits.currentText() in BITS_FOR[fmt]
                                 else BITS_FOR[fmt][0])
        self.bits.blockSignals(False)

    def _fmt_changed(self, fmt):
        self.win.fmt.setCurrentText(fmt)
        self._sync_bits()
        self.win.bits.setCurrentText(self.bits.currentText())

    def _mark_theme(self, name):
        for n, b in self.theme_btns.items():
            b.setChecked(n == name)

    def _pick_theme(self, name):
        self._mark_theme(name)
        self.win.set_ui("theme", name)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if hasattr(os, "startfile"):
        os.startfile(path)


# ============================================================================ main window

class MainWindow(QMainWindow):
    def __init__(self, reuse=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.bus = Bus()
        self.ui = dict(UI_DEFAULTS)
        self.model_cache = {}
        self.files = []
        self.job = None            # running detail / batch control
        self.job_kind = None
        self.cur_token = 0
        self.src_size = (0, 0)
        self.preview_is_full = False
        self.full_pending = False
        self.need_source = True
        self.detail_active = False
        self.thumb_token = 0
        self.thumb_items = {}
        self.batch_t0 = 0
        self.live_ctl = None
        self.live_token = 0
        self.live_key = None
        self.errors = {}           # field -> message
        self.applied_easy = None   # quick-edit values the preview shows (differs from the sliders in Manual mode)
        self.pending = False
        self.patch_forced = False
        self.region = None         # stored AI-stage result for the zoomed-in area
        self._loading = True
        self._pending = {}

        data = self._read_settings()
        self.ui.update({k: v for k, v in data.get("ui", {}).items() if k in UI_DEFAULTS})
        self.ui["scale_default_303"] = True     # _read_settings already applied the one-time switch
        DENSITY["compact"] = self.ui.get("density", "compact") == "compact"
        self.colors = themes.THEMES.get(self.ui["theme"], themes.THEMES[themes.DEFAULT_THEME])
        if reuse is not None:            # rebuilt interface (language change): keep the worker and loaded models
            self.model_cache = reuse.model_cache
            self.preview = reuse.preview
            self.preview.bus = self.bus
            self.preview.key = None          # re-send the image to the new window (from its memory cache)
        else:
            self.preview = PreviewWorker(self.bus)

        self.live_timer = QTimer(self)
        self.live_timer.setSingleShot(True)
        self.live_timer.timeout.connect(self._run_live)

        self._build()
        self._connect_bus()
        self.apply_theme(self.ui["theme"])
        self._apply_settings(data)
        E.set_performance(self.ui["gpu_limit"] / 100, self.ui["cpu_threads"])
        self._refresh_models()
        self._loading = False
        self._on_input_changed()
        self._setup_model_watch()
        if self.ui.get("geometry"):
            try:
                x, y, w, h = self.ui["geometry"]
                self.setGeometry(x, y, w, h)
            except Exception:
                self.resize(1500, 920)
        else:
            self.resize(1500, 920)
        QTimer.singleShot(0, self._keep_on_screen)
        threading.Thread(target=lambda: self.bus.device.emit(E.available_device()), daemon=True).start()

    # ------------------------------------------------------------------ layout

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_sidebar())
        split.addWidget(self._build_center())
        self.lm_panel = LightMixPanel(self.ui.get("lm_mode", "individual"))
        self.lm_panel.hide()
        split.addWidget(self.lm_panel)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1100, 0])
        self.main_split = split
        v.addWidget(split, 1)
        self._set_job_ui(False)

        self.activity = ActivityBar()
        self.activity.on_cancel = self._cancel_all
        self.activity.log_btn.clicked.connect(lambda: self.log.setVisible(not self.log.isVisible()))
        lay_a = self.activity.layout()
        show = QLabel("Show:")
        show.setObjectName("Subtle")
        self.show_img = QCheckBox("Images")
        self.show_img.setToolTip("Show and process PNG / JPG / TIF / EXR files")
        self.show_cxr = QCheckBox("CXR")
        self.show_cxr.setToolTip("Show and process Corona .cxr files.\n"
                                 "Unchecked hides the LightMix panel; its settings are kept.")
        self.show_img.setChecked(self.ui.get("show_images", True))
        self.show_cxr.setChecked(self.ui.get("show_cxr", True))
        i = lay_a.indexOf(self.activity.log_btn)
        for k, wdg in enumerate((show, self.show_img, self.show_cxr)):
            lay_a.insertWidget(i + k, wdg)
        lay_a.insertSpacing(i + 3, 10)
        self.reset_btn.setProperty("flat", True)
        lay_a.addSpacing(6)
        lay_a.addWidget(self.reset_btn)
        lay_a.addWidget(self.device)
        v.addWidget(self.activity)

        for w in (self.viewer, self.film):
            QShortcut(QKeySequence(Qt.Key_Right), w, activated=lambda: self._step_image(1), context=Qt.WidgetShortcut)
            QShortcut(QKeySequence(Qt.Key_Left), w, activated=lambda: self._step_image(-1), context=Qt.WidgetShortcut)
        # Up / Down: step through the looks (or presets) while the preview, the strip or those buttons have focus
        self.viewer.stepKey.connect(self._cycle_choice)
        self.film.stepKey.connect(self._cycle_choice)
        for w in (self.looks_btn, self.presets_btn):
            QShortcut(QKeySequence(Qt.Key_Up), w, activated=lambda: self._cycle_choice(-1), context=Qt.WidgetShortcut)
            QShortcut(QKeySequence(Qt.Key_Down), w, activated=lambda: self._cycle_choice(1), context=Qt.WidgetShortcut)
        QShortcut(QKeySequence("Escape"), self, activated=self._escape)
        QShortcut(QKeySequence("F5"), self, activated=self._update_now)

    def _build_header(self):
        h = QFrame()
        h.setObjectName("Header")
        h.setFixedHeight(D("header_h"))
        lay = QHBoxLayout(h)
        lay.setContentsMargins(16, 8, 14, 8)
        lay.setSpacing(10)
        logo = QLabel()
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm = app_icon().pixmap(QSize(30, 30), dpr) if hasattr(QIcon, "pixmap") else app_icon().pixmap(30, 30)
        logo.setPixmap(pm)
        lay.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel(APP_NAME)
        t.setObjectName("AppTitle")
        self.subtitle = ElidedLabel("Batch post-processing for renders")   # a long folder path ends in "…"
        self.subtitle.setObjectName("Subtle")
        col.addWidget(t)
        col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        self.device = QLabel("Detecting GPU…")
        self.device.setObjectName("Badge")
        reset = QPushButton("↺  Reset all")
        reset.setToolTip("Put every setting back to its default (folders are kept)")
        reset.clicked.connect(self._reset_all)
        self.reset_btn = reset          # both live in the bottom bar, right of "Activity log"
        looks = QPushButton("Looks")
        self.looks_btn = looks
        looks.setContextMenuPolicy(Qt.CustomContextMenu)
        looks.customContextMenuRequested.connect(lambda _p: self._clear_look())
        looks.setToolTip(LOOKS_TIP)
        lmenu = QMenu(looks)
        lmenu.setToolTipsVisible(True)
        lmenu.aboutToShow.connect(lambda m=lmenu: self._fill_looks(m))
        looks.setMenu(lmenu)
        self._fill_looks(lmenu)
        lay.addWidget(looks)
        presets = QPushButton("Presets")
        self.presets_btn = presets
        presets.setContextMenuPolicy(Qt.CustomContextMenu)
        presets.customContextMenuRequested.connect(lambda _p: self._reset_all())
        presets.setToolTip(PRESETS_TIP)
        pmenu = QMenu(presets)
        pmenu.setToolTipsVisible(True)
        pmenu.aboutToShow.connect(lambda m=pmenu: self._fill_presets(m))
        presets.setMenu(pmenu)
        self._fill_presets(pmenu)
        lay.addWidget(presets)
        gear = QPushButton("⚙  Settings")
        gear.clicked.connect(lambda: SettingsDialog(self).exec())
        self.gear = gear
        lay.addWidget(gear)
        return h

    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setMinimumWidth(388)
        side.setMaximumWidth(540)
        outer = QVBoxLayout(side)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = CardColumn()
        inner.setObjectName("SidebarInner")
        inner.reordered.connect(self._reorder_cards)
        self.card_column = inner
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(*(D("side_margins"),) * 4)
        lay.setSpacing(D("side_spacing"))
        self.scroll.setWidget(inner)
        outer.addWidget(self.scroll, 1)
        self.cards = []
        self.card_of = {}
        self.switch = {}
        col = set(self.ui.get("collapsed", []))

        def card(title, key, switch_key=None, tip=""):
            c = Card(title, key, key in col, tip if switch_key else None)
            self.cards.append(c)
            inner.cards.append(c)
            if switch_key:
                self.switch[switch_key] = c.switch
            lay.addWidget(c)
            return c

        # Source
        c = card("Source", "source")
        self.in_dir = PathRow("Folder with your renders", label="📂  Input folder: your renders")
        self.out_dir = PathRow("Empty = a 'processed' folder inside the input folder",
                               label="💾  Output folder: where results are saved")
        c.lay.addWidget(self.in_dir)
        c.lay.addWidget(self.out_dir)
        self.count_lbl = QLabel("No folder selected")
        self.count_lbl.setObjectName("Subtle")
        self.count_lbl.setWordWrap(True)
        c.lay.addWidget(self.count_lbl)
        self.card_of.update(input_dir=c, output_dir=c)

        # Mood / relight
        c = card("Mood / relight", "look", "look_on", "Turn the mood grade on or off")
        chips = QGridLayout()
        chips.setSpacing(6)
        for i, name in enumerate(MOODS):
            b = prop(QPushButton(name), "chip")
            b.clicked.connect(lambda _=False, n=name: self._apply_mood(n))
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            chips.addWidget(b, i // 3, i % 3)
        c.lay.addLayout(chips)
        self.sl = {}
        for key, label, lo, hi, dflt, dec, suf, tip, hard in (
                ("exposure", "Exposure (brightness)", -3, 3, 0.0, 2, " EV", "Brightness in camera stops: +1 = twice as bright", (-10, 10)),
                ("temperature", "Temperature (cool ↔ warm)", -100, 100, 0.0, 0, "", "Negative = cooler / bluer, positive = warmer / more orange", (-300, 300)),
                ("tint", "Tint (green ↔ magenta)", -100, 100, 0.0, 0, "", "Negative = greener, positive = more magenta", (-300, 300)),
                ("contrast", "Contrast", 0.5, 1.5, 1.0, 2, "", "1 = unchanged", (0.0, 4.0)),
                ("saturation", "Saturation (color intensity)", 0.0, 2.0, 1.0, 2, "", "0 = black and white, 1 = unchanged", (0.0, 4.0))):
            self.sl[key] = SliderRow(label, lo, hi, dflt, dec, suf, tip, hard)
            c.lay.addWidget(self.sl[key])
        self.lut = LutRow()
        c.lay.addWidget(self.lut)
        self.sl["lut_strength"] = SliderRow("LUT strength", 0, 1, 1.0, 2, "", "How much of the LUT look is applied",
                                            (0.0, 2.0))
        c.lay.addWidget(self.sl["lut_strength"])
        reset = prop(QPushButton("Reset mood"), "flat")
        reset.clicked.connect(lambda: self._apply_mood("Neutral"))
        c.lay.addWidget(reset, 0, Qt.AlignRight)
        self.card_of.update(lut_path=c)

        self._build_atmosphere(card)

        # Match colors
        c = card("Match colors", "match", "match_on", "Match every image to a reference image's colors")
        info = QLabel("Pulls every image toward the colors of a reference, for a consistent set. "
                      "The reference can be any image, also one outside the folder: drop it here.")
        info.setObjectName("Subtle")
        info.setWordWrap(True)
        c.lay.addWidget(info)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        self.ref_thumb = QLabel()
        self.ref_thumb.setFixedSize(96, 60)
        self.ref_thumb.setAlignment(Qt.AlignCenter)
        self.ref_thumb.setObjectName("RefThumb")
        rl.addWidget(self.ref_thumb)
        rc = QVBoxLayout()
        rc.setSpacing(6)
        self.ref = QComboBox()
        rc.addWidget(self.ref)
        pick = QPushButton("Choose any image…")
        pick.clicked.connect(self._browse_ref)
        rc.addWidget(pick)
        rl.addLayout(rc, 1)
        c.lay.addWidget(row)
        self.sl["cm_strength"] = SliderRow("Strength", 0, 1, 0.6, 2, "", "0 = no change, 1 = full match", (0.0, 2.0))
        c.lay.addWidget(self.sl["cm_strength"])
        self.card_of.update(ref_image=c)

        # Denoise
        c = card("Denoise", "denoise", "denoise_on", "Turn AI denoising on or off")
        self.dn_model = ModelCombo()
        self.dn_model.setToolTip("Models from the models\\denoise folder. Default models with a ⬇ icon "
                                 "aren't downloaded yet: pick one to download it")
        c.lay.addWidget(field("Model", self.dn_model))
        self.mode_btns = {}
        c.lay.addWidget(self._mode_row("dn_mode", "denoising"))
        self.sl["denoise_strength"] = SliderRow("Strength", 0, 1, 0.7, 2, "",
                                                "Blend between the original (0) and fully denoised (1)",
                                                help_text="0.6–0.8 removes noise and keeps fine texture like wood grain.")
        c.lay.addWidget(self.sl["denoise_strength"])
        self.card_of.update(denoise_model=c)

        # Resize / upscale
        c = card("Resolution / upscale", "upscale", "resize_on", "Off = keep the original resolution")
        self.up_model = ModelCombo()
        self.up_model.setToolTip("Models from the models\\upscale folder. Default models with a ⬇ icon "
                                 "aren't downloaded yet: pick one to download it")
        c.lay.addWidget(field("AI model", self.up_model))
        c.lay.addWidget(self._mode_row("up_mode", "upscaling"))
        self.mode = QComboBox()
        for t in ["Scale factor", "Fit width", "Fit height", "Fit inside box", "Exact size"]:
            self.mode.addItem(t)
        c.lay.addWidget(field("Resize", self.mode))
        wh = QWidget()
        whl = QHBoxLayout(wh)
        whl.setContentsMargins(0, 0, 0, 0)
        self.w_spin, self.h_spin = QSpinBox(), QSpinBox()
        for sp in (self.w_spin, self.h_spin):
            sp.setRange(64, 16384)
            sp.setSingleStep(8)
            sp.setButtonSymbols(QSpinBox.NoButtons)
        whl.addWidget(self.w_spin)
        whl.addWidget(QLabel("×"))
        whl.addWidget(self.h_spin)
        self.wh_field = field("Width × height", wh)
        c.lay.addWidget(self.wh_field)
        self.factor = QDoubleSpinBox()
        self.factor.setRange(0.05, 16)
        self.factor.setDecimals(2)
        self.factor.setSingleStep(0.25)
        self.factor.setSuffix(" ×")
        self.factor.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.factor_field = field("Scale factor", self.factor)
        set_spin_default(self.w_spin, E.DEFAULTS["width"])
        set_spin_default(self.h_spin, E.DEFAULTS["height"])
        set_spin_default(self.factor, E.DEFAULTS["factor"])
        c.lay.addWidget(self.factor_field)
        mr = QWidget()
        mrl = QHBoxLayout(mr)
        mrl.setContentsMargins(0, 0, 0, 0)
        mrl.setSpacing(8)
        self.multi_chk = QCheckBox("Upscale multiple times")
        self.multi_chk.setToolTip("Runs the upscale step several times in a row before saving, each time by the\n"
                                  "Scale factor (2x three times = 8x), e.g. for a wallpaper texture going to print.\n"
                                  "The file name gets _upscaled_x<times>. Only with Resize: Scale factor.")
        self.multi_n = QSpinBox()
        self.multi_n.setRange(1, 8)
        self.multi_n.setValue(1)
        self.multi_n.setSuffix(" ×")
        self.multi_n.setFixedWidth(D("spin_w"))
        self.multi_n.setToolTip("How many times the image is upscaled")
        set_spin_default(self.multi_n, 1)
        mrl.addWidget(self.multi_chk, 1)
        mrl.addWidget(self.multi_n)
        self.multi_row = mr
        c.lay.addWidget(mr)
        self.save_unscaled = QCheckBox("Also save a version without upscaling")
        self.save_unscaled.setToolTip("Saves the image a second time at its original size (with the same grade,\n"
                                      "sharpening...) next to the upscaled one, named ..._no-upscale.\n"
                                      "The upscaled file then gets the upscaler's name, so trying several\n"
                                      "upscalers keeps every result side by side for comparing.")
        c.lay.addWidget(self.save_unscaled)
        self.sl["min_model_scale"] = SliderRow("Use the AI upscaler only when enlarging at least", 1.0, 3.0, 1.3, 2, "×",
                                               "Example: 1.30× means an image made 1.3 times bigger (or more) goes through\n"
                                               "the AI upscaler; smaller enlargements use a normal resize.",
                                               (1.0, 16.0),
                                               "Smaller size changes use a normal resize: faster and nothing is invented.")
        c.lay.addWidget(self.sl["min_model_scale"])
        self.fp16 = QCheckBox("Fast mode (half precision: quicker, less power)")
        c.lay.addWidget(self.fp16)
        self.target_lbl = QLabel("")
        self.target_lbl.setObjectName("Subtle")
        self.target_lbl.setWordWrap(True)
        c.lay.addWidget(self.target_lbl)
        self.min_scale_row = self.sl["min_model_scale"]
        self.card_of.update(upscale_model=c, width=c, height=c, factor=c)

        # Sharpen
        c = card("Sharpen", "sharpen", "sharpen_on", "Turn sharpening on or off")
        self.sl["sharpen_amount"] = SliderRow("Amount", 0, 2, 0.3, 2, "", "How strong the sharpening is", (0.0, 10.0))
        self.sl["sharpen_radius"] = SliderRow("Detail size", 0.3, 4, 1.0, 1, " px",
                                              "Small = crisp fine texture, large = punchier edges", (0.1, 20.0))
        self.sl["sharpen_threshold"] = SliderRow("Protect smooth areas", 0, 0.1, 0.01, 3, "",
                                                 "Detail weaker than this is left alone", (0.0, 0.5),
                                                 "Keeps grain and flat walls from being sharpened. Raise it if noise gets crunchy.")
        for k in ("sharpen_amount", "sharpen_radius", "sharpen_threshold"):
            c.lay.addWidget(self.sl[k])

        # Texture & grain
        c = card("Texture & grain", "texture", "texture_on", "Turn clarity and grain on or off")
        info = QLabel("Brings back the fine texture that denoising and upscaling remove, so renders read as photos.")
        info.setObjectName("Subtle")
        info.setWordWrap(True)
        c.lay.addWidget(info)
        self.sl["clarity"] = SliderRow("Clarity (micro-contrast)", -0.5, 1.0, 0.2, 2, "",
                                       "Local contrast in the midtones. Negative = softer", (-3.0, 3.0),
                                       "Restores the depth that denoising flattens. Updates live at any zoom.")
        c.lay.addWidget(self.sl["clarity"])
        self.sl["grain_amount"] = SliderRow("Grain amount", 0.0, 1.5, 0.4, 2, "",
                                            "0 = no grain. About 0.3–0.6 looks like a clean photo", (0.0, 5.0),
                                            "Follows the light like real capture: none in blown highlights, "
                                            "most in shadows (sensor) or midtones (film). Zoom to 100% to judge it.")
        c.lay.addWidget(self.sl["grain_amount"])
        self.sl["grain_size"] = SliderRow("Grain size", 0.5, 3.0, 1.0, 1, " px",
                                          "Size of a grain at the final resolution. 1 = fine digital, 2+ = film",
                                          (0.2, 10.0))
        c.lay.addWidget(self.sl["grain_size"])
        self.sl["grain_color"] = SliderRow("Grain color", 0.0, 1.0, 0.25, 2, "",
                                           "0 = monochrome grain, 1 = fully colored speckles")
        c.lay.addWidget(self.sl["grain_color"])
        self.grain_type = QComboBox()
        for key, text in GRAIN_TYPES:
            self.grain_type.addItem(text, key)
        self.grain_type.setToolTip("Digital sensor: strongest in the shadows, gone in clipped highlights\n"
                                   "Film: strongest in the midtones, fades toward black and white")
        c.lay.addWidget(field("Grain type", self.grain_type))
        self.grain_model = QComboBox()
        self.grain_model.setToolTip("Optional 1x grain models from the models\\grain folder.\n"
                                    "The built-in grain is recommended: it's consistent across a batch.")
        c.lay.addWidget(field("Grain source", self.grain_model))
        self.card_of.update(grain_model=c)

        lay.addStretch()
        self.card_layout = lay
        # output settings live in Settings; these widgets hold the values
        self.fmt = QComboBox(self)
        self.fmt.addItems(["png", "tif", "jpg"])
        self.bits = QComboBox(self)
        self.bits.addItems(BITS_FOR["png"])
        self.suffix = QLineEdit(self)
        for w in (self.fmt, self.bits, self.suffix):
            w.hide()
        self.fmt.currentTextChanged.connect(self._fill_bits)
        self._apply_card_layout()
        for cb in inner.findChildren(QComboBox):  # don't let long names widen the sidebar
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(6)
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # the widget that gets the red outline for each setting
        self.field_widget = {
            "input_dir": self.in_dir.edit, "lightmix": None, "output_dir": self.out_dir.edit, "lut_path": self.lut.edit,
            "ref_image": self.ref, "denoise_model": self.dn_model, "upscale_model": self.up_model,
            "width": self.w_spin, "height": self.h_spin, "factor": self.factor, "suffix": self.suffix,
            "grain_model": self.grain_model,
        }

        # batch card, pinned at the bottom
        bc = QFrame()
        bc.setObjectName("BatchCard")
        bl = QVBoxLayout(bc)
        bl.setContentsMargins(*D("batch_m"))
        bl.setSpacing(6)
        self.batch_title = QLabel("Batch")
        self.batch_title.setStyleSheet("font-weight: 600;")
        self.batch_info = QLabel("Choose an input folder")
        self.batch_info.setObjectName("Subtle")
        self.batch_info.setWordWrap(True)
        self.batch_bar = QProgressBar()
        self.batch_bar.setRange(0, 1000)
        self.batch_bar.setTextVisible(False)
        self.img_bar = QProgressBar()
        self.img_bar.setRange(0, 1000)
        self.img_bar.setTextVisible(False)
        self.img_bar.setFixedHeight(4)
        row = QHBoxLayout()
        self.run_btn = prop(QPushButton("▶  Process folder"), "primary")
        self.run_btn.clicked.connect(lambda: self._start_batch())
        self.sel_btn = QPushButton("Process selected")
        self.sel_btn.setToolTip("Process only the images selected in the strip below the preview\n"
                                "(Ctrl / Shift-click to select several)")
        self.sel_btn.clicked.connect(lambda: self._start_batch(selected=True))
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn = prop(QPushButton("Cancel"), "danger")
        self.cancel_btn.clicked.connect(self._cancel_job)
        self.open_btn = QPushButton("Open output")
        self.open_btn.clicked.connect(self._open_output)
        row.addWidget(self.run_btn, 1)
        row.addWidget(self.sel_btn, 1)
        row.addWidget(self.pause_btn)
        row.addWidget(self.cancel_btn)
        bl.addWidget(self.batch_title)
        bl.addWidget(self.batch_info)
        bl.addWidget(self.batch_bar)
        bl.addWidget(self.img_bar)
        self.skip = QCheckBox("Skip images already processed (resume)")
        self.skip.setToolTip("Images whose output file already exists are left alone")
        bl.addWidget(self.skip)
        bl.addLayout(row)
        bl.addWidget(self.open_btn)
        outer.addWidget(bc)
        wrap_checkboxes(side)
        self.sidebar_root = side
        return side

    # ------------------------------------------------------------------ Atmosphere card

    def _build_atmosphere(self, card):
        from lightmix_panel import ColorSwatch
        c = card("Atmosphere", "atmosphere", "atmo_on", "Time of day, fog and light rays: on or off")
        self.atmo_card = c

        def sub(text):
            l = QLabel(text)
            l.setObjectName("SubHead")
            l.setStyleSheet("font-weight: 600; padding-top: 4px;")
            return l

        # Time of day
        c.lay.addWidget(sub("Time of day"))
        seg = QFrame()
        seg.setObjectName("Seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.tod_group = QButtonGroup(seg)
        self.tod_btns = {}
        for val, text, tip in (("off", "As rendered", "Keep the lighting of the render"),
                               ("day", "Day", "Brighter daylight; interior lights dimmed"),
                               ("dusk", "Dusk", "Low warm sun, violet sky, interior lights on"),
                               ("night", "Night", "Sun off, dark blue sky, interior lights on")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setProperty("val", val)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.tod_group.addButton(b)
            sl.addWidget(b)
            self.tod_btns[val] = b
        self.tod_btns["off"].setChecked(True)
        self.tod_group.buttonClicked.connect(lambda _b: (self._sync_atmo_ui(), self._on_setting("tod_mode")))
        c.lay.addWidget(seg)
        self.tod_note = QLabel(".cxr files are relit through their LightMix passes (Sun, Environment and the "
                               "interior lights); other images are relit with a grade that keeps lamps lit.")
        self.tod_note.setObjectName("Subtle")
        self.tod_note.setWordWrap(True)
        self.tod_note.setStyleSheet("font-size: 8.5pt;")
        c.lay.addWidget(self.tod_note)
        for key, label, lo, hi, dflt, dec, suf, tip, hard in (
                ("tod_amount", "Amount", 0, 1, 1.0, 2, "", "How far toward the chosen time of day (1 = all the way)", None),
                ("tod_lights", "Interior lights", 0, 4, 1.0, 2, " ×", "Brightness of the lamps and other interior lights", (0.0, 20.0)),
                ("tod_sky", "Sky & sun", 0, 4, 1.0, 2, " ×", "Brightness of the daylight (sky, sun, windows)", (0.0, 20.0)),
                ("tod_warmth", "Lights warmth", -1, 1, 0.0, 2, "", "Negative = cooler lamps, positive = warmer", None)):
            self.sl[key] = SliderRow(label, lo, hi, dflt, dec, suf, tip, hard)
            c.lay.addWidget(self.sl[key])

        # Fog
        self.fog_chk = QCheckBox("Fog / haze")
        self.fog_chk.setStyleSheet("font-weight: 600; padding-top: 6px;")
        self.fog_chk.setToolTip("Air that gets thicker with distance; far things fade into the fog color")
        self.fog_chk.toggled.connect(lambda _v: (self._sync_atmo_ui(), self._on_setting("fog_on")))
        c.lay.addWidget(self.fog_chk)
        for key, label, lo, hi, dflt, dec, suf, tip, hard in (
                ("fog_density", "Density", 0, 1, 0.3, 2, "", "How thick the fog is", (0.0, 3.0)),
                ("fog_start", "Starts at", 0, 0.9, 0.1, 2, "", "Distance where the fog begins (0 = right at the camera, "
                                                               "higher = only far things get foggy)", None),
                ("fog_height", "Ground fog", 0, 1, 0.0, 2, "", "Higher = the fog lies low and thins out toward the top "
                                                            "of the image", None),
                ("fog_glow", "Glow around lights", 0, 1, 0.3, 2, "", "Light scattered by the fog: soft halos around "
                                                                    "lamps and windows", (0.0, 3.0))):
            self.sl[key] = SliderRow(label, lo, hi, dflt, dec, suf, tip, hard)
            c.lay.addWidget(self.sl[key])
        self.fog_sw = ColorSwatch("Fog", tip="Fog color (white box = automatic, from the scene's light)\n"
                                             "Click: pick a color · Right-click: back to automatic",
                                  reset_text="↺  Automatic")
        self.fog_sw.colorChanged.connect(lambda: self._on_setting("fog_color"))
        self.fog_color_row = field("Fog color", self.fog_sw, False)
        c.lay.addWidget(self.fog_color_row)

        # Light rays
        self.rays_chk = QCheckBox("Light rays (volumetric light)")
        self.rays_chk.setStyleSheet("font-weight: 600; padding-top: 6px;")
        self.rays_chk.setToolTip("Visible beams of light, like sunlight through a window in dusty air")
        self.rays_chk.toggled.connect(lambda _v: (self._sync_atmo_ui(), self._on_setting("rays_on")))
        c.lay.addWidget(self.rays_chk)
        self.rays_src = QComboBox()
        self.rays_src.addItem("Bright areas (automatic)", "auto")
        self.rays_src.setToolTip("What the rays come from. For .cxr files you can pick one LightMix light\n"
                                 "(e.g. the Sun) so only that light makes rays.")
        self.rays_src.currentIndexChanged.connect(lambda _i: self._on_setting("rays_source"))
        self.rays_src_row = field("Rays from", self.rays_src)
        c.lay.addWidget(self.rays_src_row)
        for key, label, lo, hi, dflt, dec, suf, tip, hard in (
                ("rays_intensity", "Intensity", 0, 2, 0.6, 2, "", "How bright the rays are", (0.0, 10.0)),
                ("rays_length", "Length", 0, 1, 0.5, 2, "", "How far the rays reach", None),
                ("rays_angle", "Light comes from", -180, 180, 20.0, 0, "°", "Direction of the light: 0° = from above, "
                                                                         "90° = from the right, -90° = from the left", None),
                ("rays_spread", "Spread", 0, 1, 0.15, 2, "", "0 = parallel beams (sunlight), 1 = rays fanning out from "
                                                             "the light itself (a lamp)", None),
                ("rays_threshold", "Only from brighter than", 0.1, 1, 0.6, 2, "", "Which areas make rays: higher = only "
                                                                              "the brightest (windows, the sun patch)", None)):
            self.sl[key] = SliderRow(label, lo, hi, dflt, dec, suf, tip, hard)
            c.lay.addWidget(self.sl[key])
        self._rays_point = []
        pr = QWidget()
        prl = QHBoxLayout(pr)
        prl.setContentsMargins(0, 0, 0, 0)
        prl.setSpacing(6)
        self.rays_pick = QPushButton("⌖  Pick on image")
        self.rays_pick.setToolTip("Click a point in the preview: the rays fan out from there\n"
                                  "(replaces 'Light comes from' and 'Spread')")
        self.rays_pick.clicked.connect(self._pick_rays_point)
        self.rays_auto = prop(QPushButton("Automatic"), "flat")
        self.rays_auto.setToolTip("Use 'Light comes from' and 'Spread' again")
        self.rays_auto.clicked.connect(lambda: self._set_rays_point([]))
        self.rays_point_lbl = QLabel("")
        self.rays_point_lbl.setObjectName("Subtle")
        prl.addWidget(self.rays_pick)
        prl.addWidget(self.rays_auto)
        prl.addWidget(self.rays_point_lbl, 1)
        self.rays_point_row = field("Center", pr)
        c.lay.addWidget(self.rays_point_row)
        self.rays_sw = ColorSwatch("Rays", tip="Rays color (white box = the color of the light itself)\n"
                                               "Click: pick a color · Right-click: back to automatic",
                                   reset_text="↺  Automatic")
        self.rays_sw.colorChanged.connect(lambda: self._on_setting("rays_color"))
        self.rays_color_row = field("Rays color", self.rays_sw, False)
        c.lay.addWidget(self.rays_color_row)

        # depth for fog / rays
        dr = QWidget()
        drl = QHBoxLayout(dr)
        drl.setContentsMargins(0, 4, 0, 0)
        drl.setSpacing(6)
        self.depth_lbl = QLabel("")
        self.depth_lbl.setObjectName("Subtle")
        self.depth_lbl.setWordWrap(True)
        self.depth_lbl.setStyleSheet("font-size: 8.5pt;")
        self.depth_btn = QPushButton("Download (99 MB)")
        self.depth_btn.setToolTip("Downloads Depth Anything V2 Small (free, Apache-2.0) into models\\depth.\n"
                                  "Once, then it works offline. You can also copy the file there by hand.")
        self.depth_btn.clicked.connect(self._download_depth)
        drl.addWidget(self.depth_lbl, 1)
        drl.addWidget(self.depth_btn, 0, Qt.AlignTop)
        c.lay.addWidget(dr)
        self._sync_atmo_ui()

    def _tod_value(self):
        for v, b in self.tod_btns.items():
            if b.isChecked():
                return v
        return "off"

    def _sync_atmo_ui(self):
        """Greys out what doesn't apply; shows where fog / rays get their depth from."""
        import atmosphere
        tod = self._tod_value() != "off"
        for k in ("tod_amount", "tod_lights", "tod_sky", "tod_warmth"):
            self.sl[k].setEnabled(tod)
        fog = self.fog_chk.isChecked()
        for k in ("fog_density", "fog_start", "fog_height", "fog_glow"):
            self.sl[k].setEnabled(fog)
        self.fog_color_row.setEnabled(fog)
        rays = self.rays_chk.isChecked()
        picked = bool(self._rays_point)
        for k in ("rays_intensity", "rays_length", "rays_threshold"):
            self.sl[k].setEnabled(rays)
        for k in ("rays_angle", "rays_spread"):
            self.sl[k].setEnabled(rays and not picked)
        for w in (self.rays_src_row, self.rays_point_row, self.rays_color_row):
            w.setEnabled(rays)
        self.rays_auto.setVisible(picked)
        self.rays_point_lbl.setText(f"picked ({self._rays_point[0] * 100:.0f}%, {self._rays_point[1] * 100:.0f}%)"
                                    if picked else "")
        has = atmosphere.has_depth_model(MODELS_DIR)
        downloading = getattr(self, "_dl_ctl", None) is not None
        if has:
            self.depth_lbl.setText("Depth for fog and rays: AI depth model ✓ (a Corona ZDepth pass in a .cxr is "
                                   "used instead when the file has one)")
        else:
            self.depth_lbl.setText("Depth for fog and rays: rough guess. Download the AI depth model once for "
                                   "accurate fog (a Corona ZDepth pass in a .cxr is used when present).")
        self.depth_btn.setVisible(not has)
        self.depth_btn.setEnabled(not downloading)
        if hasattr(self, "viewer"):
            self.viewer.rays_marker = self._rays_point if (rays and picked) else None
            self.viewer.update()

    def _set_rays_point(self, pt):
        self._rays_point = [round(float(pt[0]), 4), round(float(pt[1]), 4)] if pt else []
        self._sync_atmo_ui()
        self._on_setting("rays_point")

    def _pick_rays_point(self):
        if not self.src_size[0]:
            return
        self.viewer.start_pick(lambda x, y: self._set_rays_point([x, y]))
        self.viewer.set_status("Click where the light rays should come from (Esc cancels)")

    def _download_depth(self):
        import atmosphere
        if getattr(self, "_dl_ctl", None) is not None:
            return
        ctl = self._dl_ctl = E.Control()
        bus = self.bus
        self._sync_atmo_ui()
        self.activity.set("download", "Downloading the depth model…", -1, 2)

        def work():
            try:
                atmosphere.download_depth_model(
                    MODELS_DIR, progress=lambda f: bus.task.emit("download", "Downloading the depth model…", f, 2),
                    cancel=ctl.check)
                bus.depth_done.emit("")
            except E.Cancelled:
                bus.depth_done.emit("cancelled")
            except Exception as e:
                bus.depth_done.emit(str(e) or type(e).__name__)
            finally:
                bus.task_end.emit("download")

        threading.Thread(target=work, daemon=True).start()

    def _on_depth_done(self, err):
        import atmosphere
        self._dl_ctl = None
        self.activity.end("download")
        if not err:
            atmosphere._DEPTH.clear()
            atmosphere._MAPS.clear()
            self.activity.flash("Depth model installed: fog and light rays now use real depth")
            self._log("Depth model downloaded to models\\depth")
            self._on_look_changed()
        elif err != "cancelled":
            self._log(f"Depth model download failed: {err}")
            QMessageBox.warning(self, APP_NAME, "The depth model couldn't be downloaded:\n\n" + err +
                                "\n\nCheck the internet connection and try again. You can also download\n"
                                + atmosphere.DEPTH_URL + "\nand save it as models\\depth\\" + atmosphere.DEPTH_FILE)
        self._sync_atmo_ui()

    def _update_rays_sources(self, names):
        cur = self.rays_src.currentData() or getattr(self, "_rays_src_pending", "auto")
        self.rays_src.blockSignals(True)
        self.rays_src.clear()
        self.rays_src.addItem("Bright areas (automatic)", "auto")
        for n in names or []:
            self.rays_src.addItem(f"Light: {n}", n)
        i = self.rays_src.findData(cur)
        self.rays_src.setCurrentIndex(max(0, i))
        self.rays_src.blockSignals(False)

    def _mode_row(self, key, what):
        seg = QFrame()
        seg.setObjectName("Seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        grp = QButtonGroup(seg)
        btns = []
        for val, text, tip in (("interactive", "Interactive", f"Preview re-renders the {what} on the zoomed-in area\n"
                                                              "a moment after every change (uses the GPU more)"),
                               ("manual", "Manual", f"The {what} preview only runs when you press\n"
                                                    "Update preview (F5) or Detail check")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setProperty("val", val)
            b.setChecked(self.ui.get(key, "manual") == val)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grp.addButton(b)
            sl.addWidget(b)
            btns.append(b)
        grp.buttonClicked.connect(lambda b, k=key: self.set_ui(k, b.property("val")))
        self.mode_btns[key] = btns
        return field("Preview", seg)

    def _build_center(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        tb = ResponsiveBar()
        tb.setObjectName("Toolbar")
        self.toolbar = tb
        tl = QHBoxLayout(tb)
        tl.setContentsMargins(D("toolbar_m")[0], D("toolbar_m")[1], D("toolbar_m")[0], D("toolbar_m")[1])
        tl.setSpacing(8)
        prev_b = prop(QPushButton("◀"), "flat")
        next_b = prop(QPushButton("▶"), "flat")
        prev_b.clicked.connect(lambda: self._step_image(-1))
        next_b.clicked.connect(lambda: self._step_image(1))
        self.img_combo = QComboBox()
        self.img_combo.setMinimumWidth(96)
        self.img_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.img_combo.setMinimumContentsLength(5)
        self.img_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.img_combo.setMaximumWidth(320)
        self.img_combo.currentTextChanged.connect(self._on_image_selected)
        tl.addWidget(prev_b)
        tl.addWidget(self.img_combo)
        tl.addWidget(next_b)
        tl.addSpacing(6)

        seg = QFrame()
        seg.setObjectName("Seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        for key, text, short, tiny in (("split", "Split", "Split", "◧"), ("side", "Side by side", "Side", "◫"),
                                       ("before", "Before", "Before", "B"), ("after", "After", "After", "A")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(text)
            tb.add(b, [text, short, short, tiny])
            b.setCheckable(True)
            b.setProperty("mode", key)
            b.setCursor(Qt.PointingHandCursor)
            self.mode_group.addButton(b)
            sl.addWidget(b)
        self.mode_group.buttonClicked.connect(lambda b: self._set_view_mode(b.property("mode")))
        tl.addWidget(seg)
        tl.addStretch(1)

        self.update_btn = QPushButton("⟳  Update preview")
        self.update_btn.setToolTip("Applies waiting changes and renders every enabled step (also denoise and upscale)\n"
                                   "on the area you're zoomed into, or on the drawn Region.  Shortcut: F5")
        self.update_btn.clicked.connect(self._update_now)
        tl.addWidget(self.update_btn)
        tl.addSpacing(4)
        for text, fn, hide in (("−", lambda: self.viewer.set_zoom(self.viewer.zoom / 1.25, self._vc()), 2),
                               ("Fit", lambda: self.viewer.fit(), None),
                               ("100%", lambda: self.viewer.zoom_100(self._vc()), 2),
                               ("+", lambda: self.viewer.set_zoom(self.viewer.zoom * 1.25, self._vc()), 2)):
            b = prop(QPushButton(text), "flat")
            b.clicked.connect(fn)
            b.setToolTip({"−": "Zoom out", "Fit": "Fit to window (F)", "100%": "100% (1)", "+": "Zoom in"}[text])
            tl.addWidget(b)
            tb.add(b, None, hide)
        tl.addSpacing(4)
        self.roi_btn = QPushButton("⬚  Region")
        self.roi_btn.setCheckable(True)
        self.roi_btn.setToolTip("Region: drag on the image to choose the area the preview processes.\n"
                                "Denoise, upscale, sharpen and Detail check then run only inside it, however far\n"
                                "you zoom in or out. Drag its edges to resize it, drag inside to move it,\n"
                                "double-click inside to zoom to it, × or right-click to remove it.\n"
                                "Shortcut: R  (Shift-drag works anytime)")
        self.roi_btn.toggled.connect(self._toggle_roi_tool)
        tl.addWidget(self.roi_btn)
        self.roi_clear_btn = prop(QPushButton("✕"), "flat")
        self.roi_clear_btn.setToolTip("Clear the region: process the visible area again (Esc)")
        self.roi_clear_btn.clicked.connect(lambda: self.viewer.set_roi(None))
        self.roi_clear_btn._rb_wanted = False
        self.roi_clear_btn.hide()
        tl.addWidget(self.roi_clear_btn)
        tb.add(self.roi_btn, ["⬚  Region", "⬚  Region", "⬚", "⬚"])
        tb.add(self.roi_clear_btn, None)
        self.detail_btn = QPushButton("🔍  Detail check")
        self.detail_btn.setToolTip("Runs the full pipeline (denoise, upscale, sharpen) on the visible area\n"
                                   "and shows it at 100% of the final resolution")
        self.detail_btn.clicked.connect(self._detail_check)
        tl.addWidget(self.detail_btn)
        self.lm_btn = QPushButton("💡  LightMix")
        self.lm_btn.setCheckable(True)
        self.lm_btn.setToolTip("Corona LightMix: change the lights of the .cxr files in this folder")
        self.lm_btn.toggled.connect(self._toggle_lm_panel)
        self.lm_btn.hide()
        self.lm_btn._rb_wanted = False
        tl.addWidget(self.lm_btn)
        tb.add(self.detail_btn, ["🔍  Detail check", "🔍  Detail", "🔍", "🔍"])
        tb.add(self.lm_btn, ["💡  LightMix", "💡  LightMix", "💡", "💡"])
        tl.setSpacing(6)
        tb.on_level = lambda _l: self._update_btn_text()
        tb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        lay.addWidget(tb)

        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(12, 6, 8, 6)
        self.banner_text = QLabel("")
        back = QPushButton("Back to preview")
        back.clicked.connect(self._leave_detail)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(back)
        self.banner.hide()
        bw = QWidget()
        bwl = QVBoxLayout(bw)
        bwl.setContentsMargins(12, 0, 12, 8)
        bwl.addWidget(self.banner)
        lay.addWidget(bw)

        self.viewer = ImageViewer(self.colors)
        self.viewer.zoomedIn.connect(self._maybe_full_res)
        self.viewer.viewChanged.connect(self._view_changed)
        self.viewer.roiChanged.connect(self._roi_changed)
        self.viewer.roiToolKey.connect(lambda: self.roi_btn.toggle())
        lay.addWidget(self.viewer, 1)

        fbar = QFrame()
        fbar.setObjectName("FilmBar")
        fbl = QHBoxLayout(fbar)
        fbl.setContentsMargins(12, 2, 8, 2)
        fbl.setSpacing(8)
        self.film_info = QLabel("Drag to reorder · Ctrl / Shift-click to select several · drop files here to add them")
        self.film_info.setObjectName("Subtle")
        self.film_info.setStyleSheet("font-size: 8pt;")
        self.film_info.setMinimumWidth(0)
        self.film_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        fbl.addWidget(self.film_info, 1)
        self.order_btn = prop(QPushButton("Reset order"), "flat")
        self.order_btn.setToolTip("Back to the automatic order: processed images first, then by name")
        self.order_btn.clicked.connect(self._reset_film_order)
        self.order_btn.hide()
        fbl.addWidget(self.order_btn)
        lay.addWidget(fbar)
        self.film = FilmStrip()
        self.film.setObjectName("Filmstrip")
        self.film.setViewMode(QListWidget.IconMode)
        self.film.setFlow(QListWidget.LeftToRight)
        self.film.setWrapping(False)
        self.film.setIconSize(QSize(*D("thumb")))
        self.film.setFixedHeight(D("film_h"))
        self.film.setMovement(QListWidget.Snap)
        self.film.setSpacing(4)
        self.film.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.film.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.film.currentItemChanged.connect(lambda it, _: it and self.img_combo.setCurrentText(it.data(Qt.UserRole)))
        self.film.itemSelectionChanged.connect(self._on_film_selection)
        self.film.reordered.connect(self._on_film_reordered)
        self.film.filesDropped.connect(self._import_files)
        lay.addWidget(self.film)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(150)
        self.log.hide()
        lay.addWidget(self.log)
        return w

    def _vc(self):
        return QPointF(self.viewer.width() / 2, self.viewer.height() / 2)

    def _escape(self):
        if self.viewer.pick_cb is not None:
            self.viewer.cancel_pick()
            self.viewer.set_status("")
        elif self.detail_active:
            self._leave_detail()
        elif self.viewer.roi is not None:
            self.viewer.set_roi(None)
        elif self.roi_btn.isChecked():
            self.roi_btn.setChecked(False)

    # ------------------------------------------------------------------ processing region

    def _roi(self):
        """The drawn region in full-res pixels, or None (then the preview processes the visible area)."""
        v = self.viewer
        if self.detail_active or not self.src_size[0] or not v.roi_active():
            return None
        W, H = self.src_size
        x0, y0, x1, y1 = v.roi
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        return (x0, y0, x1, y1) if x1 - x0 >= 16 and y1 - y0 >= 16 else None

    def _toggle_roi_tool(self, on):
        self.viewer.set_roi_tool(on)
        if on:
            self.viewer.setFocus()
            if self.viewer.roi is None and self.src_size[0]:
                self.viewer.set_status("Drag on the image to draw the region to process")
        elif self.viewer.status == i18n.tr("Drag on the image to draw the region to process") or \
                self.viewer.status == "Drag on the image to draw the region to process":
            self.viewer.set_status("")

    def _roi_changed(self):
        """Region drawn / moved / resized / cleared: render the new area (or wait for F5 in manual modes)."""
        has = self.viewer.roi is not None
        self.toolbar.set_wanted(self.roi_clear_btn, has)
        self.detail_btn.setToolTip(
            "Runs the full pipeline (denoise, upscale, sharpen) on the region\nand shows it at 100% of the final resolution"
            if has else "Runs the full pipeline (denoise, upscale, sharpen) on the visible area\n"
                        "and shows it at 100% of the final resolution")
        if self.detail_active or self._loading or not self.src_size[0]:
            return
        if has:
            s = E.with_defaults(self._effective())
            _dn, _up, _q, waiting = self._flags(s, False)
            shown = self.viewer.patch is not None or getattr(self, "region", None) is not None
            if waiting and shown:
                # Manual denoise / upscale: keep the processed part on screen until you press Update preview
                self._cancel_live()
                self._set_pending(True)
                self.viewer.set_status("Region changed · press Update preview (F5) to process it")
                return
        self._drop_region()            # the old render may cover more (or less) than the new region
        self.viewer.set_status("")
        if has:
            self._roi_kick()
        else:
            self._view_changed()

    def _roi_kick(self):
        """Starts the region render if the current modes render automatically; otherwise says how."""
        if self._roi() is None:
            return
        s = E.with_defaults(self._effective())
        inc_dn, inc_up, inc_q, waiting = self._flags(s, False)
        if inc_dn or inc_up or inc_q:
            self.live_timer.start(120)
        elif waiting:
            self.viewer.set_status(f"Press Update preview (F5) to see {' and '.join(waiting)} in the region")

    # ------------------------------------------------------------------ theme / ui settings

    def apply_theme(self, name):
        t = themes.THEMES.get(name, themes.THEMES[themes.DEFAULT_THEME])
        self.colors = t
        app = QApplication.instance()
        app.setPalette(themes.palette(t))
        app.setStyleSheet(themes.stylesheet(t, self.ui.get("density", "compact") == "compact"))
        self.viewer.set_colors(t)
        self.activity.set_colors(t)
        self._update_ref_thumb()
        QTimer.singleShot(0, self.toolbar.fit)   # button sizes depend on the theme

    def set_ui(self, key, value):
        self.ui[key] = value
        if key in ("theme", "density"):
            self.apply_theme(self.ui["theme"])
        elif key in ("gpu_limit", "cpu_threads"):
            E.set_performance(self.ui["gpu_limit"] / 100, self.ui["cpu_threads"])
        elif key == "preview_res":
            self._request_preview()
        elif key == "auto_side":
            self.viewer.auto_side = value
            self.viewer.update()
        elif key == "easy_mode":
            if value == "auto" and self.pending:
                self._update_now()
        elif key in ("dn_mode", "up_mode"):
            for b in self.mode_btns[key]:
                b.setChecked(b.property("val") == value)
            if value == "interactive":
                self._view_changed()

    # ------------------------------------------------------------------ settings <-> widgets

    def _ref_value(self):
        d = self.ref.currentData()
        return "" if d in (None, "") else d

    def _ref_path(self, s=None):
        s = s or self.settings()
        ref = s["ref_image"] or (self.files[0] if self.files else "")
        if not ref:
            return ""
        return ref if os.path.isabs(ref) else os.path.join(s["input_dir"], ref)

    def settings(self):
        s = {k: self.sl[k].value() for k in self.sl}
        s.update({k: sw.isChecked() for k, sw in self.switch.items()})
        s.update(
            input_dir=self.in_dir.text(), output_dir=self.out_dir.text(), lut_path=self.lut.text(),
            ref_image=self._ref_value(),
            denoise_model="" if self.dn_model.currentText() in ("", NONE) else self.dn_model.currentText(),
            upscale_model="" if self.up_model.currentText() in ("", NONE) else self.up_model.currentText(),
            resize_mode=RESIZE_MODES[max(0, self.mode.currentIndex())],
            width=self.w_spin.value(), height=self.h_spin.value(), factor=self.factor.value(),
            upscale_multi=self.multi_chk.isChecked(), upscale_times=self.multi_n.value(),
            save_unscaled=self.save_unscaled.isChecked(),
            fp16=self.fp16.isChecked(), fmt=self.fmt.currentText(), bits=self.bits.currentText(),
            grain_type=self.grain_type.currentData() or "sensor",
            grain_model="" if self.grain_model.currentText() in ("", PROCEDURAL) else self.grain_model.currentText(),
            suffix=self.suffix.text(), skip_existing=self.skip.isChecked(),
            preview_image=self.img_combo.currentText(),
            lightmix=self.lm_panel.light_values("individual"), lightmix_grouped=self.lm_panel.light_values("group"),
            light_groups=self.lm_panel.group_values(), lightmix_mode=self.lm_panel.mode,
            cxr_post=self.lm_panel.post.isChecked(), cxr_denoised=self.lm_panel.dn.currentData() or "file",
            show_images=self.ui.get("show_images", True), show_cxr=self.ui.get("show_cxr", True),
            _lut_dirs=[LUT_DIR],
            tod_mode=self._tod_value(), fog_on=self.fog_chk.isChecked(), rays_on=self.rays_chk.isChecked(),
            fog_color=list(self.fog_sw.color or []), rays_color=list(self.rays_sw.color or []),
            rays_source=self.rays_src.currentData() or getattr(self, "_rays_src_pending", "auto"),
            rays_point=list(self._rays_point))
        return s

    def _apply_settings(self, data, keep_folders=False):
        s = E.with_defaults({k: v for k, v in data.items() if k != "ui"})
        for k, w in self.sl.items():
            w.set(s.get(k, w.default), emit=False)
        for k, sw in self.switch.items():
            sw.blockSignals(True)
            sw.setChecked(bool(s[k]))
            sw.blockSignals(False)
            for c in self.cards:
                if c.switch is sw:
                    c._switched(bool(s[k]))
        if not keep_folders:
            self.in_dir.set(s["input_dir"], emit=False)
            self.out_dir.set(s["output_dir"], emit=False)
        self.lut.set(s["lut_path"], emit=False)
        self._pending = {"ref_image": s.get("ref_image", ""), "denoise_model": s.get("denoise_model", ""),
                         "upscale_model": s.get("upscale_model", ""), "preview_image": s.get("preview_image", ""),
                         "grain_model": s.get("grain_model", "")}
        self.mode.setCurrentIndex(RESIZE_MODES.index(s["resize_mode"]) if s["resize_mode"] in RESIZE_MODES else 0)
        self.w_spin.setValue(int(s["width"]))
        self.h_spin.setValue(int(s["height"]))
        self.factor.setValue(float(s["factor"]))
        for wdg, v in ((self.multi_chk, s.get("upscale_multi")), (self.save_unscaled, s.get("save_unscaled"))):
            wdg.blockSignals(True)
            wdg.setChecked(bool(v))
            wdg.blockSignals(False)
        self.multi_n.blockSignals(True)
        self.multi_n.setValue(int(s.get("upscale_times", 1) or 1))
        self.multi_n.blockSignals(False)
        self._user_factor, self._user_w, self._user_h = float(s["factor"]), int(s["width"]), int(s["height"])
        self.fp16.setChecked(bool(s["fp16"]))
        self.fmt.setCurrentText(s["fmt"] if s["fmt"] in BITS_FOR else "png")
        self._fill_bits()
        self.bits.setCurrentText(str(s["bits"]) if str(s["bits"]) in BITS_FOR[self.fmt.currentText()] else
                                 BITS_FOR[self.fmt.currentText()][0])
        self.suffix.setText(s["suffix"])
        self.skip.setChecked(bool(s["skip_existing"]))
        i = self.grain_type.findData(s.get("grain_type", "sensor"))
        self.grain_type.setCurrentIndex(max(0, i))
        self.lm_panel.set_values(s.get("lightmix"), s.get("lightmix_grouped"), s.get("light_groups"),
                                 s.get("lightmix_mode") or self.ui.get("lm_mode", "individual"))
        p = self.lm_panel
        p.post.blockSignals(True)
        p.post.setChecked(bool(s.get("cxr_post", True)))
        p.post.blockSignals(False)
        p.dn.blockSignals(True)
        p.dn.setCurrentIndex(max(0, p.dn.findData(s.get("cxr_denoised", "file"))))
        p.dn.blockSignals(False)
        for w, v in ((self.fog_chk, s.get("fog_on")), (self.rays_chk, s.get("rays_on"))):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        self.tod_btns.get(s.get("tod_mode", "off"), self.tod_btns["off"]).setChecked(True)
        self.fog_sw.set_color(s.get("fog_color") or None)
        self.rays_sw.set_color(s.get("rays_color") or None)
        pt = s.get("rays_point") or []
        self._rays_point = list(pt) if len(pt) == 2 else []
        self._rays_src_pending = s.get("rays_source") or "auto"
        i = self.rays_src.findData(self._rays_src_pending)
        if i >= 0:
            self.rays_src.blockSignals(True)
            self.rays_src.setCurrentIndex(i)
            self.rays_src.blockSignals(False)
        self._sync_atmo_ui()
        self.viewer.auto_side = self.ui["auto_side"]
        self.viewer.split = float(self.ui.get("split", 0.5))
        self._set_view_mode(self.ui.get("view_mode", "split"))

    def _read_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        ui = data.setdefault("ui", {})
        if isinstance(ui, dict) and not ui.get("scale_default_303"):   # 3.0.3: Scale factor became the default
            ui["scale_default_303"] = True
            data["resize_mode"] = "scale_factor"
        return data

    def _save_settings(self):
        g = self.geometry()
        self.ui["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        self.ui["collapsed"] = [c.key for c in self.cards if c.collapsed]
        self.ui["split"] = self.viewer.split
        data = self.settings()
        data["ui"] = self.ui
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1)
        except OSError:
            pass

    def _restore(self, data, message):
        """Applies a full set of settings (preset, reset, undo) while keeping the folders."""
        self._loading = True
        self._apply_settings(dict(data, ui=self.ui), keep_folders=True)
        self._refresh_models()
        self._fill_ref()
        self._loading = False
        self._clear_all_errors()
        self.applied_easy = self._easy_values()
        self._set_pending(False)
        self._request_preview()
        self._drop_region()
        self._update_summary()
        if message:
            self.activity.flash(message)

    @staticmethod
    def _json_files(folder):
        try:
            names = [f for f in os.listdir(folder) if f.lower().endswith(".json")]
        except OSError:
            return []
        return sorted(names, key=str.lower)

    @staticmethod
    def _look_title(fname):
        """'05 Perfect Golden Hour.json' -> 'Perfect Golden Hour' (the number only sorts the list)."""
        return re.sub(r"^\d+\s+", "", os.path.splitext(fname)[0])

    def _menu_item(self, menu, path, title, tip="", translate=False):
        from PySide6.QtGui import QAction
        act = QAction((tr(title) if translate else title).replace("&", "&&"), menu)
        if tip:
            act.setToolTip(tr(tip))
        act.triggered.connect(lambda _c=False, p=path: self._load_preset(p))
        menu.addAction(act)

    def _fill_looks(self, menu):
        """Looks menu: None, the built-in looks, your looks, then save / folder."""
        menu.clear()
        none = menu.addAction("None (no look)", self._clear_look)
        none.setToolTip(tr("Look settings back to their defaults; adjust everything yourself"))
        menu.addSeparator()
        for f in self._json_files(BUILTIN_LOOK_DIR):
            path = os.path.join(BUILTIN_LOOK_DIR, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    desc = json.load(fh).get("_description", "")
            except (OSError, ValueError):
                desc = ""
            self._menu_item(menu, path, self._look_title(f), desc, translate=True)
        mine = self._json_files(LOOK_DIR)
        if mine:
            menu.addSeparator()
            for f in mine:
                self._menu_item(menu, os.path.join(LOOK_DIR, f), os.path.splitext(f)[0], "Your look")
        menu.addSeparator()
        menu.addAction("Save look…", self._save_look)
        menu.addAction("Open looks folder", lambda: self._open_dir(LOOK_DIR))
        menu.addAction("Load from another file…", lambda: self._load_preset(None))

    def _fill_presets(self, menu):
        """Presets menu: None, your presets, then save / folder."""
        menu.clear()
        none = menu.addAction("None (default settings)", self._reset_all)
        none.setToolTip(tr("Every setting back to its default (folders are kept)"))
        menu.addSeparator()
        files = self._json_files(PRESET_DIR)
        for f in files:
            self._menu_item(menu, os.path.join(PRESET_DIR, f), os.path.splitext(f)[0])
        if not files:
            empty = menu.addAction("No presets yet: save one")
            empty.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Save preset…", self._save_preset)
        menu.addAction("Open presets folder", lambda: self._open_dir(PRESET_DIR))
        menu.addAction("Load from another file…", lambda: self._load_preset(None))

    def _open_dir(self, folder):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _ask_name(self, title, label, folder, what):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, title, label)
        name = re.sub(r'[<>:"/\\|?*]+', "-", (name or "").strip()).strip(". ")
        if not ok or not name:
            return None
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + ".json")
        if os.path.exists(path) and QMessageBox.question(
                self, APP_NAME, f"A {what} called '{name}' already exists. Replace it?") != QMessageBox.Yes:
            return None
        return path

    def _rel_lut(self, path):
        """Built-in LUTs are stored relative to the app, so looks keep working when the app folder moves."""
        code_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            if path and os.path.commonpath([os.path.abspath(path), code_dir]) == code_dir:
                return os.path.relpath(path, code_dir).replace("\\", "/")
        except ValueError:
            pass
        return path

    def _save_look(self):
        path = self._ask_name("Save look", "Look name:", LOOK_DIR, "look")
        if not path:
            return
        cur = self.settings()
        data = {"_look": True}
        data.update({k: cur[k] for k in LOOK_PRESET_KEYS if k in cur})
        data["lut_path"] = self._rel_lut(data.get("lut_path", ""))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        self.activity.flash(f"Look saved: {os.path.splitext(os.path.basename(path))[0]}")

    def _save_preset(self):
        path = self._ask_name("Save preset", "Preset name:", PRESET_DIR, "preset")
        if not path:
            return
        data = {k: v for k, v in self.settings().items() if k not in ("input_dir", "output_dir", "preview_image")}
        data["lut_path"] = self._rel_lut(data.get("lut_path", ""))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        self.activity.flash(f"Preset saved: {os.path.splitext(os.path.basename(path))[0]}")

    def _load_preset(self, path=None):
        """Loads a look (changes only the look settings) or a preset (every setting)."""
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Load look or preset", PRESET_DIR if os.path.isdir(PRESET_DIR)
                                                  else APP_DIR, "Look or preset (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            assert isinstance(data, dict)
        except (OSError, ValueError, AssertionError) as e:
            QMessageBox.warning(self, APP_NAME, f"That file can't be read:\n{e}")
            return
        lp = data.get("lut_path") or ""
        if lp and not os.path.isabs(lp):     # LUTs that come with the app are stored relative to it
            data["lut_path"] = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), lp))
        name = self._look_title(os.path.basename(path)) if path.startswith(BUILTIN_LOOK_DIR) else \
            os.path.splitext(os.path.basename(path))[0]
        if data.get("_look"):
            cur = self.settings()
            for k in LOOK_PRESET_KEYS:              # a look replaces the whole look, nothing else
                cur[k] = E.DEFAULTS.get(k, cur.get(k))
            cur.update({k: v for k, v in data.items() if k in LOOK_PRESET_KEYS})
            self._restore(cur, "")
            self._set_current("look", path, name, builtin=path.startswith(BUILTIN_LOOK_DIR))
        else:
            self._restore(data, "")
            self._set_current("preset", path, name)

    def _choices(self, kind):
        """The looks (built-in, then yours) or presets, in menu order."""
        if kind == "look":
            return [os.path.join(BUILTIN_LOOK_DIR, f) for f in self._json_files(BUILTIN_LOOK_DIR)] + \
                   [os.path.join(LOOK_DIR, f) for f in self._json_files(LOOK_DIR)]
        return [os.path.join(PRESET_DIR, f) for f in self._json_files(PRESET_DIR)]

    def _set_current(self, kind, path, name, builtin=False, quiet=False):
        """Shows the chosen look / preset on its button and in the status bar."""
        self._current = (kind, path, name, builtin)
        items = self._choices(kind)
        pos = next((i for i, p in enumerate(items) if os.path.normcase(p) == os.path.normcase(path)), -1)
        shown = tr(name) if builtin else name
        short = shown if len(shown) <= 24 else shown[:23] + "…"
        btn = self.looks_btn if kind == "look" else self.presets_btn
        btn.setText(tr("Look: {0}" if kind == "look" else "Preset: {0}").replace("{0}", short.replace("&", "&&")))
        btn.setToolTip(shown + "\n\n" + tr(LOOKS_TIP if kind == "look" else PRESETS_TIP))
        btn.setProperty("primary", True)
        repolish(btn)
        if kind == "preset":            # a preset sets the whole look too
            self.looks_btn.setText(tr("Looks"))
            self.looks_btn.setProperty("primary", False)
            repolish(self.looks_btn)
        if not quiet:
            msg = tr("Look {0}/{1}: {2}  ·  ↑ ↓ for the previous / next one" if kind == "look" else
                     "Preset {0}/{1}: {2}  ·  ↑ ↓ for the previous / next one")
            self.activity.flash(msg.replace("{0}", str(pos + 1)).replace("{1}", str(len(items))).replace("{2}", shown),
                                seconds=4)

    def _clear_look(self):
        """No look: the look settings (mood, LUT, clarity / grain, atmosphere) go back to their defaults;
        denoise, upscale, sharpening and output stay as they are."""
        before = self.settings()
        prev = getattr(self, "_current", None)
        cur = dict(before)
        for k in LOOK_PRESET_KEYS:
            cur[k] = E.DEFAULTS.get(k, cur.get(k))
        self._restore(cur, "")
        self._current = None
        self.looks_btn.setText("Looks")
        self.looks_btn.setToolTip(LOOKS_TIP)
        self.looks_btn.setProperty("primary", False)
        repolish(self.looks_btn)
        def undo():
            self._restore(before, "Look restored")
            if prev and prev[0] == "look":
                self._set_current(*prev, quiet=True)
        self.activity.flash("No look: look settings back to their defaults", "Undo", undo, 10)

    def _clear_current(self):
        self._current = None
        for b, t, tip in ((self.looks_btn, "Looks", LOOKS_TIP), (self.presets_btn, "Presets", PRESETS_TIP)):
            b.setText(t)
            b.setToolTip(tip)
            b.setProperty("primary", False)
            repolish(b)

    def _cycle_choice(self, step):
        """Up / Down: the previous / next look (or preset, if a preset was the last thing chosen)."""
        cur = getattr(self, "_current", None)
        kind = cur[0] if cur else "look"
        items = self._choices(kind)
        if not items:
            return
        pos = -1
        if cur:
            pos = next((i for i, p in enumerate(items) if os.path.normcase(p) == os.path.normcase(cur[1])), -1)
        nxt = (pos + step) % len(items) if pos >= 0 else (0 if step > 0 else len(items) - 1)
        self._load_preset(items[nxt])

    def _reset_all(self):
        before = self.settings()
        defaults = {k: v for k, v in E.DEFAULTS.items() if k not in ("input_dir", "output_dir")}
        defaults.update(denoise_model="", upscale_model="", ref_image="", lut_path="")
        # light groups describe the scene (e.g. one per room): keep them, reset only their values
        defaults["light_groups"] = [dict(g, mult=1.0, color=None, on=None) for g in before.get("light_groups", [])]
        defaults["lightmix_mode"] = before.get("lightmix_mode", "individual")
        self._pending = {}
        self.dn_model.setCurrentIndex(0)
        self.up_model.setCurrentIndex(0)
        self._restore(defaults, "")
        self._clear_current()
        self.activity.flash("All settings reset to defaults", "Undo", lambda: self._restore(before, "Reset undone"), 12)

    def _apply_mood(self, name):
        vals = {k: E.DEFAULTS[k] for k in ("exposure", "temperature", "tint", "contrast", "saturation")}
        vals.update(MOODS[name])
        self._loading = True
        for k, v in vals.items():
            self.sl[k].set(v, emit=False)
        if name == "Neutral":
            self.sl["lut_strength"].set(1, emit=False)
            self.lut.set("", emit=False)
        elif not self.switch["look_on"].isChecked():
            self.switch["look_on"].setChecked(True)
        self._loading = False
        self._on_look_changed()

    # ------------------------------------------------------------------ signals

    def _connect_bus(self):
        b = self.bus
        b.source.connect(self._on_source)
        b.preview.connect(self._on_preview)
        b.patch.connect(self._on_patch)
        b.region.connect(self._on_region)
        b.thumb.connect(self._on_thumb)
        b.ref_thumb.connect(self._on_ref_thumb)
        b.task.connect(lambda tid, text, f, pr: self.activity.set(tid, text, f, pr))
        b.task_end.connect(self.activity.end)
        b.log.connect(self._log)
        b.error.connect(self._on_error)
        b.batch.connect(self._on_batch_progress)
        b.batch_done.connect(self._on_batch_done)
        b.detail.connect(self._on_detail)
        b.device.connect(lambda t: self.device.setText(t))

        self.in_dir.changed.connect(lambda _: (self._clear_error("input_dir"), self._on_input_changed()))
        self.out_dir.changed.connect(lambda _: (self._clear_error("output_dir"), self._clear_error("suffix"), self._scan_done(),
                                                self._update_summary()))
        self.lut.changed.connect(lambda _: (self._clear_error("lut_path"), self._on_look_changed()))
        self.lut.edit.textChanged.connect(lambda _: self._clear_error("lut_path"))
        for k, w in self.sl.items():
            w.changed.connect(lambda _=0, key=k: self._on_setting(key))
        for k, sw in self.switch.items():
            sw.toggled.connect(lambda on, key=k: self._on_switch(key, on))
        self.ref.currentIndexChanged.connect(lambda _: (self._clear_error("ref_image"), self._update_ref_thumb(),
                                                        self._on_look_changed()))
        self.mode.currentIndexChanged.connect(lambda _: (self._restore_user_size(), self._on_setting("resize")))
        self.factor.valueChanged.connect(lambda v: self.factor.isEnabled() and setattr(self, "_user_factor", v))
        self.w_spin.valueChanged.connect(lambda v: self.w_spin.isEnabled() and setattr(self, "_user_w", v))
        self.h_spin.valueChanged.connect(lambda v: self.h_spin.isEnabled() and setattr(self, "_user_h", v))
        for key, w in (("width", self.w_spin), ("height", self.h_spin), ("factor", self.factor)):
            w.valueChanged.connect(lambda _=0, k=key: (self._clear_error(k), self._on_setting("resize")))
        self.multi_chk.toggled.connect(lambda _v: self._on_setting("resize"))
        self.multi_n.valueChanged.connect(lambda _v: self._on_setting("resize"))
        self.save_unscaled.toggled.connect(lambda _v: (self._update_summary(), self._scan_done()))
        self.dn_model.wanted.connect(lambda n: self._want_model(n, select=True))
        self.up_model.wanted.connect(lambda n: self._want_model(n, select=True))
        b.model_dl.connect(self._on_model_dl)
        self.dn_model.currentIndexChanged.connect(lambda _: (self._clear_error("denoise_model"), self._on_setting("denoise")))
        self.up_model.currentIndexChanged.connect(lambda _: (self._clear_error("upscale_model"), self._on_setting("upscale")))
        self.grain_type.currentIndexChanged.connect(lambda _: self._on_setting("grain_type"))
        self.grain_model.currentIndexChanged.connect(lambda _: (self._clear_error("grain_model"), self._on_setting("grain_model")))
        self.fp16.toggled.connect(lambda _: self._on_setting("fp16"))
        self.suffix.textChanged.connect(lambda _: (self._clear_error("suffix"), self._scan_done()))
        self.fmt.currentIndexChanged.connect(lambda _: self._scan_done())
        p = self.lm_panel
        p.changed.connect(self._on_source_setting)
        p.post.toggled.connect(lambda _: self._on_source_setting())
        p.dn.currentIndexChanged.connect(lambda _: self._on_source_setting())
        p.save_btn.clicked.connect(self._lm_save)
        p.load_btn.clicked.connect(self._lm_load)
        p.closed.connect(lambda: self.lm_btn.setChecked(False))
        p.modeChanged.connect(lambda m: (self.ui.__setitem__("lm_mode", m), self._on_source_setting()))
        b.lights.connect(self._on_lights)
        b.depth_done.connect(self._on_depth_done)
        b.imported.connect(self._on_imported)
        self._drops = [FileDrop(self.viewer, self._import_files), FileDrop(self.card_of["ref_image"], self._drop_reference)]
        self.show_img.toggled.connect(lambda v: self._set_show("show_images", v))
        self.show_cxr.toggled.connect(lambda v: self._set_show("show_cxr", v))
        self.bits.currentTextChanged.connect(lambda _: self._clear_error("bits"))

    def _on_setting(self, key):
        """Any setting changed. Quick edits follow the Automatic / Manual setting; denoise and upscale
        follow their own Interactive / Manual choice."""
        if self._loading:
            return
        if key in SOURCE_KEYS:
            self._on_source_setting()
            return
        if key in LOOK_KEYS:
            self._on_look_changed()
            return
        self._update_summary()
        if key in SHARPEN_KEYS:
            if self.ui["easy_mode"] == "auto":
                self.applied_easy = self._easy_values()
                self._quick_changed()
            else:
                self._set_pending(True)
        else:
            self._ai_changed()

    def _on_switch(self, key, on):
        if key == "denoise_on":
            self._clear_error("denoise_model")
            if on and self.sl["denoise_strength"].value() == 0:
                self.sl["denoise_strength"].set(0.7, emit=False)
        if key == "resize_on":
            self._clear_error("upscale_model")
        if key == "match_on":
            self._clear_error("ref_image")
        if key == "look_on":
            self._clear_error("lut_path")
        self._on_setting(key)

    # ------------------------------------------------------------------ Corona LightMix

    def _on_source_setting(self):
        """LightMix changed: the image itself changes, so the zoomed-in AI render is outdated."""
        if self._loading:
            return
        if self.ui["easy_mode"] == "manual":
            self._set_pending(True)
            return
        if self.detail_active:
            self._leave_detail(refresh=False)
        self.applied_easy = self._easy_values()
        self._drop_region()
        self._request_preview()
        self._schedule_live(True)

    def _lm_save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save lights", APP_DIR, "Lights preset (*.lights.json)")
        if path:
            if not path.endswith(".json"):
                path += ".lights.json"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"lightmix": self.lm_panel.light_values("individual"),
                           "lightmix_grouped": self.lm_panel.light_values("group"),
                           "light_groups": self.lm_panel.group_values(), "lightmix_mode": self.lm_panel.mode},
                          fh, indent=1)
            self.activity.flash(f"Lights saved: {os.path.basename(path)}")

    def _lm_load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load lights", APP_DIR, "Lights preset (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            lm = data.get("lightmix", data)
            assert isinstance(lm, dict)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"That lights preset can't be read:\n{e}")
            return
        groups = data.get("light_groups", self.lm_panel.group_values())
        self.lm_panel.set_values(lm, data.get("lightmix_grouped", {}), groups, data.get("lightmix_mode"))
        missing = [k for k in lm if k not in self.lm_panel.names]
        self._on_source_setting()
        self.activity.flash(f"Lights loaded: {os.path.basename(path)}"
                            + (f" · not in these files: {', '.join(missing)}" if missing else ""))

    def _scan_lights(self, folder, files):
        self.lights_token = getattr(self, "lights_token", 0) + 1
        token = self.lights_token
        cxr = [os.path.join(folder, f) for f in E.list_images(folder) if f.lower().endswith(".cxr")]

        def work():
            import corona
            try:
                names, count, errors = corona.scan_lights(cxr)
            except Exception as e:
                names, count, errors = [], {}, [("", str(e))]
            self.bus.lights.emit(token, (names, count, len(cxr), errors))

        threading.Thread(target=work, daemon=True).start()

    def _on_lights(self, token, data):
        if token != getattr(self, "lights_token", 0):
            return
        names, count, n_cxr, errors = data
        self.lm_panel.set_lights(names, count, n_cxr, errors)
        self._update_rays_sources(names)
        self.n_cxr = n_cxr
        self._update_lm_visibility()

    def _update_lm_visibility(self):
        has = getattr(self, "n_cxr", 0) > 0 and self.ui.get("show_cxr", True)
        self.toolbar.set_wanted(self.lm_btn, has)
        self.toolbar.fit()
        self.lm_btn.blockSignals(True)
        self.lm_btn.setChecked(has and self.ui.get("lm_open", False))
        self.lm_btn.blockSignals(False)
        self._show_lm_panel(has and self.ui.get("lm_open", False))

    def _toggle_lm_panel(self, on):
        self.ui["lm_open"] = bool(on)
        self._show_lm_panel(on)

    def _show_lm_panel(self, on):
        if on == self.lm_panel.isVisible():
            return
        if not on:
            self.ui["lm_width"] = self.lm_panel.width()
        self.lm_panel.setVisible(on)
        if on:
            self._fit_split()

    def _keep_on_screen(self):
        """Never larger than the screen (e.g. after moving to a smaller monitor or a scaling change)."""
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr is None:
            return
        g = self.frameGeometry()
        w, h = min(g.width(), scr.width()), min(g.height(), scr.height())
        x = min(max(g.x(), scr.x()), scr.right() - w + 1)
        y = min(max(g.y(), scr.y()), scr.bottom() - h + 1)
        if (w, h) != (g.width(), g.height()) or (x, y) != (g.x(), g.y()):
            self.resize(w - (g.width() - self.width()), h - (g.height() - self.height()))
            self.move(x, y)
        self._fit_split()

    def _fit_split(self):
        """Share the window between sidebar, preview and LightMix panel without pushing anything off screen."""
        sp = self.main_split
        total = sp.width() - sp.handleWidth() * 2
        side, center, _ = sp.sizes()
        pmin = self.lm_panel.minimumSizeHint().width() if self.lm_panel.isVisible() else 0
        pw = max(pmin, min(int(self.ui.get("lm_width", 380)), 460)) if self.lm_panel.isVisible() else 0
        cmin = max(self.toolbar.minimumWidth(), 360)
        smin = sp.widget(0).minimumWidth()
        side = max(smin, min(side, total - cmin - pw))
        if side + cmin + pw > total and pw:
            pw = max(pmin, total - side - cmin)
        sp.setSizes([side, max(cmin, total - side - pw), pw])

    def _set_show(self, key, value):
        if not self.show_img.isChecked() and not self.show_cxr.isChecked():
            # keep at least one type visible
            (self.show_cxr if key == "show_images" else self.show_img).setChecked(True)
        self.ui[key] = bool(value)
        self._update_lm_visibility()
        self._on_input_changed()

    # ------------------------------------------------------------------ sidebar cards: order and visibility

    def _apply_card_layout(self):
        order = [k for k in self.ui.get("card_order", []) if any(c.key == k for c in self.cards)]
        order += [c.key for c in self.cards if c.key not in order]
        by_key = {c.key: c for c in self.cards}
        for c in self.cards:
            self.card_layout.removeWidget(c)
        for i, k in enumerate(order):
            self.card_layout.insertWidget(i, by_key[k])
        self.card_column.cards = [by_key[k] for k in order]
        hidden = set(self.ui.get("hidden_cards", []))
        for c in self.cards:
            c.setVisible(c.key not in hidden)

    def _reorder_cards(self, order):
        self.ui["card_order"] = order
        self._apply_card_layout()

    def set_card_visible(self, key, on):
        hidden = set(self.ui.get("hidden_cards", []))
        (hidden.discard if on else hidden.add)(key)
        self.ui["hidden_cards"] = sorted(hidden)
        self._apply_card_layout()

    def _fill_bits(self, *_):
        fmt = self.fmt.currentText() or "png"
        cur = self.bits.currentText()
        self.bits.blockSignals(True)
        self.bits.clear()
        self.bits.addItems(BITS_FOR.get(fmt, ["8"]))
        self.bits.setCurrentText(cur if cur in BITS_FOR.get(fmt, []) else BITS_FOR.get(fmt, ["8"])[0])
        self.bits.blockSignals(False)
        self._scan_done()

    def _log(self, msg):
        if msg.startswith("ERROR "):
            msg = tr("ERROR") + " " + tr(msg[6:])
        else:
            msg = tr(msg)
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + msg)

    def change_language(self, code):
        """Switches the interface language in place: every text is put back from its English original, so the
        preview, zoom, region, running jobs and open dialogs all stay as they are."""
        self.ui["language"] = code
        set_language(code)
        i18n.retranslate(QApplication.instance())
        wrap_checkboxes(self.sidebar_root)          # longer translations may need to wrap now
        if hasattr(self, "toolbar"):
            self.toolbar.fit()
            self._update_btn_text()
        self._update_summary()
        self._sync_atmo_ui()
        cur = getattr(self, "_current", None)
        if cur:
            self._set_current(*cur, quiet=True)
        self.viewer.update()
        self._save_settings()
        self.activity.flash(f"Language: {dict(i18n.LANGUAGES).get(code, code)}", seconds=3)

    def reload_interface(self, open_settings=False):
        """Rebuilds the whole window in the current language, keeping settings, folder, image and caches."""
        if self.job_kind == "batch":
            QMessageBox.information(self, APP_NAME, "The language will change when the batch has finished "
                                                    "(or the next time you start the app).")
            self._save_settings()
            return
        if self.job is not None:
            self.job.cancel()
        self._cancel_live()
        self._save_settings()
        set_language(self.ui.get("language", "en"))
        geo, maxed = self.geometry(), self.isMaximized()
        new = MainWindow(reuse=self)
        new.setGeometry(geo)
        new.showMaximized() if maxed else new.show()
        global _MAIN
        _MAIN = new
        self.thumb_token += 1
        self.hide()
        self.deleteLater()
        if open_settings:
            QTimer.singleShot(150, lambda: SettingsDialog(new).exec())

    def restart(self):
        """Save and start a new copy of the app (used to switch the language)."""
        self._save_settings()
        from PySide6.QtCore import QProcess
        launcher = os.path.join(APP_DIR, "RenderBatch.exe")
        if os.name == "nt" and os.path.exists(launcher):          # portable build
            QProcess.startDetached(launcher, [], APP_DIR)
        elif getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, sys.argv[1:])
        else:
            start = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start.py")
            QProcess.startDetached(sys.executable, ["-E", "-s", start] if os.path.exists(start) else sys.argv)
        os._exit(0)

    # ------------------------------------------------------------------ errors: highlight the setting at fault

    def _on_error(self, msg, fieldname, quiet):
        if E.is_gpu_lost(msg) and E.GPU_LOST_MSG not in msg:   # raw DirectML dump → plain explanation
            msg = msg.split(":")[0] + ": " + E.GPU_LOST_MSG if msg.split(":")[0].endswith("failed") else E.GPU_LOST_MSG
        self._log("ERROR " + msg)
        if fieldname:
            self._show_error(fieldname, msg)
            self.activity.flash("⚠ " + msg, seconds=8)
        else:
            self.activity.flash("⚠ " + msg, seconds=8)
            if not quiet and self.job_kind is None:
                QMessageBox.warning(self, APP_NAME, msg)

    def _show_error(self, fieldname, msg):
        self.errors[fieldname] = msg
        w = self.field_widget.get(fieldname)
        card = self.card_of.get(fieldname)
        if w is not None:
            w.setProperty("error", True)
            w.setToolTip(msg)
            repolish(w)
        if card is not None:
            card.show_error(msg)
            QTimer.singleShot(50, lambda: self.scroll.ensureWidgetVisible(w or card, 20, 40))
            if w is not None:
                w.setFocus()
        else:
            self.activity.flash("⚠  " + msg, "Open Settings", lambda: SettingsDialog(self).exec(), 12)

    def _clear_error(self, fieldname):
        if fieldname not in self.errors:
            return
        self.errors.pop(fieldname)
        w = self.field_widget.get(fieldname)
        if w is not None:
            w.setProperty("error", False)
            w.setToolTip("")
            repolish(w)
        card = self.card_of.get(fieldname)
        if card is not None and not any(self.card_of.get(f) is card for f in self.errors):
            card.show_error("")

    def _clear_all_errors(self):
        for f in list(self.errors):
            self._clear_error(f)

    def _validate(self, need_images=True):
        """Checks settings before a job. Returns (field, message) for the first problem, or None."""
        s = E.with_defaults(self.settings())
        if not s["input_dir"] or not os.path.isdir(s["input_dir"]):
            return "input_dir", "The input folder doesn't exist. Choose the folder with your renders."
        if need_images and not self.files:
            return "input_dir", "No images found in the input folder."
        if s["look_on"] and s["lut_path"]:
            if not os.path.isfile(s["lut_path"]):
                return "lut_path", "The LUT file doesn't exist."
            try:
                E.load_cube(s["lut_path"])
            except Exception as e:
                return "lut_path", f"LUT can't be used: {e}"
        if s["match_on"] and s["cm_strength"] > 0:
            rp = self._ref_path(s)
            if not rp or not os.path.isfile(rp):
                return "ref_image", "The reference image doesn't exist. Choose another image or switch Match colors off."
        if s["denoise_on"] and s["denoise_strength"] > 0:
            if not s["denoise_model"]:
                return "denoise_model", "Denoise is on but no model is selected. Pick a model or switch Denoise off."
            if s["denoise_model"] not in E.list_models(E.model_dir(MODELS_DIR, "denoise")):
                return "denoise_model", f"Model '{s['denoise_model']}' isn't in the models\\denoise folder."
        if s["resize_on"] and self.src_size[0]:
            big = E.size_problem(self.src_size[0], self.src_size[1], s)
            if big:
                return ("factor" if s["resize_mode"] == "scale_factor" else "width"), big
        if s["resize_on"] and s["upscale_model"] and \
                s["upscale_model"] not in E.list_models(E.model_dir(MODELS_DIR, "upscale")):
            return "upscale_model", f"Model '{s['upscale_model']}' isn't in the models\\upscale folder."
        if s["texture_on"] and s["grain_model"] and s["grain_amount"] > 0 and \
                s["grain_model"] not in E.list_models(E.model_dir(MODELS_DIR, "grain")):
            return "grain_model", f"Model '{s['grain_model']}' isn't in the models\\grain folder."
        out = s["output_dir"] or os.path.join(s["input_dir"], "processed")
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(s["input_dir"])) \
                and not s["suffix"]:
            return "suffix", "The output folder is the input folder and the suffix is empty, so the originals " \
                             "would be overwritten. Add a suffix or pick another output folder."
        return None

    def _check_or_highlight(self, need_images=True):
        problem = self._validate(need_images)
        if problem:
            self._show_error(*problem)
            self.activity.flash("⚠ " + problem[1], seconds=8)
            return False
        return True

    # ------------------------------------------------------------------ folder / images

    # ------------------------------------------------------------------ automatic model conversion

    def _setup_model_watch(self):
        self.converting = False
        self._told_missing = set()
        self.model_watch = QFileSystemWatcher(self)
        for kind in E.MODEL_KINDS:
            d = E.model_dir(MODELS_DIR, kind)
            os.makedirs(d, exist_ok=True)
            self.model_watch.addPath(d)
        self.model_watch.addPath(MODELS_DIR)
        self.model_timer = QTimer(self)
        self.model_timer.setSingleShot(True)
        self.model_timer.timeout.connect(self._check_models)
        self.model_watch.directoryChanged.connect(lambda _: self.model_timer.start(1500))
        self.bus.convert.connect(self._on_convert_event)
        QTimer.singleShot(800, self._check_models)
        QTimer.singleShot(4000, self._auto_models)
        self.setAcceptDrops(True)                       # update .zip files dropped anywhere on the window
        QTimer.singleShot(3000, self._check_downloads)

    @staticmethod
    def converter_installed():
        return not getattr(sys, "frozen", False) and all(
            importlib.util.find_spec(m) is not None for m in ("torch", "spandrel", "onnx"))

    def _check_models(self):
        """New files in the model folders: sort loose ones, convert .pth / .safetensors, refresh the lists."""
        if self.converting:
            return
        try:
            E.organize_models(MODELS_DIR)
            import convert_model
            todo = convert_model.pending(MODELS_DIR)
            loose = [f for f in os.listdir(MODELS_DIR) if f.lower().endswith(convert_model.EXTS)]
        except Exception:
            todo, loose = [], []
        self._refresh_models()
        if loose:
            self.activity.flash(f"⚠ Put .pth / .safetensors files in models\\denoise or models\\upscale "
                                f"(found {len(loose)} loose in models\\)", seconds=10)
        if not todo:
            return
        if not self.converter_installed():
            key = tuple(sorted(p for p, _ in todo))
            busy = getattr(self, "_model_ctl", None) is not None or getattr(self, "_conv_dl", False)
            if key not in self._told_missing and not busy:
                self._told_missing.add(key)
                q = tr("Found {0} model file(s) (.pth / .safetensors). To use them the app needs its model converter "
                       "(a one-time download of about 120 MB). Download it now?").format(len(todo))
                if QMessageBox.question(self, APP_NAME, q) == QMessageBox.Yes:
                    self._install_converter()
            return
        self._start_conversion()

    def _start_conversion(self):
        self.converting = True
        self._conv_cancelled = False
        self._conv_t0 = time.time()
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "convert_model.py")
        cmd = [sys.executable, "-E", "-s", script, MODELS_DIR, str(self.ui["cpu_threads"])]
        flags = 0
        if os.name == "nt":
            flags = 0x08000000 | 0x00004000  # no console window, below-normal priority
        bus = self.bus
        self.activity.set("convert", "Converting new models…", -1, 2)

        def work():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="replace", creationflags=flags)
                self._conv_proc = proc
                for line in proc.stdout:
                    try:
                        bus.convert.emit(json.loads(line))
                    except ValueError:
                        pass
                err = proc.stderr.read()
                proc.wait()
                if proc.returncode not in (0, None) and not self._conv_cancelled:
                    bus.convert.emit(dict(event="fatal", message="The model converter stopped unexpectedly "
                                                                 f"(code {proc.returncode}). It may have run out of memory.",
                                          detail=err[-1500:]))
            except Exception as e:
                bus.convert.emit(dict(event="fatal", message=f"The model converter couldn't start: {e}"))
            bus.convert.emit(dict(event="finished"))

        threading.Thread(target=work, daemon=True).start()
        self._conv = {"n": 1, "i": 0, "done": [], "errors": []}

    def _cleanup_cancelled_conversion(self):
        """Removes the half-written model of a cancelled conversion (a finished one has its .json next to it);
        the original .pth / .safetensors stays, so it converts again the next time the app starts."""
        t0 = getattr(self, "_conv_t0", time.time())
        for kind in ("denoise", "upscale", "grain"):
            d = os.path.join(MODELS_DIR, kind)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if not f.endswith(".onnx"):
                    continue
                path = os.path.join(d, f)
                base = path[:-len("_fp16.onnx")] if f.endswith("_fp16.onnx") else path[:-5]
                try:
                    if os.path.getmtime(path) >= t0 - 1 and not os.path.exists(base + ".json"):
                        os.remove(path)
                except OSError:
                    pass
        self._log("Model conversion cancelled; the remaining models will be converted the next time the app starts")
        self._refresh_models()

    def _on_convert_event(self, ev):
        c = getattr(self, "_conv", {"n": 1, "i": 0, "done": [], "errors": []})
        kind = ev.get("event")
        if kind == "count":
            c["n"] = max(1, ev["n"])
        elif kind == "start":
            c["i"] = ev["i"]
            self.activity.set("convert", f"Converting model {ev['i'] + 1}/{ev['n']}: {ev['file']}", ev["i"] / c["n"], 2)
        elif kind == "stage":
            stages = {"Reading model": 0.1, "Checking the converted model": 0.8}
            f = stages.get(ev["text"], 0.4)
            self.activity.set("convert", f"Converting model {c['i'] + 1}/{c['n']}: {ev['file']} · {ev['text'].lower()}",
                              (c["i"] + f) / c["n"], 2)
        elif kind == "done":
            where = "Denoise" if ev["kind"] == "denoise" else "Upscale"
            note = f" (it's a {ev['scale']}x model, so it was moved to {where})" if ev.get("moved") else ""
            self._log(f"Converted {ev['file']} → {ev['name']} [{ev['arch']} {ev['scale']}x, {where}]"
                      f"{'' if ev.get('fp16') else ' (full precision only)'}; original removed{note}")
            c["done"].append((ev["name"], where, note))
        elif kind in ("error", "fatal"):
            self._log(f"Model conversion failed: {ev.get('file', '')} {ev['message']}")
            if ev.get("detail"):
                self._log(ev["detail"])
            c["errors"].append(f"{ev.get('file', 'converter')}: {ev['message']}")
        elif kind == "finished":
            self.converting = False
            self.activity.end("convert")
            if getattr(self, "_conv_cancelled", False):
                self._cleanup_cancelled_conversion()
            self._refresh_models()
            if c["done"]:
                names = ", ".join(f"{n} ({w})" for n, w, _ in c["done"])
                moved = "".join(note for _, _, note in c["done"])
                self.activity.flash(f"Converted {names}; original file removed{moved}", seconds=12)
            if c["errors"]:
                msg = "\n\n".join(c["errors"])
                QMessageBox.warning(self, APP_NAME, "Some models couldn't be converted and were left as they are:\n\n"
                                    + msg + "\n\nThe app won't retry a file unless it changes.")
            self._update_summary()
            QTimer.singleShot(500, self._check_models)  # files may have arrived meanwhile

    def _refresh_models(self):
        gm = E.list_models(E.model_dir(MODELS_DIR, "grain"))
        want = self._pending.get("grain_model") if "grain_model" in self._pending else self.grain_model.currentText()
        self.grain_model.blockSignals(True)
        self.grain_model.clear()
        self.grain_model.addItems([PROCEDURAL] + gm)
        self.grain_model.setCurrentText(want if want in gm else PROCEDURAL)
        self.grain_model.blockSignals(False)
        import model_store
        for cb, key, hint, kind in ((self.dn_model, "denoise_model", "scunet", "denoise"),
                                    (self.up_model, "upscale_model", "span", "upscale")):
            models = E.list_models(E.model_dir(MODELS_DIR, kind))
            want = self._pending.get(key) if key in self._pending else cb.currentText()
            ready = getattr(self, "_select_when_ready", {})
            if ready.get(kind) in models:            # a model you picked to download is ready now
                want = ready.pop(kind)
            if want not in models:
                want = next((m for m in models if hint in m.lower()), None) or NONE
            cb.set_models(models, model_store.missing(MODELS_DIR, kind, models), want)
        self._clean_model_status()

    def _fill_ref(self):
        want = self._pending.get("ref_image") if "ref_image" in self._pending else self._ref_value()
        self.ref.blockSignals(True)
        self.ref.clear()
        self.ref.addItem(FIRST_IMAGE, "")
        for f in self.files:
            self.ref.addItem(f, f)
        if want and os.path.isabs(want):
            self.ref.addItem("📁 " + os.path.basename(want), want)
            self.ref.setItemData(self.ref.count() - 1, want, Qt.ToolTipRole)
        i = self.ref.findData(want) if want else 0
        self.ref.setCurrentIndex(max(0, i))
        self.ref.blockSignals(False)
        self._update_ref_thumb()

    def _browse_ref(self):
        start = os.path.dirname(self._ref_path()) if self._ref_path() else self.in_dir.text()
        path, _ = QFileDialog.getOpenFileName(self, "Choose a reference image", start, IMG_FILTER)
        if path:
            self._set_reference(path)

    def _set_reference(self, path):
        path = os.path.normpath(path)
        folder = os.path.normcase(os.path.abspath(self.in_dir.text() or "."))
        if os.path.normcase(os.path.dirname(os.path.abspath(path))) == folder and os.path.basename(path) in self.files:
            value = os.path.basename(path)
        else:
            value = path
        self._pending["ref_image"] = value
        self._fill_ref()
        self._pending.pop("ref_image", None)
        if not self.switch["match_on"].isChecked():
            self.switch["match_on"].setChecked(True)
        self._clear_error("ref_image")
        self._on_look_changed()

    def _update_ref_thumb(self):
        if not hasattr(self, "ref_thumb"):
            return
        path = self._ref_path() if self.files or self._ref_value() else ""
        self.ref_thumb.setStyleSheet(f"background: {self.colors['card2']}; border-radius: 6px; "
                                     f"color: {self.colors['muted']};")
        if not path:
            self.ref_thumb.setPixmap(QPixmap())
            self.ref_thumb.setText("no image")
            return
        self.ref_thumb.setText("…")
        bus = self.bus

        def work():
            try:
                bus.ref_thumb.emit(path, E.load_thumbnail(path, 200))
            except Exception:
                bus.ref_thumb.emit(path, None)

        threading.Thread(target=work, daemon=True).start()

    def _on_ref_thumb(self, path, arr):
        if path != self._ref_path():
            return
        if arr is None:
            self.ref_thumb.setPixmap(QPixmap())
            self.ref_thumb.setText("can't read")
            return
        pm = QPixmap.fromImage(np_to_qimage(arr)).scaled(96, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.ref_thumb.setText("")
        self.ref_thumb.setPixmap(pm)

    def _on_input_changed(self):
        if self._loading:
            return
        folder = self.in_dir.text()
        self.files = E.list_images(folder, {"show_images": self.ui.get("show_images", True),
                                            "show_cxr": self.ui.get("show_cxr", True)})
        self._fill_ref()
        want = self._pending.get("preview_image")
        self._pending = {}
        self.img_combo.blockSignals(True)
        self.img_combo.clear()
        self.img_combo.addItems(self.files)
        self.img_combo.blockSignals(False)
        self.film.clear()
        self.thumb_items = {}
        self._drop_region()
        if not self.files and folder and E.list_images(folder):
            self.viewer.clear("Nothing to show: turn on Images or CXR at the bottom of the window")
            self.count_lbl.setText("All files are hidden by the Show filter")
            self._scan_lights(folder, [])
            self._update_summary()
            return
        if not self.files:
            self.viewer.clear("No images found in this folder" if folder else "Choose an input folder to start")
            self.count_lbl.setText("No images found" if folder else "No folder selected")
            self._scan_lights(folder, [])
            self.subtitle.setText("Batch post-processing for renders")
            if folder:
                self._show_error("input_dir", "No images found in this folder." if os.path.isdir(folder)
                                 else "This folder doesn't exist.")
            self._update_summary()
            return
        self.subtitle.setText(folder)
        self.count_lbl.setText(f"{len(self.files)} images")
        tw, th = D("thumb")
        placeholder = QPixmap(tw, th)
        placeholder.fill(QColor(self.colors["card2"]))
        self.thumb_base = {}
        for idx, name in enumerate(self.files):
            it = FilmItem(QIcon(placeholder), self._short(name))
            it.setData(Qt.UserRole, name)
            it.setData(Qt.UserRole + 1, "")
            it.setData(Qt.UserRole + 2, idx)
            it.setToolTip(name)
            it.setSizeHint(QSize(tw + 14, th + 30))
            self.film.addItem(it)
            self.thumb_items[name] = it
            self.thumb_base[name] = placeholder
        self._scan_done()
        self._load_thumbs(folder, list(self.files))
        self._scan_lights(folder, list(self.files))
        self.img_combo.blockSignals(True)
        self.img_combo.setCurrentText(want if want in self.files else self.files[0])
        self.img_combo.blockSignals(False)
        self._on_image_selected(self.img_combo.currentText())
        self._update_summary()

    @staticmethod
    def _short(name, n=20):
        return name if len(name) <= n else name[:n - 1] + "…"

    def _load_thumbs(self, folder, files):
        self.thumb_token += 1
        token = self.thumb_token

        def one(name):
            if token != self.thumb_token:
                return
            E.lower_thread_priority()
            try:
                img, _cached = E.thumbnail_cached(os.path.join(folder, name), THUMB_DIR)
                self.bus.thumb.emit(name, img)
            except Exception:
                pass
            with lock:
                done[0] += 1
                if done[0] % 4 == 0 or done[0] == len(files):
                    self.bus.task.emit("thumbs", f"Loading thumbnails {done[0]}/{len(files)}",
                                       done[0] / max(1, len(files)), 0)

        lock = threading.Lock()
        done = [0]

        def work():
            # thumbnails already in the cache appear at once; new ones are made on several cores
            from concurrent.futures import ThreadPoolExecutor
            n = max(2, min(6, (os.cpu_count() or 4) // 2))
            with ThreadPoolExecutor(n) as pool:
                list(pool.map(one, files))
            self.bus.task_end.emit("thumbs")
            E.prune_thumbnail_cache(THUMB_DIR)

        threading.Thread(target=work, daemon=True).start()

    def _on_thumb(self, name, arr):
        it = self.thumb_items.get(name)
        if it is None:
            return
        tw, th = D("thumb")
        pm = QPixmap.fromImage(np_to_qimage(arr)).scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QPixmap(tw, th)
        canvas.fill(QColor(self.colors["card2"]))
        p = QPainter(canvas)
        p.drawPixmap((tw - pm.width()) // 2, (th - pm.height()) // 2, pm)
        p.end()
        self.thumb_base[name] = canvas
        self._paint_film(name)

    # ------------------------------------------------------------------ filmstrip status (done / in progress)

    def _out_path(self, name, s=None):
        s = s or self.settings()
        out_dir = s["output_dir"] or os.path.join(s["input_dir"], "processed")
        return os.path.join(out_dir, E.output_name(name, E.with_defaults(s)))

    def _scan_done(self):
        """Marks images whose output file already exists (green) and sorts them first."""
        if not getattr(self, "thumb_items", None) or self.job_kind == "batch":
            return
        s = self.settings()
        for name in self.files:
            self._set_film_state(name, "done" if os.path.exists(self._out_path(name, s)) else "", sort=False)
        self._sort_film()

    def _paint_film(self, name):
        it = self.thumb_items.get(name)
        base = getattr(self, "thumb_base", {}).get(name)
        if it is None or base is None:
            return
        state = it.data(Qt.UserRole + 1) or ""
        pm = QPixmap(base)
        if state in STATE_COLORS:
            p = QPainter(pm)          # thin, soft outline + a small status dot in the corner
            p.setRenderHint(QPainter.Antialiasing)
            col = QColor(STATE_COLORS[state])
            col.setAlpha(170)
            pen = QPen(col, 2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, pm.width() - 2, pm.height() - 2), 3, 3)
            col.setAlpha(235)
            p.setPen(QPen(QColor(0, 0, 0, 140), 1.5))
            p.setBrush(col)
            p.drawEllipse(QPointF(pm.width() - 9, 9), 4, 4)
            p.end()
        it.setIcon(QIcon(pm))
        tips = {"done": "processed", "current": "processing now", "error": "failed (see Activity log)"}
        it.setToolTip(name + (f"  ·  {tips[state]}" if state in tips else ""))

    def _set_film_state(self, name, state, sort=True):
        it = self.thumb_items.get(name)
        if it is None or (it.data(Qt.UserRole + 1) or "") == state:
            return
        it.setData(Qt.UserRole + 1, state)
        self._paint_film(name)
        if sort:
            self._sort_film()

    def _manual_order(self):
        return self.ui.get("film_order", {}).get(os.path.normcase(os.path.abspath(self.in_dir.text() or ".")))

    def _sort_film(self):
        """Automatic order (processed first, then by name) unless you arranged the strip yourself."""
        cur = self.film.currentItem()
        sel = {it.data(Qt.UserRole) for it in self.film.selectedItems()}
        order = self._manual_order()
        self.film.blockSignals(True)
        if order:
            pos = {n: i for i, n in enumerate(order)}
            items = [self.film.takeItem(0) for _ in range(self.film.count())]
            items.sort(key=lambda it: (pos.get(it.data(Qt.UserRole), len(pos)), it.data(Qt.UserRole + 2) or 0))
            for it in items:
                self.film.addItem(it)
                it.setSelected(it.data(Qt.UserRole) in sel)
        else:
            self.film.sortItems(Qt.AscendingOrder)
        if cur is not None:
            self.film.setCurrentItem(cur, QItemSelectionModel.NoUpdate)
        self.film.blockSignals(False)
        self.order_btn.setVisible(bool(order))
        self._sync_combo_order()

    def _sync_combo_order(self):
        names = self._film_names()
        if [self.img_combo.itemText(i) for i in range(self.img_combo.count())] == names:
            return
        cur = self.img_combo.currentText()
        self.img_combo.blockSignals(True)
        self.img_combo.clear()
        self.img_combo.addItems(names)
        self.img_combo.setCurrentText(cur)
        self.img_combo.blockSignals(False)

    def _film_names(self, selected_only=False):
        items = [self.film.item(i) for i in range(self.film.count())]
        if selected_only:
            items = [it for it in items if it.isSelected()]
        return [it.data(Qt.UserRole) for it in items]

    def _on_film_reordered(self, names):
        folder = os.path.normcase(os.path.abspath(self.in_dir.text() or "."))
        self.ui.setdefault("film_order", {})[folder] = names
        self.order_btn.show()
        self._sync_combo_order()
        self._on_film_selection()

    def _reset_film_order(self):
        folder = os.path.normcase(os.path.abspath(self.in_dir.text() or "."))
        self.ui.get("film_order", {}).pop(folder, None)
        self._sort_film()

    def _on_film_selection(self):
        if not hasattr(self, "sel_btn"):
            return
        n = len(self.film.selectedItems()) if hasattr(self, "film") else 0
        self.sel_btn.setText(f"Process selected ({n})" if n else "Process selected")
        self.sel_btn.setEnabled(n > 0 and self.job is None)

    # ------------------------------------------------------------------ files dropped on the window

    def _import_files(self, paths):
        """Images / .cxr dropped on the preview or the strip: copied into the input folder and shown."""
        folder = self.in_dir.text()
        if not folder or not os.path.isdir(folder):
            folder = os.path.dirname(paths[0])
            self.in_dir.set(folder)
            self._pending["preview_image"] = os.path.basename(paths[0])
            self._on_input_changed()
            self.activity.flash(f"Input folder set to {folder}")
            return
        todo, existing = [], []
        for p in paths:
            if os.path.normcase(os.path.dirname(os.path.abspath(p))) == os.path.normcase(os.path.abspath(folder)):
                existing.append(os.path.basename(p))
                continue
            base, ext = os.path.splitext(os.path.basename(p))
            dst, k = os.path.join(folder, base + ext), 2
            while os.path.exists(dst):
                dst = os.path.join(folder, f"{base} ({k}){ext}")
                k += 1
            todo.append((p, dst))
        first = os.path.basename(todo[0][1]) if todo else (existing[0] if existing else None)
        if not todo:
            if first:
                self.img_combo.setCurrentText(first)
            return
        bus = self.bus

        def work():
            import shutil
            total = sum(os.path.getsize(a) for a, _ in todo) or 1
            done = 0
            for i, (a, b) in enumerate(todo):
                bus.task.emit("copy", f"Copying {os.path.basename(a)} into the input folder", done / total, 3)
                try:
                    shutil.copy2(a, b)
                except OSError as e:
                    bus.log.emit(f"ERROR {os.path.basename(a)}: {e}")
                done += os.path.getsize(a)
            bus.task_end.emit("copy")
            bus.imported.emit(first, len(todo))

        threading.Thread(target=work, daemon=True).start()

    def _on_imported(self, first, n):
        self._pending["preview_image"] = first
        self._on_input_changed()
        self.activity.flash(f"Added {n} file(s) to the input folder" if n > 1 else f"Added {first} to the input folder")

    def _drop_reference(self, paths):
        """An image dropped on Match colors becomes the reference."""
        self._set_reference(paths[0])


    def _step_image(self, d):
        if self.files:
            i = (self.img_combo.currentIndex() + d) % len(self.files)
            self.img_combo.setCurrentIndex(i)

    def _on_image_selected(self, name):
        if not name or self._loading:
            return
        it = self.thumb_items.get(name)
        if it is not None and self.film.currentItem() is not it:
            self.film.blockSignals(True)
            self.film.setCurrentItem(it)
            self.film.scrollToItem(it)
            self.film.blockSignals(False)
        self._leave_detail(refresh=False)
        self._drop_region()
        self.need_source = True
        self._request_preview()

    # ------------------------------------------------------------------ preview

    def _need_full(self):
        if not self.src_size[0]:
            return False
        W = self.src_size[0]
        shown = W * self.viewer.zoom * self.viewer.devicePixelRatioF()
        return shown > min(W, self.ui["preview_res"]) * 1.05

    def _easy_values(self):
        cur = self.settings()
        return {k: cur[k] for k in EASY_KEYS if k in cur}

    def _effective(self):
        """Current settings, but with the quick-edit values the preview is actually showing."""
        s = self.settings()
        if self.applied_easy is None:
            self.applied_easy = self._easy_values()
        if self.ui["easy_mode"] == "manual":
            s.update(self.applied_easy)
        return s

    def _set_pending(self, on):
        if self.pending == on:
            return
        self.pending = on
        self.update_btn.setProperty("primary", on)
        self._update_btn_text()
        repolish(self.update_btn)
        self.viewer.set_status("Changes waiting · press Update preview (F5)" if on else "")

    def _update_btn_text(self):
        lvl = self.toolbar.level if hasattr(self, "toolbar") else 0
        base = ("⟳  Update preview", "⟳  Update", "⟳", "⟳")[lvl]
        self.update_btn.setText(base + ("  •" if self.pending else ""))

    def _update_now(self):
        """Apply waiting changes and render every enabled step on the visible area."""
        if self.detail_active:
            self._leave_detail(refresh=False)
        self.applied_easy = self._easy_values()
        self._set_pending(False)
        self.viewer.set_status("")
        self._request_preview()
        self._run_live(force_all=True)

    def _request_preview(self):
        name = self.img_combo.currentText()
        if not name or not self.files:
            return
        s = self._effective()
        s["_ref_path"] = self._ref_path(s)
        self.preview_is_full = False
        self.full_pending = self._need_full()
        i, n = self.img_combo.currentIndex(), self.img_combo.count()
        nb = [self.img_combo.itemText((i + d) % n) for d in (1, -1, 2) if n > 1 and 0 <= i]
        nb = [os.path.join(s["input_dir"], x) for x in dict.fromkeys(nb) if x and x != name][:3 if n > 3 else 2]
        self.cur_token = self.preview.request(s, name, int(self.ui["preview_res"]), self.full_pending, self.need_source,
                                              neighbors=nb)
        self.need_source = False

    def _maybe_full_res(self):
        if not self.detail_active and not self.preview_is_full and not self.full_pending and self._need_full():
            self._request_preview()

    def _on_look_changed(self):
        if self._loading:
            return
        if self.ui["easy_mode"] == "manual":
            self._set_pending(True)
            return
        if self.detail_active:
            self._leave_detail(refresh=False)
        self.applied_easy = self._easy_values()
        self._request_preview()
        self._quick_changed()

    def _on_source(self, token, before, size):
        if self.detail_active:
            return
        self.src_size = size
        self.viewer.labels = ("BEFORE", "AFTER")
        self.viewer.set_images(np_to_qimage(before), None, size, keep_view=False)
        self._update_summary()
        if self.viewer.roi is not None:   # the region carries over to the next image (same camera = same spot)
            x0, y0, x1, y1 = self.viewer.roi
            if x1 > size[0] or y1 > size[1]:
                self.viewer.set_roi(None)
            else:
                QTimer.singleShot(0, self._roi_kick)

    def _on_preview(self, token, after, is_full):
        if token != self.cur_token or self.detail_active:
            return
        self.viewer.set_after(np_to_qimage(after))
        self.preview_is_full = is_full
        if is_full:
            self.full_pending = False

    # ------------------------------------------------------------------ zoomed-in render of the visible area
    # self.region keeps the AI-stage result (denoise / upscale / resize) for the area on screen, so quick
    # edits (mood, match, clarity, sharpen, grain) can be re-applied on top of it in milliseconds.

    def _cancel_live(self):
        self.live_timer.stop()
        if self.live_ctl is not None:
            self.live_ctl.cancel()
            self.live_ctl = None
        self.live_token += 1
        self.activity.end("live")

    def _drop_region(self):
        self._cancel_live()
        self.region = None
        self.viewer.clear_patch()

    def _ai_key(self, s, inc_dn, inc_up):
        import corona
        k = {"img": self.img_combo.currentText(), "dn": inc_dn, "up": inc_up,
             "mix": E.source_signature(s, self.img_combo.currentText()),
             "resize": [s["resize_on"], s["resize_mode"], s["width"], s["height"], s["factor"], E.upscale_times(s)]}
        if inc_dn:
            k["dn_s"] = [s["denoise_model"], round(float(s["denoise_strength"]), 4), s["fp16"]]
        if inc_up:
            k["up_s"] = [s["upscale_model"], round(float(s["min_model_scale"]), 4), s["fp16"]]
        return json.dumps(k, sort_keys=True)

    def _region_valid(self):
        r = getattr(self, "region", None)
        if not r:
            return False
        s = E.with_defaults(self._effective())
        return r["key"] == self._ai_key(s, *r["inc"])

    def _visible_rect(self):
        roi = self._roi()
        if roi is not None:
            return roi
        W, H = self.src_size
        x0, y0, x1, y1 = self.viewer.visible_image_rect()
        m = 12
        return int(max(0, x0 - m)), int(max(0, y0 - m)), int(min(W, x1 + m)), int(min(H, y1 + m))

    def _flags(self, s, force_all):
        dn_active = s["denoise_on"] and s["denoise_strength"] > 0 and bool(s["denoise_model"])
        up_active = s["resize_on"] and bool(s["upscale_model"])
        quick = (s["sharpen_on"] and s["sharpen_amount"] > 0) or \
            (s["texture_on"] and (s["grain_amount"] > 0 or s["clarity"] != 0))
        inc_dn = dn_active and (force_all or self.ui["dn_mode"] == "interactive")
        inc_up = up_active and (force_all or self.ui["up_mode"] == "interactive")
        inc_q = quick and (force_all or self.ui["easy_mode"] == "auto")
        waiting = [n for n, a, i in (("denoise", dn_active, inc_dn), ("upscale", up_active, inc_up)) if a and not i]
        return inc_dn, inc_up, inc_q, waiting

    def _label(self, inc_dn, inc_up, s):
        parts = [("DENOISE", inc_dn), ("UPSCALE", inc_up),
                 ("SHARPEN", s["sharpen_on"] and s["sharpen_amount"] > 0),
                 ("GRAIN", s["texture_on"] and s["grain_amount"] > 0)]
        return " ".join(f"+{n}" for n, i in parts if i) or "+FINISH"

    def _quick_changed(self):
        """A quick edit was applied: re-finish the stored AI result, or render the area if there is none."""
        if self._region_valid():
            self._refinish()
        else:
            self._schedule_live(True)

    def _ai_changed(self):
        """Denoise / upscale / size settings changed. The render on screen stays until you press
        Update preview or move the view (interactive), so nothing flickers away."""
        if getattr(self, "region", None) and not self._region_valid():
            self._cancel_live()
            self._set_pending(True)
            self.viewer.set_status("Denoise / upscale changed · press Update preview (F5)")
        elif not getattr(self, "region", None):
            self._set_pending(True)

    def _view_changed(self):
        """Pan / zoom / window resize."""
        v = self.viewer
        if self.detail_active or self._loading or not self.src_size[0]:
            return
        if self._roi() is not None:     # a drawn region: zooming / panning never changes what gets processed
            return
        if v.fitted:
            self.live_timer.stop()
            return
        r = getattr(self, "region", None)
        if r and self._region_valid() and r["res"].covers(self._visible_rect()):
            return
        s = E.with_defaults(self._effective())
        inc_dn, inc_up, inc_q, waiting = self._flags(s, False)
        if r and (r["inc"][0] or r["inc"][1]) and not (inc_dn or inc_up):
            # an Update-preview render (manual AI) is on screen: keep it, offer to render the new area
            self._set_pending(True)
            v.set_status("Press Update preview (F5) to render denoise / upscale for this area")
            return
        if inc_dn or inc_up or inc_q:
            self.live_timer.start(350)
        elif waiting:
            v.set_status(f"Press Update preview (F5) to see {' and '.join(waiting)}")

    def _schedule_live(self, settings_changed):
        if settings_changed and not self._region_valid():
            self._cancel_live()
        if not self._loading:
            self.live_timer.start(300)

    def _run_live(self, force_all=False):
        """Renders the visible area: the AI stage (if included) and then the quick edits."""
        v = self.viewer
        if self.detail_active or self.job is not None or not self.files or not self.src_size[0]:
            return
        full = E.with_defaults(self._effective())
        inc_dn, inc_up, inc_q, waiting = self._flags(full, force_all)
        if not (inc_dn or inc_up or inc_q) or self.errors:
            if not self.pending:
                v.set_status(f"Press Update preview (F5) to see {' and '.join(waiting)}" if waiting and not v.fitted else "")
            return
        roi = self._roi()
        if v.fitted and roi is None:
            if force_all:
                v.set_status("Zoom in to see denoise / upscale / sharpen on detail, or draw a Region (R), or use Detail check")
            return
        W, H = self.src_size
        tw, th = E.target_size(W, H, full)
        k = max(tw / W, th / H)
        x0, y0, x1, y1 = rect = self._visible_rect()
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        limit = (ROI_MAX_MP if roi else LIVE_MAX_MP) if (inc_dn or inc_up) else (48 if roi else 16)
        label = self._label(inc_dn, inc_up, full)
        if (x1 - x0) * (y1 - y0) * k * k / 1e6 > limit:
            if roi:
                v.set_status("The region is too big for the preview: draw a smaller one (or use Detail check)")
            else:
                v.set_status(f"Zoom in further to preview {label.replace('+', '').lower().replace(' ', ', ')}")
            return
        key = self._ai_key(full, inc_dn, inc_up)
        r = getattr(self, "region", None)
        same_area = (tuple(r["res"].rect) == tuple(rect)) if (r and roi) else (r and r["res"].covers(rect))
        if r and r["key"] == key and same_area:
            self._refinish()
            return
        self._cancel_live()
        if force_all or not waiting:
            self._set_pending(False)
        token = self.live_token
        ctl = self.live_ctl = E.Control()
        name = self.img_combo.currentText()
        files = list(self.files)
        worker = self.preview
        bus, cache = self.bus, self.model_cache
        s_ai = dict(full, denoise_on=inc_dn and full["denoise_on"], texture_on=False, sharpen_on=False)
        if not inc_up:
            s_ai["upscale_model"] = ""
        s_fin = self._finish_settings(full, name)
        v.set_status(f"Rendering {label.replace('+', '').lower().replace(' ', ', ')}…")

        def work():
            E.lower_thread_priority()
            try:
                path = os.path.join(full["input_dir"], name)
                import corona
                want = (path, E.source_signature(full, path))
                src, stats = (worker.src, worker.src_stats) if worker.src_key == want else (None, None)
                if src is None:
                    src, _ = E.load_image(path, full)
                    stats = E.lab_stats(src)
                bus.task.emit("live", "Preview: loading models", -1, 1)
                pa = E.Pipeline(s_ai, MODELS_DIR, cache)
                pa.load_models()
                res = E.ai_region(pa, src, rect, control=ctl,
                                  progress=lambda f, st: bus.task.emit("live", f"Preview · {st.lower()}", f * 0.97, 1))
                pf = E.Pipeline(s_fin, MODELS_DIR, cache)
                pf.load_models()
                pf.load_reference(files)
                pf.prepare_atmosphere(src, want, path)
                out = E.finish_region(pf, res, stats, control=ctl)
                bus.region.emit(token, dict(key=key, res=res, inc=(inc_dn, inc_up), stats=stats, src=src,
                                            src_key=want, path=path))
                bus.patch.emit(token, (out * 255 + .5).astype(np.uint8), res.rect)
            except E.Cancelled:
                pass
            except E.SettingError as e:
                bus.error.emit(str(e), e.field, True)
                bus.patch.emit(token, None, None)
            except Exception as e:
                bus.error.emit(f"Preview failed: {e}", "", True)
                bus.patch.emit(token, None, None)
            finally:
                bus.task_end.emit("live")

        self.patch_label = label
        threading.Thread(target=work, daemon=True).start()

    def _finish_settings(self, full, name):
        """Settings for the quick-edit half only (no denoise / upscale models needed)."""
        ref_path = self._ref_path(full)
        return dict(full, denoise_on=False, upscale_model="", _grain_seed=name,
                    ref_image=ref_path if full["match_on"] else full["ref_image"])

    def _refinish(self):
        """Re-applies the quick edits to the stored AI result: the render stays on screen and just updates."""
        r = getattr(self, "region", None)
        if not r or self.detail_active:
            return
        if self.live_ctl is not None:
            self.live_ctl.cancel()
        self.live_token += 1
        token = self.live_token
        ctl = self.live_ctl = E.Control()
        full = E.with_defaults(self._effective())
        name = self.img_combo.currentText()
        s_fin = self._finish_settings(full, name)
        files, bus, cache, res, stats = list(self.files), self.bus, self.model_cache, r["res"], r["stats"]
        self.patch_label = self._label(r["inc"][0], r["inc"][1], full)

        src, src_key, src_path = r.get("src"), r.get("src_key"), r.get("path")

        def work():
            try:
                pf = E.Pipeline(s_fin, MODELS_DIR, cache)
                pf.load_models()
                pf.load_reference(files)
                if src is not None:
                    pf.prepare_atmosphere(src, src_key, src_path)
                out = E.finish_region(pf, res, stats, control=ctl)
                bus.patch.emit(token, (out * 255 + .5).astype(np.uint8), res.rect)
            except E.Cancelled:
                pass
            except E.SettingError as e:
                bus.error.emit(str(e), e.field, True)
            except Exception as e:
                bus.error.emit(f"Preview failed: {e}", "", True)

        threading.Thread(target=work, daemon=True).start()

    def _on_region(self, token, data):
        if token == self.live_token:
            self.region = data

    def _on_patch(self, token, arr, rect):
        if token != self.live_token or self.detail_active:
            return
        self.live_ctl = None
        if arr is None:
            self.viewer.set_status("")
            return
        self.viewer.patch_label = getattr(self, "patch_label", "")
        self.viewer.set_patch(np_to_qimage(arr), rect)
        self.viewer.set_status("Changes waiting · press Update preview (F5)" if self.pending else "")

    # ------------------------------------------------------------------ summary

    def _restore_user_size(self):
        """Greyed-out fields show computed values; switching Resize mode brings back what you typed."""
        for sp, attr in ((self.factor, "_user_factor"), (self.w_spin, "_user_w"), (self.h_spin, "_user_h")):
            v = getattr(self, attr, None)
            if v is not None:
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)

    def _sync_resize_fields(self, s):
        """Only the fields the chosen Resize mode uses can be edited; the others are greyed out and show
        what they work out to for the current image (Scale 2× of 1000×1000 shows 2000×2000, and so on)."""
        mode = s["resize_mode"]
        w_in = mode in ("fit_width", "fit_inside", "exact")
        h_in = mode in ("fit_height", "fit_inside", "exact")
        self.w_spin.setEnabled(w_in)
        self.h_spin.setEnabled(h_in)
        self.factor.setEnabled(mode == "scale_factor")
        self.factor_field.setEnabled(mode == "scale_factor")
        self.wh_field.setEnabled(w_in or h_in)
        has_model = bool(s["upscale_model"])
        self.min_scale_row.setEnabled(has_model)
        self.multi_row.setEnabled(mode == "scale_factor")
        self.multi_n.setEnabled(mode == "scale_factor" and self.multi_chk.isChecked())
        self.fp16.setEnabled(has_model or (s["denoise_on"] and bool(s["denoise_model"])))
        if not self.src_size[0]:
            return
        W, H = self.src_size
        tw, th = E.target_size(W, H, dict(s, resize_on=True))
        shown = {self.w_spin: (not w_in, tw), self.h_spin: (not h_in, th)}
        for sp, (computed, val) in shown.items():
            if computed:
                sp.blockSignals(True)
                sp.setValue(int(min(max(val, sp.minimum()), sp.maximum())))
                sp.blockSignals(False)
        if mode != "scale_factor":
            k = tw / W if mode != "fit_height" else th / H
            self.factor.blockSignals(True)
            self.factor.setValue(min(max(k, self.factor.minimum()), self.factor.maximum()))
            self.factor.blockSignals(False)

    def _update_summary(self):
        if self._loading:
            return
        s = E.with_defaults(self.settings())
        self._sync_resize_fields(s)
        if not s["resize_on"]:
            self.target_lbl.setText("Off: images keep their original resolution")
        elif self.src_size[0]:
            w, h = self.src_size
            tw, th = E.target_size(w, h, s)
            k = max(tw / w, th / h)
            ai = s["upscale_model"] and k >= s["min_model_scale"]
            n = E.upscale_times(s)
            how = "AI upscale" if ai else "plain resize"
            if n > 1:
                how = f"{how}, {n} rounds"
            text = f"This image: {w}×{h} → {tw}×{th}  ({k:.2f}×, {how})"
            gb = tw * th * 3 * 4 * 3 / 1e9          # the result plus working copies, 32-bit
            if gb > 2:
                text += f"\n⚠ Very large result: needs about {gb:.0f} GB of memory while it's processed"
            self.target_lbl.setText(text)
        else:
            self.target_lbl.setText("")
        if self.job_kind != "batch":
            n = len(self.files)
            steps = []
            import atmosphere
            if atmosphere.tod_active(s):
                steps.append(s["tod_mode"])
            if s["denoise_on"] and s["denoise_strength"] > 0:
                steps.append("denoise")
            if s["match_on"] and s["cm_strength"] > 0:
                steps.append("match")
            if atmosphere.fog_active(s):
                steps.append("fog")
            if atmosphere.rays_active(s):
                steps.append("light rays")
            if s["look_on"]:
                steps.append("mood")
            if s["resize_on"]:
                steps.append("upscale" if s["upscale_model"] else "resize")
            if s["sharpen_on"] and s["sharpen_amount"] > 0:
                steps.append("sharpen")
            if s["texture_on"] and (s["clarity"] or s["grain_amount"] > 0):
                steps.append("texture")
            self.batch_info.setText(f"{n} images · {' → '.join(steps) or 'copy only'}" if n else "Choose an input folder")
            self.run_btn.setEnabled(n > 0 and self.job is None)

    # ------------------------------------------------------------------ view modes

    def _set_view_mode(self, mode):
        self.ui["view_mode"] = mode
        for b in self.mode_group.buttons():
            b.setChecked(b.property("mode") == mode)
        self.viewer.set_mode(mode)

    # ------------------------------------------------------------------ jobs (detail check / batch)

    def _set_job_ui(self, running, kind=None):
        self.run_btn.setEnabled(not running and bool(self.files))
        self.run_btn.setVisible(not running)
        self.sel_btn.setVisible(not running)
        self._on_film_selection()
        self.detail_btn.setEnabled(not running)
        self.pause_btn.setVisible(running and kind == "batch")
        self.cancel_btn.setVisible(running)
        self.batch_bar.setVisible(running and kind == "batch")
        self.img_bar.setVisible(running and kind == "batch")
        self.pause_btn.setText("Pause")

    def _cancel_all(self):
        """The Cancel button next to the progress bar: stops everything that's running."""
        what = []
        if self.job is not None:
            self.job.cancel()
            self.activity.set("job", "Cancelling…", -1, 3)
            what.append({"batch": "batch", "detail": "Detail check"}.get(self.job_kind, "job"))
        if self.live_ctl is not None or "live" in self.activity.tasks:
            what.append("preview render")
        self._cancel_live()
        if "load" in self.activity.tasks or "preview" in self.activity.tasks:
            what.append("loading")
        self.preview.cancel()
        self.activity.end("load")
        self.activity.end("preview")
        if getattr(self, "converting", False):
            self._cancel_conversion()
            what.append("model conversion")
        if getattr(self, "_dl_ctl", None) is not None:
            self._dl_ctl.cancel()
            what.append("download")
        if getattr(self, "_model_ctl", None) is not None or getattr(self, "_model_queue", None):
            self._model_queue = []
            if self._model_ctl is not None:
                self._model_ctl.cancel()
            what.append("model download")
        self._log("Cancelled: " + (", ".join(what) or "nothing was running"))
        if not self.detail_active and ("preview render" in what or "loading" in what):
            self.viewer.set_status("Cancelled · press Update preview (F5) to render again")

    def _cancel_conversion(self):
        proc = getattr(self, "_conv_proc", None)
        self._conv_cancelled = True
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _cancel_job(self):
        if self.job is not None:
            self.job.cancel()
            self.activity.set("job", "Cancelling…", -1, 3)

    def _toggle_pause(self):
        if self.job is None:
            return
        paused = not self.job.paused
        self.job.pause(paused)
        self.pause_btn.setText("Resume" if paused else "Pause")
        self.activity.set("job", "Paused" if paused else "Resuming…", None if paused else -1, 3)

    def _detail_check(self):
        if self.job is not None or not self.files or not self.src_size[0]:
            return
        if not self._check_or_highlight():
            return
        self._cancel_live()
        s = self.settings()
        s["ref_image"] = self._ref_path(s) if s["match_on"] else s["ref_image"]
        name = self.img_combo.currentText()
        W, H = self.src_size
        tw, th = E.target_size(W, H, E.with_defaults(s))
        k = max(tw / W, th / H)
        vw = max(200, self.viewer.width()) * self.viewer.devicePixelRatioF()
        vh = max(200, self.viewer.height()) * self.viewer.devicePixelRatioF()
        roi = self._roi()
        if roi is not None:          # the drawn region, whatever the zoom (capped at 4096 px of output per side)
            x0, y0, x1, y1 = roi
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            cw = int(min(x1 - x0, 4096 / k))
            ch = int(min(y1 - y0, 4096 / k))
        else:
            x0, y0, x1, y1 = self.viewer.visible_image_rect()
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            side = self.viewer.mode == "side" or (self.viewer.mode == "split" and self.viewer.auto_side)
            cw = int(min(W, max(64, (vw / (2 if side else 1)) / k), 2400 / k))
            ch = int(min(H, max(64, vh / k), 2400 / k))
        x = int(min(max(cx - cw / 2, 0), W - cw))
        y = int(min(max(cy - ch / 2, 0), H - ch))

        self.job = E.Control()
        self.job_kind = "detail"
        self._set_job_ui(True, "detail")
        bus, cache, ctl, files = self.bus, self.model_cache, self.job, list(self.files)

        def work():
            E.lower_thread_priority()
            try:
                bus.task.emit("job", "Detail check: loading models", -1, 3)
                rgb, _ = E.load_image(os.path.join(s["input_dir"], name), s)
                crop = rgb[y:y + ch, x:x + cw].copy()
                p = E.Pipeline(dict(s, _grain_seed=name), MODELS_DIR, cache)
                p.load_models()
                p.load_reference(files)
                path = os.path.join(s["input_dir"], name)
                p.prepare_atmosphere(rgb, (path, E.source_signature(E.with_defaults(s), path)), path)
                src_stats = E.lab_stats(rgb) if p.ref_stats is not None else None
                t0 = time.time()
                out = E.process_region(p, rgb, (x, y, x + cw, y + ch), src_stats=src_stats, control=ctl,
                                       progress=lambda f, st: bus.task.emit("job", f"Detail check: {st}", f, 3))
                before = E.resize(crop, out.shape[1], out.shape[0])
                bus.detail.emit(dict(before=(before * 255 + .5).astype(np.uint8), after=(out * 255 + .5).astype(np.uint8),
                                     info=f"Detail check · {out.shape[1]}×{out.shape[0]} crop at 100% of the final "
                                          f"{tw}×{th} · {time.time() - t0:.1f}s"))
            except E.Cancelled:
                bus.detail.emit(None)
            except E.SettingError as e:
                bus.detail.emit(None)
                bus.error.emit(str(e), e.field, False)
            except Exception as e:
                bus.detail.emit(None)
                bus.error.emit(f"Detail check failed: {e}", "", False)
            finally:
                bus.task_end.emit("job")

        threading.Thread(target=work, daemon=True).start()

    def _on_detail(self, res):
        self.job = None
        self.job_kind = None
        self._set_job_ui(False)
        self._update_summary()
        if res is None:
            return
        self.detail_active = True
        self.viewer.roi_enabled = False
        self.viewer.labels = ("PLAIN RESIZE", "FULL PIPELINE")
        self.viewer.clear_patch()
        self.viewer.set_status("")
        b, a = res["before"], res["after"]
        self.viewer.set_images(np_to_qimage(b), np_to_qimage(a), (a.shape[1], a.shape[0]))
        self.viewer.zoom_100(self._vc())
        self.banner_text.setText(res["info"] + "   ·   Esc to go back")
        self.banner.show()

    def _leave_detail(self, refresh=True):
        if not self.detail_active:
            return
        self.detail_active = False
        self.viewer.roi_enabled = True
        self.banner.hide()
        self.viewer.labels = ("BEFORE", "AFTER")
        self.need_source = True
        if refresh:
            self._request_preview()
            if self._region_valid():
                QTimer.singleShot(0, self._refinish)

    def _start_batch(self, selected=False):
        if not self.files or self.job is not None:
            return
        names = self._film_names(selected_only=selected)
        if selected and not names:
            self.activity.flash("Select one or more images in the strip below the preview first")
            return
        if not self._check_or_highlight():
            return
        self._cancel_live()
        s = self.settings()
        s["_files"] = names
        s["ref_image"] = self._ref_path(s) if s["match_on"] else s["ref_image"]
        self._save_settings()
        self.job = E.Control()
        self.job_kind = "batch"
        self._set_job_ui(True, "batch")
        self._batch_cur = -1
        self.batch_bar.setValue(0)
        self.img_bar.setValue(0)
        self.batch_t0 = time.time()
        self._log(f"Batch started: {len(names)} images")
        bus, cache, ctl = self.bus, self.model_cache, self.job

        def work():
            E.lower_thread_priority()
            try:
                res = E.run_batch(s, MODELS_DIR, cache, ctl,
                                  on_progress=lambda i, n, name, f, st: bus.batch.emit((i, n, name, f, st)),
                                  on_log=bus.log.emit)
                bus.batch_done.emit(dict(ok=True, out=res[0], report=res[1], errors=res[3]))
            except E.SettingError as e:
                bus.error.emit(str(e), e.field, True)
                bus.batch_done.emit(dict(ok=False, report=str(e), quiet=True))
            except Exception as e:
                bus.log.emit(traceback.format_exc())
                bus.batch_done.emit(dict(ok=False, report=str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_batch_progress(self, data):
        i, n, name, f, stage = data
        final = stage in ("Done", "Failed") or stage.startswith("Skipped")
        if final and i < getattr(self, "_batch_cur", -1):
            # an earlier image finished saving while the GPU already works on a later one
            self._set_film_state(name, "done" if stage != "Failed" else "error")
            return
        if not final:
            self._batch_cur = max(i, getattr(self, "_batch_cur", -1))
        if name:
            if stage in ("Done",) or stage.startswith("Skipped"):
                self._set_film_state(name, "done")
            elif stage == "Failed":
                self._set_film_state(name, "error")
            elif (self.thumb_items.get(name) is not None
                  and (self.thumb_items[name].data(Qt.UserRole + 1) or "") != "current"):
                self._set_film_state(name, "current")
                self.film.scrollToItem(self.thumb_items[name])
        total = (i + f) / max(1, n)
        self.batch_bar.setValue(int(total * 1000))
        self.img_bar.setValue(int(f * 1000))
        el = time.time() - self.batch_t0
        eta = ""
        if total > 0.02 and not (self.job and self.job.paused):
            rem = el * (1 - total) / total
            eta = f" · about {int(rem // 60)}m {int(rem % 60):02d}s left" if rem >= 60 else f" · about {int(rem)}s left"
        self.batch_title.setText(f"Batch · {min(i + 1, n)} of {n}")
        self.batch_info.setText(f"{name or 'Preparing'} — {stage}{eta}")
        if not (self.job and self.job.paused):
            self.activity.set("job", f"Batch {min(i + 1, n)}/{n}: {stage.lower()}{eta}", total, 3)

    def _on_batch_done(self, res):
        self.job = None
        self.job_kind = None
        for name, it in self.thumb_items.items():  # a cancelled image is no longer "in progress"
            if (it.data(Qt.UserRole + 1) or "") == "current":
                self._set_film_state(name, "", sort=False)
        self._sort_film()
        self.activity.end("job")
        self._set_job_ui(False)
        self.batch_title.setText("Batch")
        if res.get("ok"):
            self.last_out = res["out"]
            self.batch_info.setText("✓ " + res["report"])
            self.activity.flash(f"Batch finished: {res['report']}", "Open folder", self._open_output, 15)
            QApplication.alert(self)
        else:
            self.batch_info.setText("Batch stopped: " + res["report"])
            if not res.get("quiet"):
                QMessageBox.warning(self, APP_NAME, "The batch stopped:\n\n" + res["report"])
        self.run_btn.setEnabled(bool(self.files))

    def _open_output(self):
        s = self.settings()
        d = getattr(self, "last_out", None) or s["output_dir"] or (
            os.path.join(s["input_dir"], "processed") if s["input_dir"] else "")
        if d and os.path.isdir(d):
            open_folder(d)
        else:
            self.activity.flash("The output folder doesn't exist yet")

    # ------------------------------------------------------------------ close

    # ------------------------------------------------------------------ default models

    def _auto_models(self):
        """The first time the app runs: downloads the default models that aren't there yet."""
        import model_store
        if self.ui.get("default_models_fetched") or self.job is not None:
            return
        self.ui["default_models_fetched"] = True
        self._save_settings()
        for kind in ("denoise", "upscale"):
            have = E.list_models(E.model_dir(MODELS_DIR, kind))
            for m in model_store.missing(MODELS_DIR, kind, have):
                if not model_store.waiting_file(MODELS_DIR, m):    # downloaded already: just waits for conversion
                    self._want_model(m["name"], quiet=True)

    def _combo_for(self, kind):
        return self.dn_model if kind == "denoise" else self.up_model

    def _want_model(self, name, select=False, quiet=False):
        import model_store
        m = model_store.by_name(name)
        if m is None:
            return
        if not hasattr(self, "_model_queue"):
            self._model_queue, self._model_ctl, self._model_cur, self._model_failures = [], None, None, []
        if select:
            self._select_when_ready = getattr(self, "_select_when_ready", {})
            self._select_when_ready[m["kind"]] = name
        if (self._model_cur and self._model_cur["name"] == name) or any(q["name"] == name for q, _ in self._model_queue):
            return
        if model_store.waiting_file(MODELS_DIR, m):        # downloaded, not converted yet
            if self.converter_installed():
                self._combo_for(m["kind"]).set_status(name, tr("converting…"))
                self._check_models()
                return
            # no converter yet: the queue gets it (the file isn't downloaded again)
        self._model_queue.append((m, quiet))
        self._combo_for(m["kind"]).set_status(name, tr("waiting…"))
        if not quiet:
            self.activity.flash(tr("Downloading {0} from {1}").format(name, m["page"]))
        self._model_next()

    def _model_next(self):
        import model_store
        import packs
        if self._model_ctl is not None:
            return
        if not self._model_queue:
            self.activity.end("models")
            if self._model_failures:
                fails, self._model_failures = self._model_failures, []
                self._report_model_failures(fails)
            return
        m, quiet = self._model_queue.pop(0)
        self._model_cur = dict(m, quiet=quiet)
        ctl = self._model_ctl = E.Control()
        bus = self.bus
        need_converter = not self.converter_installed()

        def work():
            try:
                if not model_store.waiting_file(MODELS_DIR, m):
                    model_store.download(m, MODELS_DIR, cancel=ctl.check,
                                         progress=lambda f: bus.model_dl.emit({"name": m["name"], "progress": f}))
                if need_converter and not self.converter_installed():
                    todo, man = packs.converter_packs(APP_DIR)
                    if todo:
                        packs.install(todo, man, APP_DIR, cancel=ctl.check, step=lambda text, f: bus.model_dl.emit(
                            {"name": m["name"], "converter": f}))
                    importlib.invalidate_caches()
                bus.model_dl.emit({"name": m["name"], "done": True})
            except E.Cancelled:
                bus.model_dl.emit({"name": m["name"], "cancelled": True})
            except Exception as e:     # noqa: BLE001
                bus.model_dl.emit({"name": m["name"], "error": str(e) or type(e).__name__})

        threading.Thread(target=work, daemon=True).start()

    def _on_model_dl(self, ev):
        import model_store
        if not ev["name"]:
            self._on_converter_dl(ev)
            return
        m = model_store.by_name(ev["name"])
        cb = self._combo_for(m["kind"])
        if "progress" in ev:
            f = ev["progress"]
            pct = tr("downloading {0}%").format(int(f * 100)) if f >= 0 else tr("downloading…")
            cb.set_status(m["name"], pct)
            self.activity.set("models", tr("Downloading model {0}…").format(m["name"]), f, 2)
            return
        if "converter" in ev:
            cb.set_status(m["name"], tr("getting the converter…"))
            self.activity.set("models", "Downloading the model converter (one time, about 120 MB)…", ev["converter"], 2)
            return
        quiet = (self._model_cur or {}).get("quiet", True)
        self._model_ctl = None
        self._model_cur = None
        if ev.get("done"):
            cb.set_status(m["name"], tr("converting…"))
            self._log(f"Downloaded {m['name']} from {m['urls'][0]}; converting it for the app")
            self._check_models()
        elif ev.get("cancelled"):
            cb.set_status(m["name"], "")
            self._log(f"Download of {m['name']} cancelled")
        else:
            cb.set_status(m["name"], "")
            self._log(f"Model {m['name']} couldn't be downloaded: {ev['error']}")
            if quiet:
                self._model_failures.append((m, ev["error"]))
            else:
                self._report_model_failures([(m, ev["error"])])
            self._refresh_models()
        self._model_next()

    def _install_converter(self):
        """Downloads the model converter add-on (PyTorch) from the RenderBatch repository, then converts."""
        import packs
        todo, man = packs.converter_packs(APP_DIR)
        if not todo:
            importlib.invalidate_caches()
            self._check_models()
            return
        self._conv_dl = True
        ctl = self._dl_ctl = E.Control()
        bus = self.bus

        def work():
            try:
                packs.install(todo, man, APP_DIR, cancel=ctl.check,
                              step=lambda text, f: bus.model_dl.emit({"name": "", "converter": f}))
                importlib.invalidate_caches()
                bus.model_dl.emit({"name": "", "done": True})
            except E.Cancelled:
                bus.model_dl.emit({"name": "", "cancelled": True})
            except Exception as e:     # noqa: BLE001
                bus.model_dl.emit({"name": "", "error": str(e) or type(e).__name__})

        threading.Thread(target=work, daemon=True).start()

    def _on_converter_dl(self, ev):
        if "converter" in ev:
            self.activity.set("models", "Downloading the model converter (one time, about 120 MB)…", ev["converter"], 2)
            return
        self._conv_dl = False
        self._dl_ctl = None
        self.activity.end("models")
        if ev.get("done"):
            self._log("Model converter installed")
            self._told_missing.clear()
            self._check_models()
        elif ev.get("error"):
            QMessageBox.warning(self, APP_NAME, tr("The model converter couldn't be downloaded:\n\n{0}").format(
                ev["error"][-600:]))

    @staticmethod
    def _conversion_failed(m):
        """True when the converter already gave up on this downloaded file (it isn't retried unchanged)."""
        import convert_model
        import model_store
        p = model_store.waiting_file(MODELS_DIR, m)
        try:
            with open(os.path.join(MODELS_DIR, convert_model.FAILED_FILE), "r", encoding="utf-8") as fh:
                failed = json.load(fh)
            st = os.stat(p)
            return failed.get(p) == [st.st_size, int(st.st_mtime)]
        except (OSError, ValueError, TypeError):
            return False

    def _report_model_failures(self, fails):
        lines = []
        for m, err in fails:
            folder = "models\\denoise" if m["kind"] == "denoise" else "models\\upscale"
            lines.append(f"• {m['name']}\n   {tr('Download it from:')} {m['page']}\n   "
                         f"{tr('Direct link:')} {m['urls'][0]}\n   {tr('Put the file in:')} {folder}\n   ({err.splitlines()[-1].strip()[:160]})")
        QMessageBox.warning(self, APP_NAME, tr("These default models couldn't be downloaded automatically:") + "\n\n"
                            + "\n\n".join(lines) + "\n\n"
                            + tr("Download them yourself and put each file in its folder (Settings › Folders); the app "
                                 "converts it automatically. You can also try again later: pick the model in its "
                                 "dropdown (the ⬇ icon)."))

    def _clean_model_status(self):
        """Statuses of models that are neither downloading nor waiting for conversion any more go away."""
        import model_store
        busy = {q["name"] for q, _ in getattr(self, "_model_queue", [])}
        if getattr(self, "_model_cur", None):
            busy.add(self._model_cur["name"])
        for cb in (self.dn_model, self.up_model):
            for name in list(cb.status):
                m = model_store.by_name(name)
                if name in busy:
                    continue
                if m and model_store.waiting_file(MODELS_DIR, m) and self.converter_installed() \
                        and not self._conversion_failed(m):
                    cb.set_status(name, tr("converting…"))      # downloaded; converts now or in a moment
                else:
                    cb.set_status(name, "")

    # ------------------------------------------------------------------ updates

    @staticmethod
    def _update_zips(mime):
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile().lower().endswith(".zip")]

    def dragEnterEvent(self, e):
        if self._update_zips(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._update_zips(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        zips = self._update_zips(e.mimeData())
        if zips:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda p=zips[0]: self._install_update(p))
        else:
            super().dropEvent(e)

    def _pick_update(self):
        start = os.path.join(os.path.expanduser("~"), "Downloads")
        path, _ = QFileDialog.getOpenFileName(self.window() if QApplication.activeModalWidget() is None
                                              else QApplication.activeModalWidget(), "Install update",
                                              start if os.path.isdir(start) else APP_DIR, "RenderBatch update (*.zip)")
        if path:
            self._install_update(path)

    def _check_downloads(self):
        """Offers a newer update zip found in Downloads / Desktop (each file is offered once)."""
        import updater
        if self.job is not None or QApplication.activeModalWidget() is not None:
            QTimer.singleShot(30000, self._check_downloads)
            return
        seen = list(self.ui.get("update_seen", []))
        try:
            found = updater.find_updates(VERSION, seen=set(seen))
        except Exception:
            return
        if not found:
            return
        path, ver = found[0]
        self.ui["update_seen"] = (seen + [updater.seen_key(p) for p, _ in found])[-20:]
        self._save_settings()
        self._install_update(path, found=True)

    def _install_update(self, path, found=False):
        import updater
        ver = updater.zip_version(path)
        if not ver:
            QMessageBox.warning(self, APP_NAME, tr("{0} isn't a RenderBatch update.").format(os.path.basename(path)))
            return
        if self.job_kind == "batch":
            QMessageBox.information(self, APP_NAME, "A batch is running. Install the update when it has finished.")
            return
        newer = updater.vtuple(ver) > updater.vtuple(VERSION)
        if found:
            q = tr("RenderBatch {0} is in your Downloads:\n{1}\n\nInstall it now? (you have {2}; the app restarts, "
                   "your settings, presets and looks stay)").format(ver, os.path.basename(path), VERSION)
        elif newer:
            q = tr("Install RenderBatch {0}? (you have {1})\n\nThe app closes, updates and starts again. "
                   "Your settings, presets and looks stay.").format(ver, VERSION)
        else:
            q = tr("This update is RenderBatch {0}, but you already have {1}.\n\nInstall it anyway?").format(ver, VERSION)
        if QMessageBox.question(self, APP_NAME, q) != QMessageBox.Yes:
            return
        self.activity.flash(tr("Unpacking update {0}").format(ver))
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            updater.stage(path, APP_DIR)
            self._save_settings()
            updater.launch_helper(APP_DIR, ver)
        except Exception as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP_NAME, tr("The update couldn't be installed: {0}").format(ex))
            return
        QApplication.restoreOverrideCursor()
        self._quitting_for_update = True
        if self.job is not None:
            self.job.cancel()
        self._cancel_live()
        for w in QApplication.topLevelWidgets():
            if w is not self and w.isVisible():
                w.close()
        QApplication.instance().quit()

    def closeEvent(self, e):
        if self.job_kind == "batch":
            r = QMessageBox.question(self, APP_NAME, "A batch is running. Stop it and quit?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        if self.job is not None:
            self.job.cancel()
        self._cancel_live()
        self._save_settings()
        e.accept()


_MAIN = None
_EN_CONST = {"NONE": "(none)", "FIRST_IMAGE": "(first image in folder)", "PROCEDURAL": "(built-in grain)"}


def set_language(code):
    """Loads a language; widgets built afterwards use it."""
    global NONE, FIRST_IMAGE, PROCEDURAL
    i18n.load(code)
    i18n.install()
    NONE, FIRST_IMAGE, PROCEDURAL = (tr(_EN_CONST[k]) for k in ("NONE", "FIRST_IMAGE", "PROCEDURAL"))


def setup_language():
    """Reads the chosen interface language and routes all texts through the translation."""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            code = json.load(fh).get("ui", {}).get("language", "en")
    except (OSError, ValueError):
        code = "en"
    set_language(code)


_RUNNING_MUTEX = None


def main():
    global _RUNNING_MUTEX
    if os.name == "nt":   # own taskbar entry and icon (not Python's)
        try:
            import ctypes
            # lets the installer / uninstaller see that the app is open (released when the app exits)
            _RUNNING_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "RenderBatch_running")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RenderBatch.App")
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle(ClickSliderStyle("Fusion"))
    app.setWindowIcon(app_icon())
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    install_spin_arrows(app)
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(LUT_DIR, exist_ok=True)
    setup_language()
    try:
        E.organize_models(MODELS_DIR)
    except Exception:
        pass
    global _MAIN
    win = _MAIN = MainWindow()
    win.show()
    code = app.exec()
    os._exit(code)  # settings are saved on close; don't wait for background threads


if __name__ == "__main__":
    main()

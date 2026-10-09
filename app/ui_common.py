"""Small widgets shared by the main window and the LightMix panel."""
from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QProxyStyle, QSlider,
                               QSizePolicy, QStyle, QWidget)


DENSITY = {"compact": True}
_SIZES = {  # compact, comfortable
    "card_margins": ((10, 7, 10, 9), (12, 10, 12, 12)), "card_spacing": (4, 6),
    "side_margins": (8, 12), "side_spacing": (7, 10), "slider_vgap": (0, 2), "spin_w": (76, 90),
    "label_w": (72, 84), "activity_h": (32, 40), "header_h": (46, 58), "toolbar_m": ((10, 6), (12, 10)),
    "thumb": ((112, 68), (132, 80)), "film_h": (122, 128), "batch_m": ((12, 8, 12, 10), (14, 12, 14, 14)),
}


def D(key):
    """Size for the current interface density (compact or comfortable)."""
    return _SIZES[key][0 if DENSITY["compact"] else 1]


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def prop(w, name, value=True):
    w.setProperty(name, value)
    return w


def field(label, widget, stretch=True):
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    l = QLabel(label)
    l.setObjectName("FieldLabel")
    l.setMinimumWidth(D("label_w"))
    lay.addWidget(l)
    lay.addWidget(widget, 1 if stretch else 0)
    return row


def wrap_checkboxes(root, max_chars=34):
    """Tick boxes can't wrap their text; long ones get a wrapping label next to them (clicking it ticks)."""
    for cb in root.findChildren(QCheckBox):
        text = cb.text()
        lay = cb.parentWidget().layout() if cb.parentWidget() else None
        if len(text) <= max_chars or lay is None or cb.property("_wrapped"):
            continue
        idx = lay.indexOf(cb)
        if idx < 0:
            continue
        box = QWidget()
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setToolTip(cb.toolTip())
        lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lbl.setMinimumWidth(40)
        lbl.mousePressEvent = lambda e, c=cb: c.isEnabled() and c.toggle()
        cb.setText("")
        cb.setProperty("_wrapped", True)
        cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if hasattr(lay, "insertWidget"):
            lay.insertWidget(idx, box)
        else:
            lay.addWidget(box)
        hl.addWidget(cb, 0, Qt.AlignTop)
        hl.addWidget(lbl, 1)
        cb.toggled.connect(lambda _=0, l=lbl, c=cb: l.setEnabled(c.isEnabled()))


class ClickSliderStyle(QProxyStyle):
    """Clicking anywhere on a slider moves the handle there (and you can keep dragging)."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_Slider_AbsoluteSetButtons:
            return int(Qt.LeftButton.value) if hasattr(Qt.LeftButton, "value") else int(Qt.LeftButton)
        return super().styleHint(hint, option, widget, returnData)


class SliderRow(QWidget):
    changed = Signal(float)

    def __init__(self, label, lo, hi, default, decimals=2, suffix="", tip="", hard=None, help_text="", extra=None):
        super().__init__()
        self.lo, self.hi, self.default = lo, hi, default
        hard_lo, hard_hi = hard or (lo, hi)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 1 + D("slider_vgap"), 0, 1 + D("slider_vgap"))
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(D("slider_vgap"))
        self.label = QLabel(label)
        self.label.setObjectName("FieldLabel")
        self.label.setWordWrap(True)
        from i18n import tr
        self.label.setToolTip((tr(tip) + "\n" if tip else "") + tr("Right-click to reset to the default"))
        self.spin = QDoubleSpinBox()
        self.spin.setProperty("compact", True)
        self.spin.setRange(hard_lo, hard_hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep((hi - lo) / 100)
        self.spin.setSuffix(suffix)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignRight)
        self.spin.setFixedWidth(D("spin_w"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        lay.addWidget(self.label, 0, 0)
        lay.addWidget(self.spin, 0, 1, Qt.AlignRight)
        cols = 2
        if extra is not None:  # e.g. a color swatch on the same line as the number
            lay.addWidget(extra, 0, 2, Qt.AlignRight)
            cols = 3
        lay.addWidget(self.slider, 1, 0, 1, cols)
        if help_text:
            h = QLabel(help_text)
            h.setObjectName("Subtle")
            h.setWordWrap(True)
            h.setStyleSheet("font-size: 8.5pt;")
            lay.addWidget(h, 2, 0, 1, cols)
        if hard:
            self.spin.setToolTip(f"Type any value from {hard_lo:g} to {hard_hi:g}; the slider covers the usual range")
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.label.mouseDoubleClickEvent = lambda e: self.set(self.default)
        # right-click on the slider, the number or the name puts the default back
        for w in (self.slider, self.spin, self.label):
            w.setContextMenuPolicy(Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda _pos: self.reset())
        self.slider.setToolTip(tr("Right-click to reset to the default"))
        self.spin._reset = self.reset
        self.set(default, emit=False)

    def _from_slider(self, i):
        v = self.lo + (self.hi - self.lo) * i / 1000
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self.changed.emit(self.spin.value())

    def _pos(self, v):
        return int(min(max(round((v - self.lo) / (self.hi - self.lo) * 1000), 0), 1000))

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(self._pos(v))
        self.slider.blockSignals(False)
        self.changed.emit(v)

    def reset(self):
        if self.isEnabled() and self.slider.isEnabled():
            self.set(self.default)

    def value(self):
        return float(self.spin.value())

    def set(self, v, emit=True):
        v = min(max(float(v), self.spin.minimum()), self.spin.maximum())
        for w in (self.spin, self.slider):
            w.blockSignals(True)
        self.spin.setValue(v)
        self.slider.setValue(self._pos(v))
        for w in (self.spin, self.slider):
            w.blockSignals(False)
        if emit:
            self.changed.emit(v)




class ElidedLabel(QLabel):
    """One-line label that never asks for more width than it gets: long texts end in '…' and the whole
    text is in the tooltip. Keeps a long message from stretching the window past the screen."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text):
        super().setText(text)
        full = self.text()
        self.setToolTip(full if self.fontMetrics().horizontalAdvance(full) > max(1, self.width()) else "")
        self.update()

    def minimumSizeHint(self):
        h = super().minimumSizeHint()
        return QSize(0, h.height())

    def sizeHint(self):
        h = super().sizeHint()
        return QSize(min(h.width(), 400), h.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        full = self.text()
        self.setToolTip(full if self.fontMetrics().horizontalAdvance(full) > max(1, self.width()) else "")

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.contentsRect()
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text().replace("\n", " "), Qt.ElideRight, r.width())
        p.drawText(r, int(self.alignment()) | Qt.AlignVCenter, text)



class SpinArrows(QWidget):
    """Small up / down arrows inside a number box. Click to step, hold to repeat,
    or press and drag up / down to scrub the value."""
    W = 11

    def __init__(self, spin):
        super().__init__(spin)
        self.spin = spin
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip("Click to step · hold to repeat · drag up / down to change")
        self._press = None
        self._hover = 0
        self.setMouseTracking(True)
        self._rep = QTimer(self)
        self._rep.timeout.connect(self._repeat)
        le = spin.lineEdit()
        if le is not None:
            m = le.textMargins()
            le.setTextMargins(m.left(), m.top(), max(m.right(), self.W), m.bottom())
        spin.installEventFilter(self)
        self._place()
        self.show()

    def _place(self):
        h = self.spin.height()
        self.setGeometry(self.spin.width() - self.W - 3, 2, self.W, max(8, h - 4))
        self.raise_()

    def eventFilter(self, obj, ev):
        if obj is self.spin and ev.type() in (QEvent.Resize, QEvent.Show):
            self._place()
        return False

    def _half(self, y):
        return 1 if y < self.height() / 2 else -1

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        base = pal.color(pal.ColorRole.PlaceholderText)
        hot = pal.color(pal.ColorRole.Highlight)
        if not self.isEnabled() or self.spin.isReadOnly():
            base.setAlpha(70)
        w, h = self.width(), self.height()
        cx, a = w / 2, 3.2
        for d, cy in ((1, h * 0.30), (-1, h * 0.70)):
            c = hot if (self._hover == d and self.isEnabled()) else base
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawPolygon(QPolygonF([QPointF(cx - a, cy + d * a * 0.6), QPointF(cx + a, cy + d * a * 0.6),
                                     QPointF(cx, cy - d * a * 0.8)]))

    def mouseMoveEvent(self, e):
        y = e.position().y()
        if self._press is None:
            d = self._half(y)
            if d != self._hover:
                self._hover = d
                self.update()
            return
        y0, gy0, start, moved = self._press
        dy = gy0 - e.globalPosition().y()
        if not moved and abs(dy) < 4:
            return
        self._rep.stop()
        self._press = (y0, gy0, start, True)
        steps = int(dy / 6)             # 6 px of mouse travel = one step
        self.spin.setValue(start + steps * self.spin.singleStep())

    def leaveEvent(self, _):
        self._hover = 0
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            reset_spin(self.spin)
            return
        if e.button() != Qt.LeftButton or self.spin.isReadOnly():
            return
        self.spin.setFocus()
        self._press = (e.position().y(), e.globalPosition().y(), self.spin.value(), False)
        self._dir = self._half(e.position().y())
        self._rep.start(420)

    def _repeat(self):
        self._rep.setInterval(60)
        self.spin.stepBy(self._dir)

    def mouseReleaseEvent(self, e):
        if self._press is None:
            return
        repeated = self._rep.interval() == 60
        self._rep.stop()
        self._rep.setInterval(420)
        moved = self._press[3]
        self._press = None
        if not moved and not repeated:
            self.spin.stepBy(self._dir)


def set_spin_default(spin, value):
    """Right-click on the box (or its arrows) puts this value back."""
    spin._default = value
    spin.setContextMenuPolicy(Qt.CustomContextMenu)
    spin.customContextMenuRequested.connect(lambda _pos: reset_spin(spin))


def reset_spin(spin):
    if not spin.isEnabled() or spin.isReadOnly():
        return
    if getattr(spin, "_reset", None):
        spin._reset()
    elif getattr(spin, "_default", None) is not None:
        spin.setValue(spin._default)


class _SelectNumber(QObject):
    """Clicking into a number box selects the whole number (not the suffix like ' ×'), so typing replaces it.
    A second click inside the already-focused box places the cursor as usual."""

    def __init__(self, spin):
        super().__init__(spin)
        self.spin = spin
        self._armed = False
        spin.installEventFilter(self)
        le = spin.lineEdit()
        if le is not None:
            le.installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.FocusIn and ev.reason() in (Qt.MouseFocusReason, Qt.OtherFocusReason):
            self._armed = True
        elif t == QEvent.MouseButtonRelease and self._armed:
            self._armed = False
            le = self.spin.lineEdit()
            if le is not None and not le.hasSelectedText():
                QTimer.singleShot(0, self._select)
        elif t == QEvent.FocusOut and obj is self.spin:
            self._armed = False
        return False

    def _select(self):
        if self.spin.hasFocus() or (self.spin.lineEdit() is not None and self.spin.lineEdit().hasFocus()):
            self.spin.selectAll()      # QAbstractSpinBox.selectAll leaves out the prefix / suffix


class _SpinArrowInstaller(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Polish and isinstance(obj, QAbstractSpinBox) and not getattr(obj, "_arrows", None):
            obj.setButtonSymbols(QAbstractSpinBox.NoButtons)
            obj._arrows = SpinArrows(obj)
            obj._select_number = _SelectNumber(obj)
            if getattr(obj, "_reset", None) or getattr(obj, "_default", None) is not None:
                from i18n import tr
                obj._arrows.setToolTip(tr("Click to step · hold to repeat · drag up / down to change") + "\n"
                                       + tr("Right-click to reset to the default"))
        return False


def install_spin_arrows(app):
    """Every number box in the app (also in dialogs) gets the small up / down arrows."""
    app._spin_arrows = _SpinArrowInstaller(app)
    app.installEventFilter(app._spin_arrows)

"""Before/after image viewer: smooth zoom around the cursor, panning, draggable split divider,
synchronized side-by-side panes (automatically when zoomed in), mip-mapped for fast redraws."""

import math

import cv2
import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

import i18n


def np_to_qimage(arr):
    """uint8 HxWx3 RGB numpy array -> QImage (owns its memory)."""
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _half(img):
    """Half-size copy of a QImage (area averaging, like Qt's smooth scaling but several times faster)."""
    w, h = img.width(), img.height()
    if img.format() != QImage.Format_RGB888:
        img = img.convertToFormat(QImage.Format_RGB888)
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())
    arr = arr[:, :w * 3].reshape(h, w, 3)
    return np_to_qimage(cv2.resize(arr, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA))


class Pyramid:
    """An image plus halved copies (built only when needed), so drawing a big image small is fast and clean."""

    def __init__(self, img):
        self.levels = [img]

    def pick(self, needed_width):
        while (self.levels[-1].width() // 2 >= needed_width and min(self.levels[-1].width(), self.levels[-1].height()) > 256
               and len(self.levels) < 6):
            self.levels.append(_half(self.levels[-1]))
        best = self.levels[0]
        for lv in self.levels:
            if lv.width() >= needed_width:
                best = lv
        return best


class ImageViewer(QWidget):
    viewChanged = Signal()     # zoom / pan / mode changed
    zoomedIn = Signal()        # zoom passed the preview resolution (full-res needed)
    roiChanged = Signal()      # the processing region was drawn, moved, resized or cleared
    roiToolKey = Signal()      # R pressed: toggle the region tool
    stepKey = Signal(int)      # Up / Down pressed: previous / next look (or preset)

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(300, 200)
        self.colors = colors
        self.before = self.after = None   # Pyramid
        self.W = self.H = 0               # logical image size (full resolution)
        self.zoom, self.cx, self.cy = 1.0, 0.0, 0.0
        self.fitted = True
        self.mode = "split"               # split | side | before | after
        self.split = 0.5
        self.auto_side = True
        self.smooth_limit = 2.0
        self.labels = ("BEFORE", "AFTER")
        self.hint = "Choose an input folder to start"
        self.patch = None      # (QImage, (x0, y0, x1, y1) in image coords): live AI result drawn over "after"
        self.status = ""       # small note shown bottom-left
        self.patch_label = ""
        self._drag = None
        self._last = QPointF()
        # processing region: (x0, y0, x1, y1) in full-res image pixels, or None
        self.roi = None
        self.roi_tool = False      # left-drag draws / edits the region instead of panning
        self.roi_enabled = True    # off while Detail check is shown (different image)
        self._roi_edit = None      # (kind, start image point, region at start)
        self.pick_cb = None        # one click on the image calls this with (x, y) as 0..1 (light rays center)
        self.rays_marker = None    # [x, y] 0..1: where the light rays come from (drawn as a small sun)

    # ------------------------------------------------------------------ data

    def set_colors(self, colors):
        self.colors = colors
        self.update()

    def set_images(self, before, after, size, keep_view=False):
        """before/after: QImage or None. size: logical (W, H) that both map onto."""
        self.before = Pyramid(before) if before is not None else None
        self.after = Pyramid(after) if after is not None else None
        new_size = (int(size[0]), int(size[1]))
        if not keep_view or new_size != (self.W, self.H):
            self.W, self.H = new_size
            self.fit()
        self.update()

    def set_after(self, after):
        self.after = Pyramid(after) if after is not None else None
        self.update()

    def set_patch(self, img, rect):
        self.patch = (img, rect) if img is not None else None
        self.update()

    def clear_patch(self):
        if self.patch is not None:
            self.patch = None
            self.update()

    def set_status(self, text):
        if text != self.status:
            self.status = text
            self.update()

    def clear(self, hint=None):
        self.before = self.after = None
        self.patch = None
        self.W = self.H = 0
        if hint:
            self.hint = hint
        self.update()

    def has_image(self):
        return self.W > 0 and (self.before is not None or self.after is not None)

    # ------------------------------------------------------------------ processing region

    def set_roi(self, rect, notify=True):
        if rect is not None:
            x0, y0, x1, y1 = rect
            x0, x1 = sorted((min(max(x0, 0), self.W), min(max(x1, 0), self.W)))
            y0, y1 = sorted((min(max(y0, 0), self.H), min(max(y1, 0), self.H)))
            rect = (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
            if rect[2] - rect[0] < 16 or rect[3] - rect[1] < 16:
                rect = None
        changed = rect != self.roi
        self.roi = rect
        self.update()
        if changed and notify:
            self.roiChanged.emit()

    def start_pick(self, callback):
        self.pick_cb = callback
        self.setCursor(Qt.CrossCursor)
        self.setFocus()

    def cancel_pick(self):
        self.pick_cb = None
        self.unsetCursor()

    def set_roi_tool(self, on):
        self.roi_tool = bool(on)
        self._roi_edit = None
        self.unsetCursor()
        self.update()

    def roi_active(self):
        return self.roi is not None and self.roi_enabled and self.has_image()

    def _roi_screen(self, rect):
        x0, y0, x1, y1 = self.roi
        return QRectF(rect.center().x() + (x0 - self.cx) * self.zoom, rect.center().y() + (y0 - self.cy) * self.zoom,
                      (x1 - x0) * self.zoom, (y1 - y0) * self.zoom)

    def _roi_hit(self, pos):
        """Which part of the region is under the cursor: 'move', an edge/corner like 'l', 'tr', or None."""
        if not self.roi_active():
            return None
        r = self._roi_screen(self._pane_at(pos))
        m = 7
        if not r.adjusted(-m, -m, m, m).contains(pos):
            return None
        kind = ""
        if abs(pos.y() - r.top()) <= m:
            kind += "t"
        elif abs(pos.y() - r.bottom()) <= m:
            kind += "b"
        if abs(pos.x() - r.left()) <= m:
            kind += "l"
        elif abs(pos.x() - r.right()) <= m:
            kind += "r"
        if kind:
            return kind
        return "move" if r.contains(pos) and self._can_move_inside(pos) else None

    _CURSORS = {"t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                "bl": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor}

    def _roi_update(self, pos):
        kind, (sx, sy), start = self._roi_edit
        ix, iy = self.to_image(self._pane_at(pos), pos)
        if kind == "new":
            x0, y0, x1, y1 = sx, sy, ix, iy
        else:
            x0, y0, x1, y1 = start
            dx, dy = ix - sx, iy - sy
            if kind == "move":
                w, h = x1 - x0, y1 - y0
                x0 = min(max(x0 + dx, 0), self.W - w)
                y0 = min(max(y0 + dy, 0), self.H - h)
                x1, y1 = x0 + w, y0 + h
            else:
                if "l" in kind:
                    x0 += dx
                if "r" in kind:
                    x1 += dx
                if "t" in kind:
                    y0 += dy
                if "b" in kind:
                    y1 += dy
        x0, x1 = sorted((min(max(x0, 0), self.W), min(max(x1, 0), self.W)))
        y0, y1 = sorted((min(max(y0, 0), self.H), min(max(y1, 0), self.H)))
        self.roi = (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
        self.update()

    # ------------------------------------------------------------------ geometry

    def _dpr(self):
        return self.devicePixelRatioF() or 1.0

    def _fit_zoom(self, mode=None):
        if not self.W:
            return 1.0
        r = self._panes(mode or self._base_for_fit())[0][0]
        return min(r.width() / self.W, r.height() / self.H)

    def _base_for_fit(self):
        return "after" if self.mode == "split" else self.mode

    def effective_mode(self):
        if self.mode == "split" and self.auto_side and self.W and self.zoom > self._fit_zoom("after") * 1.03:
            return "side"
        return self.mode

    def _panes(self, mode=None):
        mode = mode or self.effective_mode()
        r = QRectF(self.rect())
        if mode == "side":
            gap = 6
            w = (r.width() - gap) / 2
            return [(QRectF(r.left(), r.top(), w, r.height()), "before"),
                    (QRectF(r.left() + w + gap, r.top(), w, r.height()), "after")]
        return [(r, mode)]

    def _pane_at(self, pos):
        for rect, which in self._panes():
            if rect.contains(pos):
                return rect
        return self._panes()[0][0]

    def to_image(self, rect, pos):
        return (self.cx + (pos.x() - rect.center().x()) / self.zoom,
                self.cy + (pos.y() - rect.center().y()) / self.zoom)

    def _clamp(self):
        self.cx = min(max(self.cx, 0.0), float(self.W))
        self.cy = min(max(self.cy, 0.0), float(self.H))
        r = self._panes()[0][0]
        if self.W * self.zoom <= r.width():
            self.cx = self.W / 2
        if self.H * self.zoom <= r.height():
            self.cy = self.H / 2

    def fit(self):
        self.fitted = True
        self.zoom = self._fit_zoom()
        self.cx, self.cy = self.W / 2, self.H / 2
        self.update()
        self.viewChanged.emit()

    def set_zoom(self, z, anchor=None):
        if not self.W:
            return
        fz = self._fit_zoom()
        z = min(max(z, fz * 0.5), 40.0)
        was_side = self.effective_mode() == "side"
        if anchor is not None:
            rect = self._pane_at(anchor)
            ix, iy = self.to_image(rect, anchor)
            self.zoom = z
            self.cx = ix - (anchor.x() - rect.center().x()) / z
            self.cy = iy - (anchor.y() - rect.center().y()) / z
            if (self.effective_mode() == "side") != was_side:  # pane layout changed: keep the point centered
                self.cx, self.cy = ix, iy
        else:
            self.zoom = z
        self.fitted = abs(z - fz) < 1e-6
        self._clamp()
        self.update()
        self.viewChanged.emit()
        self.zoomedIn.emit()

    def zoom_100(self, anchor=None):
        self.set_zoom(1.0 / self._dpr(), anchor)

    def zoom_percent(self):
        return self.zoom * self._dpr() * 100

    def visible_image_rect(self):
        """(x0, y0, x1, y1) of the image area visible in the first pane, in full-res pixels."""
        rect = self._panes()[0][0]
        x0, y0 = self.to_image(rect, rect.topLeft())
        x1, y1 = self.to_image(rect, rect.bottomRight())
        return max(0, x0), max(0, y0), min(self.W, x1), min(self.H, y1)

    def set_mode(self, mode):
        self.mode = mode
        if self.fitted:
            self.zoom = self._fit_zoom()
            self.cx, self.cy = self.W / 2, self.H / 2
        self._clamp()
        self.update()
        self.viewChanged.emit()

    def resizeEvent(self, e):
        if self.fitted:
            self.zoom = self._fit_zoom()
            self.cx, self.cy = self.W / 2, self.H / 2
        self._clamp()
        super().resizeEvent(e)
        self.viewChanged.emit()

    # ------------------------------------------------------------------ painting

    def _draw(self, p, rect, pyr):
        if pyr is None:
            return
        x0, y0 = self.to_image(rect, rect.topLeft())
        x1, y1 = self.to_image(rect, rect.bottomRight())
        x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(float(self.W), x1), min(float(self.H), y1)
        if x1 <= x0 or y1 <= y0:
            return
        img = pyr.pick(self.W * self.zoom * self._dpr())
        sx, sy = img.width() / self.W, img.height() / self.H
        src = QRectF(x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy)
        tl = QPointF(rect.center().x() + (x0 - self.cx) * self.zoom, rect.center().y() + (y0 - self.cy) * self.zoom)
        dst = QRectF(tl.x(), tl.y(), (x1 - x0) * self.zoom, (y1 - y0) * self.zoom)
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.zoom * self._dpr() < self.smooth_limit)
        p.drawImage(dst, img, src)

    def _draw_patch(self, p, rect):
        if self.patch is None:
            return
        img, (x0, y0, x1, y1) = self.patch
        tl = QPointF(rect.center().x() + (x0 - self.cx) * self.zoom, rect.center().y() + (y0 - self.cy) * self.zoom)
        dst = QRectF(tl.x(), tl.y(), (x1 - x0) * self.zoom, (y1 - y0) * self.zoom)
        if not dst.intersects(rect):
            return
        p.save()
        p.setClipRect(rect, Qt.IntersectClip)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(dst, img, QRectF(0, 0, img.width(), img.height()))
        p.restore()

    def patch_visible(self):
        if self.patch is None or not self.W:
            return False
        x0, y0, x1, y1 = self.patch[1]
        vx0, vy0, vx1, vy1 = self.visible_image_rect()
        return x0 < vx1 and x1 > vx0 and y0 < vy1 and y1 > vy0

    def _pill(self, p, text, x, y, align_right=False):
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        text = i18n.tr_label(text)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 18, fm.height() + 8
        if align_right:
            x -= w
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        p.fillPath(path, QColor(0, 0, 0, 150))
        p.setPen(QColor(255, 255, 255, 230))
        p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)

    def _roi_close_center(self, rect):
        r = self._roi_screen(rect)
        return QPointF(min(r.right(), rect.right() - 10), max(r.top(), rect.top() + 10))

    def _draw_roi(self, p, rect):
        """Thin dashed frame with a soft glow; the image inside and outside stays untouched."""
        r = self._roi_screen(rect)
        p.save()
        p.setClipRect(rect)
        p.setBrush(Qt.NoBrush)
        p.setRenderHint(QPainter.Antialiasing, True)
        glow = QColor(self.colors["accent"])
        for width, alpha in ((7, 18), (4, 34), (2, 60)):
            glow.setAlpha(alpha)
            p.setPen(QPen(glow, width))
            p.drawRect(r)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1.6))
        p.drawRect(r)
        pen = QPen(QColor(255, 255, 255, 215), 1.2, Qt.CustomDashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.drawRect(r)
        # small × to remove the region
        c = self._roi_close_center(rect)
        hot = getattr(self, "_close_hover", False)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        p.setBrush(QColor(self.colors["accent"]) if hot else QColor(20, 20, 24, 200))
        p.drawEllipse(c, 7.5, 7.5)
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.4))
        d = 2.8
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
        p.restore()

    def _draw_sun(self, p, rect):
        """Small sun where the light rays come from."""
        x, y = self.rays_marker
        c = QPointF(rect.center().x() + (x * self.W - self.cx) * self.zoom, rect.center().y() + (y * self.H - self.cy) * self.zoom)
        if not rect.adjusted(-20, -20, 20, 20).contains(c):
            return
        p.save()
        p.setClipRect(rect)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(0, 0, 0, 120), 3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 6, 6)
        p.setPen(QPen(QColor(255, 214, 120, 235), 1.6))
        p.drawEllipse(c, 6, 6)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * 9, c.y() + math.sin(a) * 9),
                       QPointF(c.x() + math.cos(a) * 13, c.y() + math.sin(a) * 13))
        p.restore()

    def _on_roi_close(self, pos):
        if not self.roi_active():
            return False
        for rect, _w in self._panes():
            c = self._roi_close_center(rect)
            if (pos.x() - c.x()) ** 2 + (pos.y() - c.y()) ** 2 <= 9.5 ** 2:
                return True
        return False

    def _can_move_inside(self, pos):
        """Dragging inside moves the region while the Region tool is on, or when the region is small on screen
        (when it fills the view, dragging inside pans as usual; the frame edges still resize it)."""
        if self.roi_tool:
            return True
        pane = self._pane_at(pos)
        r = self._roi_screen(pane)
        return r.width() < pane.width() * 0.8 and r.height() < pane.height() * 0.8

    def paintEvent(self, _):
        p = QPainter(self)
        live = self.patch_visible()
        saved_labels = self.labels
        if live:
            self.labels = (self.labels[0], self.labels[1] + " · " + (self.patch_label or "LIVE"))
        p.fillRect(self.rect(), QColor(self.colors["viewer"]))
        if not self.has_image():
            p.setPen(QColor(self.colors["muted"]))
            f = QFont(self.font())
            f.setPointSizeF(12)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, i18n.tr(self.hint))
            return

        mode = self.effective_mode()
        panes = self._panes(mode)
        before = self.before or self.after
        after = self.after or self.before
        if mode == "side":
            for rect, which in panes:
                p.save()
                p.setClipRect(rect)
                self._draw(p, rect, before if which == "before" else after)
                if which == "after":
                    self._draw_patch(p, rect)
                p.restore()
            self._pill(p, self.labels[0], panes[0][0].left() + 12, 12)
            self._pill(p, self.labels[1], panes[1][0].right() - 12, 12, True)
        elif mode == "split":
            rect = panes[0][0]
            self._draw(p, rect, after)
            self._draw_patch(p, rect)
            sx = rect.left() + rect.width() * self.split
            p.save()
            p.setClipRect(QRectF(rect.left(), rect.top(), sx - rect.left(), rect.height()))
            self._draw(p, rect, before)
            p.restore()
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QPen(QColor(255, 255, 255, 220), 2))
            p.drawLine(QPointF(sx, rect.top()), QPointF(sx, rect.bottom()))
            cy = rect.center().y()
            p.setBrush(QColor(self.colors["accent"]))
            p.setPen(QPen(QColor(255, 255, 255, 235), 2))
            p.drawEllipse(QPointF(sx, cy), 14, 14)
            p.setPen(QPen(QColor(self.colors["accent_text"]), 2))
            for d in (-1, 1):  # little arrows on the handle
                p.drawLine(QPointF(sx + d * 3, cy - 4), QPointF(sx + d * 7, cy))
                p.drawLine(QPointF(sx + d * 7, cy), QPointF(sx + d * 3, cy + 4))
            p.setRenderHint(QPainter.Antialiasing, False)
            if sx - rect.left() > 90:
                self._pill(p, self.labels[0], rect.left() + 12, 12)
            if rect.right() - sx > 90:
                self._pill(p, self.labels[1], rect.right() - 12, 12, True)
        else:
            self._draw(p, panes[0][0], before if mode == "before" else after)
            if mode == "after":
                self._draw_patch(p, panes[0][0])
            self._pill(p, self.labels[0] if mode == "before" else self.labels[1], panes[0][0].left() + 12, 12)

        if self.roi_active():
            for rect, _which in panes:
                self._draw_roi(p, rect)
        if self.rays_marker and self.roi_enabled:
            for rect, _which in panes:
                self._draw_sun(p, rect)
        self._pill(p, f"{self.zoom_percent():.0f}%", self.width() - 12, self.height() - 36, True)
        if self.status:
            self._pill(p, self.status, 12, self.height() - 36)
        self.labels = saved_labels

    # ------------------------------------------------------------------ interaction

    def _near_divider(self, pos):
        if self.effective_mode() != "split" or not self.has_image():
            return False
        r = QRectF(self.rect())
        return abs(pos.x() - (r.left() + r.width() * self.split)) < 16

    def mousePressEvent(self, e):
        if not self.has_image():
            return
        self._last = e.position()
        pos = e.position()
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if self.pick_cb is not None and e.button() == Qt.LeftButton and self.roi_enabled:
            ix, iy = self.to_image(self._pane_at(pos), pos)
            cb, self.pick_cb = self.pick_cb, None
            self.unsetCursor()
            cb(min(max(ix / max(1, self.W), -1.0), 2.0), min(max(iy / max(1, self.H), -1.0), 2.0))
            return
        if e.button() == Qt.LeftButton and self._on_roi_close(pos):
            self.set_roi(None)
            return
        if e.button() == Qt.RightButton and self.roi_active() and self._roi_hit(pos):
            self.set_roi(None)          # right-click on the region clears it
            return
        if e.button() == Qt.LeftButton and self.roi_enabled:
            hit = self._roi_hit(pos)
            new = hit is None and (self.roi_tool or shift)
            if hit is None and new and self.roi_tool and self._near_divider(pos):
                new = False
            if hit or new:
                ix, iy = self.to_image(self._pane_at(pos), pos)
                self._roi_edit = (hit or "new", (ix, iy), self.roi)
                self._roi_before = self.roi
                self._drag = "roi"
                return
        if e.button() == Qt.LeftButton and self._near_divider(e.position()):
            self._drag = "split"
        elif e.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._drag = "pan"
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag == "roi":
            self._roi_update(pos)
            return
        if self._drag == "split":
            self.split = min(max((pos.x() - self.rect().left()) / max(1, self.width()), 0.0), 1.0)
            self.update()
        elif self._drag == "pan":
            d = pos - self._last
            self._last = pos
            self.cx -= d.x() / self.zoom
            self.cy -= d.y() / self.zoom
            self.fitted = False
            self._clamp()
            self.update()
            self.viewChanged.emit()
        else:
            close = self._on_roi_close(pos)
            if close != getattr(self, "_close_hover", False):
                self._close_hover = close
                self.update()
            hit = None if close else self._roi_hit(pos)
            if close:
                self.setCursor(Qt.PointingHandCursor)
            elif hit:
                self.setCursor(self._CURSORS[hit])
            elif self.roi_tool and self.has_image() and self.roi_enabled and not self._near_divider(pos):
                self.setCursor(Qt.CrossCursor)
            elif self._near_divider(pos):
                self.setCursor(Qt.SizeHorCursor)
            elif self.has_image():
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, e):
        if self._drag == "roi":
            self._drag = None
            before, self._roi_edit = getattr(self, "_roi_before", None), None
            new = self.roi
            self.roi = before
            self.set_roi(new)      # validates (too small → cleared) and notifies
        self._drag = None
        self.mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if not self.has_image() or self._near_divider(e.position()):
            return
        if self._roi_hit(e.position()) == "move":   # double-click inside: zoom to the region
            self.zoom_to_roi()
            return
        if self.fitted or self.zoom < 0.9 / self._dpr():
            self.zoom_100(e.position())
        else:
            self.fit()

    def wheelEvent(self, e):
        if not self.has_image():
            return
        d = e.angleDelta().y()
        if d == 0:
            d = e.pixelDelta().y() * 2
        factor = math.pow(1.0016, d)  # one mouse notch (120) = about 21%, trackpads are smooth
        self.set_zoom(self.zoom * factor, e.position())
        e.accept()

    def zoom_to_roi(self):
        if not self.roi_active():
            return
        x0, y0, x1, y1 = self.roi
        r = self._panes()[0][0]
        z = min(r.width() / max(1, x1 - x0), r.height() / max(1, y1 - y0)) * 0.9
        self.set_zoom(z)
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.fitted = False
        self._clamp()
        self.update()
        self.viewChanged.emit()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Up, Qt.Key_Down) and not e.modifiers():
            self.stepKey.emit(-1 if k == Qt.Key_Up else 1)
        elif k == Qt.Key_R and not e.modifiers():
            self.roiToolKey.emit()
        elif k == Qt.Key_F:
            self.fit()
        elif k == Qt.Key_1:
            self.zoom_100(QPointF(self.width() / 2, self.height() / 2))
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.set_zoom(self.zoom * 1.25, QPointF(self.width() / 2, self.height() / 2))
        elif k == Qt.Key_Minus:
            self.set_zoom(self.zoom / 1.25, QPointF(self.width() / 2, self.height() / 2))
        else:
            super().keyPressEvent(e)

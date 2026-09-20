"""A fixed screenshot and green boxes painted in one coordinate system."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .capture_state import fit_image


class CaptureViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setObjectName("captureViewer")
        self.frame = None
        self.boxes = ()
        self._image = QImage()

    def show_frame(self, frame):
        self.frame = frame
        self.boxes = ()
        rgb = frame.rgb
        self._image = QImage(rgb.data, frame.width, frame.height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.update()

    def show_result(self, result):
        if result.frame is not self.frame:
            return False
        self.boxes = result.boxes
        self.update()
        return True

    def image_transform(self):
        if self.frame is None:
            return 0.0, 0.0, 0.0
        return fit_image(self.frame.width, self.frame.height, self.width(), self.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101418"))
        if self.frame is None:
            painter.setPen(QColor("#9daab5"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Capture your primary screen\n\nYour screenshot and detected regions will appear here.")
            return
        scale, left, top = self.image_transform()
        if scale <= 0:
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(left, top)
        painter.scale(scale, scale)
        painter.drawImage(0, 0, self._image)
        pen = QPen(QColor("#00ff66"), 2.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setClipRect(QRectF(0, 0, self.frame.width, self.frame.height))
        for x1, y1, x2, y2 in self.boxes:
            painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))

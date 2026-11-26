"""Koinfra PDF - app/tool icons and the approval (결재) table drawing used by the preview and the saved PDF."""
from pathlib import Path
from PyQt5.QtCore import QPointF, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetricsF, QIcon, QImage, QImageReader, QPainter, QPen, QPixmap
from kpdf_core import APP_ICON2_ICO, APP_ICON_ICO, APP_ICON_SVG, TOOL_ICON_DIR, TOOL_ICON_SIZES, normalize_approval_line


def approval_geometry(rect, labels):
    """Fractions used by the approval table (same math for preview and PDF)."""
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    count = max(1, len(labels))
    label_width = min(width * 0.22, max(width * 0.16, width * 0.72 / (count + 1)))
    signer_width = (width - label_width) / count
    header_height = height * 0.34
    return label_width, signer_width, header_height


def approval_signature_cell_rect(rect, labels, cell_index):
    """Normalized rect of the signature area under the header of one approval column."""
    left, top, _right, bottom = rect
    label_width, signer_width, header_height = approval_geometry(rect, labels)
    cell_index = max(0, min(len(labels) - 1, cell_index))
    cell_left = left + label_width + signer_width * cell_index
    cell_top = top + header_height
    pad_x = signer_width * 0.08
    pad_y = (bottom - cell_top) * 0.10
    return (cell_left + pad_x, cell_top + pad_y, cell_left + signer_width - pad_x, bottom - pad_y)


APPROVAL_FONT_FAMILIES = ["Malgun Gothic", "\ub9d1\uc740 \uace0\ub515", "Apple SD Gothic Neo", "Noto Sans CJK KR", "NanumGothic", "Gulim"]


def approval_font(pixel_size, bold=True):
    font = QFont()
    font.setFamilies(APPROVAL_FONT_FAMILIES) if hasattr(font, "setFamilies") else font.setFamily(APPROVAL_FONT_FAMILIES[0])
    font.setPixelSize(max(1, int(pixel_size)))
    font.setBold(bold)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def paint_approval_table(painter, rect, labels, signature=None, signature_cell=0):
    """Draw a complete approval table: vertical '\uacb0\uc7ac' head cell, title row, signature row, all borders.

    Line width and text size scale with the table, so the on-screen preview and the high-resolution
    image embedded in the saved PDF look the same.
    """
    labels = normalize_approval_line(labels)
    pen_width = max(1.0, rect.height() * 0.012)
    half = pen_width / 2
    box = QRectF(rect).adjusted(half, half, -half, -half)
    label_ratio, signer_ratio, header_ratio = approval_geometry((0.0, 0.0, 1.0, 1.0), labels)
    left, top, width, height = box.left(), box.top(), box.width(), box.height()
    label_width = width * label_ratio
    signer_width = width * signer_ratio
    header_height = height * header_ratio
    x1 = left + label_width

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setBrush(QColor(255, 255, 255, 235))
    pen = QPen(QColor("#111827"), pen_width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
    painter.setPen(pen)
    painter.drawRect(box)
    painter.drawLine(QPointF(x1, top), QPointF(x1, box.bottom()))
    for column in range(1, len(labels)):
        x = x1 + signer_width * column
        painter.drawLine(QPointF(x, top), QPointF(x, box.bottom()))
    painter.drawLine(QPointF(x1, top + header_height), QPointF(box.right(), top + header_height))

    painter.setPen(QColor("#111827"))
    # Title row: one size for every column, shrunk until the longest title fits.
    title_size = max(4.0, header_height * 0.46)
    while title_size > 4:
        metrics = QFontMetricsF(approval_font(title_size))
        if max(metrics.horizontalAdvance(label) for label in labels) <= signer_width * 0.86:
            break
        title_size -= 0.5
    painter.setFont(approval_font(title_size))
    for index, label in enumerate(labels):
        painter.drawText(QRectF(x1 + signer_width * index, top, signer_width, header_height), Qt.AlignCenter, label)

    head_size = max(4.0, min(label_width * 0.5, height * 0.2))
    painter.setFont(approval_font(head_size))
    painter.drawText(QRectF(left, top, label_width, height), Qt.AlignCenter, "\uacb0\n\uc7ac")

    if signature is not None and not signature.isNull():
        cell = approval_signature_cell_rect((0.0, 0.0, 1.0, 1.0), labels, signature_cell)
        target = QRectF(left + cell[0] * width, top + cell[1] * height, (cell[2] - cell[0]) * width, (cell[3] - cell[1]) * height)
        ratio = min(target.width() / max(1, signature.width()), target.height() / max(1, signature.height()))
        fitted_width = signature.width() * ratio
        fitted_height = signature.height() * ratio
        fitted = QRectF(target.center().x() - fitted_width / 2, target.center().y() - fitted_height / 2, fitted_width, fitted_height)
        painter.drawPixmap(fitted, signature, QRectF(signature.rect()))
    painter.restore()


def render_approval_image(path, labels, width_pt, height_pt, signature_path=None, signature_cell=0, dpi=432):
    """Rasterize the approval table at print resolution (transparent PNG) for embedding into the PDF."""
    scale = dpi / 72.0
    longest = max(width_pt, height_pt) * scale
    if longest > 3600:
        scale *= 3600 / longest
    image = QImage(max(8, int(round(width_pt * scale))), max(8, int(round(height_pt * scale))), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    signature = QPixmap(str(signature_path)) if signature_path and Path(signature_path).is_file() else None
    painter = QPainter(image)
    paint_approval_table(painter, QRectF(0, 0, image.width(), image.height()), labels, signature, signature_cell)
    painter.end()
    if not image.save(str(path), "PNG"):
        raise OSError(f"approval image save failed: {path}")
    return path


def load_app_icon():
    # Prefer the multi-size ICO (16~256 px frames: white document with navy "PDF" title, KOINFRA symbol and pen; 48 px and below use simplified art).
    for icon_path in (APP_ICON2_ICO, APP_ICON_ICO):
        if not icon_path.is_file():
            continue
        icon = QIcon()
        reader = QImageReader(str(icon_path))
        for image_index in range(max(1, reader.imageCount())):
            reader.jumpToImage(image_index)
            image = reader.read()
            if not image.isNull():
                icon.addPixmap(QPixmap.fromImage(image))
        if not icon.isNull():
            return icon
    if APP_ICON_SVG.is_file():
        return QIcon(str(APP_ICON_SVG))
    return QIcon()


_TOOL_ICON_CACHE = {}


def make_tool_icon(kind, size=32):
    """Load the Lucide-based line icon from icons/<kind>_<px>.png (multi-size, HiDPI-ready).

    The PNGs are always bundled (static_check verifies every key); a missing set yields an empty icon.
    """
    cached = _TOOL_ICON_CACHE.get(kind)
    if cached is not None:
        return cached
    icon = QIcon()
    for px in TOOL_ICON_SIZES:
        icon_path = TOOL_ICON_DIR / f"{kind}_{px}.png"
        if icon_path.is_file():
            icon.addFile(str(icon_path), QSize(px, px))
    _TOOL_ICON_CACHE[kind] = icon
    return icon


def make_page_icon(rotation=0, canvas_size=None):
    pixmap = QPixmap(70, 94)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#cbd5e1"), 1))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(7, 5, 54, 78), 4, 4)
    painter.setPen(QPen(QColor("#94a3b8"), 1))
    painter.drawLine(QPointF(17, 28), QPointF(51, 28))
    painter.drawLine(QPointF(17, 38), QPointF(46, 38))
    painter.drawLine(QPointF(17, 48), QPointF(51, 48))

    if rotation:
        painter.setPen(QPen(QColor("#0f766e"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawArc(QRectF(21, 56, 26, 18), 25 * 16, 280 * 16)
        painter.drawLine(QPointF(46, 60), QPointF(51, 58))
        painter.drawLine(QPointF(46, 60), QPointF(49, 65))

    painter.end()
    if canvas_size is not None:
        # Same size as the rendered thumbnail: QListView sizes/positions icon-mode items by the icon pixmap,
        # so a smaller placeholder made lazily rendered items grow to the right of their slot (off-center).
        width, height = canvas_size
        canvas = QPixmap(max(1, width), max(1, height))
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap((canvas.width() - pixmap.width()) // 2, (canvas.height() - pixmap.height()) // 2, pixmap)
        painter.end()
        pixmap = canvas
    return QIcon(pixmap)

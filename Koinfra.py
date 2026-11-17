import os
import sys
import tempfile
import winreg
import ctypes
import json
import shutil
import subprocess
import time
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

try:
    import pypdfium2 as pdfium
    from PIL import Image, ImageChops
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject
    from PyQt5.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
    from PyQt5.QtGui import (
        QColor,
        QPainterPath,
        QDragEnterEvent,
        QDropEvent,
        QFont,
        QFontMetricsF,
        QIcon,
        QImage,
        QImageReader,
        QKeySequence,
        QPainter,
        QPen,
        QPixmap,
    )
    from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QAction,
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QFrame,
        QMenu,
        QFileDialog,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLayout,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QSplitter,
        QStatusBar,
        QToolBar,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    print(
        "\ud544\uc218 \ud328\ud0a4\uc9c0\uac00 \uc124\uce58\ub418\uc5b4 \uc788\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.\n"
        "\uc544\ub798 \uba85\ub839\uc73c\ub85c \uc124\uce58\ud558\uc138\uc694.\n"
        "  py -3 -m pip install PyQt5 pypdfium2 pypdf pillow\n\n"
        f"Import error: {exc}"
    )
    raise


APP_TITLE = "KoinfraPDFEdit"
PDF_FILTER = "PDF \ud30c\uc77c (*.pdf)"
PAGE_ROLE = Qt.UserRole + 1
APP_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", str(APP_DIR)))
APP_ICON_SVG = RESOURCE_DIR / "icon.svg"
APP_ICON2_ICO = RESOURCE_DIR / "icon2.ico"
APP_ICON_ICO = RESOURCE_DIR / "icon.ico"
PDF_DOC_ICON_NAME = "koinfra_pdf_doc.ico"
INSTALLER_APP_ID = "{6E0B5D4C-6F1E-4C0B-9B6B-3F7A1E2C9A51}"  # Inno Setup AppId (KoinfraPDF.iss)
TOOL_ICON_DIR = RESOURCE_DIR / "icons"
TOOL_ICON_SIZES = (16, 20, 22, 24, 28, 32, 40, 44, 48, 56, 64)
APP_EXE_NAME = "KoinfraPDFEdit.exe"
APP_USER_MODEL_ID = "Koinfra.PDFEdit.KoinfraPDFEdit"
APP_PROG_ID = "Koinfra.PDFEdit.Document"
APP_DEFAULTS_CAPABILITY_PATH = r"Software\Koinfra\KoinfraPDFEdit\Capabilities"
PRINT_DPI = 200
PRINT_MAX_RENDER_SCALE = 3.0
DEFAULT_APPROVAL_LINE = ("\ub2f4\ub2f9", "\uac80\ud1a0", "\uc2b9\uc778")


@dataclass(frozen=True)
class PageMark:
    kind: str
    rect: tuple
    data: tuple = ()


@dataclass(frozen=True)
class PageRef:
    path: str
    page_index: int
    rotation: int = 0
    marks: tuple = ()
    password: str = ""


def user_data_dir():
    base_dir = Path(os.environ.get("APPDATA") or APP_DIR)
    return base_dir / APP_TITLE


def user_preferences_path():
    return user_data_dir() / "preferences.json"


def normalize_approval_line(labels):
    if isinstance(labels, str):
        labels = labels.replace("\n", ",").split(",")
    if not isinstance(labels, (list, tuple)):
        return DEFAULT_APPROVAL_LINE

    cleaned = []
    for label in labels:
        value = str(label).strip()
        if value and value not in cleaned:
            cleaned.append(value[:24])
    return tuple(cleaned[:6]) or DEFAULT_APPROVAL_LINE


def _read_preferences_payload():
    try:
        payload = json.loads(user_preferences_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _write_preferences_payload(payload):
    preferences_path = user_preferences_path()
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    preferences_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_user_preferences():
    payload = _read_preferences_payload()

    approval_line = normalize_approval_line(payload.get("approval_line"))
    stamp_images = []
    stored_images = payload.get("stamp_images", [])
    for value in stored_images if isinstance(stored_images, list) else []:
        path = Path(value)
        if path.is_file() and path.suffix.lower() == ".png":
            stamp_images.append(str(path))
    return approval_line, stamp_images


def save_user_preferences(approval_line, stamp_images):
    payload = _read_preferences_payload()
    payload["approval_line"] = list(normalize_approval_line(approval_line))
    payload["stamp_images"] = [str(Path(path)) for path in stamp_images if Path(path).is_file()]
    _write_preferences_payload(payload)


STAMP_POSITIONS = (
    ("bottom_right", "오른쪽 아래"),
    ("top_right", "오른쪽 위"),
    ("bottom_left", "왼쪽 아래"),
    ("top_left", "왼쪽 위"),
    ("center", "가운데"),
)
DEFAULT_STAMP_SETTINGS = {"image": "", "width_percent": 16, "opacity_percent": 100, "position": "bottom_right"}
DEFAULT_SIGNATURE_SETTINGS = {"image": "", "cell": 0}


def _clamped_int(value, default, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def normalize_stamp_settings(value):
    value = value if isinstance(value, dict) else {}
    image = str(value.get("image") or "")
    if image and not Path(image).is_file():
        image = ""
    position = value.get("position")
    if position not in {key for key, _ in STAMP_POSITIONS}:
        position = DEFAULT_STAMP_SETTINGS["position"]
    return {
        "image": image,
        "width_percent": _clamped_int(value.get("width_percent"), DEFAULT_STAMP_SETTINGS["width_percent"], 5, 60),
        "opacity_percent": _clamped_int(value.get("opacity_percent"), DEFAULT_STAMP_SETTINGS["opacity_percent"], 20, 100),
        "position": position,
    }


def normalize_signature_settings(value):
    value = value if isinstance(value, dict) else {}
    image = str(value.get("image") or "")
    if image and not Path(image).is_file():
        image = ""
    return {"image": image, "cell": _clamped_int(value.get("cell"), 0, 0, 5)}


def load_stamp_settings():
    return normalize_stamp_settings(_read_preferences_payload().get("stamp_settings"))


def save_stamp_settings(settings):
    payload = _read_preferences_payload()
    payload["stamp_settings"] = normalize_stamp_settings(settings)
    _write_preferences_payload(payload)


def load_signature_settings():
    return normalize_signature_settings(_read_preferences_payload().get("signature"))


def save_signature_settings(settings):
    payload = _read_preferences_payload()
    payload["signature"] = normalize_signature_settings(settings)
    _write_preferences_payload(payload)


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


def png_image_to_pdf_page_bytes(image_path, opacity=1.0):
    """Build a one-page PDF (page size = image px) that keeps PNG transparency via an /SMask."""
    with Image.open(image_path) as source:
        image = source.convert("RGBA")
    alpha = image.getchannel("A")
    if opacity < 1.0:
        factor = max(0.0, min(1.0, opacity))
        alpha = alpha.point(lambda value: int(value * factor))
    width, height = image.size
    rgb_data = zlib.compress(image.convert("RGB").tobytes())
    alpha_data = zlib.compress(alpha.tobytes())
    content = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
        f"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>".encode("ascii"),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
        f"/BitsPerComponent 8 /Filter /FlateDecode /SMask 6 0 R /Length {len(rgb_data)} >>\nstream\n".encode("ascii")
        + rgb_data
        + b"\nendstream",
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray "
        f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(alpha_data)} >>\nstream\n".encode("ascii")
        + alpha_data
        + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_offset = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_offset)
    return bytes(output)


def remove_white_background(image_path, threshold=235):
    with Image.open(image_path) as source:
        image = source.convert("RGBA")
    red, green, blue, alpha = image.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    keep_mask = darkest.point(lambda value: 0 if value >= threshold else 255)
    image.putalpha(ImageChops.darker(alpha, keep_mask))
    image.save(image_path, "PNG")


def parse_page_ranges(text, total_pages):
    pages = []
    seen = set()

    for chunk in text.replace(" ", "").split(","):
        if not chunk:
            continue

        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start = int(start_text)
            end = int(end_text)
        else:
            start = end = int(chunk)

        if start < 1 or end < 1 or start > total_pages or end > total_pages:
            raise ValueError(
                f"\ud398\uc774\uc9c0 \ubc94\uc704\ub294 1-{total_pages} \uc548\uc5d0\uc11c \uc785\ub825\ud574\uc57c \ud569\ub2c8\ub2e4: {chunk}"
            )
        if start > end:
            raise ValueError(f"\uc2dc\uc791 \ud398\uc774\uc9c0\uac00 \ub05d \ud398\uc774\uc9c0\ubcf4\ub2e4 \ud07d\ub2c8\ub2e4: {chunk}")

        for page_number in range(start, end + 1):
            page_index = page_number - 1
            if page_index not in seen:
                seen.add(page_index)
                pages.append(page_index)

    if not pages:
        raise ValueError("\uc120\ud0dd\ub41c \ud398\uc774\uc9c0\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.")

    return pages


def page_refs_for_pdf(path, password=""):
    with pdfium.PdfDocument(path, password=password or None) as doc:
        return [PageRef(path, index, password=password) for index in range(len(doc))]


def load_app_icon():
    # Prefer the multi-size ICO: 16~48px use the simplified "K" monogram, 64px+ the full logo.
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


def configure_windows_taskbar_icon():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def installed_exe_path():
    """KoinfraPDFEdit.exe installed by the Koinfra PDF installer (per-user or all users), else None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    if sys.platform != "win32":
        return None
    subkey = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + INSTALLER_APP_ID + "_is1"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                location = winreg.QueryValueEx(key, "InstallLocation")[0]
        except OSError:
            continue
        exe = Path(location) / APP_EXE_NAME
        if exe.is_file():
            return exe
    return None


def app_icon_reference():
    exe = installed_exe_path()
    if exe is not None:
        return f"{exe},0"
    if APP_ICON2_ICO.is_file():
        return str(APP_ICON2_ICO)
    if APP_ICON_ICO.is_file():
        return str(APP_ICON_ICO)
    return str(Path(__file__).resolve())


def pdf_document_icon_reference():
    """Blue Koinfra PDF-document icon for files of our ProgID (falls back to the app icon)."""
    exe = installed_exe_path()
    candidates = [exe.parent / PDF_DOC_ICON_NAME] if exe is not None else []
    candidates += [RESOURCE_DIR / PDF_DOC_ICON_NAME, APP_DIR / PDF_DOC_ICON_NAME]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return app_icon_reference()


def build_openwith_command():
    exe = installed_exe_path()
    if exe is not None:
        return f'"{exe}" "%1"'
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    return f'"{executable}" "{Path(__file__).resolve()}" "%1"'


# ---------------------------------------------------------------- PDF file association (per user, HKCU only)
# Windows 10/11 protect the real default (UserChoice + hash). We never write UserChoice: we only register
# the app as a PDF handler and open the Windows "Default apps" page so the user picks it there.
ASSOC_BASE = "Software"  # tests inject a throwaway root such as Software\KoinfraTest\<id>
APP_FRIENDLY_NAME = "Koinfra PDF"
APP_REGISTERED_NAME = APP_TITLE  # value name under RegisteredApplications (= registeredAppUser=...)
SHCNE_ASSOCCHANGED = 0x08000000


def _assoc_paths(base=None):
    base = base or ASSOC_BASE
    classes = base + r"\Classes"
    company = base + r"\Koinfra\KoinfraPDFEdit"
    return {
        "app": classes + "\\Applications\\" + APP_EXE_NAME,
        "progid": classes + "\\" + APP_PROG_ID,
        "openwith": classes + r"\.pdf\OpenWithProgids",
        "openwithlist": classes + r"\.pdf\OpenWithList" + "\\" + APP_EXE_NAME,
        "company": company,
        "capabilities": company + r"\Capabilities",
        "registered": base + r"\RegisteredApplications",
        "fileexts": base + r"\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf",
    }


def _reg_set(path, name, value, hive=None):
    with winreg.CreateKeyEx(hive or winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _reg_get(path, name, hive=None):
    try:
        with winreg.OpenKey(hive or winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _reg_delete_tree(path, hive=None):
    hive = hive or winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            children = []
            index = 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return
    for child in children:
        _reg_delete_tree(path + "\\" + child, hive)
    try:
        winreg.DeleteKey(hive, path)
    except OSError:
        pass


def _reg_delete_value(path, name, hive=None):
    try:
        with winreg.OpenKey(hive or winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def notify_association_changed():
    try:
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)
    except (AttributeError, OSError):
        pass


def association_target_warning():
    """Explain when the registered command points at a location that may disappear (build output)."""
    target = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    lowered = [part.lower() for part in target.parts]
    if any(part.startswith(("dist", "build")) for part in lowered) or "temp" in lowered:
        return (
            f"\ud604\uc7ac \uc2e4\ud589 \uc704\uce58({target.parent})\uac00 \ube4c\ub4dc/\uc784\uc2dc \ud3f4\ub354\uc785\ub2c8\ub2e4. "
            "\ud30c\uc77c\uc744 \uc62e\uae30\uac70\ub098 \uc9c0\uc6b0\uba74 \uc5f0\uacb0\uc774 \ub04a\uc5b4\uc9c0\ub2c8 \uc124\uce58\ubcf8 \uc0ac\uc6a9\uc744 \uad8c\uc7a5\ud569\ub2c8\ub2e4."
        )
    return ""


def register_openwith(base=None, hive=None, notify=True):
    """Register Koinfra PDF as a PDF handler for the current user (Open with list + Default apps page)."""
    paths = _assoc_paths(base)
    command = build_openwith_command()
    icon_ref = app_icon_reference()

    _reg_set(paths["app"], "FriendlyAppName", APP_FRIENDLY_NAME, hive)
    _reg_set(paths["app"] + r"\DefaultIcon", "", icon_ref, hive)
    _reg_set(paths["app"] + r"\SupportedTypes", ".pdf", "", hive)
    _reg_set(paths["app"] + r"\shell\open\command", "", command, hive)

    _reg_set(paths["progid"], "", "Koinfra PDF \ubb38\uc11c", hive)
    _reg_set(paths["progid"], "FriendlyTypeName", "Koinfra PDF \ubb38\uc11c", hive)
    _reg_set(paths["progid"] + r"\DefaultIcon", "", pdf_document_icon_reference(), hive)
    _reg_set(paths["progid"] + r"\shell\open", "FriendlyAppName", APP_FRIENDLY_NAME, hive)
    _reg_set(paths["progid"] + r"\shell\open\command", "", command, hive)
    _reg_set(paths["openwith"], APP_PROG_ID, "", hive)
    _reg_set(paths["openwithlist"], "", "", hive)
    _reg_set(paths["progid"], "AppUserModelID", APP_USER_MODEL_ID, hive)

    _reg_set(paths["capabilities"], "ApplicationName", APP_FRIENDLY_NAME, hive)
    _reg_set(paths["capabilities"], "ApplicationDescription", "Koinfra PDF \ud3b8\uc9d1\uae30 (\ubcd1\ud569\u00b7\ub3c4\uc7a5\u00b7\uacb0\uc7ac)", hive)
    _reg_set(paths["capabilities"], "ApplicationIcon", icon_ref, hive)
    _reg_set(paths["capabilities"] + r"\FileAssociations", ".pdf", APP_PROG_ID, hive)
    _reg_set(paths["registered"], APP_REGISTERED_NAME, paths["capabilities"], hive)
    if notify:
        notify_association_changed()
    return paths


def unregister_openwith(base=None, hive=None, notify=True):
    """Remove everything register_openwith() wrote. The Windows UserChoice is left alone (it is protected)."""
    paths = _assoc_paths(base)
    _reg_delete_tree(paths["app"], hive)
    _reg_delete_tree(paths["progid"], hive)
    _reg_delete_value(paths["openwith"], APP_PROG_ID, hive)
    _reg_delete_tree(paths["openwithlist"], hive)
    _reg_delete_value(paths["registered"], APP_REGISTERED_NAME, hive)
    _reg_delete_tree(paths["company"], hive)
    company_root = paths["company"].rsplit("\\", 1)[0]
    try:
        winreg.DeleteKey(hive or winreg.HKEY_CURRENT_USER, company_root)  # only succeeds when empty
    except OSError:
        pass
    if notify:
        notify_association_changed()


def is_pdf_association_registered(base=None, hive=None):
    paths = _assoc_paths(base)
    return bool(_reg_get(paths["registered"], APP_REGISTERED_NAME, hive)) and bool(
        _reg_get(paths["progid"] + r"\shell\open\command", "", hive)
    )


def current_pdf_default_progid(base=None, hive=None):
    """Read-only: the ProgId Windows uses for .pdf (UserChoiceLatest on new Windows 11, else UserChoice)."""
    paths = _assoc_paths(base)
    return _reg_get(paths["fileexts"] + r"\UserChoiceLatest\ProgId", "ProgId", hive) or _reg_get(
        paths["fileexts"] + r"\UserChoice", "ProgId", hive
    )


def is_pdf_default_app(base=None, hive=None):
    return current_pdf_default_progid(base, hive) in (APP_PROG_ID, "Applications\\" + APP_EXE_NAME)


def default_apps_settings_target():
    """Windows 11 (build 22000+): Settings > Default apps opened on this app's page; Windows 10: Default apps."""
    try:
        build = sys.getwindowsversion().build
    except AttributeError:
        build = 0
    if build >= 22000:
        return f"ms-settings:defaultapps?registeredAppUser={APP_REGISTERED_NAME}"
    return "ms-settings:defaultapps"


def open_default_pdf_app_settings(launcher=None):
    if sys.platform != "win32" and launcher is None:
        raise OSError("Windows default app settings are only available on Windows.")
    register_openwith()
    target = default_apps_settings_target()
    (launcher or os.startfile)(target)
    return target


# Windows 'choose an app' picker. Windows 11 blocks SHOpenWithDialog for apps ("go to Settings > Default apps"),
# so we ask the shell's own picker (rundll32 shell32,OpenAs_RunDLL) for one small check PDF and verify that a real
# picker window appeared; otherwise we fall back to the Settings page. The choice itself is always the user's.
OPEN_WITH_PICKER_TIMEOUT_MS = 6000
DEFAULT_APP_WATCH_MS = 5 * 60 * 1000
DEFAULT_APP_GUIDE_PICKER = "\ub728\ub294 \ucc3d\uc5d0\uc11c Koinfra PDF \uc120\ud0dd \u2192 '\ud56d\uc0c1' \ud074\ub9ad"
DEFAULT_APP_GUIDE_SETTINGS = "\uc124\uc815 \ucc3d\uc5d0\uc11c .pdf \ud074\ub9ad \u2192 Koinfra PDF \uc120\ud0dd \u2192 '\uae30\ubcf8\uac12 \uc124\uc815' \ud074\ub9ad"
DEFAULT_APP_DONE_TEXT = "\u2714 \uc124\uc815 \uc644\ub8cc"


def default_app_check_pdf_path():
    """A one-page PDF in %TEMP% used only as the picker's sample file (opens in Koinfra after 'Always')."""
    folder = Path(tempfile.gettempdir()) / "KoinfraPDFEdit"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "Koinfra_PDF_default_app_check.pdf"
    if path.is_file() and path.stat().st_size > 0:
        return path
    from PyQt5.QtGui import QPdfWriter, QPageSize

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setResolution(72)
    writer.setTitle("Koinfra PDF")
    painter = QPainter(writer)
    painter.setPen(QColor("#1F5EFF"))
    painter.setFont(QFont("Malgun Gothic", 22, QFont.Bold))
    painter.drawText(QRectF(40, 260, 515, 60), Qt.AlignCenter, DEFAULT_APP_DONE_TEXT)
    painter.setPen(QColor("#334155"))
    painter.setFont(QFont("Malgun Gothic", 13))
    painter.drawText(
        QRectF(40, 330, 515, 60), Qt.AlignCenter,
        "\uc774\uc81c PDF \ud30c\uc77c\uc744 \uc5f4\uba74 Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4.\n\uc774 \ucc3d\uc740 \ub2eb\uc544\ub3c4 \ub429\ub2c8\ub2e4.",
    )
    painter.end()
    return path


def _process_image_name(pid):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def open_with_process_ids():
    """PIDs of running OpenWith.exe processes (the Windows app picker host)."""
    if sys.platform != "win32":
        return set()
    array = (ctypes.c_ulong * 4096)()
    needed = ctypes.c_ulong()
    try:
        if not ctypes.windll.psapi.EnumProcesses(array, ctypes.sizeof(array), ctypes.byref(needed)):
            return set()
    except (AttributeError, OSError):
        return set()
    count = needed.value // ctypes.sizeof(ctypes.c_ulong)
    return {pid for pid in array[:count] if pid and _process_image_name(pid) == "openwith.exe"}


def _visible_windows_of(pids):
    """Visible, non-minimized top-level windows (hwnd, pid, class) owned by the given processes."""
    if sys.platform != "win32" or not pids:
        return []
    user32 = ctypes.windll.user32
    found = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _param):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(ctypes.c_void_p(hwnd)) and not user32.IsIconic(ctypes.c_void_p(hwnd)):
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(ctypes.c_void_p(hwnd), name, 256)
            if "dummy" not in name.value.lower() and name.value not in ("IME", "MSCTFIME UI"):
                found.append((hwnd, pid.value, name.value))
        return True

    user32.EnumWindows(proc_type(callback), None)
    return found


def launch_open_with_picker(pdf_path):
    """Ask the Windows shell to show its 'choose an app' picker for pdf_path. Returns the helper process."""
    if sys.platform != "win32":
        raise OSError("The Windows app picker is only available on Windows.")
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_ulong(0xFFFFFFFF))  # ASFW_ANY: let the picker come to front
    except (AttributeError, OSError):
        pass
    rundll = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "rundll32.exe"
    return subprocess.Popen([str(rundll), "shell32.dll,OpenAs_RunDLL", str(pdf_path)])


def close_windows(windows):
    for hwnd, _pid, _cls in windows:
        try:
            ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE
        except (AttributeError, OSError):
            pass


def stop_open_with_leftovers(process, before_pids):
    """After a failed attempt: end our rundll32 helper and the picker hosts it started (never pre-existing ones)."""
    if process is not None and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    for pid in open_with_process_ids() - set(before_pids or ()):
        if _visible_windows_of({pid}):
            continue  # it has a real window after all; leave it to the user
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 0)
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            pass


def load_association_preferences():
    value = _read_preferences_payload().get("pdf_association")
    value = value if isinstance(value, dict) else {}
    return {"disabled": bool(value.get("disabled", False)), "ask_default": bool(value.get("ask_default", True))}


def save_association_preferences(**changes):
    payload = _read_preferences_payload()
    current = load_association_preferences()
    current.update({key: bool(value) for key, value in changes.items() if key in current})
    payload["pdf_association"] = current
    _write_preferences_payload(payload)
    return current


_TOOL_ICON_CACHE = {}


def make_tool_icon(kind, size=32):
    """Load the Lucide-based line icon from icons/<kind>_<px>.png (multi-size, HiDPI-ready).

    Falls back to the legacy painter-drawn icon when the icon files are missing.
    """
    cached = _TOOL_ICON_CACHE.get(kind)
    if cached is not None:
        return cached
    icon = QIcon()
    for px in TOOL_ICON_SIZES:
        icon_path = TOOL_ICON_DIR / f"{kind}_{px}.png"
        if icon_path.is_file():
            icon.addFile(str(icon_path), QSize(px, px))
    if icon.isNull():
        return _draw_legacy_tool_icon(kind, size)
    _TOOL_ICON_CACHE[kind] = icon
    return icon


def _draw_legacy_tool_icon(kind, size=32):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    palette = {
        "open": ("#2563eb", "#dbeafe"),
        "save": ("#0f766e", "#ccfbf1"),
        "merge": ("#7c3aed", "#ede9fe"),
        "extract": ("#ea580c", "#ffedd5"),
        "insert": ("#16a34a", "#dcfce7"),
        "prev": ("#475569", "#f1f5f9"),
        "next": ("#475569", "#f1f5f9"),
        "zoom_out": ("#1d4ed8", "#dbeafe"),
        "zoom_in": ("#be123c", "#ffe4e6"),
        "print": ("#374151", "#f3f4f6"),
        "rotate_left": ("#0f766e", "#ccfbf1"),
        "rotate_right": ("#0f766e", "#ccfbf1"),
        "rect_mark": ("#dc2626", "#fee2e2"),
        "highlight": ("#ca8a04", "#fef3c7"),
        "approval_stamp": ("#1f2937", "#e5e7eb"),
        "cut": ("#b45309", "#ffedd5"),
        "copy": ("#0369a1", "#e0f2fe"),
        "paste": ("#4d7c0f", "#ecfccb"),
        "delete": ("#991b1b", "#fee2e2"),
        "info": ("#334155", "#e2e8f0"),
        "lock": ("#7c2d12", "#ffedd5"),
        "unlock": ("#047857", "#d1fae5"),
    }

    stroke_hex, fill_hex = palette.get(kind, ("#2563eb", "#dbeafe"))
    color = QColor(stroke_hex)
    fill = QColor(fill_hex)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    scale = size / 32

    def u(value):
        return value * scale

    def pt(x, y):
        return QPointF(u(x), u(y))

    def rect(x, y, w, h):
        return QRectF(u(x), u(y), u(w), u(h))

    def set_pen(width=2.15, pen_color=None):
        painter.setPen(QPen(pen_color or color, u(width), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def draw_badge():
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 23, 42, 24))
        painter.drawRoundedRect(rect(2.5, 3.5, 27, 27), u(8), u(8))
        painter.setPen(QPen(QColor("#d8e2ef"), u(0.85)))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect(2, 2, 28, 28), u(8), u(8))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(pt(24.2, 7.8), u(2.35), u(2.35))

    def draw_page(x, y, w=14, h=18, lines=True):
        painter.setBrush(QColor("#ffffff"))
        set_pen(1.7)
        painter.drawRoundedRect(rect(x, y, w, h), u(2.8), u(2.8))
        painter.drawLine(pt(x + w - 4, y), pt(x + w, y + 4))
        if lines:
            set_pen(1.25, QColor("#94a3b8"))
            painter.drawLine(pt(x + 4, y + 7), pt(x + w - 4, y + 7))
            painter.drawLine(pt(x + 4, y + 11), pt(x + w - 5, y + 11))

    def draw_arrow(x1, y1, x2, y2, direction="right", width=2.2):
        set_pen(width)
        painter.drawLine(pt(x1, y1), pt(x2, y2))
        if direction == "right":
            painter.drawLine(pt(x2, y2), pt(x2 - 4, y2 - 4))
            painter.drawLine(pt(x2, y2), pt(x2 - 4, y2 + 4))
        elif direction == "left":
            painter.drawLine(pt(x2, y2), pt(x2 + 4, y2 - 4))
            painter.drawLine(pt(x2, y2), pt(x2 + 4, y2 + 4))
        elif direction == "up":
            painter.drawLine(pt(x2, y2), pt(x2 - 4, y2 + 4))
            painter.drawLine(pt(x2, y2), pt(x2 + 4, y2 + 4))
        else:
            painter.drawLine(pt(x2, y2), pt(x2 - 4, y2 - 4))
            painter.drawLine(pt(x2, y2), pt(x2 + 4, y2 - 4))

    draw_badge()
    set_pen()

    if kind == "open":
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect(6, 11, 20, 13), u(3), u(3))
        painter.drawLine(pt(7.5, 11), pt(11, 7.5))
        painter.drawLine(pt(11, 7.5), pt(17, 7.5))
        painter.drawLine(pt(17, 7.5), pt(20, 11))
        draw_arrow(16, 20.5, 22.5, 16, "right", 1.8)
    elif kind == "save":
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect(8, 6, 16, 20), u(3), u(3))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect(11, 7, 8, 6), u(1.5), u(1.5))
        painter.drawRoundedRect(rect(11, 20, 10, 4), u(1.5), u(1.5))
        painter.drawLine(pt(21, 7), pt(21, 13))
    elif kind == "merge":
        draw_page(6, 8, 12, 16)
        draw_page(15, 8, 12, 16)
        draw_arrow(12, 24.5, 20, 24.5, "right", 1.8)
    elif kind == "extract":
        draw_page(6, 7, 15, 19)
        draw_arrow(17, 16, 26, 16, "right")
    elif kind == "insert":
        draw_page(6, 7, 15, 19)
        set_pen(2.3)
        painter.drawLine(pt(23.5, 11), pt(23.5, 21))
        painter.drawLine(pt(18.5, 16), pt(28.5, 16))
    elif kind == "prev":
        draw_arrow(24, 16, 9, 16, "left", 2.6)
    elif kind == "next":
        draw_arrow(8, 16, 23, 16, "right", 2.6)
    elif kind == "zoom_in" or kind == "zoom_out":
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(pt(14, 14), u(6), u(6))
        painter.drawLine(pt(18.5, 18.5), pt(24, 24))
        painter.drawLine(pt(10, 14), pt(18, 14))
        if kind == "zoom_in":
            painter.drawLine(pt(14, 10), pt(14, 18))
    elif kind == "print":
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect(7, 11, 18, 10), u(3), u(3))
        painter.drawRoundedRect(rect(10, 6, 12, 7), u(1.5), u(1.5))
        painter.drawRoundedRect(rect(10, 18, 12, 7), u(1.5), u(1.5))
        painter.setBrush(color)
        painter.drawEllipse(pt(22, 15), u(1), u(1))
    elif kind == "rotate_left" or kind == "rotate_right":
        arc_rect = rect(8, 8, 16, 16)
        if kind == "rotate_left":
            painter.drawArc(arc_rect, 30 * 16, 285 * 16)
            draw_arrow(12, 8.5, 7.5, 8.5, "left", 2)
        else:
            painter.drawArc(arc_rect, -135 * 16, 285 * 16)
            draw_arrow(20, 8.5, 24.5, 8.5, "right", 2)
    elif kind == "rect_mark":
        painter.setBrush(QColor(255, 255, 255, 155))
        painter.drawRoundedRect(rect(7, 9, 18, 14), u(2.5), u(2.5))
        set_pen(1.4, QColor("#fca5a5"))
        painter.drawLine(pt(10, 13), pt(22, 13))
    elif kind == "highlight":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(250, 204, 21, 190))
        painter.drawRoundedRect(rect(6, 15, 20, 6), u(2), u(2))
        set_pen(2)
        painter.drawLine(pt(10, 10), pt(20, 20))
        painter.drawLine(pt(20, 20), pt(24, 16))
        set_pen(1.4, QColor("#854d0e"))
        painter.drawLine(pt(8, 23), pt(24, 23))
    elif kind == "approval_stamp":
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect(7, 10, 18, 13), u(2.5), u(2.5))
        set_pen(1.2, QColor("#9ca3af"))
        painter.drawLine(pt(13, 10), pt(13, 23))
        painter.drawLine(pt(19, 10), pt(19, 23))
        painter.drawLine(pt(7, 15), pt(25, 15))
        set_pen(1.9)
        painter.drawLine(pt(12, 7), pt(20, 7))
        painter.drawLine(pt(16, 7), pt(16, 10))
        painter.drawEllipse(pt(16, 19), u(1.8), u(1.8))
    elif kind == "cut":
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(pt(10, 22), u(3), u(3))
        painter.drawEllipse(pt(22, 22), u(3), u(3))
        painter.drawLine(pt(12, 20), pt(23, 9))
        painter.drawLine(pt(20, 20), pt(9, 9))
    elif kind == "copy":
        draw_page(8, 11, 13, 15)
        draw_page(13, 7, 13, 15)
    elif kind == "paste":
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect(9, 9, 16, 18), u(3), u(3))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect(12, 6, 10, 6), u(2), u(2))
        set_pen(1.25, QColor("#84cc16"))
        painter.drawLine(pt(13, 17), pt(21, 17))
        painter.drawLine(pt(13, 21), pt(19, 21))
    elif kind == "delete":
        set_pen(2.6)
        painter.drawLine(pt(10, 10), pt(22, 22))
        painter.drawLine(pt(22, 10), pt(10, 22))
    elif kind == "info":
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(pt(16, 10), u(2.15), u(2.15))
        painter.drawRoundedRect(rect(14.25, 14, 3.5, 10), u(1.75), u(1.75))
    elif kind == "lock" or kind == "unlock":
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect(8, 14, 16, 11), u(3), u(3))
        set_pen(2)
        if kind == "unlock":
            painter.drawArc(rect(10, 7, 11, 11), 25 * 16, 230 * 16)
        else:
            painter.drawArc(rect(10, 7, 12, 12), 0, 180 * 16)
        painter.drawLine(pt(10, 14), pt(10, 13))
        painter.drawLine(pt(22, 14), pt(22, 13))
        painter.setBrush(color)
        painter.drawEllipse(pt(16, 19), u(1.3), u(1.3))
        painter.drawLine(pt(16, 20.5), pt(16, 22.5))

    painter.end()
    return QIcon(pixmap)


def make_page_icon(rotation=0):
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
    return QIcon(pixmap)


class PageThumbnailList(QListWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.drop_indicator_row = None
        self.drag_rows = []
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Fixed scrollbar policy: the vertical bar never toggles (it would change the width -> clipping),
        # and thumbnails are always fitted to the width, so no horizontal scrolling.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.owner.schedule_thumbnail_refresh()

    def startDrag(self, supported_actions):
        self.drag_rows = self.drag_rows or self.selected_page_rows()
        self.restore_drag_selection()
        super().startDrag(Qt.CopyAction)

    def mousePressEvent(self, event):
        pressed_index = self.indexAt(event.pos())
        selected_rows = self.selected_page_rows()

        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier and pressed_index.isValid():
            item = self.item(pressed_index.row())
            if item is not None:
                was_selected = item.isSelected()
                item.setSelected(not was_selected)
                if not was_selected:
                    self.setCurrentItem(item)
            self.drag_rows = []
            event.accept()
            return

        if event.button() == Qt.LeftButton and pressed_index.isValid() and pressed_index.row() in selected_rows:
            self.drag_rows = selected_rows
        else:
            self.drag_rows = []
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if len(self.drag_rows) > 1:
            self.restore_drag_selection()
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Undo):
            self.owner.undo()
            event.accept()
            return
        if event.matches(QKeySequence.Redo):
            self.owner.redo()
            event.accept()
            return
        if event.matches(QKeySequence.Save):
            self.owner.save_current()
            event.accept()
            return
        if event.matches(QKeySequence.Print):
            self.owner.print_pdf()
            event.accept()
            return
        if event.key() == Qt.Key_Delete and self.owner.selected_mark_index is not None:
            self.owner.delete_selected_mark()
            event.accept()
            return
        if event.matches(QKeySequence.Cut):
            self.owner.cut_selected_pages()
            return
        if event.matches(QKeySequence.Copy):
            self.owner.copy_selected_pages()
            return
        if event.matches(QKeySequence.Paste):
            self.owner.paste_pages_after_current()
            return
        if event.key() == Qt.Key_Delete:
            self.owner.delete_selected_pages()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        cut_action = menu.addAction("\uc798\ub77c\ub0b4\uae30")
        copy_action = menu.addAction("\ubcf5\uc0ac\ud558\uae30")
        paste_action = menu.addAction("\ubd99\uc5ec\ub123\uae30")
        menu.addSeparator()
        rotate_right_action = menu.addAction("\uc624\ub978\ucabd \ud68c\uc804")
        rotate_left_action = menu.addAction("\uc67c\ucabd \ud68c\uc804")
        menu.addSeparator()
        delete_action = menu.addAction("\uc9c0\uc6b0\uae30")

        has_selection = bool(self.selectedIndexes())
        cut_action.setEnabled(has_selection)
        copy_action.setEnabled(has_selection)
        rotate_right_action.setEnabled(has_selection)
        rotate_left_action.setEnabled(has_selection)
        delete_action.setEnabled(has_selection)
        paste_action.setEnabled(self.owner.has_clipboard_pages())

        selected_action = menu.exec_(event.globalPos())
        if selected_action == cut_action:
            self.owner.cut_selected_pages()
        elif selected_action == copy_action:
            self.owner.copy_selected_pages()
        elif selected_action == paste_action:
            self.owner.paste_pages_after_current()
        elif selected_action == rotate_right_action:
            self.owner.rotate_selected_pages_right()
        elif selected_action == rotate_left_action:
            self.owner.rotate_selected_pages_left()
        elif selected_action == delete_action:
            self.owner.delete_selected_pages()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self.has_pdf_urls(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.has_pdf_urls(event):
            self.drop_indicator_row = self.drop_insert_row(event.pos())
            self.viewport().update()
            event.acceptProposedAction()
            return
        if event.source() is self:
            self.drop_indicator_row = self.drop_insert_row(event.pos())
            self.viewport().update()
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if self.has_pdf_urls(event):
            insert_row = self.drop_insert_row(event.pos())
            self.drop_indicator_row = None
            self.viewport().update()
            self.owner.insert_pdf_files(self.pdf_paths_from_event(event), insert_row)
            event.acceptProposedAction()
            return
        if event.source() is self:
            self.move_selected_items_to_drop_position(event)
            self.drop_indicator_row = None
            self.viewport().update()
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        super().dropEvent(event)
        self.drop_indicator_row = None
        self.viewport().update()
        self.owner.after_page_order_changed()

    def dragLeaveEvent(self, event):
        self.drop_indicator_row = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.drop_indicator_row is None:
            return

        y = self.drop_indicator_y(self.drop_indicator_row)
        painter = QPainter(self.viewport())
        pen = QPen(QColor("#d63b3b"), 2, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(12, y, self.viewport().width() - 12, y)
        painter.end()

    def has_pdf_urls(self, event):
        mime_data = event.mimeData()
        return any(self.is_pdf_url(url) for url in mime_data.urls())

    def pdf_paths_from_event(self, event):
        return [url.toLocalFile() for url in event.mimeData().urls() if self.is_pdf_url(url)]

    def is_pdf_url(self, url):
        return url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")

    def drop_insert_row(self, position):
        target_index = self.indexAt(position)
        if not target_index.isValid():
            return self.drop_insert_row_from_gap(position)

        target_row = target_index.row()
        rect = self.visualItemRect(self.item(target_row))
        if position.y() > rect.center().y():
            return target_row + 1
        return target_row

    def drop_insert_row_from_gap(self, position):
        if self.count() == 0:
            return 0

        for row in range(self.count()):
            rect = self.visualItemRect(self.item(row))
            if position.y() < rect.center().y():
                return row
        return self.count()

    def drop_indicator_y(self, row):
        if self.count() == 0:
            return self.viewport().height() // 2

        if row <= 0:
            rect = self.visualItemRect(self.item(0))
            return max(2, rect.top() - 4)

        if row >= self.count():
            rect = self.visualItemRect(self.item(self.count() - 1))
            return min(self.viewport().height() - 2, rect.bottom() + 7)

        rect = self.visualItemRect(self.item(row))
        return max(2, rect.top() - 4)

    def move_selected_items_to_drop_position(self, event):
        selected_rows = self.drag_rows or self.selected_page_rows()
        self.drag_rows = []
        if not selected_rows:
            return

        target_row = self.drop_insert_row(event.pos())
        self.owner.move_pages_by_rows(selected_rows, target_row)

    def selected_page_rows(self):
        return sorted({index.row() for index in self.selectedIndexes()})

    def restore_drag_selection(self):
        if not self.drag_rows:
            return

        self.blockSignals(True)
        self.clearSelection()
        for row in self.drag_rows:
            item = self.item(row)
            if item is not None:
                item.setSelected(True)
        self.blockSignals(False)


class PdfScrollArea(QScrollArea):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.owner.schedule_responsive_preview_refresh()

    def wheelEvent(self, event):
        if self.owner.page_count <= 0:
            super().wheelEvent(event)
            return

        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.owner.zoom_in(event.pos())
            else:
                self.owner.zoom_out(event.pos())
        else:
            if event.angleDelta().y() > 0:
                self.owner.previous_page()
            else:
                self.owner.next_page()
        event.accept()

    def contextMenuEvent(self, event):
        if self.owner.page_count <= 0:
            return

        menu = QMenu(self)
        rotate_right = menu.addAction(make_tool_icon("rotate_right"), "\uc624\ub978\ucabd \ud68c\uc804")
        rotate_left = menu.addAction(make_tool_icon("rotate_left"), "\uc67c\ucabd \ud68c\uc804")
        menu.addSeparator()
        rect_mark = menu.addAction(make_tool_icon("rect_mark"), "\ube68\uac04 \uc0ac\uac01\ud615")
        highlight = menu.addAction(make_tool_icon("highlight"), "\ud615\uad11\ud39c")
        stamp = menu.addAction(make_tool_icon("stamp"), "도장")
        approval_stamp = menu.addAction(make_tool_icon("signature"), "결재 (내 서명)")
        selected_action = menu.exec_(event.globalPos())
        if selected_action == rotate_right:
            self.owner.rotate_current_page(90)
        elif selected_action == rotate_left:
            self.owner.rotate_current_page(-90)
        elif selected_action == rect_mark:
            self.owner.enable_rectangle_mode()
        elif selected_action == highlight:
            self.owner.enable_highlight_mode()
        elif selected_action == stamp:
            self.owner.insert_default_stamp()
        elif selected_action == approval_stamp:
            self.owner.insert_signed_approval()


class PdfCanvasLabel(QLabel):
    def __init__(self, owner, text=""):
        super().__init__(text)
        self.owner = owner
        self.drag_start = None
        self.drag_rect = None
        self.edit_mark_index = None
        self.edit_action = None
        self.edit_original_rect = None
        self.edit_snapshot = None
        self.pan_active = False
        self.pan_last_pos = None
        self.pan_last_global_pos = None
        self.setFocusPolicy(Qt.StrongFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.owner.annotation_mode:
            self.setFocus()
            self.drag_start = event.pos()
            self.drag_rect = QRectF(self.drag_start, self.drag_start)
            self.owner.update_preview_drag_rect(self.drag_rect)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            hit = self.owner.hit_test_mark(event.pos())
            if hit is not None:
                mark_index, action = hit
                self.setFocus()
                self.drag_start = event.pos()
                self.edit_mark_index = mark_index
                self.edit_action = action
                self.edit_original_rect = self.owner.current_mark_rect(mark_index)
                self.edit_snapshot = self.owner.make_history_snapshot()
                self.owner.select_mark(mark_index)
                event.accept()
                return
            if self.owner.can_pan_preview():
                self.setFocus()
                self.pan_active = True
                self.pan_last_pos = event.pos()
                self.pan_last_global_pos = event.globalPos()
                self.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return
            self.owner.select_mark(None)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None and self.owner.annotation_mode:
            self.drag_rect = QRectF(self.drag_start, event.pos()).normalized()
            self.owner.update_preview_drag_rect(self.drag_rect)
            event.accept()
            return
        if self.edit_mark_index is not None:
            self.owner.update_existing_mark(
                self.edit_mark_index,
                self.edit_action,
                self.drag_start,
                event.pos(),
                self.edit_original_rect,
                refresh_icon=False,
            )
            event.accept()
            return
        if self.pan_active and self.pan_last_global_pos is not None:
            delta = event.globalPos() - self.pan_last_global_pos
            self.owner.pan_preview_by(delta)
            self.pan_last_pos = event.pos()
            self.pan_last_global_pos = event.globalPos()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_start is not None and self.owner.annotation_mode:
            rect = QRectF(self.drag_start, event.pos()).normalized()
            self.drag_start = None
            self.drag_rect = None
            self.owner.finish_preview_annotation(rect)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.edit_mark_index is not None:
            if self.edit_snapshot is not None and event.pos() != self.drag_start:
                self.owner.push_undo_snapshot(self.edit_snapshot)
            self.owner.update_existing_mark(
                self.edit_mark_index,
                self.edit_action,
                self.drag_start,
                event.pos(),
                self.edit_original_rect,
                refresh_icon=True,
            )
            self.drag_start = None
            self.edit_mark_index = None
            self.edit_action = None
            self.edit_original_rect = None
            self.edit_snapshot = None
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.pan_active:
            self.pan_active = False
            self.pan_last_pos = None
            self.pan_last_global_pos = None
            self.owner.update_preview_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Undo):
            self.owner.undo()
            event.accept()
            return
        if event.matches(QKeySequence.Redo):
            self.owner.redo()
            event.accept()
            return
        if event.matches(QKeySequence.Save):
            self.owner.save_current()
            event.accept()
            return
        if event.matches(QKeySequence.Print):
            self.owner.print_pdf()
            event.accept()
            return
        if event.key() == Qt.Key_Delete and self.owner.selected_mark_index is not None:
            self.owner.delete_selected_mark()
            event.accept()
            return
        super().keyPressEvent(event)


class SignaturePad(QWidget):
    """Mouse-drawn signature canvas. Exports a trimmed transparent PNG."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(460, 180)
        self.setCursor(Qt.CrossCursor)
        self.strokes = []
        self.background_image = QImage()
        self.pen_color = QColor("#0b1f4d")
        self.pen_width = 3.2

    def clear(self):
        self.strokes = []
        self.background_image = QImage()
        self.update()

    def load_image(self, path):
        image = QImage(str(path))
        if image.isNull():
            return False
        self.strokes = []
        self.background_image = image.convertToFormat(QImage.Format_ARGB32)
        self.update()
        return True

    def is_empty(self):
        return not self.strokes and self.background_image.isNull()

    def image_target_rect(self):
        if self.background_image.isNull():
            return QRectF()
        area = QRectF(self.rect()).adjusted(16, 12, -16, -12)
        size = self.background_image.size()
        scale = min(area.width() / max(1, size.width()), area.height() / max(1, size.height()))
        width = size.width() * scale
        height = size.height() * scale
        return QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.strokes.append([QPointF(event.pos())])
            self.update()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.strokes:
            self.strokes[-1].append(QPointF(event.pos()))
            self.update()

    def paint_content(self, painter):
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.background_image.isNull():
            painter.drawImage(self.image_target_rect(), self.background_image)
        painter.setPen(QPen(self.pen_color, self.pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        for stroke in self.strokes:
            if len(stroke) == 1:
                painter.drawPoint(stroke[0])
                continue
            path = QPainterPath(stroke[0])
            for point in stroke[1:]:
                path.lineTo(point)
            painter.drawPath(path)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#cbd5e1"), 1, Qt.DashLine))
        baseline_y = self.height() - 36
        painter.drawLine(QPointF(24, baseline_y), QPointF(self.width() - 24, baseline_y))
        if self.is_empty():
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(self.rect(), Qt.AlignCenter, "여기에 마우스로 서명하세요")
        self.paint_content(painter)
        painter.end()

    def to_image(self):
        image = QImage(self.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        self.paint_content(painter)
        painter.end()
        return self.trim_image(image)

    @staticmethod
    def trim_image(image):
        left, top, right, bottom = image.width(), image.height(), -1, -1
        for y in range(image.height()):
            for x in range(image.width()):
                if QColor.fromRgba(image.pixel(x, y)).alpha() > 8:
                    left = min(left, x)
                    right = max(right, x)
                    top = min(top, y)
                    bottom = max(bottom, y)
        if right < left or bottom < top:
            return image
        margin = 6
        left = max(0, left - margin)
        top = max(0, top - margin)
        right = min(image.width() - 1, right + margin)
        bottom = min(image.height() - 1, bottom + margin)
        return image.copy(left, top, right - left + 1, bottom - top + 1)


class SignatureDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("결재 서명 등록")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QLabel("본인 서명을 마우스로 그리거나 서명 이미지를 불러온 뒤 저장하세요.")
        hint.setObjectName("panelHint")
        layout.addWidget(hint)

        self.pad = SignaturePad(self)
        self.pad.setObjectName("signaturePad")
        existing = owner.signature_settings.get("image")
        if existing:
            self.pad.load_image(existing)
        layout.addWidget(self.pad)

        tools = QHBoxLayout()
        self.clear_button = QPushButton("지우기")
        self.load_button = QPushButton("이미지 불러오기…")
        self.clear_button.clicked.connect(self.pad.clear)
        self.load_button.clicked.connect(self.load_image)
        tools.addWidget(self.clear_button)
        tools.addWidget(self.load_button)
        tools.addStretch(1)
        layout.addLayout(tools)

        form = QFormLayout()
        self.line_edit = QLineEdit(", ".join(owner.approval_line))
        self.line_edit.setPlaceholderText("예: 담당, 검토, 승인")
        self.cell_combo = QComboBox()
        self.line_edit.textChanged.connect(self.refresh_cells)
        form.addRow("결재선", self.line_edit)
        form.addRow("내 서명 칸", self.cell_combo)
        layout.addLayout(form)
        self.refresh_cells()
        self.cell_combo.setCurrentIndex(min(owner.signature_settings.get("cell", 0), self.cell_combo.count() - 1))

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("저장", QDialogButtonBox.AcceptRole)
        buttons.addButton("취소", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def refresh_cells(self, *args):
        current = self.cell_combo.currentIndex()
        labels = normalize_approval_line(self.line_edit.text())
        self.cell_combo.blockSignals(True)
        self.cell_combo.clear()
        self.cell_combo.addItems(list(labels))
        self.cell_combo.setCurrentIndex(max(0, min(current, len(labels) - 1)))
        self.cell_combo.blockSignals(False)

    def load_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "서명 이미지 불러오기", "", "이미지 (*.png *.jpg *.jpeg *.bmp)")
        if path and not self.pad.load_image(path):
            QMessageBox.warning(self, "서명 이미지", "이미지를 읽을 수 없습니다.")

    def accept(self):
        if self.pad.is_empty():
            QMessageBox.warning(self, "결재 서명 등록", "서명을 그리거나 이미지를 불러와 주세요.")
            return
        super().accept()

    def result_values(self):
        return self.pad.to_image(), self.line_edit.text(), self.cell_combo.currentIndex()


class StampSettingsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.insert_after_save = False
        self.setWindowTitle("도장 설정")
        self.setModal(True)
        self.setMinimumWidth(560)
        settings = owner.stamp_settings
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.preview = QLabel()
        self.preview.setObjectName("pageCanvas")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(360, 170)
        layout.addWidget(self.preview, 0, Qt.AlignHCenter)

        form = QFormLayout()
        image_row = QHBoxLayout()
        self.image_combo = QComboBox()
        self.image_combo.setIconSize(QSize(28, 28))
        self.image_combo.setMinimumWidth(170)
        self.image_combo.currentIndexChanged.connect(self.update_preview)
        self.add_button = QPushButton("불러오기…")
        self.remove_button = QPushButton("목록에서 삭제")
        self.add_button.clicked.connect(self.add_image)
        self.remove_button.clicked.connect(self.remove_image)
        image_row.addWidget(self.image_combo, 1)
        image_row.addWidget(self.add_button)
        image_row.addWidget(self.remove_button)
        form.addRow("도장 이미지", image_row)

        self.transparent_check = QCheckBox("불러올 때 흰 배경을 투명하게")
        self.transparent_check.setChecked(True)
        form.addRow("", self.transparent_check)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(5, 60)
        self.size_spin.setSuffix(" % (페이지 너비 기준)")
        self.size_spin.setValue(settings["width_percent"])
        form.addRow("크기", self.size_spin)

        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(20, 100)
        self.opacity_spin.setSuffix(" %")
        self.opacity_spin.setValue(settings["opacity_percent"])
        self.opacity_spin.valueChanged.connect(self.update_preview)
        form.addRow("불투명도", self.opacity_spin)

        self.position_combo = QComboBox()
        for key, label in STAMP_POSITIONS:
            self.position_combo.addItem(label, key)
        self.position_combo.setCurrentIndex(max(0, self.position_combo.findData(settings["position"])))
        form.addRow("기본 위치", self.position_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        self.save_insert_button = buttons.addButton("저장 후 삽입", QDialogButtonBox.ActionRole)
        self.save_button = buttons.addButton("저장", QDialogButtonBox.AcceptRole)
        buttons.addButton("취소", QDialogButtonBox.RejectRole)
        self.save_insert_button.clicked.connect(self.save_and_insert)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload_images(settings["image"])

    def reload_images(self, selected_path=""):
        self.image_combo.blockSignals(True)
        self.image_combo.clear()
        for index, path in enumerate(self.owner.stamp_image_paths, start=1):
            self.image_combo.addItem(QIcon(path), f"도장 {index}", path)
        found = self.image_combo.findData(selected_path) if selected_path else -1
        if found < 0 and self.image_combo.count():
            found = self.image_combo.count() - 1
        self.image_combo.setCurrentIndex(found)
        self.image_combo.blockSignals(False)
        self.remove_button.setEnabled(self.image_combo.count() > 0)
        self.save_insert_button.setEnabled(self.image_combo.count() > 0)
        self.update_preview()

    def selected_image(self):
        return self.image_combo.currentData() or ""

    def update_preview(self, *args):
        path = self.selected_image()
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("등록된 도장이 없습니다.\n[불러오기…]로 PNG/JPG 도장을 추가하세요.")
            return
        canvas = QPixmap(self.preview.size())
        canvas.fill(QColor("#ffffff"))
        scaled = pixmap.scaled(self.preview.width() - 40, self.preview.height() - 30, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(canvas)
        painter.setOpacity(self.opacity_spin.value() / 100)
        painter.drawPixmap((canvas.width() - scaled.width()) // 2, (canvas.height() - scaled.height()) // 2, scaled)
        painter.end()
        self.preview.setPixmap(canvas)

    def add_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "도장 이미지 불러오기", "", "이미지 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        try:
            stored = self.owner.store_png_stamp(path, remove_background=self.transparent_check.isChecked())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "도장 이미지", str(exc))
            return
        self.reload_images(stored)

    def remove_image(self):
        path = self.selected_image()
        if not path:
            return
        self.owner.stamp_image_paths = [item for item in self.owner.stamp_image_paths if item != path]
        self.owner.save_stamp_preferences()
        self.reload_images()

    def values(self):
        return {
            "image": self.selected_image(),
            "width_percent": self.size_spin.value(),
            "opacity_percent": self.opacity_spin.value(),
            "position": self.position_combo.currentData(),
        }

    def save_and_insert(self):
        self.insert_after_save = True
        self.accept()



class PdfPreviewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_pdf_path = None
        self.page_count = 0
        self.current_page_index = 0
        self.zoom = 1.35
        self.zoom_factor = 1.0
        self.preview_refresh_pending = False
        self.thumbnail_refresh_pending = False
        self.thumbnail_scale = 0.16
        self.thumbnail_cache = {}
        self.placeholder_icon_cache = {}
        self.page_size_cache = {}
        self.page_render_cache = {}
        self.preview_reference_width = None
        self.preview_reference_height = None
        self.preview_content_rect = QRectF()
        self.preview_content_size = QSize()
        self.last_thumbnail_layout = None
        self.pdf_passwords = {}
        self.approval_line, self.stamp_image_paths = load_user_preferences()
        self.stamp_settings = load_stamp_settings()
        self.signature_settings = load_signature_settings()
        self.last_stamp_dialog_inserted = False
        self.stamp_settings_dialog = None
        self.signature_dialog = None
        self.stamp_menu = None
        self.approval_menu = None
        self.toolbar_context_buttons = []
        self.default_app_watch = None
        self.default_app_guide = None
        self.open_with_attempt = None
        self.default_app_done_shown = False
        app_instance = QApplication.instance()
        if app_instance is not None:
            app_instance.applicationStateChanged.connect(self.on_application_state_changed)
        self.cut_buffer = []
        self.copy_buffer = []
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.selected_mark_index = None
        self.undo_stack = []
        self.redo_stack = []
        self.preview_refresh_timer = QTimer(self)
        self.preview_refresh_timer.setSingleShot(True)
        self.preview_refresh_timer.timeout.connect(self._flush_responsive_preview_refresh)
        self.thumbnail_refresh_timer = QTimer(self)
        self.thumbnail_refresh_timer.setSingleShot(True)
        self.thumbnail_refresh_timer.timeout.connect(self._flush_thumbnail_refresh)

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(load_app_icon())
        self.resize(1280, 860)
        self.setMinimumSize(1040, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(self.app_stylesheet())

        self._build_menu()

        self.page_list = PageThumbnailList(self)
        self.page_list.setObjectName("pageList")
        self.page_list.setMinimumWidth(128)
        self.page_list.setViewMode(QListWidget.IconMode)
        self.page_list.setResizeMode(QListWidget.Adjust)
        self.page_list.setMovement(QListWidget.Snap)
        self.page_list.setSpacing(6)
        self.page_list.currentRowChanged.connect(self.show_page)
        self.page_list.verticalScrollBar().valueChanged.connect(self.schedule_thumbnail_refresh)
        self.thumbnail_item_width = 82
        self.thumbnail_item_height = 116
        self.thumbnail_canvas_width = 90
        self.thumbnail_canvas_height = 122
        self.update_thumbnail_layout()

        self.page_label = PdfCanvasLabel(
            self, "PDF \ud30c\uc77c\uc744 \uc5f4\uac70\ub098 \uc774 \ud654\uba74\uc73c\ub85c \ub4dc\ub798\uadf8\ud574\uc11c \ucd94\uac00\ud558\uc138\uc694."
        )
        self.page_label.setObjectName("pageCanvas")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumSize(640, 480)
        self.page_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.page_label.customContextMenuRequested.connect(self.show_preview_context_menu)

        self.scroll_area = PdfScrollArea(self)
        self.scroll_area.setObjectName("documentArea")
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll_area.setWidget(self.page_label)

        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(0)
        self.page_spin.setMaximum(0)
        self.page_spin.setPrefix("\ud398\uc774\uc9c0 ")
        self.page_spin.setObjectName("pageSpin")
        self.page_spin.valueChanged.connect(self.go_to_page)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("zoomLabel")

        self._build_toolbar()
        self._build_layout()
        self.setStatusBar(QStatusBar())
        self.schedule_thumbnail_refresh()
        self.set_status("\uc900\ube44\ub428")

    def app_stylesheet(self):
        return """
            QMainWindow {
                background: #f3f4f6;
                font-family: "Malgun Gothic", "Segoe UI", Arial, sans-serif;
                font-size: 12px;
                color: #172033;
            }
            QMenuBar {
                background: #ffffff;
                border-bottom: 1px solid #d9dee7;
                padding: 3px 8px;
            }
            QMenuBar::item {
                padding: 5px 10px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background: #e8f0ff;
                color: #155eef;
            }
            QMenu {
                background: #ffffff;
                border: 1px solid #d9dee7;
                padding: 4px;
            }
            QMenu::item {
                padding: 7px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #eaf2ff;
                color: #155eef;
            }
            QToolBar {
                background: #ffffff;
                border: 0;
                border-bottom: 1px solid #d9dee7;
                spacing: 2px;
                padding: 4px 6px 2px 6px;
            }
            QToolButton {
                color: #1f2937;
                min-width: 48px;
                padding: 4px 5px;
                border: 1px solid transparent;
                border-radius: 5px;
            }
            #toolbarGroupLabel {
                color: #8f99a8;
                font-size: 10px;
                padding: 0px 0px 0px 0px;
            }
            #toolbarGroupDivider {
                color: #e3e7ee;
                background: #e3e7ee;
                margin: 4px 2px 4px 2px;
            }
            #toolbarGroup QToolButton {
                min-width: 36px;
                padding: 2px 3px;
            }
            QSpinBox#pageSpin {
                min-width: 60px;
                padding: 4px 5px;
            }
            QToolButton:hover {
                background: #f1f5f9;
                border-color: #cbd5e1;
            }
            QToolButton:pressed {
                background: #e2e8f0;
            }
            #leftPanel {
                background: #ffffff;
                border-right: 1px solid #d9dee7;
            }
            #panelTitle {
                color: #0f172a;
                font-size: 14px;
                font-weight: 800;
                padding: 10px 4px 2px 4px;
            }
            #panelHint {
                color: #64748b;
                font-size: 11px;
                padding: 0 4px 8px 4px;
            }
            #pageList {
                background: #ffffff;
                border: 0;
                outline: 0;
            }
            #pageList::item {
                color: #334155;
                padding: 6px 3px;
                border: 1px solid transparent;
                border-radius: 8px;
            }
            #pageList::item:selected {
                background: #dbeafe;
                border: 1px solid #60a5fa;
                color: #0f172a;
            }
            #pageList::item:hover {
                background: #e0f2fe;
            }
            #documentArea {
                background: #e5e7eb;
                border: 0;
            }
            QSplitter::handle {
                background: #d1d5db;
            }
            QSplitter::handle:horizontal {
                width: 8px;
            }
            QSplitter::handle:hover {
                background: #94a3b8;
            }
            #pageCanvas {
                background: #ffffff;
                color: #64748b;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                padding: 6px 9px;
                color: #172033;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #eff6ff;
                border-color: #60a5fa;
            }
            QSpinBox {
                min-width: 94px;
                padding: 5px 7px;
                border: 1px solid #334155;
                border-radius: 5px;
                background: #ffffff;
                color: #0f172a;
                font-weight: 700;
            }
            #zoomLabel {
                min-width: 48px;
                color: #334155;
                font-weight: 700;
                padding-left: 4px;
            }
            QStatusBar {
                background: #ffffff;
                border-top: 1px solid #d9e2ef;
                color: #475569;
                font-weight: 600;
            }
        """

    def _build_menu(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("\ud30c\uc77c")
        view_menu = menu_bar.addMenu("\ubcf4\uae30")
        edit_menu = menu_bar.addMenu("\ud3b8\uc9d1")
        tool_menu = menu_bar.addMenu("\ub3c4\uad6c")

        file_menu.addAction(self.make_action("\uc5f4\uae30", self.open_pdf, "open"))
        file_menu.addAction(self.make_action("\uc800\uc7a5", self.save_current, "save", shortcut=QKeySequence.Save))
        file_menu.addAction(self.make_action("\ub2e4\ub978 \uc774\ub984\uc73c\ub85c \uc800\uc7a5", self.save_copy_as, "save"))
        file_menu.addAction(self.make_action("\uc778\uc1c4", self.print_pdf, "print", shortcut=QKeySequence.Print))
        file_menu.addSeparator()
        file_menu.addAction(self.make_action("\uc885\ub8cc", self.close))

        view_menu.addAction(self.make_action("\ud655\ub300", self.zoom_in, "zoom_in"))
        view_menu.addAction(self.make_action("\ucd95\uc18c", self.zoom_out, "zoom_out"))
        view_menu.addAction(self.make_action("\uc774\uc804 \ud398\uc774\uc9c0", self.previous_page, "prev"))
        view_menu.addAction(self.make_action("\ub2e4\uc74c \ud398\uc774\uc9c0", self.next_page, "next"))

        mark_menu = menu_bar.addMenu("\uc8fc\uc11d")
        mark_menu.addAction(self.make_action("\ube68\uac04 \uc0ac\uac01\ud615", self.enable_rectangle_mode, "rect_mark"))
        mark_menu.addAction(self.make_action("\ud615\uad11\ud39c", self.enable_highlight_mode, "highlight"))
        mark_menu.addSeparator()
        mark_menu.addAction(self.make_action("도장 찍기", self.insert_default_stamp, "stamp"))
        mark_menu.addAction(self.make_action("도장 설정…", self.open_stamp_settings, "settings"))
        mark_menu.addAction(self.make_action("PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc\u00b7\ubcf4\uad00", self.import_stamp_png, "image_plus"))
        mark_menu.addAction(self.make_action("\uc800\uc7a5\ub41c PNG \ub3c4\uc7a5 \uc0bd\uc785", self.insert_saved_png_stamp, "stamp"))
        mark_menu.addSeparator()
        mark_menu.addAction(self.make_action("결재 (내 서명 포함)", self.insert_signed_approval, "signature"))
        mark_menu.addAction(self.make_action("\uacb0\uc7ac \ub3c4\uc7a5\uce78 (서명 없이)", self.add_approval_stamp_to_first_page, "approval_stamp"))
        mark_menu.addAction(self.make_action("서명 등록…", self.open_signature_settings, "pen_line"))
        mark_menu.addAction(self.make_action("\uacb0\uc7ac\uc120 \uc124\uc815", self.configure_approval_line, "settings"))
        mark_menu.addSeparator()
        mark_menu.addAction(self.make_action("\uc8fc\uc11d \ubaa8\ub4dc \ub044\uae30", self.disable_annotation_mode))

        edit_menu.addAction(self.make_action("\uc798\ub77c\ub0b4\uae30", self.cut_selected_pages, "cut", shortcut=QKeySequence.Cut))
        edit_menu.addAction(self.make_action("\ubcf5\uc0ac\ud558\uae30", self.copy_selected_pages, "copy", shortcut=QKeySequence.Copy))
        edit_menu.addAction(self.make_action("\ubd99\uc5ec\ub123\uae30", self.paste_pages_after_current, "paste", shortcut=QKeySequence.Paste))
        edit_menu.addAction(self.make_action("\uc9c0\uc6b0\uae30", self.delete_selected_pages, "delete", shortcut=QKeySequence.Delete))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("\ub418\ub3cc\ub9ac\uae30", self.undo, shortcut=QKeySequence.Undo))
        edit_menu.addAction(self.make_action("\ub2e4\uc2dc \uc2e4\ud589", self.redo, shortcut=QKeySequence.Redo))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("\uc624\ub978\ucabd \ud68c\uc804", self.rotate_selected_pages_right, "rotate_right"))
        edit_menu.addAction(self.make_action("\uc67c\ucabd \ud68c\uc804", self.rotate_selected_pages_left, "rotate_left"))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("PDF \ubcd1\ud569", self.merge_pdfs, "merge"))
        edit_menu.addAction(self.make_action("\ud398\uc774\uc9c0 \ucd94\ucd9c", self.extract_pages, "extract"))
        edit_menu.addAction(self.make_action("PDF \uc0bd\uc785", self.insert_pdf, "insert"))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("PDF \uc554\ud638 \uc124\uc815", self.save_pdf_with_password, "lock"))
        edit_menu.addAction(self.make_action("PDF \uc554\ud638 \ud574\uc81c", self.save_pdf_without_password, "unlock"))

        tool_menu.addAction(self.make_action("\ud604\uc7ac \ud30c\uc77c \uc815\ubcf4", self.show_file_info, "info"))
        tool_menu.addSeparator()
        self.default_app_action = self.make_action("PDF \uae30\ubcf8 \uc571\uc73c\ub85c \uc124\uc815\u2026", self.set_pdf_default_app)  # no icon: the check mark shows
        self.default_app_action.setCheckable(True)
        tool_menu.addAction(self.default_app_action)
        self.unregister_assoc_action = self.make_action("PDF \uc5f0\uacb0 \ub4f1\ub85d \ud574\uc81c", self.unregister_openwith_from_menu)
        tool_menu.addAction(self.unregister_assoc_action)
        tool_menu.aboutToShow.connect(self.refresh_default_app_menu)
        self.tool_menu = tool_menu

    def _build_toolbar(self):
        toolbar = QToolBar("PDF \ub3c4\uad6c")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        toolbar.setIconSize(QSize(28, 28))
        self.addToolBar(toolbar)

        groups = [
            ("\ud30c\uc77c", [
                ("\uc5f4\uae30", self.open_pdf, "open"),
                ("\uc800\uc7a5", self.save_current, "save"),
                ("\uc778\uc1c4", self.print_pdf, "print"),
            ]),
            ("\ud398\uc774\uc9c0", [
                ("\ubcd1\ud569", self.merge_pdfs, "merge"),
                ("\ucd94\ucd9c", self.extract_pages, "extract"),
                ("\uc0bd\uc785", self.insert_pdf, "insert"),
            ]),
            ("\ubcf4\uc548", [
                ("\uc554\ud638", self.save_pdf_with_password, "lock"),
                ("\ud574\uc81c", self.save_pdf_without_password, "unlock"),
            ]),
            ("\uc8fc\uc11d", [
                ("\uc0ac\uac01\ud615", self.enable_rectangle_mode, "rect_mark"),
                ("\ud615\uad11\ud39c", self.enable_highlight_mode, "highlight"),
            ]),
            ("\uc11c\uba85", [
                ("\ub3c4\uc7a5", self.insert_default_stamp, "stamp"),
                ("\uacb0\uc7ac", self.insert_signed_approval, "signature"),
            ]),
            ("\ubcf4\uae30", [
                ("\uc6b0\ud68c\uc804", self.rotate_selected_pages_right, "rotate_right"),
                ("\uc88c\ud68c\uc804", self.rotate_selected_pages_left, "rotate_left"),
                ("\uc774\uc804", self.previous_page, "prev"),
                ("\ub2e4\uc74c", self.next_page, "next"),
                ("\ucd95\uc18c", self.zoom_out, "zoom_out"),
                ("\ud655\ub300", self.zoom_in, "zoom_in"),
            ]),
        ]

        self.toolbar_groups = []
        self.toolbar_buttons = {}
        for group_index, (group_name, items) in enumerate(groups):
            if group_index:
                divider = QFrame()
                divider.setObjectName("toolbarGroupDivider")
                divider.setFrameShape(QFrame.VLine)
                divider.setFixedWidth(1)
                toolbar.addWidget(divider)
            group = QWidget()
            group.setObjectName("toolbarGroup")
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(1, 0, 1, 0)
            group_layout.setSpacing(0)
            # Never squeeze (that elides the labels); if the window is really narrow the toolbar's
            # overflow (>>) menu takes whole groups instead.
            group_layout.setSizeConstraint(QLayout.SetFixedSize)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            group_actions = []
            for label, handler, icon_key in items:
                action = self.make_action(label, handler, icon_key)
                button = QToolButton(group)
                button.setDefaultAction(action)
                button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                button.setIconSize(QSize(28, 28))
                button.setAutoRaise(True)
                row.addWidget(button)
                group_actions.append(action)
                self.toolbar_buttons[action] = button
                if handler == self.insert_default_stamp:
                    self.stamp_toolbar_action = action
                    self.attach_toolbar_context_menu(toolbar, action, self.show_stamp_button_menu, "\ub3c4\uc7a5 \ucc0d\uae30 (\uc6b0\ud074\ub9ad: \ub3c4\uc7a5 \uc124\uc815\u00b7\uc800\uc7a5)")
                elif handler == self.insert_signed_approval:
                    self.approval_toolbar_action = action
                    self.attach_toolbar_context_menu(toolbar, action, self.show_approval_button_menu, "\uacb0\uc7ac + \ub0b4 \uc11c\uba85 (\uc6b0\ud074\ub9ad: \uc11c\uba85 \ub4f1\ub85d\u00b7\uc800\uc7a5)")
            group_layout.addLayout(row)
            caption = QLabel(group_name)
            caption.setObjectName("toolbarGroupLabel")
            caption.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            group_layout.addWidget(caption)
            toolbar.addWidget(group)
            self.toolbar_groups.append((group_name, group_actions, caption))

        toolbar.addSeparator()
        toolbar.addWidget(self.page_spin)
        toolbar.addSeparator()
        toolbar.addWidget(self.zoom_label)

    def attach_toolbar_context_menu(self, toolbar, action, handler, tooltip):
        action.setToolTip(tooltip)
        button = self.toolbar_buttons.get(action) or toolbar.widgetForAction(action)
        if button is None:
            return
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda position, b=button: handler(b.mapToGlobal(position)))
        # Keep the Python wrapper reachable; otherwise the GC can collect the button/lambda cycle
        # and silently drop the right-click connection.
        self.toolbar_context_buttons.append(button)

    def build_stamp_menu(self):
        menu = QMenu(self)
        menu.addAction(make_tool_icon("stamp"), "도장 찍기 (저장된 설정)", self.insert_default_stamp)
        menu.addAction(make_tool_icon("settings"), "도장 설정·저장…", self.open_stamp_settings)
        menu.addAction(make_tool_icon("image_plus"), "도장 이미지 추가…", self.import_stamp_png)
        menu.addAction(make_tool_icon("stamp"), "저장된 도장 골라 찍기…", self.insert_saved_png_stamp)
        return menu

    def build_approval_menu(self):
        menu = QMenu(self)
        menu.addAction(make_tool_icon("signature"), "결재 + 내 서명 넣기", self.insert_signed_approval)
        menu.addAction(make_tool_icon("pen_line"), "서명 등록·저장…", self.open_signature_settings)
        menu.addAction(make_tool_icon("settings"), "결재선 설정…", self.configure_approval_line)
        menu.addSeparator()
        menu.addAction(make_tool_icon("approval_stamp"), "결재칸만 넣기 (서명 없이)", self.add_approval_stamp_to_first_page)
        return menu

    def show_stamp_button_menu(self, global_pos):
        self.stamp_menu = self.build_stamp_menu()
        self.stamp_menu.exec_(global_pos)

    def show_approval_button_menu(self, global_pos):
        self.approval_menu = self.build_approval_menu()
        self.approval_menu.exec_(global_pos)

    def make_action(self, label, handler, icon_key=None, shortcut=None):
        if icon_key is None:
            action = QAction(label, self)
        elif isinstance(icon_key, str):
            action = QAction(make_tool_icon(icon_key), label, self)
        else:
            action = QAction(self.style().standardIcon(icon_key), label, self)
        action.triggered.connect(handler)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.setToolTip(label)
        return action

    def _build_layout(self):
        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 6, 10, 10)
        left_layout.setSpacing(5)

        panel_title = QLabel("\ud398\uc774\uc9c0")
        panel_title.setObjectName("panelTitle")
        panel_hint = QLabel("\ub4dc\ub798\uadf8\ub85c \uc21c\uc11c \ubcc0\uacbd")
        panel_hint.setObjectName("panelHint")
        left_layout.addWidget(panel_title)
        left_layout.addWidget(panel_hint)
        left_layout.addWidget(self.page_list)

        prev_button = QPushButton("\uc774\uc804")
        next_button = QPushButton("\ub2e4\uc74c")
        prev_button.setIcon(make_tool_icon("prev"))
        next_button.setIcon(make_tool_icon("next"))
        prev_button.setIconSize(QSize(22, 22))
        next_button.setIconSize(QSize(22, 22))
        prev_button.clicked.connect(self.previous_page)
        next_button.clicked.connect(self.next_page)

        nav_layout = QHBoxLayout()
        nav_layout.addWidget(prev_button)
        nav_layout.addWidget(next_button)
        left_layout.addLayout(nav_layout)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setOpaqueResize(False)
        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(self.scroll_area)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([150, 1130])
        self.main_splitter.splitterMoved.connect(self.schedule_responsive_preview_refresh)
        self.main_splitter.splitterMoved.connect(self.schedule_thumbnail_refresh)

        layout.addWidget(self.main_splitter)
        self.setCentralWidget(central)

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "PDF \uc5f4\uae30", "", PDF_FILTER)
        if path:
            self.load_pdf(path)

    def load_pdf(self, path):
        page_refs = self.page_refs_for_pdf_with_password(path)
        if page_refs is None:
            return

        self.current_pdf_path = path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selected_mark_index = None
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.thumbnail_cache.clear()
        self.page_render_cache.clear()
        self.page_size_cache.clear()
        self.preview_reference_width = None
        self.preview_reference_height = None
        self.page_list.blockSignals(True)
        self.page_list.clear()
        self.add_page_refs_to_list(page_refs)
        self.page_list.blockSignals(False)
        self.refresh_preview_reference_width()
        self.schedule_thumbnail_refresh()
        self.refresh_page_numbers()
        self.page_list.setCurrentRow(0)
        self.show_page(0)
        self.setWindowTitle(f"{APP_TITLE} - {Path(path).name}")
        self.set_status(f"\uc5f4\ub9bc: {path}")

    def add_pdf_pages_to_list(self, path):
        page_refs = self.page_refs_for_pdf_with_password(path)
        if page_refs is None:
            return False
        self.add_page_refs_to_list(page_refs)
        return True

    def add_page_refs_to_list(self, page_refs):
        for page_ref in page_refs:
            self.page_list.addItem(self.create_item_for_page_ref(page_ref))

    def create_item_for_page_ref(self, page_ref):
        item = QListWidgetItem(self.placeholder_page_icon(page_ref.rotation), "")
        item.setTextAlignment(Qt.AlignCenter)
        item.setData(PAGE_ROLE, page_ref)
        item.setToolTip(f"{Path(page_ref.path).name} / {page_ref.page_index + 1} \ud398\uc774\uc9c0")
        return item

    def placeholder_page_icon(self, rotation=0):
        cache_key = rotation % 360
        icon = self.placeholder_icon_cache.get(cache_key)
        if icon is None:
            icon = make_page_icon(cache_key)
            self.placeholder_icon_cache[cache_key] = icon
        return icon

    def _page_refs_for_pdf_with_password_legacy(self, path):
        path = str(Path(path))
        cached_password = self.pdf_passwords.get(path, "")
        try:
            refs = page_refs_for_pdf(path, cached_password)
            if cached_password:
                self.pdf_passwords[path] = cached_password
            return refs
        except Exception as first_error:
            self.pdf_passwords.pop(path, None)
            last_error = first_error
            prompt = (
                f"{Path(path).name}\n"
                "암호가 필요하거나 파일을 열 수 없습니다.\n"
                "PDF 암호를 입력하세요."
            )
            while True:
                password, ok = QInputDialog.getText(
                    self,
                    "암호 PDF",
                    prompt,
                    QLineEdit.Password,
                )
                if not ok:
                    return None

                try:
                    refs = page_refs_for_pdf(path, password)
                    self.pdf_passwords[path] = password
                    return refs
                except Exception as error:
                    last_error = error
                    QMessageBox.warning(
                        self,
                        "암호 확인",
                        f"{Path(path).name}\n\n암호가 맞지 않거나 파일을 열 수 없습니다.\n다시 입력해 주세요.",
                    )
                    prompt = (
                        f"{Path(path).name}\n"
                        "암호가 맞지 않습니다.\n"
                        "PDF 암호를 다시 입력하세요."
                    )

            self.show_error(
                "PDF 열기 오류",
                f"{Path(path).name}\n\n암호를 확인하거나 파일 상태를 확인해주세요.\n\n{last_error}",
            )
            return None

    def page_refs_for_pdf_with_password_old(self, path):
        path = str(Path(path))
        cached_password = self.pdf_passwords.get(path, "")
        try:
            refs = page_refs_for_pdf(path, cached_password)
            if cached_password:
                self.pdf_passwords[path] = cached_password
            return refs
        except Exception as first_error:
            password, ok = QInputDialog.getText(
                self,
                "\uc554\ud638 PDF",
                f"{Path(path).name}\n\uc554\ud638\uac00 \ud544\uc694\ud558\uac70\ub098 \ud30c\uc77c\uc744 \uc5f4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\nPDF \uc554\ud638\ub97c \uc785\ub825\ud558\uc138\uc694.",
                QLineEdit.Password,
            )
            if not ok:
                return None

            try:
                refs = page_refs_for_pdf(path, password)
                self.pdf_passwords[path] = password
                return refs
            except Exception as second_error:
                self.show_error(
                    "PDF \uc5f4\uae30 \uc624\ub958",
                    f"{Path(path).name}\n\n\uc554\ud638\ub97c \ud655\uc778\ud558\uac70\ub098 \ud30c\uc77c \uc0c1\ud0dc\ub97c \ud655\uc778\ud574\uc8fc\uc138\uc694.\n\n{second_error or first_error}",
                )
                return None

    def page_refs_for_pdf_with_password(self, path):
        path = str(Path(path))
        cached_password = self.pdf_passwords.get(path, "")
        try:
            refs = page_refs_for_pdf(path, cached_password)
            if cached_password:
                self.pdf_passwords[path] = cached_password
            return refs
        except Exception:
            self.pdf_passwords.pop(path, None)

        prompt = (
            f"{Path(path).name}\n"
            "\uc554\ud638\uac00 \ud544\uc694\ud558\uac70\ub098 \ud30c\uc77c\uc744 \uc5f4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\n"
            "PDF \uc554\ud638\ub97c \uc785\ub825\ud558\uc138\uc694."
        )
        while True:
            password, ok = QInputDialog.getText(self, "\uc554\ud638 PDF", prompt, QLineEdit.Password)
            if not ok:
                return None

            try:
                refs = page_refs_for_pdf(path, password)
                self.pdf_passwords[path] = password
                return refs
            except Exception:
                QMessageBox.warning(
                    self,
                    "\uc554\ud638 \ud655\uc778",
                    f"{Path(path).name}\n\n\uc554\ud638\uac00 \ub9de\uc9c0 \uc54a\uac70\ub098 \ud30c\uc77c\uc744 \uc5f4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\n\ub2e4\uc2dc \uc785\ub825\ud574 \uc8fc\uc138\uc694.",
                )
                prompt = (
                    f"{Path(path).name}\n"
                    "\uc554\ud638\uac00 \ub9de\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.\n"
                    "PDF \uc554\ud638\ub97c \ub2e4\uc2dc \uc785\ub825\ud558\uc138\uc694."
                )

    def password_for_page_ref(self, page_ref):
        return page_ref.password or self.pdf_passwords.get(page_ref.path, "")

    def append_pdf_files(self, paths):
        self.insert_pdf_files(paths)

    def insert_pdf_files(self, paths, insert_row=None):
        valid_paths = [path for path in paths if Path(path).is_file() and path.lower().endswith(".pdf")]
        if not valid_paths:
            return

        self.save_undo_state()
        was_empty = self.page_list.count() == 0
        previous_pdf_path = self.current_pdf_path
        if was_empty:
            self.current_pdf_path = valid_paths[0]

        if insert_row is None:
            insert_row = self.page_list.count()
        insert_row = max(0, min(insert_row, self.page_list.count()))
        new_items = []
        for path in valid_paths:
            new_items.extend(self.create_items_for_pdf(path) or [])

        if not new_items:
            self.undo_stack.pop()
            self.current_pdf_path = previous_pdf_path
            return

        for offset, item in enumerate(new_items):
            self.page_list.insertItem(insert_row + offset, item)

        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        if was_empty:
            self.page_list.setCurrentRow(0)
            self.show_page(0)
        else:
            self.page_list.setCurrentRow(insert_row)
            self.show_page(insert_row)
        self.set_status(f"PDF {len(valid_paths)}\uac1c \ucd94\uac00 \uc644\ub8cc")

    def ordered_page_refs(self):
        refs = []
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            page_ref = item.data(PAGE_ROLE)
            if page_ref:
                refs.append(page_ref)
        return refs

    def make_history_snapshot(self):
        return (tuple(self.ordered_page_refs()), self.current_page_index, self.selected_mark_index)

    def save_undo_state(self):
        self.undo_stack.append(self.make_history_snapshot())
        self.redo_stack.clear()

    def push_undo_snapshot(self, snapshot):
        self.undo_stack.append(snapshot)
        self.redo_stack.clear()

    def restore_history_snapshot(self, snapshot):
        page_refs, current_row, selected_mark_index = snapshot
        self.selected_mark_index = selected_mark_index
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.thumbnail_cache.clear()
        self.rebuild_page_list(list(page_refs), current_row=current_row)
        self.selected_mark_index = selected_mark_index
        if self.page_list.count():
            self.show_page(min(max(0, current_row), self.page_list.count() - 1))

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.make_history_snapshot())
        self.restore_history_snapshot(self.undo_stack.pop())
        self.set_status("\ub418\ub3cc\ub9ac\uae30 \uc644\ub8cc")

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.make_history_snapshot())
        self.restore_history_snapshot(self.redo_stack.pop())
        self.set_status("\ub2e4\uc2dc \uc2e4\ud589 \uc644\ub8cc")

    def rebuild_page_list(self, page_refs, selected_rows=None, current_row=None):
        selected_rows = selected_rows or []
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for page_ref in page_refs:
            self.page_list.addItem(self.create_item_for_page_ref(page_ref))
        self.page_list.blockSignals(False)

        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        self.page_list.clearSelection()

        for row in selected_rows:
            if 0 <= row < self.page_list.count():
                self.page_list.item(row).setSelected(True)

        if current_row is not None and 0 <= current_row < self.page_list.count():
            self.page_list.setCurrentRow(current_row)
            self.show_page(current_row)
        elif self.page_list.count():
            self.page_list.setCurrentRow(0)
            self.show_page(0)
        else:
            self.clear_preview()

    def move_pages_by_rows(self, selected_rows, target_row):
        all_refs = self.ordered_page_refs()
        selected_set = set(selected_rows)
        moving_refs = [all_refs[row] for row in selected_rows if 0 <= row < len(all_refs)]
        if not moving_refs:
            return

        self.save_undo_state()
        remaining_refs = [page_ref for index, page_ref in enumerate(all_refs) if index not in selected_set]
        adjusted_row = target_row - sum(1 for row in selected_rows if row < target_row)
        adjusted_row = max(0, min(adjusted_row, len(remaining_refs)))

        new_refs = remaining_refs[:adjusted_row] + moving_refs + remaining_refs[adjusted_row:]
        selected_after_move = list(range(adjusted_row, adjusted_row + len(moving_refs)))
        self.rebuild_page_list(new_refs, selected_after_move, adjusted_row)
        self.set_status("\ud398\uc774\uc9c0 \uc21c\uc11c\uac00 \ubcc0\uacbd\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

    def refresh_page_numbers(self):
        self.page_count = self.page_list.count()
        for row in range(self.page_count):
            self.page_list.item(row).setText(str(row + 1))

        self.page_spin.blockSignals(True)
        if self.page_count:
            self.page_spin.setMinimum(1)
            self.page_spin.setMaximum(self.page_count)
            self.page_spin.setValue(min(self.current_page_index + 1, self.page_count))
        else:
            self.page_spin.setMinimum(0)
            self.page_spin.setMaximum(0)
            self.page_spin.setValue(0)
        self.page_spin.blockSignals(False)

    def after_page_order_changed(self):
        self.refresh_page_numbers()
        row = self.page_list.currentRow()
        if row >= 0:
            self.show_page(row)
        self.set_status("\ud398\uc774\uc9c0 \uc21c\uc11c\uac00 \ubcc0\uacbd\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

    def selected_page_rows(self):
        selected_rows = sorted({index.row() for index in self.page_list.selectedIndexes()})
        if selected_rows:
            return selected_rows
        row = self.page_list.currentRow()
        return [row] if row >= 0 else []

    def page_refs_for_rows(self, rows):
        page_refs = []
        for row in rows:
            item = self.page_list.item(row)
            if item is None:
                continue
            page_ref = item.data(PAGE_ROLE)
            if page_ref:
                page_refs.append(page_ref)
        return page_refs

    def has_clipboard_pages(self):
        return bool(self.cut_buffer or self.copy_buffer)

    def cut_selected_pages(self):
        selected_rows = self.selected_page_rows()
        if not selected_rows:
            return

        self.save_undo_state()
        self.cut_buffer = self.page_refs_for_rows(selected_rows)
        self.copy_buffer = []

        for row in reversed(selected_rows):
            self.page_list.takeItem(row)

        next_row = min(selected_rows[0], self.page_list.count() - 1)
        self.refresh_page_numbers()

        if next_row >= 0:
            self.page_list.setCurrentRow(next_row)
            self.show_page(next_row)
        else:
            self.clear_preview()

        self.set_status(f"{len(self.cut_buffer)}\uac1c \ud398\uc774\uc9c0 \uc798\ub77c\ub0b4\uae30 \uc644\ub8cc")

    def copy_selected_pages(self):
        selected_rows = self.selected_page_rows()
        if not selected_rows:
            return

        self.copy_buffer = self.page_refs_for_rows(selected_rows)
        self.cut_buffer = []
        self.set_status(f"{len(self.copy_buffer)}\uac1c \ud398\uc774\uc9c0 \ubcf5\uc0ac \uc644\ub8cc")

    def paste_pages_after_current(self):
        page_refs = self.cut_buffer or self.copy_buffer
        if not page_refs:
            return

        self.save_undo_state()
        current_row = self.page_list.currentRow()
        insert_row = current_row + 1 if current_row >= 0 else self.page_list.count()

        for offset, page_ref in enumerate(page_refs):
            self.page_list.insertItem(insert_row + offset, self.create_item_for_page_ref(page_ref))

        pasted_count = len(page_refs)
        if self.cut_buffer:
            self.cut_buffer = []
        self.refresh_page_numbers()

        self.page_list.clearSelection()
        for row in range(insert_row, insert_row + pasted_count):
            self.page_list.item(row).setSelected(True)
        self.page_list.setCurrentRow(insert_row)
        self.show_page(insert_row)
        self.set_status(f"{pasted_count}\uac1c \ud398\uc774\uc9c0 \ubd99\uc5ec\ub123\uae30 \uc644\ub8cc")

    def delete_selected_pages(self):
        if self.selected_mark_index is not None:
            self.delete_selected_mark()
            return

        selected_rows = self.selected_page_rows()
        if not selected_rows:
            return

        self.save_undo_state()
        for row in reversed(selected_rows):
            self.page_list.takeItem(row)

        next_row = min(selected_rows[0], self.page_list.count() - 1)
        self.refresh_page_numbers()

        if next_row >= 0:
            self.page_list.setCurrentRow(next_row)
            self.show_page(next_row)
        else:
            self.clear_preview()

        self.set_status(f"{len(selected_rows)}\uac1c \ud398\uc774\uc9c0 \uc0ad\uc81c \uc644\ub8cc")

    def clear_preview(self):
        self.current_page_index = 0
        self.selected_mark_index = None
        self.preview_content_rect = QRectF()
        self.preview_content_size = QSize()
        self.page_label.clear()
        self.page_label.setText("PDF \ud30c\uc77c\uc744 \uc5f4\uac70\ub098 \uc774 \ud654\uba74\uc73c\ub85c \ub4dc\ub798\uadf8\ud574\uc11c \ucd94\uac00\ud558\uc138\uc694.")
        self.page_label.setFixedSize(640, 480)
        self.update_preview_cursor()

    def show_page(self, index):
        if index < 0 or index >= self.page_count:
            return

        item = self.page_list.item(index)
        page_ref = item.data(PAGE_ROLE)
        if not page_ref:
            return

        if index != self.current_page_index:
            self.selected_mark_index = None
        self.current_page_index = index
        self.update_zoom_for_page(page_ref)
        pixmap = self.render_page_pixmap(page_ref, self.zoom, self.selected_mark_index)
        if self.preview_drag_rect is not None:
            self.draw_page_marks(pixmap, (), self.preview_drag_rect)
        preview_canvas = self.create_preview_canvas(pixmap)
        self.page_label.setPixmap(preview_canvas)
        self.page_label.setFixedSize(preview_canvas.size())
        self.update_preview_cursor()

        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)

        self.page_list.blockSignals(True)
        self.page_list.setCurrentRow(index)
        self.page_list.blockSignals(False)
        self.schedule_thumbnail_refresh()

        self.set_status(f"{index + 1} / {self.page_count} \ud398\uc774\uc9c0")

    def refresh_responsive_preview(self, *args):
        if self.page_count <= 0:
            return
        self.show_page(self.current_page_index)

    def refresh_preview_reference_width(self):
        page_refs = self.ordered_page_refs()
        if not page_refs:
            self.preview_reference_width = None
            self.preview_reference_height = None
            return
        page_sizes = [self.page_display_size(page_ref) for page_ref in page_refs]
        self.preview_reference_width = max(width for width, _ in page_sizes)
        self.preview_reference_height = max(height for _, height in page_sizes)

    def create_preview_canvas(self, page_pixmap):
        canvas_width = max(page_pixmap.width(), int(round((self.preview_reference_width or 0) * self.zoom)))
        canvas_height = max(page_pixmap.height(), int(round((self.preview_reference_height or 0) * self.zoom)))
        canvas = QPixmap(canvas_width, canvas_height)
        canvas.fill(QColor("#e8eef7"))
        x = (canvas_width - page_pixmap.width()) // 2
        y = (canvas_height - page_pixmap.height()) // 2
        painter = QPainter(canvas)
        painter.drawPixmap(x, y, page_pixmap)
        painter.end()
        self.preview_content_rect = QRectF(x, y, page_pixmap.width(), page_pixmap.height())
        self.preview_content_size = page_pixmap.size()
        return canvas

    def can_pan_preview(self):
        if self.annotation_mode or self.page_label.pixmap() is None:
            return False
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        return horizontal.maximum() > 0 or vertical.maximum() > 0

    def pan_preview_by(self, delta):
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        horizontal.setValue(horizontal.value() - delta.x())
        vertical.setValue(vertical.value() - delta.y())

    def update_preview_cursor(self):
        if getattr(self.page_label, "pan_active", False):
            self.page_label.setCursor(Qt.ClosedHandCursor)
        elif self.can_pan_preview():
            self.page_label.setCursor(Qt.OpenHandCursor)
        else:
            self.page_label.unsetCursor()

    THUMBNAIL_ITEM_CHROME = 14  # #pageList::item padding (3px x2) + border (1px x2) + Qt item margins
    THUMBNAIL_SIDE_MARGIN = 4

    def update_thumbnail_layout(self):
        # One centered column that always fits the real viewport width (scrollbar/frame excluded).
        viewport_width = max(60, self.page_list.viewport().width())
        grid_width = viewport_width
        canvas_width = max(48, min(228, grid_width - self.THUMBNAIL_ITEM_CHROME - 2 * self.THUMBNAIL_SIDE_MARGIN))
        item_width = canvas_width - 8
        item_height = int(item_width * 1.42)
        canvas_height = item_height + 6
        layout_signature = (grid_width, item_width, item_height, canvas_width, canvas_height)

        if self.last_thumbnail_layout == layout_signature:
            return False

        self.thumbnail_item_width = item_width
        self.thumbnail_item_height = item_height
        self.thumbnail_canvas_width = canvas_width
        self.thumbnail_canvas_height = canvas_height
        self.last_thumbnail_layout = layout_signature
        self.page_list.setIconSize(QSize(canvas_width, canvas_height))
        self.page_list.setGridSize(QSize(grid_width, canvas_height + 32))
        return True

    def ensure_thumbnails_centered(self):
        # Snap movement keeps item positions from older layouts; re-layout when the first item is off-center.
        if self.page_list.count() == 0:
            return
        rect = self.page_list.visualRect(self.page_list.model().index(0, 0))
        viewport_width = self.page_list.viewport().width()
        if abs(rect.left() - (viewport_width - 1 - rect.right())) > 2 or rect.right() >= viewport_width:
            self.page_list.doItemsLayout()

    def refresh_thumbnail_icons(self):
        layout_changed = self.update_thumbnail_layout()
        if layout_changed:
            self.thumbnail_cache.clear()
            for row in range(self.page_list.count()):
                item = self.page_list.item(row)
                if item is not None:
                    page_ref = item.data(PAGE_ROLE)
                    if page_ref:
                        item.setIcon(self.placeholder_page_icon(page_ref.rotation))
            self.page_list.doItemsLayout()

        for row in self.visible_thumbnail_rows():
            item = self.page_list.item(row)
            if item is None:
                continue
            page_ref = item.data(PAGE_ROLE)
            if page_ref:
                item.setIcon(self.render_thumbnail(page_ref))

        if layout_changed:
            self.page_list.doItemsLayout()
        else:
            self.ensure_thumbnails_centered()

    def visible_thumbnail_rows(self):
        if self.page_list.count() == 0:
            return []

        viewport_rect = self.page_list.viewport().rect()
        rows = set()
        current_row = self.page_list.currentRow()
        if current_row >= 0:
            rows.update(range(max(0, current_row - 2), min(self.page_list.count(), current_row + 3)))

        grid_size = self.page_list.gridSize()
        x_step = max(24, grid_size.width() // 2)
        y_step = max(24, grid_size.height() // 2)
        x_values = list(range(viewport_rect.left(), viewport_rect.right() + 1, x_step))
        y_values = list(range(viewport_rect.top(), viewport_rect.bottom() + 1, y_step))
        if viewport_rect.right() not in x_values:
            x_values.append(viewport_rect.right())
        if viewport_rect.bottom() not in y_values:
            y_values.append(viewport_rect.bottom())

        for y in y_values:
            for x in x_values:
                index = self.page_list.indexAt(QPoint(x, y))
                if index.isValid():
                    rows.add(index.row())
                    rows.add(min(self.page_list.count() - 1, index.row() + 1))

        return sorted(row for row in rows if 0 <= row < self.page_list.count())[:40]

    def schedule_thumbnail_refresh(self, *args):
        if self.thumbnail_refresh_pending:
            self.thumbnail_refresh_timer.start(120)
            return
        self.thumbnail_refresh_pending = True
        self.thumbnail_refresh_timer.start(120)

    def _flush_thumbnail_refresh(self):
        self.thumbnail_refresh_pending = False
        self.refresh_thumbnail_icons()

    def schedule_responsive_preview_refresh(self, *args):
        if self.preview_refresh_pending:
            self.preview_refresh_timer.start(80)
            return
        self.preview_refresh_pending = True
        self.preview_refresh_timer.start(80)

    def _flush_responsive_preview_refresh(self):
        self.preview_refresh_pending = False
        self.refresh_responsive_preview()

    def update_zoom_for_page(self, page_ref):
        page_width, page_height = self.page_display_size(page_ref)
        base_width = self.preview_reference_width or page_width
        base_height = self.preview_reference_height or page_height
        viewport = self.scroll_area.viewport()
        # Height without the horizontal scrollbar, so the fit does not change when that scrollbar appears.
        stable_height = (
            self.scroll_area.height()
            - 2 * self.scroll_area.frameWidth()
            - self.scroll_area.horizontalScrollBar().sizeHint().height()
        )
        available_width = max(240, viewport.width() - 48)
        available_height = max(240, stable_height - 32)
        # 100% = whole page visible and centered (fit page) instead of fit width.
        fitted_zoom = min(available_width / max(1.0, base_width), available_height / max(1.0, base_height))
        self.zoom = min(4.0, max(0.35, fitted_zoom * self.zoom_factor))
        self.zoom_label.setText(f"{int(self.zoom * 100)}%")

    def page_display_size(self, page_ref):
        cache_key = (page_ref.path, page_ref.page_index, page_ref.rotation)
        cached_size = self.page_size_cache.get(cache_key)
        if cached_size is not None:
            return cached_size

        with pdfium.PdfDocument(page_ref.path, password=self.password_for_page_ref(page_ref) or None) as doc:
            width, height = doc[page_ref.page_index].get_size()

        if page_ref.rotation in (90, 270):
            width, height = height, width

        self.page_size_cache[cache_key] = (width, height)
        return width, height

    def render_page_pixmap(self, page_ref, scale, selected_mark_index=None):
        cache_key = (page_ref.path, page_ref.page_index, page_ref.rotation, round(scale, 3))
        cached_pixmap = self.page_render_cache.get(cache_key)
        if cached_pixmap is None:
            with pdfium.PdfDocument(page_ref.path, password=self.password_for_page_ref(page_ref) or None) as doc:
                page = doc[page_ref.page_index]
                bitmap = page.render(scale=scale, rotation=page_ref.rotation)
                image = bitmap.to_pil().convert("RGBA")

            width, height = image.size
            data = image.tobytes("raw", "RGBA")
            qimage = QImage(data, width, height, width * 4, QImage.Format_RGBA8888).copy()
            cached_pixmap = QPixmap.fromImage(qimage)
            if len(self.page_render_cache) >= 24:
                self.page_render_cache.clear()
            self.page_render_cache[cache_key] = cached_pixmap

        pixmap = cached_pixmap.copy()
        self.draw_page_marks(pixmap, page_ref.marks, selected_mark_index=selected_mark_index)
        return pixmap

    def draw_page_marks(self, pixmap, marks, drag_rect=None, selected_mark_index=None):
        if not marks and drag_rect is None:
            return

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        for index, mark in enumerate(marks):
            rect = self.normalized_rect_to_pixmap(mark.rect, pixmap.size())
            self.draw_mark_rect(painter, mark, rect)
            if selected_mark_index == index:
                self.draw_selected_mark_handles(painter, rect)
        if drag_rect is not None and self.annotation_mode:
            self.draw_mark_rect(painter, PageMark(self.annotation_mode, ()), drag_rect)
        painter.end()

    def approval_labels_for_mark(self, mark):
        if mark.data:
            return normalize_approval_line(mark.data)
        return self.approval_line

    def png_path_for_mark(self, mark):
        if mark.kind != "image_stamp" or not mark.data:
            return None
        image_path = Path(mark.data[0])
        return image_path if image_path.is_file() and image_path.suffix.lower() == ".png" else None

    def draw_mark_rect(self, painter, mark, rect):
        if mark.kind == "highlight":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 235, 59, 105))
            painter.drawRect(rect)
            return
        if mark.kind == "approval":
            self.draw_approval_stamp(painter, rect, self.approval_labels_for_mark(mark))
            return
        if mark.kind == "approval_signed":
            labels = normalize_approval_line(mark.data[0])
            signature = QPixmap(str(mark.data[1])) if Path(mark.data[1]).is_file() else None
            paint_approval_table(painter, QRectF(rect), labels, signature, mark.data[2])
            return
        if mark.kind == "image_stamp":
            image_path = self.png_path_for_mark(mark)
            image = QPixmap(str(image_path)) if image_path else QPixmap()
            if not image.isNull():
                opacity = mark.data[1] / 100 if len(mark.data) > 1 else 1.0
                painter.save()
                painter.setOpacity(opacity)
                painter.drawPixmap(rect, image, QRectF(image.rect()))
                painter.restore()
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("#0f766e"), 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
                painter.drawRect(rect)
                return

            painter.setBrush(QColor("#fef2f2"))
            painter.setPen(QPen(QColor("#dc2626"), 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
            painter.drawRect(rect)
            painter.drawText(rect, Qt.AlignCenter, "PNG")
            return

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#dc2626"), 3, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        painter.drawRect(rect)

    def draw_approval_stamp(self, painter, rect, labels):
        paint_approval_table(painter, QRectF(rect), labels)

    def draw_selected_mark_handles(self, painter, rect):
        painter.setPen(QPen(QColor("#2563eb"), 2, Qt.SolidLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        painter.setBrush(QColor("#ffffff"))
        for handle in self.mark_handle_rects(rect).values():
            painter.drawRect(handle)

    def mark_handle_rects(self, rect):
        size = 8
        half = size / 2
        return {
            "nw": QRectF(rect.left() - half, rect.top() - half, size, size),
            "ne": QRectF(rect.right() - half, rect.top() - half, size, size),
            "sw": QRectF(rect.left() - half, rect.bottom() - half, size, size),
            "se": QRectF(rect.right() - half, rect.bottom() - half, size, size),
        }

    def normalized_rect_to_pixmap(self, rect, size):
        left, top, right, bottom = rect
        return QRectF(left * size.width(), top * size.height(), (right - left) * size.width(), (bottom - top) * size.height())

    def render_thumbnail(self, page_ref):
        cache_key = (
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks,
            self.thumbnail_item_width,
            self.thumbnail_item_height,
        )
        cached_icon = self.thumbnail_cache.get(cache_key)
        if cached_icon is not None:
            return cached_icon

        pixmap = self.render_page_pixmap(page_ref, self.thumbnail_scale)
        thumbnail = pixmap.scaled(
            self.thumbnail_item_width,
            self.thumbnail_item_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        canvas = QPixmap(self.thumbnail_canvas_width, self.thumbnail_canvas_height)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 23, 42, 36))
        painter.drawRoundedRect(
            QRectF(7, 7, self.thumbnail_canvas_width - 12, self.thumbnail_canvas_height - 12),
            3,
            3,
        )
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(
            QRectF(4, 4, self.thumbnail_canvas_width - 10, self.thumbnail_canvas_height - 10),
            3,
            3,
        )
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.drawRoundedRect(
            QRectF(4.5, 4.5, self.thumbnail_canvas_width - 11, self.thumbnail_canvas_height - 11),
            3,
            3,
        )
        x = (canvas.width() - thumbnail.width()) // 2
        y = 6 + ((self.thumbnail_canvas_height - 14) - thumbnail.height()) // 2
        painter.drawPixmap(x, y, thumbnail)
        painter.end()

        icon = QIcon(canvas)
        self.thumbnail_cache[cache_key] = icon
        return icon

    def go_to_page(self, value):
        if value > 0:
            self.show_page(value - 1)

    def previous_page(self):
        self.show_page(max(0, self.current_page_index - 1))

    def next_page(self):
        self.show_page(min(self.page_count - 1, self.current_page_index + 1))

    def zoom_in(self, anchor_pos=None):
        self.zoom_factor = min(3.0, self.zoom_factor + 0.15)
        self.update_zoom(anchor_pos)

    def zoom_out(self, anchor_pos=None):
        self.zoom_factor = max(0.35, self.zoom_factor - 0.15)
        self.update_zoom(anchor_pos)

    def update_zoom(self, anchor_pos=None):
        if self.page_count:
            if anchor_pos is None:
                viewport = self.scroll_area.viewport()
                anchor_pos = QPoint(viewport.width() // 2, viewport.height() // 2)
            anchor_state = self.capture_zoom_anchor(anchor_pos)
            self.show_page(self.current_page_index)
            self.restore_zoom_anchor(anchor_state)
            # Scroll ranges can change once more after the layout settles (scrollbar appearing).
            QTimer.singleShot(0, lambda state=anchor_state: self.restore_zoom_anchor(state))
        else:
            self.zoom_label.setText(f"{int(self.zoom_factor * 100)}%")

    def capture_zoom_anchor(self, anchor_pos):
        if anchor_pos is None or self.page_label.pixmap() is None:
            return None

        label_size = self.page_label.size()
        if label_size.width() <= 0 or label_size.height() <= 0:
            return None

        # mapFrom() already includes the current scroll offset (the label is moved by the scroll area).
        label_pos = self.page_label.mapFrom(self.scroll_area.viewport(), anchor_pos)
        content_x = label_pos.x()
        content_y = label_pos.y()
        ratio_x = min(max(content_x / max(1, label_size.width()), 0.0), 1.0)
        ratio_y = min(max(content_y / max(1, label_size.height()), 0.0), 1.0)
        return anchor_pos, ratio_x, ratio_y

    def restore_zoom_anchor(self, anchor_state):
        if anchor_state is None or self.page_label.pixmap() is None:
            return

        anchor_pos, ratio_x, ratio_y = anchor_state
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        target_x = int(round(self.page_label.width() * ratio_x - anchor_pos.x()))
        target_y = int(round(self.page_label.height() * ratio_y - anchor_pos.y()))
        horizontal.setValue(max(0, min(target_x, horizontal.maximum())))
        vertical.setValue(max(0, min(target_y, vertical.maximum())))

    def current_page_item_and_ref(self):
        item = self.page_list.item(self.current_page_index)
        if item is None:
            return None, None
        return item, item.data(PAGE_ROLE)

    def enable_rectangle_mode(self):
        self.annotation_mode = "rect"
        self.preview_drag_rect = None
        self.selected_mark_index = None
        if self.page_count:
            self.show_page(self.current_page_index)
        self.set_status("\ube68\uac04 \uc0ac\uac01\ud615: \ubcf8\ubb38\uc5d0\uc11c \ub4dc\ub798\uadf8\ud558\uc138\uc694.")

    def enable_highlight_mode(self):
        self.annotation_mode = "highlight"
        self.preview_drag_rect = None
        self.selected_mark_index = None
        if self.page_count:
            self.show_page(self.current_page_index)
        self.set_status("\ud615\uad11\ud39c: \ud14d\uc2a4\ud2b8 \uc704\ub97c \ub4dc\ub798\uadf8\ud558\uc138\uc694.")

    def save_stamp_preferences(self):
        save_user_preferences(self.approval_line, self.stamp_image_paths)

    def set_approval_line(self, labels, persist=True):
        self.approval_line = normalize_approval_line(labels)
        if persist:
            self.save_stamp_preferences()
        return self.approval_line

    def configure_approval_line(self):
        text, ok = QInputDialog.getText(
            self,
            "\uacb0\uc7ac\uc120 \uc124\uc815",
            "\uc9c1\ucc45\uc744 \uc27c\ud45c\ub85c \uad6c\ubd84\ud574 \uc785\ub825\ud558\uc138\uc694. (\ucd5c\ub300 6\uac1c)",
            text=", ".join(self.approval_line),
        )
        if not ok:
            return
        self.set_approval_line(text)
        self.set_status(f"\uacb0\uc7ac\uc120 \uc800\uc7a5: {', '.join(self.approval_line)}")

    def store_png_stamp(self, source_path, remove_background=False):
        source = Path(source_path)
        if not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
            raise ValueError("PNG/JPG/BMP \uc774\ubbf8\uc9c0\ub9cc \ub3c4\uc7a5\uc73c\ub85c \ub4f1\ub85d\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.")
        image = QImage(str(source))
        if image.isNull():
            raise ValueError("\uc774\ubbf8\uc9c0\ub97c \uc77d\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")

        stamp_dir = user_data_dir() / "stamps"
        stamp_dir.mkdir(parents=True, exist_ok=True)
        destination = stamp_dir / f"{uuid.uuid4().hex}.png"
        if source.suffix.lower() == ".png":
            shutil.copy2(str(source), str(destination))
        elif not image.save(str(destination), "PNG"):
            raise ValueError("\uc774\ubbf8\uc9c0\ub97c PNG\ub85c \uc800\uc7a5\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.")
        if remove_background:
            remove_white_background(destination)
        self.stamp_image_paths = [path for path in self.stamp_image_paths if Path(path).is_file()]
        self.stamp_image_paths.append(str(destination))
        self.save_stamp_preferences()
        return str(destination)

    def import_stamp_png(self):
        source_path, _ = QFileDialog.getOpenFileName(self, "PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc", "", "\uc774\ubbf8\uc9c0 (*.png *.jpg *.jpeg *.bmp)")
        if not source_path:
            return
        try:
            destination = self.store_png_stamp(source_path)
        except (OSError, ValueError) as exc:
            self.show_error("PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc \uc624\ub958", str(exc))
            return
        if not self.stamp_settings.get("image"):
            self.stamp_settings["image"] = destination
            save_stamp_settings(self.stamp_settings)
        self.set_status(f"PNG \ub3c4\uc7a5 \ubcf4\uad00 \uc644\ub8cc: {Path(destination).name}")

    def add_png_stamp(self, image_path, rect=None, opacity_percent=100):
        if not self.require_pdf():
            return False
        image_path = Path(image_path)
        if QPixmap(str(image_path)).isNull():
            self.show_error("PNG \ub3c4\uc7a5 \uc624\ub958", "\uc774\ubbf8\uc9c0\ub97c \uc77d\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")
            return False

        row = max(0, self.current_page_index)
        item = self.page_list.item(row)
        page_ref = item.data(PAGE_ROLE) if item else None
        if not page_ref:
            return False
        self.save_undo_state()
        stamp_rect = rect or (0.68, 0.68, 0.88, 0.88)
        data = (str(image_path),) if opacity_percent >= 100 else (str(image_path), int(opacity_percent))
        mark = PageMark("image_stamp", stamp_rect, data)
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks + (mark,),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        item.setIcon(self.render_thumbnail(updated_ref))
        self.selected_mark_index = len(updated_ref.marks) - 1
        self.show_page(row)
        self.set_status("PNG \ub3c4\uc7a5\uc744 \ucd94\uac00\ud588\uc2b5\ub2c8\ub2e4. \ub9c8\uc6b0\uc2a4\ub85c \uc704\uce58\uc640 \ud06c\uae30\ub97c \uc870\uc815\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.")
        return True

    def insert_saved_png_stamp(self):
        self.stamp_image_paths = [path for path in self.stamp_image_paths if Path(path).is_file()]
        if not self.stamp_image_paths:
            self.show_error("PNG \ub3c4\uc7a5 \uc5c6\uc74c", "\uba3c\uc800 \uc8fc\uc11d > PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc\u00b7\ubcf4\uad00\uc73c\ub85c \ub3c4\uc7a5\uc744 \ub4f1\ub85d\ud574 \uc8fc\uc138\uc694.")
            return
        labels = [f"도장 {index} ({Path(path).name[:8]})" for index, path in enumerate(self.stamp_image_paths, start=1)]
        label, ok = QInputDialog.getItem(self, "\uc800\uc7a5\ub41c PNG \ub3c4\uc7a5", "\uc0bd\uc785\ud560 \ub3c4\uc7a5", labels, 0, False)
        if ok and label:
            image_path = self.stamp_image_paths[labels.index(label)]
            self.add_png_stamp(
                image_path,
                self.stamp_rect_for_current_page(image_path),
                self.stamp_settings["opacity_percent"],
            )

    def stamp_rect_for_current_page(self, image_path):
        _, page_ref = self.current_page_item_and_ref()
        image = QImage(str(image_path))
        if not page_ref or image.isNull():
            return None
        page_width, page_height = self.page_display_size(page_ref)
        width = self.stamp_settings["width_percent"] / 100
        height = width * (page_width / max(1.0, page_height)) * (image.height() / max(1, image.width()))
        if height > 0.6:
            width *= 0.6 / height
            height = 0.6
        margin_x, margin_y = 0.05, 0.04
        position = self.stamp_settings["position"]
        if position == "center":
            left, top = (1 - width) / 2, (1 - height) / 2
        else:
            left = margin_x if position.endswith("left") else 1 - margin_x - width
            top = margin_y if position.startswith("top") else 1 - margin_y - height
        # Do not cover an approval table that is already on this page: move the stamp just above it.
        for mark in page_ref.marks:
            if mark.kind not in ("approval", "approval_signed"):
                continue
            m_left, m_top, m_right, m_bottom = mark.rect
            overlaps = left < m_right and left + width > m_left and top < m_bottom and top + height > m_top
            if overlaps and m_top - height - 0.01 >= 0:
                top = m_top - height - 0.01
        return (left, top, left + width, top + height)

    def open_stamp_settings(self):
        self.last_stamp_dialog_inserted = False
        self.stamp_settings_dialog = dialog = StampSettingsDialog(self)
        accepted = dialog.exec_() == QDialog.Accepted
        self.stamp_settings_dialog = None
        if not accepted:
            return False
        self.stamp_settings = normalize_stamp_settings(dialog.values())
        save_stamp_settings(self.stamp_settings)
        self.set_status("도장 설정을 저장했습니다.")
        if dialog.insert_after_save and self.stamp_settings.get("image"):
            self.last_stamp_dialog_inserted = bool(self.insert_default_stamp())
        return True

    def insert_default_stamp(self):
        if not self.require_pdf():
            return False
        image_path = self.stamp_settings.get("image")
        if not image_path or not Path(image_path).is_file():
            self.set_status("저장된 도장이 없습니다. 도장 설정에서 도장을 등록하세요.")
            if not self.open_stamp_settings():
                return False
            image_path = self.stamp_settings.get("image")
            if not image_path:
                return False
            if self.last_stamp_dialog_inserted:
                return True
        return self.add_png_stamp(
            image_path,
            self.stamp_rect_for_current_page(image_path),
            self.stamp_settings["opacity_percent"],
        )

    def store_signature_image(self, image):
        signature_dir = user_data_dir() / "signatures"
        signature_dir.mkdir(parents=True, exist_ok=True)
        destination = signature_dir / f"signature_{uuid.uuid4().hex[:8]}.png"
        if not image.save(str(destination), "PNG"):
            raise OSError("서명 이미지를 저장하지 못했습니다.")
        previous = self.signature_settings.get("image")
        if previous and Path(previous).parent == signature_dir and Path(previous) != destination:
            try:
                Path(previous).unlink()
            except OSError:
                pass
        return str(destination)

    def open_signature_settings(self):
        self.signature_dialog = dialog = SignatureDialog(self)
        accepted = dialog.exec_() == QDialog.Accepted
        self.signature_dialog = None
        if not accepted:
            return False
        image, line_text, cell_index = dialog.result_values()
        try:
            image_path = self.store_signature_image(image)
        except OSError as exc:
            self.show_error("서명 저장 오류", str(exc))
            return False
        self.set_approval_line(line_text)
        self.signature_settings = normalize_signature_settings({"image": image_path, "cell": cell_index})
        save_signature_settings(self.signature_settings)
        self.set_status("결재 서명을 저장했습니다.")
        return True

    def insert_signed_approval(self):
        if not self.require_pdf():
            return False
        signature = self.signature_settings.get("image")
        if not signature or not Path(signature).is_file():
            self.set_status("등록된 서명이 없습니다. 서명을 먼저 등록하세요.")
            if not self.open_signature_settings():
                return False
            signature = self.signature_settings.get("image")
        cell = min(self.signature_settings.get("cell", 0), len(self.approval_line) - 1)
        return self.add_approval_stamp_to_first_page(signature_path=signature, signature_cell=cell)

    def add_approval_stamp_to_first_page(self, signature_path=None, signature_cell=0):
        if not self.require_pdf():
            return False

        item = self.page_list.item(0)
        if item is None:
            return
        page_ref = item.data(PAGE_ROLE)
        if not page_ref:
            return

        self.save_undo_state()
        stamp_rect = (0.54, 0.82, 0.96, 0.955)
        if signature_path:
            mark = PageMark("approval_signed", stamp_rect, (tuple(self.approval_line), str(signature_path), int(signature_cell)))
        else:
            mark = PageMark("approval", stamp_rect, tuple(self.approval_line))
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks + (mark,),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        item.setIcon(self.render_thumbnail(updated_ref))
        self.page_list.setCurrentRow(0)
        self.current_page_index = 0
        self.selected_mark_index = len(updated_ref.marks) - 1
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.show_page(0)
        self.set_status("\uccab \ud398\uc774\uc9c0 \ud558\ub2e8\uc5d0 \uacb0\uc7ac \ub3c4\uc7a5\uce78\uc744 \ucd94\uac00\ud588\uc2b5\ub2c8\ub2e4.")
        return True

    def disable_annotation_mode(self):
        self.annotation_mode = None
        self.preview_drag_rect = None
        if self.page_count:
            self.show_page(self.current_page_index)
        self.set_status("\uc8fc\uc11d \ubaa8\ub4dc \ub044\uae30")

    def update_preview_drag_rect(self, rect):
        self.preview_drag_rect = self.clamped_label_rect(rect)
        self.show_page(self.current_page_index)

    def finish_preview_annotation(self, rect):
        self.preview_drag_rect = None
        rect = self.clamped_label_rect(rect)
        if rect.width() < 4 or rect.height() < 4:
            self.annotation_mode = None
            self.show_page(self.current_page_index)
            return

        item, page_ref = self.current_page_item_and_ref()
        if not page_ref:
            return

        self.save_undo_state()
        size = self.preview_content_size if not self.preview_content_size.isEmpty() else self.page_label.size()
        mark = PageMark(self.annotation_mode, self.pixmap_rect_to_normalized(rect, size))
        marked_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks + (mark,),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, marked_ref)
        item.setIcon(self.render_thumbnail(marked_ref))
        self.annotation_mode = None
        self.selected_mark_index = len(marked_ref.marks) - 1
        self.show_page(self.current_page_index)
        self.set_status("\uc8fc\uc11d \ucd94\uac00 \uc644\ub8cc")

    def clamped_label_rect(self, rect):
        content_rect = self.preview_content_rect
        if content_rect.isEmpty():
            size = self.page_label.pixmap().size() if self.page_label.pixmap() else self.page_label.size()
            content_rect = QRectF(0, 0, size.width(), size.height())
        left = max(content_rect.left(), min(rect.left(), content_rect.right()))
        top = max(content_rect.top(), min(rect.top(), content_rect.bottom()))
        right = max(content_rect.left(), min(rect.right(), content_rect.right()))
        bottom = max(content_rect.top(), min(rect.bottom(), content_rect.bottom()))
        return QRectF(
            QPointF(left - content_rect.left(), top - content_rect.top()),
            QPointF(right - content_rect.left(), bottom - content_rect.top()),
        ).normalized()

    def pixmap_rect_to_normalized(self, rect, size):
        left = rect.left() / max(1, size.width())
        top = rect.top() / max(1, size.height())
        right = rect.right() / max(1, size.width())
        bottom = rect.bottom() / max(1, size.height())
        return (left, top, right, bottom)

    def select_mark(self, mark_index):
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.selected_mark_index = mark_index
        self.show_page(self.current_page_index)

    def current_mark_rect(self, mark_index):
        _, page_ref = self.current_page_item_and_ref()
        if not page_ref or mark_index is None or not (0 <= mark_index < len(page_ref.marks)):
            return None
        return page_ref.marks[mark_index].rect

    def hit_test_mark(self, position):
        _, page_ref = self.current_page_item_and_ref()
        if self.preview_content_size.isEmpty() or not page_ref or not self.preview_content_rect.contains(position):
            return None

        position = QPointF(
            position.x() - self.preview_content_rect.left(),
            position.y() - self.preview_content_rect.top(),
        )

        for index in range(len(page_ref.marks) - 1, -1, -1):
            mark = page_ref.marks[index]
            rect = self.normalized_rect_to_pixmap(mark.rect, self.preview_content_size)
            for action, handle in self.mark_handle_rects(rect).items():
                if handle.contains(position):
                    return index, action
            if rect.contains(position):
                return index, "move"
        return None

    def update_existing_mark(self, mark_index, action, start_pos, current_pos, original_rect, refresh_icon=False):
        if original_rect is None:
            return

        if self.preview_content_size.isEmpty():
            return

        dx = (current_pos.x() - start_pos.x()) / max(1, self.preview_content_size.width())
        dy = (current_pos.y() - start_pos.y()) / max(1, self.preview_content_size.height())
        left, top, right, bottom = original_rect

        if action == "move":
            width = right - left
            height = bottom - top
            left = min(max(0.0, left + dx), 1.0 - width)
            top = min(max(0.0, top + dy), 1.0 - height)
            right = left + width
            bottom = top + height
        else:
            if "w" in action:
                left += dx
            if "e" in action:
                right += dx
            if "n" in action:
                top += dy
            if "s" in action:
                bottom += dy

        self.replace_mark_rect(mark_index, (left, top, right, bottom), refresh_icon)

    def clamp_normalized_rect(self, rect):
        left, top, right, bottom = rect
        left = min(max(0.0, left), 1.0)
        top = min(max(0.0, top), 1.0)
        right = min(max(0.0, right), 1.0)
        bottom = min(max(0.0, bottom), 1.0)
        if left > right:
            left, right = right, left
        if top > bottom:
            top, bottom = bottom, top

        min_size = 0.005
        if right - left < min_size:
            right = min(1.0, left + min_size)
        if bottom - top < min_size:
            bottom = min(1.0, top + min_size)
        return (left, top, right, bottom)

    def replace_mark_rect(self, mark_index, rect, refresh_icon):
        item, page_ref = self.current_page_item_and_ref()
        if not page_ref or not (0 <= mark_index < len(page_ref.marks)):
            return

        marks = list(page_ref.marks)
        marks[mark_index] = PageMark(
            marks[mark_index].kind,
            self.clamp_normalized_rect(rect),
            marks[mark_index].data,
        )
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            tuple(marks),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        if refresh_icon:
            item.setIcon(self.render_thumbnail(updated_ref))
        self.selected_mark_index = mark_index
        self.show_page(self.current_page_index)

    def delete_selected_mark(self):
        item, page_ref = self.current_page_item_and_ref()
        mark_index = self.selected_mark_index
        if not page_ref or mark_index is None or not (0 <= mark_index < len(page_ref.marks)):
            return

        self.save_undo_state()
        marks = list(page_ref.marks)
        del marks[mark_index]
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            tuple(marks),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        item.setIcon(self.render_thumbnail(updated_ref))
        self.selected_mark_index = None
        self.show_page(self.current_page_index)
        self.set_status("\uc8fc\uc11d \uc0ad\uc81c \uc644\ub8cc")

    def rotate_current_page(self, degrees):
        row = self.current_page_index
        if row < 0 or row >= self.page_list.count():
            return
        self.rotate_page_rows([row], degrees)

    def rotate_selected_pages_right(self):
        self.rotate_page_rows(self.selected_page_rows(), 90)

    def rotate_selected_pages_left(self):
        self.rotate_page_rows(self.selected_page_rows(), -90)

    def rotate_page_rows(self, rows, degrees):
        if not rows:
            return

        self.save_undo_state()
        changed_rows = []
        for row in rows:
            item = self.page_list.item(row)
            if item is None:
                continue
            page_ref = item.data(PAGE_ROLE)
            if not page_ref:
                continue
            rotated_ref = PageRef(
                page_ref.path,
                page_ref.page_index,
                (page_ref.rotation + degrees) % 360,
                page_ref.marks,
                page_ref.password,
            )
            item.setData(PAGE_ROLE, rotated_ref)
            item.setIcon(self.render_thumbnail(rotated_ref))
            changed_rows.append(row)

        if not changed_rows:
            return

        current_row = self.current_page_index
        if current_row in changed_rows:
            self.show_page(current_row)
        self.set_status(f"{len(changed_rows)}\uac1c \ud398\uc774\uc9c0 \ud68c\uc804 \uc644\ub8cc")

    def show_preview_context_menu(self, position):
        if self.page_count <= 0:
            return

        menu = QMenu(self)
        rotate_right = menu.addAction(make_tool_icon("rotate_right"), "\uc624\ub978\ucabd \ud68c\uc804")
        rotate_left = menu.addAction(make_tool_icon("rotate_left"), "\uc67c\ucabd \ud68c\uc804")
        menu.addSeparator()
        rect_mark = menu.addAction(make_tool_icon("rect_mark"), "\ube68\uac04 \uc0ac\uac01\ud615")
        highlight = menu.addAction(make_tool_icon("highlight"), "\ud615\uad11\ud39c")
        stamp = menu.addAction(make_tool_icon("stamp"), "도장")
        approval_stamp = menu.addAction(make_tool_icon("signature"), "결재 (내 서명)")
        selected_action = menu.exec_(self.page_label.mapToGlobal(position))
        if selected_action == rotate_right:
            self.rotate_current_page(90)
        elif selected_action == rotate_left:
            self.rotate_current_page(-90)
        elif selected_action == rect_mark:
            self.enable_rectangle_mode()
        elif selected_action == highlight:
            self.enable_highlight_mode()
        elif selected_action == stamp:
            self.insert_default_stamp()
        elif selected_action == approval_stamp:
            self.insert_signed_approval()

    def save_copy_as(self):
        if not self.require_pdf():
            return

        output_path = self.ask_save_path("PDF \ub2e4\ub978 \uc774\ub984\uc73c\ub85c \uc800\uc7a5")
        if not output_path:
            return

        self.write_ordered_pdf(output_path, self.ordered_page_refs())
        self.current_pdf_path = output_path
        self.set_status(f"\uc800\uc7a5\ub428: {output_path}")

    def ask_new_pdf_password(self):
        password, ok = QInputDialog.getText(
            self,
            "PDF \uc554\ud638 \uc124\uc815",
            "\uc0c8 PDF \uc554\ud638\ub97c \uc785\ub825\ud558\uc138\uc694.",
            QLineEdit.Password,
        )
        if not ok:
            return None
        if not password:
            self.show_error("\uc554\ud638 \uc124\uc815", "\ube48 \uc554\ud638\ub294 \uc0ac\uc6a9\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")
            return None

        confirm, ok = QInputDialog.getText(
            self,
            "PDF \uc554\ud638 \ud655\uc778",
            "\uc0c8 PDF \uc554\ud638\ub97c \ud55c \ubc88 \ub354 \uc785\ub825\ud558\uc138\uc694.",
            QLineEdit.Password,
        )
        if not ok:
            return None
        if password != confirm:
            self.show_error("\uc554\ud638 \ud655\uc778", "\uc554\ud638\uac00 \uc11c\ub85c \ub2e4\ub985\ub2c8\ub2e4.")
            return None
        return password

    def save_pdf_with_password(self):
        if not self.require_pdf():
            return

        password = self.ask_new_pdf_password()
        if password is None:
            return

        output_path = self.ask_save_path("PDF \uc554\ud638 \uc124\uc815 \uc800\uc7a5")
        if not output_path:
            return

        try:
            self.write_ordered_pdf(output_path, self.ordered_page_refs(), password=password)
            self.current_pdf_path = output_path
            self.pdf_passwords[str(Path(output_path))] = password
            self.set_status(f"\uc554\ud638 PDF \uc800\uc7a5\ub428: {output_path}")
        except Exception as exc:
            self.show_error("PDF \uc554\ud638 \uc124\uc815 \uc624\ub958", str(exc))

    def save_pdf_without_password(self):
        if not self.require_pdf():
            return

        output_path = self.ask_save_path("PDF \uc554\ud638 \ud574\uc81c \uc800\uc7a5")
        if not output_path:
            return

        try:
            self.write_ordered_pdf(output_path, self.ordered_page_refs(), password=None)
            self.current_pdf_path = output_path
            self.pdf_passwords.pop(str(Path(output_path)), None)
            self.set_status(f"\uc554\ud638 \ud574\uc81c PDF \uc800\uc7a5\ub428: {output_path}")
        except Exception as exc:
            self.show_error("PDF \uc554\ud638 \ud574\uc81c \uc624\ub958", str(exc))

    def print_pdf(self):
        if not self.require_pdf():
            return

        printer = QPrinter(QPrinter.ScreenResolution)
        printer.setResolution(PRINT_DPI)
        printer.setDocName(Path(self.current_pdf_path).name if self.current_pdf_path else APP_TITLE)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("PDF \uc778\uc1c4")
        if dialog.exec_() != QPrintDialog.Accepted:
            return

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
                temp_path = temp_file.name
            self.write_ordered_pdf(temp_path, self.ordered_page_refs())
            self.print_pdf_file(temp_path, printer)
            self.set_status("\uc778\uc1c4 \uc791\uc5c5\uc744 \ubcf4\ub0c8\uc2b5\ub2c8\ub2e4.")
        except Exception as exc:
            self.show_error("\uc778\uc1c4 \uc624\ub958", str(exc))
        finally:
            if temp_path and Path(temp_path).exists():
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def print_pdf_file(self, pdf_path, printer):
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError("\ud504\ub9b0\ud130\ub97c \uc2dc\uc791\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")

        try:
            with pdfium.PdfDocument(pdf_path) as doc:
                for page_index in range(len(doc)):
                    if page_index > 0:
                        if not printer.newPage():
                            raise RuntimeError("Could not create a new print page.")

                    page = doc[page_index]
                    page_width, page_height = page.get_size()
                    page_rect = self.printer_page_rect(printer)
                    target_scale = min(
                        page_rect.width() / max(1.0, page_width),
                        page_rect.height() / max(1.0, page_height),
                    )
                    render_scale = min(max(0.1, target_scale), PRINT_MAX_RENDER_SCALE)
                    bitmap = page.render(scale=render_scale)
                    image = bitmap.to_pil().convert("RGBA")
                    width, height = image.size
                    data = image.tobytes("raw", "RGBA")
                    qimage = QImage(data, width, height, width * 4, QImage.Format_RGBA8888).copy()
                    draw_width = page_width * target_scale
                    draw_height = page_height * target_scale
                    x = page_rect.x() + max(0, (page_rect.width() - draw_width) / 2)
                    y = page_rect.y() + max(0, (page_rect.height() - draw_height) / 2)
                    target_rect = QRectF(float(x), float(y), float(draw_width), float(draw_height))
                    painter.drawImage(target_rect, qimage)
        finally:
            painter.end()

    def printer_page_rect(self, printer):
        try:
            return printer.pageRect(QPrinter.DevicePixel)
        except TypeError:
            return printer.pageRect()

    def save_current(self):
        if not self.require_pdf():
            return

        if not self.current_pdf_path:
            self.save_copy_as()
            return

        output_path = str(Path(self.current_pdf_path))
        temp_path = None
        try:
            output_dir = Path(output_path).parent
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=output_dir) as temp_file:
                temp_path = temp_file.name

            self.write_ordered_pdf(temp_path, self.ordered_page_refs())
            os.replace(temp_path, output_path)
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.set_status(f"\uc800\uc7a5\ub428: {output_path}")
        except Exception as exc:
            if temp_path and Path(temp_path).exists():
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            self.show_error("\uc800\uc7a5 \uc624\ub958", str(exc))

    def merge_pdfs(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "\ubcd1\ud569\ud560 PDF \uc120\ud0dd", "", PDF_FILTER)
        if not paths:
            return

        self.save_undo_state()
        previous_pdf_path = self.current_pdf_path
        if self.page_list.count() == 0:
            self.current_pdf_path = paths[0]
        added_count = 0
        for path in paths:
            before_count = self.page_list.count()
            if self.add_pdf_pages_to_list(path):
                added_count += self.page_list.count() - before_count
        if added_count == 0:
            self.undo_stack.pop()
            self.current_pdf_path = previous_pdf_path
            return
        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        if self.page_list.currentRow() < 0:
            self.page_list.setCurrentRow(0)
            self.show_page(0)
        self.set_status(f"PDF {len(paths)}\uac1c\uac00 \ub4a4\uc5d0 \ucd94\uac00\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

    def extract_pages(self):
        if not self.require_pdf():
            return

        text, ok = QInputDialog.getText(
            self,
            "\ud398\uc774\uc9c0 \ucd94\ucd9c",
            f"\ucd94\ucd9c\ud560 \ud398\uc774\uc9c0\ub97c \uc785\ub825\ud558\uc138\uc694. (1-{self.page_count})\n\uc608: 1-3, 5, 8",
        )
        if not ok:
            return

        try:
            selected_pages = parse_page_ranges(text, self.page_count)
        except ValueError as exc:
            self.show_error("\ud398\uc774\uc9c0 \ubc94\uc704 \uc624\ub958", str(exc))
            return

        output_path = self.ask_save_path("\ucd94\ucd9c\ud55c PDF \uc800\uc7a5")
        if not output_path:
            return

        ordered_refs = self.ordered_page_refs()
        selected_refs = [ordered_refs[index] for index in selected_pages]
        self.write_ordered_pdf(output_path, selected_refs)
        self.load_pdf(output_path)
        self.set_status(f"{len(selected_pages)}\uac1c \ud398\uc774\uc9c0 \ucd94\ucd9c \uc644\ub8cc")

    def insert_pdf(self):
        if not self.require_pdf():
            return

        insert_path, _ = QFileDialog.getOpenFileName(self, "\uc0bd\uc785\ud560 PDF \uc120\ud0dd", "", PDF_FILTER)
        if not insert_path:
            return

        choices = [
            f"\ud604\uc7ac \ud398\uc774\uc9c0 \uc55e ({self.current_page_index + 1})",
            f"\ud604\uc7ac \ud398\uc774\uc9c0 \ub4a4 ({self.current_page_index + 1})",
            "\ub9e8 \ub4a4",
        ]
        choice, ok = QInputDialog.getItem(self, "\uc0bd\uc785 \uc704\uce58", "\uc704\uce58", choices, 1, False)
        if not ok:
            return

        self.save_undo_state()
        insert_row = self.page_list.count()
        if choice.startswith("\ud604\uc7ac \ud398\uc774\uc9c0 \uc55e"):
            insert_row = self.current_page_index
        elif choice.startswith("\ud604\uc7ac \ud398\uc774\uc9c0 \ub4a4"):
            insert_row = self.current_page_index + 1

        new_items = self.create_items_for_pdf(insert_path)
        if not new_items:
            self.undo_stack.pop()
            return
        for offset, item in enumerate(new_items):
            self.page_list.insertItem(insert_row + offset, item)

        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        self.page_list.setCurrentRow(insert_row)
        self.show_page(insert_row)
        self.set_status(f"\uc0bd\uc785 \uc644\ub8cc: {Path(insert_path).name}")

    def create_items_for_pdf(self, path):
        items = []
        page_refs = self.page_refs_for_pdf_with_password(path)
        if page_refs is None:
            return items
        for page_ref in page_refs:
            items.append(self.create_item_for_page_ref(page_ref))
        return items

    def ask_save_path(self, title):
        if self.current_pdf_path:
            default_dir = str(Path(self.current_pdf_path).parent)
        else:
            default_dir = ""

        output_path, _ = QFileDialog.getSaveFileName(self, title, default_dir, PDF_FILTER)
        if output_path and not output_path.lower().endswith(".pdf"):
            output_path += ".pdf"
        return output_path

    def write_ordered_pdf(self, output_path, page_refs, password=None):
        writer = PdfWriter()
        reader_cache = {}
        output_password = password
        try:
            for page_ref in page_refs:
                reader = reader_cache.get(page_ref.path)
                if reader is None:
                    reader = PdfReader(page_ref.path)
                    if getattr(reader, "is_encrypted", False):
                        source_password = self.password_for_page_ref(page_ref)
                        if not source_password or reader.decrypt(source_password) == 0:
                            raise ValueError(f"PDF \uc554\ud638\uac00 \ud544\uc694\ud569\ub2c8\ub2e4: {Path(page_ref.path).name}")
                    reader_cache[page_ref.path] = reader
                writer.add_page(reader.pages[page_ref.page_index])
                added_page = writer.pages[-1]
                if page_ref.rotation:
                    if hasattr(added_page, "rotate"):
                        added_page.rotate(page_ref.rotation)
                    else:
                        added_page.rotate_clockwise(page_ref.rotation)
                self.add_pdf_mark_annotations(writer, added_page, page_ref)
            self.write_pdf(writer, output_path, password=output_password)
        finally:
            reader_cache.clear()

    def add_pdf_mark_annotations(self, writer, page, page_ref):
        if not page_ref.marks:
            return

        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        annots = page.get("/Annots")
        if annots is None:
            annots = ArrayObject()
            page[NameObject("/Annots")] = annots
        else:
            annots = annots.get_object()

        for mark in page_ref.marks:
            if mark.kind == "approval":
                self.add_approval_table_to_pdf_page(page, mark.rect, self.approval_labels_for_mark(mark))
                continue
            if mark.kind == "approval_signed":
                self.add_approval_table_to_pdf_page(page, mark.rect, normalize_approval_line(mark.data[0]), mark.data[1], mark.data[2])
                continue
            if mark.kind == "image_stamp":
                self.add_png_stamp_to_pdf_page(page, mark, width, height)
                continue

            left, top, right, bottom = mark.rect
            x1 = left * width
            x2 = right * width
            y1 = height - (bottom * height)
            y2 = height - (top * height)
            rect = ArrayObject([FloatObject(x1), FloatObject(y1), FloatObject(x2), FloatObject(y2)])

            if mark.kind == "highlight":
                annotation = DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Highlight"),
                        NameObject("/Rect"): rect,
                        NameObject("/QuadPoints"): ArrayObject(
                            [
                                FloatObject(x1),
                                FloatObject(y2),
                                FloatObject(x2),
                                FloatObject(y2),
                                FloatObject(x1),
                                FloatObject(y1),
                                FloatObject(x2),
                                FloatObject(y1),
                            ]
                        ),
                        NameObject("/C"): ArrayObject([FloatObject(1), FloatObject(0.92), FloatObject(0)]),
                        NameObject("/CA"): FloatObject(0.45),
                        NameObject("/F"): NumberObject(4),
                    }
                )
            else:
                annotation = DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Square"),
                        NameObject("/Rect"): rect,
                        NameObject("/C"): ArrayObject([FloatObject(1), FloatObject(0), FloatObject(0)]),
                        NameObject("/BS"): DictionaryObject(
                            {NameObject("/Type"): NameObject("/Border"), NameObject("/W"): NumberObject(2)}
                        ),
                        NameObject("/F"): NumberObject(4),
                    }
                )

            annots.append(writer._add_object(annotation))

    def add_png_stamp_to_pdf_page(self, page, mark, width, height):
        image_path = self.png_path_for_mark(mark)
        if image_path is None:
            return
        # Keep PNG transparency (SMask) so the stamp does not cover the text with a white box.
        opacity = mark.data[1] / 100 if len(mark.data) > 1 else 1.0
        self.merge_png_into_pdf_page(page, image_path, mark.rect, opacity)

    @staticmethod
    def pdf_display_to_user_point(rotation, box_width, box_height, x, y):
        """Displayed page point (top-left origin, after /Rotate) -> unrotated PDF user space."""
        if rotation == 90:
            return y, x
        if rotation == 180:
            return box_width - x, y
        if rotation == 270:
            return box_width - y, box_height - x
        return x, box_height - y

    def merge_png_into_pdf_page(self, page, image_path, normalized_rect, opacity=1.0):
        """Draw a PNG into the page content, fitted (aspect kept) and centered in a rect given in
        displayed-page coordinates, so it lands where the preview shows it even on rotated pages."""
        rotation = int(getattr(page, "rotation", 0) or 0) % 360
        box = page.cropbox
        box_left, box_bottom = float(box.left), float(box.bottom)
        box_width, box_height = float(box.width), float(box.height)
        display_width, display_height = (box_height, box_width) if rotation in (90, 270) else (box_width, box_height)

        temporary_paths = []
        try:
            with Image.open(image_path) as source:
                image = source.convert("RGBA")
            left, top, right, bottom = normalized_rect
            target_width = max(1.0, (right - left) * display_width)
            target_height = max(1.0, (bottom - top) * display_height)
            ratio = min(target_width / image.width, target_height / image.height)
            draw_width, draw_height = image.width * ratio, image.height * ratio
            dx0 = left * display_width + (target_width - draw_width) / 2
            dy0 = top * display_height + (target_height - draw_height) / 2
            if rotation:
                image = image.rotate(rotation, expand=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temporary_file:
                temporary_paths.append(temporary_file.name)
            image.save(temporary_paths[-1], "PNG")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temporary_file:
                temporary_paths.append(temporary_file.name)
                temporary_file.write(png_image_to_pdf_page_bytes(temporary_paths[0], opacity))

            stamp_page = PdfReader(temporary_paths[-1]).pages[0]
            source_width = float(stamp_page.mediabox.width)
            source_height = float(stamp_page.mediabox.height)
            corners = [
                self.pdf_display_to_user_point(rotation, box_width, box_height, dx0, dy0),
                self.pdf_display_to_user_point(rotation, box_width, box_height, dx0 + draw_width, dy0 + draw_height),
            ]
            ux0, ux1 = sorted(point[0] for point in corners)
            uy0, uy1 = sorted(point[1] for point in corners)
            transform = Transformation().scale((ux1 - ux0) / source_width, (uy1 - uy0) / source_height).translate(
                box_left + ux0, box_bottom + uy0
            )
            page.merge_transformed_page(stamp_page, transform, expand=False)
        finally:
            for temporary_path in temporary_paths:
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass

    def add_approval_table_to_pdf_page(self, page, normalized_rect, labels, signature_path=None, signature_cell=0):
        """Approval table goes into the page content as a print-resolution image: it looks identical in
        every viewer (Edge/Chrome/Acrobat), Korean text needs no PDF font, and it matches the preview."""
        rotation = int(getattr(page, "rotation", 0) or 0) % 360
        box = page.cropbox
        display_width, display_height = (float(box.height), float(box.width)) if rotation in (90, 270) else (float(box.width), float(box.height))
        left, top, right, bottom = normalized_rect
        width_pt = max(1.0, (right - left) * display_width)
        height_pt = max(1.0, (bottom - top) * display_height)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temporary_file:
            image_path = temporary_file.name
        try:
            render_approval_image(image_path, labels, width_pt, height_pt, signature_path, signature_cell)
            self.merge_png_into_pdf_page(page, image_path, normalized_rect)
        finally:
            try:
                os.remove(image_path)
            except OSError:
                pass

    def write_pdf(self, writer, output_path, password=None):
        output_dir = Path(output_path).parent
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        if password:
            writer.encrypt(user_password=password, owner_password=password)

        with open(output_path, "wb") as output_file:
            writer.write(output_file)

    def require_pdf(self):
        if self.page_list.count() > 0:
            return True
        self.show_error("PDF \uc5c6\uc74c", "\uba3c\uc800 PDF \ud30c\uc77c\uc744 \uc5f4\uc5b4\uc8fc\uc138\uc694.")
        return False

    def show_file_info(self):
        if not self.require_pdf():
            return
        QMessageBox.information(
            self,
            "\ud604\uc7ac \ud30c\uc77c \uc815\ubcf4",
            f"\ud398\uc774\uc9c0 \uc218: {self.page_count}\n\ucd9c\ucc98 PDF \uc218: {len(set(ref.path for ref in self.ordered_page_refs()))}",
        )

    def refresh_default_app_menu(self):
        try:
            is_default = is_pdf_default_app()
            registered = is_pdf_association_registered()
        except OSError:
            is_default, registered = False, False
        self.default_app_action.setChecked(is_default)
        self.default_app_action.setText(
            "PDF \uae30\ubcf8 \uc571: Koinfra PDF (\uc0ac\uc6a9 \uc911)" if is_default else "PDF \uae30\ubcf8 \uc571\uc73c\ub85c \uc124\uc815\u2026"
        )
        self.unregister_assoc_action.setEnabled(registered)

    def register_openwith_from_menu(self):
        try:
            register_openwith()
        except OSError as exc:
            self.show_error("\ub4f1\ub85d \uc624\ub958", str(exc))
            return
        save_association_preferences(disabled=False)
        QMessageBox.information(self, "\ub4f1\ub85d \uc644\ub8cc", "Koinfra PDF\uac00 PDF '\uc5f0\uacb0 \ud504\ub85c\uadf8\ub7a8' \ubaa9\ub85d\uc5d0 \ub4f1\ub85d\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

    def unregister_openwith_from_menu(self):
        answer = QMessageBox.question(
            self,
            "PDF \uc5f0\uacb0 \ub4f1\ub85d \ud574\uc81c",
            "Koinfra PDF\uc758 PDF \uc5f0\uacb0 \ub4f1\ub85d\uc744 \uc9c0\uc6b8\uae4c\uc694?\n"
            "(\ud604\uc7ac \uae30\ubcf8 \uc571\uc774\uba74 Windows\uac00 \ub2e4\uc74c\uc5d0 PDF\ub97c \uc5f4 \ub54c \ub2e4\ub978 \uc571\uc744 \uace0\ub974\uac8c \ud569\ub2c8\ub2e4.)",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            unregister_openwith()
        except OSError as exc:
            self.show_error("\ud574\uc81c \uc624\ub958", str(exc))
            return
        save_association_preferences(disabled=True)  # do not silently re-register on the next start
        self.set_status("PDF \uc5f0\uacb0 \ub4f1\ub85d\uc744 \ud574\uc81c\ud588\uc2b5\ub2c8\ub2e4.")

    def set_pdf_default_app(self):
        """One click: Windows app picker ('Always'), else Settings > Default apps; then confirm automatically."""
        if self.default_app_action.isCheckable():
            self.default_app_action.setChecked(False)  # the check mark only reflects the real Windows state
        try:
            if is_pdf_default_app():
                self.refresh_default_app_menu()
                QMessageBox.information(self, "PDF \uae30\ubcf8 \uc571", "\uc774\ubbf8 Koinfra PDF\uac00 PDF \uae30\ubcf8 \uc571\uc785\ub2c8\ub2e4.")
                return
            register_openwith()
        except OSError as exc:
            self.show_error("\uae30\ubcf8 \uc571 \uc124\uc815 \uc624\ub958", str(exc))
            return
        save_association_preferences(disabled=False)
        self.start_default_app_watch()
        before = open_with_process_ids()
        try:
            process = launch_open_with_picker(default_app_check_pdf_path())
        except (OSError, RuntimeError):
            self.open_default_pdf_app_settings()
            return
        self.open_with_attempt = {"process": process, "before": before, "started": time.monotonic(), "shown": False}
        self.set_status("Windows \uc571 \uc120\ud0dd \ucc3d\uc744 \uc5ec\ub294 \uc911\u2026")
        QTimer.singleShot(300, self.poll_open_with_picker)

    def poll_open_with_picker(self):
        attempt = self.open_with_attempt
        if attempt is None or attempt["shown"]:
            return
        process = attempt["process"]
        own_pid = getattr(process, "pid", None)
        blocked = _visible_windows_of({own_pid}) if own_pid else []
        if blocked:  # Windows answered with "go to Settings" instead of a picker
            close_windows(blocked)
            self.fall_back_to_settings()
            return
        pickers = _visible_windows_of(open_with_process_ids() - set(attempt["before"]))
        if pickers:
            attempt["shown"] = True
            self.show_default_app_guide(DEFAULT_APP_GUIDE_PICKER)
            return
        if (time.monotonic() - attempt["started"]) * 1000 >= OPEN_WITH_PICKER_TIMEOUT_MS:
            self.fall_back_to_settings()
            return
        QTimer.singleShot(300, self.poll_open_with_picker)

    def fall_back_to_settings(self):
        attempt, self.open_with_attempt = self.open_with_attempt, None
        if attempt is not None:
            stop_open_with_leftovers(attempt["process"], attempt["before"])
        self.open_default_pdf_app_settings()

    def open_default_pdf_app_settings(self):
        """Fallback: Settings > Default apps opened on the Koinfra PDF page."""
        try:
            open_default_pdf_app_settings()
        except OSError as exc:
            self.show_error("\uae30\ubcf8 \uc571 \uc124\uc815 \uc624\ub958", str(exc))
            return
        save_association_preferences(disabled=False)
        self.start_default_app_watch()
        self.show_default_app_guide(DEFAULT_APP_GUIDE_SETTINGS)

    def show_default_app_guide(self, text):
        """Non-blocking hint next to the Windows window (does not steal focus from it)."""
        self.set_status(text)
        warning = association_target_warning()
        if self.default_app_guide is not None:
            self.default_app_guide.close()
        box = QMessageBox(self)
        box.setWindowTitle("PDF \uae30\ubcf8 \uc571 \uc124\uc815")
        box.setIcon(QMessageBox.Information)
        box.setText(text)
        box.setInformativeText(("\uc644\ub8cc\ub418\uba74 \uc790\ub3d9\uc73c\ub85c \ud655\uc778\ud574 \uc54c\ub824\ub4dc\ub9bd\ub2c8\ub2e4." + ("\n\n" + warning if warning else "")))
        box.setStandardButtons(QMessageBox.Ok)
        box.setModal(False)
        box.setAttribute(Qt.WA_ShowWithoutActivating, True)
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.finished.connect(lambda _result, b=box: self._forget_guide(b))
        self.default_app_guide = box
        box.show()

    def _forget_guide(self, box):
        if self.default_app_guide is box:
            self.default_app_guide = None

    def start_default_app_watch(self):
        """Re-check the Windows default when we get focus back, plus a light 2 s poll for a few minutes."""
        self.default_app_done_shown = False
        if self.default_app_watch is None:
            self.default_app_watch = QTimer(self)
            self.default_app_watch.setInterval(2000)
            self.default_app_watch.timeout.connect(lambda: self.check_default_app_done(from_focus=False))
        self.default_app_watch_deadline = time.monotonic() + DEFAULT_APP_WATCH_MS / 1000
        self.default_app_watch.start()

    def on_application_state_changed(self, state):
        if state == Qt.ApplicationActive and self.default_app_watch is not None and self.default_app_watch.isActive():
            QTimer.singleShot(400, lambda: self.check_default_app_done(from_focus=True))

    def check_default_app_done(self, from_focus=False):
        watch = self.default_app_watch
        if watch is None or not watch.isActive():
            return False
        try:
            done = is_pdf_default_app()
        except OSError:
            done = False
        if not done:
            if time.monotonic() > getattr(self, "default_app_watch_deadline", 0):
                watch.stop()
            return False
        watch.stop()
        self.open_with_attempt = None
        if self.default_app_guide is not None:
            self.default_app_guide.close()
        self.refresh_default_app_menu()
        self.set_status(DEFAULT_APP_DONE_TEXT + " \u2014 \uc774\uc81c PDF\ub294 Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4.")
        if not self.default_app_done_shown:
            self.default_app_done_shown = True
            QMessageBox.information(self, DEFAULT_APP_DONE_TEXT, "\uc774\uc81c PDF \ud30c\uc77c\uc744 \uc5f4\uba74 Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4.")
        return True

    def maybe_ask_default_app(self):
        """Once at start: offer to make Koinfra PDF the default (with 'don't ask again')."""
        prefs = load_association_preferences()
        if prefs["disabled"] or not prefs["ask_default"]:
            return
        try:
            if is_pdf_default_app():
                return
        except OSError:
            return
        box = QMessageBox(self)
        box.setWindowTitle("PDF \uae30\ubcf8 \uc571")
        box.setIcon(QMessageBox.Question)
        box.setText("PDF\ub97c \uc5f4 \ub54c \ud56d\uc0c1 Koinfra PDF\ub97c \uc0ac\uc6a9\ud558\ub3c4\ub85d \uc124\uc815\ud560\uae4c\uc694?")
        box.setInformativeText("\ub098\uc911\uc5d0 [\ub3c4\uad6c] \uba54\ub274\uc5d0\uc11c\ub3c4 \uc124\uc815\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dont_ask = QCheckBox("\ub2e4\uc2dc \ubb3b\uc9c0 \uc54a\uae30")
        box.setCheckBox(dont_ask)
        self.default_app_prompt = box
        answer = box.exec_()
        self.default_app_prompt = None
        if dont_ask.isChecked():
            save_association_preferences(ask_default=False)
        if answer == QMessageBox.Yes:
            self.set_pdf_default_app()

    def show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def set_status(self, message):
        self.statusBar().showMessage(message)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if any(url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf") for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            self.append_pdf_files(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


def main():
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    configure_windows_taskbar_icon()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app_icon = load_app_icon()
    app.setWindowIcon(app_icon)
    if not load_association_preferences()["disabled"]:
        try:
            register_openwith()  # keeps the command path current (Open with list); never touches UserChoice
        except OSError:
            pass
    window = PdfPreviewer()
    window.setWindowIcon(app_icon)
    startup_paths = [str(Path(arg).resolve()) for arg in sys.argv[1:] if arg.lower().endswith(".pdf") and Path(arg).is_file()]
    if startup_paths:
        window.append_pdf_files(startup_paths)
    window.show()
    if "--set-default-pdf" in sys.argv[1:]:  # installer finish page: start the default-app flow right away
        QTimer.singleShot(700, window.set_pdf_default_app)
    elif sys.platform == "win32" and not os.environ.get("KOINFRA_NO_DEFAULT_PROMPT"):
        QTimer.singleShot(900, window.maybe_ask_default_app)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

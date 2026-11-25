"""Koinfra PDF - shared constants, page model, user preferences and small PDF/image helpers (no widgets)."""
import os
import sys
import json
import zlib
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import pypdfium2 as pdfium
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage


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


PRINT_DPI = 200


PRINT_MAX_RENDER_SCALE = 3.0


# Rendered preview pages kept for scrolling/zoom back (least recently used dropped first).
# Thumbnails no longer go through this cache (they used to fill most of its slots), so fewer slots hold the same pages.
RENDER_CACHE_MAX_ITEMS = 12
RENDER_CACHE_MAX_BYTES = 128 * 1024 * 1024


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


class PdfSourceCache:
    """Recently used PDF documents and pages, kept open for the next render.

    Re-opening the file and re-parsing the page for every preview/thumbnail render cost about as much as the
    rendering itself. Documents are read into memory, so the file is never kept open (it can still be renamed,
    replaced or deleted in Explorer), and a file that changed on disk (size or modified time) is read again.
    pdfium keeps what it parsed for the whole life of a document, so a document is re-created from the same
    bytes after a number of page loads (memory stays flat while paging through a long PDF).
    Very large files are not kept; they are opened per use as before."""

    def __init__(self, max_documents=4, max_pages=3, max_file_bytes=96 * 1024 * 1024, max_total_bytes=256 * 1024 * 1024,
                 recycle_after_pages=12):
        self.max_documents = max_documents
        self.max_pages = max_pages
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.recycle_after_pages = recycle_after_pages
        self._documents = OrderedDict()  # (path, password) -> {"signature", "data", "doc", "loads"}
        self._pages = OrderedDict()  # (path, password, page index) -> (PdfDocument, PdfPage)

    @staticmethod
    def _signature(path):
        stat = os.stat(path)
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _close(obj):
        try:
            obj.close()
        except Exception:
            pass

    def _close_pages(self, key):
        for page_key in [k for k in self._pages if k[:2] == key]:
            self._close(self._pages.pop(page_key)[1])

    def _drop(self, key):
        self._close_pages(key)
        entry = self._documents.pop(key, None)
        if entry is not None:
            self._close(entry["doc"])

    def _cached_document(self, path, password):
        key = (path, password)
        signature = self._signature(path)
        entry = self._documents.get(key)
        if entry is not None and entry["signature"] != signature:
            self._drop(key)
            entry = None
        if entry is not None:
            self._documents.move_to_end(key)
            if entry["loads"] >= self.recycle_after_pages:  # fresh document from the same bytes: frees pdfium's caches
                self._close_pages(key)
                self._close(entry["doc"])
                entry["doc"] = pdfium.PdfDocument(entry["data"], password=password or None)
                entry["loads"] = 0
            return entry
        if signature[1] > self.max_file_bytes:
            return None
        with open(path, "rb") as source:
            data = source.read()
        entry = {"signature": signature, "data": data, "doc": pdfium.PdfDocument(data, password=password or None), "loads": 0}
        self._documents[key] = entry
        while len(self._documents) > 1 and (
            len(self._documents) > self.max_documents or sum(len(e["data"]) for e in self._documents.values()) > self.max_total_bytes
        ):
            self._drop(next(iter(self._documents)))
        return entry

    @contextmanager
    def document(self, path, password=""):
        path, password = str(path), password or ""
        entry = self._cached_document(path, password)
        if entry is not None:
            yield entry["doc"]
            return
        with pdfium.PdfDocument(path, password=password or None) as document:
            yield document

    @contextmanager
    def page(self, path, page_index, password=""):
        path, password = str(path), password or ""
        entry = self._cached_document(path, password)
        if entry is None:
            with pdfium.PdfDocument(path, password=password or None) as uncached:
                page = uncached[page_index]
                try:
                    yield page
                finally:
                    self._close(page)
            return
        document = entry["doc"]
        key = (path, password, page_index)
        cached = self._pages.get(key)
        if cached is not None and cached[0] is document:
            self._pages.move_to_end(key)
            yield cached[1]
            return
        if cached is not None:
            self._close(self._pages.pop(key)[1])
        page = document[page_index]
        entry["loads"] += 1
        self._pages[key] = (document, page)
        while len(self._pages) > max(1, self.max_pages):  # never the page about to be used
            self._close(self._pages.pop(next(iter(self._pages)))[1])
        yield page

    def invalidate(self, path=None):
        """Forget one file (any password) or everything."""
        target = None if path is None else os.path.normcase(os.path.abspath(str(path)))
        for key in list(self._documents):
            if target is None or os.path.normcase(os.path.abspath(key[0])) == target:
                self._drop(key)

    def stats(self):
        return {"documents": len(self._documents), "pages": len(self._pages), "bytes": sum(len(e["data"]) for e in self._documents.values())}


PDF_SOURCES = PdfSourceCache()


def pdf_page_sizes(path, password=""):
    """(width, height) in points of every page, read from the page tree without loading page content."""
    with PDF_SOURCES.document(path, password) as doc:
        return [tuple(doc.get_page_size(index)) for index in range(len(doc))]


def page_refs_for_pdf(path, password=""):
    with PDF_SOURCES.document(path, password) as doc:
        return [PageRef(path, index, password=password) for index in range(len(doc))]


def png_image_to_pdf_page_bytes(image_path, opacity=1.0):
    """Build a one-page PDF (page size = image px) that keeps PNG transparency via an /SMask.

    `image_path` may also be an already loaded PIL image (no temporary PNG needed)."""
    from PIL import Image

    if isinstance(image_path, Image.Image):
        image = image_path.convert("RGBA")
    else:
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
    from PIL import Image, ImageChops

    with Image.open(image_path) as source:
        image = source.convert("RGBA")
    red, green, blue, alpha = image.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    keep_mask = darkest.point(lambda value: 0 if value >= threshold else 255)
    image.putalpha(ImageChops.darker(alpha, keep_mask))
    image.save(image_path, "PNG")


def pdfium_page_to_qimage(page, scale, rotation=0):
    """Render straight into a QImage (RGB byte order from pdfium), skipping the PIL RGBA conversion."""
    try:
        bitmap = page.render(scale=scale, rotation=rotation, rev_byteorder=True)
        formats = {3: QImage.Format_RGB888, 4: QImage.Format_RGBA8888}
        image_format = formats.get(getattr(bitmap, "n_channels", 0))
        if image_format is not None:
            return QImage(bitmap.buffer, bitmap.width, bitmap.height, bitmap.stride, image_format).copy()
    except (TypeError, AttributeError):
        pass
    image = page.render(scale=scale, rotation=rotation).to_pil().convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    return QImage(data, image.width, image.height, image.width * 4, QImage.Format_RGBA8888).copy()

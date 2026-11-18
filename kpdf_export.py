"""Koinfra PDF - writing the edited page list to a PDF (pypdf, imported on first use) and printing."""
import io
import os
import tempfile
from pathlib import Path
import pypdfium2 as pdfium
from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QPainter
from kpdf_core import PRINT_MAX_RENDER_SCALE, normalize_approval_line, pdfium_page_to_qimage, png_image_to_pdf_page_bytes
from kpdf_graphics import render_approval_image


class PdfExportMixin:
    """Writing the edited page list to PDF (pypdf) and printing; mixed into PdfPreviewer."""

    def write_ordered_pdf(self, output_path, page_refs, password=None):
        from pypdf import PdfReader, PdfWriter

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
        from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject

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

        from PIL import Image
        from pypdf import PdfReader, Transformation

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
        # Built in memory: no temporary PNG/PDF files.
        stamp_page = PdfReader(io.BytesIO(png_image_to_pdf_page_bytes(image, opacity))).pages[0]
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
                    qimage = pdfium_page_to_qimage(page, render_scale)
                    draw_width = page_width * target_scale
                    draw_height = page_height * target_scale
                    x = page_rect.x() + max(0, (page_rect.width() - draw_width) / 2)
                    y = page_rect.y() + max(0, (page_rect.height() - draw_height) / 2)
                    target_rect = QRectF(float(x), float(y), float(draw_width), float(draw_height))
                    painter.drawImage(target_rect, qimage)
        finally:
            painter.end()

    def printer_page_rect(self, printer):
        from PyQt5.QtPrintSupport import QPrinter

        try:
            return printer.pageRect(QPrinter.DevicePixel)
        except TypeError:
            return printer.pageRect()

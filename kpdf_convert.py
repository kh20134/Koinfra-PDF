"""PDF -> Excel (.xlsx) / 한글 (.hwpx, .hwp) / image (PNG/JPG) conversion. No Qt here (the UI is ConvertDialog).

- Excel: tables found by PyMuPDF (find_tables) become one sheet each, merged cells kept; text outside tables
  (or pages without tables) goes to a text sheet, one row per line, split into cells at wide gaps.
- 한글: .hwpx (OWPML) is written directly, so no Hancom Office is needed; text lines become paragraphs and tables
  become real HWP tables (borders, merged cells, header row). .hwp is made from that .hwpx by Hancom Office
  (COM automation) when it is installed.
- Images: one file per page rendered by pdfium at the chosen DPI.
Scanned pages (no text layer) carry no text/tables: they are reported so the UI can suggest image conversion.
"""
import base64
import datetime
import json
import os
import re
import tempfile
import zipfile
import zlib
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

FORMAT_XLSX = "xlsx"
FORMAT_HWPX = "hwpx"
FORMAT_HWP = "hwp"
FORMAT_PNG = "png"
FORMAT_JPG = "jpg"
FORMAT_LABELS = {
    FORMAT_XLSX: "엑셀 (.xlsx)",
    FORMAT_HWPX: "한글 (.hwpx)",
    FORMAT_HWP: "한글 (.hwp)",
    FORMAT_PNG: "이미지 (PNG)",
    FORMAT_JPG: "이미지 (JPG)",
}
IMAGE_DPI_CHOICES = (72, 96, 150, 200, 300, 600)
DEFAULT_IMAGE_DPI = 150
MAX_IMAGE_PIXELS = 20000 * 20000  # refuse absurd renders (e.g. 600 DPI on an A0 drawing) instead of running out of memory


class ConversionCancelled(Exception):
    pass


class ScannedPdfError(Exception):
    """None of the selected pages has a text layer (scanned PDF): nothing to put in Excel/한글."""


def _open_fitz(pdf_path, password=None):
    try:
        import pymupdf as fitz
    except ImportError:  # older PyMuPDF
        import fitz
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise ValueError(unreadable_pdf_message(pdf_path)) from exc
    if doc.needs_pass and not doc.authenticate(password or ""):
        doc.close()
        raise ValueError("PDF 암호가 맞지 않습니다.")
    return doc


def unreadable_pdf_message(pdf_path):
    try:
        with open(pdf_path, "rb") as handle:
            head = handle.read(64)
    except OSError:
        return f"파일을 읽을 수 없습니다: {pdf_path}"
    if not head.lstrip().startswith(b"%PDF") and b"%PDF" not in head:
        return "PDF로 열 수 없는 파일입니다. 문서보안(DRM)이 걸린 파일이면 보안을 해제한 뒤 다시 시도하세요."
    return "PDF를 열 수 없습니다 (손상된 파일일 수 있습니다)."


def pdf_page_count(pdf_path, password=None):
    doc = _open_fitz(pdf_path, password)
    try:
        return doc.page_count
    finally:
        doc.close()


def _check(cancel, progress, done, total, message):
    if cancel is not None and cancel():
        raise ConversionCancelled()
    if progress is not None:
        progress(done, total, message)


_CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def clean_text(text):
    return _CONTROL_CHARS.sub("", (text or "").replace("\t", " ").replace("\r", ""))


def page_has_text(page):
    return bool(page.get_text("text").strip())


def text_layer_report(pdf_path, pages=None, password=None):
    """(pages with text, pages without) for the given 0-based page indexes (default: all)."""
    doc = _open_fitz(pdf_path, password)
    try:
        indexes = list(range(doc.page_count)) if pages is None else list(pages)
        with_text = [i for i in indexes if page_has_text(doc[i])]
        return with_text, [i for i in indexes if i not in with_text]
    finally:
        doc.close()


# ---------------------------------------------------------------- page analysis (shared by Excel and 한글)
class TableGrid:
    """A detected table as a grid: cells[(row, col)] = (text, row_span, col_span); covered cells are absent."""

    def __init__(self, bbox, row_count, col_count, cells, col_widths, row_heights):
        self.bbox = bbox
        self.row_count = row_count
        self.col_count = col_count
        self.cells = cells
        self.col_widths = col_widths  # PDF points
        self.row_heights = row_heights

    def rows_as_lists(self):
        rows = [["" for _ in range(self.col_count)] for _ in range(self.row_count)]
        for (r, c), (text, _rs, _cs) in self.cells.items():
            rows[r][c] = text
        return rows


def _boundaries(starts, end, count):
    """Fill missing column/row starts (None) by interpolation so spans can always be computed."""
    values = list(starts)
    known = [(i, v) for i, v in enumerate(values) if v is not None]
    if not known:
        return values
    for i in range(count):
        if values[i] is None:
            prev = next(((j, v) for j, v in reversed(known) if j < i), None)
            nxt = next(((j, v) for j, v in known if j > i), None)
            if prev and nxt:
                values[i] = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / (nxt[0] - prev[0])
            elif prev:
                values[i] = prev[1] + (end - prev[1]) * (i - prev[0]) / max(1, count - prev[0])
            else:
                values[i] = nxt[1]
    return values


def table_grid_from_fitz(table, page=None):
    data = table.extract()
    row_count = len(data)
    col_count = max((len(r) for r in data), default=0)
    x0, y0, x1, y1 = table.bbox
    tol = 1.5
    col_starts = [None] * col_count
    row_starts = [None] * row_count
    rows = list(getattr(table, "rows", []) or [])
    for r, row in enumerate(rows[:row_count]):
        row_starts[r] = row.bbox[1] if getattr(row, "bbox", None) else None
        for c, cell in enumerate(list(row.cells)[:col_count]):
            if cell is not None and (col_starts[c] is None or cell[0] < col_starts[c]):
                col_starts[c] = cell[0]
    col_starts = _boundaries(col_starts, x1, col_count)
    row_starts = _boundaries(row_starts, y1, row_count)
    cells = {}
    for r in range(row_count):
        row_cells = list(rows[r].cells) if r < len(rows) else [None] * col_count
        for c in range(col_count):
            value = data[r][c] if c < len(data[r]) else None
            bbox = row_cells[c] if c < len(row_cells) else None
            if value is None and bbox is None:
                continue  # covered by a merged cell
            col_span = row_span = 1
            if bbox is not None and None not in col_starts and None not in row_starts:
                col_span = max(1, sum(1 for j in range(c, col_count) if col_starts[j] < bbox[2] - tol))
                row_span = max(1, sum(1 for i in range(r, row_count) if row_starts[i] < bbox[3] - tol))
            cells[(r, c)] = (clean_text(value or "").strip(), row_span, col_span)
    # a cell may not claim cells that another real cell occupies: clip spans
    occupied = set(cells)
    for (r, c), (text, rs, cs) in list(cells.items()):
        while cs > 1 and any((rr, c + cs - 1) in occupied for rr in range(r, r + rs) if (rr, c + cs - 1) != (r, c)):
            cs -= 1
        while rs > 1 and any((r + rs - 1, cc) in occupied for cc in range(c, c + cs) if (r + rs - 1, cc) != (r, c)):
            rs -= 1
        cells[(r, c)] = (text, rs, cs)
    covered = set()
    for (r, c), (_t, rs, cs) in sorted(cells.items()):
        if (r, c) in covered:
            continue
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                if (rr, cc) != (r, c):
                    covered.add((rr, cc))
    cells = {k: v for k, v in cells.items() if k not in covered}
    edges_x = [v if v is not None else x0 for v in col_starts] + [x1]
    edges_y = [v if v is not None else y0 for v in row_starts] + [y1]
    _fill_grid_gaps(cells, row_count, col_count, edges_x, edges_y, page)
    col_widths = [max(4.0, edges_x[i + 1] - edges_x[i]) for i in range(col_count)]
    row_heights = [max(4.0, edges_y[i + 1] - edges_y[i]) for i in range(row_count)]
    return TableGrid(tuple(table.bbox), row_count, col_count, cells, col_widths, row_heights)


def _fill_grid_gaps(cells, row_count, col_count, edges_x, edges_y, page=None):
    """Every grid position must belong to exactly one cell (Hancom Office rejects tables with holes).
    Holes are cells the table finder could not outline (often a tall merged cell): each vertical run of holes
    in a column (joined with identical runs to its right) becomes one cell holding the page text in that area."""
    owner = set()
    for (r, c), (_t, rs, cs) in cells.items():
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                owner.add((rr, cc))
    runs = []
    for c in range(col_count):
        r = 0
        while r < row_count:
            if (r, c) in owner:
                r += 1
                continue
            start = r
            while r < row_count and (r, c) not in owner:
                r += 1
            runs.append([start, r - start, c, 1])
    merged = []
    for run in sorted(runs, key=lambda item: (item[0], item[2])):
        prev = merged[-1] if merged else None
        if prev and prev[0] == run[0] and prev[1] == run[1] and prev[2] + prev[3] == run[2]:
            prev[3] += 1
        else:
            merged.append(run)
    for r, rs, c, cs in merged:
        text = ""
        if page is not None:
            try:
                rect = (edges_x[c], edges_y[r], edges_x[c + cs], edges_y[r + rs])
                text = clean_text(page.get_textbox(rect)).strip()
            except Exception:
                text = ""
        cells[(r, c)] = (text, rs, cs)


def _inside(rect, box, margin=1.0):
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return box[0] - margin <= cx <= box[2] + margin and box[1] - margin <= cy <= box[3] + margin


def analyse_page(page, find_tables=True):
    """Items in reading order: ("table", y, TableGrid) and ("line", y, segments, size, bold)
    where segments are the line's text pieces split at wide horizontal gaps."""
    tables = []
    if find_tables:
        try:
            found = page.find_tables()
            for table in found.tables:
                grid = table_grid_from_fitz(table, page)
                if grid.row_count and grid.col_count and any(t for t, _rs, _cs in grid.cells.values()):
                    tables.append(grid)
        except Exception:
            tables = []
    items = [("table", grid.bbox[1], grid) for grid in tables]
    # Lines from different text blocks that sit on the same baseline (columns, "label    value") form one row.
    rows = []
    text = page.get_text("dict", sort=True)
    for block in text.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if clean_text(s.get("text", "")).strip()]
            if not spans:
                continue
            bbox = line.get("bbox")
            if any(_inside(bbox, grid.bbox) for grid in tables):
                continue
            if (line.get("dir") or (1, 0))[0] < 0.9:  # vertical/rotated text: own row
                rows.append([list(bbox), spans])
                continue
            for row in rows:
                top, bottom = max(row[0][1], bbox[1]), min(row[0][3], bbox[3])
                if bottom - top > 0.5 * min(row[0][3] - row[0][1], bbox[3] - bbox[1]):
                    row[0] = [min(row[0][0], bbox[0]), min(row[0][1], bbox[1]), max(row[0][2], bbox[2]), max(row[0][3], bbox[3])]
                    row[1].extend(spans)
                    break
            else:
                rows.append([list(bbox), list(spans)])
    for bbox, spans in rows:
        spans.sort(key=lambda span: span["bbox"][0])
        segments, current, last_x1 = [], "", None
        for span in spans:
            size = span.get("size", 10) or 10
            if last_x1 is not None and span["bbox"][0] - last_x1 > size * 2.2:
                segments.append(current)
                current = ""
            elif last_x1 is not None and span["bbox"][0] - last_x1 > size * 0.25 and not current.endswith(" ") and not span["text"].startswith(" "):
                current += " "
            current += clean_text(span["text"])
            last_x1 = span["bbox"][2]
        segments.append(current)
        pieces = []
        for segment in segments:  # runs of 3+ spaces inside one text object are column gaps too
            pieces.extend(part.strip() for part in re.split(r" {3,}", segment))
        chars = sum(len(s["text"]) for s in spans) or 1
        size = sum((s.get("size", 10) or 10) * len(s["text"]) for s in spans) / chars
        bold = sum(len(s["text"]) for s in spans if (s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower()) * 2 > chars
        items.append(("line", bbox[1], [piece for piece in pieces if piece], size, bold))
    items.sort(key=lambda item: item[1])
    return items


def _body_size(items):
    sizes = sorted(item[3] for item in items if item[0] == "line")
    return sizes[len(sizes) // 2] if sizes else 10.0


# ---------------------------------------------------------------- Excel
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
_NUMBER = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|[1-9]\d{0,14}|0)(?:\.\d+)?$")


def _sheet_title(workbook, title):
    title = _SHEET_BAD.sub("_", title)[:31] or "Sheet"
    base, n = title, 2
    while title in workbook.sheetnames:
        suffix = f"({n})"
        title = base[: 31 - len(suffix)] + suffix
        n += 1
    return title


def _cell_value(text):
    """Numbers stay numbers in Excel (1,234 / 12.5); codes such as 007 or phone numbers stay text."""
    if text and _NUMBER.match(text):
        try:
            value = float(text.replace(",", ""))
            return int(value) if value.is_integer() and "." not in text else value
        except ValueError:
            pass
    return text


def _display_width(text):
    return max((sum(2 if ord(ch) > 0x2E80 else 1 for ch in line) for line in str(text).split("\n")), default=0)


def _fit_columns(sheet, widths):
    from openpyxl.utils import get_column_letter
    for index, width in widths.items():
        sheet.column_dimensions[get_column_letter(index)].width = max(6, min(60, width + 2))


def _write_table_sheet(workbook, title, grid):
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    sheet = workbook.create_sheet(_sheet_title(workbook, title))
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="EDEFF3")
    widths = {}
    for (r, c), (text, rs, cs) in sorted(grid.cells.items()):
        cell = sheet.cell(row=r + 1, column=c + 1, value=_cell_value(text))
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        if r == 0 and grid.row_count > 1:
            cell.font = Font(bold=True)
            cell.fill = header_fill
        if rs > 1 or cs > 1:
            sheet.merge_cells(start_row=r + 1, start_column=c + 1, end_row=r + rs, end_column=c + cs)
        if cs == 1:
            widths[c + 1] = max(widths.get(c + 1, 0), _display_width(text))
    for r in range(grid.row_count):
        for c in range(grid.col_count):
            sheet.cell(row=r + 1, column=c + 1).border = border
    _fit_columns(sheet, widths)
    return sheet


def _write_lines_sheet(workbook, title, lines, note=None):
    from openpyxl.styles import Alignment, Font
    sheet = workbook.create_sheet(_sheet_title(workbook, title))
    widths = {}
    row = 1
    if note:
        sheet.cell(row=1, column=1, value=note).font = Font(italic=True, color="888888")
        row = 2
    for segments, bold in lines:
        for c, text in enumerate(segments, start=1):
            cell = sheet.cell(row=row, column=c, value=_cell_value(text))
            if bold:
                cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="top")
            widths[c] = max(widths.get(c, 0), _display_width(text))
        row += 1
    _fit_columns(sheet, widths)
    return sheet


def convert_to_xlsx(pdf_path, output_path, pages=None, password=None, progress=None, cancel=None):
    """One sheet per table ("p1_표1"), plus "p1_텍스트" for text outside tables or pages without tables."""
    from openpyxl import Workbook
    doc = _open_fitz(pdf_path, password)
    stats = {"pages": 0, "tables": 0, "sheets": 0, "scanned_pages": []}
    try:
        indexes = list(range(doc.page_count)) if pages is None else list(pages)
        workbook = Workbook()
        workbook.remove(workbook.active)
        for n, index in enumerate(indexes):
            _check(cancel, progress, n, len(indexes), f"{index + 1}쪽 표·텍스트 추출 중")
            page = doc[index]
            stats["pages"] += 1
            if not page_has_text(page):
                stats["scanned_pages"].append(index)
                _write_lines_sheet(workbook, f"p{index + 1}", [], note="이 페이지에는 텍스트가 없습니다 (스캔 이미지).")
                continue
            items = analyse_page(page)
            table_no = 0
            lines = []
            for item in items:
                if item[0] == "table":
                    table_no += 1
                    _write_table_sheet(workbook, f"p{index + 1}_표{table_no}", item[2])
                else:
                    lines.append((item[2], item[4]))
            stats["tables"] += table_no
            if lines or not table_no:
                _write_lines_sheet(workbook, f"p{index + 1}_텍스트" if table_no else f"p{index + 1}", lines)
        if not workbook.sheetnames:
            workbook.create_sheet("Sheet1")
        stats["sheets"] = len(workbook.sheetnames)
        _check(cancel, progress, len(indexes), len(indexes), "엑셀 파일 저장 중")
        _save_atomically(output_path, workbook.save)
    finally:
        doc.close()
    return stats


def _save_atomically(output_path, writer):
    """Write to a temp file next to the target, then replace: an open/locked target or an error never leaves a
    half-written file behind."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(suffix=output_path.suffix, dir=str(output_path.parent))
    os.close(fd)
    try:
        writer(temp_path)
        os.replace(temp_path, output_path)
    except PermissionError:
        _remove_quietly(temp_path)
        raise PermissionError(f"파일에 쓸 수 없습니다. 다른 프로그램(엑셀·한글 등)에서 열려 있으면 닫고 다시 시도하세요.\n{output_path}")
    except BaseException:
        _remove_quietly(temp_path)
        raise


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- 한글 (.hwpx)
# Header/section scaffolding from a blank Hancom Office 2020 document (fonts 함초롬, A4, styles), extended with
# borderFill 4 (table header shading) and charPr 7 bold / 8 title (14pt bold) / 9 heading (12pt bold).
_HWPX_TEMPLATE = (
    "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"
)
_HWPX = None
HWP_TEXT_WIDTH = 51024  # A4 minus 15 mm margins, HWPUNIT (1/7200 inch; 1 pt = 100)
CHAR_NORMAL, CHAR_BOLD, CHAR_TITLE, CHAR_HEADING = 0, 7, 8, 9
BORDER_CELL, BORDER_HEADER = 3, 4


def _hwpx_template():
    global _HWPX
    if _HWPX is None:
        _HWPX = json.loads(zlib.decompress(base64.b64decode("".join(_HWPX_TEMPLATE))).decode("utf-8"))
    return _HWPX


def _x(text):
    return _xml_escape(clean_text(text), {'"': "&quot;"})


class HwpxBuilder:
    def __init__(self):
        self.parts = []
        self.table_id = 1000
        self.first = True
        self.pending_page_break = False
        self.plain_text = []

    def _p_open(self, para_pr=0):
        page_break = "1" if self.pending_page_break else "0"
        self.pending_page_break = False
        head = f'<hp:p id="0" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="{page_break}" columnBreak="0" merged="0">'
        if self.first:
            self.first = False
            head += _hwpx_template()["secpr"]
        return head

    def page_break(self):
        if not self.first:
            self.pending_page_break = True

    def paragraph(self, text, char_pr=CHAR_NORMAL):
        run = f'<hp:run charPrIDRef="{char_pr}"><hp:t>{_x(text)}</hp:t></hp:run>' if text else f'<hp:run charPrIDRef="{char_pr}"/>'
        self.parts.append(self._p_open() + run + "</hp:p>")
        self.plain_text.append(text)

    def table(self, grid):
        self.table_id += 1
        scale = min(1.0, HWP_TEXT_WIDTH / max(1.0, sum(grid.col_widths) * 100))
        col_w = [max(600, int(w * 100 * scale)) for w in grid.col_widths]
        overflow = sum(col_w) - HWP_TEXT_WIDTH
        if overflow > 0:  # minimum widths pushed it over: take it back from the widest columns
            for i in sorted(range(len(col_w)), key=lambda k: -col_w[k]):
                take = min(overflow, col_w[i] - 600)
                col_w[i] -= take
                overflow -= take
                if overflow <= 0:
                    break
        row_h = [max(800, int(h * 100 * scale)) for h in grid.row_heights]
        header = grid.row_count > 1
        rows_xml = []
        for r in range(grid.row_count):
            cells_xml = []
            for c in range(grid.col_count):
                if (r, c) not in grid.cells:
                    continue
                text, rs, cs = grid.cells[(r, c)]
                is_header = header and r == 0
                char_pr = CHAR_BOLD if is_header else CHAR_NORMAL
                paras = "".join(
                    f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                    + (f'<hp:run charPrIDRef="{char_pr}"><hp:t>{_x(line)}</hp:t></hp:run>' if line else f'<hp:run charPrIDRef="{char_pr}"/>')
                    + "</hp:p>"
                    for line in (text.split("\n") if text else [""])
                )
                width = sum(col_w[c:c + cs])
                height = sum(row_h[r:r + rs])
                cells_xml.append(
                    f'<hp:tc name="" header="{1 if is_header else 0}" hasMargin="0" protect="0" editable="0" dirty="0" '
                    f'borderFillIDRef="{BORDER_HEADER if is_header else BORDER_CELL}">'
                    '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" '
                    'linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                    f"{paras}</hp:subList>"
                    f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/>'
                    f'<hp:cellSz width="{width}" height="{height}"/>'
                    '<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>'
                )
                self.plain_text.append(text)
            rows_xml.append("<hp:tr>" + "".join(cells_xml) + "</hp:tr>")
        tbl = (
            f'<hp:tbl id="{self.table_id}" zOrder="{self.table_id - 1000}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
            'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
            f'rowCnt="{grid.row_count}" colCnt="{grid.col_count}" cellSpacing="0" borderFillIDRef="{BORDER_CELL}" noAdjust="0">'
            f'<hp:sz width="{sum(col_w)}" widthRelTo="ABSOLUTE" height="{sum(row_h)}" heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
            'horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
            '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
            + "".join(rows_xml) + "</hp:tbl>"
        )
        self.parts.append(self._p_open() + f'<hp:run charPrIDRef="0">{tbl}<hp:t/></hp:run></hp:p>')

    def save(self, output_path, title=""):
        if self.first:
            self.paragraph("")
        t = _hwpx_template()
        section = t["sec_open"] + "".join(self.parts) + "</hs:sec>"
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        content = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<opf:package xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/pa'
            'ragraph" xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/201'
            '1/section" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" xmlns:hh="http://www.hancom.co.kr/hwpml/2011/hea'
            'd" xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-'
            'page" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:op'
            'f="http://www.idpf.org/2007/opf/" xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" xmlns:hwpun'
            'itchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:config'
            '="urn:oasis:names:tc:opendocument:xmlns:config:1.0" version="" unique-identifier="" id="">'
            f'<opf:metadata><opf:title>{_x(title)}</opf:title><opf:language>ko</opf:language>'
            '<opf:meta name="creator" content="text">Koinfra PDF</opf:meta>'
            '<opf:meta name="lastsaveby" content="text">Koinfra PDF</opf:meta>'
            f'<opf:meta name="CreatedDate" content="text">{now}</opf:meta>'
            f'<opf:meta name="ModifiedDate" content="text">{now}</opf:meta></opf:metadata>'
            '<opf:manifest><opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
            '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
            '<opf:item id="settings" href="settings.xml" media-type="application/xml"/></opf:manifest>'
            '<opf:spine><opf:itemref idref="header"/><opf:itemref idref="section0"/></opf:spine></opf:package>'
        )
        settings = re.sub(r'paraIDRef="\d+" pos="\d+"', 'paraIDRef="0" pos="0"', t["settings"])
        preview = "\r\n".join(line for line in self.plain_text if line)[:1024]

        def write(path):
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
                archive.writestr("version.xml", t["version"])
                archive.writestr("Contents/header.xml", t["header"])
                archive.writestr("Contents/section0.xml", section)
                archive.writestr("Preview/PrvText.txt", preview)
                archive.writestr("settings.xml", settings)
                archive.writestr("META-INF/container.rdf", t["rdf"])
                archive.writestr("Contents/content.hpf", content)
                archive.writestr("META-INF/container.xml", t["container"])
                archive.writestr("META-INF/manifest.xml", t["manifest"])

        _save_atomically(output_path, write)


def convert_to_hwpx(pdf_path, output_path, pages=None, password=None, progress=None, cancel=None, title=None):
    """Text lines -> paragraphs (larger text -> bold title/heading), tables -> HWP tables; each PDF page starts a new page."""
    doc = _open_fitz(pdf_path, password)
    stats = {"pages": 0, "tables": 0, "paragraphs": 0, "scanned_pages": []}
    builder = HwpxBuilder()
    try:
        indexes = list(range(doc.page_count)) if pages is None else list(pages)
        for n, index in enumerate(indexes):
            _check(cancel, progress, n, len(indexes), f"{index + 1}쪽 한글 문서로 옮기는 중")
            page = doc[index]
            stats["pages"] += 1
            builder.page_break()
            if not page_has_text(page):
                stats["scanned_pages"].append(index)
                builder.paragraph(f"[{index + 1}쪽: 텍스트가 없는 페이지(스캔 이미지)입니다]", CHAR_BOLD)
                continue
            items = analyse_page(page)
            body = _body_size(items)
            for item in items:
                if item[0] == "table":
                    builder.table(item[2])
                    stats["tables"] += 1
                    continue
                _kind, _y, segments, size, bold = item
                text = "    ".join(segments)
                if size >= body * 1.45:
                    char_pr = CHAR_TITLE
                elif size >= body * 1.15:
                    char_pr = CHAR_HEADING
                else:
                    char_pr = CHAR_BOLD if bold else CHAR_NORMAL
                builder.paragraph(text, char_pr)
                stats["paragraphs"] += 1
        _check(cancel, progress, len(indexes), len(indexes), "한글 파일 저장 중")
        builder.save(output_path, title or Path(pdf_path).stem)
    finally:
        doc.close()
    return stats


# ---------------------------------------------------------------- 한글 (.hwp) via Hancom Office
def hancom_installed():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"HWPFrame.HwpObject\CLSID"):
            return True
    except Exception:
        return False


def _hancom_security_module():
    """Name of a registered Hancom file-path security module (lets automation open/save without a prompt)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\HNC\HwpAutomation\Modules") as key:
            name, _value, _type = winreg.EnumValue(key, 0)
            return name
    except Exception:
        return None


def hwpx_to_hwp(hwpx_path, hwp_path):
    """Open the .hwpx in Hancom Office (hidden) and save it as .hwp. Raises if Hancom Office is missing.
    Saved next to the .hwpx first: Hancom keeps the saved file open until the document is closed."""
    import pythoncom
    import win32com.client
    temp_hwp = Path(hwpx_path).with_suffix(".hwp")
    pythoncom.CoInitialize()
    hwp = None
    try:
        hwp = win32com.client.DispatchEx("HWPFrame.HwpObject")
        module = _hancom_security_module()
        if module:
            hwp.RegisterModule("FilePathCheckDLL", module)
        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:
            pass
        if not hwp.Open(str(hwpx_path), "HWPX", "forceopen:true"):
            raise RuntimeError("한글에서 변환한 .hwpx 파일을 열지 못했습니다.")
        if not hwp.SaveAs(str(temp_hwp), "HWP", ""):
            raise RuntimeError("한글에서 .hwp로 저장하지 못했습니다.")
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)
                hwp.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
    data = temp_hwp.read_bytes()
    _save_atomically(hwp_path, lambda path: Path(path).write_bytes(data))


def convert_to_hwp(pdf_path, output_path, pages=None, password=None, progress=None, cancel=None):
    temp_dir = tempfile.mkdtemp(prefix="koinfra_hwp_")
    try:
        temp_hwpx = Path(temp_dir) / (Path(output_path).stem + ".hwpx")
        stats = convert_to_hwpx(pdf_path, temp_hwpx, pages, password, progress, cancel, title=Path(output_path).stem)
        if progress is not None:
            progress(1, 1, "한글 프로그램으로 .hwp 저장 중")
        hwpx_to_hwp(temp_hwpx, output_path)
        return stats
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------- images
def convert_to_images(pdf_path, output_dir, pages=None, password=None, dpi=DEFAULT_IMAGE_DPI, image_format=FORMAT_PNG,
                      progress=None, cancel=None, base_name=None, jpg_quality=90):
    """One file per page: <base>_p001.png ... Returns stats with the written files."""
    import pypdfium2 as pdfium
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = base_name or Path(pdf_path).stem
    ext = "jpg" if image_format == FORMAT_JPG else "png"
    scale = max(10, min(1200, int(dpi))) / 72.0
    try:
        pdf = pdfium.PdfDocument(str(pdf_path), password=password or None)
    except Exception as exc:
        if "password" in str(exc).lower():
            raise ValueError("PDF 암호가 맞지 않습니다.") from exc
        raise ValueError(unreadable_pdf_message(pdf_path)) from exc
    stats = {"pages": 0, "files": [], "dpi": int(dpi)}
    try:
        indexes = list(range(len(pdf))) if pages is None else list(pages)
        digits = max(3, len(str(max(indexes) + 1))) if indexes else 3
        for n, index in enumerate(indexes):
            _check(cancel, progress, n, len(indexes), f"{index + 1}쪽 이미지 저장 중")
            page = pdf[index]
            try:
                width, height = page.get_size()
                if width * scale * height * scale > MAX_IMAGE_PIXELS:
                    raise ValueError(f"{index + 1}쪽이 너무 커서 {int(dpi)} DPI로 저장할 수 없습니다. DPI를 낮춰 주세요.")
                image = page.render(scale=scale, may_draw_forms=True).to_pil()
            finally:
                page.close()
            image = image.convert("RGB")
            target = output_dir / f"{base_name}_p{index + 1:0{digits}d}.{ext}"
            if ext == "jpg":
                _save_atomically(target, lambda p, im=image: im.save(p, "JPEG", quality=jpg_quality, dpi=(dpi, dpi), optimize=True))
            else:
                _save_atomically(target, lambda p, im=image: im.save(p, "PNG", dpi=(dpi, dpi), optimize=False))
            stats["files"].append(str(target))
            stats["pages"] += 1
        _check(cancel, progress, len(indexes), len(indexes), "완료")
    finally:
        pdf.close()
    return stats


# ---------------------------------------------------------------- one entry point for the UI
def default_output_path(pdf_path, output_dir, fmt):
    stem = Path(pdf_path).stem if pdf_path else "변환"
    if fmt in (FORMAT_PNG, FORMAT_JPG):
        return Path(output_dir) / f"{stem}_이미지"
    return Path(output_dir) / f"{stem}.{fmt}"


def unique_path(path):
    """file.xlsx -> file (2).xlsx ... so a conversion never overwrites an existing result silently."""
    path = Path(path)
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def convert_pdf(pdf_path, fmt, output, pages=None, password=None, dpi=DEFAULT_IMAGE_DPI, progress=None, cancel=None,
                base_name=None):
    """Convert and return stats. Text formats raise ScannedPdfError if no selected page has a text layer."""
    if fmt in (FORMAT_XLSX, FORMAT_HWPX, FORMAT_HWP):
        with_text, _without = text_layer_report(pdf_path, pages, password)
        if not with_text:
            raise ScannedPdfError(
                "선택한 페이지에 글자 정보가 없습니다 (스캔한 PDF로 보입니다).\n"
                "엑셀·한글로 옮길 텍스트·표가 없어요. '이미지'로 변환하거나, OCR(글자 인식)을 거친 PDF로 다시 시도하세요."
            )
    if fmt == FORMAT_XLSX:
        stats = convert_to_xlsx(pdf_path, output, pages, password, progress, cancel)
    elif fmt == FORMAT_HWPX:
        stats = convert_to_hwpx(pdf_path, output, pages, password, progress, cancel, title=Path(output).stem)
    elif fmt == FORMAT_HWP:
        stats = convert_to_hwp(pdf_path, output, pages, password, progress, cancel)
    elif fmt in (FORMAT_PNG, FORMAT_JPG):
        stats = convert_to_images(pdf_path, output, pages, password, dpi, fmt, progress, cancel, base_name=base_name)
    else:
        raise ValueError(f"지원하지 않는 형식: {fmt}")
    stats["format"] = fmt
    stats["output"] = str(output)
    return stats

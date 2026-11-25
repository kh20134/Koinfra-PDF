"""'변환' window: the open PDF (or another file) -> 엑셀 / 한글 / 이미지, with page range, output folder and progress."""
import os
import subprocess
import tempfile
from pathlib import Path

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)

import kpdf_convert as conv
from kpdf_core import PDF_FILTER, _read_preferences_payload, _write_preferences_payload, parse_page_ranges
from kpdf_graphics import make_tool_icon

KIND_EXCEL, KIND_HWP, KIND_IMAGE = "excel", "hwp", "image"
KIND_LABELS = ((KIND_EXCEL, "엑셀", "conv_excel"), (KIND_HWP, "한글", "conv_hwp"), (KIND_IMAGE, "이미지", "conv_image"))
PREF_KEY = "convert"


def load_convert_preferences():
    value = _read_preferences_payload().get(PREF_KEY)
    return value if isinstance(value, dict) else {}


def save_convert_preferences(**changes):
    payload = _read_preferences_payload()
    current = payload.get(PREF_KEY) if isinstance(payload.get(PREF_KEY), dict) else {}
    current.update(changes)
    payload[PREF_KEY] = current
    try:
        _write_preferences_payload(payload)
    except OSError:
        pass


def open_folder(path, select_file=None):
    """Explorer at the result (the file selected when there is one)."""
    try:
        if select_file and os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(select_file)])
        elif os.name == "nt":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


class ConvertWorker(QThread):
    progressed = pyqtSignal(int, int, str)
    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.cancel_requested = False

    def run(self):
        try:
            stats = conv.convert_pdf(
                progress=lambda done, total, message: self.progressed.emit(done, total, message),
                cancel=lambda: self.cancel_requested,
                **self.job,
            )
            self.succeeded.emit(stats)
        except conv.ConversionCancelled:
            self.cancelled.emit()
        except Exception as exc:  # shown in the dialog; the app keeps running
            self.failed.emit(str(exc) or type(exc).__name__)


class ConvertDialog(QDialog):
    def __init__(self, owner, initial_kind=KIND_EXCEL):
        super().__init__(owner)
        self.owner = owner
        self.worker = None
        self.temp_dir = None
        self.source = None  # dict(path, password, name, page_count, current_index, from_document)
        self.last_stats = None
        self.last_error = None
        self.scanned_all = False
        self.hancom = conv.hancom_installed()
        self.prefs = load_convert_preferences()
        self.setWindowTitle("PDF 변환")
        self.setObjectName("convertDialog")
        self.setMinimumWidth(560)
        self.setStyleSheet(self.dialog_stylesheet())
        self.build_ui()
        self.use_document_source()
        self.select_kind(initial_kind if initial_kind in self.kind_buttons else KIND_EXCEL)

    # ---------------------------------------------------------------- UI
    @staticmethod
    def dialog_stylesheet():
        return """
            QWidget { font-family: "Malgun Gothic", "Segoe UI", sans-serif; font-size: 12px; color: #172033; }
            QDialog#convertDialog { background: #ffffff; }
            QLabel#convertTitle { font-size: 16px; font-weight: 800; color: #0f172a; }
            QLabel#convertHint, QLabel#outputPreview { color: #64748b; font-size: 11px; }
            QLabel#scanWarning { color: #b45309; background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 6px 8px; }
            QLabel#sectionLabel { color: #334155; font-weight: 800; }
            QFrame#sourceBox { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; }
            QPushButton#kindButton { min-width: 96px; padding: 8px 12px; border-radius: 8px; font-size: 13px; text-align: center; }
            QPushButton#kindButton:checked { background: #eaf2ff; border: 1px solid #155eef; color: #155eef; }
            QPushButton#convertButton { background: #155eef; border: 1px solid #155eef; color: #ffffff; min-width: 96px; }
            QPushButton#convertButton:hover { background: #0f4fd1; }
            QPushButton#convertButton:disabled { background: #94a3b8; border-color: #94a3b8; }
            QProgressBar { border: 1px solid #cbd5e1; border-radius: 5px; background: #f1f5f9; height: 14px; text-align: center; }
            QProgressBar::chunk { background: #155eef; border-radius: 4px; }
            QLineEdit, QComboBox { padding: 5px 7px; border: 1px solid #cbd5e1; border-radius: 5px; background: #ffffff; }
        """

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        title = QLabel("PDF 변환")
        title.setObjectName("convertTitle")
        hint = QLabel("열려 있는 PDF를 엑셀·한글·이미지 파일로 저장합니다.")
        hint.setObjectName("convertHint")
        layout.addWidget(title)
        layout.addWidget(hint)

        source_box = QFrame()
        source_box.setObjectName("sourceBox")
        source_row = QHBoxLayout(source_box)
        source_row.setContentsMargins(10, 8, 10, 8)
        self.source_label = QLabel("-")
        self.source_label.setWordWrap(True)
        self.document_source_button = QPushButton("열린 문서")
        self.document_source_button.clicked.connect(self.use_document_source)
        self.file_source_button = QPushButton("다른 PDF…")
        self.file_source_button.clicked.connect(self.choose_source_file)
        source_row.addWidget(self.source_label, 1)
        source_row.addWidget(self.document_source_button)
        source_row.addWidget(self.file_source_button)
        layout.addWidget(source_box)
        self.scan_warning = QLabel("")
        self.scan_warning.setObjectName("scanWarning")
        self.scan_warning.setWordWrap(True)
        self.scan_warning.hide()
        layout.addWidget(self.scan_warning)

        layout.addWidget(self._section("변환 형식"))
        kind_row = QHBoxLayout()
        self.kind_group = QButtonGroup(self)
        self.kind_group.setExclusive(True)
        self.kind_buttons = {}
        for kind, label, icon in KIND_LABELS:
            button = QPushButton(make_tool_icon(icon), label)
            button.setObjectName("kindButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, k=kind: self.select_kind(k))
            self.kind_group.addButton(button)
            self.kind_buttons[kind] = button
            kind_row.addWidget(button)
        kind_row.addStretch(1)
        layout.addLayout(kind_row)

        self.options = QStackedWidget()
        excel = QLabel("표는 표마다 시트 하나(p1_표1…), 표가 없는 페이지·표 밖 글자는 줄 단위로 'p1_텍스트' 시트에 넣습니다.")
        excel.setWordWrap(True)
        excel.setObjectName("convertHint")
        hwp = QWidget()
        hwp_row = QHBoxLayout(hwp)
        hwp_row.setContentsMargins(0, 0, 0, 0)
        hwp_row.addWidget(QLabel("파일 형식"))
        self.hwp_format = QComboBox()
        self.hwp_format.addItem(".hwpx (한글 2014 이상, 기본)", conv.FORMAT_HWPX)
        self.hwp_format.addItem(".hwp (한글 프로그램으로 저장)", conv.FORMAT_HWP)
        if not self.hancom:
            self.hwp_format.model().item(1).setEnabled(False)
            self.hwp_format.setItemData(1, "이 PC에 한컴 한글이 없어 .hwp는 만들 수 없습니다. .hwpx는 한글 2014 이상에서 열립니다.", Qt.ToolTipRole)
        self.hwp_format.currentIndexChanged.connect(self.update_output_preview)
        hwp_row.addWidget(self.hwp_format)
        hwp_note = QLabel("글자·표 중심 (그림·도형 제외)")
        hwp_note.setObjectName("convertHint")
        hwp_row.addWidget(hwp_note)
        hwp_row.addStretch(1)
        image = QWidget()
        image_row = QHBoxLayout(image)
        image_row.setContentsMargins(0, 0, 0, 0)
        image_row.addWidget(QLabel("형식"))
        self.image_format = QComboBox()
        self.image_format.addItem("PNG", conv.FORMAT_PNG)
        self.image_format.addItem("JPG", conv.FORMAT_JPG)
        self.image_format.currentIndexChanged.connect(self.update_output_preview)
        image_row.addWidget(self.image_format)
        image_row.addSpacing(10)
        image_row.addWidget(QLabel("해상도"))
        self.dpi_combo = QComboBox()
        for dpi in conv.IMAGE_DPI_CHOICES:
            self.dpi_combo.addItem(f"{dpi} DPI" + (" (기본)" if dpi == conv.DEFAULT_IMAGE_DPI else ""), dpi)
        try:
            saved_dpi = int(self.prefs.get("dpi", conv.DEFAULT_IMAGE_DPI))
        except (TypeError, ValueError):
            saved_dpi = conv.DEFAULT_IMAGE_DPI
        if saved_dpi not in conv.IMAGE_DPI_CHOICES:
            saved_dpi = conv.DEFAULT_IMAGE_DPI
        self.dpi_combo.setCurrentIndex(conv.IMAGE_DPI_CHOICES.index(saved_dpi))
        image_row.addWidget(self.dpi_combo)
        image_row.addStretch(1)
        self.option_pages = {KIND_EXCEL: excel, KIND_HWP: hwp, KIND_IMAGE: image}
        for widget in (excel, hwp, image):
            self.options.addWidget(widget)
        layout.addWidget(self.options)

        layout.addWidget(self._section("페이지"))
        page_row = QHBoxLayout()
        self.all_pages_radio = QRadioButton("전체")
        self.current_page_radio = QRadioButton("현재 페이지")
        self.range_radio = QRadioButton("범위")
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("예: 1-3, 5")
        self.range_edit.textEdited.connect(lambda _t: self.range_radio.setChecked(True))
        self.all_pages_radio.setChecked(True)
        for widget in (self.all_pages_radio, self.current_page_radio, self.range_radio):
            page_row.addWidget(widget)
        page_row.addWidget(self.range_edit, 1)
        layout.addLayout(page_row)

        layout.addWidget(self._section("저장 위치"))
        out_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.textChanged.connect(self.update_output_preview)
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(self.choose_output_dir)
        out_row.addWidget(self.output_dir_edit, 1)
        out_row.addWidget(browse)
        layout.addLayout(out_row)
        self.output_preview = QLabel("")
        self.output_preview.setObjectName("outputPreview")
        self.output_preview.setWordWrap(True)
        layout.addWidget(self.output_preview)
        self.open_folder_check = QCheckBox("완료 후 폴더 열기")
        self.open_folder_check.setChecked(bool(self.prefs.get("open_folder", True)))
        layout.addWidget(self.open_folder_check)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label = QLabel("")
        self.status_label.setObjectName("convertHint")
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.convert_button = QPushButton("변환")
        self.convert_button.setObjectName("convertButton")
        self.convert_button.clicked.connect(self.start_conversion)
        self.close_button = QPushButton("닫기")
        self.close_button.clicked.connect(self.close_or_cancel)
        buttons.addWidget(self.convert_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    @staticmethod
    def _section(text):
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    # ---------------------------------------------------------------- state
    def current_kind(self):
        for kind, button in self.kind_buttons.items():
            if button.isChecked():
                return kind
        return KIND_EXCEL

    def select_kind(self, kind):
        self.kind_buttons[kind].setChecked(True)
        self.options.setCurrentWidget(self.option_pages[kind])
        if self.worker is None:
            self.progress.setValue(0)
            self.status_label.setText("")
        self.update_output_preview()

    def current_format(self):
        kind = self.current_kind()
        if kind == KIND_EXCEL:
            return conv.FORMAT_XLSX
        if kind == KIND_HWP:
            return self.hwp_format.currentData()
        return self.image_format.currentData()

    def _cleanup_temp(self):
        if self.temp_dir:
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None

    def use_document_source(self):
        """The open document as it is on screen (merged files, page order, rotation, stamps included)."""
        owner = self.owner
        refs = owner.ordered_page_refs() if owner is not None and owner.page_list.count() else []
        if not refs:
            self.source = None
            self.source_label.setText("열린 문서가 없습니다. '다른 PDF…'로 파일을 고르세요.")
            self.update_output_preview()
            return
        name_path = owner.current_pdf_path or refs[0].path
        first = refs[0]
        pristine = all(
            ref.path == first.path and ref.page_index == i and not ref.rotation and not ref.marks for i, ref in enumerate(refs)
        )
        if pristine:
            try:
                pristine = conv.pdf_page_count(first.path, owner.password_for_page_ref(first)) == len(refs)
            except Exception:
                pristine = False
        self._cleanup_temp()
        if pristine:
            path, password = first.path, owner.password_for_page_ref(first)
        else:
            self.temp_dir = tempfile.mkdtemp(prefix="koinfra_convert_")
            path = str(Path(self.temp_dir) / (Path(name_path).stem + ".pdf"))
            password = None
            try:
                owner.write_ordered_pdf(path, refs)
            except Exception as exc:
                self._cleanup_temp()
                self.source = None
                self.source_label.setText(f"열린 문서를 준비하지 못했습니다: {exc}")
                return
        self.source = {
            "path": path, "password": password, "name": Path(name_path).name, "stem": Path(name_path).stem,
            "page_count": len(refs), "current_index": max(0, min(len(refs) - 1, owner.page_list.currentRow())),
            "from_document": True, "folder": str(Path(name_path).parent),
        }
        edited = "" if pristine else " · 편집 내용 포함"
        self.source_label.setText(f"<b>열린 문서</b>  {self.source['name']} ({len(refs)}쪽{edited})")
        self.current_page_radio.setEnabled(True)
        self.after_source_changed()

    def choose_source_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "변환할 PDF 선택", self.output_dir_edit.text(), PDF_FILTER)
        if path:
            self.set_source_file(path)

    def set_source_file(self, path, password=None):
        try:
            count = conv.pdf_page_count(path, password)
        except Exception:
            known = self.owner.pdf_passwords.get(str(Path(path)), "") if self.owner is not None else ""
            try:
                count = conv.pdf_page_count(path, known)
                password = known
            except Exception as exc:
                QMessageBox.warning(self, "PDF 변환", f"{Path(path).name}\n\n파일을 열 수 없습니다(암호 PDF는 먼저 편집기에서 열어 주세요).\n{exc}")
                return False
        self._cleanup_temp()
        self.source = {
            "path": str(path), "password": password, "name": Path(path).name, "stem": Path(path).stem,
            "page_count": count, "current_index": 0, "from_document": False, "folder": str(Path(path).parent),
        }
        self.source_label.setText(f"<b>선택한 파일</b>  {Path(path).name} ({count}쪽)")
        if self.current_page_radio.isChecked():
            self.all_pages_radio.setChecked(True)
        self.current_page_radio.setEnabled(False)
        self.after_source_changed()
        return True

    def after_source_changed(self):
        if not self.output_dir_edit.text().strip():
            folder = self.prefs.get("output_dir") or self.source["folder"]
            if not folder or not Path(folder).is_dir():
                folder = self.source["folder"]
            self.output_dir_edit.setText(folder)
        try:
            _with, without = conv.text_layer_report(self.source["path"], None, self.source["password"])
        except Exception:
            _with, without = [1], []
        if without and not _with:
            self.scan_warning.setText("이 PDF는 글자 정보가 없는 스캔 문서예요. 엑셀·한글로는 옮길 내용이 없어서 '이미지' 변환만 쓸 수 있어요 (글자로 바꾸려면 OCR이 필요합니다).")
            self.scan_warning.show()
        elif without:
            pages = ", ".join(str(i + 1) for i in without[:8]) + (" …" if len(without) > 8 else "")
            self.scan_warning.setText(f"글자 정보가 없는 스캔 페이지가 있어요 ({pages}쪽). 엑셀·한글에는 빈 페이지로 들어갑니다.")
            self.scan_warning.show()
        else:
            self.scan_warning.hide()
        self.scanned_all = bool(without and not _with)
        self.update_output_preview()

    def selected_pages(self):
        """0-based page indexes, or None for all pages. Raises ValueError for a bad range."""
        if self.source is None:
            raise ValueError("변환할 PDF가 없습니다.")
        if self.current_page_radio.isChecked():
            return [self.source["current_index"]]
        if self.range_radio.isChecked():
            text = self.range_edit.text().strip()
            if not text:
                raise ValueError("페이지 범위를 입력하세요 (예: 1-3, 5).")
            return parse_page_ranges(text, self.source["page_count"])
        return None

    def output_target(self):
        folder = self.output_dir_edit.text().strip()
        if self.source is None or not folder:
            return None
        return conv.unique_path(conv.default_output_path(self.source["stem"] + ".pdf", folder, self.current_format()))

    def update_output_preview(self, *_args):
        target = self.output_target()
        if target is None:
            self.output_preview.setText("")
            return
        if self.current_format() in (conv.FORMAT_PNG, conv.FORMAT_JPG):
            self.output_preview.setText(f"저장: {Path(target) / (self.source['stem'] + '_p001.' + self.current_format())} …")
        else:
            self.output_preview.setText(f"저장: {target}")

    def choose_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "저장 위치 선택", self.output_dir_edit.text())
        if folder:
            self.output_dir_edit.setText(str(Path(folder)))

    # ---------------------------------------------------------------- run
    def build_job(self):
        if self.source is None:
            raise ValueError("변환할 PDF가 없습니다. PDF를 열거나 '다른 PDF…'로 고르세요.")
        fmt = self.current_format()
        if fmt == conv.FORMAT_HWP and not self.hancom:
            raise ValueError("이 PC에는 한컴 한글이 없어 .hwp로 저장할 수 없습니다. .hwpx를 선택하세요.")
        pages = self.selected_pages()
        folder = self.output_dir_edit.text().strip()
        if not folder:
            raise ValueError("저장 위치를 고르세요.")
        Path(folder).mkdir(parents=True, exist_ok=True)
        return {
            "pdf_path": self.source["path"], "fmt": fmt, "output": str(self.output_target()), "pages": pages,
            "password": self.source["password"] or None, "dpi": int(self.dpi_combo.currentData()), "base_name": self.source["stem"],
        }

    def start_conversion(self):
        try:
            job = self.build_job()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "PDF 변환", str(exc))
            return False
        if job["fmt"] in (conv.FORMAT_XLSX, conv.FORMAT_HWPX, conv.FORMAT_HWP) and self.scanned_all:
            QMessageBox.information(
                self, "스캔 PDF",
                "이 PDF는 글자 정보가 없는 스캔 문서라 엑셀·한글로 옮길 내용이 없습니다.\n'이미지'로 변환하거나 OCR(글자 인식)을 거친 PDF로 다시 시도하세요.",
            )
            return False
        save_convert_preferences(output_dir=self.output_dir_edit.text().strip(), open_folder=self.open_folder_check.isChecked(),
                                 dpi=int(self.dpi_combo.currentData()), kind=self.current_kind())
        self.last_stats = None
        self.last_error = None
        self.set_running(True)
        self.progress.setValue(0)
        self.status_label.setText("변환 준비 중…")
        self.worker = ConvertWorker(job, self)
        self.worker.progressed.connect(self.on_progress)
        self.worker.succeeded.connect(self.on_succeeded)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()
        return True

    def set_running(self, running):
        self.convert_button.setEnabled(not running)
        self.close_button.setText("취소" if running else "닫기")
        for widget in list(self.kind_buttons.values()) + [self.document_source_button, self.file_source_button, self.options,
                                                          self.output_dir_edit, self.range_edit]:
            widget.setEnabled(not running)

    def on_progress(self, done, total, message):
        self.progress.setValue(int(done * 100 / total) if total else 0)
        self.status_label.setText(message)

    def on_succeeded(self, stats):
        self.last_stats = stats
        self.progress.setValue(100)
        fmt = stats.get("format")
        if fmt in (conv.FORMAT_PNG, conv.FORMAT_JPG):
            summary = f"이미지 {len(stats.get('files', []))}장 저장 ({stats.get('dpi')} DPI)"
            select = stats["files"][0] if stats.get("files") else None
            folder = stats["output"]
        else:
            parts = [f"{stats.get('pages', 0)}쪽"]
            if fmt == conv.FORMAT_XLSX:
                parts.append(f"표 {stats.get('tables', 0)}개 · 시트 {stats.get('sheets', 0)}개")
            else:
                parts.append(f"표 {stats.get('tables', 0)}개 · 문단 {stats.get('paragraphs', 0)}개")
            if stats.get("scanned_pages"):
                parts.append(f"스캔 페이지 {len(stats['scanned_pages'])}쪽은 내용 없음")
            summary = " · ".join(parts)
            select = stats["output"]
            folder = str(Path(stats["output"]).parent)
        self.status_label.setText(f"완료: {summary}\n{stats['output']}")
        if self.owner is not None and hasattr(self.owner, "set_status"):
            self.owner.set_status(f"변환 완료: {stats['output']}")
        if self.open_folder_check.isChecked():
            open_folder(folder, select)
        self.update_output_preview()

    def on_failed(self, message):
        self.last_error = message
        self.status_label.setText("변환하지 못했습니다.")
        QMessageBox.warning(self, "PDF 변환", message)

    def on_cancelled(self):
        self.status_label.setText("취소했습니다.")
        self.progress.setValue(0)

    def on_worker_finished(self):
        self.set_running(False)
        self.worker = None

    def close_or_cancel(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel_requested = True
            self.status_label.setText("취소하는 중…")
            return
        self.close()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel_requested = True
            self.worker.wait(15000)
        self._cleanup_temp()
        super().closeEvent(event)

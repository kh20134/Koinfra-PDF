"""Koinfra PDF - custom widgets: page thumbnail list, preview scroll area/canvas, signature pad and the stamp/signature dialogs."""
from PyQt5.QtCore import QPointF, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QPainterPath, QDragEnterEvent, QDropEvent, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QMenu, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget
from kpdf_core import STAMP_POSITIONS, normalize_approval_line
from kpdf_graphics import make_tool_icon


class PageThumbnailList(QListWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.drop_indicator_row = None
        self.drag_rows = []
        self.last_context_menu = None
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
        width_changed = event.size().width() != event.oldSize().width()
        if width_changed and getattr(self.owner, "thumbnail_view_anchor", 1) is None and hasattr(self.owner, "thumbnail_view_state"):
            self.owner.thumbnail_view_anchor = self.owner.thumbnail_view_state()  # first resize of a drag: before re-flow
        super().resizeEvent(event)
        if width_changed:
            self.owner.schedule_thumbnail_refresh()

    def startDrag(self, supported_actions):
        self.drag_rows = self.drag_rows or self.selected_page_rows()
        self.restore_drag_selection()
        super().startDrag(Qt.CopyAction)

    def mousePressEvent(self, event):
        pressed_index = self.indexAt(event.pos())
        selected_rows = self.selected_page_rows()

        if event.button() == Qt.RightButton:
            # Right button never starts a drag or changes a multi-selection it lands on (the 내보내기/회전/지우기
            # menu then acts on all selected pages); on an unselected page it selects just that page.
            item = self.item(pressed_index.row()) if pressed_index.isValid() else None
            if item is not None and not item.isSelected():
                self.clearSelection()
                self.setCurrentItem(item)
            self.drag_rows = []
            event.accept()
            return

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

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        # Right-click on a page outside the selection selects just that page (Explorer-style);
        # on a selected page the whole selection stays, so it can be exported/rotated/deleted together.
        item = self.itemAt(event.pos())
        if item is not None and not item.isSelected():
            self.clearSelection()
            self.setCurrentItem(item)
        menu = QMenu(self)
        cut_action = menu.addAction("\uc798\ub77c\ub0b4\uae30")
        copy_action = menu.addAction("\ubcf5\uc0ac\ud558\uae30")
        paste_action = menu.addAction("\ubd99\uc5ec\ub123\uae30")
        menu.addSeparator()
        rotate_right_action = menu.addAction("\uc624\ub978\ucabd \ud68c\uc804")
        rotate_left_action = menu.addAction("\uc67c\ucabd \ud68c\uc804")
        menu.addSeparator()
        delete_action = menu.addAction("\uc9c0\uc6b0\uae30")
        menu.addSeparator()
        selected_count = len({index.row() for index in self.selectedIndexes()})
        export_menu = menu.addMenu(make_tool_icon("convert"), f"내보내기 (선택 {selected_count}쪽)" if selected_count else "내보내기")
        export_actions = [
            (export_menu.addAction(make_tool_icon("extract"), "PDF로… (선택 페이지만 새 PDF)"), "pdf"),
            (export_menu.addAction(make_tool_icon("conv_excel"), "엑셀로… (.xlsx)"), "excel"),
            (export_menu.addAction(make_tool_icon("conv_hwp"), "한글로… (.hwpx/.hwp)"), "hwp"),
            (export_menu.addAction(make_tool_icon("conv_image"), "이미지로… (PNG/JPG)"), "image"),
        ]
        self.last_context_menu = menu

        has_selection = bool(self.selectedIndexes())
        export_menu.setEnabled(has_selection)
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
        else:
            for action, kind in export_actions:
                if selected_action is not None and selected_action == action:
                    self.owner.export_selected_pages(kind)
                    break

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

        painter = QPainter(self.viewport())
        pen = QPen(QColor("#d63b3b"), 2, Qt.DashLine)
        painter.setPen(pen)
        if self.grid_columns() > 1:
            x, top, bottom = self.drop_indicator_x(self.drop_indicator_row)
            painter.drawLine(x, top, x, bottom)
        else:
            y = self.drop_indicator_y(self.drop_indicator_row)
            painter.drawLine(12, y, self.viewport().width() - 12, y)
        painter.end()

    def has_pdf_urls(self, event):
        mime_data = event.mimeData()
        return any(self.is_pdf_url(url) for url in mime_data.urls())

    def pdf_paths_from_event(self, event):
        return [url.toLocalFile() for url in event.mimeData().urls() if self.is_pdf_url(url)]

    def is_pdf_url(self, url):
        return url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")

    def grid_columns(self):
        """Thumbnail columns currently shown (1 in a narrow panel, more when it is widened)."""
        grid_width = self.gridSize().width()
        if grid_width <= 0:
            return 1
        return max(1, self.viewport().width() // grid_width)

    def drop_insert_row(self, position):
        target_index = self.indexAt(position)
        if not target_index.isValid():
            return self.drop_insert_row_from_gap(position)

        target_row = target_index.row()
        rect = self.visualItemRect(self.item(target_row))
        if self.grid_columns() > 1:  # grid: left half = before, right half = after
            return target_row + 1 if position.x() > rect.center().x() else target_row
        if position.y() > rect.center().y():
            return target_row + 1
        return target_row

    def drop_insert_row_from_gap(self, position):
        if self.count() == 0:
            return 0

        grid = self.grid_columns() > 1
        for row in range(self.count()):
            rect = self.visualItemRect(self.item(row))
            if not grid:
                if position.y() < rect.center().y():
                    return row
                continue
            # reading order: a later line, or this line left of the item's middle
            if position.y() < rect.top() - self.spacing() or (position.y() <= rect.bottom() + self.spacing() and position.x() < rect.center().x()):
                return row
        return self.count()

    def drop_indicator_x(self, row):
        """Vertical marker (x, top, bottom) between two thumbnails in grid mode."""
        if self.count() == 0:
            return self.viewport().width() // 2, 4, self.viewport().height() - 4
        if row >= self.count():
            rect = self.visualItemRect(self.item(self.count() - 1))
            x = min(self.viewport().width() - 2, rect.right() + 4)
        else:
            rect = self.visualItemRect(self.item(max(0, row)))
            x = max(2, rect.left() - 4)
        return x, rect.top() + 4, rect.bottom() - 4

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

        delta = event.angleDelta().y()
        if event.modifiers() & Qt.ControlModifier:
            self.owner.wheel_zoom(delta, event.pos())
            event.accept()
            return

        bar = self.verticalScrollBar()
        if delta and bar.maximum() > bar.minimum():
            # Zoomed page taller than the window: scroll it first, turn the page only at its top/bottom edge.
            if (delta > 0 and bar.value() > bar.minimum()) or (delta < 0 and bar.value() < bar.maximum()):
                self.owner.wheel_page_remainder = 0
                super().wheelEvent(event)
                return
        self.owner.wheel_page(delta)
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

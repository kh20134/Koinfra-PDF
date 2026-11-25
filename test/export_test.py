"""썸네일 우클릭 > 내보내기: click / Ctrl+click / Shift+click / Ctrl+A selection, right-click selection rules,
the 내보내기 submenu (PDF / 엑셀 / 한글 / 이미지) next to the existing items, exported content = selected pages.
usage: python export_test.py <project_dir> <out_dir>   (temp APPDATA, dialogs stubbed)"""
import importlib.util, json, os, shutil, sys, tempfile, traceback, types
from pathlib import Path

PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="koinfra_export_test_"))
os.environ["APPDATA"] = str(WORK / "appdata")
if sys.platform != "win32":
    import mimetypes; mimetypes.init()
    sys.modules["winreg"] = types.ModuleType("winreg")
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("Koinfra", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)
import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtGui import QContextMenuEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog, QMenu  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def make_pdf(path, pages=5):
    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((60, 80), f"페이지 {i + 1} 제목", fontname="korea", fontsize=18)
        p.insert_text((60, 120), f"본문 {i + 1}쪽 내용입니다.", fontname="korea", fontsize=11)
        if i == 1:
            sh = p.new_shape()
            for r in range(4): sh.draw_line((60, 160 + r * 24), (420, 160 + r * 24))
            for x in (60, 240, 420): sh.draw_line((x, 160), (x, 232))
            sh.finish(color=(0, 0, 0), width=0.8); sh.commit()
            for r, row in enumerate([["항목", "값"], ["CCTV", "12"], ["출입", "8"]]):
                for c, t in enumerate(row): p.insert_text((66 + c * 180, 177 + r * 24), t, fontname="korea", fontsize=10)
    doc.save(str(path)); doc.close()


def pdf_texts(path):
    doc = pymupdf.open(str(path))
    try:
        return [(page.get_text().split("\n")[0], page.rotation) for page in doc]
    finally:
        doc.close()


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    import kpdf_convert_dialog as ui
    opened = []
    ui.open_folder = lambda path, select=None: opened.append((str(path), select))
    saves = []
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (saves.pop(0) if saves else "", ""))
    src = WORK / "원본.pdf"; make_pdf(src)
    w = K.PdfPreviewer(); w.resize(1280, 1000); w.show(); QTest.qWait(200)
    w.load_pdf(str(src)); QTest.qWait(300)
    pl = w.page_list; vp = pl.viewport()
    w.main_splitter.setSizes([420, 860]); QTest.qWait(400)  # wider panel: thumbnail grid with 2+ columns

    def center(row):
        pl.scrollToItem(pl.item(row)); QTest.qWait(20)
        return pl.visualItemRect(pl.item(row)).center()

    def sel():
        return sorted({i.row() for i in pl.selectedIndexes()})

    check("ui.page_ranges_text", ui.page_ranges_text([0, 1, 2, 4, 6, 7]) == "1-3, 5, 7-8" and ui.page_ranges_text([3]) == "4")
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, center(1)); QTest.qWait(50)
    check("click selects one page", sel() == [1] and pl.currentRow() == 1, sel())
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, center(3)); QTest.qWait(50)
    check("Ctrl+click adds a page", sel() == [1, 3], sel())
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, center(1)); QTest.qWait(50)
    check("Ctrl+click on a selected page removes it", sel() == [3], sel())
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, center(0)); QTest.qWait(50)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ShiftModifier, center(2)); QTest.qWait(50)
    check("Shift+click selects a range", sel() == [0, 1, 2], sel())
    pl.setFocus(); QTest.keyClick(pl, Qt.Key_A, Qt.ControlModifier); QTest.qWait(50)
    check("Ctrl+A selects all pages", sel() == [0, 1, 2, 3, 4], sel())

    # selected pages clearly marked: selected background is a stronger blue than an unselected one
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, center(1)); QTest.qWait(100)
    pl.scrollToItem(pl.item(1)); QTest.qWait(100)
    shot = vp.grab().toImage()
    def corner_color(row):
        r = pl.visualItemRect(pl.item(row)); c = shot.pixelColor(r.left() + 4, r.top() + 4); return (c.red(), c.green(), c.blue())
    s_col, u_col = corner_color(1), corner_color(2)
    check("selected thumbnail visibly highlighted (blue background)", s_col[2] > s_col[0] + 40 and u_col[0] > 230, (s_col, u_col))

    # ---- context menu
    menus = []
    choose = {"text": None}

    def fake_exec(menu, *a):
        menus.append(menu)
        want = choose["text"]
        if not want:
            return None
        for action in menu.actions():
            sub = action.menu()
            for sa in (sub.actions() if sub else [action]):
                if sa.text().startswith(want):
                    return sa
        return None
    QMenu.exec_ = fake_exec

    def right_click(row):
        pos = center(row)
        QTest.mousePress(vp, Qt.RightButton, Qt.NoModifier, pos); QTest.mouseRelease(vp, Qt.RightButton, Qt.NoModifier, pos)
        QApplication.sendEvent(vp, QContextMenuEvent(QContextMenuEvent.Mouse, pos, vp.mapToGlobal(pos))); QTest.qWait(50)

    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, center(0)); QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, center(1))
    right_click(3)
    check("right-click on an unselected page selects just that page", sel() == [3] and pl.currentRow() == 3, sel())
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, center(1)); QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, center(3))
    right_click(3)
    check("right-click on a selected page keeps the multi-selection", sel() == [1, 3], sel())
    menu = menus[-1]
    top = [a.text() for a in menu.actions() if not a.isSeparator()]
    check("existing items kept (잘라내기/복사하기/붙여넣기/회전/지우기)", top[:6] == ["잘라내기", "복사하기", "붙여넣기", "오른쪽 회전", "왼쪽 회전", "지우기"], top)
    export = [a for a in menu.actions() if a.menu() is not None]
    sub = [a.text() for a in export[0].menu().actions()] if export else []
    check("내보내기 submenu shows count + PDF/엑셀/한글/이미지", export and export[0].text() == "내보내기 (선택 2쪽)" and [t[:3] for t in sub] == ["PDF", "엑셀로", "한글로", "이미지"] and export[0].isEnabled(), (top, sub))

    # ---- PDF로: only the selected pages, in list order, with edits (rotation)
    w.rotate_page_rows([3], 90); QTest.qWait(50)
    pl.clearSelection(); pl.item(1).setSelected(True); pl.item(3).setSelected(True)
    out_pdf = OUT / "선택_2_4.pdf"; out_pdf.unlink(missing_ok=True)
    saves.append(str(out_pdf)); choose["text"] = "PDF로"; opened.clear()
    right_click(3)
    texts = pdf_texts(out_pdf) if out_pdf.exists() else []
    check("PDF로: new PDF with pages 2 and 4 (4 rotated 90)", [t for t, _ in texts] == ["페이지 2 제목", "페이지 4 제목"] and texts[1][1] == 90, texts)
    check("PDF로: folder opened with the file selected, document unchanged", opened and opened[-1][1] == str(out_pdf) and w.page_count == 5 and Path(w.current_pdf_path).name == "원본.pdf", (opened, w.page_count))

    # ---- 엑셀/한글/이미지: the 변환 window preset to the selected pages (range), same engine
    runs = []

    def run_dialog(dlg):
        runs.append({"title": dlg.windowTitle(), "range": dlg.range_edit.text(), "range_on": dlg.range_radio.isChecked(), "kind": dlg.current_kind()})
        dlg.output_dir_edit.setText(str(OUT / "conv"))
        if dlg.current_kind() == "image":
            dlg.dpi_combo.setCurrentIndex(0)  # 72 DPI
        if dlg.start_conversion():
            for _ in range(400):
                QTest.qWait(25)
                if dlg.worker is None:
                    break
        runs[-1]["stats"] = dlg.last_stats
        runs[-1]["error"] = dlg.last_error
        return 0
    QDialog.exec_ = run_dialog
    shutil.rmtree(OUT / "conv", ignore_errors=True)
    for label, kind in (("엑셀로", "excel"), ("한글로", "hwp"), ("이미지로", "image")):
        pl.clearSelection(); pl.item(1).setSelected(True); pl.item(3).setSelected(True)
        choose["text"] = label
        right_click(1)
        r = runs[-1] if runs else {}
        st = r.get("stats") or {}
        check(f"{label}: 변환 창 preset to 2, 4 and converted 2 pages", r.get("kind") == kind and r.get("range") == "2, 4" and r.get("range_on") and r.get("title") == "선택한 2쪽 내보내기" and st.get("pages") == 2, r)
    xl = runs[0].get("stats", {}) or {}
    check("엑셀로: table on page 2 found", xl.get("tables") == 1, xl)
    img = runs[2].get("stats", {}) or {}
    sizes = [Image.open(f).size for f in img.get("files", [])]
    check("이미지로: p002 portrait + p004 landscape (rotation kept)", [Path(f).name for f in img.get("files", [])] == ["원본_p002.png", "원본_p004.png"] and sizes[1][0] > sizes[1][1], (img.get("files"), sizes))

    # ---- nothing selected / empty document
    menus.clear(); choose["text"] = None
    pl.clearSelection()
    pos = QPoint(vp.width() - 5, vp.height() - 5)
    QApplication.sendEvent(vp, QContextMenuEvent(QContextMenuEvent.Mouse, pos, vp.mapToGlobal(pos))); QTest.qWait(30)
    export = [a for a in menus[-1].actions() if a.menu() is not None]
    check("right-click on empty space with no selection: 내보내기 disabled", export and not export[0].isEnabled())
    check("export with no document does nothing", K.PdfPreviewer().export_selected_pages("pdf") is None)

    # ---- drag reorder still works with a multi-selection
    pl.clearSelection(); pl.item(0).setSelected(True); pl.item(1).setSelected(True)
    w.move_pages_by_rows([0, 1], 5); QTest.qWait(100)
    order = [Path(r.path).name + str(r.page_index) for r in w.ordered_page_refs()]
    check("move selected pages (drag order) unaffected", [r.page_index for r in w.ordered_page_refs()] == [2, 3, 4, 0, 1], order)
    w.close()


try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False, traceback.format_exc()[-300:]))
finally:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / "export_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0 if passed == len(RESULTS) else 1)

"""Koinfra PDF - responsive thumbnail grid in the left panel (1 / 2 / 3 ... columns). Test PDFs only, temp APPDATA.
usage: python thumb_grid_test.py <project_dir> <out_dir> [--capture]"""
import importlib.util, json, math, os, sys, tempfile, time, traceback, types
from pathlib import Path

PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
CAPTURE = "--capture" in sys.argv
WORK = Path(tempfile.mkdtemp(prefix="koinfra_grid_"))
os.environ["APPDATA"] = str(WORK / "appdata")
if sys.platform != "win32":
    sys.modules["winreg"] = types.ModuleType("winreg")
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("Koinfra", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)

from PIL import Image, ImageDraw, ImageFont
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox

RESULTS, ERRORS = [], []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok))); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def make_pdf(path, pages):
    images = []
    for i in range(pages):
        size = (1754, 1240) if i % 7 == 5 else (1240, 1754)
        im = Image.new("RGB", size, "white"); d = ImageDraw.Draw(im)
        d.rectangle([40, 40, size[0] - 40, size[1] - 40], outline=(20, 60, 200), width=10)
        d.rectangle([90, 110, size[0] - 90, 300], fill=(30, 64, 175))
        try:
            font = ImageFont.truetype("arial.ttf" if sys.platform == "win32" else "DejaVuSans-Bold.ttf", 360)
        except OSError:
            font = ImageFont.load_default()
        d.text((size[0] // 2, size[1] // 2), str(i + 1), fill=(15, 23, 42), font=font, anchor="mm")
        for row in range(12):
            d.line([(120, size[1] - 520 + row * 34), (size[0] - 120 - (row % 3) * 160, size[1] - 520 + row * 34)], fill=(148, 163, 184), width=6)
        images.append(im)
    images[0].save(path, "PDF", resolution=150.0, save_all=True, append_images=images[1:])


def main():
    sys.excepthook = lambda t, v, tb: ERRORS.append("".join(traceback.format_exception(t, v, tb)))
    app = QApplication.instance() or QApplication(sys.argv[:1])
    for n in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.No))
    doc = WORK / "grid.pdf"; make_pdf(doc, 24)
    w = K.PdfPreviewer(); w.resize(1280, 820); w.show(); QTest.qWait(300)
    w.load_pdf(str(doc)); QTest.qWait(400)
    pl = w.page_list
    total = sum(w.main_splitter.sizes())

    def set_panel(width):
        w.main_splitter.setSizes([width, total - width]); QTest.qWait(450)

    def layout_state():
        vw = pl.viewport().width(); vh = pl.viewport().height()
        rects = [pl.visualRect(pl.model().index(r, 0)) for r in range(pl.count())]
        visible = [(r, rc) for r, rc in enumerate(rects) if rc.isValid() and rc.bottom() >= 0 and rc.top() <= vh]
        tops = sorted({rc.top() for _, rc in visible})
        first_line = [rc for _, rc in visible if rc.top() == tops[0]] if tops else []
        cols = len(first_line)
        worst = 0
        for _, rc in visible:
            left, right = w.thumbnail_cell_bounds(rc)
            worst = max(worst, abs((rc.left() - left) - (right - rc.right())), max(0, rc.right() - vw + 1), max(0, -rc.left()))
        return vw, cols, worst, rects

    def reading_order_ok(rects, cols):
        # page r sits in column r % cols of line r // cols (left to right, then down)
        for r in range(1, len(rects)):
            a, b = rects[r - 1], rects[r]
            if r % cols:
                if not (b.top() == a.top() and b.left() > a.right()):
                    return False, r
            elif not (b.top() > a.bottom() and b.left() < a.left()) and cols > 1:
                return False, r
            elif cols == 1 and not b.top() > a.bottom():
                return False, r
        return True, None

    # ---------------- narrow -> 1 column, wider -> 2, 3, 4 columns
    seen = {}
    for width in (170, 260, 330, 400, 560, 760):
        set_panel(width)
        bar = pl.verticalScrollBar()
        bad = []
        for value in (0, bar.maximum() // 2, bar.maximum()):
            bar.setValue(value); QTest.qWait(200)
            vw, cols, worst, rects = layout_state()
            if worst > 2:
                bad.append((value, worst))
        bar.setValue(0); QTest.qWait(200)
        vw, cols, worst, rects = layout_state()
        expect = max(1, vw // w.THUMBNAIL_MIN_CELL)
        seen[width] = (vw, cols)
        order_ok, where = reading_order_ok(rects, cols)
        check(f"panel {width}px (viewport {vw}): {expect} column(s)", cols == expect == w.thumbnail_column_count == pl.grid_columns(), (cols, expect))
        check(f"panel {width}px: every thumbnail centered in its cell while scrolling, nothing clipped, no sideways scroll", not bad and pl.horizontalScrollBar().maximum() == 0, bad)
        check(f"panel {width}px: pages in reading order (left->right, then down)", order_ok, where)
        lines = len({rc.top() for rc in rects})
        check(f"panel {width}px: {math.ceil(24 / cols)} lines for 24 pages", lines == math.ceil(24 / cols), lines)
        if cols > 1:
            line = sorted([rc for rc in rects if rc.top() == rects[0].top()], key=lambda rc: rc.left())
            used_left, used_right = line[0].left(), vw - 1 - line[-1].right()
            check(f"panel {width}px: the grid as a whole is centered", abs(used_left - used_right) <= cols + 2, (used_left, used_right))
            check(f"panel {width}px: thumbnails stay a usable size (>= 120px wide)", w.thumbnail_canvas_width >= 120, w.thumbnail_canvas_width)
        if CAPTURE and cols in (1, 2, 3) and f"thumb_grid_{cols}col.png" not in [p.name for p in OUT.glob("*.png")]:
            w.show_page(min(4, pl.count() - 1)); QTest.qWait(250)
            w.grab().save(str(OUT / f"thumb_grid_{cols}col.png"))
    check("all of 1, 2, 3 columns reached", {c for _, c in seen.values()} >= {1, 2, 3}, seen)

    # ---------------- page numbers, selection, scroll-to-current in grid mode
    set_panel(560)
    check("page numbers under each thumbnail (1..24, centered)", [pl.item(r).text() for r in range(pl.count())] == [str(r + 1) for r in range(24)]
          and int(pl.viewOptions().displayAlignment) & int(Qt.AlignHCenter))
    pl.clearSelection(); w.show_page(5); pl.item(4).setSelected(True); pl.item(5).setSelected(True); QTest.qWait(50)
    set_panel(250); set_panel(760)
    check("selection + current page kept when the panel switches 1 <-> grid", pl.selected_page_rows() == [4, 5] and pl.currentRow() == 5 and w.current_page_index == 5, (pl.selected_page_rows(), pl.currentRow()))
    for width in (250, 400, 560, 760, 250):  # current page stays on screen while the column count changes
        set_panel(width)
        rc = pl.visualRect(pl.model().index(5, 0))
        if not (rc.bottom() > 0 and rc.top() < pl.viewport().height()):
            break
    check("current page stays in view while the panel goes 1 -> 2 -> 3 -> 4 -> 1 columns", rc.bottom() > 0 and rc.top() < pl.viewport().height(), (width, rc.top(), rc.bottom()))
    set_panel(760)
    w.show_page(22); QTest.qWait(300)
    rc = pl.visualRect(pl.model().index(22, 0))
    check("scroll-to-current works in the grid (page 23 visible)", rc.top() >= 0 and rc.bottom() <= pl.viewport().height(), (rc.top(), rc.bottom(), pl.viewport().height()))
    w.show_page(0); QTest.qWait(300)
    rc = pl.visualRect(pl.model().index(0, 0))
    check("scroll back to page 1", rc.top() >= 0)

    # ---------------- lazy rendering + speed in grid mode
    set_panel(560)
    w.thumbnail_cache.clear(); t0 = time.perf_counter(); w.refresh_thumbnail_icons(); dt = (time.perf_counter() - t0) * 1000
    visible = w.visible_thumbnail_rows()
    rendered = len(w.thumbnail_cache)
    check("lazy rendering: only visible thumbnails rendered (not all 24)", 0 < rendered < 24 and set(range(0, 6)) <= set(visible), (rendered, visible[:12]))
    check(f"grid refresh fast enough ({dt:.0f} ms for {rendered} thumbnails)", dt < 2500, dt)
    pl.verticalScrollBar().setValue(pl.verticalScrollBar().maximum()); QTest.qWait(400)
    check("scrolled to the end: the last page's thumbnail gets rendered", any(k[1] == 23 for k in w.thumbnail_cache), sorted({k[1] for k in w.thumbnail_cache}))
    pl.verticalScrollBar().setValue(0); QTest.qWait(300)

    # ---------------- drag-reorder targets in grid mode
    cols = pl.grid_columns()
    r7 = pl.visualRect(pl.model().index(7, 0))
    check("drop on the right half of a thumbnail = after it, left half = before it",
          pl.drop_insert_row(QPoint(r7.right() - 4, r7.center().y())) == 8 and pl.drop_insert_row(QPoint(r7.left() + 4, r7.center().y())) == 7)
    last = pl.visualRect(pl.model().index(pl.count() - 1, 0))
    gap_point = QPoint(pl.viewport().width() - 3, last.center().y()) if (pl.count() % cols) else QPoint(last.center().x(), last.bottom() + 6)
    pl.verticalScrollBar().setValue(pl.verticalScrollBar().maximum()); QTest.qWait(300)
    last = pl.visualRect(pl.model().index(pl.count() - 1, 0))
    check("drop in the empty space after the last page = at the end", pl.drop_insert_row(QPoint(min(pl.viewport().width() - 3, last.right() + 6), last.center().y())) == pl.count())
    pl.verticalScrollBar().setValue(0); QTest.qWait(300)
    r2 = pl.visualRect(pl.model().index(2, 0))
    x, top, bottom = pl.drop_indicator_x(2)
    check("grid drop marker = vertical line just left of the target thumbnail", r2.left() - 6 <= x <= r2.left() and top >= r2.top() and bottom <= r2.bottom())
    pl.drop_indicator_row = 2; pl.viewport().repaint(); img = pl.viewport().grab().toImage(); pl.drop_indicator_row = None; pl.viewport().update()
    red = sum(1 for y in range(top, bottom, 3) if (lambda c: c.red() > 180 and c.green() < 110)(img.pixelColor(x, y)))
    check("grid drop marker is drawn", red >= 3, red)
    refs = [pl.item(r).data(K.PAGE_ROLE).page_index for r in range(pl.count())]
    pl.clearSelection(); pl.item(0).setSelected(True); pl.setCurrentRow(0)
    target = pl.drop_insert_row(QPoint(r7.right() - 4, r7.center().y()))
    w.move_pages_by_rows([0], target); QTest.qWait(300)
    after = [pl.item(r).data(K.PAGE_ROLE).page_index for r in range(pl.count())]
    check("drag-reorder in the grid: page 1 dropped after page 8", after[:8] == refs[1:8] + [refs[0]] and after[8:] == refs[8:] and [pl.item(r).text() for r in range(3)] == ["1", "2", "3"], after[:10])
    vw, cols2, worst, _ = layout_state()
    check("still a centered grid after reordering", cols2 == cols and worst <= 2, (cols2, worst))

    # ---------------- back to narrow: the single centered column (earlier centering fix)
    set_panel(170)
    vw, cols, worst, rects = layout_state()
    check("narrow again: one centered column", cols == 1 and worst <= 2 and pl.grid_columns() == 1, (cols, worst))
    check("no unhandled exceptions", not ERRORS, ERRORS[-1:])
    w.close()


try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False))
finally:
    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / "thumb_grid_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)  # the temp test PDF / APPDATA (was left behind in %TEMP%)
    os._exit(0 if passed == len(RESULTS) else 1)

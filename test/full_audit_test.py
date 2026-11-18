"""Koinfra PDF full feature audit (test PDFs only, temp APPDATA, dialogs stubbed).
usage: python full_audit_test.py <project_dir> <out_dir> [--capture]"""
import importlib.util, os, shutil, sys, tempfile, time, traceback, types, random
from pathlib import Path

PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
CAPTURE = "--capture" in sys.argv
WORK = Path(tempfile.mkdtemp(prefix="koinfra_audit_"))
os.environ["APPDATA"] = str(WORK / "appdata")
if sys.platform != "win32":
    sys.modules["winreg"] = types.ModuleType("winreg")
spec = importlib.util.spec_from_file_location("Koinfra", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent, QImage
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

RESULTS, ERRORS, MSG = [], [], []
Q = {"open": [], "save": [], "text": [], "item": []}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok))); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def make_pdf(path, pages, seed=0, landscape_every=5):
    random.seed(seed); images = []
    for i in range(pages):
        size = (1754, 1240) if landscape_every and i % landscape_every == landscape_every - 2 else (1240, 1754)
        im = Image.new("RGB", size, "white"); d = ImageDraw.Draw(im)
        d.rectangle([40, 40, size[0] - 40, size[1] - 40], outline=(20, 60, 200), width=10)
        d.rectangle([size[0] // 2 - 120, 120, size[0] // 2 + 120, 360], fill=(200, 30, 30))  # page marker block
        for k in range(int(i) + 1):
            d.rectangle([80 + k * 30, size[1] - 200, 100 + k * 30, size[1] - 120], fill=(0, 0, 0))  # i+1 bars = page number
        for row in range(40):
            d.line([(90, 420 + row * 30), (size[0] - 90, 420 + row * 30)], fill=(random.randint(0, 90),) * 3, width=3)
        images.append(im)
    images[0].save(path, "PDF", resolution=150.0, save_all=True, append_images=images[1:])


def stub_dialogs():
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (Q["open"].pop(0) if Q["open"] else "", ""))
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([Q["open"].pop(0)] if Q["open"] else [], ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (Q["save"].pop(0) if Q["save"] else "", ""))
    QInputDialog.getText = staticmethod(lambda *a, **k: (Q["text"].pop(0), True) if Q["text"] else ("", False))
    QInputDialog.getItem = staticmethod(lambda *a, **k: (Q["item"].pop(0), True) if Q["item"] else ("", False))
    for n in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, n, staticmethod(lambda *a, n=n, **k: (MSG.append((n, a[1] if len(a) > 1 else "")), QMessageBox.No)[1]))
    QMessageBox.exec_ = lambda self: (MSG.append(("box", self.text())), QMessageBox.No)[1]
    QDialog.exec_ = lambda self: (MSG.append(("dialog", type(self).__name__)), 0)[1]
    QPrintDialog.exec_ = lambda self: (MSG.append(("print", "")), 0)[1]
    QMenu.exec_ = lambda self, *a: None


def wheel(widget, dy, mods=Qt.NoModifier, pos=None):
    pos = pos or QPoint(widget.width() // 2, widget.height() // 2)
    QApplication.sendEvent(widget, QWheelEvent(QPointF(pos), QPointF(widget.mapToGlobal(pos)), QPoint(0, 0), QPoint(0, dy), Qt.NoButton, mods, Qt.NoScrollPhase, False))


def refs(w):
    return [(Path(r.path).name, r.page_index, r.rotation, len(r.marks)) for r in w.ordered_page_refs()]


def main():
    sys.excepthook = lambda t, v, tb: ERRORS.append("".join(traceback.format_exception(t, v, tb)))
    app = QApplication.instance() or QApplication(sys.argv[:1])
    stub_dialogs()
    many = WORK / "many.pdf"; make_pdf(many, 24)
    small = WORK / "small.pdf"; make_pdf(small, 3, seed=2, landscape_every=0)
    w = K.PdfPreviewer(); w.resize(1280, 820); w.show(); QTest.qWait(300)

    # ---------------- every menu / toolbar action, without and with a document: no exceptions
    def all_actions():
        acts = [(m.text(), a) for m in w.menuBar().actions() for a in m.menu().actions() if a.text() and not a.isSeparator()]
        acts += [(name, a) for name, group, _ in w.toolbar_groups for a in group]
        return [(m, a) for m, a in acts if a.text() != "종료" and "기본 앱" not in a.text() and "연결" not in a.text()]
    for phase in ("빈 화면", "문서"):
        if phase == "문서":
            scratch = WORK / "actions.pdf"; shutil.copy(many, scratch)  # 저장 action overwrites this copy
            w.load_pdf(str(scratch)); QTest.qWait(300)
        ERRORS.clear(); failed = []
        for menu, action in all_actions():
            before = len(ERRORS); action.trigger(); QTest.qWait(15)
            if len(ERRORS) > before:
                failed.append(f"{menu}/{action.text()}: {ERRORS[-1].strip().splitlines()[-1]}")
        check(f"all menu+toolbar actions run without error ({phase}, {len(all_actions())})", not failed, "; ".join(failed[:4]))
    w.load_pdf(str(many)); QTest.qWait(300)
    check("open 24-page PDF", w.page_count == 24)
    for name in ("확대", "축소"):
        before = w.zoom_factor; ERRORS.clear()
        next(a for _, g, _ in w.toolbar_groups for a in g if a.text() == name).trigger(); QTest.qWait(30)
        check(f"toolbar {name} button changes zoom (was TypeError)", not ERRORS and w.zoom_factor != before, ERRORS[-1:] if ERRORS else w.zoom_factor)
    w.zoom_factor = 1.0; w.update_zoom(); QTest.qWait(30)

    # ---------------- thumbnails: centered at top, middle, bottom, at several panel widths
    pl = w.page_list
    def thumb_asym():
        vw = pl.viewport().width(); worst = 0
        for r in range(pl.count()):
            rect = pl.visualRect(pl.model().index(r, 0))
            if rect.bottom() >= 0 and rect.top() <= pl.viewport().height():
                worst = max(worst, abs(rect.left() - (vw - 1 - rect.right())), max(0, rect.right() - vw + 1), max(0, -rect.left()))
        return worst
    bad = []
    for width in (150, 230, 320):
        w.main_splitter.setSizes([width, 1280 - width]); QTest.qWait(250)
        bar = pl.verticalScrollBar()
        for value in (0, bar.maximum() // 3, bar.maximum() * 2 // 3, bar.maximum()):
            bar.setValue(value); QTest.qWait(220)
            if thumb_asym() > 2:
                bad.append((width, value, thumb_asym()))
    check("thumbnails centered while scrolling (3 widths x 4 positions)", not bad, bad)
    if CAPTURE:
        w.grab().save(str(OUT / "thumbs_scrolled_bottom.png"))
    w.main_splitter.setSizes([170, 1110]); QTest.qWait(250)

    # ---------------- preview centered on every page, several zoom levels and window widths
    def offset():
        vp = w.scroll_area.viewport(); pos = w.page_label.mapTo(vp, QPoint(0, 0)); c = w.preview_content_rect
        bar = w.scroll_area.horizontalScrollBar()
        page_center = pos.x() + c.center().x()
        if bar.maximum() > 0:  # wider than the window: the visible part must be the middle (scrollbar centered)
            return abs(bar.value() - bar.maximum() / 2)
        return abs(page_center - vp.width() / 2)
    bad = []
    for size in ((1280, 820), (1700, 900), (1040, 720)):
        w.resize(*size); QTest.qWait(200)
        for zf in (1.0, 0.5, 1.6, 2.5):
            w.zoom_factor = zf; w.update_zoom(); w.show_page(0); QTest.qWait(30)
            for page in range(0, 24, 3):
                w.go_to_page(page + 1); QTest.qWait(5)
                if offset() > 3:
                    bad.append((size, zf, page, round(offset(), 1)))
    check("preview page centered horizontally (3 sizes x 4 zooms x 8 pages)", not bad, bad[:5])
    w.resize(1280, 820); w.zoom_factor = 1.0; w.update_zoom(); w.show_page(0); QTest.qWait(100)

    # ---------------- plain wheel: one notch = one page; zoomed page scrolls first
    vp = w.scroll_area.viewport()
    wheel(vp, -120); QTest.qWait(20)
    check("wheel down: next page", w.current_page_index == 1)
    for _ in range(4):
        wheel(vp, -30)
    QTest.qWait(20)
    check("touchpad: 4 x 30 = one page (accumulated)", w.current_page_index == 2, w.current_page_index)
    w.zoom_factor = 2.0; w.update_zoom(); QTest.qWait(50); bar = w.scroll_area.verticalScrollBar(); bar.setValue(0)
    wheel(vp, -120); QTest.qWait(20)
    check("zoomed page: wheel scrolls inside the page first", w.current_page_index == 2 and bar.value() > 0)
    bar.setValue(bar.maximum()); wheel(vp, -120); QTest.qWait(20)
    check("zoomed page: at the bottom the wheel turns the page (shown from its top)", w.current_page_index == 3 and bar.value() == bar.minimum())
    wheel(vp, 120); QTest.qWait(20)
    check("zoomed page: wheel up at the top goes to previous page bottom", w.current_page_index == 2 and bar.value() == bar.maximum())
    w.zoom_factor = 1.0; w.update_zoom(); w.show_page(0); QTest.qWait(50)

    # ---------------- Ctrl+wheel zoom: instant, anchored, sharp render after the wheel stops
    w.zoom_factor = 2.0; w.update_zoom(); QTest.qWait(250)  # page wider than the window: the anchor can hold in x and y
    anchor = QPoint(vp.width() // 3, vp.height() // 3)
    def doc_point(p):
        lp = w.page_label.mapFrom(vp, p); c = w.preview_content_rect
        return ((lp.x() - c.left()) / c.width(), (lp.y() - c.top()) / c.height())
    before_pt = doc_point(anchor); renders = []
    original = w.render_page_pixmap
    w.render_page_pixmap = lambda ref, scale, *a, **k: (scale != w.thumbnail_scale and renders.append(scale), original(ref, scale, *a, **k))[1]
    t0 = time.perf_counter(); lat = []
    for _ in range(6):
        t = time.perf_counter(); wheel(vp, 120, Qt.ControlModifier, anchor); lat.append((time.perf_counter() - t) * 1000)
    QTest.qWait(10)
    check("ctrl+wheel: no PDF render per wheel step", len(renders) == 0, renders)
    check("ctrl+wheel: zoom applied immediately (6 steps)", abs(w.zoom_factor - 2.9) < 1e-6, w.zoom_factor)
    QTest.qWait(w.ZOOM_SETTLE_MS + 150)
    check("ctrl+wheel: one sharp render after the wheel stops", len(renders) == 1 and abs(renders[0] - w.zoom) < 1e-9, renders)
    after_pt = doc_point(anchor)
    drift = max(abs(after_pt[0] - before_pt[0]), abs(after_pt[1] - before_pt[1]))
    check("ctrl+wheel: point under the cursor stays put", drift < 0.01, f"drift={drift:.4f}")
    w.render_page_pixmap = original
    print(f"   ctrl+wheel per-event avg {sum(lat)/len(lat):.1f} ms, max {max(lat):.1f} ms")
    for _ in range(40):
        wheel(vp, -15, Qt.ControlModifier, anchor)
    QTest.qWait(w.ZOOM_SETTLE_MS + 150)
    check("hi-res wheel: 40 x 15 = 5 notches (not 40 steps)", abs(w.zoom_factor - (2.9 - 0.75)) < 1e-6, w.zoom_factor)
    for _ in range(60):
        wheel(vp, -120, Qt.ControlModifier, anchor)
    QTest.qWait(w.ZOOM_SETTLE_MS + 150)
    check("zoom clamps at minimum", abs(w.zoom_factor - w.ZOOM_MIN) < 1e-9)
    w.zoom_factor = 1.0; w.update_zoom(); QTest.qWait(30)
    check("back at 100%: page fits and is centered", offset() <= 3 and w.preview_content_rect.height() <= vp.height())

    # ---------------- page operations + undo/redo
    w.load_pdf(str(small)); QTest.qWait(150)
    w.page_list.setCurrentRow(0); w.copy_selected_pages(); w.paste_pages_after_current()
    check("copy/paste page", w.page_count == 4 and refs(w)[1][1] == 0)
    w.page_list.setCurrentRow(1); w.cut_selected_pages(); w.page_list.setCurrentRow(2); w.paste_pages_after_current()
    check("cut/paste moves page", w.page_count == 4 and [r[1] for r in refs(w)] == [0, 1, 2, 0], refs(w))
    w.undo(); w.undo(); check("undo x2", w.page_count == 4 and [r[1] for r in refs(w)] == [0, 0, 1, 2], refs(w))
    w.redo(); check("redo", w.page_count == 3, refs(w))
    w.move_pages_by_rows([2], 0); check("drag-reorder (move_pages_by_rows)", [r[1] for r in refs(w)] == [2, 0, 1], refs(w))
    w.page_list.setCurrentRow(0); w.rotate_selected_pages_right(); w.rotate_selected_pages_right(); w.rotate_selected_pages_left()
    check("rotate right/right/left = 90", refs(w)[0][2] == 90)
    Q["open"] = [str(many)]; Q["item"] = ["맨 뒤"]; w.insert_pdf(); check("insert PDF at end", w.page_count == 27)
    w.undo(); check("undo insert", w.page_count == 3)
    Q["open"] = [str(small)]; w.merge_pdfs(); check("merge PDF", w.page_count == 6)
    w.page_list.selectAll(); w.delete_selected_pages(); check("delete all pages -> empty preview", w.page_count == 0)
    ERRORS.clear(); w.next_page(); w.zoom_in(); w.rotate_current_page(90); wheel(vp, -120); wheel(vp, 120, Qt.ControlModifier); QTest.qWait(250)
    check("navigation/zoom on empty document: no error", not ERRORS, ERRORS[-1:])
    w.undo(); check("undo delete all", w.page_count == 6)
    w.page_spin.setValue(4); check("page box jumps to page", w.current_page_index == 3)
    w.page_list.setFocus(); QTest.keyClick(w.page_list, Qt.Key_Down); QTest.qWait(20)
    check("keyboard down in page list", w.current_page_index == 4, w.current_page_index)

    # ---------------- save (overwrite) after edits: file correct, view reloaded, second save does not double anything
    doc = WORK / "edit.pdf"; shutil.copy(many, doc)
    w.load_pdf(str(doc)); QTest.qWait(150)
    w.page_list.setCurrentRow(0); w.delete_selected_pages()
    w.page_list.setCurrentRow(0); w.rotate_selected_pages_right()
    w.add_approval_stamp_to_first_page()
    ERRORS.clear(); w.save_current(); QTest.qWait(80)
    reader = PdfReader(str(doc))
    check("save: 23 pages, first rotated 90", len(reader.pages) == 23 and reader.pages[0].rotation == 90, [p.rotation for p in reader.pages[:3]])
    check("save: view reloaded from saved file (no stale page refs)", refs(w)[0] == ("edit.pdf", 0, 0, 0) and refs(w)[-1][1] == 22 and w.page_count == 23, refs(w)[:2])
    w.show_page(22); QTest.qWait(30)
    check("save: last page renders after save (was PdfiumError)", not ERRORS and w.current_page_index == 22, ERRORS[-1:])
    size_before = doc.stat().st_size
    w.save_current(); reader = PdfReader(str(doc))
    check("second save: no double rotation / page shift", len(reader.pages) == 23 and reader.pages[0].rotation == 90 and abs(doc.stat().st_size - size_before) < size_before * 0.05, (reader.pages[0].rotation, size_before, doc.stat().st_size))
    w.undo(); check("undo after save does not resurrect stale refs", w.page_count == 23)

    # ---------------- save as / errors
    Q["save"] = [str(WORK / "copy_as")]; w.save_copy_as()
    check("save as adds .pdf and keeps the document", (WORK / "copy_as.pdf").is_file() and len(PdfReader(str(WORK / "copy_as.pdf")).pages) == 23 and w.current_pdf_path.endswith("copy_as.pdf"))
    check("window title follows save as", w.windowTitle() == "copy_as.pdf", w.windowTitle())
    blocker = WORK / "blocked"; blocker.write_text("x")
    MSG.clear(); ERRORS.clear(); Q["save"] = [str(blocker / "sub" / "x.pdf")]; w.save_copy_as()
    check("save as to an impossible path: message, no crash", not ERRORS and MSG and MSG[-1][0] == "critical", (ERRORS[-1:], MSG[-1:]))
    # a failed write must not truncate the target
    w.load_pdf(str(doc)); QTest.qWait(80); good = doc.read_bytes()
    orig_write = w.write_ordered_pdf
    def broken(path, refs_, password=None):
        Path(path).write_bytes(b"%PDF-partial"); raise RuntimeError("disk full")
    w.write_ordered_pdf = broken; MSG.clear(); w.save_current(); w.write_ordered_pdf = orig_write
    check("failed save keeps the original file intact", doc.read_bytes() == good and MSG and MSG[-1][0] == "critical")
    leftovers = [p.name for p in WORK.glob("tmp*.pdf")]
    check("failed save leaves no temp files", not leftovers, leftovers)

    # ---------------- passwords
    enc = WORK / "enc.pdf"; wr = PdfWriter(); [wr.add_page(p) for p in PdfReader(str(small)).pages]; wr.encrypt("pw-1"); wr.write(str(enc))
    Q["text"] = ["wrong", "pw-1"]; MSG.clear(); w.load_pdf(str(enc)); QTest.qWait(80)
    check("encrypted PDF: wrong password warns, right one opens", w.page_count == 3 and any(m[0] == "warning" for m in MSG))
    w.rotate_selected_pages_right(); w.save_current(); r = PdfReader(str(enc))
    check("save of encrypted PDF stays encrypted (was silently decrypted)", r.is_encrypted and r.decrypt("pw-1") != 0 and len(r.pages) == 3)
    Q["save"] = [str(WORK / "plain.pdf")]; w.save_pdf_without_password()
    check("암호 해제 저장 -> unencrypted copy", not PdfReader(str(WORK / "plain.pdf")).is_encrypted)
    Q["text"] = ["new-pw", "new-pw"]; Q["save"] = [str(WORK / "locked.pdf")]; w.save_pdf_with_password()
    r = PdfReader(str(WORK / "locked.pdf")); check("암호 설정 저장 -> encrypted copy", r.is_encrypted and r.decrypt("new-pw") != 0)
    Q["text"] = ["a", "b"]; MSG.clear(); w.save_pdf_with_password(); check("암호 확인 불일치 -> message", MSG and MSG[-1][0] == "critical")
    w.page_list.selectAll(); Q["save"] = [str(enc)]; w.save_pdf_without_password(); QTest.qWait(50)
    check("unlock-save over the open encrypted file reloads it unencrypted", not PdfReader(str(enc)).is_encrypted and w.page_count == 3 and not ERRORS)

    # ---------------- extract
    w.load_pdf(str(many)); QTest.qWait(80)
    Q["text"] = ["2-4, 10"]; Q["save"] = [str(WORK / "ext.pdf")]; w.extract_pages()
    check("extract pages 2-4,10", len(PdfReader(str(WORK / "ext.pdf")).pages) == 4 and w.page_count == 4)
    Q["text"] = ["0, 99"]; MSG.clear(); w.extract_pages(); check("extract invalid range -> message", MSG and MSG[-1][0] == "critical")

    # ---------------- print (to a PDF printer) renders every page
    w.load_pdf(str(small)); QTest.qWait(80)
    printer = QPrinter(QPrinter.ScreenResolution); printer.setOutputFormat(QPrinter.PdfFormat); printer.setOutputFileName(str(WORK / "printed.pdf"))
    tmp = WORK / "to_print.pdf"; w.write_ordered_pdf(str(tmp), w.ordered_page_refs()); w.print_pdf_file(str(tmp), printer)
    check("print: all pages sent to printer", len(PdfReader(str(WORK / "printed.pdf")).pages) == 3)

    # ---------------- broken / missing files
    bad_pdf = WORK / "broken.pdf"; bad_pdf.write_bytes(b"%PDF-1.4 not really")
    ERRORS.clear(); MSG.clear(); w.load_pdf(str(bad_pdf)); w.append_pdf_files([str(WORK / "missing.pdf")])
    check("broken / missing PDF: no crash, document kept", not ERRORS and w.page_count == 3, ERRORS[-1:])
    # opening into an empty window (file association / command line path): title shows the file, not undoable
    w2 = K.PdfPreviewer(); w2.show(); QTest.qWait(50); w2.append_pdf_files([str(small)]); QTest.qWait(50)
    check("open via 연결 프로그램/argv: pages shown, title has file name", w2.page_count == 3 and w2.windowTitle().endswith("small.pdf"), w2.windowTitle())
    w2.undo(); check("open via argv is not undoable (undo keeps the document)", w2.page_count == 3); w2.close()
    # drag & drop of a PDF file onto the window
    from PyQt5.QtCore import QMimeData, QUrl
    from PyQt5.QtGui import QDropEvent
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(small))])
    ev = QDropEvent(QPointF(400, 300), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    w.dropEvent(ev); QTest.qWait(80)
    check("drop PDF file onto window appends pages", w.page_count == 6, w.page_count)

    # ---------------- rendering speed path gives the same pixels as the old PIL path
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(str(small)) as d:
        fast = K.pdfium_page_to_qimage(d[0], 0.5, 90)
        im = d[0].render(scale=0.5, rotation=90).to_pil().convert("RGBA")
        old = QImage(im.tobytes("raw", "RGBA"), im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()
    same = fast.size() == old.size() and all(fast.pixel(x, y) == old.pixel(x, y) for x in range(0, fast.width(), 37) for y in range(0, fast.height(), 41))
    check("fast render path = old render path (pixels)", same)
    if CAPTURE:
        w.load_pdf(str(many)); w.main_splitter.setSizes([190, 1090]); QTest.qWait(300)
        w.page_list.verticalScrollBar().setValue(w.page_list.verticalScrollBar().maximum()); w.show_page(23); QTest.qWait(300)
        w.grab().save(str(OUT / "window_last_page.png"))
    check("no unhandled exceptions overall", not ERRORS, ERRORS[-1:])
    w.close()


try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False))
finally:
    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0 if passed == len(RESULTS) else 1)

"""Koinfra PDF 1.3.2 - document/page cache, thumbnail resolution, render-cache safety. Test PDFs only, temp APPDATA.
usage: python perf_cache_test.py <project_dir> <out_dir>"""
import importlib.util, json, os, shutil, sys, tempfile, time, traceback, types
from pathlib import Path

PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="koinfra_cache_"))
os.environ["APPDATA"] = str(WORK / "appdata"); os.environ["KOINFRA_NO_REGISTER"] = "1"
if sys.platform != "win32":
    sys.modules["winreg"] = types.ModuleType("winreg")
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("Koinfra", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)
import kpdf_core as C

from PIL import Image, ImageDraw
from PyQt5.QtCore import QRectF
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox

RESULTS, ERRORS = [], []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok))); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def make_pdf(path, pages, color=(30, 64, 175)):
    images = []
    for i in range(pages):
        im = Image.new("RGB", (1240, 1754), "white"); d = ImageDraw.Draw(im)
        d.rectangle([90, 110, 1150, 300], fill=color)
        for row in range(30):
            d.line([(120, 420 + row * 40), (1100 - (row % 4) * 120, 420 + row * 40)], fill=(60, 60, 60), width=5)
        d.rectangle([100 + i * 20, 1500, 300 + i * 20, 1650], fill=(200, 30, 30))
        images.append(im)
    images[0].save(path, "PDF", resolution=150.0, save_all=True, append_images=images[1:])


def main():
    sys.excepthook = lambda t, v, tb: ERRORS.append("".join(traceback.format_exception(t, v, tb)))
    app = QApplication.instance() or QApplication(sys.argv[:1])
    for n in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.No))

    # ---------------- PdfSourceCache
    a = WORK / "a.pdf"; make_pdf(a, 5)
    cache = C.PdfSourceCache(max_documents=2, max_pages=3)
    with cache.document(a) as d1:
        n1 = len(d1)
    with cache.document(a) as d2:
        pass
    check("same document reused (no re-open)", d1 is d2 and n1 == 5)
    with cache.page(a, 1) as p1:
        pass
    with cache.page(a, 1) as p2:
        pass
    check("same page reused (no re-parse)", p1 is p2)
    for i in range(5):
        with cache.page(a, i):
            pass
    check("page cache bounded", cache.stats()["pages"] == 3, cache.stats())
    rc = C.PdfSourceCache(recycle_after_pages=4)
    with rc.document(a) as first:
        pass
    for i in range(5):
        with rc.page(a, i):
            pass
    with rc.page(a, 0) as pg:
        recycled_size = pg.get_size()
    with rc.document(a) as second:
        n_second = len(second)
    check("document re-created after N page loads (bounded pdfium memory), still works", second is not first and n_second == 5 and recycled_size[0] > 0)
    # file is not kept open: it can be replaced / deleted while cached (Windows locks open files)
    b = WORK / "b.pdf"; make_pdf(b, 3, (10, 120, 40))
    try:
        replaced = WORK / "a_copy.pdf"; shutil.copyfile(a, replaced)
        os.replace(b, a); ok_replace = True
    except OSError as exc:
        ok_replace = exc
    check("cached file can be replaced on disk (not locked)", ok_replace is True, ok_replace)
    with cache.document(a) as d3:
        n3 = len(d3)
    check("changed file is read again (3 pages now)", n3 == 3 and d3 is not d1, n3)
    with cache.page(a, 2) as p:
        w_, h_ = p.get_size()
    check("pages come from the new file", w_ > 0 and h_ > 0)
    try:
        os.remove(a); removed = True
    except OSError as exc:
        removed = exc
    check("cached file can be deleted", removed is True, removed)
    os.replace(replaced, a)
    for k in range(3):
        f = WORK / f"m{k}.pdf"; make_pdf(f, 1)
        with cache.document(f):
            pass
    check("document cache bounded", cache.stats()["documents"] == 2, cache.stats())
    cache.invalidate(); check("invalidate() empties", cache.stats() == {"documents": 0, "pages": 0, "bytes": 0})
    big = C.PdfSourceCache(max_file_bytes=0)
    with big.page(a, 0) as p:
        size0 = p.get_size()
    check("very large files: opened per use, not kept", big.stats()["documents"] == 0 and size0[0] > 0)
    # password
    enc = WORK / "enc.pdf"
    from pypdf import PdfReader, PdfWriter
    wr = PdfWriter(); [wr.add_page(pg) for pg in PdfReader(str(a)).pages]; wr.encrypt(user_password="pw1", owner_password="pw1")
    with open(enc, "wb") as fh:
        wr.write(fh)
    try:
        with cache.document(enc, "wrong"):
            pass
        wrong_ok = False
    except Exception:
        wrong_ok = True
    check("wrong password raises, nothing cached", wrong_ok and cache.stats()["documents"] == 0, cache.stats())
    with cache.document(enc, "pw1") as d:
        check("right password opens", len(d) == 5)

    # ---------------- in the window
    doc = WORK / "doc.pdf"; make_pdf(doc, 12)
    w = K.PdfPreviewer(); w.resize(1280, 820); w.show(); QTest.qWait(300)
    w.load_pdf(str(doc)); QTest.qWait(500)
    ref = w.page_list.item(0).data(K.PAGE_ROLE)
    pm = w.render_page_pixmap(ref, 1.0)
    cached = w.page_render_cache[(ref.path, 0, 0, 1.0)]
    before = cached.toImage().pixel(50, 50)
    from PyQt5.QtGui import QPainter, QColor
    painter = QPainter(pm); painter.fillRect(0, 0, 200, 200, QColor("#ff00ff")); painter.end()
    after = w.page_render_cache[(ref.path, 0, 0, 1.0)].toImage().pixel(50, 50)
    check("painting on a rendered page never changes the render cache", before == after and pm.toImage().pixel(50, 50) != before)
    w.selected_mark_index = None
    w.preview_drag_rect = QRectF(0.1, 0.1, 0.3, 0.3); w.show_page(0); w.preview_drag_rect = None
    clean = w.render_page_pixmap(ref, w.zoom).toImage()
    w.show_page(0)
    check("drag frame does not stick to the cached page", w.preview_page_pixmap.toImage() == clean)

    w.thumbnail_cache.clear(); w.refresh_thumbnail_icons()
    keys = list(w.page_render_cache)
    check("thumbnails are not stored in the preview render cache", all(abs(k[3] - w.zoom) < 0.01 or k[3] == 1.0 for k in keys), keys)
    thumb = w.render_thumbnail_pixmap(ref)
    iw, ih = w.thumbnail_item_width, w.thumbnail_item_height
    check(f"thumbnail rendered at its shown size ({thumb.width()}x{thumb.height()} in {iw}x{ih})",
          thumb.width() <= iw + 1 and thumb.height() <= ih + 1 and (abs(thumb.width() - iw) <= 2 or abs(thumb.height() - ih) <= 2), (thumb.width(), thumb.height()))
    total = sum(w.main_splitter.sizes()); w.main_splitter.setSizes([560, total - 560]); QTest.qWait(500)
    thumb_wide = w.render_thumbnail_pixmap(ref)
    check("wide grid cells get sharper (bigger) thumbnails", thumb_wide.width() >= thumb.width(), (thumb.width(), thumb_wide.width()))
    # marks show on thumbnails
    marked = K.PageRef(ref.path, 0, 0, (K.PageMark("rect", (0.1, 0.1, 0.9, 0.5)),))
    plain_img = w.render_thumbnail_pixmap(ref).toImage(); marked_img = w.render_thumbnail_pixmap(marked).toImage()
    check("marks drawn on thumbnails", plain_img != marked_img)

    # rendering speed: second render of a page (document + page cached) is faster than a cold one
    C.PDF_SOURCES.invalidate(); w.page_render_cache.clear()
    t = time.perf_counter(); w.render_page_pixmap(w.page_list.item(3).data(K.PAGE_ROLE), 1.2); cold = time.perf_counter() - t
    w.page_render_cache.clear()
    t = time.perf_counter(); w.render_page_pixmap(w.page_list.item(3).data(K.PAGE_ROLE), 1.2); warm = time.perf_counter() - t
    print(f"   render cold {cold*1000:.1f} ms, warm {warm*1000:.1f} ms")
    check("warm render not slower than cold", warm <= cold * 1.2 + 0.002, (cold, warm))

    # save over the source -> cache dropped, new content shown
    w.rotate_page_rows([0], 90); QTest.qWait(100)
    ok = w.write_pdf_safely(str(doc), w.ordered_page_refs(), "t"); w.reload_if_overwritten(str(doc)); QTest.qWait(300)
    r0 = w.page_list.item(0).data(K.PAGE_ROLE)
    wpt, hpt = w.page_display_size(r0)
    check("saved over the source: reloaded from the new file (rotation baked in)", ok and r0.rotation == 0 and wpt > hpt, (r0.rotation, wpt, hpt))

    # opening another file releases the previous one
    other = WORK / "other.pdf"; make_pdf(other, 2)
    w.load_pdf(str(other)); QTest.qWait(300)
    paths = {k[0] for k in C.PDF_SOURCES._documents}
    check("opening another PDF releases the previous document", str(doc) not in paths and any(p.endswith("other.pdf") for p in paths), paths)
    w.close(); QTest.qWait(50)
    check("no uncaught exceptions", not ERRORS, ERRORS[:1])


try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False))
finally:
    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / "perf_cache_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        C.PDF_SOURCES.invalidate()
    except Exception:
        pass
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0 if passed == len(RESULTS) else 1)

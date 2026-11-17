"""Koinfra dynamic GUI test (QTest based). Usage: python dyn_test.py <project_dir> <out_dir> [--capture]"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
import types
from pathlib import Path

PROJECT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
CAPTURE = "--capture" in sys.argv
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="koinfra_test_"))
os.environ["APPDATA"] = str(WORK / "appdata")  # isolate preferences/stamps/signatures
if sys.platform != "win32":
    sys.modules["winreg"] = types.ModuleType("winreg")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

spec = importlib.util.spec_from_file_location("Koinfra", PROJECT / "Koinfra.py")
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def pump(ms=120):
    QTest.qWait(ms)


def make_sample_pdf(path):
    pages = []
    try:
        font = ImageFont.truetype("malgun.ttf", 28)
    except OSError:
        font = ImageFont.load_default(size=28)
    for number, size in enumerate([(1240, 1754), (1240, 1754), (1754, 1240)], start=1):
        page = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle([60, 60, size[0] - 60, size[1] - 60], outline=(30, 64, 175), width=4)
        for row in range(0, size[1] - 200, 90):
            draw.line([(80, 200 + row), (size[0] - 80, 200 + row)], fill=(15, 15, 15), width=6)
        for row in range(24):
            draw.text((120, 140 + row * 52), f"Page {number} line {row + 1} - Koinfra sample text", fill=(20, 20, 20), font=font)
        pages.append(page)
    pages[0].save(path, "PDF", resolution=150.0, save_all=True, append_images=pages[1:])


def make_stamp_png(path):
    image = Image.new("RGB", (300, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse([20, 20, 280, 280], outline=(220, 30, 30), width=18)
    draw.rectangle([100, 120, 200, 180], fill=(220, 30, 30))
    image.save(path)


def grab(widget, name):
    if CAPTURE:
        widget.grab().save(str(OUT / name))


def send_drag(widget, points):
    def ev(kind, point, buttons):
        return QMouseEvent(kind, QPointF(point), Qt.LeftButton, buttons, Qt.NoModifier)

    QApplication.sendEvent(widget, ev(QEvent.MouseButtonPress, points[0], Qt.LeftButton))
    for point in points[1:]:
        QApplication.sendEvent(widget, ev(QEvent.MouseMove, point, Qt.LeftButton))
    QApplication.sendEvent(widget, ev(QEvent.MouseButtonRelease, points[-1], Qt.NoButton))


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    sample = WORK / "sample.pdf"
    stamp_png = WORK / "seal.jpg"
    make_sample_pdf(sample)
    Image.open(str(WORK / "sample.pdf")) if False else None
    tmp_png = WORK / "seal_src.png"
    make_stamp_png(tmp_png)
    Image.open(tmp_png).convert("RGB").save(stamp_png, "JPEG", quality=95)

    icon = K.load_app_icon()
    check("app icon loads (multi-size ICO)", not icon.isNull() and len(icon.availableSizes()) >= 8, str(len(icon.availableSizes())))
    for key in ["open", "save", "stamp", "signature", "pen_line", "settings", "image_plus", "approval_stamp"]:
        check(f"tool icon file set: {key}", (K.TOOL_ICON_DIR / f"{key}_28.png").is_file() and not K.make_tool_icon(key).isNull())

    window = K.PdfPreviewer()
    window.setWindowIcon(icon)
    window.resize(1280, 800)
    window.show()
    pump(400)

    toolbar_labels = [a.text() for _, acts, _ in window.toolbar_groups for a in acts]
    check("toolbar has separate 도장 / 결재", "도장" in toolbar_labels and "결재" in toolbar_labels, ",".join(toolbar_labels))
    expected_groups = [
        ("파일", ["열기", "저장", "인쇄"]),
        ("페이지", ["병합", "추출", "삽입"]),
        ("보안", ["암호", "해제"]),
        ("주석", ["사각형", "형광펜"]),
        ("서명", ["도장", "결재"]),
        ("보기", ["우회전", "좌회전", "이전", "다음", "축소", "확대"]),
    ]
    actual_groups = [(name, [a.text() for a in acts]) for name, acts, _ in window.toolbar_groups]
    check("toolbar groups + order", actual_groups == expected_groups, str(actual_groups))
    check("toolbar group captions shown", all(cap.isVisible() and cap.text() == name for name, _, cap in window.toolbar_groups))
    tb_widgets = [window.findChildren(K.QToolBar)[0].widgetForAction(a) for a in window.findChildren(K.QToolBar)[0].actions()]
    check("page spin box after last group", window.page_spin in tb_widgets and tb_widgets.index(window.page_spin) > tb_widgets.index(window.toolbar_groups[-1][2].parentWidget()))

    window.load_pdf(str(sample))
    pump(500)
    check("open sample PDF (3 pages)", window.page_count == 3, str(window.page_count))

    # ---------------- centering
    def centered_state():
        viewport = window.scroll_area.viewport()
        label = window.page_label
        pos = label.mapTo(viewport, QPoint(0, 0))
        content = window.preview_content_rect
        page_left = pos.x() + content.left()
        page_top = pos.y() + content.top()
        dx = abs((page_left + content.width() / 2) - viewport.width() / 2)
        dy = abs((page_top + content.height() / 2) - viewport.height() / 2)
        fits = content.width() <= viewport.width() and content.height() <= viewport.height()
        return dx, dy, fits

    dx, dy, fits = centered_state()
    check("preview: whole page visible at 100%", fits)
    check("preview: page centered (h/v within 3px)", dx <= 3 and dy <= 3, f"dx={dx:.1f} dy={dy:.1f}")
    grab(window, "main_centered.png")

    window.next_page(); pump()
    check("next page", window.current_page_index == 1)
    window.next_page(); pump()
    dx, dy, fits = centered_state()
    check("landscape page centered + fits", fits and dx <= 3 and dy <= 3, f"dx={dx:.1f} dy={dy:.1f}")
    window.previous_page(); window.previous_page(); pump()
    check("previous page", window.current_page_index == 0)

    zoom_before = window.zoom
    window.zoom_in(); window.zoom_in(); window.zoom_in(); pump(300)
    hbar = window.scroll_area.horizontalScrollBar()
    vbar = window.scroll_area.verticalScrollBar()
    check("zoom in increases zoom", window.zoom > zoom_before, f"{zoom_before:.2f}->{window.zoom:.2f}")
    h_ratio = hbar.value() / hbar.maximum() if hbar.maximum() else 0.5
    v_ratio = vbar.value() / vbar.maximum() if vbar.maximum() else 0.5
    check("zoom in keeps view centered", abs(h_ratio - 0.5) < 0.08 and abs(v_ratio - 0.5) < 0.08, f"h={h_ratio:.2f} v={v_ratio:.2f}")
    window.zoom_out(); window.zoom_out(); window.zoom_out(); pump(300)
    dx, dy, fits = centered_state()
    check("zoom out back to centered fit", fits and dx <= 3 and dy <= 3 and abs(window.zoom - zoom_before) < 0.01)

    # Ctrl+wheel anchor: point under cursor must stay put (bug fix check)
    for _ in range(5):
        window.zoom_in()
    pump(200)
    window.scroll_area.horizontalScrollBar().setValue(window.scroll_area.horizontalScrollBar().maximum() // 2)
    window.scroll_area.verticalScrollBar().setValue(window.scroll_area.verticalScrollBar().maximum() // 2)
    anchor = QPoint(window.scroll_area.viewport().width() // 3, window.scroll_area.viewport().height() // 3)
    before_label = window.page_label.mapFrom(window.scroll_area.viewport(), anchor)
    ratio_before = (before_label.x() / window.page_label.width(), before_label.y() / window.page_label.height())
    window.zoom_in(anchor); pump(200)
    after_label = window.page_label.mapFrom(window.scroll_area.viewport(), anchor)
    ratio_after = (after_label.x() / window.page_label.width(), after_label.y() / window.page_label.height())
    # Only axes that can scroll can keep the anchor; a narrower page stays centered horizontally.
    axes = []
    if window.scroll_area.horizontalScrollBar().maximum() > 0:
        axes.append(abs(ratio_before[0] - ratio_after[0]))
    if window.scroll_area.verticalScrollBar().maximum() > 0:
        axes.append(abs(ratio_before[1] - ratio_after[1]))
    drift = max(axes) if axes else 0.0
    check("ctrl+wheel zoom keeps cursor anchor", drift < 0.01, f"drift={drift:.3f}")
    window.zoom_factor = 1.0; window.update_zoom(); pump(200)

    window.rotate_selected_pages_right(); pump()
    check("rotate right", window.page_list.item(0).data(K.PAGE_ROLE).rotation == 90)
    dx, dy, fits = centered_state()
    check("rotated page centered + fits", fits and dx <= 3 and dy <= 3)
    window.rotate_selected_pages_left(); pump()
    check("rotate left", window.page_list.item(0).data(K.PAGE_ROLE).rotation == 0)

    # v3: thumbnail panel fits the viewport, centered, no clipping (several widths, incl. rotation)
    def thumb_state():
        pump(450)
        lst = window.page_list
        vw = lst.viewport().width()
        worst_over, worst_asym, text_off = 0, 0, 0
        for r in range(lst.count()):
            rect = lst.visualRect(lst.model().index(r, 0))
            worst_over = max(worst_over, rect.right() - (vw - 1), -rect.left())
            worst_asym = max(worst_asym, abs(rect.left() - (vw - 1 - rect.right())))
        text_off = 0 if int(lst.model().index(0, 0).data(Qt.TextAlignmentRole) or 0) & int(Qt.AlignHCenter) or (lst.viewMode() == lst.IconMode and int(lst.viewOptions().displayAlignment) & int(Qt.AlignHCenter)) else 99
        return vw, worst_over, worst_asym, lst.horizontalScrollBar().maximum(), text_off
    orig_sizes = window.main_splitter.sizes()
    total = sum(orig_sizes)
    for width in (140, 170, 200, 260, 340, 460):
        window.main_splitter.setSizes([width, total - width])
        vw, over, asym, hmax, text_off = thumb_state()
        check(f"thumbnails fit + centered @panel {width}", over <= 0 and asym <= 2 and hmax == 0 and text_off == 0,
              f"vp={vw} over={over} asym={asym} hscroll={hmax}")
    window.page_list.setCurrentRow(0); window.rotate_selected_pages_right(); pump()
    window.main_splitter.setSizes([200, total - 200])
    vw, over, asym, hmax, _ = thumb_state()
    check("rotated thumbnails fit + centered", over <= 0 and asym <= 2 and hmax == 0, f"over={over} asym={asym}")
    window.rotate_selected_pages_left(); pump()
    old_size = window.size()
    window.resize(1000, 640)
    vw, over, asym, hmax, _ = thumb_state()
    check("thumbnails fit after window resize", over <= 0 and asym <= 2 and hmax == 0, f"over={over} asym={asym}")
    check("thumbnail vertical scrollbar fixed (no width toggle)", window.page_list.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOn)
    window.resize(old_size)
    window.main_splitter.setSizes(orig_sizes); pump(400)

    # ---------------- right-click menus on toolbar buttons
    toolbar = window.findChildren(K.QToolBar)[0]
    for attr, name, expected in (
        ("stamp_toolbar_action", "menu_stamp_rightclick.png", "도장 설정"),
        ("approval_toolbar_action", "menu_approval_rightclick.png", "서명 등록"),
    ):
        button = window.toolbar_buttons[getattr(window, attr)]
        seen = {}

        def close_popup(name=name, seen=seen):
            popup = QApplication.activePopupWidget()
            seen["texts"] = [a.text() for a in popup.actions()] if popup else []
            if popup:
                if CAPTURE:
                    shot = window.grab()
                    from PyQt5.QtGui import QPainter
                    painter = QPainter(shot)
                    painter.drawPixmap(window.mapFromGlobal(popup.mapToGlobal(QPoint(0, 0))), popup.grab())
                    painter.end()
                    shot.save(str(OUT / name))
                popup.close()

        QTimer.singleShot(400, close_popup)
        from PyQt5.QtGui import QContextMenuEvent
        QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(10, 10), button.mapToGlobal(QPoint(10, 10))))
        pump(100)
        check(f"right-click menu on {attr}", any(expected in t for t in seen.get("texts", [])), " | ".join(seen.get("texts", [])))

    # ---------------- stamp: first click without settings -> settings dialog
    def drive_stamp_dialog():
        dialog = window.stamp_settings_dialog
        if dialog is None:
            check("stamp settings dialog opened", False)
            return
        stored = window.store_png_stamp(str(stamp_png), remove_background=True)
        dialog.reload_images(stored)
        dialog.size_spin.setValue(20)
        dialog.opacity_spin.setValue(85)
        dialog.position_combo.setCurrentIndex(dialog.position_combo.findData("bottom_right"))
        pump(100)
        grab(dialog, "dialog_stamp_settings.png")
        check("stamp settings dialog opened", True)
        dialog.save_insert_button.click()

    QTimer.singleShot(500, drive_stamp_dialog)
    window.insert_default_stamp()
    pump(300)
    marks = window.page_list.item(0).data(K.PAGE_ROLE).marks
    stamp_marks = [m for m in marks if m.kind == "image_stamp"]
    check("stamp inserted once via '저장 후 삽입'", len(stamp_marks) == 1, str([m.kind for m in marks]))
    if stamp_marks:
        m = stamp_marks[0]
        check("stamp uses opacity 85 + bottom-right", len(m.data) > 1 and m.data[1] == 85 and m.rect[2] > 0.9 and m.rect[3] > 0.9, str(m))
        width = m.rect[2] - m.rect[0]
        check("stamp width = 20% of page", abs(width - 0.20) < 0.005, f"{width:.3f}")
    stored_png = Path(window.stamp_settings["image"])
    with Image.open(stored_png) as img:
        corner_alpha = img.convert("RGBA").getpixel((2, 2))[3]
    check("JPG stamp converted to PNG + white made transparent", stored_png.suffix == ".png" and corner_alpha == 0)

    # second click: inserts directly, no dialog
    opened = {"dialog": False}

    def fail_if_dialog():
        if window.stamp_settings_dialog is not None:
            opened["dialog"] = True
            window.stamp_settings_dialog.reject()

    QTimer.singleShot(300, fail_if_dialog)
    window.page_list.setCurrentRow(1); pump()
    window.stamp_settings["position"] = "center"
    K.save_stamp_settings(window.stamp_settings)
    window.insert_default_stamp()
    pump(400)
    check("2nd stamp click inserts directly (no dialog)", not opened["dialog"] and any(m.kind == "image_stamp" for m in window.page_list.item(1).data(K.PAGE_ROLE).marks))

    # ---------------- approval + signature
    def drive_signature_dialog():
        dialog = window.signature_dialog
        if dialog is None:
            check("signature dialog opened", False)
            return
        check("signature dialog opened", True)
        pad = dialog.pad
        w, h = pad.width(), pad.height()
        stroke1 = [QPoint(int(w * (0.15 + i * 0.02)), int(h * (0.6 - 0.25 * ((i % 6) / 5)))) for i in range(30)]
        stroke2 = [QPoint(int(w * (0.3 + i * 0.015)), int(h * (0.35 + i * 0.012))) for i in range(30)]
        send_drag(pad, stroke1)
        send_drag(pad, stroke2)
        dialog.line_edit.setText("담당, 팀장, 부서장")
        dialog.cell_combo.setCurrentIndex(0)
        pump(100)
        grab(dialog, "dialog_signature.png")
        check("signature pad has strokes", len(pad.strokes) == 2)
        dialog.save_button.click()

    window.page_list.setCurrentRow(0); pump()
    QTimer.singleShot(500, drive_signature_dialog)
    window.insert_signed_approval()
    pump(400)
    marks = window.page_list.item(0).data(K.PAGE_ROLE).marks
    signed = [m for m in marks if m.kind == "approval_signed"]
    check("결재(서명) mark inserted on first page", len(signed) == 1, str([m.kind for m in marks]))
    sig_path = window.signature_settings.get("image")
    check("signature PNG saved in profile", bool(sig_path) and Path(sig_path).is_file() and str(WORK) in sig_path)
    check("approval line saved", window.approval_line == ("담당", "팀장", "부서장"), str(window.approval_line))
    # stamp inserted on a page that already has the approval table must not cover it
    window.stamp_settings["position"] = "bottom_right"
    window.page_list.setCurrentRow(0); pump()
    window.insert_default_stamp(); pump(200)
    marks0 = window.page_list.item(0).data(K.PAGE_ROLE).marks
    new_stamp, table = marks0[-1], [m for m in marks0 if m.kind == "approval_signed"][0]
    check("new stamp placed above approval table (no overlap)", new_stamp.kind == "image_stamp" and new_stamp.rect[3] <= table.rect[1] + 1e-6, f"{new_stamp.rect[3]:.3f} <= {table.rect[1]:.3f}")
    window.selected_mark_index = None
    window.show_page(0); pump(200)
    grab(window, "main_with_stamp_and_approval.png")
    K.save_stamp_settings(window.stamp_settings)

    # reload settings from disk (persistence)
    prefs = json.loads((Path(os.environ["APPDATA"]) / K.APP_TITLE / "preferences.json").read_text(encoding="utf-8"))
    check("preferences.json keeps all sections", all(k in prefs for k in ("approval_line", "stamp_images", "stamp_settings", "signature")), ",".join(prefs))
    check("reload stamp settings", K.load_stamp_settings() == window.stamp_settings)
    check("reload signature settings", K.load_signature_settings() == window.signature_settings)
    window2 = K.PdfPreviewer()
    check("new window picks up saved settings", window2.stamp_settings == window.stamp_settings and window2.approval_line == window.approval_line)
    window2.close()

    # plain approval (no signature) still works
    window.add_approval_stamp_to_first_page(); pump()
    check("결재칸(서명 없이) still works", any(m.kind == "approval" for m in window.page_list.item(0).data(K.PAGE_ROLE).marks))
    window.undo(); pump()
    check("undo removes last mark", not any(m.kind == "approval" for m in window.page_list.item(0).data(K.PAGE_ROLE).marks))

    # ---------------- save + verify output
    from pypdf import PdfReader
    import pypdfium2 as pdfium

    out_pdf = WORK / "out" / "result.pdf"
    window.write_ordered_pdf(str(out_pdf), window.ordered_page_refs())
    reader = PdfReader(str(out_pdf))
    check("saved PDF opens, 3 pages", len(reader.pages) == 3)
    page0 = reader.pages[0]
    annots = [a.get_object() for a in page0.get("/Annots", [])]
    subtypes = [str(a.get("/Subtype")) for a in annots]
    check("approval table drawn in page content (no appearance-less Line/FreeText annots)", "/Line" not in subtypes and "/FreeText" not in subtypes, str(subtypes))

    def images_with_smask(page):
        found = []
        def walk(resources):
            xobjects = resources.get("/XObject") if resources else None
            if not xobjects:
                return
            for _, ref in xobjects.get_object().items():
                obj = ref.get_object()
                if obj.get("/Subtype") == "/Image":
                    found.append("/SMask" in obj)
                elif obj.get("/Subtype") == "/Form":
                    walk(obj.get("/Resources"))
        walk(page.get("/Resources"))
        return found

    imgs = images_with_smask(page0)
    check("stamp + signature images embedded on page 1 (with transparency)", len([x for x in imgs if x]) >= 2, str(imgs))
    check("page 2 stamp embedded", len(images_with_smask(reader.pages[1])) >= 2, str(images_with_smask(reader.pages[1])))

    doc = pdfium.PdfDocument(str(out_pdf))
    bitmap = doc[0].render(scale=1.5, may_draw_forms=True, draw_annots=True).to_pil().convert("RGB")
    bitmap.save(str(OUT / "saved_page1_render.png"))
    page2 = doc[1].render(scale=1.5).to_pil().convert("RGB")
    page2.save(str(OUT / "saved_page2_render.png"))
    m2 = [m for m in window.page_list.item(1).data(K.PAGE_ROLE).marks if m.kind == "image_stamp"][0]
    W, H = page2.size
    box = (int(m2.rect[0] * W), int(m2.rect[1] * H), int(m2.rect[2] * W), int(m2.rect[3] * H))
    region = page2.crop(box)
    px = list(region.get_flattened_data() if hasattr(region, "get_flattened_data") else region.getdata())
    reds = sum(1 for p in px if p[0] > 150 and p[1] < 130 and p[2] < 130)
    darks = sum(1 for p in px if p[0] < 90 and p[1] < 90 and p[2] < 90)
    check("stamp visible (red pixels) in rendered PDF", reds > 200, f"red={reds}")
    check("text under stamp still visible (transparent, no white box)", darks > 100, f"dark={darks}")
    sig_rect = K.approval_signature_cell_rect(signed[0].rect, ("담당", "팀장", "부서장"), 0)
    W1, H1 = bitmap.size
    sig_region = bitmap.crop((int(sig_rect[0] * W1), int(sig_rect[1] * H1), int(sig_rect[2] * W1), int(sig_rect[3] * H1)))
    navy = sum(1 for p in (sig_region.get_flattened_data() if hasattr(sig_region, "get_flattened_data") else sig_region.getdata()) if p[2] > p[0] + 20 and p[0] < 120)
    check("signature drawn inside 담당 cell in PDF", navy > 50, f"navy={navy}")
    doc.close()

    # save_current to the opened (temp copy) path
    copy_path = WORK / "edit_target.pdf"
    shutil.copy2(sample, copy_path)
    window.current_pdf_path = str(copy_path)
    window.save_current(); pump()
    check("save_current writes file", len(PdfReader(str(copy_path)).pages) == 3)

    # delete selected mark
    window.page_list.setCurrentRow(1); pump()
    window.selected_mark_index = 0
    window.delete_selected_mark(); pump()
    check("delete selected mark", len(window.page_list.item(1).data(K.PAGE_ROLE).marks) == 0)

    window.page_list.setCurrentRow(0); pump()
    window.zoom_factor = 1.0; window.update_zoom(); pump(300)
    grab(window, "main_final.png")
    window.close()
    pump(100)


try:
    main()
except Exception:
    traceback.print_exc()
    RESULTS.append(("unhandled exception", False, ""))
finally:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / "dyn_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    QApplication.instance() and QApplication.instance().quit()
    sys.exit(0 if passed == len(RESULTS) else 1)

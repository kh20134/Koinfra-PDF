"""변환 탭: PDF -> 엑셀(.xlsx) / 한글(.hwpx, 한컴 한글이 있으면 .hwp도) / 이미지(PNG·JPG).
Test PDFs are generated here (Korean text, ruled tables with a merged header cell, a text-only page, a scanned page).
usage: python convert_test.py <project_dir> <out_dir>   (uses a temp APPDATA; opens Hancom Office hidden if installed)"""
import importlib.util, json, os, shutil, sys, tempfile, traceback, types, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="koinfra_convert_test_"))
os.environ["APPDATA"] = str(WORK / "appdata")
if sys.platform != "win32":
    import mimetypes; mimetypes.init()  # before the winreg stub (mimetypes reads the registry when it exists)
    sys.modules["winreg"] = types.ModuleType("winreg")
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("Koinfra", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)
import kpdf_convert as conv  # noqa: E402
import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

RESULTS = []
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def ruled_table(page, x0, y0, col_w, row_h, rows, merged_header=None):
    """Draw a table with full grid lines; merged_header=(text, first_col, last_col) spans the first row."""
    xs = [x0]
    for w in col_w:
        xs.append(xs[-1] + w)
    ys = [y0 + i * row_h for i in range(len(rows) + 1)]
    shape = page.new_shape()
    for y in ys:
        shape.draw_line((xs[0], y), (xs[-1], y))
    for i, x in enumerate(xs):
        if merged_header and merged_header[1] < i <= merged_header[2]:
            shape.draw_line((x, ys[1]), (x, ys[-1]))  # no line inside the merged header cell
        else:
            shape.draw_line((x, ys[0]), (x, ys[-1]))
    shape.finish(color=(0, 0, 0), width=0.8); shape.commit()
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text is None:
                continue
            page.insert_text((xs[c] + 4, ys[r] + row_h - 7), text, fontname="korea", fontsize=10)


def make_sample(path):
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((60, 70), "2026년 시설 점검 결과 보고", fontname="korea", fontsize=20)
    p1.insert_text((60, 100), "작성: 정보보안팀    일자: 2026-11-25", fontname="korea", fontsize=10)
    p1.insert_text((60, 120), "아래 표는 분기별 점검 건수입니다.", fontname="korea", fontsize=10)
    rows = [["구분", "점검 결과", None, "비고"],
            ["항목", "1분기", "2분기", ""],
            ["CCTV", "1,234", "12.5", "정상"],
            ["출입통제", "007", "45", "보완 필요"]]
    ruled_table(p1, 60, 140, [120, 90, 90, 160], 24, rows, merged_header=("점검 결과", 1, 2))
    p1.insert_text((60, 290), "표 아래 문단: 끝.", fontname="korea", fontsize=10)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((60, 70), "2쪽 제목", fontname="korea", fontsize=16)
    for i in range(5):
        p2.insert_text((60, 100 + i * 18), f"{i + 1}. 텍스트 줄 <특수&문자> \"인용\" {i}", fontname="korea", fontsize=10)
    p2.insert_text((60, 220), "왼쪽 칸", fontname="korea", fontsize=10)
    p2.insert_text((360, 220), "오른쪽 칸", fontname="korea", fontsize=10)
    p3 = doc.new_page(width=842, height=595)  # landscape scanned page: only an image
    img = Image.new("RGB", (842, 595), "white")
    for x in range(100, 700, 20):
        for y in range(100, 400):
            img.putpixel((x, y), (0, 0, 0))
    img_path = WORK / "scan.png"; img.save(img_path)
    p3.insert_image(p3.rect, filename=str(img_path))
    doc.save(str(path)); doc.close()


def make_scanned_only(path):
    doc = pymupdf.open(); page = doc.new_page(width=595, height=842)
    img_path = WORK / "scan.png"
    page.insert_image(page.rect, filename=str(img_path)); doc.save(str(path)); doc.close()


def hwpx_section(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        info = z.getinfo("mimetype")
        return names, info, z.read("mimetype"), ET.fromstring(z.read("Contents/section0.xml")), ET.fromstring(z.read("Contents/header.xml")), z.read("Contents/content.hpf").decode("utf-8")


def all_text(root):
    return "\n".join(t.text or "" for t in root.iter(HP + "t"))


def main():
    sample = WORK / "sample.pdf"; make_sample(sample)
    scanned = WORK / "scanned.pdf"; make_scanned_only(scanned)

    # ---------------- Excel
    xlsx = OUT / "sample.xlsx"
    stats = conv.convert_pdf(str(sample), conv.FORMAT_XLSX, xlsx)
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    check("xlsx sheets: table / text / text-only page / scanned page", wb.sheetnames == ["p1_표1", "p1_텍스트", "p2", "p3"], wb.sheetnames)
    t = wb["p1_표1"]
    check("xlsx table cells", t["A1"].value == "구분" and t["B1"].value == "점검 결과" and t["A3"].value == "CCTV" and t["D4"].value == "보완 필요",
          [[c.value for c in r] for r in t.iter_rows()])
    check("xlsx merged header cell B1:C1 kept", "B1:C1" in [str(m) for m in t.merged_cells.ranges], t.merged_cells.ranges)
    check("xlsx numbers are numbers, codes stay text", t["B3"].value == 1234 and t["C3"].value == 12.5 and t["B4"].value == "007",
          (t["B3"].value, t["C3"].value, t["B4"].value))
    check("xlsx header row bold", t["A1"].font.bold)
    txt = [r[0] for r in wb["p1_텍스트"].iter_rows(values_only=True)]
    check("xlsx text outside table, reading order", txt[:3] == ["2026년 시설 점검 결과 보고", "작성: 정보보안팀", "아래 표는 분기별 점검 건수입니다."] and "표 아래 문단: 끝." in txt, txt)
    row = [r for r in wb["p1_텍스트"].iter_rows(values_only=True) if r[0] == "작성: 정보보안팀"]
    check("xlsx wide gap splits a line into cells", row and row[0][1] == "일자: 2026-11-25", row)
    p2 = [r for r in wb["p2"].iter_rows(values_only=True)]
    check("xlsx text-only page: one row per line", p2[0][0] == "2쪽 제목" and len(p2) == 7 and p2[6][:2] == ("왼쪽 칸", "오른쪽 칸"), p2)
    check("xlsx scanned page noted, stats", "텍스트가 없습니다" in str(wb["p3"]["A1"].value) and stats["scanned_pages"] == [2] and stats["tables"] == 1, stats)
    sub = OUT / "sample_p2.xlsx"; conv.convert_pdf(str(sample), conv.FORMAT_XLSX, sub, pages=[1])
    check("xlsx page range (2쪽만)", load_workbook(sub).sheetnames == ["p2"])

    # ---------------- 한글 .hwpx
    hwpx = OUT / "sample.hwpx"
    stats = conv.convert_pdf(str(sample), conv.FORMAT_HWPX, hwpx)
    names, info, mimetype, sec, head, hpf = hwpx_section(hwpx)
    check("hwpx: mimetype first + stored", names[0] == "mimetype" and info.compress_type == zipfile.ZIP_STORED and mimetype == b"application/hwp+zip", names[:2])
    check("hwpx: required parts", all(n in names for n in ("version.xml", "Contents/header.xml", "Contents/section0.xml", "Contents/content.hpf", "META-INF/container.xml", "settings.xml")))
    for tag in ("borderFills", "charProperties", "paraProperties", "styles", "fontfaces"):
        el = head.find(f".//{HH}{tag}")
        check(f"hwpx header {tag} itemCnt matches", el is not None and int(el.get("itemCnt")) == len(list(el)), (el.get("itemCnt"), len(list(el))) if el is not None else None)
    char_ids = {int(c.get("id")) for c in head.iter(HH + "charPr")}
    used = {int(r.get("charPrIDRef")) for r in sec.iter(HP + "run")}
    check("hwpx: every charPrIDRef defined", used <= char_ids, used - char_ids)
    bf_ids = {int(b.get("id")) for b in head.iter(HH + "borderFill")}
    used_bf = {int(e.get("borderFillIDRef")) for e in sec.iter() if e.get("borderFillIDRef")}
    check("hwpx: every borderFillIDRef defined", used_bf <= bf_ids, used_bf - bf_ids)
    tbls = list(sec.iter(HP + "tbl"))
    check("hwpx: one table 4x4", len(tbls) == 1 and tbls[0].get("rowCnt") == "4" and tbls[0].get("colCnt") == "4", [(x.get("rowCnt"), x.get("colCnt")) for x in tbls])
    spans = [(tc.find(HP + "cellAddr").get("rowAddr"), tc.find(HP + "cellAddr").get("colAddr"), tc.find(HP + "cellSpan").get("colSpan")) for tc in tbls[0].iter(HP + "tc")]
    check("hwpx: merged header cell colSpan=2, covered cell omitted", ("0", "1", "2") in spans and len(spans) == 15, spans)
    widths = [int(tc.find(HP + "cellSz").get("width")) for tc in tbls[0].iter(HP + "tc") if tc.find(HP + "cellAddr").get("rowAddr") == "1"]
    check("hwpx: table width = sum of columns, fits page", sum(widths) == int(tbls[0].find(HP + "sz").get("width")) <= conv.HWP_TEXT_WIDTH, widths)
    def grid_ok(tbl):
        R, C, seen = int(tbl.get("rowCnt")), int(tbl.get("colCnt")), []
        for tc in tbl.iter(HP + "tc"):
            a, sp = tc.find(HP + "cellAddr"), tc.find(HP + "cellSpan")
            r, c, rs, cs = int(a.get("rowAddr")), int(a.get("colAddr")), int(sp.get("rowSpan")), int(sp.get("colSpan"))
            seen += [(rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)]
        return sorted(seen) == [(r, c) for r in range(R) for c in range(C)]
    check("hwpx: every table grid position covered exactly once", all(grid_ok(x) for x in tbls))
    class FakePage:
        def get_textbox(self, rect):
            return f"merged text {round(rect[1])}-{round(rect[3])}"
    cells = {(0, 0): ("h1", 1, 1), (0, 1): ("h2", 1, 1), (1, 0): ("a", 1, 1), (2, 0): ("b", 1, 1)}
    conv._fill_grid_gaps(cells, 3, 2, [0, 50, 100], [0, 10, 20, 30], FakePage())
    check("grid holes (outline not found) -> one tall cell with the page text", cells.get((1, 1)) == ("merged text 10-30", 2, 1) and len(cells) == 5, cells)
    text = all_text(sec)
    check("hwpx: text + table text + escaping", all(s in text for s in ("2026년 시설 점검 결과 보고", "보완 필요", "1,234", "<특수&문자> \"인용\"", "표 아래 문단: 끝.")), text[:200])
    breaks = [p for p in sec.iter(HP + "p") if p.get("pageBreak") == "1"]
    check("hwpx: each PDF page starts a new page (2 breaks)", len(breaks) == 2, len(breaks))
    titles = [r for r in sec.iter(HP + "run") if r.get("charPrIDRef") == str(conv.CHAR_TITLE)]
    check("hwpx: big text -> title style", titles and titles[0].find(HP + "t").text == "2026년 시설 점검 결과 보고")
    check("hwpx: scanned page placeholder, stats", "텍스트가 없는 페이지" in text and stats["tables"] == 1 and stats["scanned_pages"] == [2], stats)
    check("hwpx: no personal metadata (creator = Koinfra PDF)", "Koinfra PDF" in hpf and os.environ.get("USERNAME", "@@") not in hpf.replace("Koinfra PDF", ""))
    ET.fromstring(hpf.encode("utf-8"))

    # ---------------- images
    imgs = conv.convert_pdf(str(sample), conv.FORMAT_PNG, OUT / "png150", dpi=150)
    sizes = [Image.open(f).size for f in imgs["files"]]
    near = lambda a, b: abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1
    check("png: 3 files at 150 DPI (A4 ~1240x1754, landscape 3rd)", len(sizes) == 3 and near(sizes[0], (1240, 1754)) and near(sizes[2], (1754, 1240)), sizes)
    check("png names", [Path(f).name for f in imgs["files"]] == ["sample_p001.png", "sample_p002.png", "sample_p003.png"])
    jpgs = conv.convert_pdf(str(sample), conv.FORMAT_JPG, OUT / "jpg72", pages=[0, 2], dpi=72)
    sizes = [Image.open(f).size for f in jpgs["files"]]
    check("jpg: page range 1,3 at 72 DPI", [Path(f).name for f in jpgs["files"]] == ["sample_p001.jpg", "sample_p003.jpg"] and sizes == [(595, 842), (842, 595)], sizes)
    check("jpg is JPEG", Image.open(jpgs["files"][0]).format == "JPEG")

    # ---------------- scanned PDF, cancel, password
    try:
        conv.convert_pdf(str(scanned), conv.FORMAT_XLSX, OUT / "scanned.xlsx"); ok = False
    except conv.ScannedPdfError as exc:
        ok = "스캔" in str(exc)
    check("scanned-only PDF -> ScannedPdfError (xlsx)", ok and not (OUT / "scanned.xlsx").exists())
    try:
        conv.convert_pdf(str(scanned), conv.FORMAT_HWPX, OUT / "scanned.hwpx"); ok = False
    except conv.ScannedPdfError:
        ok = True
    check("scanned-only PDF -> ScannedPdfError (hwpx)", ok)
    check("scanned-only PDF converts to image", len(conv.convert_pdf(str(scanned), conv.FORMAT_PNG, OUT / "scan_img", dpi=72)["files"]) == 1)
    before = set(os.listdir(OUT))
    try:
        conv.convert_pdf(str(sample), conv.FORMAT_XLSX, OUT / "cancelled.xlsx", cancel=lambda: True); ok = False
    except conv.ConversionCancelled:
        ok = True
    check("cancel stops, leaves no file", ok and set(os.listdir(OUT)) == before, set(os.listdir(OUT)) - before)
    locked = WORK / "locked.pdf"
    d = pymupdf.open(str(sample)); d.save(str(locked), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="1234"); d.close()
    s = conv.convert_pdf(str(locked), conv.FORMAT_HWPX, OUT / "locked.hwpx", password="1234")
    check("password PDF converts with password", s["tables"] == 1)
    try:
        conv.convert_pdf(str(locked), conv.FORMAT_XLSX, OUT / "locked.xlsx", password="x"); ok = False
    except ValueError:
        ok = True
    check("wrong password -> error", ok)
    drm = WORK / "drm.pdf"; drm.write_bytes(b"<DOCUMENT SAFER 3.0>" + os.urandom(4000))
    msgs = []
    for fmt in (conv.FORMAT_XLSX, conv.FORMAT_PNG):
        try:
            conv.convert_pdf(str(drm), fmt, OUT / "drm_out")
        except ValueError as exc:
            msgs.append(str(exc))
    check("DRM/non-PDF file -> friendly message (xlsx, png)", len(msgs) == 2 and all("DRM" in m for m in msgs), msgs)
    check("unique_path never overwrites", conv.unique_path(xlsx).name == "sample (2).xlsx")

    # ---------------- Hancom Office: open the .hwpx, save .hwp
    if conv.hancom_installed():
        import pythoncom, win32com.client
        pythoncom.CoInitialize()
        hwp = win32com.client.DispatchEx("HWPFrame.HwpObject")
        module = conv._hancom_security_module()
        if module:
            hwp.RegisterModule("FilePathCheckDLL", module)
        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:
            pass
        opened = hwp.Open(str(hwpx), "HWPX", "forceopen:true")
        hwp_text = hwp.GetTextFile("UNICODE", "") or ""
        pages = hwp.PageCount
        hwp.InitScan(); hwp.ReleaseScan()
        hwp.Clear(1); hwp.Quit(); pythoncom.CoUninitialize()
        check("Hancom Office opens the .hwpx", opened, opened)
        check("Hancom Office reads text + table cells", "2026년 시설 점검 결과 보고" in hwp_text and "보완 필요" in hwp_text and "1,234" in hwp_text, hwp_text[:200])
        check("Hancom Office lays it out on 3 pages", pages == 3, pages)
        hwp_out = OUT / "sample.hwp"
        conv.convert_pdf(str(sample), conv.FORMAT_HWP, hwp_out)
        check(".hwp saved via Hancom Office (OLE file)", hwp_out.is_file() and hwp_out.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1"))
    else:
        print("SKIP Hancom Office not installed (.hwpx structure checked above)")

    # ---------------- UI: 변환 menu/toolbar, dialog with the open (edited) document
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QDialog
    import kpdf_convert_dialog as ui
    app = QApplication.instance() or QApplication(sys.argv[:1])
    QDialog.exec_ = lambda self: 0
    opened_folders = []
    ui.open_folder = lambda path, select=None: opened_folders.append((str(path), select))
    w = K.PdfPreviewer(); w.resize(1280, 820); w.show(); QTest.qWait(200)
    menus = [m.text() for m in w.menuBar().actions()]
    check("menu bar has 변환 tab", "변환" in menus, menus)
    conv_menu = [a.text() for a in w.convert_menu.actions()]
    check("변환 menu: 엑셀/한글/이미지", len(conv_menu) == 3 and conv_menu[0].startswith("엑셀") and conv_menu[1].startswith("한글") and conv_menu[2].startswith("이미지"), conv_menu)
    groups = {name: [a.text() for a in acts] for name, acts, _ in w.toolbar_groups}
    check("toolbar group 변환 = one 변환 button", groups.get("변환") == ["변환"], groups)
    dlg = w.open_convert_dialog("image")
    check("dialog without a document: asks for a file", dlg.source is None and "열린 문서가 없습니다" in dlg.source_label.text())
    edit_copy = WORK / "편집본.pdf"; shutil.copy(sample, edit_copy)
    w.load_pdf(str(edit_copy)); QTest.qWait(200)
    w.page_list.setCurrentRow(1); w.rotate_page_rows([1], 90); QTest.qWait(100)
    dlg = ui.ConvertDialog(w, "image"); dlg.show(); QTest.qWait(100)
    check("dialog uses open document incl. edits (temp copy)", dlg.source and dlg.source["from_document"] and dlg.temp_dir and "편집 내용 포함" in dlg.source_label.text(), dlg.source_label.text())
    check("dialog: scanned page warning shown", not dlg.scan_warning.isHidden() and "3" in dlg.scan_warning.text(), dlg.scan_warning.text())
    check("dialog: image options page", dlg.options.currentWidget() is dlg.option_pages["image"] and dlg.kind_buttons["image"].isChecked())
    out_dir = OUT / "ui_out"; shutil.rmtree(out_dir, ignore_errors=True); out_dir.mkdir()
    dlg.output_dir_edit.setText(str(out_dir))
    dlg.current_page_radio.setChecked(True)
    dlg.dpi_combo.setCurrentIndex(conv.IMAGE_DPI_CHOICES.index(72))
    progress_seen = []
    dlg.progress.valueChanged.connect(progress_seen.append)
    dlg.grab().save(str(OUT / "convert_dialog_image.png"))
    check("dialog start", dlg.start_conversion())
    for _ in range(200):
        QTest.qWait(50)
        if dlg.worker is None:
            break
    files = dlg.last_stats["files"] if dlg.last_stats else []
    check("dialog: current page (rotated 2쪽) -> 1 PNG, landscape", len(files) == 1 and Image.open(files[0]).size == (842, 595), [Image.open(f).size for f in files])
    check("dialog: progress reached 100% and folder opened", progress_seen and progress_seen[-1] == 100 and opened_folders, (progress_seen[-3:], opened_folders))
    check("dialog: button back to 닫기", dlg.close_button.text() == "닫기" and dlg.convert_button.isEnabled())
    dlg.select_kind("excel"); dlg.range_radio.setChecked(True); dlg.range_edit.setText("1-2")
    check("dialog: excel output preview", dlg.output_preview.text().endswith("편집본.xlsx"), dlg.output_preview.text())
    dlg.grab().save(str(OUT / "convert_dialog_excel.png"))
    dlg.start_conversion()
    for _ in range(200):
        QTest.qWait(50)
        if dlg.worker is None:
            break
    check("dialog: excel range 1-2 done", dlg.last_stats and dlg.last_stats["pages"] == 2 and Path(dlg.last_stats["output"]).is_file(), dlg.last_error or dlg.last_stats)
    dlg.select_kind("hwp"); dlg.all_pages_radio.setChecked(True)
    dlg.grab().save(str(OUT / "convert_dialog_hwp.png"))
    dlg.start_conversion()
    for _ in range(300):
        QTest.qWait(50)
        if dlg.worker is None:
            break
    check("dialog: hwpx all pages done", dlg.last_stats and dlg.last_stats["format"] == "hwpx" and Path(dlg.last_stats["output"]).is_file(), dlg.last_error or dlg.last_stats)
    msgs = []
    ui.QMessageBox.warning = staticmethod(lambda *a, **k: msgs.append(a[2] if len(a) > 2 else ""))
    dlg.range_radio.setChecked(True); dlg.range_edit.setText("9")
    check("dialog: bad page range -> message, no run", not dlg.start_conversion() and msgs and "1-3" in msgs[-1], msgs)
    temp_dir = dlg.temp_dir
    dlg.close(); QTest.qWait(50)
    check("dialog: temp copy removed on close", temp_dir and not Path(temp_dir).exists())
    dlg2 = ui.ConvertDialog(w, "excel")
    infos = []
    ui.QMessageBox.information = staticmethod(lambda *a, **k: infos.append(a[2] if len(a) > 2 else ""))
    dlg2.set_source_file(str(scanned)); dlg2.output_dir_edit.setText(str(out_dir))
    check("dialog: scanned file -> warning, xlsx refused", not dlg2.scan_warning.isHidden() and not dlg2.start_conversion() and infos and "스캔" in infos[-1], infos)
    check("dialog: other file disables 현재 페이지", not dlg2.current_page_radio.isEnabled())
    dlg2.close()
    w.close(); QTest.qWait(50)


try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False, traceback.format_exc()[-300:]))
finally:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / "convert_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0 if passed == len(RESULTS) else 1)

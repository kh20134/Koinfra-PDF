"""v4: approval table rendering (preview vs saved PDF) + grouped toolbar layout/captures.
usage: python approval_test.py <project_dir> <out_dir> <tag>"""
import json, os, shutil, sys, tempfile, traceback, types
from pathlib import Path
if os.name != "nt":
    sys.modules["winreg"] = types.ModuleType("winreg")
PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); TAG = sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="koinfra_v4_"))
os.environ["APPDATA"] = str(WORK / "appdata")  # isolated: never touches the user's preferences
os.environ["LOCALAPPDATA"] = str(WORK / "localappdata")
import importlib.util
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("K", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from PyQt5.QtCore import QBuffer, QIODevice
from PIL import Image, ImageDraw
from io import BytesIO
import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)

def to_pil(pixmap):
    buf = QBuffer(); buf.open(QIODevice.WriteOnly); pixmap.save(buf, "PNG")
    return Image.open(BytesIO(bytes(buf.data()))).convert("RGB")

def is_dark(p): return p[0] < 110 and p[1] < 110 and p[2] < 130 and not is_navy(p)
def is_navy(p): return p[2] > p[0] + 45 and p[0] < 110

def runs(values):
    count, inside = 0, False
    for v in values:
        if v and not inside: count += 1
        inside = v
    return count

def analyse(img, rect, labels, cell):
    """Pixel checks of one rendered approval table inside a display-oriented page image."""
    W, H = img.size
    L, T, R, B = rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H
    px = img.load()
    res = {}
    # bounding box of dark pixels near the mark
    xs, ys = [], []
    for y in range(max(0, int(T) - 12), min(H, int(B) + 12)):
        for x in range(max(0, int(L) - 12), min(W, int(R) + 12)):
            if is_dark(px[x, y]): xs.append(x); ys.append(y)
    if not xs:
        return {"found": False}
    bw, bh = R - L, B - T
    res["bbox_err"] = max(abs(min(xs) - L) / bw, abs(max(xs) - R) / bw, abs(min(ys) - T) / bh, abs(max(ys) - B) / bh)
    lr, sr, hr = K.approval_geometry((0, 0, 1, 1), labels)
    x1 = L + lr * bw; sw = sr * bw; hh = hr * bh
    # vertical borders along a row near the bottom of the signature row
    y_scan = int(B - 0.05 * (bh - hh))
    res["v_lines"] = runs([is_dark(px[x, y_scan]) for x in range(int(L) - 4, int(R) + 5)])
    # horizontal borders down a column inside an empty signer cell (not the signature cell)
    empty = [i for i in range(len(labels)) if i != cell][0]
    x_scan = int(x1 + sw * empty + sw * 0.05)
    res["h_lines"] = runs([is_dark(px[x_scan, y]) for y in range(int(T) - 4, int(B) + 5)])
    # header text in every title cell + vertical 결재 in head cell
    def dark_in(a, b, c, d):
        return sum(1 for y in range(int(b), int(d)) for x in range(int(a), int(c)) if is_dark(px[x, y]))
    res["title_dark"] = [dark_in(x1 + sw * i + sw * 0.15, T + hh * 0.2, x1 + sw * (i + 1) - sw * 0.15, T + hh * 0.8) for i in range(len(labels))]
    res["head_dark"] = dark_in(L + lr * bw * 0.2, T + bh * 0.15, x1 - lr * bw * 0.2, B - bh * 0.15)
    # signature: navy pixels inside the chosen cell, none elsewhere
    c = K.approval_signature_cell_rect(rect, labels, cell)
    inside = outside = 0
    # (signature row only: on-screen ClearType text has bluish fringes in the title row/head cell)
    for y in range(int(T + hh + 3), min(H, int(B) + 12)):
        for x in range(int(x1 + 3), min(W, int(R) + 12)):
            if is_navy(px[x, y]):
                if c[0] * W - 2 <= x <= c[2] * W + 2 and c[1] * H - 2 <= y <= c[3] * H + 2: inside += 1
                else: outside += 1
    res["sig_in"], res["sig_out"] = inside, outside
    res["found"] = True
    return res

def verdict(res, labels):
    return (res.get("found") and res["bbox_err"] < 0.03 and res["v_lines"] == len(labels) + 2 and res["h_lines"] == 3
            and min(res["title_dark"]) > 8 and res["head_dark"] > 15 and res["sig_in"] > 40 and res["sig_out"] == 0)

def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    # ---- inputs
    portrait = WORK / "portrait.pdf"; Image.new("RGB", (1240, 1754), "white").save(portrait, "PDF", resolution=150.0)
    landscape = WORK / "landscape.pdf"; Image.new("RGB", (1754, 1240), "white").save(landscape, "PDF", resolution=150.0)
    native_rot = WORK / "native_rot90.pdf"
    w = PdfWriter(); w.append(str(portrait)); w.pages[0].rotate(90); w.write(str(native_rot))
    sig = WORK / "sig.png"; im = Image.new("RGBA", (360, 140), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.line([(10, 110), (90, 20), (170, 120), (250, 40), (350, 70)], fill=(20, 40, 170, 255), width=12); im.save(sig)

    win = K.PdfPreviewer(); win.resize(1280, 860); win.show(); QTest.qWait(300)
    win.load_pdf(str(portrait)); QTest.qWait(400)
    # real flow: 결재 button with saved signature
    win.set_approval_line(("담당", "검토", "승인"), persist=True)
    win.signature_settings = {"image": str(sig), "cell": 0}
    win.insert_signed_approval(); QTest.qWait(400)
    win.grab().save(str(OUT / f"{TAG}_app_window.png"))

    rect = (0.54, 0.82, 0.96, 0.955)
    cases = [
        ("3cells_me0", portrait, ("담당", "검토", "승인"), 0, 0, rect),
        ("3cells_me2", portrait, ("담당", "검토", "승인"), 2, 0, rect),
        ("4cells_me1", portrait, ("담당", "팀장", "부서장", "대표"), 1, 0, rect),
        ("6cells_me5", portrait, ("담당", "대리", "과장", "차장", "부장", "대표이사"), 5, 0, (0.40, 0.80, 0.96, 0.95)),
        ("rot90_me1", portrait, ("담당", "검토", "승인"), 1, 90, (0.50, 0.70, 0.96, 0.93)),
        ("rot180_me0", portrait, ("담당", "검토", "승인"), 0, 180, rect),
        ("rot270_me2", portrait, ("담당", "검토", "승인"), 2, 270, (0.50, 0.70, 0.96, 0.93)),
        ("landscape_me1", landscape, ("담당", "검토", "승인", "대표"), 1, 0, (0.60, 0.74, 0.96, 0.95)),
        ("nativeRotate90_me0", native_rot, ("담당", "검토", "승인"), 0, 0, (0.50, 0.70, 0.96, 0.93)),
    ]
    for name, src, labels, cell, rotation, mrect in cases:
        mark = K.PageMark("approval_signed", mrect, (labels, str(sig), cell))
        ref = K.PageRef(str(src), 0, rotation, (mark,), None)
        preview = to_pil(win.render_page_pixmap(ref, 2.0))
        out_pdf = WORK / f"{name}.pdf"
        win.write_ordered_pdf(str(out_pdf), [ref])
        doc = pdfium.PdfDocument(str(out_pdf)); saved = doc[0].render(scale=2.0, may_draw_forms=True).to_pil().convert("RGB"); doc.close()
        rp, rs = analyse(preview, mrect, labels, cell), analyse(saved, mrect, labels, cell)
        check(f"preview approval table complete [{name}]", verdict(rp, labels), json.dumps(rp, ensure_ascii=False))
        check(f"saved PDF approval table complete [{name}]", verdict(rs, labels), json.dumps(rs, ensure_ascii=False))
        annots = [str(a.get_object().get("/Subtype")) for a in PdfReader(str(out_pdf)).pages[0].get("/Annots", [])]
        check(f"no appearance-less annotations [{name}]", not annots, str(annots))
        if name in ("3cells_me0", "6cells_me5", "rot90_me1", "nativeRotate90_me0"):
            preview.save(OUT / f"{TAG}_{name}_preview.png"); saved.save(OUT / f"{TAG}_{name}_saved_pdfium.png")
            shutil.copy2(out_pdf, OUT / f"{TAG}_{name}.pdf")
    # unsigned table
    mark = K.PageMark("approval", rect, ("담당", "검토", "승인"))
    ref = K.PageRef(str(portrait), 0, 0, (mark,), None)
    out_pdf = WORK / "unsigned.pdf"; win.write_ordered_pdf(str(out_pdf), [ref])
    doc = pdfium.PdfDocument(str(out_pdf)); saved = doc[0].render(scale=2.0).to_pil().convert("RGB"); doc.close()
    r = analyse(saved, rect, ("담당", "검토", "승인"), 0)
    check("unsigned approval table complete in PDF", r.get("found") and r["v_lines"] == 5 and r["h_lines"] == 3 and min(r["title_dark"]) > 8 and r["sig_in"] == 0, json.dumps(r, ensure_ascii=False))

    # ---- toolbar
    tb = win.findChildren(K.QToolBar)[0]
    for width in (1280, 1100):
        win.resize(width, 760); QTest.qWait(300)
        ext = [b for b in tb.findChildren(K.QToolButton) if b.objectName() == "qt_toolbar_ext_button"]
        ext_visible = any(b.isVisible() for b in ext)
        tb_rect = tb.rect()
        bad = []
        for name, acts, cap in win.toolbar_groups:
            for a in acts:
                b = win.toolbar_buttons[a]
                top_left = b.mapTo(tb, b.rect().topLeft()); bottom_right = b.mapTo(tb, b.rect().bottomRight())
                if not b.isVisible() or bottom_right.x() > tb_rect.right() or b.width() < b.sizeHint().width():
                    bad.append(f"{a.text()}:{b.width()}<{b.sizeHint().width()}")
        spin_ok = win.page_spin.isVisible() and win.page_spin.mapTo(tb, win.page_spin.rect().bottomRight()).x() <= tb_rect.right()
        check(f"toolbar fits without clipping @ {width}px", not bad and not ext_visible and spin_ok, f"bad={bad} ext={ext_visible} spin={spin_ok}")
        tb.grab().save(str(OUT / f"toolbar_{TAG}_{width}.png"))
    win.close(); QTest.qWait(100)

try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False, ""))
finally:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / f"{TAG}_approval_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0 if passed == len(RESULTS) else 1)

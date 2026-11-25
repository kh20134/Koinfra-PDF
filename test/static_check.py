"""Static checks: compile, icon keys -> files, spec datas, resource paths."""
import ast
import py_compile
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
# The app is Koinfra.py plus its kpdf_*.py modules (1.2+); all checks run over the combined source.
SOURCES = [root / "Koinfra.py"] + sorted(root.glob("kpdf_*.py"))
src = "\n".join(path.read_text(encoding="utf-8") for path in SOURCES)
ok = True


def report(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


for path in SOURCES:
    py_compile.compile(str(path), doraise=True, cfile=str(Path(sys.argv[2] if len(sys.argv) > 2 else root) / f"_compile_check_{path.stem}.pyc"))
report(f"py_compile ({', '.join(path.name for path in SOURCES)})", True)
tree = ast.parse(src)
keys = set(re.findall(r'make_tool_icon\("([a-z_]+)"\)', src))
keys |= set(re.findall(r'self\.make_action\([^\n]*?,\s*"([a-z_]+)"(?:,|\))', src))
keys |= set(re.findall(r'\("[^"]*", self\.[a-z_]+, "([a-z_]+)"\)', src))
missing = sorted(k for k in keys if not all((root / "icons" / f"{k}_{s}.png").is_file() for s in (16, 22, 28, 56)))
report(f"icon keys used in code have PNG files ({len(keys)} keys)", not missing, ",".join(missing) or ",".join(sorted(keys)))
for name in ("icon.svg", "icon.ico", "icon2.ico", "icon2.png", "icon_256.png", "icon_small.svg", "ICONS_LICENSE.txt"):
    report(f"resource exists: {name}", (root / name).is_file())
# 1.4.2 brand images: every app/document ICO carries 16~256 px frames; the setup wizard images exist at their
# scaled sizes and the Inno script uses them.
import struct  # noqa: E402
for name in ("icon.ico", "icon2.ico", "koinfra_pdf_doc.ico"):
    data = (root / name).read_bytes() if (root / name).is_file() else b"\0" * 6
    count = struct.unpack_from("<H", data, 4)[0]
    frames = sorted({(data[6 + 16 * i] or 256) for i in range(count)})
    report(f"{name} has 16~256 px frames", {16, 20, 24, 32, 40, 48, 64, 96, 128, 256} <= set(frames), frames)
iss = (root / "KoinfraPDF.iss").read_text(encoding="utf-8-sig")
wizard = {"wizard_image_%d.bmp" % w: (w, h) for w, h in ((164, 314), (205, 393), (246, 471), (328, 628))}
wizard.update({"wizard_small_%d.bmp" % s: (s, s) for s in (55, 69, 83, 110)})
for name, (w, h) in wizard.items():
    data = (root / name).read_bytes() if (root / name).is_file() else b""
    dims = struct.unpack_from("<ii", data, 18) if len(data) > 26 and data[:2] == b"BM" else None
    report(f"setup image {name} is a {w}x{h} BMP used by the installer", dims is not None and (dims[0], abs(dims[1])) == (w, h) and name in iss, dims)
report("installer uses the new icon + refreshes the icon cache", "SetupIconFile=icon2.ico" in iss and "ie4uinit.exe" in iss)
spec = (root / "KoinfraPDFEdit.spec").read_text(encoding="utf-8")
for item in ("('icon.svg', '.')", "('icon2.ico', '.')", "('icon.ico', '.')", "('icons', 'icons')"):
    report(f"spec datas has {item}", item in spec)
report("spec exe icon = icon2.ico", "icon=['icon2.ico']" in spec)
report("resources resolved via sys._MEIPASS", 'RESOURCE_DIR = Path(getattr(sys, "_MEIPASS"' in src and 'TOOL_ICON_DIR = RESOURCE_DIR / "icons"' in src)
methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
refs = set(re.findall(r"self\.([a-z_]+)\b(?!\s*=)", src)) | set(re.findall(r"self\.owner\.([a-z_]+)\(", src))
called = set(re.findall(r"self(?:\.owner)?\.([a-z_]+)\(", src))
from PyQt5.QtWidgets import QLabel, QListWidget, QMainWindow, QScrollArea, QDialog, QWidget  # noqa: E402

qt_classes = (QLabel, QListWidget, QMainWindow, QScrollArea, QDialog, QWidget)
undefined = sorted(c for c in called if c not in methods and not any(hasattr(cls, c) for cls in qt_classes) and not c.startswith(("page_list", "scroll_area")) and c not in {
    "setWindowTitle", "setWindowIcon", "resize", "setMinimumSize", "setAcceptDrops", "setStyleSheet", "menuBar", "addToolBar",
    "setCentralWidget", "setStatusBar", "statusBar", "style", "close", "setText", "clear", "setFixedSize", "pixmap"})
report("every self.<method>() call resolves to a defined method", not undefined, ",".join(undefined))
print("STATIC", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)

"""Static checks: compile, icon keys -> files, spec datas, resource paths."""
import ast
import py_compile
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
src = (root / "Koinfra.py").read_text(encoding="utf-8")
ok = True


def report(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


py_compile.compile(str(root / "Koinfra.py"), doraise=True, cfile=str(Path(sys.argv[2] if len(sys.argv) > 2 else root) / "_compile_check.pyc"))
report("py_compile", True)
tree = ast.parse(src)
keys = set(re.findall(r'make_tool_icon\("([a-z_]+)"\)', src))
keys |= set(re.findall(r'self\.make_action\([^\n]*?,\s*"([a-z_]+)"(?:,|\))', src))
keys |= set(re.findall(r'\("[^"]*", self\.[a-z_]+, "([a-z_]+)"\)', src))
missing = sorted(k for k in keys if not all((root / "icons" / f"{k}_{s}.png").is_file() for s in (16, 22, 28, 56)))
report(f"icon keys used in code have PNG files ({len(keys)} keys)", not missing, ",".join(missing) or ",".join(sorted(keys)))
for name in ("icon.svg", "icon.ico", "icon2.ico", "icon2.png", "icon_256.png", "icon_small.svg", "ICONS_LICENSE.txt"):
    report(f"resource exists: {name}", (root / name).is_file())
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

"""v6: PDF default-app registration + one-click picker flow (picker/launcher mocked). Uses a throwaway registry root HKCU\\Software\\KoinfraTest\\<id>; never
writes the real association keys, never opens Settings (launcher + SHChangeNotify are mocked).
usage: python assoc_test.py <project_dir> <out_dir>"""
import json, os, shutil, sys, tempfile, traceback, uuid
from pathlib import Path
PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="koinfra_v7_")); os.environ["APPDATA"] = str(WORK)
import winreg
import importlib.util
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("K", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtTest import QTest
from PyQt5.QtCore import QTimer, Qt
import tempfile as _tf

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail))); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)

HK = winreg.HKEY_CURRENT_USER
TEST_ROOT = r"Software\KoinfraTest"
BASE = TEST_ROOT + "\\" + uuid.uuid4().hex[:8]

def real_snapshot():
    g = K._reg_get
    return (
        g(r"Software\Classes\Koinfra.PDFEdit.Document\shell\open\command", ""),
        g(r"Software\RegisteredApplications", "KoinfraPDFEdit"),
        g(r"Software\Koinfra\KoinfraPDFEdit\Capabilities\FileAssociations", ".pdf"),
        g(r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice", "ProgId"),
        g(r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice", "Hash"),
        g(r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoiceLatest\ProgId", "ProgId"),
    )

def key_exists(path):
    try:
        winreg.CloseKey(winreg.OpenKey(HK, path)); return True
    except OSError:
        return False

def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    before = real_snapshot()
    notified, launched = [], []
    K.notify_association_changed = lambda: notified.append(1)
    K.os.startfile = lambda target: launched.append(target)  # never open Settings in tests
    K.ASSOC_BASE = BASE
    p = K._assoc_paths()
    check("paths are under the test root", all(v.startswith(BASE) for v in p.values()), BASE)

    K.register_openwith()
    cmd = K.build_openwith_command()
    g = K._reg_get
    check("ProgID open command (quoted %1)", g(p["progid"] + r"\shell\open\command", "") == cmd and cmd.endswith('"%1"'), cmd)
    exe = K.installed_exe_path()
    if exe is not None:
        check("installed: command = installed exe (not pythonw)", cmd == f'"{exe}" "%1"' and "pythonw" not in cmd.lower(), cmd)
        check("installed: app icon = exe,0", g(p["app"] + r"\DefaultIcon", "") == f"{exe},0")
    else:
        check("script: command = pythonw + script", "pythonw" in cmd.lower() and str(PROJECT / "Koinfra.py") in cmd, cmd)
    doc_icon = g(p["progid"] + r"\DefaultIcon", "")
    check("ProgID DefaultIcon = Koinfra PDF document icon", doc_icon.lower().endswith("koinfra_pdf_doc.ico") and Path(doc_icon).is_file(), doc_icon)
    check("ProgID AppUserModelID = window AUMID", g(p["progid"], "AppUserModelID") == K.APP_USER_MODEL_ID)
    check(".pdf OpenWithProgids has ProgID", g(p["openwith"], K.APP_PROG_ID) == "")
    check("Applications\\exe SupportedTypes .pdf", g(p["app"] + r"\SupportedTypes", ".pdf") == "" and g(p["app"], "FriendlyAppName") == "Koinfra PDF")
    check("Capabilities FileAssociations .pdf -> ProgID", g(p["capabilities"] + r"\FileAssociations", ".pdf") == K.APP_PROG_ID)
    check("Capabilities ApplicationName", g(p["capabilities"], "ApplicationName") == "Koinfra PDF")
    check("RegisteredApplications -> Capabilities", g(p["registered"], "KoinfraPDFEdit") == p["capabilities"])
    check("SHChangeNotify called", len(notified) == 1)
    check("is_pdf_association_registered", K.is_pdf_association_registered())
    check(".pdf OpenWithList has app (HKCU)", key_exists(p["openwithlist"]) and p["openwithlist"].endswith(r"\.pdf\OpenWithList\KoinfraPDFEdit.exe"), p["openwithlist"])

    # default-app detection reads UserChoiceLatest first, then UserChoice (read only)
    check("not default without UserChoice", not K.is_pdf_default_app())
    K._reg_set(p["fileexts"] + r"\UserChoice", "ProgId", K.APP_PROG_ID)
    check("default via UserChoice", K.is_pdf_default_app())
    K._reg_set(p["fileexts"] + r"\UserChoiceLatest\ProgId", "ProgId", "Acrobat.Document.DC")
    check("UserChoiceLatest wins (Win11 24H2+)", not K.is_pdf_default_app() and K.current_pdf_default_progid() == "Acrobat.Document.DC")
    K._reg_set(p["fileexts"] + r"\UserChoiceLatest\ProgId", "ProgId", K.APP_PROG_ID)
    check("default via UserChoiceLatest", K.is_pdf_default_app())
    target = K.default_apps_settings_target()
    build = sys.getwindowsversion().build
    check("settings target fits OS", target == ("ms-settings:defaultapps?registeredAppUser=KoinfraPDFEdit" if build >= 22000 else "ms-settings:defaultapps"), f"build={build} {target}")

    # ---- menu (window-only captures)
    infos = []
    K.QMessageBox.information = staticmethod(lambda *a, **k: infos.append(a[2] if len(a) > 2 else "") or QMessageBox.Ok)
    K.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    win = K.PdfPreviewer(); win.resize(1100, 760); win.show(); QTest.qWait(300)
    menu = win.tool_menu

    def shoot(name):
        def run():
            popup = QApplication.activePopupWidget()
            if popup is not None:
                popup.grab().save(str(OUT / name))  # grab() renders only this widget, never the screen
                popup.close()
        return run

    QTimer.singleShot(400, shoot("menu_tools_default_checked.png"))
    menu.exec_(win.mapToGlobal(win.rect().center()))
    check("menu: check mark when Koinfra is default", win.default_app_action.isChecked() and "\uc0ac\uc6a9 \uc911" in win.default_app_action.text(), win.default_app_action.text())
    check("menu: unregister enabled when registered", win.unregister_assoc_action.isEnabled())
    K._reg_set(p["fileexts"] + r"\UserChoiceLatest\ProgId", "ProgId", "Acrobat.Document.DC")
    QTimer.singleShot(400, shoot("menu_tools_not_default.png"))
    menu.exec_(win.mapToGlobal(win.rect().center()))
    check("menu: unchecked when another app is default", not win.default_app_action.isChecked() and win.default_app_action.text().startswith("PDF \uae30\ubcf8 \uc571\uc73c\ub85c"))

    # ---- v6 one-click flow (Windows picker mocked; nothing real is launched)
    _tf.tempdir = str(WORK)  # check PDF goes to the test temp dir
    check_pdf = K.default_app_check_pdf_path()
    check("check PDF created in temp", check_pdf.is_file() and str(WORK) in str(check_pdf) and check_pdf.read_bytes()[:5] == b"%PDF-", str(check_pdf))
    state = {"pids": {100}, "win_for": set(), "launched": [], "stopped": [], "closed": []}
    class FakeProc:
        pid = 777777
        def poll(self): return None
    def fake_launch(path):
        state["launched"].append(str(path)); return FakeProc()
    K.launch_open_with_picker = fake_launch
    K.open_with_process_ids = lambda: set(state["pids"])
    K._visible_windows_of = lambda pids: [(1, pid, "WinUI") for pid in pids if pid in state["win_for"]]
    K.stop_open_with_leftovers = lambda proc, before: state["stopped"].append(sorted(before))
    K.close_windows = lambda ws: state["closed"].extend(ws)
    def guide_text():
        return win.default_app_guide.text() if win.default_app_guide is not None else ""

    # A) picker appears -> picker guide, no Settings
    state["pids"] = {100, 4242}; state["win_for"] = {4242}
    K.open_with_process_ids = (lambda seq=[{100}]: (lambda: set(seq.pop(0)) if seq else set(state["pids"])))()
    win.default_app_action.trigger(); QTest.qWait(900)
    check("picker: launched for check PDF", state["launched"] == [str(check_pdf)], str(state["launched"]))
    check("picker: guide '\ub728\ub294 \ucc3d\uc5d0\uc11c Koinfra PDF \uc120\ud0dd \u2192 \ud56d\uc0c1'", guide_text() == "\ub728\ub294 \ucc3d\uc5d0\uc11c Koinfra PDF \uc120\ud0dd \u2192 '\ud56d\uc0c1' \ud074\ub9ad", guide_text())
    check("picker: Settings not opened", launched == [], str(launched))
    check("picker: guide does not steal focus", win.default_app_guide is not None and win.default_app_guide.testAttribute(Qt.WA_ShowWithoutActivating) and not win.default_app_guide.isModal())
    if win.default_app_guide is not None:
        win.default_app_guide.grab().save(str(OUT / "guide_picker.png"))
    check("action stays unchecked until Windows says so", not win.default_app_action.isChecked())
    check("watch running after click", win.default_app_watch is not None and win.default_app_watch.isActive())

    # B) Koinfra chosen + 'Always' (simulated in the TEST root only) -> focus return shows done
    K._reg_set(p["fileexts"] + r"\UserChoiceLatest\ProgId", "ProgId", K.APP_PROG_ID)
    n_info = len(infos)
    win.on_application_state_changed(Qt.ApplicationActive); QTest.qWait(700)
    check("focus return: '\u2714 \uc124\uc815 \uc644\ub8cc' shown", any("Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4" in m for m in infos[n_info:]) and win.statusBar().currentMessage().startswith("\u2714 \uc124\uc815 \uc644\ub8cc"), win.statusBar().currentMessage())
    check("focus return: watch stopped, guide closed, menu checked", not win.default_app_watch.isActive() and win.default_app_guide is None and win.default_app_action.isChecked())
    win.statusBar().grab().save(str(OUT / "status_done.png"))
    win.on_application_state_changed(Qt.ApplicationActive); QTest.qWait(600)
    check("done message only once", sum("Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4" in m for m in infos[n_info:]) == 1)
    win.default_app_action.trigger(); QTest.qWait(200)
    check("already default -> no picker again", len(state["launched"]) == 1 and any("\uc774\ubbf8 Koinfra PDF" in m for m in infos))
    K._reg_set(p["fileexts"] + r"\UserChoiceLatest\ProgId", "ProgId", "Acrobat.Document.DC")

    # C) picker never appears -> fallback to Settings after the timeout, helper cleaned up
    K.OPEN_WITH_PICKER_TIMEOUT_MS = 700
    state["win_for"] = set(); K.open_with_process_ids = lambda: {100, 5555}
    win.default_app_action.trigger(); QTest.qWait(1400)
    check("timeout: Settings opened with registeredAppUser", launched == [target], str(launched))
    check("timeout: our helper processes stopped", len(state["stopped"]) == 1 and state["stopped"][0] == [100, 5555])
    check("timeout: settings guide text", guide_text() == "\uc124\uc815 \ucc3d\uc5d0\uc11c .pdf \ud074\ub9ad \u2192 Koinfra PDF \uc120\ud0dd \u2192 '\uae30\ubcf8\uac12 \uc124\uc815' \ud074\ub9ad", guide_text())
    if win.default_app_guide is not None:
        win.default_app_guide.grab().save(str(OUT / "guide_settings.png"))

    # D) Windows answers with its 'go to Settings' message -> closed + fallback
    launched.clear(); state["win_for"] = {777777}
    win.default_app_action.trigger(); QTest.qWait(700)
    check("blocked: message closed + Settings fallback", len(state["closed"]) == 1 and launched == [target], f"{state['closed']} {launched}")
    state["win_for"] = set()

    # E) launcher error -> Settings immediately
    launched.clear()
    def boom(path): raise OSError("no rundll32")
    K.launch_open_with_picker = boom
    win.default_app_action.trigger(); QTest.qWait(200)
    check("launch error: Settings immediately", launched == [target], str(launched))
    if win.default_app_watch is not None:
        win.default_app_watch.stop()
    if win.default_app_guide is not None:
        win.default_app_guide.close()

    win.unregister_assoc_action.trigger(); QTest.qWait(100)
    left = [k for k in ("app", "progid", "company") if key_exists(p[k])]
    check("unregister removes keys", not left, str(left))
    check("unregister removes OpenWithList entry", not key_exists(p["openwithlist"]))
    check("unregister removes values", g(p["openwith"], K.APP_PROG_ID) is None and g(p["registered"], "KoinfraPDFEdit") is None)
    check("unregister keeps UserChoice untouched", K._reg_get(p["fileexts"] + r"\UserChoice", "ProgId") == K.APP_PROG_ID)
    check("unregister remembered (no auto re-register at start)", K.load_association_preferences()["disabled"])
    menu.aboutToShow.emit()
    check("menu: unregister disabled after removal", not win.unregister_assoc_action.isEnabled())

    # first-start prompt with 'don't ask again'
    K.save_association_preferences(disabled=False, ask_default=True)
    def answer_prompt():
        box = win.default_app_prompt
        if box is not None:
            box.grab().save(str(OUT / "prompt_first_start.png"))
            box.checkBox().setChecked(True)
            box.button(QMessageBox.No).click()
    QTimer.singleShot(400, answer_prompt)
    win.maybe_ask_default_app(); QTest.qWait(100)
    check("first-start prompt: 'don't ask again' saved", K.load_association_preferences()["ask_default"] is False)
    opened = {"n": 0}
    QTimer.singleShot(300, lambda: opened.__setitem__("n", 1 if win.default_app_prompt else 0))
    win.maybe_ask_default_app(); QTest.qWait(400)
    check("prompt not shown again", opened["n"] == 0)
    calls = []
    win.set_pdf_default_app = lambda: calls.append(1)
    K.save_association_preferences(ask_default=True)
    def yes_prompt():
        box = win.default_app_prompt
        if box is not None:
            box.button(QMessageBox.Yes).click()
    QTimer.singleShot(300, yes_prompt)
    win.maybe_ask_default_app(); QTest.qWait(100)
    check("first-start 'Yes' -> one-click flow", calls == [1])
    win.close(); QTest.qWait(100)

    check("real registry untouched", real_snapshot() == before, str(before[:3]))

try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False, ""))
finally:
    K._reg_delete_tree(TEST_ROOT)
    check("test root removed", not key_exists(TEST_ROOT))
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / "assoc_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0 if passed == len(RESULTS) else 1)

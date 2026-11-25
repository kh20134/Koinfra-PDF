"""1.3.1: where the PDF 'Open with' registration points. The installed copy always wins, a build/temp copy never takes
over, and a stale registration (program deleted -> nothing opens, generic icon) is repaired at start.
Uses a throwaway registry root HKCU\\Software\\KoinfraTest\\<id> and a temp APPDATA; the real association keys are never written.
usage: python registration_test.py <project_dir> <out_dir>"""
import json, os, shutil, sys, tempfile, traceback, uuid
from pathlib import Path
PROJECT = Path(sys.argv[1]).resolve(); OUT = Path(sys.argv[2]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="koinfra_reg_")); os.environ["APPDATA"] = str(WORK / "appdata")
os.environ.pop("KOINFRA_NO_REGISTER", None)
import winreg
import importlib.util
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("K", PROJECT / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail))); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)

HK = winreg.HKEY_CURRENT_USER
BASE = r"Software\KoinfraTest" + "\\" + uuid.uuid4().hex[:8]

def real_snapshot():
    g = K._reg_get
    keys = (
        (r"Software\Classes\Koinfra.PDFEdit.Document\shell\open\command", ""),
        (r"Software\Classes\Applications\KoinfraPDFEdit.exe\shell\open\command", ""),
        (r"Software\Classes\Applications\KoinfraPDFEdit.exe\DefaultIcon", ""),
        (r"Software\Koinfra\KoinfraPDFEdit\Capabilities", "ApplicationIcon"),
        (r"Software\RegisteredApplications", "KoinfraPDFEdit"),
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice", "ProgId"),
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice", "Hash"),
    )
    return tuple(g(k, n) for k, n in keys)

def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"MZ"); return path

def main():
    before = real_snapshot()
    K.notify_association_changed = lambda: None
    K.ASSOC_BASE = BASE
    p = K._assoc_paths()
    g = K._reg_get
    installed = touch(WORK / "Programs" / "Koinfra PDF" / "KoinfraPDFEdit.exe")
    touch(installed.parent / "koinfra_pdf_doc.ico")
    build = touch(WORK / "stage" / "dist_installer" / "KoinfraPDFEdit" / "KoinfraPDFEdit.exe")
    real_installer_location = K.installer_location_exe

    def run_as_build(installed_present):
        K.installer_location_exe = (lambda: installed) if installed_present else (lambda: None)
        sys.frozen = True; sys.executable_backup = sys.executable; sys.executable = str(build)

    def restore():
        K.installer_location_exe = real_installer_location
        if hasattr(sys, "executable_backup"):
            sys.executable = sys.executable_backup; del sys.executable_backup
        if hasattr(sys, "frozen"):
            del sys.frozen

    check("command path parsing", K._command_exe('"C:\\a b\\x.exe" "%1"') == "C:\\a b\\x.exe" and K._command_exe("C:\\x.exe %1") == "C:\\x.exe")

    # ---- a build copy runs while the app is installed -> registration still points at the installed exe
    try:
        run_as_build(True)
        check("installed copy wins over the running build copy", K.installed_exe_path() == installed, K.installed_exe_path())
        check("startup registration allowed (it targets the installed exe)", K.startup_registration_allowed())
        os.environ["KOINFRA_NO_REGISTER"] = "1"
        check("KOINFRA_NO_REGISTER=1 turns auto-registration off (smoke tests)", not K.startup_registration_allowed() and K.startup_register() == "")
        os.environ.pop("KOINFRA_NO_REGISTER")
        result = K.startup_register()
        cmd = f'"{installed}" "%1"'
        check("start from a build copy: registered with the installed exe", result == "registered" and g(p["progid"] + r"\shell\open\command", "") == cmd
              and g(p["app"] + r"\shell\open\command", "") == cmd, (result, g(p["progid"] + r"\shell\open\command", "")))
        check("Open-with icon = installed exe,0 (app key + Capabilities)", g(p["app"] + r"\DefaultIcon", "") == f"{installed},0" and g(p["capabilities"], "ApplicationIcon") == f"{installed},0")
        check("document icon from the installed folder", g(p["progid"] + r"\DefaultIcon", "") == str(installed.parent / "koinfra_pdf_doc.ico"))
        check("registration healthy", not K.registration_broken())
        check("nothing points into the build folder", "dist_installer" not in json.dumps([g(p["progid"] + r"\shell\open\command", ""), g(p["app"] + r"\DefaultIcon", ""), g(p["capabilities"], "ApplicationIcon")]))
    finally:
        restore()

    # ---- the stale state found on the PC: registration points at a deleted build folder
    gone = WORK / "deleted" / "dist_installer" / "KoinfraPDFEdit" / "KoinfraPDFEdit.exe"
    for key in (p["progid"] + r"\shell\open\command", p["app"] + r"\shell\open\command"):
        K._reg_set(key, "", f'"{gone}" "%1"')
    K._reg_set(p["app"] + r"\DefaultIcon", "", f"{gone},0")
    K._reg_set(p["capabilities"], "ApplicationIcon", f"{gone},0")
    check("stale registration detected (program deleted)", K.registration_broken())
    K.save_association_preferences(disabled=True)
    try:
        K.installer_location_exe = lambda: installed
        result = K.startup_register()
        check("repaired at start even with auto-registration off", result == "repaired" and g(p["progid"] + r"\shell\open\command", "") == f'"{installed}" "%1"'
              and not K.registration_broken(), result)
        check("healthy + turned off -> left alone", K.startup_register() == "")
        K.save_association_preferences(disabled=False)
        notified = []
        K.notify_association_changed = lambda: notified.append(1)
        check("healthy + on -> nothing rewritten, no shell refresh", K.startup_register() == "current" and not notified)
        K._reg_set(p["progid"] + r"\shell\open\command", "", f'"{installed}" "%1" --old')
        check("outdated command (program exists) -> rewritten", K.startup_register() == "registered" and g(p["progid"] + r"\shell\open\command", "") == f'"{installed}" "%1"' and notified)
        K.notify_association_changed = lambda: None
        K._reg_set(p["app"] + r"\DefaultIcon", "", str(WORK / "nope.ico"))
        check("missing icon file also counts as broken", K.registration_broken())
        K.startup_register()
    finally:
        restore()

    # ---- no installed copy: a build/temp copy must not bind PDFs to itself
    K.unregister_openwith()
    try:
        run_as_build(False)
        check("no install + build copy: auto-registration refused", not K.startup_registration_allowed() and K.startup_register() == "")
        check("nothing written", g(p["progid"] + r"\shell\open\command", "") is None and g(p["app"] + r"\shell\open\command", "") is None)
    finally:
        restore()

    # ---- installer keeps the icon/registration right
    iss = (PROJECT / "KoinfraPDF.iss").read_text(encoding="utf-8")
    check("installer: Applications\\exe DefaultIcon + Capabilities ApplicationIcon = {app}\\exe,0",
          'Subkey: "Software\\Classes\\Applications\\{#AppExeName}\\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\\{#AppExeName},0"' in iss
          and 'ValueName: "ApplicationIcon"; ValueData: "{app}\\{#AppExeName},0"' in iss)
    check("installer: refreshes the icon cache (ie4uinit -show, explorer untouched) + ChangesAssociations",
          "ie4uinit.exe'), '-show'" in iss and "ChangesAssociations=yes" in iss and "taskkill" not in iss.lower())
    check("installer: stale App Paths 'Path' removed", 'ValueName: "Path"; Flags: deletevalue' in iss)
    iss_code = "\n".join(line for line in iss.splitlines() if not line.lstrip().startswith((";", "//")))
    check("installer never writes UserChoice", "userchoice" not in iss_code.lower())
    spec_text = (PROJECT / "KoinfraPDFEdit_installer.spec").read_text(encoding="utf-8")
    check("exe built with the app icon resource", "icon=['icon2.ico']" in spec_text and (PROJECT / "icon2.ico").is_file())
    check("real registry untouched", real_snapshot() == before, before)


try:
    main()
except Exception:
    traceback.print_exc(); RESULTS.append(("unhandled exception", False, ""))
finally:
    K._reg_delete_tree(BASE)
    try:
        winreg.DeleteKey(HK, r"Software\KoinfraTest")  # only when empty
    except OSError:
        pass
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT {passed}/{len(RESULTS)} passed", flush=True)
    (OUT / "registration_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0 if passed == len(RESULTS) else 1)

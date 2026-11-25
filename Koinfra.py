import os
import sys
import tempfile
import winreg
import ctypes
import shutil
import subprocess
import time
import uuid
from pathlib import Path

# The kpdf_* modules sit next to this file; also importable when the tests load Koinfra.py by path.
_APP_SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_SOURCE_DIR not in sys.path:
    sys.path.insert(0, _APP_SOURCE_DIR)

try:
    import importlib.util

    for _required in ("PIL", "pypdf"):  # imported lazily (on save/print); only checked for presence here
        if importlib.util.find_spec(_required) is None:
            raise ImportError(f"No module named '{_required}'")
    from PyQt5.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
    from PyQt5.QtGui import (
        QColor, QDragEnterEvent, QDropEvent, QFont, QIcon, QImage, QKeySequence, QPainter, QPen,
        QPixmap,
    )
    from PyQt5.QtWidgets import (
        QAction, QApplication, QCheckBox, QDialog, QFrame, QMenu, QFileDialog, QHBoxLayout,
        QInputDialog, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
        QSpinBox, QSplitter, QStatusBar, QToolBar, QToolButton, QVBoxLayout, QWidget,
    )
    from kpdf_core import APP_DIR, APP_EXE_NAME, APP_ICON2_ICO, APP_ICON_ICO, APP_PROG_ID, APP_TITLE, APP_USER_MODEL_ID, INSTALLER_APP_ID, PAGE_ROLE, PDF_DOC_ICON_NAME, PDF_FILTER, PRINT_DPI, PageMark, PageRef, RESOURCE_DIR, _read_preferences_payload, _write_preferences_payload, load_signature_settings, load_stamp_settings, load_user_preferences, normalize_approval_line, normalize_signature_settings, normalize_stamp_settings, page_refs_for_pdf, parse_page_ranges, pdfium_page_to_qimage, remove_white_background, save_signature_settings, save_stamp_settings, save_user_preferences, user_data_dir
    from kpdf_widgets import PageThumbnailList, PdfCanvasLabel, PdfScrollArea, SignatureDialog, StampSettingsDialog
    from kpdf_graphics import load_app_icon, make_page_icon, make_tool_icon, paint_approval_table
    from kpdf_export import PdfExportMixin
    from kpdf_core import PDF_SOURCES, RENDER_CACHE_MAX_BYTES, RENDER_CACHE_MAX_ITEMS, TOOL_ICON_DIR, pdf_page_sizes
    from kpdf_graphics import approval_geometry, approval_signature_cell_rect
except ImportError as exc:
    print(
        "\ud544\uc218 \ud328\ud0a4\uc9c0\uac00 \uc124\uce58\ub418\uc5b4 \uc788\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.\n"
        "\uc544\ub798 \uba85\ub839\uc73c\ub85c \uc124\uce58\ud558\uc138\uc694.\n"
        "  py -3 -m pip install PyQt5 pypdfium2 pypdf pillow\n\n"
        f"Import error: {exc}"
    )
    raise

# Moved into kpdf_* modules but still part of this module's surface (the test suites load Koinfra.py as `K`).
_REEXPORTED = (TOOL_ICON_DIR, save_stamp_settings, approval_geometry, approval_signature_cell_rect)


def configure_windows_taskbar_icon():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def installer_location_exe():
    """KoinfraPDFEdit.exe at the Koinfra PDF installer's InstallLocation (per-user or all users), else None."""
    if sys.platform != "win32":
        return None
    subkey = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + INSTALLER_APP_ID + "_is1"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                location = winreg.QueryValueEx(key, "InstallLocation")[0]
        except OSError:
            continue
        exe = Path(location) / APP_EXE_NAME
        if exe.is_file():
            return exe
    return None


def installed_exe_path():
    """The exe PDF files should open with: the installed copy whenever there is one (a build or test copy that happens
    to run must never take over the registration), else the running frozen exe, else None (script)."""
    exe = installer_location_exe()
    if exe is not None:
        return exe
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def app_icon_reference():
    exe = installed_exe_path()
    if exe is not None:
        return f"{exe},0"
    if APP_ICON2_ICO.is_file():
        return str(APP_ICON2_ICO)
    if APP_ICON_ICO.is_file():
        return str(APP_ICON_ICO)
    return str(Path(__file__).resolve())


def pdf_document_icon_reference():
    """Blue Koinfra PDF-document icon for files of our ProgID (falls back to the app icon)."""
    exe = installed_exe_path()
    candidates = [exe.parent / PDF_DOC_ICON_NAME] if exe is not None else []
    candidates += [RESOURCE_DIR / PDF_DOC_ICON_NAME, APP_DIR / PDF_DOC_ICON_NAME]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return app_icon_reference()


def build_openwith_command():
    exe = installed_exe_path()
    if exe is not None:
        return f'"{exe}" "%1"'
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    return f'"{executable}" "{Path(__file__).resolve()}" "%1"'


# ---------------------------------------------------------------- PDF file association (per user, HKCU only)
# Windows 10/11 protect the real default (UserChoice + hash). We never write UserChoice: we only register
# the app as a PDF handler and open the Windows "Default apps" page so the user picks it there.
ASSOC_BASE = "Software"  # tests inject a throwaway root such as Software\KoinfraTest\<id>
APP_FRIENDLY_NAME = "Koinfra PDF"
APP_REGISTERED_NAME = APP_TITLE  # value name under RegisteredApplications (= registeredAppUser=...)
SHCNE_ASSOCCHANGED = 0x08000000


def _assoc_paths(base=None):
    base = base or ASSOC_BASE
    classes = base + r"\Classes"
    company = base + r"\Koinfra\KoinfraPDFEdit"
    return {
        "app": classes + "\\Applications\\" + APP_EXE_NAME,
        "progid": classes + "\\" + APP_PROG_ID,
        "openwith": classes + r"\.pdf\OpenWithProgids",
        "openwithlist": classes + r"\.pdf\OpenWithList" + "\\" + APP_EXE_NAME,
        "company": company,
        "capabilities": company + r"\Capabilities",
        "registered": base + r"\RegisteredApplications",
        "fileexts": base + r"\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf",
    }


def _reg_set(path, name, value, hive=None):
    with winreg.CreateKeyEx(hive or winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _reg_get(path, name, hive=None):
    try:
        with winreg.OpenKey(hive or winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _reg_delete_tree(path, hive=None):
    hive = hive or winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            children = []
            index = 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return
    for child in children:
        _reg_delete_tree(path + "\\" + child, hive)
    try:
        winreg.DeleteKey(hive, path)
    except OSError:
        pass


def _reg_delete_value(path, name, hive=None):
    try:
        with winreg.OpenKey(hive or winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def notify_association_changed():
    try:
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)
    except (AttributeError, OSError):
        pass


def association_target_warning():
    """Explain when the registered command points at a location that may disappear (build output)."""
    target = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    lowered = [part.lower() for part in target.parts]
    if any(part.startswith(("dist", "build")) for part in lowered) or "temp" in lowered:
        return (
            f"\ud604\uc7ac \uc2e4\ud589 \uc704\uce58({target.parent})\uac00 \ube4c\ub4dc/\uc784\uc2dc \ud3f4\ub354\uc785\ub2c8\ub2e4. "
            "\ud30c\uc77c\uc744 \uc62e\uae30\uac70\ub098 \uc9c0\uc6b0\uba74 \uc5f0\uacb0\uc774 \ub04a\uc5b4\uc9c0\ub2c8 \uc124\uce58\ubcf8 \uc0ac\uc6a9\uc744 \uad8c\uc7a5\ud569\ub2c8\ub2e4."
        )
    return ""


def _command_exe(command):
    """First (quoted) path of a registered shell command."""
    command = (command or "").strip()
    if command.startswith('"'):
        return command[1:].split('"', 1)[0]
    return command.split(" ", 1)[0]


def registration_broken(base=None, hive=None):
    """True when our Open-with / ProgID registration points at a program or icon file that no longer exists."""
    paths = _assoc_paths(base)
    for key in (paths["progid"] + r"\shell\open\command", paths["app"] + r"\shell\open\command"):
        command = _reg_get(key, "", hive)
        if command and not Path(_command_exe(command)).is_file():
            return True
    for key, name in ((paths["app"] + r"\DefaultIcon", ""), (paths["capabilities"], "ApplicationIcon")):
        icon = _reg_get(key, name, hive)
        if icon and not Path(icon.rsplit(",", 1)[0] if icon.rsplit(",", 1)[-1].lstrip("-").isdigit() else icon).is_file():
            return True
    return False


def startup_registration_allowed():
    """Auto-registration at start: always fine when the app is installed (it registers the installed exe);
    without an installed copy, never bind PDFs to a build/temp folder (it vanishes -> 'Open with' does nothing)."""
    if os.environ.get("KOINFRA_NO_REGISTER"):
        return False
    if installer_location_exe() is not None:
        return True
    return not association_target_warning()


def registration_current(base=None, hive=None):
    """True when every value register_openwith() would write is already there (nothing to do at start)."""
    paths = _assoc_paths(base)
    command = build_openwith_command()
    icon_ref = app_icon_reference()
    expected = (
        (paths["app"] + r"\shell\open\command", "", command),
        (paths["app"] + r"\DefaultIcon", "", icon_ref),
        (paths["progid"] + r"\shell\open\command", "", command),
        (paths["progid"] + r"\DefaultIcon", "", pdf_document_icon_reference()),
        (paths["capabilities"], "ApplicationIcon", icon_ref),
        (paths["capabilities"] + r"\FileAssociations", ".pdf", APP_PROG_ID),
        (paths["registered"], APP_REGISTERED_NAME, paths["capabilities"]),
        (paths["openwith"], APP_PROG_ID, ""),
    )
    return all(_reg_get(key, name, hive) == value for key, name, value in expected)


def startup_register(base=None, hive=None):
    """Called at start. Keeps the registration current, and repairs a stale one even when auto-registration was turned off.
    Returns 'registered', 'repaired', 'current' (already right: nothing written, no shell refresh) or ''."""
    if not startup_registration_allowed():
        return ""
    broken = registration_broken(base, hive)
    if load_association_preferences()["disabled"] and not broken:
        return ""
    if not broken and registration_current(base, hive):
        return "current"
    register_openwith(base, hive)
    return "repaired" if broken else "registered"


def register_openwith(base=None, hive=None, notify=True):
    """Register Koinfra PDF as a PDF handler for the current user (Open with list + Default apps page)."""
    paths = _assoc_paths(base)
    command = build_openwith_command()
    icon_ref = app_icon_reference()

    _reg_set(paths["app"], "FriendlyAppName", APP_FRIENDLY_NAME, hive)
    _reg_set(paths["app"] + r"\DefaultIcon", "", icon_ref, hive)
    _reg_set(paths["app"] + r"\SupportedTypes", ".pdf", "", hive)
    _reg_set(paths["app"] + r"\shell\open\command", "", command, hive)

    _reg_set(paths["progid"], "", "Koinfra PDF \ubb38\uc11c", hive)
    _reg_set(paths["progid"], "FriendlyTypeName", "Koinfra PDF \ubb38\uc11c", hive)
    _reg_set(paths["progid"] + r"\DefaultIcon", "", pdf_document_icon_reference(), hive)
    _reg_set(paths["progid"] + r"\shell\open", "FriendlyAppName", APP_FRIENDLY_NAME, hive)
    _reg_set(paths["progid"] + r"\shell\open\command", "", command, hive)
    _reg_set(paths["openwith"], APP_PROG_ID, "", hive)
    _reg_set(paths["openwithlist"], "", "", hive)
    _reg_set(paths["progid"], "AppUserModelID", APP_USER_MODEL_ID, hive)

    _reg_set(paths["capabilities"], "ApplicationName", APP_FRIENDLY_NAME, hive)
    _reg_set(paths["capabilities"], "ApplicationDescription", "Koinfra PDF \ud3b8\uc9d1\uae30 (\ubcd1\ud569\u00b7\ub3c4\uc7a5\u00b7\uacb0\uc7ac)", hive)
    _reg_set(paths["capabilities"], "ApplicationIcon", icon_ref, hive)
    _reg_set(paths["capabilities"] + r"\FileAssociations", ".pdf", APP_PROG_ID, hive)
    _reg_set(paths["registered"], APP_REGISTERED_NAME, paths["capabilities"], hive)
    if notify:
        notify_association_changed()
    return paths


def unregister_openwith(base=None, hive=None, notify=True):
    """Remove everything register_openwith() wrote. The Windows UserChoice is left alone (it is protected)."""
    paths = _assoc_paths(base)
    _reg_delete_tree(paths["app"], hive)
    _reg_delete_tree(paths["progid"], hive)
    _reg_delete_value(paths["openwith"], APP_PROG_ID, hive)
    _reg_delete_tree(paths["openwithlist"], hive)
    _reg_delete_value(paths["registered"], APP_REGISTERED_NAME, hive)
    _reg_delete_tree(paths["company"], hive)
    company_root = paths["company"].rsplit("\\", 1)[0]
    try:
        winreg.DeleteKey(hive or winreg.HKEY_CURRENT_USER, company_root)  # only succeeds when empty
    except OSError:
        pass
    if notify:
        notify_association_changed()


def is_pdf_association_registered(base=None, hive=None):
    paths = _assoc_paths(base)
    return bool(_reg_get(paths["registered"], APP_REGISTERED_NAME, hive)) and bool(
        _reg_get(paths["progid"] + r"\shell\open\command", "", hive)
    )


def current_pdf_default_progid(base=None, hive=None):
    """Read-only: the ProgId Windows uses for .pdf (UserChoiceLatest on new Windows 11, else UserChoice)."""
    paths = _assoc_paths(base)
    return _reg_get(paths["fileexts"] + r"\UserChoiceLatest\ProgId", "ProgId", hive) or _reg_get(
        paths["fileexts"] + r"\UserChoice", "ProgId", hive
    )


def is_pdf_default_app(base=None, hive=None):
    return current_pdf_default_progid(base, hive) in (APP_PROG_ID, "Applications\\" + APP_EXE_NAME)


def default_apps_settings_target():
    """Windows 11 (build 22000+): Settings > Default apps opened on this app's page; Windows 10: Default apps."""
    try:
        build = sys.getwindowsversion().build
    except AttributeError:
        build = 0
    if build >= 22000:
        return f"ms-settings:defaultapps?registeredAppUser={APP_REGISTERED_NAME}"
    return "ms-settings:defaultapps"


def open_default_pdf_app_settings(launcher=None):
    if sys.platform != "win32" and launcher is None:
        raise OSError("Windows default app settings are only available on Windows.")
    register_openwith()
    target = default_apps_settings_target()
    (launcher or os.startfile)(target)
    return target


# Windows 'choose an app' picker. Windows 11 blocks SHOpenWithDialog for apps ("go to Settings > Default apps"),
# so we ask the shell's own picker (rundll32 shell32,OpenAs_RunDLL) for one small check PDF and verify that a real
# picker window appeared; otherwise we fall back to the Settings page. The choice itself is always the user's.
OPEN_WITH_PICKER_TIMEOUT_MS = 6000
DEFAULT_APP_WATCH_MS = 5 * 60 * 1000
DEFAULT_APP_GUIDE_PICKER = "\ub728\ub294 \ucc3d\uc5d0\uc11c Koinfra PDF \uc120\ud0dd \u2192 '\ud56d\uc0c1' \ud074\ub9ad"
DEFAULT_APP_GUIDE_SETTINGS = "\uc124\uc815 \ucc3d\uc5d0\uc11c .pdf \ud074\ub9ad \u2192 Koinfra PDF \uc120\ud0dd \u2192 '\uae30\ubcf8\uac12 \uc124\uc815' \ud074\ub9ad"
DEFAULT_APP_DONE_TEXT = "\u2714 \uc124\uc815 \uc644\ub8cc"


def default_app_check_pdf_path():
    """A one-page PDF in %TEMP% used only as the picker's sample file (opens in Koinfra after 'Always')."""
    folder = Path(tempfile.gettempdir()) / "KoinfraPDFEdit"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "Koinfra_PDF_default_app_check.pdf"
    if path.is_file() and path.stat().st_size > 0:
        return path
    from PyQt5.QtGui import QPdfWriter, QPageSize

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setResolution(72)
    writer.setTitle("Koinfra PDF")
    painter = QPainter(writer)
    painter.setPen(QColor("#1F5EFF"))
    painter.setFont(QFont("Malgun Gothic", 22, QFont.Bold))
    painter.drawText(QRectF(40, 260, 515, 60), Qt.AlignCenter, DEFAULT_APP_DONE_TEXT)
    painter.setPen(QColor("#334155"))
    painter.setFont(QFont("Malgun Gothic", 13))
    painter.drawText(
        QRectF(40, 330, 515, 60), Qt.AlignCenter,
        "\uc774\uc81c PDF \ud30c\uc77c\uc744 \uc5f4\uba74 Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4.\n\uc774 \ucc3d\uc740 \ub2eb\uc544\ub3c4 \ub429\ub2c8\ub2e4.",
    )
    painter.end()
    return path


def _process_image_name(pid):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def open_with_process_ids():
    """PIDs of running OpenWith.exe processes (the Windows app picker host)."""
    if sys.platform != "win32":
        return set()
    array = (ctypes.c_ulong * 4096)()
    needed = ctypes.c_ulong()
    try:
        if not ctypes.windll.psapi.EnumProcesses(array, ctypes.sizeof(array), ctypes.byref(needed)):
            return set()
    except (AttributeError, OSError):
        return set()
    count = needed.value // ctypes.sizeof(ctypes.c_ulong)
    return {pid for pid in array[:count] if pid and _process_image_name(pid) == "openwith.exe"}


def _visible_windows_of(pids):
    """Visible, non-minimized top-level windows (hwnd, pid, class) owned by the given processes."""
    if sys.platform != "win32" or not pids:
        return []
    user32 = ctypes.windll.user32
    found = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _param):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(ctypes.c_void_p(hwnd)) and not user32.IsIconic(ctypes.c_void_p(hwnd)):
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(ctypes.c_void_p(hwnd), name, 256)
            if "dummy" not in name.value.lower() and name.value not in ("IME", "MSCTFIME UI"):
                found.append((hwnd, pid.value, name.value))
        return True

    user32.EnumWindows(proc_type(callback), None)
    return found


def launch_open_with_picker(pdf_path):
    """Ask the Windows shell to show its 'choose an app' picker for pdf_path. Returns the helper process."""
    if sys.platform != "win32":
        raise OSError("The Windows app picker is only available on Windows.")
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_ulong(0xFFFFFFFF))  # ASFW_ANY: let the picker come to front
    except (AttributeError, OSError):
        pass
    rundll = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "rundll32.exe"
    return subprocess.Popen([str(rundll), "shell32.dll,OpenAs_RunDLL", str(pdf_path)])


def close_windows(windows):
    for hwnd, _pid, _cls in windows:
        try:
            ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE
        except (AttributeError, OSError):
            pass


def stop_open_with_leftovers(process, before_pids):
    """After a failed attempt: end our rundll32 helper and the picker hosts it started (never pre-existing ones)."""
    if process is not None and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    for pid in open_with_process_ids() - set(before_pids or ()):
        if _visible_windows_of({pid}):
            continue  # it has a real window after all; leave it to the user
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 0)
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            pass


def load_association_preferences():
    value = _read_preferences_payload().get("pdf_association")
    value = value if isinstance(value, dict) else {}
    return {"disabled": bool(value.get("disabled", False)), "ask_default": bool(value.get("ask_default", True))}


def save_association_preferences(**changes):
    payload = _read_preferences_payload()
    current = load_association_preferences()
    current.update({key: bool(value) for key, value in changes.items() if key in current})
    payload["pdf_association"] = current
    _write_preferences_payload(payload)
    return current


class PdfPreviewer(PdfExportMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_pdf_path = None
        self.page_count = 0
        self.current_page_index = 0
        self.zoom = 1.35
        self.zoom_factor = 1.0
        self.preview_refresh_pending = False
        self.thumbnail_refresh_pending = False
        self.thumbnail_scale = 0.16  # legacy; thumbnails are rendered at their shown size (render_thumbnail_pixmap)
        self.thumbnail_cache = {}
        self.placeholder_icon_cache = {}
        self.page_size_cache = {}
        self.page_render_cache = {}
        self.preview_reference_width = None
        self.preview_reference_height = None
        self.preview_content_rect = QRectF()
        self.preview_content_size = QSize()
        self.last_thumbnail_layout = None
        self.thumbnail_view_anchor = None  # (current page visible?, scroll fraction) from before a panel resize
        self.pdf_passwords = {}
        self.approval_line, self.stamp_image_paths = load_user_preferences()
        self.stamp_settings = load_stamp_settings()
        self.signature_settings = load_signature_settings()
        self.last_stamp_dialog_inserted = False
        self.stamp_settings_dialog = None
        self.signature_dialog = None
        self.stamp_menu = None
        self.approval_menu = None
        self.toolbar_context_buttons = []
        self.convert_dialog = None
        self.default_app_watch = None
        self.default_app_guide = None
        self.open_with_attempt = None
        self.default_app_done_shown = False
        app_instance = QApplication.instance()
        if app_instance is not None:
            app_instance.applicationStateChanged.connect(self.on_application_state_changed)
        self.cut_buffer = []
        self.copy_buffer = []
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.selected_mark_index = None
        self.undo_stack = []
        self.redo_stack = []
        self.preview_refresh_timer = QTimer(self)
        self.preview_refresh_timer.setSingleShot(True)
        self.preview_refresh_timer.timeout.connect(self._flush_responsive_preview_refresh)
        self.thumbnail_refresh_timer = QTimer(self)
        self.thumbnail_refresh_timer.setSingleShot(True)
        self.thumbnail_refresh_timer.timeout.connect(self._flush_thumbnail_refresh)
        # Ctrl+wheel zoom: show a scaled copy immediately, render sharply once the wheel pauses.
        self.zoom_render_timer = QTimer(self)
        self.zoom_render_timer.setSingleShot(True)
        self.zoom_render_timer.timeout.connect(self._flush_zoom_render)
        self.zoom_render_anchor = None
        self.preview_page_pixmap = None
        self.preview_pixmap_zoom = None
        self.wheel_page_remainder = 0

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(load_app_icon())
        self.resize(1280, 860)
        self.setMinimumSize(1040, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(self.app_stylesheet())

        self._build_menu()

        self.page_list = PageThumbnailList(self)
        self.page_list.setObjectName("pageList")
        self.page_list.setMinimumWidth(128)
        self.page_list.setViewMode(QListWidget.IconMode)
        self.page_list.setResizeMode(QListWidget.Adjust)
        self.page_list.setMovement(QListWidget.Snap)
        self.page_list.setSpacing(6)
        self.page_list.currentRowChanged.connect(self.show_page)
        self.page_list.verticalScrollBar().valueChanged.connect(self.schedule_thumbnail_refresh)
        self.thumbnail_item_width = 82
        self.thumbnail_item_height = 116
        self.thumbnail_canvas_width = 90
        self.thumbnail_canvas_height = 122
        self.update_thumbnail_layout()

        self.page_label = PdfCanvasLabel(
            self, "PDF \ud30c\uc77c\uc744 \uc5f4\uac70\ub098 \uc774 \ud654\uba74\uc73c\ub85c \ub4dc\ub798\uadf8\ud574\uc11c \ucd94\uac00\ud558\uc138\uc694."
        )
        self.page_label.setObjectName("pageCanvas")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumSize(640, 480)
        self.page_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.page_label.customContextMenuRequested.connect(self.show_preview_context_menu)

        self.scroll_area = PdfScrollArea(self)
        self.scroll_area.setObjectName("documentArea")
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll_area.setWidget(self.page_label)

        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(0)
        self.page_spin.setMaximum(0)
        self.page_spin.setPrefix("\ud398\uc774\uc9c0 ")
        self.page_spin.setObjectName("pageSpin")
        self.page_spin.valueChanged.connect(self.go_to_page)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("zoomLabel")

        self._build_toolbar()
        self._build_layout()
        self.setStatusBar(QStatusBar())
        self.schedule_thumbnail_refresh()
        self.set_status("\uc900\ube44\ub428")

    def app_stylesheet(self):
        return """
            QMainWindow {
                background: #f3f4f6;
                font-family: "Malgun Gothic", "Segoe UI", Arial, sans-serif;
                font-size: 12px;
                color: #172033;
            }
            QMenuBar {
                background: #ffffff;
                border-bottom: 1px solid #d9dee7;
                padding: 3px 8px;
            }
            QMenuBar::item {
                padding: 5px 10px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background: #e8f0ff;
                color: #155eef;
            }
            QMenu {
                background: #ffffff;
                border: 1px solid #d9dee7;
                padding: 4px;
            }
            QMenu::item {
                padding: 7px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #eaf2ff;
                color: #155eef;
            }
            QToolBar {
                background: #ffffff;
                border: 0;
                border-bottom: 1px solid #d9dee7;
                spacing: 2px;
                padding: 4px 6px 2px 6px;
            }
            QToolButton {
                color: #1f2937;
                min-width: 48px;
                padding: 4px 5px;
                border: 1px solid transparent;
                border-radius: 5px;
            }
            #toolbarGroupLabel {
                color: #8f99a8;
                font-size: 10px;
                padding: 0px 0px 0px 0px;
            }
            #toolbarGroupDivider {
                color: #e3e7ee;
                background: #e3e7ee;
                margin: 4px 2px 4px 2px;
            }
            #toolbarGroup QToolButton {
                min-width: 32px;
                padding: 2px 3px;
            }
            QSpinBox#pageSpin {
                min-width: 60px;
                padding: 4px 5px;
            }
            QToolButton:hover {
                background: #f1f5f9;
                border-color: #cbd5e1;
            }
            QToolButton:pressed {
                background: #e2e8f0;
            }
            #leftPanel {
                background: #ffffff;
                border-right: 1px solid #d9dee7;
            }
            #panelTitle {
                color: #0f172a;
                font-size: 14px;
                font-weight: 800;
                padding: 10px 4px 2px 4px;
            }
            #panelHint {
                color: #64748b;
                font-size: 11px;
                padding: 0 4px 8px 4px;
            }
            #pageList {
                background: #ffffff;
                border: 0;
                outline: 0;
            }
            #pageList::item {
                color: #334155;
                padding: 6px 3px;
                border: 1px solid transparent;
                border-radius: 8px;
            }
            #pageList::item:selected {
                background: #bfdbfe;
                border: 1px solid #1d4ed8;
                color: #0f172a;
            }
            #pageList::item:hover {
                background: #e0f2fe;
            }
            #documentArea {
                background: #e5e7eb;
                border: 0;
            }
            QSplitter::handle {
                background: #d1d5db;
            }
            QSplitter::handle:horizontal {
                width: 8px;
            }
            QSplitter::handle:hover {
                background: #94a3b8;
            }
            #pageCanvas {
                background: #ffffff;
                color: #64748b;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                padding: 6px 9px;
                color: #172033;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #eff6ff;
                border-color: #60a5fa;
            }
            QSpinBox {
                min-width: 94px;
                padding: 5px 7px;
                border: 1px solid #334155;
                border-radius: 5px;
                background: #ffffff;
                color: #0f172a;
                font-weight: 700;
            }
            #zoomLabel {
                min-width: 48px;
                color: #334155;
                font-weight: 700;
                padding-left: 4px;
            }
            QStatusBar {
                background: #ffffff;
                border-top: 1px solid #d9e2ef;
                color: #475569;
                font-weight: 600;
            }
        """

    def _build_menu(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("\ud30c\uc77c")
        view_menu = menu_bar.addMenu("\ubcf4\uae30")
        edit_menu = menu_bar.addMenu("\ud3b8\uc9d1")
        tool_menu = menu_bar.addMenu("\ub3c4\uad6c")

        file_menu.addAction(self.make_action("\uc5f4\uae30", self.open_pdf, "open"))
        file_menu.addAction(self.make_action("\uc800\uc7a5", self.save_current, "save", shortcut=QKeySequence.Save))
        file_menu.addAction(self.make_action("\ub2e4\ub978 \uc774\ub984\uc73c\ub85c \uc800\uc7a5", self.save_copy_as, "save"))
        file_menu.addAction(self.make_action("\uc778\uc1c4", self.print_pdf, "print", shortcut=QKeySequence.Print))
        file_menu.addSeparator()
        file_menu.addAction(self.make_action("\uc885\ub8cc", self.close))

        view_menu.addAction(self.make_action("\ud655\ub300", self.zoom_in, "zoom_in"))
        view_menu.addAction(self.make_action("\ucd95\uc18c", self.zoom_out, "zoom_out"))
        view_menu.addAction(self.make_action("\uc774\uc804 \ud398\uc774\uc9c0", self.previous_page, "prev"))
        view_menu.addAction(self.make_action("\ub2e4\uc74c \ud398\uc774\uc9c0", self.next_page, "next"))

        mark_menu = menu_bar.addMenu("\uc8fc\uc11d")
        mark_menu.addAction(self.make_action("\ube68\uac04 \uc0ac\uac01\ud615", self.enable_rectangle_mode, "rect_mark"))
        mark_menu.addAction(self.make_action("\ud615\uad11\ud39c", self.enable_highlight_mode, "highlight"))
        mark_menu.addSeparator()
        mark_menu.addAction(self.make_action("도장 찍기", self.insert_default_stamp, "stamp"))
        mark_menu.addAction(self.make_action("도장 설정…", self.open_stamp_settings, "settings"))
        mark_menu.addAction(self.make_action("PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc\u00b7\ubcf4\uad00", self.import_stamp_png, "image_plus"))
        mark_menu.addAction(self.make_action("\uc800\uc7a5\ub41c PNG \ub3c4\uc7a5 \uc0bd\uc785", self.insert_saved_png_stamp, "stamp"))
        mark_menu.addSeparator()
        mark_menu.addAction(self.make_action("결재 (내 서명 포함)", self.insert_signed_approval, "signature"))
        mark_menu.addAction(self.make_action("\uacb0\uc7ac \ub3c4\uc7a5\uce78 (서명 없이)", self.add_approval_stamp_to_first_page, "approval_stamp"))
        mark_menu.addAction(self.make_action("서명 등록…", self.open_signature_settings, "pen_line"))
        mark_menu.addAction(self.make_action("\uacb0\uc7ac\uc120 \uc124\uc815", self.configure_approval_line, "settings"))
        mark_menu.addSeparator()
        mark_menu.addAction(self.make_action("\uc8fc\uc11d \ubaa8\ub4dc \ub044\uae30", self.disable_annotation_mode))

        convert_menu = menu_bar.addMenu("변환")
        convert_menu.addAction(self.make_action("엑셀로 변환… (.xlsx)", self.convert_to_excel, "conv_excel"))
        convert_menu.addAction(self.make_action("한글로 변환… (.hwpx/.hwp)", self.convert_to_hwp, "conv_hwp"))
        convert_menu.addAction(self.make_action("이미지로 변환… (PNG/JPG)", self.convert_to_image, "conv_image"))
        self.convert_menu = convert_menu

        edit_menu.addAction(self.make_action("\uc798\ub77c\ub0b4\uae30", self.cut_selected_pages, "cut", shortcut=QKeySequence.Cut))
        edit_menu.addAction(self.make_action("\ubcf5\uc0ac\ud558\uae30", self.copy_selected_pages, "copy", shortcut=QKeySequence.Copy))
        edit_menu.addAction(self.make_action("\ubd99\uc5ec\ub123\uae30", self.paste_pages_after_current, "paste", shortcut=QKeySequence.Paste))
        edit_menu.addAction(self.make_action("\uc9c0\uc6b0\uae30", self.delete_selected_pages, "delete", shortcut=QKeySequence.Delete))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("\ub418\ub3cc\ub9ac\uae30", self.undo, shortcut=QKeySequence.Undo))
        edit_menu.addAction(self.make_action("\ub2e4\uc2dc \uc2e4\ud589", self.redo, shortcut=QKeySequence.Redo))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("\uc624\ub978\ucabd \ud68c\uc804", self.rotate_selected_pages_right, "rotate_right"))
        edit_menu.addAction(self.make_action("\uc67c\ucabd \ud68c\uc804", self.rotate_selected_pages_left, "rotate_left"))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("PDF \ubcd1\ud569", self.merge_pdfs, "merge"))
        edit_menu.addAction(self.make_action("\ud398\uc774\uc9c0 \ucd94\ucd9c", self.extract_pages, "extract"))
        edit_menu.addAction(self.make_action("PDF \uc0bd\uc785", self.insert_pdf, "insert"))
        edit_menu.addSeparator()
        edit_menu.addAction(self.make_action("PDF \uc554\ud638 \uc124\uc815", self.save_pdf_with_password, "lock"))
        edit_menu.addAction(self.make_action("PDF \uc554\ud638 \ud574\uc81c", self.save_pdf_without_password, "unlock"))

        tool_menu.addAction(self.make_action("\ud604\uc7ac \ud30c\uc77c \uc815\ubcf4", self.show_file_info, "info"))
        tool_menu.addSeparator()
        self.default_app_action = self.make_action("PDF \uae30\ubcf8 \uc571\uc73c\ub85c \uc124\uc815\u2026", self.set_pdf_default_app)  # no icon: the check mark shows
        self.default_app_action.setCheckable(True)
        tool_menu.addAction(self.default_app_action)
        self.unregister_assoc_action = self.make_action("PDF \uc5f0\uacb0 \ub4f1\ub85d \ud574\uc81c", self.unregister_openwith_from_menu)
        tool_menu.addAction(self.unregister_assoc_action)
        tool_menu.aboutToShow.connect(self.refresh_default_app_menu)
        self.tool_menu = tool_menu

    def _build_toolbar(self):
        toolbar = QToolBar("PDF \ub3c4\uad6c")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        toolbar.setIconSize(QSize(28, 28))
        self.addToolBar(toolbar)

        groups = [
            ("\ud30c\uc77c", [
                ("\uc5f4\uae30", self.open_pdf, "open"),
                ("\uc800\uc7a5", self.save_current, "save"),
                ("\uc778\uc1c4", self.print_pdf, "print"),
            ]),
            ("\ud398\uc774\uc9c0", [
                ("\ubcd1\ud569", self.merge_pdfs, "merge"),
                ("\ucd94\ucd9c", self.extract_pages, "extract"),
                ("\uc0bd\uc785", self.insert_pdf, "insert"),
            ]),
            ("\ubcf4\uc548", [
                ("\uc554\ud638", self.save_pdf_with_password, "lock"),
                ("\ud574\uc81c", self.save_pdf_without_password, "unlock"),
            ]),
            ("\uc8fc\uc11d", [
                ("\uc0ac\uac01\ud615", self.enable_rectangle_mode, "rect_mark"),
                ("\ud615\uad11\ud39c", self.enable_highlight_mode, "highlight"),
            ]),
            ("\uc11c\uba85", [
                ("\ub3c4\uc7a5", self.insert_default_stamp, "stamp"),
                ("\uacb0\uc7ac", self.insert_signed_approval, "signature"),
            ]),
            ("변환", [
                ("변환", self.open_convert_dialog, "convert"),
            ]),
            ("\ubcf4\uae30", [
                ("\uc6b0\ud68c\uc804", self.rotate_selected_pages_right, "rotate_right"),
                ("\uc88c\ud68c\uc804", self.rotate_selected_pages_left, "rotate_left"),
                ("\uc774\uc804", self.previous_page, "prev"),
                ("\ub2e4\uc74c", self.next_page, "next"),
                ("\ucd95\uc18c", self.zoom_out, "zoom_out"),
                ("\ud655\ub300", self.zoom_in, "zoom_in"),
            ]),
        ]

        self.toolbar_groups = []
        self.toolbar_buttons = {}
        for group_index, (group_name, items) in enumerate(groups):
            if group_index:
                divider = QFrame()
                divider.setObjectName("toolbarGroupDivider")
                divider.setFrameShape(QFrame.VLine)
                divider.setFixedWidth(1)
                toolbar.addWidget(divider)
            group = QWidget()
            group.setObjectName("toolbarGroup")
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(1, 0, 1, 0)
            group_layout.setSpacing(0)
            # Never squeeze (that elides the labels); if the window is really narrow the toolbar's
            # overflow (>>) menu takes whole groups instead.
            group_layout.setSizeConstraint(QLayout.SetFixedSize)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            group_actions = []
            for label, handler, icon_key in items:
                action = self.make_action(label, handler, icon_key)
                button = QToolButton(group)
                button.setDefaultAction(action)
                button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                button.setIconSize(QSize(28, 28))
                button.setAutoRaise(True)
                row.addWidget(button)
                group_actions.append(action)
                self.toolbar_buttons[action] = button
                if handler == self.insert_default_stamp:
                    self.stamp_toolbar_action = action
                    self.attach_toolbar_context_menu(toolbar, action, self.show_stamp_button_menu, "\ub3c4\uc7a5 \ucc0d\uae30 (\uc6b0\ud074\ub9ad: \ub3c4\uc7a5 \uc124\uc815\u00b7\uc800\uc7a5)")
                elif handler == self.insert_signed_approval:
                    self.approval_toolbar_action = action
                    self.attach_toolbar_context_menu(toolbar, action, self.show_approval_button_menu, "\uacb0\uc7ac + \ub0b4 \uc11c\uba85 (\uc6b0\ud074\ub9ad: \uc11c\uba85 \ub4f1\ub85d\u00b7\uc800\uc7a5)")
            group_layout.addLayout(row)
            caption = QLabel(group_name)
            caption.setObjectName("toolbarGroupLabel")
            caption.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            group_layout.addWidget(caption)
            toolbar.addWidget(group)
            self.toolbar_groups.append((group_name, group_actions, caption))

        toolbar.addSeparator()
        toolbar.addWidget(self.page_spin)
        toolbar.addSeparator()
        toolbar.addWidget(self.zoom_label)

    def attach_toolbar_context_menu(self, toolbar, action, handler, tooltip):
        action.setToolTip(tooltip)
        button = self.toolbar_buttons.get(action) or toolbar.widgetForAction(action)
        if button is None:
            return
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda position, b=button: handler(b.mapToGlobal(position)))
        # Keep the Python wrapper reachable; otherwise the GC can collect the button/lambda cycle
        # and silently drop the right-click connection.
        self.toolbar_context_buttons.append(button)

    def build_stamp_menu(self):
        menu = QMenu(self)
        menu.addAction(make_tool_icon("stamp"), "도장 찍기 (저장된 설정)", self.insert_default_stamp)
        menu.addAction(make_tool_icon("settings"), "도장 설정·저장…", self.open_stamp_settings)
        menu.addAction(make_tool_icon("image_plus"), "도장 이미지 추가…", self.import_stamp_png)
        menu.addAction(make_tool_icon("stamp"), "저장된 도장 골라 찍기…", self.insert_saved_png_stamp)
        return menu

    def build_approval_menu(self):
        menu = QMenu(self)
        menu.addAction(make_tool_icon("signature"), "결재 + 내 서명 넣기", self.insert_signed_approval)
        menu.addAction(make_tool_icon("pen_line"), "서명 등록·저장…", self.open_signature_settings)
        menu.addAction(make_tool_icon("settings"), "결재선 설정…", self.configure_approval_line)
        menu.addSeparator()
        menu.addAction(make_tool_icon("approval_stamp"), "결재칸만 넣기 (서명 없이)", self.add_approval_stamp_to_first_page)
        return menu

    def show_stamp_button_menu(self, global_pos):
        self.stamp_menu = self.build_stamp_menu()
        self.stamp_menu.exec_(global_pos)

    def show_approval_button_menu(self, global_pos):
        self.approval_menu = self.build_approval_menu()
        self.approval_menu.exec_(global_pos)

    def make_action(self, label, handler, icon_key=None, shortcut=None):
        if icon_key is None:
            action = QAction(label, self)
        elif isinstance(icon_key, str):
            action = QAction(make_tool_icon(icon_key), label, self)
        else:
            action = QAction(self.style().standardIcon(icon_key), label, self)
        # triggered(bool checked) would otherwise land in optional parameters (zoom_in(anchor_pos=False) crashed).
        action.triggered.connect(lambda _checked=False, target=handler: target())
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.setToolTip(label)
        return action

    def _build_layout(self):
        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 6, 10, 10)
        left_layout.setSpacing(5)

        panel_title = QLabel("\ud398\uc774\uc9c0")
        panel_title.setObjectName("panelTitle")
        panel_hint = QLabel("\ub4dc\ub798\uadf8\ub85c \uc21c\uc11c \ubcc0\uacbd")
        panel_hint.setObjectName("panelHint")
        left_layout.addWidget(panel_title)
        left_layout.addWidget(panel_hint)
        left_layout.addWidget(self.page_list)

        prev_button = QPushButton("\uc774\uc804")
        next_button = QPushButton("\ub2e4\uc74c")
        prev_button.setIcon(make_tool_icon("prev"))
        next_button.setIcon(make_tool_icon("next"))
        prev_button.setIconSize(QSize(22, 22))
        next_button.setIconSize(QSize(22, 22))
        prev_button.clicked.connect(self.previous_page)
        next_button.clicked.connect(self.next_page)

        nav_layout = QHBoxLayout()
        nav_layout.addWidget(prev_button)
        nav_layout.addWidget(next_button)
        left_layout.addLayout(nav_layout)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setOpaqueResize(False)
        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(self.scroll_area)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([150, 1130])
        self.main_splitter.splitterMoved.connect(self.schedule_responsive_preview_refresh)
        self.main_splitter.splitterMoved.connect(self.schedule_thumbnail_refresh)

        layout.addWidget(self.main_splitter)
        self.setCentralWidget(central)

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "PDF \uc5f4\uae30", "", PDF_FILTER)
        if path:
            self.load_pdf(path)

    def load_pdf(self, path):
        page_refs = self.page_refs_for_pdf_with_password(path)
        if page_refs is None:
            return

        keep = os.path.normcase(os.path.abspath(str(path)))
        for other in {ref.path for ref in self.ordered_page_refs()}:
            if os.path.normcase(os.path.abspath(other)) != keep:
                PDF_SOURCES.invalidate(other)
        self.current_pdf_path = path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selected_mark_index = None
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.thumbnail_cache.clear()
        self.page_render_cache.clear()
        self.page_size_cache.clear()
        self.preview_reference_width = None
        self.preview_reference_height = None
        self.page_list.blockSignals(True)
        self.page_list.clear()
        self.add_page_refs_to_list(page_refs)
        self.page_list.blockSignals(False)
        self.refresh_preview_reference_width()
        self.schedule_thumbnail_refresh()
        self.refresh_page_numbers()
        self.page_list.setCurrentRow(0)
        self.show_page(0)
        self.setWindowTitle(Path(path).name)
        self.set_status(f"\uc5f4\ub9bc: {path}")

    def add_pdf_pages_to_list(self, path):
        page_refs = self.page_refs_for_pdf_with_password(path)
        if page_refs is None:
            return False
        self.add_page_refs_to_list(page_refs)
        return True

    def add_page_refs_to_list(self, page_refs):
        for page_ref in page_refs:
            self.page_list.addItem(self.create_item_for_page_ref(page_ref))

    def create_item_for_page_ref(self, page_ref):
        item = QListWidgetItem(self.placeholder_page_icon(page_ref.rotation), "")
        item.setTextAlignment(Qt.AlignCenter)
        item.setData(PAGE_ROLE, page_ref)
        item.setToolTip(f"{Path(page_ref.path).name} / {page_ref.page_index + 1} \ud398\uc774\uc9c0")
        return item

    def placeholder_page_icon(self, rotation=0):
        size = (self.thumbnail_canvas_width, self.thumbnail_canvas_height)
        cache_key = (rotation % 360, size)
        icon = self.placeholder_icon_cache.get(cache_key)
        if icon is None:
            icon = make_page_icon(cache_key[0], size)
            self.placeholder_icon_cache[cache_key] = icon
        return icon


    def page_refs_for_pdf_with_password(self, path):
        path = str(Path(path))
        cached_password = self.pdf_passwords.get(path, "")
        try:
            refs = page_refs_for_pdf(path, cached_password)
            if cached_password:
                self.pdf_passwords[path] = cached_password
            return refs
        except Exception:
            self.pdf_passwords.pop(path, None)

        prompt = (
            f"{Path(path).name}\n"
            "\uc554\ud638\uac00 \ud544\uc694\ud558\uac70\ub098 \ud30c\uc77c\uc744 \uc5f4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\n"
            "PDF \uc554\ud638\ub97c \uc785\ub825\ud558\uc138\uc694."
        )
        while True:
            password, ok = QInputDialog.getText(self, "\uc554\ud638 PDF", prompt, QLineEdit.Password)
            if not ok:
                return None

            try:
                refs = page_refs_for_pdf(path, password)
                self.pdf_passwords[path] = password
                return refs
            except Exception:
                QMessageBox.warning(
                    self,
                    "\uc554\ud638 \ud655\uc778",
                    f"{Path(path).name}\n\n\uc554\ud638\uac00 \ub9de\uc9c0 \uc54a\uac70\ub098 \ud30c\uc77c\uc744 \uc5f4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\n\ub2e4\uc2dc \uc785\ub825\ud574 \uc8fc\uc138\uc694.",
                )
                prompt = (
                    f"{Path(path).name}\n"
                    "\uc554\ud638\uac00 \ub9de\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.\n"
                    "PDF \uc554\ud638\ub97c \ub2e4\uc2dc \uc785\ub825\ud558\uc138\uc694."
                )

    def password_for_page_ref(self, page_ref):
        return page_ref.password or self.pdf_passwords.get(page_ref.path, "")

    def append_pdf_files(self, paths):
        self.insert_pdf_files(paths)

    def insert_pdf_files(self, paths, insert_row=None):
        valid_paths = [path for path in paths if Path(path).is_file() and path.lower().endswith(".pdf")]
        if not valid_paths:
            return

        self.save_undo_state()
        was_empty = self.page_list.count() == 0
        previous_pdf_path = self.current_pdf_path
        if was_empty:
            self.current_pdf_path = valid_paths[0]

        if insert_row is None:
            insert_row = self.page_list.count()
        insert_row = max(0, min(insert_row, self.page_list.count()))
        new_items = []
        for path in valid_paths:
            new_items.extend(self.create_items_for_pdf(path) or [])

        if not new_items:
            self.undo_stack.pop()
            self.current_pdf_path = previous_pdf_path
            return

        for offset, item in enumerate(new_items):
            self.page_list.insertItem(insert_row + offset, item)

        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        if was_empty:
            # Opening into an empty window (double-click / 연결 프로그램 / drag) is "open", not an undoable edit.
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.setWindowTitle(Path(valid_paths[0]).name)
            self.page_list.setCurrentRow(0)
            self.show_page(0)
        else:
            self.page_list.setCurrentRow(insert_row)
            self.show_page(insert_row)
        self.set_status(f"PDF {len(valid_paths)}\uac1c \ucd94\uac00 \uc644\ub8cc")

    def ordered_page_refs(self):
        refs = []
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            page_ref = item.data(PAGE_ROLE)
            if page_ref:
                refs.append(page_ref)
        return refs

    def make_history_snapshot(self):
        return (tuple(self.ordered_page_refs()), self.current_page_index, self.selected_mark_index)

    def save_undo_state(self):
        self.undo_stack.append(self.make_history_snapshot())
        self.redo_stack.clear()

    def push_undo_snapshot(self, snapshot):
        self.undo_stack.append(snapshot)
        self.redo_stack.clear()

    def restore_history_snapshot(self, snapshot):
        page_refs, current_row, selected_mark_index = snapshot
        self.selected_mark_index = selected_mark_index
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.thumbnail_cache.clear()
        self.rebuild_page_list(list(page_refs), current_row=current_row)
        self.selected_mark_index = selected_mark_index
        if self.page_list.count():
            self.show_page(min(max(0, current_row), self.page_list.count() - 1))

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.make_history_snapshot())
        self.restore_history_snapshot(self.undo_stack.pop())
        self.set_status("\ub418\ub3cc\ub9ac\uae30 \uc644\ub8cc")

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.make_history_snapshot())
        self.restore_history_snapshot(self.redo_stack.pop())
        self.set_status("\ub2e4\uc2dc \uc2e4\ud589 \uc644\ub8cc")

    def rebuild_page_list(self, page_refs, selected_rows=None, current_row=None):
        selected_rows = selected_rows or []
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for page_ref in page_refs:
            self.page_list.addItem(self.create_item_for_page_ref(page_ref))
        self.page_list.blockSignals(False)

        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        self.page_list.clearSelection()

        for row in selected_rows:
            if 0 <= row < self.page_list.count():
                self.page_list.item(row).setSelected(True)

        if current_row is not None and 0 <= current_row < self.page_list.count():
            self.page_list.setCurrentRow(current_row)
            self.show_page(current_row)
        elif self.page_list.count():
            self.page_list.setCurrentRow(0)
            self.show_page(0)
        else:
            self.clear_preview()

    def move_pages_by_rows(self, selected_rows, target_row):
        all_refs = self.ordered_page_refs()
        selected_set = set(selected_rows)
        moving_refs = [all_refs[row] for row in selected_rows if 0 <= row < len(all_refs)]
        if not moving_refs:
            return

        self.save_undo_state()
        remaining_refs = [page_ref for index, page_ref in enumerate(all_refs) if index not in selected_set]
        adjusted_row = target_row - sum(1 for row in selected_rows if row < target_row)
        adjusted_row = max(0, min(adjusted_row, len(remaining_refs)))

        new_refs = remaining_refs[:adjusted_row] + moving_refs + remaining_refs[adjusted_row:]
        selected_after_move = list(range(adjusted_row, adjusted_row + len(moving_refs)))
        self.rebuild_page_list(new_refs, selected_after_move, adjusted_row)
        self.set_status("\ud398\uc774\uc9c0 \uc21c\uc11c\uac00 \ubcc0\uacbd\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

    def refresh_page_numbers(self):
        self.page_count = self.page_list.count()
        for row in range(self.page_count):
            self.page_list.item(row).setText(str(row + 1))

        self.page_spin.blockSignals(True)
        if self.page_count:
            self.page_spin.setMinimum(1)
            self.page_spin.setMaximum(self.page_count)
            self.page_spin.setValue(min(self.current_page_index + 1, self.page_count))
        else:
            self.page_spin.setMinimum(0)
            self.page_spin.setMaximum(0)
            self.page_spin.setValue(0)
        self.page_spin.blockSignals(False)

    def after_page_order_changed(self):
        self.refresh_page_numbers()
        row = self.page_list.currentRow()
        if row >= 0:
            self.show_page(row)
        self.set_status("\ud398\uc774\uc9c0 \uc21c\uc11c\uac00 \ubcc0\uacbd\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

    def selected_page_rows(self):
        selected_rows = sorted({index.row() for index in self.page_list.selectedIndexes()})
        if selected_rows:
            return selected_rows
        row = self.page_list.currentRow()
        return [row] if row >= 0 else []

    def page_refs_for_rows(self, rows):
        page_refs = []
        for row in rows:
            item = self.page_list.item(row)
            if item is None:
                continue
            page_ref = item.data(PAGE_ROLE)
            if page_ref:
                page_refs.append(page_ref)
        return page_refs

    def has_clipboard_pages(self):
        return bool(self.cut_buffer or self.copy_buffer)

    def cut_selected_pages(self):
        selected_rows = self.selected_page_rows()
        if not selected_rows:
            return

        self.save_undo_state()
        self.cut_buffer = self.page_refs_for_rows(selected_rows)
        self.copy_buffer = []

        for row in reversed(selected_rows):
            self.page_list.takeItem(row)

        next_row = min(selected_rows[0], self.page_list.count() - 1)
        self.refresh_page_numbers()

        if next_row >= 0:
            self.page_list.setCurrentRow(next_row)
            self.show_page(next_row)
        else:
            self.clear_preview()

        self.set_status(f"{len(self.cut_buffer)}\uac1c \ud398\uc774\uc9c0 \uc798\ub77c\ub0b4\uae30 \uc644\ub8cc")

    def copy_selected_pages(self):
        selected_rows = self.selected_page_rows()
        if not selected_rows:
            return

        self.copy_buffer = self.page_refs_for_rows(selected_rows)
        self.cut_buffer = []
        self.set_status(f"{len(self.copy_buffer)}\uac1c \ud398\uc774\uc9c0 \ubcf5\uc0ac \uc644\ub8cc")

    def paste_pages_after_current(self):
        page_refs = self.cut_buffer or self.copy_buffer
        if not page_refs:
            return

        self.save_undo_state()
        current_row = self.page_list.currentRow()
        insert_row = current_row + 1 if current_row >= 0 else self.page_list.count()

        for offset, page_ref in enumerate(page_refs):
            self.page_list.insertItem(insert_row + offset, self.create_item_for_page_ref(page_ref))

        pasted_count = len(page_refs)
        if self.cut_buffer:
            self.cut_buffer = []
        self.refresh_page_numbers()

        self.page_list.clearSelection()
        for row in range(insert_row, insert_row + pasted_count):
            self.page_list.item(row).setSelected(True)
        self.page_list.setCurrentRow(insert_row)
        self.show_page(insert_row)
        self.set_status(f"{pasted_count}\uac1c \ud398\uc774\uc9c0 \ubd99\uc5ec\ub123\uae30 \uc644\ub8cc")

    def delete_selected_pages(self):
        if self.selected_mark_index is not None:
            self.delete_selected_mark()
            return

        selected_rows = self.selected_page_rows()
        if not selected_rows:
            return

        self.save_undo_state()
        for row in reversed(selected_rows):
            self.page_list.takeItem(row)

        next_row = min(selected_rows[0], self.page_list.count() - 1)
        self.refresh_page_numbers()

        if next_row >= 0:
            self.page_list.setCurrentRow(next_row)
            self.show_page(next_row)
        else:
            self.clear_preview()

        self.set_status(f"{len(selected_rows)}\uac1c \ud398\uc774\uc9c0 \uc0ad\uc81c \uc644\ub8cc")

    def clear_preview(self):
        self.current_page_index = 0
        self.selected_mark_index = None
        self.preview_content_rect = QRectF()
        self.preview_content_size = QSize()
        self.page_label.clear()
        self.page_label.setText("PDF \ud30c\uc77c\uc744 \uc5f4\uac70\ub098 \uc774 \ud654\uba74\uc73c\ub85c \ub4dc\ub798\uadf8\ud574\uc11c \ucd94\uac00\ud558\uc138\uc694.")
        self.page_label.setFixedSize(640, 480)
        self.update_preview_cursor()

    def show_page(self, index):
        if index < 0 or index >= self.page_count:
            return

        item = self.page_list.item(index)
        page_ref = item.data(PAGE_ROLE)
        if not page_ref:
            return

        if index != self.current_page_index:
            self.selected_mark_index = None
        self.current_page_index = index
        self.update_zoom_for_page(page_ref)
        self.zoom_render_timer.stop()  # this render is already at the current zoom
        pixmap = self.render_page_pixmap(page_ref, self.zoom, self.selected_mark_index)
        if self.preview_drag_rect is not None:
            self.draw_page_marks(pixmap, (), self.preview_drag_rect)
        self.preview_page_pixmap = pixmap
        self.preview_pixmap_zoom = self.zoom
        preview_canvas = self.create_preview_canvas(pixmap)
        self.page_label.setPixmap(preview_canvas)
        self.page_label.setFixedSize(preview_canvas.size())
        self.update_preview_cursor()

        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)

        self.page_list.blockSignals(True)
        self.page_list.setCurrentRow(index)
        self.page_list.blockSignals(False)
        self.schedule_thumbnail_refresh()

        self.set_status(f"{index + 1} / {self.page_count} \ud398\uc774\uc9c0")

    def refresh_responsive_preview(self, *args):
        if self.page_count <= 0:
            return
        self.show_page(self.current_page_index)

    def refresh_preview_reference_width(self):
        page_refs = self.ordered_page_refs()
        if not page_refs:
            self.preview_reference_width = None
            self.preview_reference_height = None
            return
        page_sizes = [self.page_display_size(page_ref) for page_ref in page_refs]
        self.preview_reference_width = max(width for width, _ in page_sizes)
        self.preview_reference_height = max(height for _, height in page_sizes)

    def create_preview_canvas(self, page_pixmap):
        canvas_width = max(page_pixmap.width(), int(round((self.preview_reference_width or 0) * self.zoom)))
        canvas_height = max(page_pixmap.height(), int(round((self.preview_reference_height or 0) * self.zoom)))
        canvas = QPixmap(canvas_width, canvas_height)
        canvas.fill(QColor("#e8eef7"))
        x = (canvas_width - page_pixmap.width()) // 2
        y = (canvas_height - page_pixmap.height()) // 2
        painter = QPainter(canvas)
        painter.drawPixmap(x, y, page_pixmap)
        painter.end()
        self.preview_content_rect = QRectF(x, y, page_pixmap.width(), page_pixmap.height())
        self.preview_content_size = page_pixmap.size()
        return canvas

    def can_pan_preview(self):
        if self.annotation_mode or self.page_label.pixmap() is None:
            return False
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        return horizontal.maximum() > 0 or vertical.maximum() > 0

    def pan_preview_by(self, delta):
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        horizontal.setValue(horizontal.value() - delta.x())
        vertical.setValue(vertical.value() - delta.y())

    def update_preview_cursor(self):
        if getattr(self.page_label, "pan_active", False):
            self.page_label.setCursor(Qt.ClosedHandCursor)
        elif self.can_pan_preview():
            self.page_label.setCursor(Qt.OpenHandCursor)
        else:
            self.page_label.unsetCursor()

    THUMBNAIL_ITEM_CHROME = 14  # #pageList::item padding (3px x2) + border (1px x2) + Qt item margins
    THUMBNAIL_SIDE_MARGIN = 4
    THUMBNAIL_MIN_CELL = 170  # widen the panel: a 2nd column once two cells this wide fit, a 3rd at three, ...
    THUMBNAIL_MAX_CANVAS = 228

    def thumbnail_columns(self, viewport_width=None):
        if viewport_width is None:
            viewport_width = self.page_list.viewport().width()
        return max(1, int(viewport_width) // self.THUMBNAIL_MIN_CELL)

    def update_thumbnail_layout(self):
        # Responsive grid that always fits the real viewport width (scrollbar/frame excluded):
        # one centered column in a narrow panel, then 2, 3, ... equal columns, each thumbnail centered in its cell.
        viewport_width = max(60, self.page_list.viewport().width())
        columns = self.thumbnail_columns(viewport_width)
        # Qt wraps a line when it reaches the viewport edge exactly, so a grid of N columns keeps 1px spare
        grid_width = viewport_width if columns == 1 else (viewport_width - 1) // columns
        canvas_width = max(48, min(self.THUMBNAIL_MAX_CANVAS, grid_width - self.THUMBNAIL_ITEM_CHROME - 2 * self.THUMBNAIL_SIDE_MARGIN))
        item_width = canvas_width - 8
        item_height = int(item_width * 1.42)
        canvas_height = item_height + 6
        layout_signature = (columns, grid_width, item_width, item_height, canvas_width, canvas_height)

        if self.last_thumbnail_layout == layout_signature:
            return False

        self.thumbnail_item_width = item_width
        self.thumbnail_item_height = item_height
        self.thumbnail_canvas_width = canvas_width
        self.thumbnail_canvas_height = canvas_height
        self.last_thumbnail_layout = layout_signature
        self.thumbnail_column_count = columns
        self.page_list.setIconSize(QSize(canvas_width, canvas_height))
        self.page_list.setGridSize(QSize(grid_width, canvas_height + 32))
        return True

    def thumbnail_cell_bounds(self, rect):
        """Left/right edge of the grid column a thumbnail sits in (the whole viewport when there is one column)."""
        viewport_width = self.page_list.viewport().width()
        grid_width = max(1, self.page_list.gridSize().width())
        columns = max(1, viewport_width // grid_width)
        if columns == 1:
            return 0, viewport_width - 1
        column = min(columns - 1, max(0, rect.center().x() // grid_width))
        return column * grid_width, (column + 1) * grid_width - 1

    def thumbnails_off_center(self, rows=None):
        viewport_width = self.page_list.viewport().width()
        rows = range(self.page_list.count()) if rows is None else rows
        for row in rows:
            rect = self.page_list.visualRect(self.page_list.model().index(row, 0))
            if not rect.isValid():
                continue
            left, right = self.thumbnail_cell_bounds(rect)
            if abs((rect.left() - left) - (right - rect.right())) > 2 or rect.right() >= viewport_width or rect.left() < 0:
                return True
        return False

    def ensure_thumbnails_centered(self):
        # Snap movement keeps item positions from older layouts; re-layout when any visible item is off-center
        # (checking only the first item missed rows further down the list).
        if self.page_list.count() == 0:
            return
        if self.thumbnails_off_center(set(self.visible_thumbnail_rows()) | {0}):
            self.page_list.doItemsLayout()

    def thumbnail_view_state(self):
        """What the user is looking at in the thumbnail list: (current page on screen?, scroll position 0..1)."""
        bar = self.page_list.verticalScrollBar()
        item = self.page_list.currentItem()
        current_visible = item is not None and self.page_list.visualItemRect(item).intersects(self.page_list.viewport().rect())
        return current_visible, (bar.value() / bar.maximum() if bar.maximum() > 0 else 0.0)

    def refresh_thumbnail_icons(self):
        # the state from before the panel was resized (recorded by the list), else the state right now
        keep_current, scroll_fraction = self.thumbnail_view_anchor or self.thumbnail_view_state()
        self.thumbnail_view_anchor = None
        bar = self.page_list.verticalScrollBar()
        layout_changed = self.update_thumbnail_layout()
        if layout_changed:
            self.thumbnail_cache.clear()
            for row in range(self.page_list.count()):
                item = self.page_list.item(row)
                if item is not None:
                    page_ref = item.data(PAGE_ROLE)
                    if page_ref:
                        item.setIcon(self.placeholder_page_icon(page_ref.rotation))
            self.page_list.doItemsLayout()

        for row in self.visible_thumbnail_rows():
            item = self.page_list.item(row)
            if item is None:
                continue
            page_ref = item.data(PAGE_ROLE)
            if page_ref:
                item.setIcon(self.render_thumbnail(page_ref))

        if layout_changed:
            self.page_list.doItemsLayout()
            # column count / size changed: keep the current page in view (else the same relative spot)
            if keep_current and self.page_list.currentItem() is not None:
                self.page_list.scrollToItem(self.page_list.currentItem(), QListWidget.EnsureVisible)
            elif bar.maximum() > 0:
                bar.setValue(round(scroll_fraction * bar.maximum()))
        else:
            self.ensure_thumbnails_centered()

    def visible_thumbnail_rows(self):
        if self.page_list.count() == 0:
            return []

        viewport_rect = self.page_list.viewport().rect()
        rows = set()
        current_row = self.page_list.currentRow()
        if current_row >= 0:
            rows.update(range(max(0, current_row - 2), min(self.page_list.count(), current_row + 3)))

        grid_size = self.page_list.gridSize()
        x_step = max(24, grid_size.width() // 2)
        y_step = max(24, grid_size.height() // 2)
        x_values = list(range(viewport_rect.left(), viewport_rect.right() + 1, x_step))
        y_values = list(range(viewport_rect.top(), viewport_rect.bottom() + 1, y_step))
        if viewport_rect.right() not in x_values:
            x_values.append(viewport_rect.right())
        if viewport_rect.bottom() not in y_values:
            y_values.append(viewport_rect.bottom())

        for y in y_values:
            for x in x_values:
                index = self.page_list.indexAt(QPoint(x, y))
                if index.isValid():
                    rows.add(index.row())
                    rows.add(min(self.page_list.count() - 1, index.row() + 1))

        return sorted(row for row in rows if 0 <= row < self.page_list.count())[:60]

    def schedule_thumbnail_refresh(self, *args):
        if self.thumbnail_refresh_pending:
            self.thumbnail_refresh_timer.start(120)
            return
        self.thumbnail_refresh_pending = True
        self.thumbnail_refresh_timer.start(120)

    def _flush_thumbnail_refresh(self):
        self.thumbnail_refresh_pending = False
        self.refresh_thumbnail_icons()

    def schedule_responsive_preview_refresh(self, *args):
        if self.preview_refresh_pending:
            self.preview_refresh_timer.start(80)
            return
        self.preview_refresh_pending = True
        self.preview_refresh_timer.start(80)

    def _flush_responsive_preview_refresh(self):
        self.preview_refresh_pending = False
        if self.zoom_render_timer.isActive():
            return  # a wheel zoom is in progress; its final render covers the new viewport size
        self.refresh_responsive_preview()

    def update_zoom_for_page(self, page_ref):
        page_width, page_height = self.page_display_size(page_ref)
        base_width = self.preview_reference_width or page_width
        base_height = self.preview_reference_height or page_height
        viewport = self.scroll_area.viewport()
        # Height without the horizontal scrollbar, so the fit does not change when that scrollbar appears.
        stable_height = (
            self.scroll_area.height()
            - 2 * self.scroll_area.frameWidth()
            - self.scroll_area.horizontalScrollBar().sizeHint().height()
        )
        available_width = max(240, viewport.width() - 48)
        available_height = max(240, stable_height - 32)
        # 100% = whole page visible and centered (fit page) instead of fit width.
        fitted_zoom = min(available_width / max(1.0, base_width), available_height / max(1.0, base_height))
        self.zoom = min(4.0, max(0.35, fitted_zoom * self.zoom_factor))
        self.zoom_label.setText(f"{int(self.zoom * 100)}%")

    def page_display_size(self, page_ref):
        cache_key = (page_ref.path, page_ref.page_index, page_ref.rotation)
        cached_size = self.page_size_cache.get(cache_key)
        if cached_size is not None:
            return cached_size

        # One document open fills every page of this file (sizes come from the page tree, no page loading):
        # opening an 80-page PDF used to open the document once per page here.
        sizes = pdf_page_sizes(page_ref.path, self.password_for_page_ref(page_ref))
        for index, (width, height) in enumerate(sizes):
            for rotation in (0, 90, 180, 270):
                self.page_size_cache[(page_ref.path, index, rotation)] = (
                    (height, width) if rotation in (90, 270) else (width, height)
                )
        cached_size = self.page_size_cache.get(cache_key)
        if cached_size is None:
            raise IndexError(f"page {page_ref.page_index + 1} not in {page_ref.path}")
        return cached_size

    def render_page_pixmap(self, page_ref, scale, selected_mark_index=None):
        cache_key = (page_ref.path, page_ref.page_index, page_ref.rotation, round(scale, 3))
        cached_pixmap = self.page_render_cache.get(cache_key)
        if cached_pixmap is None:
            with PDF_SOURCES.page(page_ref.path, page_ref.page_index, self.password_for_page_ref(page_ref)) as page:
                qimage = pdfium_page_to_qimage(page, scale, page_ref.rotation)

            cached_pixmap = QPixmap.fromImage(qimage)
            self.page_render_cache[cache_key] = cached_pixmap
            self.trim_page_render_cache()
        else:
            self.page_render_cache[cache_key] = self.page_render_cache.pop(cache_key)  # most recently used

        if not page_ref.marks:
            # shallow Qt copy (no pixel copy): painting on it later (drag frame) detaches, the cached page stays clean
            return QPixmap(cached_pixmap)
        pixmap = cached_pixmap.copy()
        self.draw_page_marks(pixmap, page_ref.marks, selected_mark_index=selected_mark_index)
        return pixmap

    def trim_page_render_cache(self):
        """Drop the least recently used renders beyond RENDER_CACHE_MAX_ITEMS or RENDER_CACHE_MAX_BYTES
        (the newest render always stays, so a single huge zoomed page still works)."""
        cache = self.page_render_cache
        total = sum(pixmap.width() * pixmap.height() * 4 for pixmap in cache.values())
        while len(cache) > 1 and (len(cache) > RENDER_CACHE_MAX_ITEMS or total > RENDER_CACHE_MAX_BYTES):
            oldest = cache.pop(next(iter(cache)))
            total -= oldest.width() * oldest.height() * 4

    def draw_page_marks(self, pixmap, marks, drag_rect=None, selected_mark_index=None):
        if not marks and drag_rect is None:
            return

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        for index, mark in enumerate(marks):
            rect = self.normalized_rect_to_pixmap(mark.rect, pixmap.size())
            self.draw_mark_rect(painter, mark, rect)
            if selected_mark_index == index:
                self.draw_selected_mark_handles(painter, rect)
        if drag_rect is not None and self.annotation_mode:
            self.draw_mark_rect(painter, PageMark(self.annotation_mode, ()), drag_rect)
        painter.end()

    def approval_labels_for_mark(self, mark):
        if mark.data:
            return normalize_approval_line(mark.data)
        return self.approval_line

    def png_path_for_mark(self, mark):
        if mark.kind != "image_stamp" or not mark.data:
            return None
        image_path = Path(mark.data[0])
        return image_path if image_path.is_file() and image_path.suffix.lower() == ".png" else None

    def draw_mark_rect(self, painter, mark, rect):
        if mark.kind == "highlight":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 235, 59, 105))
            painter.drawRect(rect)
            return
        if mark.kind == "approval":
            self.draw_approval_stamp(painter, rect, self.approval_labels_for_mark(mark))
            return
        if mark.kind == "approval_signed":
            labels = normalize_approval_line(mark.data[0])
            signature = QPixmap(str(mark.data[1])) if Path(mark.data[1]).is_file() else None
            paint_approval_table(painter, QRectF(rect), labels, signature, mark.data[2])
            return
        if mark.kind == "image_stamp":
            image_path = self.png_path_for_mark(mark)
            image = QPixmap(str(image_path)) if image_path else QPixmap()
            if not image.isNull():
                opacity = mark.data[1] / 100 if len(mark.data) > 1 else 1.0
                painter.save()
                painter.setOpacity(opacity)
                painter.drawPixmap(rect, image, QRectF(image.rect()))
                painter.restore()
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("#0f766e"), 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
                painter.drawRect(rect)
                return

            painter.setBrush(QColor("#fef2f2"))
            painter.setPen(QPen(QColor("#dc2626"), 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
            painter.drawRect(rect)
            painter.drawText(rect, Qt.AlignCenter, "PNG")
            return

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#dc2626"), 3, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        painter.drawRect(rect)

    def draw_approval_stamp(self, painter, rect, labels):
        paint_approval_table(painter, QRectF(rect), labels)

    def draw_selected_mark_handles(self, painter, rect):
        painter.setPen(QPen(QColor("#2563eb"), 2, Qt.SolidLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        painter.setBrush(QColor("#ffffff"))
        for handle in self.mark_handle_rects(rect).values():
            painter.drawRect(handle)

    def mark_handle_rects(self, rect):
        size = 8
        half = size / 2
        return {
            "nw": QRectF(rect.left() - half, rect.top() - half, size, size),
            "ne": QRectF(rect.right() - half, rect.top() - half, size, size),
            "sw": QRectF(rect.left() - half, rect.bottom() - half, size, size),
            "se": QRectF(rect.right() - half, rect.bottom() - half, size, size),
        }

    def normalized_rect_to_pixmap(self, rect, size):
        left, top, right, bottom = rect
        return QRectF(left * size.width(), top * size.height(), (right - left) * size.width(), (bottom - top) * size.height())

    def render_thumbnail_pixmap(self, page_ref):
        """The page rendered straight at thumbnail size (not cached here: render_thumbnail caches the finished icon)."""
        width, height = self.page_display_size(page_ref)
        scale = min(self.thumbnail_item_width / max(1.0, width), self.thumbnail_item_height / max(1.0, height))
        scale = max(0.02, min(2.0, scale))
        with PDF_SOURCES.page(page_ref.path, page_ref.page_index, self.password_for_page_ref(page_ref)) as page:
            pixmap = QPixmap.fromImage(pdfium_page_to_qimage(page, scale, page_ref.rotation))
        self.draw_page_marks(pixmap, page_ref.marks)
        return pixmap

    def render_thumbnail(self, page_ref):
        cache_key = (
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks,
            self.thumbnail_item_width,
            self.thumbnail_item_height,
        )
        cached_icon = self.thumbnail_cache.get(cache_key)
        if cached_icon is not None:
            return cached_icon

        thumbnail = self.render_thumbnail_pixmap(page_ref)
        if thumbnail.width() > self.thumbnail_item_width or thumbnail.height() > self.thumbnail_item_height:
            thumbnail = thumbnail.scaled(
                self.thumbnail_item_width,
                self.thumbnail_item_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        canvas = QPixmap(self.thumbnail_canvas_width, self.thumbnail_canvas_height)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 23, 42, 36))
        painter.drawRoundedRect(
            QRectF(7, 7, self.thumbnail_canvas_width - 12, self.thumbnail_canvas_height - 12),
            3,
            3,
        )
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(
            QRectF(4, 4, self.thumbnail_canvas_width - 10, self.thumbnail_canvas_height - 10),
            3,
            3,
        )
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.drawRoundedRect(
            QRectF(4.5, 4.5, self.thumbnail_canvas_width - 11, self.thumbnail_canvas_height - 11),
            3,
            3,
        )
        x = (canvas.width() - thumbnail.width()) // 2
        y = 6 + ((self.thumbnail_canvas_height - 14) - thumbnail.height()) // 2
        painter.drawPixmap(x, y, thumbnail)
        painter.end()

        icon = QIcon(canvas)
        self.thumbnail_cache[cache_key] = icon
        return icon

    def go_to_page(self, value):
        if value > 0:
            self.show_page(value - 1)

    def previous_page(self):
        self.show_page(max(0, self.current_page_index - 1))

    def next_page(self):
        self.show_page(min(self.page_count - 1, self.current_page_index + 1))

    ZOOM_MIN = 0.35
    ZOOM_MAX = 3.0
    ZOOM_STEP = 0.15
    ZOOM_SETTLE_MS = 160

    def zoom_in(self, anchor_pos=None):
        self.zoom_factor = min(self.ZOOM_MAX, self.zoom_factor + self.ZOOM_STEP)
        self.update_zoom(anchor_pos)

    def zoom_out(self, anchor_pos=None):
        self.zoom_factor = max(self.ZOOM_MIN, self.zoom_factor - self.ZOOM_STEP)
        self.update_zoom(anchor_pos)

    def wheel_zoom(self, delta_y, anchor_pos):
        """Ctrl+wheel: one notch (120) = one zoom step; hi-res wheels/touchpads send fractions of a notch."""
        if not delta_y or self.page_count <= 0:
            return
        factor = min(self.ZOOM_MAX, max(self.ZOOM_MIN, self.zoom_factor + self.ZOOM_STEP * delta_y / 120.0))
        if abs(factor - self.zoom_factor) < 1e-9:
            return
        self.zoom_factor = factor
        self.zoom_render_anchor = QPoint(anchor_pos)
        if not self.show_scaled_preview(anchor_pos):
            self.update_zoom(anchor_pos)
            return
        self.zoom_render_timer.start(self.ZOOM_SETTLE_MS)

    def show_scaled_preview(self, anchor_pos):
        """Instant zoom feedback: rescale the last rendered page instead of rendering the PDF for every wheel step."""
        base = self.preview_page_pixmap
        _, page_ref = self.current_page_item_and_ref()
        if base is None or base.isNull() or not self.preview_pixmap_zoom or page_ref is None:
            return False
        anchor_state = self.capture_zoom_anchor(anchor_pos)
        self.update_zoom_for_page(page_ref)
        ratio = self.zoom / self.preview_pixmap_zoom
        size = QSize(max(1, int(round(base.width() * ratio))), max(1, int(round(base.height() * ratio))))
        scaled = base.scaled(size, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        canvas = self.create_preview_canvas(scaled)
        self.page_label.setPixmap(canvas)
        self.page_label.setFixedSize(canvas.size())
        self.restore_zoom_anchor(anchor_state)
        self.update_preview_cursor()
        return True

    def _flush_zoom_render(self):
        if self.page_count > 0:
            self.update_zoom(self.zoom_render_anchor)

    def wheel_page(self, delta_y):
        """Plain wheel over the page: one notch = one page (fractions accumulate)."""
        if self.page_count <= 0 or not delta_y:
            return
        if (self.wheel_page_remainder > 0) != (delta_y > 0):
            self.wheel_page_remainder = 0
        self.wheel_page_remainder += delta_y
        while abs(self.wheel_page_remainder) >= 120:
            step = -1 if self.wheel_page_remainder > 0 else 1
            self.wheel_page_remainder += 120 if step > 0 else -120
            target = min(self.page_count - 1, max(0, self.current_page_index + step))
            if target == self.current_page_index:
                self.wheel_page_remainder = 0
                break
            self.show_page(target)
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(bar.minimum() if step > 0 else bar.maximum())

    def update_zoom(self, anchor_pos=None):
        if self.page_count:
            if anchor_pos is None:
                viewport = self.scroll_area.viewport()
                anchor_pos = QPoint(viewport.width() // 2, viewport.height() // 2)
            anchor_state = self.capture_zoom_anchor(anchor_pos)
            self.show_page(self.current_page_index)
            self.restore_zoom_anchor(anchor_state)
            # Scroll ranges can change once more after the layout settles (scrollbar appearing).
            QTimer.singleShot(0, lambda state=anchor_state: self.restore_zoom_anchor(state))
        else:
            self.zoom_label.setText(f"{int(self.zoom_factor * 100)}%")

    def capture_zoom_anchor(self, anchor_pos):
        if anchor_pos is None or self.page_label.pixmap() is None:
            return None

        label_size = self.page_label.size()
        if label_size.width() <= 0 or label_size.height() <= 0:
            return None

        # mapFrom() already includes the current scroll offset (the label is moved by the scroll area).
        label_pos = self.page_label.mapFrom(self.scroll_area.viewport(), anchor_pos)
        content_x = label_pos.x()
        content_y = label_pos.y()
        ratio_x = min(max(content_x / max(1, label_size.width()), 0.0), 1.0)
        ratio_y = min(max(content_y / max(1, label_size.height()), 0.0), 1.0)
        return anchor_pos, ratio_x, ratio_y

    def restore_zoom_anchor(self, anchor_state):
        if anchor_state is None or self.page_label.pixmap() is None:
            return

        anchor_pos, ratio_x, ratio_y = anchor_state
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        target_x = int(round(self.page_label.width() * ratio_x - anchor_pos.x()))
        target_y = int(round(self.page_label.height() * ratio_y - anchor_pos.y()))
        horizontal.setValue(max(0, min(target_x, horizontal.maximum())))
        vertical.setValue(max(0, min(target_y, vertical.maximum())))

    def current_page_item_and_ref(self):
        item = self.page_list.item(self.current_page_index)
        if item is None:
            return None, None
        return item, item.data(PAGE_ROLE)

    def enable_rectangle_mode(self):
        self.annotation_mode = "rect"
        self.preview_drag_rect = None
        self.selected_mark_index = None
        if self.page_count:
            self.show_page(self.current_page_index)
        self.set_status("\ube68\uac04 \uc0ac\uac01\ud615: \ubcf8\ubb38\uc5d0\uc11c \ub4dc\ub798\uadf8\ud558\uc138\uc694.")

    def enable_highlight_mode(self):
        self.annotation_mode = "highlight"
        self.preview_drag_rect = None
        self.selected_mark_index = None
        if self.page_count:
            self.show_page(self.current_page_index)
        self.set_status("\ud615\uad11\ud39c: \ud14d\uc2a4\ud2b8 \uc704\ub97c \ub4dc\ub798\uadf8\ud558\uc138\uc694.")

    def save_stamp_preferences(self):
        save_user_preferences(self.approval_line, self.stamp_image_paths)

    def set_approval_line(self, labels, persist=True):
        self.approval_line = normalize_approval_line(labels)
        if persist:
            self.save_stamp_preferences()
        return self.approval_line

    def configure_approval_line(self):
        text, ok = QInputDialog.getText(
            self,
            "\uacb0\uc7ac\uc120 \uc124\uc815",
            "\uc9c1\ucc45\uc744 \uc27c\ud45c\ub85c \uad6c\ubd84\ud574 \uc785\ub825\ud558\uc138\uc694. (\ucd5c\ub300 6\uac1c)",
            text=", ".join(self.approval_line),
        )
        if not ok:
            return
        self.set_approval_line(text)
        self.set_status(f"\uacb0\uc7ac\uc120 \uc800\uc7a5: {', '.join(self.approval_line)}")

    def store_png_stamp(self, source_path, remove_background=False):
        source = Path(source_path)
        if not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
            raise ValueError("PNG/JPG/BMP \uc774\ubbf8\uc9c0\ub9cc \ub3c4\uc7a5\uc73c\ub85c \ub4f1\ub85d\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.")
        image = QImage(str(source))
        if image.isNull():
            raise ValueError("\uc774\ubbf8\uc9c0\ub97c \uc77d\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")

        stamp_dir = user_data_dir() / "stamps"
        stamp_dir.mkdir(parents=True, exist_ok=True)
        destination = stamp_dir / f"{uuid.uuid4().hex}.png"
        if source.suffix.lower() == ".png":
            shutil.copy2(str(source), str(destination))
        elif not image.save(str(destination), "PNG"):
            raise ValueError("\uc774\ubbf8\uc9c0\ub97c PNG\ub85c \uc800\uc7a5\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.")
        if remove_background:
            remove_white_background(destination)
        self.stamp_image_paths = [path for path in self.stamp_image_paths if Path(path).is_file()]
        self.stamp_image_paths.append(str(destination))
        self.save_stamp_preferences()
        return str(destination)

    def import_stamp_png(self):
        source_path, _ = QFileDialog.getOpenFileName(self, "PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc", "", "\uc774\ubbf8\uc9c0 (*.png *.jpg *.jpeg *.bmp)")
        if not source_path:
            return
        try:
            destination = self.store_png_stamp(source_path)
        except (OSError, ValueError) as exc:
            self.show_error("PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc \uc624\ub958", str(exc))
            return
        if not self.stamp_settings.get("image"):
            self.stamp_settings["image"] = destination
            save_stamp_settings(self.stamp_settings)
        self.set_status(f"PNG \ub3c4\uc7a5 \ubcf4\uad00 \uc644\ub8cc: {Path(destination).name}")

    def add_png_stamp(self, image_path, rect=None, opacity_percent=100):
        if not self.require_pdf():
            return False
        image_path = Path(image_path)
        if QPixmap(str(image_path)).isNull():
            self.show_error("PNG \ub3c4\uc7a5 \uc624\ub958", "\uc774\ubbf8\uc9c0\ub97c \uc77d\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")
            return False

        row = max(0, self.current_page_index)
        item = self.page_list.item(row)
        page_ref = item.data(PAGE_ROLE) if item else None
        if not page_ref:
            return False
        self.save_undo_state()
        stamp_rect = rect or (0.68, 0.68, 0.88, 0.88)
        data = (str(image_path),) if opacity_percent >= 100 else (str(image_path), int(opacity_percent))
        mark = PageMark("image_stamp", stamp_rect, data)
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks + (mark,),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        item.setIcon(self.render_thumbnail(updated_ref))
        self.selected_mark_index = len(updated_ref.marks) - 1
        self.show_page(row)
        self.set_status("PNG \ub3c4\uc7a5\uc744 \ucd94\uac00\ud588\uc2b5\ub2c8\ub2e4. \ub9c8\uc6b0\uc2a4\ub85c \uc704\uce58\uc640 \ud06c\uae30\ub97c \uc870\uc815\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.")
        return True

    def insert_saved_png_stamp(self):
        self.stamp_image_paths = [path for path in self.stamp_image_paths if Path(path).is_file()]
        if not self.stamp_image_paths:
            self.show_error("PNG \ub3c4\uc7a5 \uc5c6\uc74c", "\uba3c\uc800 \uc8fc\uc11d > PNG \ub3c4\uc7a5 \uc5c5\ub85c\ub4dc\u00b7\ubcf4\uad00\uc73c\ub85c \ub3c4\uc7a5\uc744 \ub4f1\ub85d\ud574 \uc8fc\uc138\uc694.")
            return
        labels = [f"도장 {index} ({Path(path).name[:8]})" for index, path in enumerate(self.stamp_image_paths, start=1)]
        label, ok = QInputDialog.getItem(self, "\uc800\uc7a5\ub41c PNG \ub3c4\uc7a5", "\uc0bd\uc785\ud560 \ub3c4\uc7a5", labels, 0, False)
        if ok and label:
            image_path = self.stamp_image_paths[labels.index(label)]
            self.add_png_stamp(
                image_path,
                self.stamp_rect_for_current_page(image_path),
                self.stamp_settings["opacity_percent"],
            )

    def stamp_rect_for_current_page(self, image_path):
        _, page_ref = self.current_page_item_and_ref()
        image = QImage(str(image_path))
        if not page_ref or image.isNull():
            return None
        page_width, page_height = self.page_display_size(page_ref)
        width = self.stamp_settings["width_percent"] / 100
        height = width * (page_width / max(1.0, page_height)) * (image.height() / max(1, image.width()))
        if height > 0.6:
            width *= 0.6 / height
            height = 0.6
        margin_x, margin_y = 0.05, 0.04
        position = self.stamp_settings["position"]
        if position == "center":
            left, top = (1 - width) / 2, (1 - height) / 2
        else:
            left = margin_x if position.endswith("left") else 1 - margin_x - width
            top = margin_y if position.startswith("top") else 1 - margin_y - height
        # Do not cover an approval table that is already on this page: move the stamp just above it.
        for mark in page_ref.marks:
            if mark.kind not in ("approval", "approval_signed"):
                continue
            m_left, m_top, m_right, m_bottom = mark.rect
            overlaps = left < m_right and left + width > m_left and top < m_bottom and top + height > m_top
            if overlaps and m_top - height - 0.01 >= 0:
                top = m_top - height - 0.01
        return (left, top, left + width, top + height)

    def open_stamp_settings(self):
        self.last_stamp_dialog_inserted = False
        self.stamp_settings_dialog = dialog = StampSettingsDialog(self)
        accepted = dialog.exec_() == QDialog.Accepted
        self.stamp_settings_dialog = None
        if not accepted:
            return False
        self.stamp_settings = normalize_stamp_settings(dialog.values())
        save_stamp_settings(self.stamp_settings)
        self.set_status("도장 설정을 저장했습니다.")
        if dialog.insert_after_save and self.stamp_settings.get("image"):
            self.last_stamp_dialog_inserted = bool(self.insert_default_stamp())
        return True

    def insert_default_stamp(self):
        if not self.require_pdf():
            return False
        image_path = self.stamp_settings.get("image")
        if not image_path or not Path(image_path).is_file():
            self.set_status("저장된 도장이 없습니다. 도장 설정에서 도장을 등록하세요.")
            if not self.open_stamp_settings():
                return False
            image_path = self.stamp_settings.get("image")
            if not image_path:
                return False
            if self.last_stamp_dialog_inserted:
                return True
        return self.add_png_stamp(
            image_path,
            self.stamp_rect_for_current_page(image_path),
            self.stamp_settings["opacity_percent"],
        )

    def store_signature_image(self, image):
        signature_dir = user_data_dir() / "signatures"
        signature_dir.mkdir(parents=True, exist_ok=True)
        destination = signature_dir / f"signature_{uuid.uuid4().hex[:8]}.png"
        if not image.save(str(destination), "PNG"):
            raise OSError("서명 이미지를 저장하지 못했습니다.")
        previous = self.signature_settings.get("image")
        if previous and Path(previous).parent == signature_dir and Path(previous) != destination:
            try:
                Path(previous).unlink()
            except OSError:
                pass
        return str(destination)

    def open_signature_settings(self):
        self.signature_dialog = dialog = SignatureDialog(self)
        accepted = dialog.exec_() == QDialog.Accepted
        self.signature_dialog = None
        if not accepted:
            return False
        image, line_text, cell_index = dialog.result_values()
        try:
            image_path = self.store_signature_image(image)
        except OSError as exc:
            self.show_error("서명 저장 오류", str(exc))
            return False
        self.set_approval_line(line_text)
        self.signature_settings = normalize_signature_settings({"image": image_path, "cell": cell_index})
        save_signature_settings(self.signature_settings)
        self.set_status("결재 서명을 저장했습니다.")
        return True

    def insert_signed_approval(self):
        if not self.require_pdf():
            return False
        signature = self.signature_settings.get("image")
        if not signature or not Path(signature).is_file():
            self.set_status("등록된 서명이 없습니다. 서명을 먼저 등록하세요.")
            if not self.open_signature_settings():
                return False
            signature = self.signature_settings.get("image")
        cell = min(self.signature_settings.get("cell", 0), len(self.approval_line) - 1)
        return self.add_approval_stamp_to_first_page(signature_path=signature, signature_cell=cell)

    def add_approval_stamp_to_first_page(self, signature_path=None, signature_cell=0):
        if not self.require_pdf():
            return False

        item = self.page_list.item(0)
        if item is None:
            return
        page_ref = item.data(PAGE_ROLE)
        if not page_ref:
            return

        self.save_undo_state()
        stamp_rect = (0.54, 0.82, 0.96, 0.955)
        if signature_path:
            mark = PageMark("approval_signed", stamp_rect, (tuple(self.approval_line), str(signature_path), int(signature_cell)))
        else:
            mark = PageMark("approval", stamp_rect, tuple(self.approval_line))
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks + (mark,),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        item.setIcon(self.render_thumbnail(updated_ref))
        self.page_list.setCurrentRow(0)
        self.current_page_index = 0
        self.selected_mark_index = len(updated_ref.marks) - 1
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.show_page(0)
        self.set_status("\uccab \ud398\uc774\uc9c0 \ud558\ub2e8\uc5d0 \uacb0\uc7ac \ub3c4\uc7a5\uce78\uc744 \ucd94\uac00\ud588\uc2b5\ub2c8\ub2e4.")
        return True

    def disable_annotation_mode(self):
        self.annotation_mode = None
        self.preview_drag_rect = None
        if self.page_count:
            self.show_page(self.current_page_index)
        self.set_status("\uc8fc\uc11d \ubaa8\ub4dc \ub044\uae30")

    def update_preview_drag_rect(self, rect):
        self.preview_drag_rect = self.clamped_label_rect(rect)
        self.show_page(self.current_page_index)

    def finish_preview_annotation(self, rect):
        self.preview_drag_rect = None
        rect = self.clamped_label_rect(rect)
        if rect.width() < 4 or rect.height() < 4:
            self.annotation_mode = None
            self.show_page(self.current_page_index)
            return

        item, page_ref = self.current_page_item_and_ref()
        if not page_ref:
            return

        self.save_undo_state()
        size = self.preview_content_size if not self.preview_content_size.isEmpty() else self.page_label.size()
        mark = PageMark(self.annotation_mode, self.pixmap_rect_to_normalized(rect, size))
        marked_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            page_ref.marks + (mark,),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, marked_ref)
        item.setIcon(self.render_thumbnail(marked_ref))
        self.annotation_mode = None
        self.selected_mark_index = len(marked_ref.marks) - 1
        self.show_page(self.current_page_index)
        self.set_status("\uc8fc\uc11d \ucd94\uac00 \uc644\ub8cc")

    def clamped_label_rect(self, rect):
        content_rect = self.preview_content_rect
        if content_rect.isEmpty():
            size = self.page_label.pixmap().size() if self.page_label.pixmap() else self.page_label.size()
            content_rect = QRectF(0, 0, size.width(), size.height())
        left = max(content_rect.left(), min(rect.left(), content_rect.right()))
        top = max(content_rect.top(), min(rect.top(), content_rect.bottom()))
        right = max(content_rect.left(), min(rect.right(), content_rect.right()))
        bottom = max(content_rect.top(), min(rect.bottom(), content_rect.bottom()))
        return QRectF(
            QPointF(left - content_rect.left(), top - content_rect.top()),
            QPointF(right - content_rect.left(), bottom - content_rect.top()),
        ).normalized()

    def pixmap_rect_to_normalized(self, rect, size):
        left = rect.left() / max(1, size.width())
        top = rect.top() / max(1, size.height())
        right = rect.right() / max(1, size.width())
        bottom = rect.bottom() / max(1, size.height())
        return (left, top, right, bottom)

    def select_mark(self, mark_index):
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.selected_mark_index = mark_index
        self.show_page(self.current_page_index)

    def current_mark_rect(self, mark_index):
        _, page_ref = self.current_page_item_and_ref()
        if not page_ref or mark_index is None or not (0 <= mark_index < len(page_ref.marks)):
            return None
        return page_ref.marks[mark_index].rect

    def hit_test_mark(self, position):
        _, page_ref = self.current_page_item_and_ref()
        if self.preview_content_size.isEmpty() or not page_ref or not self.preview_content_rect.contains(position):
            return None

        position = QPointF(
            position.x() - self.preview_content_rect.left(),
            position.y() - self.preview_content_rect.top(),
        )

        for index in range(len(page_ref.marks) - 1, -1, -1):
            mark = page_ref.marks[index]
            rect = self.normalized_rect_to_pixmap(mark.rect, self.preview_content_size)
            for action, handle in self.mark_handle_rects(rect).items():
                if handle.contains(position):
                    return index, action
            if rect.contains(position):
                return index, "move"
        return None

    def update_existing_mark(self, mark_index, action, start_pos, current_pos, original_rect, refresh_icon=False):
        if original_rect is None:
            return

        if self.preview_content_size.isEmpty():
            return

        dx = (current_pos.x() - start_pos.x()) / max(1, self.preview_content_size.width())
        dy = (current_pos.y() - start_pos.y()) / max(1, self.preview_content_size.height())
        left, top, right, bottom = original_rect

        if action == "move":
            width = right - left
            height = bottom - top
            left = min(max(0.0, left + dx), 1.0 - width)
            top = min(max(0.0, top + dy), 1.0 - height)
            right = left + width
            bottom = top + height
        else:
            if "w" in action:
                left += dx
            if "e" in action:
                right += dx
            if "n" in action:
                top += dy
            if "s" in action:
                bottom += dy

        self.replace_mark_rect(mark_index, (left, top, right, bottom), refresh_icon)

    def clamp_normalized_rect(self, rect):
        left, top, right, bottom = rect
        left = min(max(0.0, left), 1.0)
        top = min(max(0.0, top), 1.0)
        right = min(max(0.0, right), 1.0)
        bottom = min(max(0.0, bottom), 1.0)
        if left > right:
            left, right = right, left
        if top > bottom:
            top, bottom = bottom, top

        min_size = 0.005
        if right - left < min_size:
            right = min(1.0, left + min_size)
        if bottom - top < min_size:
            bottom = min(1.0, top + min_size)
        return (left, top, right, bottom)

    def replace_mark_rect(self, mark_index, rect, refresh_icon):
        item, page_ref = self.current_page_item_and_ref()
        if not page_ref or not (0 <= mark_index < len(page_ref.marks)):
            return

        marks = list(page_ref.marks)
        marks[mark_index] = PageMark(
            marks[mark_index].kind,
            self.clamp_normalized_rect(rect),
            marks[mark_index].data,
        )
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            tuple(marks),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        if refresh_icon:
            item.setIcon(self.render_thumbnail(updated_ref))
        self.selected_mark_index = mark_index
        self.show_page(self.current_page_index)

    def delete_selected_mark(self):
        item, page_ref = self.current_page_item_and_ref()
        mark_index = self.selected_mark_index
        if not page_ref or mark_index is None or not (0 <= mark_index < len(page_ref.marks)):
            return

        self.save_undo_state()
        marks = list(page_ref.marks)
        del marks[mark_index]
        updated_ref = PageRef(
            page_ref.path,
            page_ref.page_index,
            page_ref.rotation,
            tuple(marks),
            page_ref.password,
        )
        item.setData(PAGE_ROLE, updated_ref)
        item.setIcon(self.render_thumbnail(updated_ref))
        self.selected_mark_index = None
        self.show_page(self.current_page_index)
        self.set_status("\uc8fc\uc11d \uc0ad\uc81c \uc644\ub8cc")

    def rotate_current_page(self, degrees):
        row = self.current_page_index
        if row < 0 or row >= self.page_list.count():
            return
        self.rotate_page_rows([row], degrees)

    def rotate_selected_pages_right(self):
        self.rotate_page_rows(self.selected_page_rows(), 90)

    def rotate_selected_pages_left(self):
        self.rotate_page_rows(self.selected_page_rows(), -90)

    def rotate_page_rows(self, rows, degrees):
        if not rows:
            return

        self.save_undo_state()
        changed_rows = []
        for row in rows:
            item = self.page_list.item(row)
            if item is None:
                continue
            page_ref = item.data(PAGE_ROLE)
            if not page_ref:
                continue
            rotated_ref = PageRef(
                page_ref.path,
                page_ref.page_index,
                (page_ref.rotation + degrees) % 360,
                page_ref.marks,
                page_ref.password,
            )
            item.setData(PAGE_ROLE, rotated_ref)
            item.setIcon(self.render_thumbnail(rotated_ref))
            changed_rows.append(row)

        if not changed_rows:
            return

        current_row = self.current_page_index
        if current_row in changed_rows:
            self.show_page(current_row)
        self.set_status(f"{len(changed_rows)}\uac1c \ud398\uc774\uc9c0 \ud68c\uc804 \uc644\ub8cc")

    def show_preview_context_menu(self, position):
        if self.page_count <= 0:
            return

        menu = QMenu(self)
        rotate_right = menu.addAction(make_tool_icon("rotate_right"), "\uc624\ub978\ucabd \ud68c\uc804")
        rotate_left = menu.addAction(make_tool_icon("rotate_left"), "\uc67c\ucabd \ud68c\uc804")
        menu.addSeparator()
        rect_mark = menu.addAction(make_tool_icon("rect_mark"), "\ube68\uac04 \uc0ac\uac01\ud615")
        highlight = menu.addAction(make_tool_icon("highlight"), "\ud615\uad11\ud39c")
        stamp = menu.addAction(make_tool_icon("stamp"), "도장")
        approval_stamp = menu.addAction(make_tool_icon("signature"), "결재 (내 서명)")
        selected_action = menu.exec_(self.page_label.mapToGlobal(position))
        if selected_action == rotate_right:
            self.rotate_current_page(90)
        elif selected_action == rotate_left:
            self.rotate_current_page(-90)
        elif selected_action == rect_mark:
            self.enable_rectangle_mode()
        elif selected_action == highlight:
            self.enable_highlight_mode()
        elif selected_action == stamp:
            self.insert_default_stamp()
        elif selected_action == approval_stamp:
            self.insert_signed_approval()

    def save_copy_as(self):
        if not self.require_pdf():
            return

        output_path = self.ask_save_path("PDF \ub2e4\ub978 \uc774\ub984\uc73c\ub85c \uc800\uc7a5")
        if not output_path:
            return

        if not self.write_pdf_safely(output_path, self.ordered_page_refs(), "\uc800\uc7a5 \uc624\ub958"):
            return
        self.current_pdf_path = output_path
        self.setWindowTitle(Path(output_path).name)  # title follows 다른 이름으로 저장
        self.reload_if_overwritten(output_path)
        self.set_status(f"\uc800\uc7a5\ub428: {output_path}")

    def write_pdf_safely(self, output_path, page_refs, error_title, password=None):
        """Write via a temp file in the target folder, then replace: a failed write never truncates the target
        (it may be one of the source files) and errors become a message instead of an unhandled exception."""
        output_path = str(Path(output_path))
        temp_path = None
        try:
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=output_dir) as temp_file:
                temp_path = temp_file.name
            self.write_ordered_pdf(temp_path, page_refs, password=password)
            os.replace(temp_path, output_path)
            return True
        except Exception as exc:
            if temp_path and Path(temp_path).exists():
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            self.show_error(error_title, str(exc))
            return False

    def reload_if_overwritten(self, output_path, force=False):
        """Pages are references (file, page index, rotation, marks). If the saved file was one of the sources,
        the references now point into the new content: reload it so pages are not shifted, rotated twice or
        stamped twice on the next save."""
        output_key = os.path.normcase(str(Path(output_path).resolve()))
        sources = {os.path.normcase(str(Path(ref.path).resolve())) for ref in self.ordered_page_refs()}
        if not force and output_key not in sources:
            return False
        PDF_SOURCES.invalidate(output_path)
        stale = [key for key in self.page_render_cache if os.path.normcase(str(Path(key[0]).resolve())) == output_key]
        for key in stale:
            self.page_render_cache.pop(key, None)
        self.page_size_cache.clear()
        self.thumbnail_cache.clear()
        page_refs = self.page_refs_for_pdf_with_password(output_path)
        if page_refs is None:
            return False
        index = self.current_page_index
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selected_mark_index = None
        self.annotation_mode = None
        self.preview_drag_rect = None
        self.current_pdf_path = str(Path(output_path))
        self.rebuild_page_list(page_refs, current_row=min(max(0, index), len(page_refs) - 1))
        return True

    def ask_new_pdf_password(self):
        password, ok = QInputDialog.getText(
            self,
            "PDF \uc554\ud638 \uc124\uc815",
            "\uc0c8 PDF \uc554\ud638\ub97c \uc785\ub825\ud558\uc138\uc694.",
            QLineEdit.Password,
        )
        if not ok:
            return None
        if not password:
            self.show_error("\uc554\ud638 \uc124\uc815", "\ube48 \uc554\ud638\ub294 \uc0ac\uc6a9\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")
            return None

        confirm, ok = QInputDialog.getText(
            self,
            "PDF \uc554\ud638 \ud655\uc778",
            "\uc0c8 PDF \uc554\ud638\ub97c \ud55c \ubc88 \ub354 \uc785\ub825\ud558\uc138\uc694.",
            QLineEdit.Password,
        )
        if not ok:
            return None
        if password != confirm:
            self.show_error("\uc554\ud638 \ud655\uc778", "\uc554\ud638\uac00 \uc11c\ub85c \ub2e4\ub985\ub2c8\ub2e4.")
            return None
        return password

    def save_pdf_with_password(self):
        if not self.require_pdf():
            return

        password = self.ask_new_pdf_password()
        if password is None:
            return

        output_path = self.ask_save_path("PDF \uc554\ud638 \uc124\uc815 \uc800\uc7a5")
        if not output_path:
            return

        if not self.write_pdf_safely(output_path, self.ordered_page_refs(), "PDF \uc554\ud638 \uc124\uc815 \uc624\ub958", password=password):
            return
        self.current_pdf_path = output_path
        self.setWindowTitle(Path(output_path).name)  # title follows 다른 이름으로 저장
        self.pdf_passwords[str(Path(output_path))] = password
        self.reload_if_overwritten(output_path)
        self.set_status(f"\uc554\ud638 PDF \uc800\uc7a5\ub428: {output_path}")

    def save_pdf_without_password(self):
        if not self.require_pdf():
            return

        output_path = self.ask_save_path("PDF \uc554\ud638 \ud574\uc81c \uc800\uc7a5")
        if not output_path:
            return

        if not self.write_pdf_safely(output_path, self.ordered_page_refs(), "PDF \uc554\ud638 \ud574\uc81c \uc624\ub958", password=None):
            return
        self.current_pdf_path = output_path
        self.setWindowTitle(Path(output_path).name)  # title follows 다른 이름으로 저장
        overwritten = self.reload_if_overwritten(output_path)
        self.pdf_passwords.pop(str(Path(output_path)), None)
        if overwritten:
            for row in range(self.page_list.count()):  # the reloaded file has no password any more
                item = self.page_list.item(row)
                ref = item.data(PAGE_ROLE)
                if ref and ref.password:
                    item.setData(PAGE_ROLE, PageRef(ref.path, ref.page_index, ref.rotation, ref.marks, ""))
        self.set_status(f"\uc554\ud638 \ud574\uc81c PDF \uc800\uc7a5\ub428: {output_path}")

    def print_pdf(self):
        if not self.require_pdf():
            return

        from PyQt5.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.ScreenResolution)
        printer.setResolution(PRINT_DPI)
        printer.setDocName(Path(self.current_pdf_path).name if self.current_pdf_path else APP_TITLE)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("PDF \uc778\uc1c4")
        if dialog.exec_() != QPrintDialog.Accepted:
            return

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
                temp_path = temp_file.name
            self.write_ordered_pdf(temp_path, self.ordered_page_refs())
            self.print_pdf_file(temp_path, printer)
            self.set_status("\uc778\uc1c4 \uc791\uc5c5\uc744 \ubcf4\ub0c8\uc2b5\ub2c8\ub2e4.")
        except Exception as exc:
            self.show_error("\uc778\uc1c4 \uc624\ub958", str(exc))
        finally:
            if temp_path and Path(temp_path).exists():
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


    def save_current(self):
        if not self.require_pdf():
            return

        if not self.current_pdf_path:
            self.save_copy_as()
            return

        output_path = str(Path(self.current_pdf_path))
        # An encrypted original stays encrypted with the same password (it used to be saved without one).
        password = self.pdf_passwords.get(output_path) or None
        if not self.write_pdf_safely(output_path, self.ordered_page_refs(), "\uc800\uc7a5 \uc624\ub958", password=password):
            return
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.reload_if_overwritten(output_path)
        self.set_status(f"\uc800\uc7a5\ub428: {output_path}")

    def merge_pdfs(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "\ubcd1\ud569\ud560 PDF \uc120\ud0dd", "", PDF_FILTER)
        if not paths:
            return

        self.save_undo_state()
        previous_pdf_path = self.current_pdf_path
        if self.page_list.count() == 0:
            self.current_pdf_path = paths[0]
        added_count = 0
        for path in paths:
            before_count = self.page_list.count()
            if self.add_pdf_pages_to_list(path):
                added_count += self.page_list.count() - before_count
        if added_count == 0:
            self.undo_stack.pop()
            self.current_pdf_path = previous_pdf_path
            return
        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        if self.page_list.currentRow() < 0:
            self.page_list.setCurrentRow(0)
            self.show_page(0)
        self.set_status(f"PDF {len(paths)}\uac1c\uac00 \ub4a4\uc5d0 \ucd94\uac00\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")

    def extract_pages(self):
        if not self.require_pdf():
            return

        text, ok = QInputDialog.getText(
            self,
            "\ud398\uc774\uc9c0 \ucd94\ucd9c",
            f"\ucd94\ucd9c\ud560 \ud398\uc774\uc9c0\ub97c \uc785\ub825\ud558\uc138\uc694. (1-{self.page_count})\n\uc608: 1-3, 5, 8",
        )
        if not ok:
            return

        try:
            selected_pages = parse_page_ranges(text, self.page_count)
        except ValueError as exc:
            self.show_error("\ud398\uc774\uc9c0 \ubc94\uc704 \uc624\ub958", str(exc))
            return

        output_path = self.ask_save_path("\ucd94\ucd9c\ud55c PDF \uc800\uc7a5")
        if not output_path:
            return

        ordered_refs = self.ordered_page_refs()
        selected_refs = [ordered_refs[index] for index in selected_pages]
        if not self.write_pdf_safely(output_path, selected_refs, "\ud398\uc774\uc9c0 \ucd94\ucd9c \uc624\ub958"):
            return
        self.page_render_cache.clear()
        self.load_pdf(output_path)
        self.set_status(f"{len(selected_pages)}\uac1c \ud398\uc774\uc9c0 \ucd94\ucd9c \uc644\ub8cc")

    def insert_pdf(self):
        if not self.require_pdf():
            return

        insert_path, _ = QFileDialog.getOpenFileName(self, "\uc0bd\uc785\ud560 PDF \uc120\ud0dd", "", PDF_FILTER)
        if not insert_path:
            return

        choices = [
            f"\ud604\uc7ac \ud398\uc774\uc9c0 \uc55e ({self.current_page_index + 1})",
            f"\ud604\uc7ac \ud398\uc774\uc9c0 \ub4a4 ({self.current_page_index + 1})",
            "\ub9e8 \ub4a4",
        ]
        choice, ok = QInputDialog.getItem(self, "\uc0bd\uc785 \uc704\uce58", "\uc704\uce58", choices, 1, False)
        if not ok:
            return

        self.save_undo_state()
        insert_row = self.page_list.count()
        if choice.startswith("\ud604\uc7ac \ud398\uc774\uc9c0 \uc55e"):
            insert_row = self.current_page_index
        elif choice.startswith("\ud604\uc7ac \ud398\uc774\uc9c0 \ub4a4"):
            insert_row = self.current_page_index + 1

        new_items = self.create_items_for_pdf(insert_path)
        if not new_items:
            self.undo_stack.pop()
            return
        for offset, item in enumerate(new_items):
            self.page_list.insertItem(insert_row + offset, item)

        self.refresh_preview_reference_width()
        self.refresh_page_numbers()
        self.schedule_thumbnail_refresh()
        self.page_list.setCurrentRow(insert_row)
        self.show_page(insert_row)
        self.set_status(f"\uc0bd\uc785 \uc644\ub8cc: {Path(insert_path).name}")

    def create_items_for_pdf(self, path):
        items = []
        page_refs = self.page_refs_for_pdf_with_password(path)
        if page_refs is None:
            return items
        items.extend(self.create_item_for_page_ref(page_ref) for page_ref in page_refs)
        return items

    def ask_save_path(self, title):
        if self.current_pdf_path:
            default_dir = str(Path(self.current_pdf_path).parent)
        else:
            default_dir = ""

        output_path, _ = QFileDialog.getSaveFileName(self, title, default_dir, PDF_FILTER)
        if output_path and not output_path.lower().endswith(".pdf"):
            output_path += ".pdf"
        return output_path


    def require_pdf(self):
        if self.page_list.count() > 0:
            return True
        self.show_error("PDF \uc5c6\uc74c", "\uba3c\uc800 PDF \ud30c\uc77c\uc744 \uc5f4\uc5b4\uc8fc\uc138\uc694.")
        return False

    def convert_to_excel(self):
        self.open_convert_dialog("excel")

    def convert_to_hwp(self):
        self.open_convert_dialog("hwp")

    def convert_to_image(self):
        self.open_convert_dialog("image")

    def export_selected_pages(self, kind):
        """Thumbnail right-click > 내보내기: the selected pages (list order) -> a new PDF, or the 변환 window
        (엑셀/한글/이미지) preset to exactly those pages."""
        if self.page_list.count() == 0:
            return None
        rows = self.selected_page_rows()
        if not rows:
            return None
        if kind == "pdf":
            return self.export_selected_pages_pdf(rows)
        return self.open_convert_dialog(kind, page_rows=rows)

    def export_selected_pages_pdf(self, rows):
        from kpdf_convert import unique_path
        from kpdf_convert_dialog import load_convert_preferences, open_folder, page_ranges_text, save_convert_preferences

        refs = self.page_refs_for_rows(rows)
        source = Path(self.current_pdf_path or refs[0].path)
        prefs = load_convert_preferences()
        folder = prefs.get("output_dir") if prefs.get("output_dir") and Path(prefs["output_dir"]).is_dir() else str(source.parent)
        label = page_ranges_text(rows).replace(", ", "_")
        default_path = unique_path(Path(folder) / f"{source.stem}_p{label}.pdf")
        output_path, _ = QFileDialog.getSaveFileName(self, f"선택한 {len(rows)}쪽을 PDF로 내보내기", str(default_path), PDF_FILTER)
        if not output_path:
            return None
        if not output_path.lower().endswith(".pdf"):
            output_path += ".pdf"
        if not self.write_pdf_safely(output_path, refs, "내보내기 오류"):
            return None
        self.reload_if_overwritten(output_path)
        save_convert_preferences(output_dir=str(Path(output_path).parent))
        if prefs.get("open_folder", True):
            open_folder(Path(output_path).parent, output_path)
        self.set_status(f"{len(rows)}쪽을 PDF로 내보냄: {output_path}")
        return output_path

    def open_convert_dialog(self, kind=None, page_rows=None):
        """'변환' window (엑셀/한글/이미지) for the open document as shown (order, rotation, stamps) or another PDF.
        The toolbar button opens it on the last used format."""
        from kpdf_convert_dialog import ConvertDialog, load_convert_preferences  # loaded on first use: start-up stays fast

        dialog = ConvertDialog(self, kind or load_convert_preferences().get("kind", "excel"), page_rows=page_rows)
        self.convert_dialog = dialog
        try:
            dialog.exec_()
        finally:
            dialog.close()
            self.convert_dialog = None
        return dialog

    def show_file_info(self):
        if not self.require_pdf():
            return
        QMessageBox.information(
            self,
            "\ud604\uc7ac \ud30c\uc77c \uc815\ubcf4",
            f"\ud398\uc774\uc9c0 \uc218: {self.page_count}\n\ucd9c\ucc98 PDF \uc218: {len(set(ref.path for ref in self.ordered_page_refs()))}",
        )

    def refresh_default_app_menu(self):
        try:
            is_default = is_pdf_default_app()
            registered = is_pdf_association_registered()
        except OSError:
            is_default, registered = False, False
        self.default_app_action.setChecked(is_default)
        self.default_app_action.setText(
            "PDF \uae30\ubcf8 \uc571: Koinfra PDF (\uc0ac\uc6a9 \uc911)" if is_default else "PDF \uae30\ubcf8 \uc571\uc73c\ub85c \uc124\uc815\u2026"
        )
        self.unregister_assoc_action.setEnabled(registered)


    def unregister_openwith_from_menu(self):
        answer = QMessageBox.question(
            self,
            "PDF \uc5f0\uacb0 \ub4f1\ub85d \ud574\uc81c",
            "Koinfra PDF\uc758 PDF \uc5f0\uacb0 \ub4f1\ub85d\uc744 \uc9c0\uc6b8\uae4c\uc694?\n"
            "(\ud604\uc7ac \uae30\ubcf8 \uc571\uc774\uba74 Windows\uac00 \ub2e4\uc74c\uc5d0 PDF\ub97c \uc5f4 \ub54c \ub2e4\ub978 \uc571\uc744 \uace0\ub974\uac8c \ud569\ub2c8\ub2e4.)",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            unregister_openwith()
        except OSError as exc:
            self.show_error("\ud574\uc81c \uc624\ub958", str(exc))
            return
        save_association_preferences(disabled=True)  # do not silently re-register on the next start
        self.set_status("PDF \uc5f0\uacb0 \ub4f1\ub85d\uc744 \ud574\uc81c\ud588\uc2b5\ub2c8\ub2e4.")

    def set_pdf_default_app(self):
        """One click: Windows app picker ('Always'), else Settings > Default apps; then confirm automatically."""
        if self.default_app_action.isCheckable():
            self.default_app_action.setChecked(False)  # the check mark only reflects the real Windows state
        try:
            if is_pdf_default_app():
                self.refresh_default_app_menu()
                QMessageBox.information(self, "PDF \uae30\ubcf8 \uc571", "\uc774\ubbf8 Koinfra PDF\uac00 PDF \uae30\ubcf8 \uc571\uc785\ub2c8\ub2e4.")
                return
            register_openwith()
        except OSError as exc:
            self.show_error("\uae30\ubcf8 \uc571 \uc124\uc815 \uc624\ub958", str(exc))
            return
        save_association_preferences(disabled=False)
        self.start_default_app_watch()
        before = open_with_process_ids()
        try:
            process = launch_open_with_picker(default_app_check_pdf_path())
        except (OSError, RuntimeError):
            self.open_default_pdf_app_settings()
            return
        self.open_with_attempt = {"process": process, "before": before, "started": time.monotonic(), "shown": False}
        self.set_status("Windows \uc571 \uc120\ud0dd \ucc3d\uc744 \uc5ec\ub294 \uc911\u2026")
        QTimer.singleShot(300, self.poll_open_with_picker)

    def poll_open_with_picker(self):
        attempt = self.open_with_attempt
        if attempt is None or attempt["shown"]:
            return
        process = attempt["process"]
        own_pid = getattr(process, "pid", None)
        blocked = _visible_windows_of({own_pid}) if own_pid else []
        if blocked:  # Windows answered with "go to Settings" instead of a picker
            close_windows(blocked)
            self.fall_back_to_settings()
            return
        pickers = _visible_windows_of(open_with_process_ids() - set(attempt["before"]))
        if pickers:
            attempt["shown"] = True
            self.show_default_app_guide(DEFAULT_APP_GUIDE_PICKER)
            return
        if (time.monotonic() - attempt["started"]) * 1000 >= OPEN_WITH_PICKER_TIMEOUT_MS:
            self.fall_back_to_settings()
            return
        QTimer.singleShot(300, self.poll_open_with_picker)

    def fall_back_to_settings(self):
        attempt, self.open_with_attempt = self.open_with_attempt, None
        if attempt is not None:
            stop_open_with_leftovers(attempt["process"], attempt["before"])
        self.open_default_pdf_app_settings()

    def open_default_pdf_app_settings(self):
        """Fallback: Settings > Default apps opened on the Koinfra PDF page."""
        try:
            open_default_pdf_app_settings()
        except OSError as exc:
            self.show_error("\uae30\ubcf8 \uc571 \uc124\uc815 \uc624\ub958", str(exc))
            return
        save_association_preferences(disabled=False)
        self.start_default_app_watch()
        self.show_default_app_guide(DEFAULT_APP_GUIDE_SETTINGS)

    def show_default_app_guide(self, text):
        """Non-blocking hint next to the Windows window (does not steal focus from it)."""
        self.set_status(text)
        warning = association_target_warning()
        if self.default_app_guide is not None:
            self.default_app_guide.close()
        box = QMessageBox(self)
        box.setWindowTitle("PDF \uae30\ubcf8 \uc571 \uc124\uc815")
        box.setIcon(QMessageBox.Information)
        box.setText(text)
        box.setInformativeText(("\uc644\ub8cc\ub418\uba74 \uc790\ub3d9\uc73c\ub85c \ud655\uc778\ud574 \uc54c\ub824\ub4dc\ub9bd\ub2c8\ub2e4." + ("\n\n" + warning if warning else "")))
        box.setStandardButtons(QMessageBox.Ok)
        box.setModal(False)
        box.setAttribute(Qt.WA_ShowWithoutActivating, True)
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.finished.connect(lambda _result, b=box: self._forget_guide(b))
        self.default_app_guide = box
        box.show()

    def _forget_guide(self, box):
        if self.default_app_guide is box:
            self.default_app_guide = None

    def start_default_app_watch(self):
        """Re-check the Windows default when we get focus back, plus a light 2 s poll for a few minutes."""
        self.default_app_done_shown = False
        if self.default_app_watch is None:
            self.default_app_watch = QTimer(self)
            self.default_app_watch.setInterval(2000)
            self.default_app_watch.timeout.connect(lambda: self.check_default_app_done(from_focus=False))
        self.default_app_watch_deadline = time.monotonic() + DEFAULT_APP_WATCH_MS / 1000
        self.default_app_watch.start()

    def on_application_state_changed(self, state):
        if state == Qt.ApplicationActive and self.default_app_watch is not None and self.default_app_watch.isActive():
            QTimer.singleShot(400, lambda: self.check_default_app_done(from_focus=True))

    def check_default_app_done(self, from_focus=False):
        watch = self.default_app_watch
        if watch is None or not watch.isActive():
            return False
        try:
            done = is_pdf_default_app()
        except OSError:
            done = False
        if not done:
            if time.monotonic() > getattr(self, "default_app_watch_deadline", 0):
                watch.stop()
            return False
        watch.stop()
        self.open_with_attempt = None
        if self.default_app_guide is not None:
            self.default_app_guide.close()
        self.refresh_default_app_menu()
        self.set_status(DEFAULT_APP_DONE_TEXT + " \u2014 \uc774\uc81c PDF\ub294 Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4.")
        if not self.default_app_done_shown:
            self.default_app_done_shown = True
            QMessageBox.information(self, DEFAULT_APP_DONE_TEXT, "\uc774\uc81c PDF \ud30c\uc77c\uc744 \uc5f4\uba74 Koinfra PDF\ub85c \uc5f4\ub9bd\ub2c8\ub2e4.")
        return True

    def maybe_ask_default_app(self):
        """Once at start: offer to make Koinfra PDF the default (with 'don't ask again')."""
        prefs = load_association_preferences()
        if prefs["disabled"] or not prefs["ask_default"]:
            return
        try:
            if is_pdf_default_app():
                return
        except OSError:
            return
        box = QMessageBox(self)
        box.setWindowTitle("PDF \uae30\ubcf8 \uc571")
        box.setIcon(QMessageBox.Question)
        box.setText("PDF\ub97c \uc5f4 \ub54c \ud56d\uc0c1 Koinfra PDF\ub97c \uc0ac\uc6a9\ud558\ub3c4\ub85d \uc124\uc815\ud560\uae4c\uc694?")
        box.setInformativeText("\ub098\uc911\uc5d0 [\ub3c4\uad6c] \uba54\ub274\uc5d0\uc11c\ub3c4 \uc124\uc815\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dont_ask = QCheckBox("\ub2e4\uc2dc \ubb3b\uc9c0 \uc54a\uae30")
        box.setCheckBox(dont_ask)
        self.default_app_prompt = box
        answer = box.exec_()
        self.default_app_prompt = None
        if dont_ask.isChecked():
            save_association_preferences(ask_default=False)
        if answer == QMessageBox.Yes:
            self.set_pdf_default_app()

    def show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def set_status(self, message):
        self.statusBar().showMessage(message)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if any(url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf") for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            self.append_pdf_files(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


def main():
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    configure_windows_taskbar_icon()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app_icon = load_app_icon()
    app.setWindowIcon(app_icon)
    try:
        startup_register()  # keeps the command path current (Open with list) / repairs a stale one; never touches UserChoice
    except OSError:
        pass
    window = PdfPreviewer()
    window.setWindowIcon(app_icon)
    startup_paths = [str(Path(arg).resolve()) for arg in sys.argv[1:] if arg.lower().endswith(".pdf") and Path(arg).is_file()]
    if startup_paths:
        window.append_pdf_files(startup_paths)
    window.show()
    if "--set-default-pdf" in sys.argv[1:]:  # installer finish page: start the default-app flow right away
        QTimer.singleShot(700, window.set_pdf_default_app)
    elif sys.platform == "win32" and not os.environ.get("KOINFRA_NO_DEFAULT_PROMPT"):
        QTimer.singleShot(900, window.maybe_ask_default_app)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

"""Live check of the real picker helpers (no selection is ever made): launch, detect for 6 s, then clean up.
Also lists the .pdf 'recommended' handlers Windows will offer (SHAssocEnumHandlers, read only)."""
import sys, time, importlib.util, os, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("K", Path(sys.argv[1]) / "Koinfra.py"); K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)
from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
tempfile.tempdir = tempfile.mkdtemp(prefix="koinfra_live_")
pdf = K.default_app_check_pdf_path()
before = K.open_with_process_ids()
proc = K.launch_open_with_picker(pdf)
t0 = time.monotonic(); picker = blocked = []
while time.monotonic() - t0 < 6:
    time.sleep(0.3)
    blocked = K._visible_windows_of({proc.pid})
    picker = K._visible_windows_of(K.open_with_process_ids() - before)
    if blocked or picker: break
print("picker windows:", picker, "blocked windows:", blocked, "after %.1fs" % (time.monotonic() - t0))
if blocked: K.close_windows(blocked)
if picker: K.close_windows(picker)  # close without choosing anything
new = K.open_with_process_ids() - before
K.stop_open_with_leftovers(proc, before)
time.sleep(1)
print("spawned OpenWith:", sorted(new), "left after cleanup:", sorted(K.open_with_process_ids() - before), "rundll alive:", proc.poll() is None)

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from coscribe.tools.exec import Isolation, isolation, own_python

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="probe")

PROBE = r'''
import ctypes, os, sys
from ctypes import wintypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.restype = wintypes.HANDLE
k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
def final(p, flags):
    h = k.CreateFileW(p, 0, 0, None, 3, 0x02000000, None)
    if h in (None, wintypes.HANDLE(-1).value, 2**64 - 1):
        return ("open failed", ctypes.get_last_error())
    buf = ctypes.create_unicode_buffer(1024)
    n = k.GetFinalPathNameByHandleW(h, buf, 1024, flags)
    e = ctypes.get_last_error()
    k.CloseHandle(h)
    return (n, e, buf.value)
for p in sys.argv[1:]:
    print("FINAL", p, "normalized", final(p, 0), "opened", final(p, 8), flush=True)
    q = os.path.dirname(p)
    while True:
        try:
            os.listdir(q); r = "list ok"
        except OSError as exc:
            r = f"list {exc.winerror}"
        print("  ANC", q, r, flush=True)
        up = os.path.dirname(q)
        if up == q:
            break
        q = up
'''


def _run(argv, cwd, iso):
    proc = isolation.start(argv, str(cwd), dict(os.environ), iso)
    got = {}
    t1 = threading.Thread(target=lambda: got.__setitem__("out", proc.stdout.read()), daemon=True)
    t2 = threading.Thread(target=lambda: got.__setitem__("err", proc.stderr.read()), daemon=True)
    t1.start(); t2.start(); t1.join(60); t2.join(5)
    code = proc.wait(10)
    return code, got.get("out", b"").decode("utf-8", "replace"), got.get("err", b"").decode("utf-8", "replace")


def test_probe(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "p.py").write_text(PROBE, encoding="utf-8")
    own = own_python.ensure_own_python(state)
    print("PROBE own", own, flush=True)
    venv = state / "venv"
    subprocess.run([str(own), "-m", "venv", str(venv)], check=True)
    vpy = venv / "Scripts" / "python.exe"
    sysbase = Path(sys.base_prefix) / "python.exe"
    sysvenv = state / "sysvenv"
    subprocess.run([sys.executable, "-m", "venv", str(sysvenv)], check=True)
    for label, py, base, rt in [
        ("own", vpy, own, isolation.venv_runtime(venv)),
        ("system", sysvenv / "Scripts" / "python.exe", sysbase, isolation.venv_runtime(sysvenv)),
    ]:
        iso = Isolation(state, writable=(work,), runtime=tuple(rt), network=False)
        code, out, err = _run([str(py), str(work / "p.py"), str(base)], work, iso)
        print(f"PROBE {label} exit={code}\n{out}\nERR={err}", flush=True)
    for p in [Path("C:/"), Path("C:/Users"), Path(os.environ["USERPROFILE"]), Path(os.environ["LOCALAPPDATA"])]:
        print("PROBE icacls", subprocess.run(["icacls", str(p)], capture_output=True, text=True).stdout[:700], flush=True)
    # Then let every container list the folders above the own Python, this folder only.
    q = own.parent.parent
    while q.parent != q:
        r = subprocess.run(["icacls", str(q), "/grant", "*S-1-15-2-1:(RD)"], capture_output=True, text=True)
        print("PROBE grant", q, r.returncode, flush=True)
        q = q.parent
    iso = Isolation(state, writable=(work,), runtime=tuple(isolation.venv_runtime(venv)), network=False)
    code, out, err = _run([str(vpy), str(work / "p.py"), str(own)], work, iso)
    print(f"PROBE own after list grants exit={code}\n{out}\nERR={err}", flush=True)

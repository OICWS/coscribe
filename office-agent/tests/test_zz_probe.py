import os
import time
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from coscribe.tools.exec import Isolation, isolation
from coscribe.tools.exec import _appcontainer

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="probe")


def _run(argv, cwd, iso):
    import threading
    proc = isolation.start(argv, str(cwd), dict(os.environ), iso)
    got = {}
    t1 = threading.Thread(target=lambda: got.__setitem__("out", proc.stdout.read()), daemon=True)
    t2 = threading.Thread(target=lambda: got.__setitem__("err", proc.stderr.read()), daemon=True)
    t1.start(); t2.start()
    t1.join(20); t2.join(5)
    try:
        code = proc.wait(5)
    except Exception:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        code = "HUNG"
    out = got.get("out", b"<none>")
    err = got.get("err", b"<none>")
    return f"exit={code} out={out.decode('utf-8','replace')[-300:]!r} err={err.decode('utf-8','replace')[-500:]!r}"


def _say(lines, text):
    lines.append(text)
    print("PROBE", text, flush=True)


def test_probe(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "marker.txt").write_text("x")
    (work / "s.js").write_text("console.log('node ok', process.cwd())")
    (work / "s.py").write_text("import os; print('py ok', os.listdir('.'))")
    node = Path(shutil.which("node")).resolve()
    iso = Isolation(state, writable=(work,), runtime=(node.parent,))
    lines = [f"work={work}"]
    for p in [Path("C:/"), Path("C:/Users"), Path(os.environ["USERPROFILE"]), work.parent]:
        _say(lines, f"icacls {p}: " + subprocess.run(["icacls", str(p)], capture_output=True, text=True).stdout[:600])
    cmd = os.environ.get("ComSpec", "cmd.exe")
    cases = {
        "cmd echo": [cmd, "/d", "/c", "echo hi"],
        "cmd dir": [cmd, "/d", "/c", "dir /b"],
        "cmd dir abs": [cmd, "/d", "/c", f"dir /b {work}"],
        "node": [str(node), str(work / "s.js")],
        "node preserve": [str(node), "--preserve-symlinks", "--preserve-symlinks-main", str(work / "s.js")],
        "node rel": [str(node), "s.js"],
    }
    t = time.time()
    names, failed = isolation.capabilities_for(iso)
    _say(lines, f"first grants took {time.time() - t:.1f}s failed={failed} node={node}")
    for name, argv in cases.items():
        try:
            _say(lines, f"[{name}] " + _run(argv, work, iso))
        except Exception as exc:  # noqa: BLE001
            _say(lines, f"[{name}] raised {exc!r}")
    t = time.time()
    names, failed = isolation.capabilities_for(iso)
    _say(lines, f"grants took {time.time() - t:.1f}s failed={failed}")
    sid = _appcontainer.capability_sid(names[0])
    granted = []
    for anc in work.parents:
        r = subprocess.run(["icacls", str(anc), "/grant", f"*{sid}:(RA)", "/Q"], capture_output=True, text=True)
        granted.append(f"{anc}:{r.returncode}")
    _say(lines, "RA grants: " + " ".join(granted))
    for name, argv in cases.items():
        try:
            _say(lines, f"[RA {name}] " + _run(argv, work, iso))
        except Exception as exc:  # noqa: BLE001
            _say(lines, f"[RA {name}] raised {exc!r}")
    granted = []
    for anc in work.parents:
        r = subprocess.run(["icacls", str(anc), "/grant", f"*{sid}:(RD,RA)", "/Q"], capture_output=True, text=True)
        granted.append(f"{anc}:{r.returncode}")
    _say(lines, "RD grants: " + " ".join(granted))
    for name, argv in cases.items():
        try:
            _say(lines, f"[RD {name}] " + _run(argv, work, iso))
        except Exception as exc:  # noqa: BLE001
            _say(lines, f"[RD {name}] raised {exc!r}")
    for anc in work.parents:
        subprocess.run(["icacls", str(anc), "/remove:g", f"*{sid}", "/Q"], capture_output=True)
    pytest.fail("\n".join(lines))

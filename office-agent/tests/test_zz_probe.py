import sys
import tempfile
from pathlib import Path

import pytest

from coscribe.tools.exec import isolation, script_env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows probe")
def test_probe() -> None:
    state = Path(tempfile.mkdtemp(prefix="probe_state_"))
    venv = script_env.ensure_script_env(state)
    print("venv", venv, (venv / "pyvenv.cfg").read_text())
    python = script_env.venv_python(venv)
    runtime = isolation.venv_runtime(venv)
    print("runtime", runtime)
    print("own-first", script_env._own_python_first(state, script_env._venv_create_candidates(state)))
    result = isolation._run_self_test(state, python, runtime)
    print("SELFTEST", result)
    base = Path(sys.base_prefix)
    result2 = isolation._run_self_test(state, Path(sys.executable), [base, Path(sys.prefix)])
    print("SELFTEST sys.executable", result2)

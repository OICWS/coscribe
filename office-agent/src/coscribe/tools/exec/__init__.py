"""Running the user's own Python and Node scripts: the tools, their isolated environments and
the folder guard, and the AppContainer that holds a script to its folders on Windows
(`isolation.py`, decision 0021)."""

from __future__ import annotations

from .capabilities import Capabilities, CapabilityOff, require_capability
from .command_sandbox import (
    record_sandbox_setup,
    sandbox_codex,
    sandbox_home,
    sandbox_setup_argv,
    sandbox_status,
    sandbox_uninstall_argv,
    sandboxed_command,
    warm_up_sandbox,
)
from .commands import expect_sandbox, start_command
from .find_python import find_pythons
from .isolation import (
    FolderGrants,
    Isolation,
    IsolationRefused,
    SelfTest,
    UnisolatedConversations,
    isolation_status,
    self_test,
    sweep_folder_grants,
)
from .isolation import available as isolation_available
from .isolation import for_scripts as isolation_for_scripts
from .isolation import scratch_root as script_scratch_root
from .node_env import ensure_node_env
from .node_env import install_package as install_node_package
from .node_env import list_packages as list_node_packages
from .node_env import uninstall_package as uninstall_node_package
from .node_scripts import build_node_script_tools
from .own_python import ensure_own_python, own_python_offer
from .running import Detachable, HandOff, RunningCommand, request_detach, stop_commands
from .script_env import (
    ensure_script_env,
    fallbacks_for_platform,
    get_interpreter_override,
    overlay_env,
    resolve_python_command,
    set_interpreter_override,
    venv_python,
    working_interpreters,
)
from .script_env import install_package as install_python_package
from .script_env import list_packages as list_python_packages
from .script_env import uninstall_package as uninstall_python_package
from .script_guard import (
    blocked_write,
    node_guard_args,
    refusal_note,
    suggested_folder,
    without_guard_warnings,
    writable_roots,
    write_python_wrapper,
)
from .scripts import _run_python_script as run_python_script
from .scripts import build_script_tools

__all__ = [
    "Capabilities",
    "CapabilityOff",
    "require_capability",
    "record_sandbox_setup",
    "sandbox_codex",
    "sandbox_home",
    "sandbox_setup_argv",
    "sandbox_status",
    "sandbox_uninstall_argv",
    "sandboxed_command",
    "start_command",
    "expect_sandbox",
    "warm_up_sandbox",
    "Detachable",
    "FolderGrants",
    "sweep_folder_grants",
    "HandOff",
    "Isolation",
    "IsolationRefused",
    "UnisolatedConversations",
    "isolation_status",
    "RunningCommand",
    "SelfTest",
    "blocked_write",
    "build_node_script_tools",
    "build_script_tools",
    "ensure_node_env",
    "ensure_script_env",
    "ensure_own_python",
    "find_pythons",
    "own_python_offer",
    "fallbacks_for_platform",
    "get_interpreter_override",
    "install_node_package",
    "install_python_package",
    "isolation_available",
    "isolation_for_scripts",
    "list_node_packages",
    "list_python_packages",
    "node_guard_args",
    "overlay_env",
    "refusal_note",
    "request_detach",
    "resolve_python_command",
    "run_python_script",
    "script_scratch_root",
    "self_test",
    "set_interpreter_override",
    "stop_commands",
    "suggested_folder",
    "uninstall_node_package",
    "uninstall_python_package",
    "venv_python",
    "without_guard_warnings",
    "working_interpreters",
    "writable_roots",
    "write_python_wrapper",
]

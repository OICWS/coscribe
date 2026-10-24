# tools.exec: Python and Node script tools, their environments and the folder guard

## Purpose
Owns `run_python_script` and `run_node_script` (`scripts.py`, `node_scripts.py`), the process they wait on, which the person can hand to the background tasks (`running.py`), the dedicated
Python venv and the npm directory the scripts run in (`script_env.py`, `node_env.py`), the Pythons offered for that venv (`find_python.py`, and coscribe's own Python, downloaded only on request, `own_python.py`) and the
guard that catches a script's mistaken writes outside the conversation's folders
(`script_guard.py`), and the Windows AppContainer that will hold a script to its folders
(`isolation.py`, `_appcontainer.py`; decision 0021), and the command line for Codex's Windows
sandbox that holds a background command to its folders (`command_sandbox.py`, started by
`commands.py`; decision 0022). Does not own the background-command tool (`tools/bookkeeping/background_tasks.py`,
which uses the same environments), the approval policy for scripts (`runtime_lg/exec_policy.py`)
or the Settings routes that install packages (`web/routes/settings/environments.py`).

## Public interface
`build_script_tools`, `build_node_script_tools`, `ensure_script_env`, `ensure_node_env`,
`venv_python`, `overlay_env`, `resolve_python_command`, the package-install functions
(`install_python_package`, `list_python_packages`, `uninstall_python_package` and the `_node_`
twins), the interpreter overrides (`get_interpreter_override`, `set_interpreter_override`,
`working_interpreters`, `fallbacks_for_platform`), the folder-guard helpers (`suggested_folder`,
`blocked_write`, `refusal_note`, `node_guard_args`, `writable_roots`, `write_python_wrapper`,
`without_guard_warnings`) and `run_python_script`, and for letting a running script go on in the background (`running.py`) `RunningCommand`, `Detachable`, `HandOff`, `request_detach`, `stop_commands`, and for the container `Isolation`,
`FolderGrants`, `sweep_folder_grants`, `self_test`, `SelfTest`, `isolation_available`, `isolation_for_scripts`,
`script_scratch_root`, and for the command sandbox `start_command`, `sandboxed_command`, `sandbox_status`,
`sandbox_codex`, `sandbox_home`, `record_sandbox_setup`, `sandbox_setup_argv`,
`sandbox_uninstall_argv`, `warm_up_sandbox`, `expect_sandbox`. Import them from
`coscribe.tools.exec`.

## Depends on
`coscribe.runtime`, `coscribe.tools._files_written`, `coscribe.tools._output_truncation`.

## Invariants
- On Windows, where the self-test passed, every script run (the two tools and the background
  ones in `tools/bookkeeping/background_tasks.py`) starts in the AppContainer with only its
  conversation's folders (decision 0021). Where the self-test failed, `for_scripts` refuses the
  run (`IsolationRefused`) unless the conversation is in `UnisolatedConversations`. Off Windows,
  or so allowed, it runs as the user, and the folder guard is a mistake catcher, not a security
  boundary. Text for the model or the person says which. A command line (the background
  command tool) is never started in the container: cmd can't list folders there, so
  `isolation.start` refuses it. It runs in Codex's sandbox where Settings set it up
  (`sandboxed_command`); otherwise `require_capability` refuses it until the person also allowed
  commands without the sandbox, and approvals then ask the person for each one (decision 0022).
- Both script tools carry the `EXEC` risk tier; approvals read it.
- Tool schemas are fixed and snapshotted (`tests/tool_schemas.txt`).
- `RunningCommand` keeps one interface whether or not it runs in the container: the
  background hand-over and Stop use it.
- A folder's grant is recorded in `folder-grants.json` before `icacls` writes it, so an ACE is
  never on a folder without an entry to remove it by. Read-write grants carry no DELETE: a
  container cannot remove or rename, only a trusted tool can. At each start the server takes
  the grants off folders no longer in Settings or any conversation (`sweep_folder_grants`), and
  the uninstaller takes all of them off (`coscribe-server --revoke-folder-grants`).

## Tests
`tests/test_scripts_tool.py`, `tests/test_node_scripts_tool.py`, `tests/test_script_guard.py`,
`tests/test_script_env.py`, `tests/test_own_python.py`, `tests/test_node_env.py`, `tests/test_env_lock.py`,
`tests/test_isolation.py` (its container tests run in the Windows CI job),
`tests/test_command_sandbox.py` (the real sandbox runs in `tests/test_windows_real.py`).

## Verify by hand
A script run on Windows with no Python or Node installed; a package install from Settings;
the self-test on a standard-user Windows 11 laptop and in a OneDrive folder; setting the command
sandbox up from Settings on a laptop, where Windows asks an administrator.

## Reviewed
2026-10-17 (first card).

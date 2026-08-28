"""Code Mode: run a Python script inside the Docker sandbox that calls
read/edit/write/bash programmatically via an injected ``minicc_tools`` facade.

Protocol: the facade writes a line ``__MINICC_TOOL_CALL__ {json}`` to stdout for
each tool invocation and blocks reading a line ``__MINICC_TOOL_RESULT__ {json}``
from stdin. The host (this module) multiplexes the container's stdout, routing
those tagged lines through ``dispatch`` (which reuses the normal policy/executor
path) and passing everything else through as the script's own stdout. A plain
line-prefix convention is used instead of extra fds because ``docker exec``
stdio handling for >2 fds is unreliable on Windows hosts.
"""

from __future__ import annotations

import base64
import json
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MINICC_TOOLS_DIR = "/opt/minicc"
MINICC_TOOLS_PATH = f"{MINICC_TOOLS_DIR}/minicc_tools.py"

_TOOL_CALL_PREFIX = "__MINICC_TOOL_CALL__ "
_TOOL_RESULT_PREFIX = "__MINICC_TOOL_RESULT__ "

MINICC_TOOLS_FACADE_SOURCE = '''\
import json
import sys


def _call(tool, **arguments):
    sys.stdout.write(
        "__MINICC_TOOL_CALL__ "
        + json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False)
        + "\\n"
    )
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError(f"minicc_tools: no response for {tool} call (host disconnected)")
    prefix = "__MINICC_TOOL_RESULT__ "
    if not line.startswith(prefix):
        raise RuntimeError(f"minicc_tools: unexpected response line: {line!r}")
    result = json.loads(line[len(prefix):])
    if result.get("is_error"):
        raise RuntimeError(f"{tool} failed: {result.get('content')}")
    return result.get("content")


def read(path, offset=None, limit=None):
    arguments = {"path": path}
    if offset is not None:
        arguments["offset"] = offset
    if limit is not None:
        arguments["limit"] = limit
    return _call("read", **arguments)


def edit(path, old_string, new_string, expected_hash, replace_all=False):
    return _call(
        "edit",
        path=path,
        old_string=old_string,
        new_string=new_string,
        expected_hash=expected_hash,
        replace_all=replace_all,
    )


def write(path, content, expected_hash=None):
    arguments = {"path": path, "content": content}
    if expected_hash is not None:
        arguments["expected_hash"] = expected_hash
    return _call("write", **arguments)


def bash(command, timeout_sec=60, description=""):
    return _call("bash", command=command, timeout_sec=timeout_sec, description=description)
'''

_LAUNCHER_SOURCE = '''\
import base64
import os
import sys
import traceback

sys.path.insert(0, "/opt/minicc")
import minicc_tools  # noqa: E402

script_source = base64.b64decode(os.environ["MINICC_CODE_MODE_SCRIPT_B64"]).decode("utf-8")
globals_ns = {
    "__name__": "__minicc_code_mode__",
    "read": minicc_tools.read,
    "edit": minicc_tools.edit,
    "write": minicc_tools.write,
    "bash": minicc_tools.bash,
}
try:
    exec(compile(script_source, "<code_mode_script>", "exec"), globals_ns)
except Exception:
    traceback.print_exc()
    sys.exit(1)
sys.exit(0)
'''


@dataclass(frozen=True)
class CodeModeResult:
    is_error: bool
    timed_out: bool
    script_exit_code: int | None
    script_stdout: str
    tool_calls_made: tuple[dict[str, Any], ...]
    traceback_text: str = ""


def inject_facade(container_name: str) -> None:
    """Write the minicc_tools.py facade into a freshly-started container.

    Cheap (one small file write) and called once per container lifetime from
    ``DockerSandboxRunner.start()``; callers should swallow failures here so a
    facade injection problem degrades code_mode availability without blocking
    the rest of the run.
    """
    subprocess.run(
        ["docker", "exec", container_name, "mkdir", "-p", MINICC_TOOLS_DIR],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "-i", container_name, "sh", "-c", f"cat > {MINICC_TOOLS_PATH}"],
        input=MINICC_TOOLS_FACADE_SOURCE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )


def run_code_mode_script(
    *,
    container_name: str,
    script: str,
    timeout_sec: int,
    dispatch: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> CodeModeResult:
    script_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    command = [
        "docker",
        "exec",
        "-i",
        "-e",
        f"MINICC_CODE_MODE_SCRIPT_B64={script_b64}",
        "--workdir",
        "/workspace",
        container_name,
        "python3",
        "-c",
        _LAUNCHER_SOURCE,
    ]
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    timed_out = {"value": False}

    def _kill() -> None:
        timed_out["value"] = True
        proc.kill()

    watchdog = threading.Timer(timeout_sec, _kill)
    watchdog.daemon = True
    watchdog.start()

    tool_calls_made: list[dict[str, Any]] = []
    script_stdout_lines: list[str] = []
    try:
        assert proc.stdout is not None and proc.stdin is not None
        for line in proc.stdout:
            if line.startswith(_TOOL_CALL_PREFIX):
                tool = ""
                try:
                    payload = json.loads(line[len(_TOOL_CALL_PREFIX):])
                    tool = str(payload.get("tool", ""))
                    arguments = payload.get("arguments") or {}
                    result = dispatch(tool, arguments)
                except Exception as exc:
                    # A protocol/dispatch failure, not the script's own error —
                    # still fed back so the script (and its exec's exception
                    # handling) can react rather than deadlocking on a read.
                    result = {"is_error": True, "content": {"error": f"host dispatch failed: {exc}"}}
                tool_calls_made.append({"tool": tool, "result": result})
                try:
                    proc.stdin.write(_TOOL_RESULT_PREFIX + json.dumps(result, ensure_ascii=False) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    break
            else:
                script_stdout_lines.append(line.rstrip("\n"))
        proc.wait()
    finally:
        watchdog.cancel()

    stderr_text = proc.stderr.read() if proc.stderr else ""
    script_stdout = "\n".join(script_stdout_lines)
    if timed_out["value"]:
        return CodeModeResult(
            is_error=False,
            timed_out=True,
            script_exit_code=None,
            script_stdout=script_stdout,
            tool_calls_made=tuple(tool_calls_made),
            traceback_text=stderr_text,
        )
    exit_code = proc.returncode
    return CodeModeResult(
        is_error=exit_code != 0,
        timed_out=False,
        script_exit_code=exit_code,
        script_stdout=script_stdout,
        tool_calls_made=tuple(tool_calls_made),
        traceback_text=stderr_text if exit_code != 0 else "",
    )

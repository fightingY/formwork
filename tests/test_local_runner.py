from __future__ import annotations

from minicc.evals import assertions
from minicc.sandbox import local_runner


def test_windows_maven_commands_use_native_shell(monkeypatch) -> None:
    monkeypatch.setattr(local_runner.sys, "platform", "win32")

    assert local_runner._local_shell_args("mvn -q test") == [
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        "mvn -q test",
    ]


def test_maven_inside_bash_sequence_is_delegated_to_cmd(monkeypatch) -> None:
    monkeypatch.setattr(local_runner.sys, "platform", "win32")

    args = local_runner._local_shell_args("mkdir -p target && mvn -q test")
    assert args is not None
    assert args[-1] == "mkdir -p target && cmd.exe /c mvn -q test"


def test_windows_assertion_maven_commands_use_native_shell(monkeypatch) -> None:
    monkeypatch.setattr(assertions.sys, "platform", "win32")

    assert assertions._shell_args("mvn -q test") == [
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        "mvn -q test",
    ]


def test_windows_jdk_commands_use_native_shell(monkeypatch) -> None:
    monkeypatch.setattr(local_runner.sys, "platform", "win32")
    monkeypatch.setattr(assertions.sys, "platform", "win32")
    command = "javac -d target/classes src/Main.java && java -cp target/classes Main"

    assert local_runner._local_shell_args(command) == [
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        command,
    ]
    assert assertions._shell_args(command) == [
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        command,
    ]

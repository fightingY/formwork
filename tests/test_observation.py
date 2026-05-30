from minicc.core.state import RunState
from minicc.sandbox.artifact_store import ArtifactStore, preview_text
from minicc.sandbox.observation import CommandResult, observation_from_command_result


def test_no_output_observation() -> None:
    state = RunState.start("run true")

    observation = observation_from_command_result(CommandResult(exit_code=0), state=state)

    assert observation.kind == "no_output"
    assert observation.message == "Command exited successfully with no output."


def test_command_error_observation() -> None:
    state = RunState.start("fail")

    observation = observation_from_command_result(
        CommandResult(exit_code=2, stdout="out", stderr="err"),
        state=state,
    )

    assert observation.kind == "command_error"
    assert observation.exit_code == 2
    assert observation.stdout_preview == "out"
    assert observation.stderr_preview == "err"


def test_large_output_writes_artifact(tmp_path) -> None:
    state = RunState.start("large output")
    artifacts = ArtifactStore(tmp_path, preview_chars=20, display_path_prefix=".minicc_artifacts")

    observation = observation_from_command_result(
        CommandResult(exit_code=0, stdout="x" * 100),
        state=state,
        artifacts=artifacts,
        artifact_threshold_bytes=10,
        preview_chars=20,
    )

    assert observation.kind == "command_result"
    assert observation.artifact_ids == ["art_0001"]
    assert "full output saved" in observation.stdout_preview
    assert ".minicc_artifacts/stdout_0001.txt" in observation.stdout_preview
    assert state.artifacts[0]["id"] == "art_0001"
    assert state.artifacts[0]["path"] == ".minicc_artifacts/stdout_0001.txt"
    assert state.metrics["artifact_bytes"] == 100
    assert (tmp_path / "stdout_0001.txt").read_text(encoding="utf-8") == "x" * 100


def test_preview_text_keeps_short_content() -> None:
    assert preview_text("short", 10) == "short"

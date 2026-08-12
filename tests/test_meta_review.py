from __future__ import annotations

import hashlib
import json

import pytest

from minicc.core.provider import ModelResponse, ModelUsage
from minicc.meta.reviewer import MetaReviewError, MetaReviewer, load_meta_review, verify_review_source


class FakeProvider:
    def __init__(self, text: str | list[str], *, mutate=None) -> None:
        self.texts = [text] if isinstance(text, str) else list(text)
        self.mutate = mutate
        self.options = None
        self.messages = []

    def complete(self, messages, *, options=None) -> ModelResponse:
        self.options = options
        self.messages.append(messages)
        if self.mutate is not None:
            self.mutate()
        return ModelResponse(
            text=self.texts.pop(0) if len(self.texts) > 1 else self.texts[0],
            raw={},
            usage=ModelUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            latency_ms=9,
            attempt_count=2,
            retry_reasons=("timeout",),
        )


def _make_run(tmp_path):
    run_dir = tmp_path / "runs" / "run-1"
    (run_dir / "artifacts").mkdir(parents=True)
    payloads = {
        "state.json": {"status": "completed", "run_id": "run-1"},
        "metrics.json": {"status": "completed", "turns": 4, "command_failures": 0},
        "run_report.json": {"result": "PASS", "run_id": "run-1"},
    }
    for name, payload in payloads.items():
        (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "trace.jsonl").write_text('{"event":"run_completed"}\n', encoding="utf-8")
    (run_dir / "artifacts" / "diff.patch").write_text("+done\n", encoding="utf-8")
    return run_dir


def _model_json() -> str:
    return json.dumps(
        {
            "summary": "The run converged with one reusable verification opportunity.",
            "findings": [
                {
                    "id": "F1",
                    "severity": "low",
                    "area": "verification",
                    "message": "Retain the successful final verification pattern.",
                    "evidence_refs": ["run_report.result", "metrics.turns"],
                }
            ],
            "suggested_changes": [
                {
                    "id": "S1",
                    "finding_ids": ["F1"],
                    "change": "Measure repeated verification commands before changing prompts.",
                    "expected_effect": "Preserve successful verification behavior.",
                    "validation": "Compare fixed-case verification traces before and after the change.",
                }
            ],
        }
    )


def test_model_review_is_separate_and_source_verified(tmp_path) -> None:
    run_dir = _make_run(tmp_path)
    before = hashlib.sha256((run_dir / "trace.jsonl").read_bytes()).hexdigest()
    provider = FakeProvider(_model_json())

    result = MetaReviewer(
        provider, model="test-model", implementation_commit="commit-1"
    ).review_run(
        run_dir, output_root=tmp_path / "reviews"
    )

    assert result.used_model is True
    assert result.output_dir.parent == tmp_path / "reviews"
    assert {path.name for path in result.output_dir.iterdir()} == {
        "report.json",
        "report.md",
        "manifest.json",
    }
    assert hashlib.sha256((run_dir / "trace.jsonl").read_bytes()).hexdigest() == before
    assert verify_review_source(result.report) is True
    assert result.report["invocation"]["attempt_count"] == 2
    assert result.report["implementation_commit"] == "commit-1"
    assert result.report["schema_version"] == 2
    assert result.report["quality_audit"]["quality_gate_passed"] is True
    assert provider.options.json_mode is True
    assert load_meta_review(result.output_dir)["review_id"] == result.review_id


def test_offline_review_is_explicit_and_not_model_evidence(tmp_path) -> None:
    result = MetaReviewer().review_run(
        _make_run(tmp_path), output_root=tmp_path / "reviews", offline=True
    )

    assert result.used_model is False
    assert result.report["invocation"]["mode"] == "offline"


def test_invalid_model_output_writes_no_bundle(tmp_path) -> None:
    output_root = tmp_path / "reviews"
    with pytest.raises(MetaReviewError, match="valid JSON"):
        MetaReviewer(FakeProvider("not json")).review_run(
            _make_run(tmp_path), output_root=output_root
        )
    assert not output_root.exists()


def test_schema_validation_retries_with_correction_prompt(tmp_path) -> None:
    invalid = json.loads(_model_json())
    invalid["findings"][0]["evidence_refs"] = ["run_report.missing"]
    provider = FakeProvider([json.dumps(invalid), _model_json()])

    result = MetaReviewer(provider, max_schema_retries=2).review_run(
        _make_run(tmp_path), output_root=tmp_path / "reviews"
    )

    invocation = result.report["invocation"]
    assert invocation["model_call_count"] == 2
    assert invocation["schema_retry_count"] == 1
    assert invocation["attempt_count"] == 4
    assert invocation["usage"]["total_tokens"] == 240
    assert "failed validation" in provider.messages[1][-1]["content"]


def test_missing_evidence_and_unlinked_finding_are_rejected(tmp_path) -> None:
    missing = json.loads(_model_json())
    missing["findings"][0]["evidence_refs"] = ["run_report.missing"]
    with pytest.raises(MetaReviewError, match="does not exist"):
        MetaReviewer(FakeProvider(json.dumps(missing)), max_schema_retries=0).review_run(
            _make_run(tmp_path / "missing"), output_root=tmp_path / "reviews-a"
        )

    unlinked = json.loads(_model_json())
    unlinked["suggested_changes"][0]["finding_ids"] = ["F99"]
    with pytest.raises(MetaReviewError, match="unknown finding"):
        MetaReviewer(FakeProvider(json.dumps(unlinked)), max_schema_retries=0).review_run(
            _make_run(tmp_path / "unlinked"), output_root=tmp_path / "reviews-b"
        )


def test_nested_trace_evidence_reference_is_resolved(tmp_path) -> None:
    run_dir = _make_run(tmp_path)
    (run_dir / "trace.jsonl").write_text(
        '{"event":"action_parsed","action":{"type":"bash","command":"pytest"}}\n',
        encoding="utf-8",
    )
    payload = json.loads(_model_json())
    payload["findings"][0]["evidence_refs"] = ["trace_tail[0].action.command"]

    result = MetaReviewer(FakeProvider(json.dumps(payload))).review_run(
        run_dir, output_root=tmp_path / "reviews"
    )

    assert result.report["findings"][0]["evidence_refs"] == [
        "trace_tail[0].action.command"
    ]


def test_review_fails_closed_if_source_changes_during_call(tmp_path) -> None:
    run_dir = _make_run(tmp_path)

    def mutate() -> None:
        (run_dir / "metrics.json").write_text('{"status":"failed"}', encoding="utf-8")

    with pytest.raises(MetaReviewError, match="changed during review"):
        MetaReviewer(FakeProvider(_model_json(), mutate=mutate)).review_run(
            run_dir, output_root=tmp_path / "reviews"
        )


def test_manifest_tamper_is_rejected(tmp_path) -> None:
    result = MetaReviewer().review_run(
        _make_run(tmp_path), output_root=tmp_path / "reviews", offline=True
    )
    (result.output_dir / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(MetaReviewError):
        load_meta_review(result.output_dir)

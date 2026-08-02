from __future__ import annotations

import hashlib
import json

import pytest

from minicc.core.provider import ModelResponse, ModelUsage
from minicc.meta.reviewer import MetaReviewError, MetaReviewer, load_meta_review, verify_review_source


class FakeProvider:
    def __init__(self, text: str, *, mutate=None) -> None:
        self.text = text
        self.mutate = mutate
        self.options = None

    def complete(self, messages, *, options=None) -> ModelResponse:
        self.options = options
        if self.mutate is not None:
            self.mutate()
        return ModelResponse(
            text=self.text,
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
                    "severity": "low",
                    "area": "verification",
                    "message": "Retain the successful final verification pattern.",
                    "evidence_refs": ["run_report.result", "metrics.turns"],
                }
            ],
            "suggested_changes": ["Measure repeated verification commands before changing prompts."],
        }
    )


def test_model_review_is_separate_and_source_verified(tmp_path) -> None:
    run_dir = _make_run(tmp_path)
    before = hashlib.sha256((run_dir / "trace.jsonl").read_bytes()).hexdigest()
    provider = FakeProvider(_model_json())

    result = MetaReviewer(provider, model="test-model").review_run(
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

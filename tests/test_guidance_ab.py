from minicc.evals.guidance_ab import build_guidance_ab_report, write_guidance_ab_report


def _suite(variant: str, *, suite_id: str, run_prefix: str) -> dict:
    skills = [] if variant == "a0" else ["release-manifest"]
    rules = [] if variant == "a0" else ["release-legacy-id"]
    return {
        "suite_id": suite_id,
        "_evidence_integrity_verified": True,
        "configuration": {
            "git_commit": "abc123",
            "model": "test-model",
            "temperature": 0.0,
            "stream": False,
            "sandbox_mode": "locked",
            "execute_local": False,
            "json_mode": True,
            "provider_max_retries": 2,
            "provider_timeout_sec": 300,
            "docker_image": "python@sha256:test",
            "case_authority_bundle_sha256": "case-sha",
            "guidance_variant": variant,
            "guidance_sequence_id": "round-1",
            "guidance_execution_order": "a0-first",
            "guidance_feedback_path": "guidance/feedback_rules.jsonl",
        },
        "cases": [
            {
                "name": "G01_release_manifest_guidance",
                "run_id": f"{run_prefix}-{index}",
                "passed": True,
                "metrics": {
                    "guidance_skill_names": skills,
                    "guidance_feedback_rule_ids": rules,
                    "guidance_selection_events": 1,
                    "provider_errors": 0,
                    "protocol_errors": 0,
                    "bash_actions": 4 if variant == "a0" else 2,
                    "prompt_tokens": 140 if variant == "a0" else 100,
                },
            }
            for index in range(1, 4)
        ],
    }


def test_guidance_ab_requires_exact_selection_and_non_regression(tmp_path) -> None:
    report = build_guidance_ab_report(
        _suite("a0", suite_id="suite-a0", run_prefix="a0"),
        _suite("a1", suite_id="suite-a1", run_prefix="a1"),
        source_commit="abc123",
        verification_commit="def456",
        execution_verification_changed_paths=["README.md"],
        allowed_verification_paths=["README.md"],
    )

    assert report["passed"] is True
    assert report["milestone"] == "stable-v3.2"
    assert report["verification_commit"] == "def456"
    assert report["criteria"]["enabled_selects_exact_relevant_guidance"] is True
    bundle = write_guidance_ab_report(report, tmp_path / "acceptance")
    assert set(bundle) == {"report.json", "report.md", "report.csv", "manifest.json"}


def test_guidance_ab_rejects_distractor_selection() -> None:
    enabled = _suite("a1", suite_id="suite-a1", run_prefix="a1")
    enabled["cases"][0]["metrics"]["guidance_skill_names"].append("database-migration")

    report = build_guidance_ab_report(
        _suite("a0", suite_id="suite-a0", run_prefix="a0"),
        enabled,
        source_commit="abc123",
    )

    assert report["passed"] is False
    assert report["criteria"]["enabled_selects_exact_relevant_guidance"] is False


def test_guidance_ab_rejects_unexpected_verification_delta() -> None:
    report = build_guidance_ab_report(
        _suite("a0", suite_id="suite-a0", run_prefix="a0"),
        _suite("a1", suite_id="suite-a1", run_prefix="a1"),
        source_commit="abc123",
        verification_commit="def456",
        execution_verification_changed_paths=["src/minicc/core/loop.py"],
        allowed_verification_paths=["README.md"],
    )

    assert report["passed"] is False
    assert report["criteria"]["verification_delta_allowed"] is False

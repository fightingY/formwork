from minicc.evals.compaction_ab import build_compaction_ab_report, write_compaction_ab_report


def test_compaction_ab_requires_two_successful_same_direction_rounds(tmp_path) -> None:
    pairs = [
        (_suite("a0", prompt_total=1000, suffix="r1"), _suite("a1", prompt_total=700, suffix="r1")),
        (_suite("a0", prompt_total=900, suffix="r2"), _suite("a1", prompt_total=600, suffix="r2")),
    ]

    report = build_compaction_ab_report(pairs)
    bundle = write_compaction_ab_report(report, tmp_path / "report")

    assert report["status"] == "PASS"
    assert report["passed"] is True
    assert report["same_direction"] is True
    assert report["rounds"][0]["a0"]["cache"]["status"] == "unsupported"
    assert bundle.json_path.exists()
    assert "Independent rounds: 2/2" in bundle.markdown_path.read_text(encoding="utf-8")


def test_compaction_ab_one_good_round_is_inconclusive() -> None:
    report = build_compaction_ab_report(
        [(_suite("a0", prompt_total=1000), _suite("a1", prompt_total=700))]
    )

    assert report["status"] == "INCONCLUSIVE"
    assert report["passed"] is False
    assert report["rounds"][0]["passed"] is True


def test_compaction_ab_rejects_untriggered_a1_semantic_compaction() -> None:
    a1 = _suite("a1", prompt_total=700)
    a1["cases"][0]["metrics"]["semantic_compaction_successes"] = 0

    report = build_compaction_ab_report(
        [(_suite("a0", prompt_total=1000), a1)],
    )

    assert report["status"] == "FAIL"
    assert report["rounds"][0]["criteria"]["a1_semantic_compaction_triggered_in_every_run"] is False


def _suite(variant: str, *, prompt_total: int, suffix: str = "r1") -> dict:
    semantic = variant == "a1"
    return {
        "suite_id": f"suite-{variant}-{suffix}",
        "configuration": {
            "context_variant": variant,
            "compaction_strategy": "semantic" if semantic else "disabled",
            "model": "fixed",
            "temperature": 0,
            "git_commit": "abc123",
        },
        "cases": [
            {
                "name": "C02",
                "attempt": 1,
                "passed": True,
                "metrics": {
                    "context_compactions": 1,
                    "context_budget_triggered": True,
                    "context_compaction_strategy": "semantic" if semantic else "disabled",
                    "semantic_compaction_successes": 1 if semantic else 0,
                    "semantic_compaction_failures": 0,
                    "prompt_char_samples": 2,
                    "prompt_chars_total": prompt_total,
                    "prompt_chars_max": prompt_total // 2 + 50,
                    "context_retention_expected": 4,
                    "context_retention_retained": 4,
                    "repeated_file_reads": 0,
                    "repeated_searches": 0,
                    "cache_metrics_available": False,
                },
            }
        ],
    }

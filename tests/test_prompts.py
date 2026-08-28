"""Deterministic assertions for the model-facing prompt contract."""

from minicc.prompts.agent import STABLE_PREFIX


def test_stable_prefix_instructs_honest_final_self_report() -> None:
    # dsh wrapup.ts GROUNDING 的对应物：final 只报会话实际证实的、不瞎编结果。
    normalized = " ".join(STABLE_PREFIX.split())
    assert (
        "`answer` only what your tool calls and their results actually established"
        in normalized
    )
    assert "do not invent results or steps the session does not show" in normalized
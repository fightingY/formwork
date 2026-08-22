"""Deterministic assertions for the model-facing prompt contract."""

from minicc.prompts.agent import STABLE_PREFIX


def test_stable_prefix_instructs_honest_final_self_report() -> None:
    # dsh wrapup.ts GROUNDING 的对应物：final 只报会话实际证实的、不瞎编结果。
    assert (
        "`answer` only what your bash actions and observations actually established"
        in STABLE_PREFIX
    )
    assert "do not invent results or steps the session does" in STABLE_PREFIX
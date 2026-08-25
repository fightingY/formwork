from minicc.core.provider import ModelUsage
from minicc.core.runner import _accumulate_usage
from minicc.core.state import RunState


def test_accumulate_usage_separates_full_chain_steady_state_and_capture_efficiency() -> None:
    state = RunState.start("Inspect")
    state.metrics["cache_prefix_local_cold_start"] = True
    state.metrics["cache_prefix_previous_is_exact"] = False

    _accumulate_usage(
        state,
        ModelUsage(
            prompt_tokens=500,
            completion_tokens=100,
            cache_hit_tokens=0,
            cache_miss_tokens=500,
        ),
        10,
    )

    state.metrics["cache_prefix_local_cold_start"] = False
    state.metrics["cache_prefix_previous_is_exact"] = True
    _accumulate_usage(
        state,
        ModelUsage(
            prompt_tokens=800,
            completion_tokens=50,
            cache_hit_tokens=400,
            cache_miss_tokens=400,
        ),
        10,
    )

    assert state.metrics["cache_hit_rate"] == 400 / 1300
    assert state.metrics["cache_steady_state_hit_rate"] == 0.5
    assert state.metrics["cache_theoretical_input_tokens"] == 500
    assert state.metrics["cache_theoretical_output_tokens"] == 600
    assert state.metrics["cache_capture_efficiency_input"] == 0.8
    assert state.metrics["cache_capture_efficiency_output"] == 400 / 600
    assert state.metrics["cache_empirical_hit_block_tokens"] == 400


def test_capture_efficiency_is_bounded_when_provider_hits_include_output_boundary() -> None:
    state = RunState.start("Inspect")
    state.metrics["cache_prefix_local_cold_start"] = True
    state.metrics["cache_prefix_previous_is_exact"] = False
    _accumulate_usage(
        state,
        ModelUsage(prompt_tokens=100, completion_tokens=80, cache_hit_tokens=0, cache_miss_tokens=100),
        1,
    )
    state.metrics["cache_prefix_local_cold_start"] = False
    state.metrics["cache_prefix_previous_is_exact"] = True

    _accumulate_usage(
        state,
        ModelUsage(prompt_tokens=200, completion_tokens=10, cache_hit_tokens=150, cache_miss_tokens=50),
        1,
    )

    assert state.metrics["cache_theoretical_input_tokens"] == 100
    assert state.metrics["cache_capture_observed_hit_tokens"] == 100
    assert state.metrics["cache_capture_efficiency_input"] == 1.0


def test_steady_state_starts_at_first_observed_hit_and_keeps_later_misses() -> None:
    state = RunState.start("Inspect")
    state.metrics["cache_prefix_local_cold_start"] = True
    state.metrics["cache_prefix_previous_is_exact"] = False
    state.metrics["cache_prefix_request_index"] = 1
    _accumulate_usage(
        state,
        ModelUsage(
            prompt_tokens=500,
            completion_tokens=10,
            cache_hit_tokens=0,
            cache_miss_tokens=500,
        ),
        1,
    )

    state.metrics["cache_prefix_local_cold_start"] = False
    state.metrics["cache_prefix_previous_is_exact"] = True
    state.metrics["cache_prefix_request_index"] = 2
    _accumulate_usage(
        state,
        ModelUsage(
            prompt_tokens=700,
            completion_tokens=10,
            cache_hit_tokens=0,
            cache_miss_tokens=700,
        ),
        1,
    )

    state.metrics["cache_prefix_request_index"] = 3
    _accumulate_usage(
        state,
        ModelUsage(
            prompt_tokens=900,
            completion_tokens=10,
            cache_hit_tokens=600,
            cache_miss_tokens=300,
        ),
        1,
    )
    state.metrics["cache_prefix_request_index"] = 4
    _accumulate_usage(
        state,
        ModelUsage(
            prompt_tokens=1_000,
            completion_tokens=10,
            cache_hit_tokens=0,
            cache_miss_tokens=1_000,
        ),
        1,
    )

    assert state.metrics["cache_steady_state_start_request_index"] == 3
    assert state.metrics["cache_steady_state_request_count"] == 2
    assert state.metrics["cache_steady_state_prompt_tokens"] == 1_900
    assert state.metrics["cache_steady_state_hit_tokens"] == 600
    assert state.metrics["cache_steady_state_hit_rate"] == 600 / 1_900

from minicc.core.provider import extract_chat_text, parse_model_usage


def test_parse_deepseek_style_cache_usage() -> None:
    usage = parse_model_usage(
        {
            "prompt_tokens": 48000,
            "completion_tokens": 900,
            "total_tokens": 48900,
            "prompt_cache_hit_tokens": 42000,
            "prompt_cache_miss_tokens": 6000,
        }
    )

    assert usage.prompt_tokens == 48000
    assert usage.completion_tokens == 900
    assert usage.cache_hit_tokens == 42000
    assert usage.cache_miss_tokens == 6000
    assert usage.cached_tokens == 42000
    assert usage.cache_hit_rate == 0.875


def test_parse_openai_style_cached_tokens() -> None:
    usage = parse_model_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 250},
        }
    )

    assert usage.cached_tokens == 250
    assert usage.cache_hit_rate == 0.25


def test_extract_chat_text_from_openai_compatible_response() -> None:
    text = extract_chat_text(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"type":"final","answer":"done"}',
                    }
                }
            ]
        }
    )

    assert text == '{"type":"final","answer":"done"}'

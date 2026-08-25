from types import SimpleNamespace

from evaluations.token_usage.accounting import UsageTotals


def test_add_response_records_provider_reported_usage():
    usage = UsageTotals()

    usage.add_response(
        SimpleNamespace(
            prompt_tokens=100,
            response_tokens=20,
            cache_read_tokens=60,
            cache_write_tokens=10,
        )
    )

    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.cache_read_tokens == 60
    assert usage.cache_write_tokens == 10
    assert usage.model_requests == 1


def test_usage_accumulates_across_model_turns():
    usage = UsageTotals()

    usage.add_response(
        SimpleNamespace(
            prompt_tokens=100,
            response_tokens=20,
            cache_read_tokens=60,
        )
    )
    usage.add_response(
        SimpleNamespace(
            prompt_tokens=150,
            response_tokens=30,
            cache_read_tokens=100,
        )
    )

    assert usage.input_tokens == 250
    assert usage.output_tokens == 50
    assert usage.cache_read_tokens == 160
    assert usage.model_requests == 2


def test_total_tokens_does_not_double_count_cached_input():
    usage = UsageTotals(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=60,
    )

    assert usage.total_tokens == 120


def test_missing_optional_cache_fields_default_to_zero():
    usage = UsageTotals()

    usage.add_response(
        SimpleNamespace(
            prompt_tokens=40,
            response_tokens=5,
        )
    )

    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0


def test_none_token_values_are_treated_as_zero():
    usage = UsageTotals()

    usage.add_response(
        SimpleNamespace(
            prompt_tokens=None,
            response_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
        )
    )

    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.model_requests == 1

from collections import Counter

from evaluations.token_usage.matrix import build_run_plan


def test_full_plan_contains_90_runs():
    plan = build_run_plan(repetitions=10, seed=20260822)

    assert len(plan) == 90


def test_every_condition_and_size_combination_repeats_ten_times():
    plan = build_run_plan(repetitions=10, seed=20260822)

    counts = Counter(
        (run.condition, run.cluster_nodes)
        for run in plan
    )

    assert len(counts) == 9
    assert set(counts.values()) == {10}


def test_each_repetition_contains_all_nine_combinations():
    plan = build_run_plan(repetitions=10, seed=20260822)

    for repetition in range(1, 11):
        block = [
            run
            for run in plan
            if run.repetition == repetition
        ]

        assert len(block) == 9
        assert len(
            {
                (run.condition, run.cluster_nodes)
                for run in block
            }
        ) == 9


def test_same_seed_produces_same_order():
    first = build_run_plan(repetitions=10, seed=20260822)
    second = build_run_plan(repetitions=10, seed=20260822)

    assert first == second


def test_different_seed_changes_order():
    first = build_run_plan(repetitions=10, seed=20260822)
    second = build_run_plan(repetitions=10, seed=20260823)

    assert first != second


def test_run_ids_are_unique_and_sequential():
    plan = build_run_plan(repetitions=10, seed=20260822)

    assert [run.run_id for run in plan] == [
        f"run-{number:03d}"
        for number in range(1, 91)
    ]

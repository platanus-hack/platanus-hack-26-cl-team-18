"""Request observations survive offload, stay isolated, and preserve failures."""

import anyio
import pytest

from app.modules.common.infrastructure.request_performance import (
    RequestStage,
    observe_request,
    record_stage,
    run_auth_sync,
    run_dependency_sync,
    timed_stage,
)


def test_stage_records_failure_and_resets_context():
    @timed_stage(RequestStage.HYDRATION)
    def fail():
        raise LookupError("expected")

    with observe_request() as observation:
        with pytest.raises(LookupError, match="expected"):
            fail()
        assert observation.fields()["stage_calls"] == {"hydration": 1}
    with pytest.raises(LookupError):
        fail()
    assert observation.fields()["stage_calls"] == {"hydration": 1}


def test_parallel_requests_and_worker_threads_have_isolated_observations():
    async def run():
        results = []

        async def request(stage, count):
            with observe_request() as observation:
                for _ in range(count):
                    await run_auth_sync(lambda: record_stage(stage, 0.001))
                results.append(observation.fields())

        async with anyio.create_task_group() as group:
            group.start_soon(request, RequestStage.DB_EXECUTE, 2)
            group.start_soon(request, RequestStage.DB_FETCH, 3)
        return results

    results = anyio.run(run)
    counts = [result["stage_calls"] for result in results]
    assert {"auth_thread_wait": 2, "db_execute": 2} in counts
    assert {"auth_thread_wait": 3, "db_fetch": 3} in counts
    assert all(set(result) == {"stage_ms", "stage_calls"} for result in results)


def test_handler_observation_separates_admission_execution_and_response():
    from app.modules.common.infrastructure.request_performance import (
        finish_stage,
        mark_stage,
        observed_handler,
    )

    @observed_handler
    def handler(value):
        return value

    with observe_request() as observation:
        mark_stage(RequestStage.HANDLER_ADMISSION)
        assert handler(42) == 42
        finish_stage(RequestStage.RESPONSE_PREPARATION)
    assert observation.fields()["stage_calls"] == {
        "handler_admission": 1,
        "handler_execution": 1,
        "response_preparation": 1,
    }


def test_dependency_worker_observation_preserves_failure_and_context():
    async def run():
        def dependency():
            record_stage(RequestStage.HYDRATION, 0.001)
            raise LookupError("dependency failed")

        with observe_request() as observation:
            with pytest.raises(LookupError, match="dependency failed"):
                await run_dependency_sync(dependency)
        return observation.fields()

    assert anyio.run(run)["stage_calls"] == {
        "dependency_thread_wait": 1,
        "hydration": 1,
    }

from backend.app.entity_service import (
    ENTITY_REFRESH_KIND_RANK,
    entity_refresh_job_pause_seconds,
    entity_refresh_worker_count,
)


def test_avatar_jobs_run_before_profile_jobs() -> None:
    assert ENTITY_REFRESH_KIND_RANK["photo"] < ENTITY_REFRESH_KIND_RANK["profile"]


def test_entity_refresh_worker_count_has_safe_bounds() -> None:
    assert entity_refresh_worker_count(0) == 1
    assert entity_refresh_worker_count(3) == 3
    assert entity_refresh_worker_count(20) == 8


def test_entity_refresh_jobs_always_yield_a_real_time_slice() -> None:
    assert entity_refresh_job_pause_seconds(0) == 0.01
    assert entity_refresh_job_pause_seconds(0.08) == 0.08

"""The hermetic plugin clears every variable AppSettings reads (tests/_hermetic_env.py)."""

from __future__ import annotations

import os

import pytest

from tests._hermetic_env import OPT_IN_VAR, _selects, settings_env_names


def test_every_setting_and_alias_is_covered() -> None:
    names = settings_env_names()
    expected = {"DATABASE_URL", "APP_ENV", "SCREEN_TOKEN", "AGENT_TOKEN", "CONSOLE_TOKEN"}
    assert expected | {"DEMO_KEY", "FAL_KEY", "PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL"} <= names


@pytest.mark.skipif(bool(os.environ.get(OPT_IN_VAR)), reason="the run opted into .env")
def test_the_run_sees_none_of_them_except_the_pinned_catalog() -> None:
    leaked = {key for key in os.environ if key.upper() in settings_env_names()}
    assert leaked <= {"DEMO_CATALOG"}


def test_marker_selection() -> None:
    assert _selects("integration", "integration")
    assert not _selects("not integration and not live", "integration")
    assert _selects("live", "live") and not _selects("not live and not sandbox", "live")
    assert not _selects("liveness", "live")

"""Pytest plugin: keep the suite hermetic from the developer's `.env`.

`AppSettings` reads `.env` (pydantic-settings `env_file`), so a real station `.env`
(DEMO_CATALOG=chile, DEMO_KEY, device tokens, API keys...) silently changes what the
tests build and dozens of them fail. Loaded early via `-p tests._hermetic_env` in
`[tool.pytest.ini_options].addopts`, before any test module imports the settings.

Opt out with `PYTEST_USE_DOTENV=1`, or by selecting live tests (`-m live`), which read
their real provider keys from `.env` (`make test-live` sets the variable too).
"""

from __future__ import annotations

import os

import pytest

OPT_IN_VAR = "PYTEST_USE_DOTENV"
# Values the suite assumes even when the shell exports a station's settings.
PINNED_ENV = {"DEMO_CATALOG": "fictional"}


def _selects_live(markexpr: str) -> bool:
    expr = " ".join(markexpr.split())
    return "live" in expr and "not live" not in expr


def _use_dotenv(config: pytest.Config) -> bool:
    if os.environ.get(OPT_IN_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return _selects_live(str(config.getoption("markexpr", "") or ""))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if _use_dotenv(config):
        return
    from app.config.settings import AppSettings

    # pydantic-settings reads model_config["env_file"] on every instantiation, so
    # this disables `.env` for every AppSettings() the tests (and the app) build.
    AppSettings.model_config["env_file"] = None
    os.environ.update(PINNED_ENV)

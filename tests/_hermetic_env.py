"""Pytest plugin: keep the suite hermetic from the developer's `.env` and shell.

`AppSettings` reads `.env` (pydantic-settings `env_file`) and the process environment,
so a real station `.env` (DEMO_CATALOG=chile, DEMO_KEY, device tokens, API keys...)
or the same values exported in the shell silently change what the tests build and
dozens of them fail. Loaded early via `-p tests._hermetic_env` in
`[tool.pytest.ini_options].addopts` (and explicitly by `make test-sandbox`, which
overrides addopts), before any test module imports the settings.

Every environment variable `AppSettings` reads (its field names and aliases, derived
from the model so a new setting is covered) is removed for the run. Integration
runs (`-m integration`) keep the shell's variables: they need its DATABASE_URL.

Opt out with `PYTEST_USE_DOTENV=1`, or by selecting live tests (`-m live`), which read
their real provider keys from `.env` (`make test-live` sets the variable too).
"""

from __future__ import annotations

import os
import re

import pytest

OPT_IN_VAR = "PYTEST_USE_DOTENV"
# Values the suite assumes even when the shell exports a station's settings.
PINNED_ENV = {"DEMO_CATALOG": "fictional"}


def _selects(markexpr: str, marker: str) -> bool:
    """`-m` asks for `marker` (not just excludes it)."""
    expr = " ".join(markexpr.split())
    mentioned = re.search(rf"\b{marker}\b", expr) is not None
    return mentioned and re.search(rf"\bnot {marker}\b", expr) is None


def _selects_live(markexpr: str) -> bool:
    return _selects(markexpr, "live")


def _use_dotenv(config: pytest.Config) -> bool:
    if os.environ.get(OPT_IN_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return _selects_live(str(config.getoption("markexpr", "") or ""))


def settings_env_names() -> set[str]:
    """Upper-cased names of every environment variable AppSettings reads."""
    from pydantic import AliasChoices

    from app.config.settings import AppSettings

    names = set()
    for name, field in AppSettings.model_fields.items():
        names.add(name.upper())
        alias = field.validation_alias
        if isinstance(alias, str):
            names.add(alias.upper())
        elif isinstance(alias, AliasChoices):
            names.update(choice.upper() for choice in alias.choices if isinstance(choice, str))
        if field.alias:
            names.add(field.alias.upper())
    return names


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if _use_dotenv(config):
        return
    from app.config.settings import AppSettings

    # pydantic-settings reads model_config["env_file"] on every instantiation, so
    # this disables `.env` for every AppSettings() the tests (and the app) build.
    AppSettings.model_config["env_file"] = None
    if not _selects(str(config.getoption("markexpr", "") or ""), "integration"):
        # Env names match case-insensitively (pydantic-settings' default).
        read = settings_env_names()
        for key in [key for key in os.environ if key.upper() in read]:
            del os.environ[key]
    os.environ.update(PINNED_ENV)

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from app.modules.creative.application.ports.voice import VoiceFailure
from app.modules.creative.application.voiceover import VoiceoverService, VoiceProfile
from app.modules.creative.infrastructure.character_budget import FileCharacterBudget
from app.modules.creative.infrastructure.media_store import FileMediaStore
from tests.unit.creative.test_voiceover import FakeSynth

PROFILE = VoiceProfile(
    model_id="eleven_flash_v2_5", language_code="es", output_format="mp3_44100_128"
)
VOICE = "JBFqnCBsd6RMkjVDRZzb"


def test_budget_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    FileCharacterBudget(path, limit=100).spend(30)
    again = FileCharacterBudget(path, limit=100)  # a restart
    assert again.spent() == 30 and again.remaining() == 70
    again.spend(80)
    assert again.remaining() == 0  # never negative


def test_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    path.write_text("{not json")
    assert FileCharacterBudget(path, limit=100).remaining() == 0


def test_rejects_negative_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FileCharacterBudget(tmp_path / "b.json", limit=-1)
    with pytest.raises(ValueError):
        FileCharacterBudget(tmp_path / "b.json", limit=10).spend(-5)


def test_service_refuses_lines_over_budget_and_charges_the_provider_cost(tmp_path: Path) -> None:
    budget = FileCharacterBudget(tmp_path / "budget.json", limit=20)
    synth = FakeSynth()
    svc = VoiceoverService(synth, FileMediaStore(tmp_path / "media"), PROFILE, budget)
    assert anyio.run(svc.voiceover_url, "Hola mundo.", VOICE) is not None  # 11 chars
    assert budget.spent() == 11  # FakeSynth reports character_cost = len(text)
    assert anyio.run(svc.voiceover_url, "Una frase demasiado larga.", VOICE) is None
    assert svc.stats.disabled_by is VoiceFailure.BUDGET
    assert len(synth.requests) == 1  # the refused line never reached the provider
    # A cached line still plays after the budget closes.
    assert anyio.run(svc.voiceover_url, "Hola mundo.", VOICE) is not None

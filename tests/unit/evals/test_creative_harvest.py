"""The creative feedback loop: a rejected live draft becomes a reviewable eval candidate."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
from evals.creative import runner

from app.modules.creative.application.ports.copywriter import WrittenCopy
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft, CopySource


class CompetitorNamingWriter:
    """A misbehaving model: every draft names a competitor (guard must reject it)."""

    name = "fake-llm"

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        return WrittenCopy(
            draft=CopyDraft(
                teaser="Mejor que Red Bull",
                offer=brief.offer.text if brief.offer else "Pruébalo",
                close="Escanea el QR",
                voice_line="Mejor que Red Bull",
                source=CopySource.AI,
            )
        )


def test_rejected_drafts_are_harvested_and_deduplicated(tmp_path: Path) -> None:
    report = anyio.run(lambda: runner.run("claude", writer=CompetitorNamingWriter(), harvest=True))
    assert report.harvested, "the guard rejected nothing, or nothing was harvested"
    first = report.harvested[0]
    assert first["outcome"] == "rejected"
    assert "competitor_name" in first["violations"]
    assert first["teaser"] == "Mejor que Red Bull"
    assert all(c.aired_violations == [] for c in report.cases)  # nothing bad aired

    out = tmp_path / "harvested.json"
    new = runner.save_harvest(report, out)
    assert new == len({(h["case"], h["teaser"], h["offer"], h["close"]) for h in report.harvested})
    assert runner.save_harvest(report, out) == 0  # a second run adds no duplicates
    entries = json.loads(out.read_text(encoding="utf-8"))["entries"]
    assert all(e["reviewed"] is False and e["harvested_at"] for e in entries)


def test_harvest_is_off_by_default() -> None:
    report = anyio.run(lambda: runner.run("claude", writer=CompetitorNamingWriter()))
    assert report.harvested == []

"""Pre-generated video loops per bundle (architecture §8: 95 % of demo video).

The hot path only looks a loop up (a file check); generation is a capped cold job.
Loops carry no logos, text or prices: those are player overlays (ADR 0003).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.modules.common.application.idempotency import compute_request_hash
from app.modules.creative.application.ports.video import VideoGenerationError, VideoGenerator

LOOP_DIR = "loops"
MOTION_BY_CATEGORY = {
    "sports_goods": "an ice-cold energy can sweating condensation, light sweeping over it",
    "energy_drink": "an ice-cold energy can sweating condensation, light sweeping over it",
    "coffee": "steam curling slowly from a fresh cup of coffee",
    "pastry": "a flaky pastry with crumbs falling in slow motion",
    "alcohol": "golden beer bubbles rising slowly in a glass",
    "burger": "a juicy burger with melting cheese, slow push-in",
    "cola": "dark cola fizzing over ice cubes",
    "snack": "crunchy chips tumbling in slow motion",
    "snacks": "crunchy chips tumbling in slow motion",
    "candy": "a chocolate wafer bar snapping in half, crumbs in slow motion",
    "water": "clear water splashing into a bottle, crisp light",
}


def bundle_key(slugs: Sequence[str]) -> str:
    return "+".join(sorted(slugs))


def loop_prompt(categories: Sequence[str]) -> str:
    scenes = [
        MOTION_BY_CATEGORY.get(category, "products on a clean counter") for category in categories
    ]
    return (
        "Seamless 6-second loop, cinematic macro product shot, warm convenience-store light: "
        + " and ".join(scenes)
        + ". No text, no letters, no logos, no labels, no people. Camera moves slowly; "
        "the last frame matches the first."
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class LoopJob:
    slugs: tuple[str, ...]
    categories: tuple[str, ...]

    @property
    def key(self) -> str:
        return bundle_key(self.slugs)


@dataclass(frozen=True, slots=True, kw_only=True)
class LoopReport:
    generated: tuple[str, ...]
    cached: tuple[str, ...]
    skipped_budget: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]
    spent_usd: Decimal


class BundleLoopLibrary:
    def __init__(self, media_dir: Path, *, url_prefix: str = "/media") -> None:
        self._dir = media_dir / LOOP_DIR
        self._prefix = url_prefix.rstrip("/")

    def path_for(self, slugs: Sequence[str]) -> Path:
        return self._dir / f"{bundle_key(slugs)}.mp4"

    def url_for(self, slugs: Sequence[str]) -> str | None:
        """The bundle's loop; for a basket without one, the lead brand's solo loop; else None."""
        for candidate in (tuple(slugs), tuple(slugs[:1])):
            if candidate and self.path_for(candidate).is_file():
                return f"{self._prefix}/{LOOP_DIR}/{bundle_key(candidate)}.mp4"
        return None

    async def generate_missing(
        self,
        generator: VideoGenerator,
        jobs: Sequence[LoopJob],
        *,
        cost_per_clip_usd: Decimal,
        budget_usd: Decimal,
        duration_s: int = 6,
        record_attempts: bool = False,
    ) -> LoopReport:
        """Generate loops that don't exist yet, never spending past `budget_usd`."""
        self._dir.mkdir(parents=True, exist_ok=True)
        generated, cached, skipped, failed = [], [], [], []
        spent = Decimal(0)
        for job in jobs:
            path = self.path_for(job.slugs)
            if path.is_file():
                cached.append(job.key)
                continue
            if spent + cost_per_clip_usd > budget_usd:
                skipped.append(job.key)
                continue
            prompt = loop_prompt(job.categories)
            if record_attempts:
                # A timed-out paid request may still complete. Leave its intent
                # behind and require reconciliation before another submission.
                try:
                    with path.with_suffix(".attempt.json").open("x") as intent:
                        request = {
                            "model": generator.model,
                            "prompt": prompt,
                            "duration_s": duration_s,
                        }
                        json.dump(
                            {
                                "key": job.key,
                                "request": request,
                                "request_hash": compute_request_hash(request),
                            },
                            intent,
                        )
                except FileExistsError:
                    failed.append((job.key, "reconciliation_required"))
                    continue
            spent += cost_per_clip_usd  # count before the call: a timeout may still bill
            try:
                clip = await generator.generate(prompt, duration_s=duration_s)
            except VideoGenerationError as exc:
                failed.append((job.key, exc.failure.value))
                continue
            if clip[4:8] != b"ftyp":
                failed.append((job.key, "invalid_output"))
                continue
            tmp = path.with_suffix(".mp4.part")
            tmp.write_bytes(clip)
            os.replace(tmp, path)  # atomic: the hot path never sees half a file
            path.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "model_version": f"{generator.name}:{generator.model}",
                        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                        "slugs": list(job.slugs),
                    }
                )
            )
            generated.append(job.key)
        return LoopReport(
            generated=tuple(generated),
            cached=tuple(cached),
            skipped_budget=tuple(skipped),
            failed=tuple(failed),
            spent_usd=spent,
        )

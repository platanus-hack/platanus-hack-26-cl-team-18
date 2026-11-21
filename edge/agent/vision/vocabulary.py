"""The object vocabulary for YOLOE-26 (open-vocabulary YOLO26, docs/stack.md §3).

YOLOE detects whatever we name in text. Each entry is a **prompt** (what the
model is asked to find) and the **canonical label** that goes on the wire.
Several prompts may share one label ("coffee cup", "paper cup" and plain
"cup" are all `cup`): the specific phrase helps on the demo props, the generic
noun on everything else. Canonical labels keep the COCO names the backend
catalog already uses (`cup`, `bottle`, `laptop`…), so cue rules in
`app/modules/audience/infrastructure/catalog` keep working, and add the demo's
convenience-store categories COCO lacks (`energy drink can`, `beer bottle`,
`pastry`, `stroller`).

Objects only (CLAUDE.md prohibitions): never add a prompt that describes a
person (clothing, body, age, emotion). Keep the list short: every extra class
is another chance of a false positive.

**Bake once, then no text encoder.** Turning prompts into class embeddings
needs MobileCLIP (a ~250 MB download plus the `clip` package). `load_object_model`
does that once and saves the prompted weights next to the base weights as
`<stem>-vocab-<fingerprint>.pt`; later starts load those in milliseconds and
never touch the text encoder. Editing the vocabulary changes the fingerprint,
so stale weights are never reused. `make edge-yoloe` (repo root) bakes ahead
of the demo, while the venue network is still irrelevant.

`gpu/service/vocabulary.py` is a verbatim copy so remote and local detections
use the same classes; `edge/tests/test_vocabulary.py` fails when they drift.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OBJECT_VOCABULARY: tuple[tuple[str, str], ...] = (
    # Coffee (Café Patio's category).
    ("coffee cup", "cup"),
    ("paper cup", "cup"),
    ("cup", "cup"),
    ("mug", "cup"),
    # Energy drinks (Voltio, Rayo) and other cans.
    ("energy drink can", "energy drink can"),
    ("soda can", "soda can"),
    # Water / gym bottles.
    ("water bottle", "bottle"),
    ("sports bottle", "bottle"),
    ("bottle", "bottle"),
    # Alcohol (Malta Sur; age-restricted, the safety beat blocks it).
    ("beer bottle", "beer bottle"),
    ("beer can", "beer bottle"),
    ("wine glass", "wine glass"),
    # Food at the counter.
    ("croissant", "pastry"),
    ("donut", "donut"),
    ("sandwich", "sandwich"),
    ("hot dog", "hot dog"),
    ("pizza slice", "pizza"),
    # Context objects the catalog maps to interests.
    ("backpack", "backpack"),
    ("handbag", "handbag"),
    ("laptop", "laptop"),
    ("smartphone", "cell phone"),
    ("dog", "dog"),
    ("skateboard", "skateboard"),
    ("soccer ball", "sports ball"),
    ("bicycle", "bicycle"),
    ("suitcase", "suitcase"),
    # Child props: they only ever *block* age-restricted ads (invariant 2).
    ("teddy bear", "teddy bear"),
    ("baby stroller", "stroller"),
)

OBJECT_PROMPTS: tuple[str, ...] = tuple(prompt for prompt, _ in OBJECT_VOCABULARY)
LABEL_FOR_PROMPT: dict[str, str] = dict(OBJECT_VOCABULARY)
OBJECT_LABELS: frozenset[str] = frozenset(LABEL_FOR_PROMPT.values())

# Objects whose presence near a tracked person counts as child evidence.
CHILD_PROP_LABELS: frozenset[str] = frozenset({"teddy bear", "stroller"})

DEFAULT_OBJECT_MODEL = "yoloe-26s-seg.pt"
# Open-vocabulary scores run lower than closed-set COCO ones (a clear cup scores
# ~0.35 where YOLO26 gave ~0.6); the snapshot's debounce absorbs the extra noise.
OBJECT_CONF = 0.25


def canonical_label(name: str) -> str:
    """Prompt → wire label. A name outside the vocabulary passes through unchanged."""
    return LABEL_FOR_PROMPT.get(name, name)


def vocabulary_fingerprint() -> str:
    return hashlib.sha256("\n".join(OBJECT_PROMPTS).encode()).hexdigest()[:10]


def baked_weights_path(weights: str) -> Path:
    base = Path(weights)
    return base.with_name(f"{base.stem}-vocab-{vocabulary_fingerprint()}.pt")


def _names(model: Any) -> list[str]:
    names = model.names
    return list(names.values()) if isinstance(names, dict) else list(names)


def bake_object_model(weights: str = DEFAULT_OBJECT_MODEL) -> str:
    """Return `.pt` weights with the vocabulary baked in, baking them once if needed."""
    from ultralytics import YOLOE  # local import: optional dependency

    baked = baked_weights_path(weights)
    if baked.exists():
        return str(baked)
    logger.info("baking the YOLOE vocabulary into %s (one-time, needs MobileCLIP)", baked)
    model = YOLOE(weights)
    model.set_classes(list(OBJECT_PROMPTS))
    model.save(str(baked))
    return str(baked)


def load_object_model(weights: str = DEFAULT_OBJECT_MODEL) -> Any:
    """YOLOE-26 whose classes are exactly `OBJECT_PROMPTS`.

    `.pt` weights are baked (see the module docstring) and loaded. Exported
    weights (`.engine`, OpenVINO dir, CoreML) carry the classes they were
    exported with, so they are loaded as they are and must have been exported
    from baked weights.
    """
    from ultralytics import YOLO, YOLOE  # local import: optional dependency

    if not weights.endswith(".pt"):
        model = YOLO(weights, task="segment")
    else:
        model = YOLOE(weights if _is_baked(weights) else bake_object_model(weights))
    if _names(model) != list(OBJECT_PROMPTS):
        raise ValueError(f"{weights} was not built from the current object vocabulary")
    return model


def _is_baked(weights: str) -> bool:
    return f"-vocab-{vocabulary_fingerprint()}" in Path(weights).stem


def main() -> None:
    """`python -m agent.vision.vocabulary [weights]`: bake the vocabulary ahead of time."""
    import sys

    logging.basicConfig(level=logging.INFO)
    weights = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OBJECT_MODEL
    print(bake_object_model(weights))


if __name__ == "__main__":
    main()

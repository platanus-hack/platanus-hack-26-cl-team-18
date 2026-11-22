"""The YOLOE vocabulary feedback loop stays consistent (no model needed: CI-safe).

The eval itself (python -m agent.vision.vocab_eval) needs the weights and runs on
purpose; these tests keep its labelled set, baseline and gate honest as the vocabulary
changes.
"""

from __future__ import annotations

import json

from agent.vision.vocab_eval import BASELINE, REPO, load_set, regressions
from agent.vision.vocabulary import OBJECT_LABELS

# Products a shopper holds that the vocabulary cannot tell apart yet (measured: they read
# as donut, cup or bottle). Kept in the set so a candidate prompt can be scored on them.
NOT_YET_EMITTED = {"chocolate bar", "candy", "milk", "sports drink"}


def test_every_photo_has_one_source_a_unique_id_and_a_known_label() -> None:
    photos = load_set()
    ids = [photo["id"] for photo in photos]
    assert len(ids) == len(set(ids))
    for photo in photos:
        assert ("commons" in photo) != ("repo" in photo), photo["id"]
        if "commons" in photo:
            assert photo["commons"].startswith("File:"), photo["id"]
        else:
            assert (REPO / photo["repo"]).is_file(), photo["repo"]
        assert photo["label"] in OBJECT_LABELS | NOT_YET_EMITTED, photo


def test_the_baseline_scores_every_labelled_product() -> None:
    baseline = json.loads(BASELINE.read_text())
    labelled = {photo["label"] for photo in load_set()}
    assert labelled <= set(baseline)
    for label, row in baseline.items():
        assert 0 <= row["found"] <= row["photos"], label


def test_a_label_that_loses_recall_or_gains_false_hits_fails_the_gate() -> None:
    before = {"chips": {"photos": 3, "found": 2, "false_hits": 0}}
    assert regressions(before, before) == []
    worse_recall = {"chips": {"photos": 3, "found": 1, "false_hits": 0}}
    assert regressions(worse_recall, before) == ["chips: found 1 < 2"]
    more_false = {"chips": {"photos": 3, "found": 3, "false_hits": 1}}
    assert regressions(more_false, before) == ["chips: false hits 1 > 0"]
    assert regressions({}, before) == ["chips: found 0 < 2"]

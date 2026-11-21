import pytest
from edge.agent.vision.frame_selector import redact_crop

np = pytest.importorskip("numpy")


def test_redaction_hides_head_and_rejects_ambiguous_frames():
    frame = np.full((300, 200, 3), 255, dtype=np.uint8)
    box = (10, 10, 190, 290)
    face = [(100, 45, 0.9), (95, 40, 0.9), (105, 40, 0.9)]
    crop = redact_crop(frame, box, [(box, face)])
    assert crop is not None
    assert np.all(crop[:126] == 0)
    assert np.all(crop[180:] == 255)
    assert np.all(frame == 255)  # input is never modified
    assert redact_crop(frame, box, []) is None
    assert redact_crop(frame, box, [(box, face), (box, face)]) is None
    assert redact_crop(frame, box, [(box, [(100, 200, 0.9)] * 3)]) is None
    assert redact_crop(frame, box, [(box, [(100, 45, 0.1)] * 3)]) is None

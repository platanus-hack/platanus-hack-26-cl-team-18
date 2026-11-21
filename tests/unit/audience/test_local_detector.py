from types import SimpleNamespace
from unittest.mock import Mock

from edge.agent.vision.replay import LocalDetector


class Scalar:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class Coordinates:
    def tolist(self):
        return [10, 20, 100, 200]


def test_detector_uses_bytetrack_and_preserves_track_id():
    box = SimpleNamespace(id=Scalar(7), cls=Scalar(0), conf=Scalar(0.9), xyxy=[Coordinates()])
    result = SimpleNamespace(boxes=[box], names={0: "person"})
    detector = LocalDetector.__new__(LocalDetector)
    detector.model = Mock()
    detector.model.track.return_value = [result]
    frame = object()

    detections = detector.detect(frame)

    detector.model.track.assert_called_once_with(
        frame, persist=True, tracker="bytetrack.yaml", verbose=False
    )
    assert len(detections) == 1
    assert detections[0].label == "person"
    assert detections[0].track_id == 7
    assert detections[0].box == (10.0, 20.0, 100.0, 200.0)

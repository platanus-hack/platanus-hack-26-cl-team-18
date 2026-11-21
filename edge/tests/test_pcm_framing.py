from agent.speech.sources import PcmFrameBuffer
from agent.speech.vad import FRAME_BYTES


def test_irregular_capture_chunks_preserve_every_sample_in_exact_frames():
    buffer = PcmFrameBuffer(8)
    data = bytes(range(256)) * 12
    position = 0
    for length in [1, 511, 17, 2000, 543]:
        buffer.push(data[position : position + length])
        position += length
    frames = []
    while (frame := buffer.pop()) is not None:
        assert len(frame) == FRAME_BYTES
        frames.append(frame)
    assert b"".join(frames) == data
    assert buffer.dropped == 0


def test_capture_is_bounded_before_scheduling_notifications():
    buffer = PcmFrameBuffer(2)
    notifications = sum(buffer.push(bytes([i]) * FRAME_BYTES) for i in range(100))
    assert notifications == 1
    assert buffer.dropped == 98
    assert buffer.pop() == bytes([98]) * FRAME_BYTES
    assert buffer.pop() == bytes([99]) * FRAME_BYTES
    assert buffer.pop() is None
    assert buffer.push(bytes(FRAME_BYTES))


def test_overflow_discards_an_incomplete_old_frame():
    buffer = PcmFrameBuffer(2)
    buffer.push(b"x" * 500)
    assert buffer.push(b"y" * FRAME_BYTES, overflow=True)
    assert buffer.pop() == b"y" * FRAME_BYTES
    assert buffer.dropped == 1

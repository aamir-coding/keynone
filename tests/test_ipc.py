import io

import pytest

from keynone.ipc import (
    EventWriter,
    Msg,
    ProtocolError,
    encode,
    encode_json,
    parse_event,
    read_frame,
)


def test_round_trip_frames() -> None:
    stream = io.BytesIO(
        encode_json(Msg.START, {"sid": 1}) + encode(Msg.AUDIO, b"\x01\x02" * 320) + encode(Msg.STOP)
    )
    kind, payload = read_frame(stream)
    assert kind is Msg.START and payload == b'{"sid":1}'
    kind, payload = read_frame(stream)
    assert kind is Msg.AUDIO and len(payload) == 640
    assert read_frame(stream) == (Msg.STOP, b"")
    assert read_frame(stream) is None  # clean EOF


def test_truncated_and_unknown_frames() -> None:
    with pytest.raises(ProtocolError):
        read_frame(io.BytesIO(encode(Msg.AUDIO, b"abcd")[:-2]))
    with pytest.raises(ProtocolError):
        read_frame(io.BytesIO(b"Z\x00\x00\x00\x00"))


def test_event_writer_and_parser() -> None:
    out = io.BytesIO()
    EventWriter(out).send("session_done", sid=3, chars=12)
    assert parse_event(out.getvalue()) == {"event": "session_done", "sid": 3, "chars": 12}
    assert parse_event(b"not json") is None


def test_event_writer_survives_closed_stream() -> None:
    out = io.BytesIO()
    writer = EventWriter(out)
    out.close()
    writer.send("ready")  # must not raise

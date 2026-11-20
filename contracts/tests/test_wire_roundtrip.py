import json

import pytest

from contracts import (
    MAX_WIRE_BYTES,
    TOPIC_TYPES,
    WIRE_TOPICS,
    WIRE_TYPES,
    WIRE_VERSION,
    AllowlistUpdate,
    AudioChunk,
    ContractError,
    Frame,
    HoldCommand,
    HoldSource,
    Lane,
    LaneResult,
    Topic,
    Transcript,
    dumps,
    from_wire,
    loads,
    to_wire,
)
from contracts.enums import LOCAL_ONLY_TOPICS
from contracts.samples import samples, write_samples


@pytest.mark.parametrize("obj", samples(), ids=lambda o: type(o).__name__)
def test_roundtrip_dict_and_bytes(obj):
    wire = to_wire(obj)
    assert wire["t"] == type(obj).__name__ and wire["v"] == WIRE_VERSION
    json.dumps(wire)  # pure JSON
    assert from_wire(wire) == obj
    assert loads(dumps(obj)) == obj


def test_every_wire_type_has_a_sample():
    assert {type(o).__name__ for o in samples()} == set(WIRE_TYPES)


def test_every_topic_has_a_payload_type_and_wire_topics_exclude_local_only():
    assert set(TOPIC_TYPES) == set(Topic)
    assert set(WIRE_TOPICS) == set(Topic) - LOCAL_ONLY_TOPICS
    assert Topic.TRANSCRIPT in LOCAL_ONLY_TOPICS  # raw transcript text never leaves the process
    assert all(c.__name__ in WIRE_TYPES for c in WIRE_TOPICS.values())


def test_unknown_field_enum_literal_version_type_are_rejected():
    wire = to_wire(HoldCommand(source=HoldSource.API, active=False, actor="a"))
    for bad in (
        {**wire, "d": {**wire["d"], "surprise": 1}},
        {**wire, "d": {**wire["d"], "source": "carrier_pigeon"}},
        {**wire, "v": 99},
        {**wire, "t": "Nope"},
    ):
        with pytest.raises(ContractError):
            from_wire(bad)
    muted = to_wire([o for o in samples() if type(o).__name__ == "MuteSpan"][0])
    muted["d"]["granularity"] = "paragraph"
    with pytest.raises(ContractError):
        from_wire(muted)


def test_invalid_values_surface_as_contract_error():
    wire = to_wire([o for o in samples() if type(o).__name__ == "RiskSignal"][0])
    wire["d"]["score"] = 7
    with pytest.raises(ContractError):
        from_wire(wire)


def test_hot_path_and_transient_types_cannot_be_serialised():
    with pytest.raises(TypeError):
        to_wire(Frame(seq=0, pts=0.0, arrival=0.0, image=None))
    with pytest.raises(TypeError):
        to_wire(AudioChunk(seq=0, pts_start=0.0, sample_rate=16000, pcm=[0] * 320, arrival=0.0))
    with pytest.raises(TypeError):
        to_wire(LaneResult(lane=Lane.FAST, frame_seq=0, pts=0.0, completed_at=0.0, ok=True))
    with pytest.raises(TypeError):
        to_wire(Transcript(0.0, 1.0, "x", (), "hi", "local", 0.1))


def test_size_limit_enforced():
    big = AllowlistUpdate(
        op="add", face_id="f", label=None, embedding=tuple(0.123456 for _ in range(30000)), version=1
    )
    with pytest.raises(ContractError):
        dumps(big)
    assert MAX_WIRE_BYTES == 256 * 1024


def test_normal_embedding_is_small():
    emb = AllowlistUpdate(
        op="add", face_id="f", label=None, embedding=tuple(0.123456 for _ in range(512)), version=1
    )
    assert len(dumps(emb)) < 8 * 1024


def test_invalid_json_is_a_contract_error():
    with pytest.raises(ContractError):
        loads(b"{not json")


def test_write_samples(tmp_path):
    written = write_samples(tmp_path)
    assert {p.stem for p in written} == set(WIRE_TYPES)
    assert all(from_wire(json.loads(p.read_text(encoding="utf-8"))) is not None for p in written)

"""
测试 topics 模块
统一用 pytest 风格编写
"""
from butler.bus.topics import (
    parse_event,
    parse_face,
    parse_tv_status,
    PUB_TV_TTS,
    SUB_TV_FACE,
)


def test_parse_face():
    ev = parse_face("tv/livingroom/face", {
        "name": "Kevin",
        "confidence": 0.92,
        "ts": 123.0,
    })
    assert ev.kind == "face"
    assert ev.member == "Kevin"
    assert ev.confidence == 0.92
    assert ev.room == "客厅"


def test_parse_face_stranger():
    ev = parse_face("tv/livingroom/face", {
        "name": "",
        "confidence": 0.3,
    })
    assert ev.member == "stranger"


def test_parse_tv_status():
    ev = parse_tv_status("tv/livingroom/status", {"state": "online"})
    assert ev.kind == "tv_status"
    assert ev.member == "online"


def test_parse_event():
    ev = parse_event("butler/event/voice", {
        "member": "lidicn",
        "text": "你好",
    })
    assert ev.kind == "voice"
    assert ev.member == "lidicn"


def test_pub_tv_tts_constant():
    assert PUB_TV_TTS == "tv/livingroom/cmd/tts"


def test_sub_tv_face_constant():
    assert SUB_TV_FACE == "tv/livingroom/face"

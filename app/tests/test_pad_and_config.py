"""The SDL gamepad reader starts headless (no controller needed), also inside a Qt application; it takes only the pads
it's told to; the 8BitDo or the DualSense, if one's attached, reads as the app's pad; config.example.toml is the app's
defaults."""
from pathlib import Path

import pytest


def test_sdl_reader_starts_headless():
    pytest.importorskip("pygame")
    from mmi_app.pad import BUTTON_NAMES, SdlGamepad

    pad = SdlGamepad()
    try:
        state = pad.poll()   # with no controller attached: not connected, all zero
        assert state.lx == 0 and state.buttons == frozenset() or state.connected
        assert "misc1" in BUTTON_NAMES and "paddle1" in BUTTON_NAMES
    finally:
        pad.close()


def test_a_pad_not_taken_is_never_read():
    pytest.importorskip("pygame")
    from mmi_app.pad import SdlGamepad

    pad = SdlGamepad(accept=frozenset())   # takes nothing: whatever is attached is ignored
    try:
        for _ in range(5):
            assert not pad.poll().connected
        assert pad.ignored == tuple((n, ids) for n, ids, mapped in pad.devices if mapped)
    finally:
        pad.close()


def test_the_apps_pad_when_one_is_attached():
    pytest.importorskip("pygame")
    from mmi_app.pad import SdlGamepad
    from mmi_app.profiles import pad_ids, pad_profile

    pad = SdlGamepad(accept=pad_ids())
    try:
        state = pad.poll()
        if not any(ids in pad_ids() for _n, ids, _m in pad.devices):
            pytest.skip("no 8BitDo Ultimate 2C or DualSense attached")
        profile = pad_profile(state.ids)
        assert state.connected and profile is not None and pad.ignored == ()
        assert {profile.halt_button, profile.menu_buttons[0]} <= set(pad._buttons)   # its halt and menu buttons are read
    finally:
        pad.close()


def test_sdl_polled_inside_qt(qtbot):
    """SDL's controller layer and Qt's event loop share the main thread, as in the app."""
    pytest.importorskip("pygame")
    from PyQt6.QtCore import QTimer

    from mmi_app.pad import SdlGamepad

    pad = SdlGamepad()
    polls = []
    timer = QTimer()
    timer.timeout.connect(lambda: polls.append(pad.poll()))
    try:
        timer.start(10)
        qtbot.waitUntil(lambda: len(polls) >= 20, timeout=2000)
    finally:
        timer.stop()
        pad.close()


def test_the_example_config_is_the_defaults():
    from mmi_app.config import Config, load

    assert load(str(Path(__file__).parent.parent / "config.example.toml")) == Config()

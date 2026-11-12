"""The lights (api.md 9.1) against the test server: the command, strict as the firmware's; the state after the hello
and on every change; the client's mailbox, for a front end that can't wait; and its mirror."""
import time

import pytest
from helpers import wait_for

from mmi_client import MmiClient, MmiError, format_light
from mmi_client.protocol import fmt_level


def light_events(unit) -> list[str]:
    return [e for _, e in unit.events if e.startswith("light ")]


def test_the_line_and_the_levels_as_written():
    assert format_light(warm=40, cool=12.5) == "light warm=40 cool=12.5"
    assert format_light(cool=0) == "light cool=0"
    with pytest.raises(ValueError):
        format_light()
    assert [fmt_level(v) for v in (39.990234375, 12.5, 0, 100, 0.0244140625, -0.0)] == [
        "39.99", "12.5", "0", "100", "0.02", "0"]


def test_dark_at_the_start_and_said_after_the_hello(client):
    assert client.lights == {"warm": 0.0, "cool": 0.0}        # unasked: it followed the !hello
    assert client.records["light"]["warm"] == 0


def test_set_and_hear_it(client, unit):
    assert client.light(warm=40, cool=12.5, wait=True) == {"warm": 39.99, "cool": 12.5}   # the nearest of its steps
    assert unit.light == {"warm": 1638 * 100 / 4096, "cool": 12.5}
    assert wait_for(lambda: client.lights == {"warm": 39.99, "cool": 12.5})               # its !light
    assert client.light(cool=0, wait=True) == {"warm": 39.99, "cool": 0.0}                # warm kept
    n = len(light_events(unit))
    assert client.light(cool=0, wait=True)["cool"] == 0                                  # no change: no event
    time.sleep(0.1)
    assert len(light_events(unit)) == n
    assert client.request("status light").values["warm"] == 39.99


@pytest.mark.parametrize("line, code", [("light", "arg"), ("light warm=101", "range"), ("light cool=-1", "range"),
                                        ("light warm=1 warm=2", "arg"), ("light Warm=1", "arg"),
                                        ("light warm=dim", "arg"), ("light warm=1e2", "arg")])
def test_refusals_change_nothing(client, unit, line, code):
    client.light(warm=25, wait=True)                           # 25 % is a step (1024): read back exactly
    with pytest.raises(MmiError) as e:
        client.request(line)
    assert e.value.code == code and unit.light == {"warm": 25.0, "cool": 0.0}


def test_the_mailbox_never_waits_and_keeps_only_the_latest(client, unit):
    t0 = time.monotonic()
    for v in range(0, 101):                                    # a slider dragged across, from the main thread
        client.light(warm=v)
    client.light(cool=12.5)                                    # then the other channel: both go
    assert time.monotonic() - t0 < 0.05                        # nothing waited for the unit
    assert wait_for(lambda: client.lights == {"warm": 100.0, "cool": 12.5})
    assert len(unit.commands("light")) < 30                    # the latest each time, not a queue of 102
    assert client.set_errors == {}


def test_a_refusal_lands_in_set_errors(client, unit):
    client.light(warm=150)
    assert wait_for(lambda: "light" in client.set_errors)
    assert "range" in client.set_errors["light"] and unit.light["warm"] == 0
    client.light(warm=50)
    assert wait_for(lambda: "light" not in client.set_errors and client.lights.get("warm") == 50)


def test_sent_once_connected_and_before_closing(unit):
    c = MmiClient(unit.host, unit.port, reconnect=True, retry_s=0.05)
    c.light(warm=30)                                           # asked for before there's a link
    c.start()
    try:
        assert wait_for(lambda: unit.light["warm"] == 1229 * 100 / 4096)   # 30 % is step 1228.8: 1229
    finally:
        c.light(warm=0, cool=0)                                # switched off on the way out ...
        c.close()
    assert unit.light == {"warm": 0.0, "cool": 0.0}            # ... and gone before the link closed


def test_every_session_hears_a_change(unit):
    a = MmiClient(unit.host, unit.port, reconnect=False)
    a.connect()
    try:
        unit.event("light t=1 warm=55 cool=5")                 # as another session's change reaches this one
        assert wait_for(lambda: a.lights == {"warm": 55.0, "cool": 5.0})
    finally:
        a.close()

"""The lid by hand (api.md 9.2) against the test server: the command, strict as the firmware's; its cap and floor;
the unit's own cuts; SW10; a duty ending with its session; the state after the hello, with every readout (once a
second, unasked) and on every change; and the client's mailbox and mirror."""
import time

import pytest
from helpers import wait_for

from mmi_client import MmiClient, MmiError, format_lid
from mmi_client.sim_lid import duty_code


def lid_states(unit) -> list[str]:
    """The !lid events sent, as their state: 'sw mode duty cut', each once (the readouts in between repeat it)."""
    out: list[str] = []
    for _, e in unit.events:
        if e.startswith("lid "):
            kv = dict(w.split("=", 1) for w in e.split()[1:] if "=" in w)
            state = " ".join(kv[k] for k in ("sw", "mode", "duty", "cut"))
            if not out or out[-1] != state:
                out.append(state)
    return out


def test_the_line_and_the_steps():
    assert format_lid(30) == "lid duty=30" and format_lid(-12.5) == "lid duty=-12.5" and format_lid(0) == "lid duty=0"
    assert duty_code(30, 50) == 1229 and duty_code(-30, 50) == 1229 and duty_code(1.99, 50) == 0   # as the firmware
    assert duty_code(33, 33) == 1351 and duty_code(100, 100) == 3276                              # never past a cap


def test_said_after_the_hello_with_its_sensors(client):
    lid = client.lid
    assert lid["sw"] == "on" and lid["mode"] == "idle" and lid["duty"] == 0 and lid["cut"] == 0   # unasked
    assert all(20 < lid[k] < 26 and 0 <= lid[k + ".sd"] < 0.02
               for k in ("board.a", "board.b", "board.edge", "med.j2", "med.j3"))           # each a mean and its SD
    assert abs(lid["v33"] - 3.3) < 0.01 and client.params["lid.duty_max"] == 50
    assert client.params["lid.medium_max"] == 40
    assert client.records["lid"] == lid


def test_a_readout_a_second_unasked(client, unit):
    seen = []
    client.on_event(lambda ev: seen.append(ev.values) if ev.name == "lid" else None)
    time.sleep(3.2)
    assert len(seen) in (3, 4)                                   # a readout a second, nothing watched
    ts = [v["t"] for v in seen]
    assert all(900 <= b - a <= 1100 for a, b in zip(ts, ts[1:]))   # t: the readout's
    assert all("board.edge.sd" in v and "med.j3.sd" in v for v in seen)
    with pytest.raises(MmiError) as e:
        client.request("watch lid 2")
    assert e.value.code == "arg" and "every readout" in str(e.value)
    assert client.request("status lid").values["t"] == client.lid["t"]   # the last readout, as sent


def test_set_and_hear_it(client, unit):
    assert client.lid_duty(30, wait=True) == 30                 # step 1229 is 30.005 %: said to 0.01 %
    assert unit.lid.code == 1229 and unit.lid.sign == 1
    assert wait_for(lambda: client.lid.get("mode") == "manual" and client.lid["duty"] == 30)   # its !lid
    assert client.lid_duty(-12.5, wait=True) == -12.5 and unit.lid.code == 512 and unit.lid.sign == -1
    assert client.lid_duty(1.5, wait=True) == 0                 # under 2 %: none
    assert client.request("lid off").values["duty"] == 0
    assert lid_states(unit)[-3:] == ["on manual 30 0", "on manual -12.5 0", "on idle 0 0"]
    n = len(lid_states(unit))
    assert client.lid_duty(0, wait=True) == 0                   # no change: no event
    time.sleep(0.1)
    assert len(lid_states(unit)) == n
    assert client.request("status lid").values["mode"] == "idle"


@pytest.mark.parametrize("line, code", [("lid", "arg"), ("lid duty", "arg"), ("lid duty=", "arg"),
                                        ("lid duty=warm", "arg"), ("lid duty=1e1", "arg"), ("lid temp=25", "arg"),
                                        ("lid duty=1 duty=2", "arg"), ("lid off duty=1", "arg"),
                                        ("lid duty=50.5", "range"), ("lid duty=-51", "range")])
def test_refusals_change_nothing(client, unit, line, code):
    client.lid_duty(25, wait=True)
    with pytest.raises(MmiError) as e:
        client.request(line)
    assert e.value.code == code and unit.lid.code == 1024
    if code == "range":
        assert "-50 to 50 (lid.duty_max)" in str(e.value)


def test_the_cap_is_the_units(unit):
    unit.params["lid.duty_max"] = "20"
    c = MmiClient(unit.host, unit.port, reconnect=False)
    c.connect()
    try:
        assert c.params["lid.duty_max"] == 20 and c.lid_duty(20, wait=True) == 20
        with pytest.raises(MmiError) as e:
            c.lid_duty(20.5, wait=True)
        assert e.value.code == "range"
    finally:
        c.close()


def test_a_hot_board_cuts_it_and_refuses_another(client, unit):
    client.lid_duty(-40, wait=True)                              # cooling too: whichever way it runs
    unit.lid_temps(board=60.5)
    assert wait_for(lambda: client.lid.get("cut") == "hot" and client.lid["duty"] == 0 and unit.lid.code == 0)
    with pytest.raises(MmiError) as e:
        client.lid_duty(10, wait=True)
    assert e.value.code == "hot" and "board.a 6" in str(e.value) and "the limit is 60" in str(e.value)
    assert client.lid_duty(0, wait=True) == 0                    # 0 always goes, and takes the cut back to 0
    assert wait_for(lambda: client.lid.get("cut") == 0)
    unit.lid_temps(board=40)
    assert wait_for(lambda: client.lid.get("board.a", 99) < 45)  # the next readout
    assert client.lid_duty(12.5, wait=True) == 12.5              # 10 would be 10.01: step 410


def test_a_warm_medium_cuts_it_either_way_and_refuses_another(client, unit):
    client.lid_duty(30, wait=True)
    unit.lid_temps(medium=40.2)                                  # med.j2 reads 40.25: past lid.medium_max
    assert wait_for(lambda: client.lid.get("cut") == "medium" and client.lid["duty"] == 0 and unit.lid.code == 0)
    assert client.lid["mode"] == "idle"
    with pytest.raises(MmiError) as e:
        client.lid_duty(-12.5, wait=True)                        # cooling refused too, till it's under
    assert e.value.code == "medium" and "med.j2 40.2" in str(e.value)
    assert "the limit is 40 (lid.medium_max)" in str(e.value)
    assert client.lid_duty(0, wait=True) == 0
    unit.lid_temps(medium=38)
    assert wait_for(lambda: client.lid.get("med.j2", 99) < 39)
    assert client.lid_duty(-12.5, wait=True) == -12.5


def test_a_bad_sensor_cuts_it_but_an_unfitted_probe_doesnt(client, unit):
    unit.unplug_lid()
    unit.lid_sensor("med.j3", "open")                            # no probe on J3: open from the first readout
    unit.plug_lid()
    assert wait_for(lambda: client.lid.get("mode") == "idle" and client.lid.get("med.j3") == "open")
    client.lid_duty(30, wait=True)
    time.sleep(1.2)
    assert unit.lid.code == 1229 and "med.j3.sd" not in client.lid   # it guards nothing, and nothing's wrong
    unit.lid_sensor("board.edge", "short")
    assert wait_for(lambda: client.lid.get("mode") == "fault" and client.lid["cut"] == "fault")
    with pytest.raises(MmiError) as e:
        client.lid_duty(10, wait=True)
    assert e.value.code == "fault" and "board.edge short" in str(e.value)
    unit.lid_sensor("board.edge", None)
    assert wait_for(lambda: client.lid.get("mode") == "idle")
    unit.lid_sensor("med.j2", "short")                           # a probe that can't guard the medium
    assert wait_for(lambda: client.lid.get("mode") == "fault")
    with pytest.raises(MmiError) as e:
        client.lid_duty(10, wait=True)
    assert e.value.code == "fault" and "med.j2 short" in str(e.value)
    unit.lid_sensor("med.j2", "open")                            # nor one come loose: it had read
    assert wait_for(lambda: client.lid.get("med.j2") == "open" and client.lid["mode"] == "fault")
    unit.lid_sensor("med.j2", None)
    assert wait_for(lambda: client.lid.get("mode") == "idle")
    assert client.lid_duty(12.5, wait=True) == 12.5


def test_unplugged_cut_and_refused_then_nothing_resumes(client, unit):
    client.lid_duty(30, wait=True)
    unit.unplug_lid()
    assert wait_for(lambda: client.lid.get("mode") == "absent" and client.lid["cut"] == "absent")
    assert "board.a" not in client.lid                           # nothing to read
    with pytest.raises(MmiError) as e:
        client.lid_duty(10, wait=True)
    assert e.value.code == "absent"
    unit.plug_lid()
    assert wait_for(lambda: client.lid.get("mode") == "idle" and "board.a" in client.lid)
    assert unit.lid.code == 0


def test_sw10_holds_it_and_keeps_the_duty(client, unit):
    client.lid_duty(30, wait=True)
    unit.set_lid_switch(False)
    assert wait_for(lambda: client.lid.get("sw") == "off" and client.lid["mode"] == "manual" and client.lid["duty"] == 30)
    b0 = unit.lid.board
    time.sleep(0.3)
    assert unit.lid.board <= b0 + 0.01                           # nothing driven
    assert client.lid_duty(20, wait=True) == 20                  # taken, held
    unit.set_lid_switch(True)
    assert wait_for(lambda: client.lid.get("sw") == "on" and client.lid["duty"] == 20)


def test_it_ends_with_the_session_that_set_it(unit):
    a = MmiClient(unit.host, unit.port, reconnect=False)
    a.connect()
    a.lid_duty(30, wait=True)
    a.close()                                                    # the app quitting, the duty left on
    assert wait_for(lambda: unit.lid.code == 0 and unit.lid.cut == "closed")
    b = MmiClient(unit.host, unit.port, reconnect=False)
    b.connect()
    try:
        assert b.lid["duty"] == 0 and b.lid["cut"] == "closed"  # the next client is told why
    finally:
        b.close()


def test_the_mailbox_never_waits_and_says_when_its_done(client, unit):
    t0 = time.monotonic()
    for v in range(0, 51):                                       # a slider dragged across, from the main thread
        client.lid_duty(v)
    assert time.monotonic() - t0 < 0.05 and client.set_pending("lid")
    assert wait_for(lambda: not client.set_pending("lid"))
    assert unit.lid.code == 2048 and wait_for(lambda: client.lid.get("duty") == 50)
    assert len(unit.commands("lid")) < 20                        # the latest each time, not a queue of 51
    client.lid_duty(60)                                          # past the cap
    assert wait_for(lambda: not client.set_pending("lid") and "lid" in client.set_errors)
    assert "range" in client.set_errors["lid"] and unit.lid.code == 2048
    client.lid_duty(0)                                           # asked again: the old refusal is gone at once
    assert "lid" not in client.set_errors
    assert wait_for(lambda: unit.lid.code == 0)


def test_the_mirror_has_the_duty_by_the_time_the_reply_is_in(client, unit):
    for v in (20, 0, -12.5, 25, 0, 37.5):                        # each said as written (20 is 19.995)
        client.lid_duty(v)
        assert wait_for(lambda: not client.set_pending("lid"))
        assert client.lid["duty"] == v and client.lid["cut"] == 0   # never the state from before it


def test_a_duty_lost_with_its_link_isnt_sent_again(unit):
    c = MmiClient(unit.host, unit.port, reconnect=True, retry_s=0.05)
    c.connect()
    try:
        unit.unplug()                                            # the cable pulled: nothing answers
        c.lid_duty(30)
        assert wait_for(lambda: not c.set_pending("lid"), timeout=3.0)
        assert "link" in c.set_errors["lid"]
        unit.replug()
        assert wait_for(lambda: c.connected, timeout=4.0)
        time.sleep(0.2)
        assert unit.lid.code == 0                                # the new session starts from 0
    finally:
        c.close()

"""The limit switches end to end: the test server's stage (sim_stage.py) trips them where the unit's would, says so
by itself as each changes, and the client mirrors it from those events alone (api.md 6.5)."""
import time

from helpers import wait_for

from mmi_client import Blocks, MmiClient
from mmi_client.protocol import halves
from mmi_client.test_server import TestServer

CLEAR = {"x": "0", "y": "0", "z": "0", "r": "0"}


def limit_events(unit) -> list[str]:
    return [e for _, e in unit.events if e.startswith(("limit ", "stall ", "lost "))]


def test_the_states_as_written():
    assert halves("0") == (frozenset(), False)
    assert halves("-+") == (frozenset({-1, 1}), False)
    assert halves("-?") == (frozenset({-1}), True)
    b = Blocks(limit=frozenset({-1}), stall=frozenset({1}))
    assert b.why(-5) == "limit" and b.why(5) == "stall" and b.why(0) is None
    assert Blocks(unknown=True).why(-5) is None          # '?' blocks nothing: both ways, slowly


def test_the_state_comes_after_the_hello():
    with TestServer(start=(-300.0, 0.0, 0.0, 0.0)) as unit:   # fine x inside its + switch (-400): found open, '?'
        c = MmiClient(unit.host, unit.port, reconnect=False)
        c.connect()
        try:
            assert c.limits == {**CLEAR, "x": "?"}            # nothing asked for: it followed the !hello
            assert c.blocks("x") == Blocks(unknown=True) and c.state.x.lim == "?"
        finally:
            c.close()


def test_a_fine_limit_mirrored(client, unit):
    unit.place(x=-480.0)                                  # 80 um short of x's + switch
    seen = []
    client.on_event(lambda ev: seen.append(ev.raw) if ev.name == "limit" else None)
    client.jog(x=200)                                     # into it: stopped where it trips, + blocked
    assert wait_for(lambda: client.limits["x"] == "+")
    assert abs(unit.pos[0] + 400) < 3
    assert client.blocks("x").why(50) == "limit" and client.blocks("x").why(-50) is None
    time.sleep(0.3)                                       # pushing on: the jog re-sent, the state not
    client.jog(x=-200)                                    # away: allowed; the switch closes 200 um back
    assert wait_for(lambda: client.limits["x"] == "0", timeout=3)
    client.jog()
    assert seen == ["limit x +", "limit x 0"]             # each change once, and nothing in between


def test_the_effector_switch_shows_on_fine_x(client, unit):
    unit.place(x=-900.0, r=-108950.0)                     # rail + fine x 50 um from MIN (-109.9 mm)
    client.jog(r=-1000)
    assert wait_for(lambda: client.limits == {**CLEAR, "x": "-", "r": "-"})
    assert client.blocks("r").why(-10) == "limit" and client.blocks("x").why(-10) == "limit"
    assert client.blocks("r").why(10) is None and client.blocks("x").why(10) is None
    client.jog(x=-50)                                     # fine x can't push the effector on either
    time.sleep(0.2)
    assert unit.stage.v[0] == 0.0
    client.jog(r=2000)                                    # the rail backs it off
    assert wait_for(lambda: client.limits == CLEAR, timeout=3)
    client.jog()
    got = [e.split(" pos=")[0] for e in limit_events(unit)]
    assert got == ["limit x -", "limit r -", "lost r limit", "limit x 0", "limit r 0"]
    assert abs(int(limit_events(unit)[1].split("pos=")[1]) + 109000) <= 3   # stopped where MIN trips


def test_the_carriage_switch_is_the_rails_alone(client, unit):
    client.jog(r=3000)                                    # MAX is 0.6 mm to the right of where it starts
    assert wait_for(lambda: client.limits == {**CLEAR, "r": "+"})
    assert client.blocks("x").why(10) is None
    client.jog()
    assert "lost r limit" in limit_events(unit)


def test_a_stall_at_a_hard_stop(client, unit):
    unit.place(z=-540.0)                                  # inside z's lo switch, 10 um from its stop: '?'
    assert wait_for(lambda: client.limits["z"] == "?")
    client.jog(z=-250)                                    # the end unknown: no more than fine.vunk (20 um/s)
    assert wait_for(lambda: client.stalls["z"] == "-", timeout=3)   # 0.5 s to the stop, then 50 ms against it
    assert unit.pos[2] == -550.0
    assert client.blocks("z").why(-5) == "stall" and client.blocks("z").why(5) is None
    client.jog(z=250)
    assert wait_for(lambda: client.stalls["z"] == "0")
    client.jog()


def test_the_record_agrees(client, unit):
    unit.place(x=-900.0, r=-109000.0)                     # on MIN
    assert wait_for(lambda: client.limits["r"] == "-")
    st = client.status()
    assert (st.x.lim, st.x.blk, st.r.lim, st.r.blk, st.y.lim) == ("-", "-", "-", "-", "0")


def test_a_new_session_starts_from_its_snapshot(client, unit):
    unit.place(y=6200.0)                                  # inside y's + switch: '?'
    assert wait_for(lambda: client.limits["y"] == "?")
    client.limits = {**CLEAR, "z": "+"}                   # whatever the client held before
    unit.drop()                                           # the session ends; the client reconnects
    assert wait_for(lambda: client.connected and client.limits == {**CLEAR, "y": "?"}, timeout=4)

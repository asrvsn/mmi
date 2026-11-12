"""MmiClient against the test server: the handshake, and the guarantees api.md asks of a client."""
import socket
import time

import pytest
from helpers import wait_for

from mmi_client import MmiClient, MmiDisconnected, MmiError


def jogs_since(unit, t):
    return [l for tt, l in unit.received if tt >= t and (l == "jog" or l.startswith("jog "))]


def test_handshake(client, unit):
    assert client.connected and client.hello.api == "0.11" and client.hello.fw == "test" and not client.hello.halt
    assert client.params["x.vmax"] == 250 and client.params["r.vmax"] == 5000 and client.params["jog.lease"] == 250
    assert client.params["x.model.duty"] == [0.038, 0.92] and client.params["r.effector"] == "min"
    assert client.cfg == ["x.vmax rejected (line 3): '999' out of range"]
    assert client.state.motors == "on" and client.motors == "on" and not client.halted


def test_requests(client):
    assert client.get("x.vmax", "jog.lease") == {"x.vmax": 250, "jog.lease": 250}
    with pytest.raises(MmiError) as e:
        client.get("x.nope")
    assert e.value.code == "arg"
    with pytest.raises(MmiError) as e:
        client.request("bogus")
    assert e.value.code == "unknown"
    assert client.ping() < 0.2
    assert client.status().r.drv == "ok"
    assert any(h.startswith("halt") for h in client.help())


def test_jog_kept_alive_inside_the_lease(client, unit):
    client.jog(x=50)
    assert wait_for(lambda: unit.targets[0] == 50)
    t = unit.now()
    time.sleep(0.6)
    assert unit.targets[0] == 50 and not unit.halted and not any(e.startswith("halt on") for _, e in unit.events)
    n = len(jogs_since(unit, t))
    assert 7 <= n <= 16                       # re-sent every 50 ms, no more


def test_jog_mailbox_never_queues(client, unit):
    t = unit.now()
    for v in range(1, 400):                   # a caller hammering it: only the latest counts
        client.jog(x=v / 4)
    assert wait_for(lambda: unit.targets[0] == 99.8)
    assert len(jogs_since(unit, t)) < 15
    client.jog()
    assert wait_for(lambda: unit.targets[0] == 0)
    t = unit.now()
    time.sleep(0.3)
    assert jogs_since(unit, t) == []          # a zero goes once, then nothing


def test_change_goes_out_promptly(client, unit):
    client.jog(y=10)
    assert wait_for(lambda: unit.targets[1] == 10)
    t0 = time.monotonic()
    client.jog(y=-20)
    assert wait_for(lambda: unit.targets[1] == -20)
    assert time.monotonic() - t0 < 0.06


def test_halt_now_then_held_until_let_go(client, unit):
    client.jog(x=50, r=1000)
    assert wait_for(lambda: unit.targets[3] == 1000)
    t0 = time.monotonic()
    client.halt()
    assert wait_for(lambda: unit.halted, timeout=0.5)
    assert time.monotonic() - t0 < 0.05       # straight onto the wire, ahead of any jog
    assert unit.targets == [0.0] * 4
    t = unit.now()
    for _ in range(30):                       # the operator still pushing
        client.jog(x=50, r=1000)
        time.sleep(0.01)
    assert jogs_since(unit, t) == [] and unit.halted   # held in the client, not even sent
    client.jog()                              # let go
    assert wait_for(lambda: not unit.halted)
    assert any(e == "halt off" for _, e in unit.events)
    client.jog(x=30)
    assert wait_for(lambda: unit.targets[0] == 30)


def test_halt_while_idle_releases_at_once(client, unit):
    client.halt(wait=True)
    assert wait_for(lambda: not unit.halted)  # nothing was held: the zero that follows releases it
    time.sleep(0.1)
    assert [c for c in unit.commands() if c != "ping"][-2:] == ["halt", "jog"]


def test_a_quick_let_go_still_counts(client, unit):
    client.jog(x=50)
    assert wait_for(lambda: unit.targets[0] == 50)
    client.halt()
    assert wait_for(lambda: unit.halted)
    client.jog()                              # let go and push again faster than the sender's 10 ms poll
    client.jog(x=20)
    assert wait_for(lambda: unit.targets[0] == 20)
    jogs = unit.commands("jog")
    assert jogs[-2:] == ["jog", "jog x=20"]   # the zero that releases the halt goes first
    assert not any(e == "jog refused halted" for _, e in unit.events)


def test_our_halt_echo_doesnt_stop_a_new_move(client, unit):
    client.halt()                             # at rest: released straight away by the zero after it
    client.jog(x=50)                          # a new request, before the !halt on has come back
    assert wait_for(lambda: unit.targets[0] == 50)
    time.sleep(0.4)                           # the late !halt on mustn't hold it: still re-sent, the lease kept
    assert unit.targets[0] == 50 and not unit.halted
    assert not any(e == "halt on cause=lease" for _, e in unit.events)


def test_halt_from_elsewhere(client, unit):
    client.jog()
    unit.halt_elsewhere()                     # someone at the USB port; the operator here is at rest
    assert wait_for(lambda: not unit.halted)  # the caller's zero goes again and releases it
    client.jog(x=10)
    assert wait_for(lambda: unit.targets[0] == 10)
    unit.halt_elsewhere()                     # now while pushing: the unit holds it
    time.sleep(0.2)
    assert unit.halted and unit.targets[0] == 0
    assert any(e == "jog refused halted" for _, e in unit.events)
    client.jog()
    assert wait_for(lambda: not unit.halted)
    client.jog(x=10)
    assert wait_for(lambda: unit.targets[0] == 10)


def test_nothing_moves_after_connect_until_let_go(unit):
    c = MmiClient(unit.host, unit.port, reconnect=False)
    c.jog(x=40)                               # a stick already held when the client starts
    c.connect()
    try:
        t = unit.now()
        time.sleep(0.2)
        assert jogs_since(unit, t) == [] and unit.targets[0] == 0
        c.jog()
        c.jog(x=20)
        assert wait_for(lambda: unit.targets[0] == 20)
    finally:
        c.close()


def test_motors_on_needs_a_let_go(client, unit):
    unit.set_motors(False)
    client.jog(z=30)
    assert wait_for(lambda: any(e == "jog refused motors-off" for _, e in unit.events))
    unit.set_motors(True)
    assert wait_for(lambda: client.motors == "on")
    t = unit.now()
    time.sleep(0.2)
    assert jogs_since(unit, t) == [] and unit.targets[2] == 0
    client.jog()
    client.jog(z=30)
    assert wait_for(lambda: unit.targets[2] == 30)


def test_a_drop_mid_move_halts_until_let_go(client, unit):
    drops = []
    client.on_disconnect(lambda c, why: drops.append(why))
    client.jog(x=40)
    assert wait_for(lambda: unit.targets[0] == 40)
    unit.drop()                               # the session ends: the client died, or the unit cut it
    assert unit.halted and unit.targets[0] == 0 and ("halt on cause=closed" in [e for _, e in unit.events])
    assert wait_for(lambda: drops and unit.connections == 2 and client.connected, timeout=3)
    assert client.hello.halt and client.halted   # the new session inherits the halt
    time.sleep(0.2)
    assert unit.halted and unit.targets[0] == 0  # the stick still held: nothing
    client.jog()                              # let go
    assert wait_for(lambda: not unit.halted and not client.halted)
    client.jog(x=10)
    assert wait_for(lambda: unit.targets[0] == 10)


def test_a_drop_at_rest_leaves_no_halt(client, unit):
    client.jog(x=40)
    assert wait_for(lambda: unit.targets[0] == 40)
    client.jog()
    assert wait_for(lambda: unit.targets[0] == 0)
    unit.drop()
    assert wait_for(lambda: unit.connections == 2 and client.connected, timeout=3)
    assert not unit.halted and not client.halted
    client.jog(y=5)
    assert wait_for(lambda: unit.targets[1] == 5)


def test_cable_pulled(unit):
    c = MmiClient(unit.host, unit.port, keepalive=0.2, timeout=0.3, retry_s=0.05)
    drops = []
    c.on_disconnect(lambda cl, why: drops.append(why))
    c.connect()
    try:
        c.jog(x=40)
        assert wait_for(lambda: unit.targets[0] == 40)
        unit.unplug()                         # the unit sees its link go and halts; the client sees nothing at all
        assert unit.halted and unit.targets[0] == 0 and ("halt on cause=link" in [e for _, e in unit.events])
        assert wait_for(lambda: drops, timeout=2) and "ping" in drops[0]   # its keepalive went unanswered
        c.jog()                               # let go while the cable is out
        time.sleep(0.3)
        assert not c.connected                # no way through yet
        unit.replug()
        assert wait_for(lambda: c.connected and not unit.halted, timeout=3)   # back, and its zero released the halt
        c.jog(x=10)
        assert wait_for(lambda: unit.targets[0] == 10)
    finally:
        c.close()


def test_replaced_stays_off(client, unit):
    other = MmiClient(unit.host, unit.port, reconnect=False)
    other.connect()
    try:
        assert wait_for(lambda: client.replaced and not client.connected)
        time.sleep(0.4)
        assert not client.connected and other.connected   # no tug of war
        with pytest.raises(MmiDisconnected):
            client.halt()
    finally:
        other.close()


def test_events_and_watch(client, unit):
    seen = []
    client.on_event(lambda ev: seen.append(ev.name))
    client.watch(20)
    assert wait_for(lambda: "drive" in seen)
    unit.event("limit x +")
    assert wait_for(lambda: "limit" in seen)
    ev = client.get_event(timeout=1)
    assert ev is not None
    client.jog(x=25)
    assert wait_for(lambda: client.state is not None and client.state.x.v == 25.0)


def test_the_server_halts_a_jog_that_isnt_renewed(unit):
    s = socket.create_connection((unit.host, unit.port))
    try:
        s.sendall(b"jog x=10\n")             # typed by hand: one lease's worth, then a halt
        assert wait_for(lambda: unit.targets[0] == 10)
        assert wait_for(lambda: unit.halted and unit.targets[0] == 0, timeout=1)
        assert any(e == "halt on cause=lease" for _, e in unit.events)
        s.sendall(b"jog x=10\n")
        time.sleep(0.1)
        assert unit.halted and unit.targets[0] == 0   # held until a plain jog
        s.sendall(b"jog\n")
        assert wait_for(lambda: not unit.halted)
    finally:
        s.close()


def test_a_stalled_client_halts_the_unit(client, unit):
    client.jog(x=40)
    assert wait_for(lambda: unit.targets[0] == 40)
    with client._sock_lock:                   # the whole process stalls (Ctrl-Z, an overloaded Pi): nothing goes out
        time.sleep(0.4)
    assert unit.halted and ("halt on cause=lease" in [e for _, e in unit.events])
    time.sleep(0.2)
    assert unit.halted and unit.targets[0] == 0   # back, the stick still held: refused until let go
    client.jog()
    assert wait_for(lambda: not unit.halted and not client.halted)
    client.jog(x=10)
    assert wait_for(lambda: unit.targets[0] == 10)


def test_deadman(unit):
    c = MmiClient(unit.host, unit.port, reconnect=False, deadman=0.15)
    c.connect()
    try:
        for _ in range(30):                   # a front end's loop, renewing every 20 ms: no trouble
            time.sleep(0.02)
            c.jog(x=40)
        t0 = time.monotonic()                 # then the loop hangs: the library stops renewing, and halts
        assert unit.targets[0] == 40 and not unit.halted and c.deadman_trips == 0
        assert wait_for(lambda: unit.halted, timeout=1)
        assert 0.14 < time.monotonic() - t0 < 0.3 and c.deadman_trips == 1
        assert unit.commands()[-1] == "halt" and not any(e == "halt on cause=lease" for _, e in unit.events)
        c.jog(x=40)                           # the loop back, the stick still held: nothing until let go
        time.sleep(0.3)
        assert unit.halted and c.deadman_trips == 1
        c.jog()
        assert wait_for(lambda: not unit.halted)
        c.jog(x=10)
        assert wait_for(lambda: unit.targets[0] == 10)
        c.jog()
        time.sleep(0.3)                       # at zero the deadman has nothing to do
        assert not unit.halted and c.deadman_trips == 1
    finally:
        c.close()


def test_close_after_letting_go_is_a_ramped_stop(unit):
    c = MmiClient(unit.host, unit.port, reconnect=False)
    c.connect()
    c.jog(x=40)
    assert wait_for(lambda: unit.targets[0] == 40)
    c.jog()
    c.close()                                 # straight after: the zero still goes out first
    assert wait_for(lambda: unit.connections == 1 and unit._conn is None)
    assert unit.commands("jog")[-1] == "jog" and not unit.halted


def test_close_mid_move_halts(unit):
    c = MmiClient(unit.host, unit.port, reconnect=False)
    c.connect()
    c.jog(x=40)
    assert wait_for(lambda: unit.targets[0] == 40)
    c.close()                                 # the move abandoned: the unit halts
    assert wait_for(lambda: unit.halted and "halt on cause=closed" in [e for _, e in unit.events])

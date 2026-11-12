"""The hand controls (halt, hold, jog) with a scripted pad, against the test server."""
import time

from helpers import wait_for

from mmi_app.hand import HandControl
from mmi_app.pad import PadState
from mmi_app.shaping import Mapping
from mmi_app.texts import drive_line, plain

REST = PadState(connected=True, name="test pad")
RIGHT = PadState(connected=True, name="test pad", rx=1.0)   # the right stick (fine x, y), full right


class ScriptedPad:
    def __init__(self):
        self.state = REST

    def poll(self):
        return self.state


def held(state, *buttons):
    return PadState(**{**state.__dict__, "buttons": frozenset(buttons)})


def run(app, pad, state, n=5):
    pad.state = state
    for _ in range(n):
        app.step()


def make(client):
    pad = ScriptedPad()
    m = Mapping()
    m.cap(client.params)
    return HandControl(client, pad, m, halt_button="b"), pad


def test_stick_drives_the_unit(client, unit):
    app, pad = make(client)
    run(app, pad, REST)
    run(app, pad, RIGHT)
    assert wait_for(lambda: unit.targets[0] == 250)
    run(app, pad, REST)
    assert wait_for(lambda: unit.targets[0] == 0)


def test_halt_button(client, unit):
    app, pad = make(client)
    run(app, pad, REST)
    run(app, pad, RIGHT)
    assert wait_for(lambda: unit.targets[0] == 250)
    run(app, pad, held(RIGHT, "b"), 1)                     # pressed while pushing right
    assert wait_for(lambda: unit.halted, timeout=0.2) and unit.targets[0] == 0
    run(app, pad, held(RIGHT, "b"), 20)                     # held: nothing
    run(app, pad, RIGHT, 20)                                # button up, stick still right: still nothing
    time.sleep(0.2)
    assert unit.targets[0] == 0 and unit.halted
    assert app.holding
    run(app, pad, REST)                                     # everything let go
    assert wait_for(lambda: not unit.halted)
    run(app, pad, RIGHT)
    assert wait_for(lambda: unit.targets[0] == 250)
    assert app.halts == 1


def test_halt_button_held_keeps_it_still(client, unit):
    app, pad = make(client)
    run(app, pad, held(REST, "b"), 1)                       # halt with nothing pushed
    assert wait_for(lambda: unit.halted or unit.commands("halt"))
    run(app, pad, held(RIGHT, "b"), 20)                     # pushing while still holding halt
    time.sleep(0.2)
    assert unit.targets[0] == 0
    run(app, pad, REST)
    run(app, pad, RIGHT)
    assert wait_for(lambda: unit.targets[0] == 250)


def test_unplugged_mid_move_stops_at_once(client, unit):
    app, pad = make(client)
    run(app, pad, REST)
    run(app, pad, RIGHT)
    assert wait_for(lambda: unit.targets[0] == 250)
    run(app, pad, PadState(), 1)                            # gone
    assert wait_for(lambda: unit.targets[0] == 0, timeout=0.1)   # well inside the 250 ms lease
    assert not unit.halted                                  # a ramped stop: nothing to let go of


def test_a_hung_loop_halts_with_the_deadman(unit):
    from mmi_client import MmiClient
    c = MmiClient(unit.host, unit.port, reconnect=False, deadman=0.15)
    c.connect()
    try:
        app, pad = make(c)
        run(app, pad, REST)
        for _ in range(10):
            run(app, pad, RIGHT, 1)
            time.sleep(0.02)
        assert unit.targets[0] == 250
        assert wait_for(lambda: unit.halted, timeout=0.5)       # the loop stops (no more steps): halted
        assert wait_for(lambda: plain(drive_line(c, app.holding)).startswith("HALTED"), timeout=0.5)   # its !halt on back
        for _ in range(10):                                     # back, the stick still right: nothing
            run(app, pad, RIGHT, 1)
            time.sleep(0.02)
        assert unit.halted and unit.targets[0] == 0
        run(app, pad, REST)
        assert wait_for(lambda: not unit.halted)
        run(app, pad, RIGHT)
        assert wait_for(lambda: unit.targets[0] == 250)
    finally:
        c.close()


def test_the_drive_line_follows(client, unit):
    app, pad = make(client)
    run(app, pad, REST)
    assert drive_line(client, app.holding) == [("PARKED", "ok")]
    run(app, pad, RIGHT)
    assert wait_for(lambda: drive_line(client, app.holding) == [("JOG x 250um/s y 0um/s z 0um/s r 0um/s", "busy")])
    run(app, pad, held(RIGHT, "b"), 1)
    assert plain(drive_line(client, app.holding)).startswith("HALTED")


def test_a_handover_holds_until_the_new_device_is_let_go(client, unit):
    app, pad = make(client)
    run(app, pad, REST)
    run(app, pad, RIGHT)
    assert wait_for(lambda: unit.targets[0] == 250)
    other = PadState(connected=True, name="keyboard", rt=1.0)    # the pad unplugged, a key held on the keyboard
    run(app, pad, other, 1)
    assert wait_for(lambda: unit.targets == [0.0, 0.0, 0.0, 0.0], timeout=0.1)   # a stop, not the rail
    run(app, pad, other, 20)
    time.sleep(0.1)
    assert unit.targets == [0.0, 0.0, 0.0, 0.0] and app.settling and not unit.halted
    assert drive_line(client, app.holding, app.settling) == [("HELD - let go to resume", "alert")]
    run(app, pad, PadState(connected=True, name="keyboard"))     # let go
    assert not app.settling
    run(app, pad, other)                                          # now it drives
    assert wait_for(lambda: unit.targets[3] == 5000)


def test_the_menu_has_the_controls(client, unit):
    app, pad = make(client)
    run(app, pad, REST)
    run(app, pad, RIGHT)
    assert wait_for(lambda: unit.targets[0] == 250)
    app.stop()                                              # the menu opening mid-move: a halt, at once
    assert wait_for(lambda: unit.halted or any(e == "halt on" for _, e in unit.events), timeout=0.2)
    pad.state = RIGHT
    assert app.step(drive=False) is RIGHT                   # the menu reads the stick, which drives nothing
    for _ in range(20):
        app.step(drive=False)
    time.sleep(0.1)
    assert unit.targets == [0.0, 0.0, 0.0, 0.0] and app.vector == (0.0, 0.0, 0.0, 0.0)
    run(app, pad, held(REST, "b"), 1)                       # the halt button still halts in the menu
    run(app, pad, RIGHT, 10)                                # the menu closed, the stick still held: nothing
    time.sleep(0.1)
    assert unit.targets[0] == 0 and app.settling
    run(app, pad, REST, 3)                                  # let go
    run(app, pad, RIGHT, 3)
    assert wait_for(lambda: unit.targets[0] == 250)         # then it drives
    run(app, pad, REST)


def test_opening_the_menu_at_rest_halts_nothing(client, unit):
    app, pad = make(client)
    run(app, pad, REST)
    n = len(unit.commands("halt"))
    app.stop()
    time.sleep(0.1)
    assert len(unit.commands("halt")) == n and not unit.halted


def test_in_the_menu_the_back_button_doesnt_halt(client, unit):
    """A pad's halt button is its back button in the menu (the roles carry both): pressed there it doesn't halt, nor
    while it's still held as the menu closes behind it. A press that only halts (the keyboard's Space) still does."""
    app, pad = make(client)
    run(app, pad, REST)
    n = len(unit.commands("halt"))
    pad.state = held(REST, "b", "back")         # B in the menu: back
    for _ in range(5):
        app.step(drive=False)
    for _ in range(5):
        app.step()                              # the menu closed by it, B still held
    time.sleep(0.1)
    assert len(unit.commands("halt")) == n and app.halts == 0 and not unit.halted
    run(app, pad, REST, 3)                      # let go, then pressed with the menu shut: a halt, as ever
    run(app, pad, held(REST, "b", "back"), 1)
    assert wait_for(lambda: len(unit.commands("halt")) == n + 1) and app.halts == 1
    run(app, pad, REST, 3)
    pad.state = held(REST, "b")                 # in the menu, a press that only halts (Space): it halts
    app.step(drive=False)
    assert app.halts == 2
    run(app, pad, REST, 3)

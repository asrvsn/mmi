"""The menu without a window: the controls as a remote's moves (nav.py), and the menu they move (menu.py), with its
lights and the lid's duty going to a stand-in for the client."""
from mmi_app.menu import COARSE, DUTY_COARSE, DUTY_FINE, FINE, ITEMS, Menu
from mmi_app.nav import Move, Navigator, pointing
from mmi_app.pad import PadState

KEYS = dict(connected=True, name="keyboard")


class Client:
    """MmiClient as the menu sees it. Each duty asked for is taken at once, as the unit would (its lid mirror says
    so), unless `pending` holds it."""

    def __init__(self):
        self.lights: list[dict] = []
        self.duties: list[float] = []
        self.params = {"lid.duty_max": 50, "lid.medium_max": 40}
        self.connected = True
        self.lid = {"sw": "on", "mode": "idle", "duty": 0, "cut": 0}
        self.set_errors: dict[str, str] = {}
        self.pending = False

    def light(self, **values) -> None:   # MmiClient.light(), never waiting
        self.lights.append(values)

    def lid_duty(self, duty: float) -> None:
        self.duties.append(duty)
        d = duty if abs(duty) >= 2 else 0
        self.lid = {**self.lid, "duty": d, "cut": 0, "mode": "manual" if d else "idle"}

    def set_pending(self, topic: str) -> bool:
        return self.pending


def test_where_the_controls_point():
    assert pointing(PadState(**KEYS)) == (None, 0.0)
    assert pointing(PadState(**KEYS, ry=-0.5)) == ("down", 0.5)                  # the down arrow (as the right stick)
    assert pointing(PadState(**KEYS, ly=1.0)) == ("up", 1.0)                     # W with Shift (the left's vertical)
    assert pointing(PadState(**KEYS, rx=0.3, ry=0.6)) == ("up", 0.6)             # along its main direction
    assert pointing(PadState(**KEYS, lx=-0.9)) == ("left", 0.9)                  # the pad's left stick too
    assert pointing(PadState(**KEYS, buttons=frozenset({"right"}))) == ("right", 1.0)   # the d-pad: all the way


def test_a_press_once_then_it_repeats():
    nav = Navigator(delay=0.4, period=0.08)
    held = PadState(**KEYS, rx=0.5)
    assert nav.moves(held, 0.0) == [Move("right", False)]                        # half way (the arrow): fine
    assert nav.moves(held, 0.39) == []
    assert nav.moves(held, 0.41) == [Move("right", False)]                       # held: repeating
    assert nav.moves(held, 0.45) == [] and nav.moves(held, 0.50) == [Move("right", False)]
    assert nav.moves(PadState(**KEYS, rx=1.0), 0.6) == [Move("right", True)]    # all the way (Shift): coarse
    assert nav.moves(PadState(**KEYS, rx=0.3), 0.7) == [Move("right", False)]   # under ON, over OFF: still held
    assert nav.moves(PadState(**KEYS), 0.8) == []                                # let go
    assert nav.moves(PadState(**KEYS, ry=0.5), 0.81) == [Move("up", False)]     # another, at once
    assert nav.moves(PadState(**KEYS, rx=0.5), 0.82) == [Move("right", False)]  # turned: a new press


def test_enter_on_its_edge():
    nav = Navigator()
    a = PadState(**KEYS, buttons=frozenset({"enter"}))
    assert nav.moves(a, 0.0) == [Move("enter")] and nav.moves(a, 1.0) == []      # held: once
    assert nav.moves(PadState(**KEYS), 1.1) == [] and nav.moves(a, 1.2) == [Move("enter")]


def test_whats_held_as_it_opens_waits_for_a_let_go():
    nav = Navigator()
    driving = PadState(**KEYS, rx=1.0, buttons=frozenset({"enter"}))            # still pushing as the menu opens
    nav.reset(driving)
    assert nav.moves(driving, 0.0) == [] and nav.moves(driving, 2.0) == []
    assert nav.moves(PadState(**KEYS), 2.1) == []                                 # let go
    assert nav.moves(PadState(**KEYS, rx=1.0), 2.2) == [Move("right", True)]


def test_light_calib_in_and_out():
    c = Client()
    m = Menu(c)
    m.open()
    assert m.is_open and not m.inside and m.chosen.label == "Light calib." and c.lights == []   # looking, not lit
    m.handle(Move("right"))
    assert m.inside and m.lights_on and c.lights == [{"warm": 0.0, "cool": 0.0}]   # in: its levels
    m.handle(Move("right"))
    m.handle(Move("right", coarse=True))
    assert m.sliders[0].value == round(FINE + COARSE, 1) and c.lights[-1] == {"warm": 1.1}
    m.handle(Move("down"))
    for _ in range(3):
        m.handle(Move("right", coarse=True))
    m.handle(Move("left"))
    assert m.row == 1 and m.sliders[1].value == 2.9 and c.lights[-1] == {"cool": 2.9}
    n = len(c.lights)
    for _ in range(40):
        m.handle(Move("left", coarse=True))                                        # down to 0, and no further
    assert m.sliders[1].value == 0 and len(c.lights) == n + 3                      # sent only while it changed
    m.handle(Move("up"))
    m.handle(Move("up"))                                                           # already at the top
    assert m.row == 0
    assert not m.back() and m.is_open and not m.inside                            # back to the list: lights off
    assert c.lights[-1] == {"warm": 0, "cool": 0}
    m.handle(Move("enter"))
    assert c.lights[-1] == {"warm": 1.1, "cool": 0.0}                              # in again: the levels kept
    m.close()                                                                      # the app quitting, from inside
    assert not m.is_open and c.lights[-1] == {"warm": 0, "cool": 0}


def test_the_list():
    c = Client()
    m = Menu(c)
    m.open()
    m.handle(Move("down"))
    m.handle(Move("down"))                                                         # the end of the list
    assert m.chosen is ITEMS[-1] and m.chosen.label == "Temp calib." and not m.inside
    m.handle(Move("up"))
    assert m.chosen.key == "light"
    assert m.back() and not m.is_open                                             # out of the menu
    m.reapply()
    assert c.lights == [] and c.duties == []                                       # not ours: nothing set again


def temp_calib(c=None) -> Menu:
    m = Menu(c or Client())
    m.open()
    m.handle(Move("down"))
    m.handle(Move("enter"))
    return m


def test_temp_calib_the_switch_before_any_duty():
    c = Client()
    m = temp_calib(c)
    assert m.inside and m.row == 0 and not m.heat_on and c.duties == [0]           # in: off, and the unit's duty 0
    m.handle(Move("down"))
    for _ in range(3):
        m.handle(Move("right"))
    m.handle(Move("right", coarse=True))
    assert m.row == 1 and m.duty.value == 3 * DUTY_FINE + DUTY_COARSE and c.duties == [0]   # set, not sent: off
    m.handle(Move("enter"))
    assert not m.heat_on                                                           # Enter on the slider: nothing
    m.handle(Move("up"))
    m.handle(Move("enter"))                                                        # the switch: on, the duty goes
    assert m.heat_on and c.duties == [0, 8.0] and not m.sync()
    m.handle(Move("down"))
    m.handle(Move("left", coarse=True))
    m.handle(Move("left", coarse=True))
    assert m.duty.value == -2 and c.duties[-2:] == [3.0, -2.0]                     # each move, as it's made: cooling
    m.handle(Move("up"))
    m.handle(Move("enter"))
    assert not m.heat_on and c.duties[-1] == 0                                     # off again: 0
    m.handle(Move("enter"))
    assert m.heat_on and c.duties[-1] == -2
    assert not m.back() and not m.inside and not m.heat_on and c.duties[-1] == 0   # out: off, and 0
    m.handle(Move("enter"))
    assert m.inside and not m.heat_on and m.duty.value == -2 and c.duties[-1] == 0  # in again: the duty kept, off
    m.handle(Move("enter"))
    m.close()                                                                      # the app quitting, from inside
    assert not m.is_open and not m.heat_on and c.duties[-1] == 0


def test_temp_calib_within_the_units_cap():
    c = Client()
    c.params["lid.duty_max"] = 20
    m = temp_calib(c)
    m.handle(Move("down"))
    for _ in range(9):
        m.handle(Move("right", coarse=True))
    assert m.duty.value == 20
    for _ in range(12):
        m.handle(Move("left", coarse=True))
    assert m.duty.value == -20
    c.params["lid.duty_max"] = 10                                                  # another unit, a lower cap
    m.handle(Move("up"))
    m.handle(Move("enter"))
    assert m.duty.value == -10 and c.duties[-1] == -10


def test_temp_calib_off_when_the_unit_cuts_or_refuses():
    c = Client()
    m = temp_calib(c)
    m.handle(Move("enter"))
    m.handle(Move("down"))
    m.handle(Move("right", coarse=True))
    assert m.heat_on and c.lid["duty"] == 5
    c.lid = {**c.lid, "duty": 0, "cut": "hot", "mode": "idle"}                     # the board reached 60 C
    c.pending = True
    assert not m.sync() and m.heat_on                                              # not while its reply is out
    c.pending = False
    assert m.sync() and not m.heat_on and "60 °C" in m.heat_note and c.duties[-1] == 0
    m.handle(Move("up"))
    m.handle(Move("enter"))
    assert m.heat_on and m.heat_note == ""
    c.set_errors["lid"] = "'lid duty=5' refused: hot board.a 60.12 C, the limit is 60"
    assert m.sync() and not m.heat_on and m.heat_note.startswith("refused: ")
    c.set_errors.clear()
    m.handle(Move("enter"))
    c.lid = {**c.lid, "duty": 0, "cut": "medium", "mode": "idle"}                  # a probe at lid.medium_max
    c.params["lid.medium_max"] = 37.5
    assert m.sync() and not m.heat_on and m.heat_note == "cut by the unit: a medium probe reached 37.5 °C (lid.medium_max)"
    m.handle(Move("enter"))
    c.lid = {**c.lid, "duty": 30}                                                  # set from elsewhere (the bench)
    assert m.sync() and not m.heat_on and "changed" in m.heat_note
    m.handle(Move("enter"))
    c.connected = False                                                            # the link gone: so is the duty
    assert m.sync() and not m.heat_on and "link" in m.heat_note
    m.handle(Move("enter"))
    assert not m.heat_on and m.heat_note.startswith("not connected")               # and it won't go on meanwhile
    c.connected = True
    m.handle(Move("down"))
    m.handle(Move("left", coarse=True))
    m.handle(Move("up"))
    m.handle(Move("enter"))                                                        # 0 %: on, at nothing
    assert m.heat_on and not m.sync()
    m.handle(Move("down"))
    m.handle(Move("right"))                                                        # 1 %: the unit sets 0
    assert c.lid["duty"] == 0 and not m.sync() and m.heat_on


def test_set_again_after_a_reconnect_only_while_inside():
    c = Client()
    m = Menu(c)
    m.open()
    m.handle(Move("right"))
    m.handle(Move("right", coarse=True))
    m.reapply()
    assert c.lights[-1] == {"warm": 1.0, "cool": 0.0}

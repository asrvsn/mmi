import math

import pytest

from mmi_app.pad import PadState
from mmi_app.inputs import Keyboard
from mmi_app.shaping import Mapping, Shaping, at_rest, axis1d, pad_to_jog, triggers, view


def test_speed_map():
    sh = Shaping(0.08, 2.0, 250.0)
    assert sh.speed(0) == 0 and sh.speed(0.08) == 0
    assert sh.speed(0.0801) == pytest.approx(2.0, rel=0.01)       # v_min just past the deadband
    assert sh.speed(1.0) == pytest.approx(250.0) and sh.speed(1.5) == pytest.approx(250.0)
    mid = sh.speed(0.54)                                           # half way along the live throw
    assert mid == pytest.approx(math.sqrt(2 * 250), rel=0.01)      # geometric mean: ~22 um/s
    xs = [sh.speed(m / 100) for m in range(101)]
    assert xs == sorted(xs)


def test_axis1d():
    sh = Shaping(0.08, 2.0, 250.0)
    assert axis1d(-1.0, sh) == pytest.approx(-250.0) and axis1d(0.05, sh) == 0


def test_each_axis_on_its_own():
    m = Mapping()
    x = pad_to_jog(PadState(connected=True, rx=-0.5), m)[0]
    assert x == pytest.approx(-18.1, abs=0.05)                     # half a deflection
    assert pad_to_jog(PadState(connected=True, rx=-0.5, ry=0.5), m)[:2] == (x, -x)   # y joins in: x as it was
    assert pad_to_jog(PadState(connected=True, rx=1.0, ry=0.06), m)[:2] == (pytest.approx(250), 0)   # a wobble
    kb = Keyboard()                                                # the keys, as the user found them
    kb.press("left")
    alone = pad_to_jog(kb.state(0.5, 1.0), m)
    kb.press("up")
    both = pad_to_jog(kb.state(0.5, 1.0), m)
    assert both[0] == alone[0] and both[1] == -alone[0] and both[2:] == (0, 0)


def test_triggers_drive_the_rail():
    sh = Shaping(0.05, 20.0, 5000.0)
    assert triggers(0, 1, sh) == pytest.approx(5000) and triggers(1, 0, sh) == pytest.approx(-5000)
    assert triggers(1, 1, sh) == 0 and triggers(0.02, 0, sh) == 0


def test_view():
    assert view(1, 0, 0, False) == (1, 0)
    assert view(1, 0, 90, False) == (0, 1)
    assert view(0, 1, 90, False) == (-1, 0)
    assert view(1, 0, 180, True) == (1, 0)


def test_pad_to_jog_and_rest():
    m = Mapping()
    p = PadState(connected=True, rx=1.0, ly=-1.0, rt=1.0)          # right stick right, left stick down
    x, y, z, r = pad_to_jog(p, m)
    assert x == pytest.approx(250) and y == 0 and z == pytest.approx(-250) and r == pytest.approx(5000)
    assert not at_rest(p, m)
    assert at_rest(PadState(connected=True, rx=0.03, ry=-0.02, lx=0.9, lt=0.01), m)   # left stick x is ignored


def test_cap_to_the_units_limits():
    m = Mapping()
    m.cap({"x.vmax": 200, "y.vmax": 150, "z.vmax": 100, "r.vmax": 3000})
    assert (m.xy.v_max, m.z.v_max, m.r.v_max) == (150, 100, 3000)
    m.cap({})
    assert m.xy.v_max == 150

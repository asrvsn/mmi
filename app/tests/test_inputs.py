"""The input profiles: the keyboard as a pad, the 8BitDo taking over when it's attached, any other pad ignored."""
from mmi_app.inputs import Inputs, Keyboard
from mmi_app.pad import PadState
from mmi_app.profiles import EIGHTBITDO, KEYBOARD

EIGHTBITDO_IDS = (0x2DC8, 0x310A)


def state(k: Keyboard) -> PadState:
    return k.state(0.5, 1.0)


def test_keys_as_a_pad():
    k = Keyboard()
    k.press("right")
    k.press("up")
    s = state(k)
    assert (s.rx, s.ry) == (0.5, 0.5) and s.connected and s.name == "keyboard" and s.buttons == frozenset()
    k.press("shift")
    assert (state(k).rx, state(k).ry) == (1.0, 1.0)
    k.press("left")                           # left and right together cancel
    assert state(k).rx == 0
    k.clear()
    assert state(k) == PadState(connected=True, name="keyboard")
    k.press("w")
    assert state(k).ly == 0.5                 # as the left stick's vertical: fine z
    k.press("s")
    assert state(k).ly == 0
    k.press("a")
    k.press("d")
    assert (state(k).lt, state(k).rt) == (0.5, 0.5)   # both triggers: the rail's cancel too
    k.press("space")
    k.press("escape")
    k.press("enter")
    k.press("backspace")
    assert state(k).buttons == {"halt", "menu", "enter", "back"}   # the roles, as the pad's B, HOME and A are
    k.release("backspace")
    k.release("enter")
    k.press("q")                              # not one of ours
    assert "q" not in k.held
    k.release("space")
    assert state(k).buttons == {"menu"}


class Pad:
    def __init__(self, s: PadState, ignored=()):
        self.s, self.ignored = s, ignored

    def poll(self) -> PadState:
        return self.s


def test_the_8bitdo_takes_over_and_the_keyboard_does_nothing():
    k = Keyboard()
    pad = Pad(PadState(connected=True, name="8BitDo Ultimate 2C Wired Controller", ids=EIGHTBITDO_IDS, rx=0.2,
                       rt=0.9, buttons=frozenset({"a", "b", "guide"})))
    i = Inputs(k, pad)
    for key in ("left", "w", "space", "escape", "shift"):   # someone at the keyboard meanwhile
        k.press(key)
    s = i.poll()
    assert i.profile is EIGHTBITDO and s.name == "8BitDo Ultimate 2C"
    assert (s.rx, s.ly, s.rt) == (0.2, 0.0, 0.9)            # the pad's, untouched by the keys
    assert s.buttons == {"halt", "back", "menu", "enter"}   # B (halt; back in the menu), HOME and A as roles
    assert i.profile.hint == "Press HOME for menu" and i.profile.icon == "joystick"
    pad.s = PadState(connected=True, name="8BitDo", ids=EIGHTBITDO_IDS)
    assert i.poll().buttons == frozenset()                  # Space and Esc held: nothing
    pad.s = PadState()                                      # unplugged: the keyboard drives
    s = i.poll()
    assert i.profile is KEYBOARD and s.name == "keyboard" and s.rx == -1.0 and s.buttons == {"halt", "menu"}
    assert i.profile.hint == "Press Esc for menu" and i.profile.icon == "keyboard"


def test_any_other_pad_is_ignored():
    k = Keyboard()
    pad = Pad(PadState(connected=True, name="Xbox Wireless Controller", ids=(0x045E, 0x0B13), rx=1.0,
                       buttons=frozenset({"b"})), ignored=(("Xbox Wireless Controller", (0x045E, 0x0B13)),))
    i = Inputs(k, pad)
    k.press("down")
    s = i.poll()
    assert i.profile is KEYBOARD and s.ry == -0.5 and s.rx == 0 and s.buttons == frozenset()
    assert i.ignored == (("Xbox Wireless Controller", (0x045E, 0x0B13)),)
    assert Inputs(k).poll().name == "keyboard" and Inputs(k).ignored == ()   # no pad reader at all (no pygame)

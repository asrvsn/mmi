"""The input profiles, and the pads known by their USB ids: the 8BitDo and the DualSense."""
from mmi_app.pad import BUTTON_NAMES, fmt_ids, guid_ids
from mmi_app.profiles import DUALSENSE, EIGHTBITDO, KEYBOARD, PADS, pad_ids, pad_profile

ULTIMATE_2C_MAC = "03003919c82d00000a31000014016800"   # as SDL 2.32 reports it on the Mac, 2026-10-21 (USB, HIDAPI)
DUALSENSE_BT = "05001a2b4c050000e60c000000016800"      # SDL's standard form for Bluetooth (bus 5), 054c:0ce6


def test_the_ids_from_sdls_guid():
    assert guid_ids(ULTIMATE_2C_MAC) == (0x2DC8, 0x310A)
    assert fmt_ids(guid_ids(ULTIMATE_2C_MAC)) == "2dc8:310a"
    assert guid_ids("03000000c82d00000a31000000010000") == (0x2DC8, 0x310A)   # an older SDL's: no name CRC
    assert guid_ids("0300000078696e707574010000000000") == (0, 0)           # no ids: the name in their place
    assert guid_ids("not a guid") == (0, 0) and guid_ids("") == (0, 0)
    assert guid_ids(DUALSENSE_BT) == (0x054C, 0x0CE6)                        # by Bluetooth, the same ids


def test_the_8bitdo_the_dualsense_and_nothing_else():
    assert pad_profile((0x2DC8, 0x310A)) is EIGHTBITDO
    assert pad_profile((0x054C, 0x0CE6)) is DUALSENSE and pad_profile((0x054C, 0x0DF2)) is DUALSENSE   # and the Edge
    assert pad_profile((0x045E, 0x028E)) is None                # an Xbox 360 pad: ignored
    assert pad_profile((0x2DC8, 0x6012)) is None                # another 8BitDo: ignored too
    assert pad_profile((0x054C, 0x09CC)) is None                # a PS4 DualShock 4: ignored too
    assert pad_ids() == {(0x2DC8, 0x310A), (0x054C, 0x0CE6), (0x054C, 0x0DF2)} and PADS == (EIGHTBITDO, DUALSENSE)


def test_their_buttons_and_labels():
    assert EIGHTBITDO.roles(frozenset({"b"})) == {"halt", "back"}                    # the menu decides which
    assert EIGHTBITDO.roles(frozenset({"guide", "a", "x"})) == {"menu", "enter"}       # X plays nothing
    assert EIGHTBITDO.roles(frozenset({"misc1"})) == {"menu"}                         # STAR, as HOME
    assert EIGHTBITDO.roles(frozenset({"dpad_up", "dpad_left"})) == {"up", "left"}    # the d-pad points, in the menu
    for p in PADS:
        assert {p.halt_button, p.enter_button, p.back_button, *p.menu_buttons} <= set(BUTTON_NAMES)
        assert p.back_button == p.halt_button and p.enter_button not in (p.halt_button, *p.menu_buttons)
        assert p.icon == "joystick"
    assert DUALSENSE.roles(frozenset({"b", "guide", "a", "dpad_down"})) == {"halt", "back", "menu", "enter", "down"}
    assert DUALSENSE.roles(frozenset({"y", "x", "touchpad", "misc1"})) == frozenset()   # Triangle, Square, ...: nothing
    assert (EIGHTBITDO.hint, DUALSENSE.hint, KEYBOARD.hint) == ("Press HOME for menu", "Press PS for menu",
                                                                "Press Esc for menu")
    assert EIGHTBITDO.menu_hint(False) == "Press HOME to exit menu"                    # in the list
    assert EIGHTBITDO.menu_hint(True) == "B: back, HOME: exit menu"                     # inside an item
    assert DUALSENSE.menu_hint(False) == "Press PS to exit menu"
    assert DUALSENSE.menu_hint(True) == "Circle: back, PS: exit menu"
    assert KEYBOARD.menu_hint(True) == "Backspace: back, Esc: exit menu"
    assert KEYBOARD.icon == "keyboard" and KEYBOARD.roles(frozenset({"b", "guide", "dpad_up"})) == frozenset()

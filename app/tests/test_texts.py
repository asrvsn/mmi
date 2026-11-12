"""The window's two lines, state by state."""
from types import SimpleNamespace

from mmi_client import ZERO, Blocks
from mmi_client.protocol import Hello

from mmi_app.texts import drive_line, lid_line, plain, sd_text, temp_text, unit_line

NEG, POS = frozenset({-1}), frozenset({1})


def client(blocks=None, **kw):
    c = dict(host="127.0.0.1", port=5000, replaced=False, connected=True, hello=Hello(fw="test"), motors="on",
             faults=[], halted=False, awaiting_release=False, sent_vector=ZERO, params={"fine.vunk": 20})
    c.update(kw)
    c["blocks"] = lambda axis: (blocks or {}).get(axis, Blocks())
    return SimpleNamespace(**c)


def test_unit_line():
    assert unit_line(client(connected=False)) == [("DISCONNECTED | 127.0.0.1:5000", "alert")]
    assert unit_line(client()) == [("CONNECTED | 127.0.0.1:5000 | fw test | motors on", "ok")]
    assert plain(unit_line(client(motors="wait"))).endswith("motors wait")
    assert unit_line(client(replaced=True, connected=False))[0][1] == "alert"
    assert unit_line(client(faults=["fault r driver noreply"])) == [
        ("CONNECTED | 127.0.0.1:5000 | fw test | motors on | fault r driver noreply", "alert")]


def test_drive_line():
    assert drive_line(client(connected=False), False) == [("DISABLED", "off")]
    assert drive_line(client(motors="off"), False) == [("DISABLED | motors off", "off")]
    assert drive_line(client(), False) == [("PARKED", "ok")]
    assert drive_line(client(sent_vector=(10, 5, 0, 0)), False) == [("JOG x 10um/s y 5um/s z 0um/s r 0um/s", "busy")]
    assert plain(drive_line(client(sent_vector=(-2.34, 0, 0.04, 1500)), False)) == (
        "JOG x -2.3um/s y 0um/s z 0um/s r 1500um/s")
    assert plain(drive_line(client(sent_vector=(0.04, 0, 0, 0)), False)) == "PARKED"   # below what a jog can say
    assert drive_line(client(halted=True), False) == [("HALTED - let go to resume", "alert")]
    assert drive_line(client(), True) == [("HALTED - let go to resume", "alert")]      # our halt, still held
    assert drive_line(client(awaiting_release=True), False) == [("HELD - let go to resume", "alert")]
    assert drive_line(client(), False, True) == [("HELD - let go to resume", "alert")]   # a handover, still held
    assert plain(drive_line(client(halted=True), False, True)).startswith("HALTED")       # a halt says so first
    assert drive_line(client(sent_vector=(10, 0, 0, 0)), False, menu=True) == [("DISABLED | menu", "off")]
    assert drive_line(client(halted=True), True, True, True) == [("DISABLED | menu", "off")]   # the menu has it
    assert drive_line(client(connected=False), False, menu=True) == [("DISABLED", "off")]   # no link says so first


def test_limit_and_stall_in_red():
    at_min = {"x": Blocks(limit=NEG), "r": Blocks(limit=NEG)}       # the effector's switch: -x on both
    assert drive_line(client(at_min, sent_vector=(-40, 5, 0, -2000)), False) == [
        ("JOG x ", "busy"), ("LIMIT", "alert"), (" y 5um/s z 0um/s r ", "busy"), ("LIMIT", "alert")]
    assert drive_line(client(at_min, sent_vector=(40, 0, 0, 2000)), False) == [   # away from it: as usual
        ("JOG x 40um/s y 0um/s z 0um/s r 2000um/s", "busy")]
    assert drive_line(client(at_min, sent_vector=(0, 5, 0, 0)), False) == [         # not asked that way: nothing
        ("JOG x 0um/s y 5um/s z 0um/s r 0um/s", "busy")]
    stalled = {"z": Blocks(stall=POS)}
    assert plain(drive_line(client(stalled, sent_vector=(0, 0, 30, 0)), False)) == "JOG x 0um/s y 0um/s z STALL r 0um/s"
    unknown = {"y": Blocks(unknown=True)}                           # its switch open, the end unknown: vunk at most
    assert plain(drive_line(client(unknown, sent_vector=(0, -250, 0, 0)), False)) == (
        "JOG x 0um/s y -20um/s? z 0um/s r 0um/s")
    assert plain(drive_line(client(unknown, sent_vector=(0, 8, 0, 0)), False)) == "JOG x 0um/s y 8um/s? z 0um/s r 0um/s"


def test_temp_calib_texts():
    assert temp_text(24.314) == ("24.31 °C", "ok") and temp_text(57.0, board=True) == ("57.00 °C", "alert")
    assert temp_text(57.0) == ("57.00 °C", "ok")                       # only the board is near its cut
    assert temp_text("open") == ("open", "off") and temp_text("open", board=True) == ("open", "alert")
    assert temp_text("short") == ("short", "alert") and temp_text(None) == ("-", "off")
    assert temp_text(39.2, warn=39.0) == ("39.20 °C", "alert") and temp_text(38.9, warn=39.0)[1] == "ok"   # a probe
    assert sd_text(0.004) == "sd 4 mK" and sd_text(0.0) == "sd 0 mK" and sd_text(1.234) == "sd 1.2 K"
    assert sd_text(None) == "" and sd_text("open") == ""
    menu = SimpleNamespace(heat_on=False, heat_note="")
    lid = dict(sw="on", mode="idle", duty=0, cut=0)
    assert lid_line(client(connected=False, lid={}), menu) == ("the unit: not connected", "off")
    assert lid_line(client(lid={}), menu) == ("the lid: not said yet", "off")
    assert lid_line(client(lid=lid), menu) == ("the lid: off", "off")
    assert lid_line(client(lid={**lid, "mode": "manual", "duty": 20}), menu) == ("the lid: heating at 20 %", "busy")
    assert lid_line(client(lid={**lid, "mode": "manual", "duty": -12.5}), menu) == ("the lid: cooling at 12.5 %", "busy")
    assert lid_line(client(lid={**lid, "sw": "off", "mode": "manual", "duty": 20}), menu) == (
        "LID HEAT (SW10) is off, nothing driven: 20 % held", "off")
    assert lid_line(client(lid={**lid, "mode": "absent"}), menu)[1] == "alert"
    assert lid_line(client(lid={**lid, "mode": "fault"}), menu) == ("a sensor reads wrong: the lid can't be driven",
                                                                     "alert")
    cut = SimpleNamespace(heat_on=False, heat_note="cut by the unit: a board NTC reached 60 °C")
    assert lid_line(client(lid={**lid, "cut": "hot"}), cut) == (cut.heat_note, "alert")   # why the switch went off

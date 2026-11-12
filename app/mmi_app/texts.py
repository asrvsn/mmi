"""The window's two lines, from the client's state: plain functions, so they're tested without a window.

Each line is a list of segments, (text, level); the level picks the colour, in the unit LED's code: ok green, busy
blue, alert red, off grey. Most lines are one segment. A jog's names, in red, an axis asked to go where the unit won't
let it: LIMIT (a limit switch) or STALL. That comes from the client's mirror of the unit's limits and stalls
(api.md 6.5), which the unit keeps up to date by itself: nothing is asked for per jog.
"""
from __future__ import annotations

import math

from mmi_client.protocol import fmt_speed

VUNK = 20.0   # um/s: fine.vunk's default, if the unit hasn't said


def plain(line: list[tuple[str, str]]) -> str:
    return "".join(text for text, _ in line)


def unit_line(c) -> list[tuple[str, str]]:
    """`CONNECTED | 127.0.0.1:5000 | fw test | motors on`, or `DISCONNECTED | 192.168.50.2:5000`."""
    addr = f"{c.host}:{c.port}"
    if c.replaced:
        return [(f"REPLACED | another client has the unit at {addr}", "alert")]
    if not c.connected:
        return [(f"DISCONNECTED | {addr}", "alert")]
    parts = ["CONNECTED", addr]
    if c.hello is not None:
        parts.append(f"fw {c.hello.fw}")
    parts.append(f"motors {c.motors}")
    if c.faults:
        parts.append(c.faults[-1])
        return [(" | ".join(parts), "alert")]
    return [(" | ".join(parts), "ok")]


def drive_line(c, holding: bool, settling: bool = False, menu: bool = False) -> list[tuple[str, str]]:
    """What the drive is being told: DISABLED, PARKED, JOG with the vector being sent, HALTED or HELD. `holding`: a
    halt held until let go (hand.py); `settling`: a handover from one device to another, held the same way; `menu`:
    the menu has the controls, so nothing drives.

    In a JOG, an axis sent into a half-space the unit blocks reads LIMIT or STALL, in red, instead of its speed. One
    whose own switch is open at an end the unit can't tell shows the speed it will run at, no more than fine.vunk,
    with a `?`."""
    if not c.connected:
        return [("DISABLED", "off")]
    if c.motors != "on":
        return [(f"DISABLED | motors {c.motors}", "off")]
    if menu:
        return [("DISABLED | menu", "off")]
    if c.halted or holding:
        return [("HALTED - let go to resume", "alert")]
    if c.awaiting_release or settling:
        return [("HELD - let go to resume", "alert")]
    v = c.sent_vector
    if all(fmt_speed(a) == "0" for a in v):
        return [("PARKED", "ok")]
    vunk = c.params.get("fine.vunk", VUNK)
    vunk = float(vunk) if isinstance(vunk, (int, float)) else VUNK
    out = [("JOG", "busy")]
    for name, a in zip("xyzr", v):
        b = c.blocks(name)
        why = b.why(a) if fmt_speed(a) != "0" else None
        if why:
            out += [(f" {name} ", "busy"), (why.upper(), "alert")]
        elif b.unknown and fmt_speed(a) != "0":
            out.append((f" {name} {fmt_speed(math.copysign(min(abs(a), vunk), a))}um/s?", "busy"))
        else:
            out.append((f" {name} {fmt_speed(a)}um/s", "busy"))
    return _merged(out)


SENSORS = (("Board", (("A", "board.a"), ("B", "board.b"), ("edge", "board.edge"))),
           ("Medium", (("J2", "med.j2"), ("J3", "med.j3"))))   # Temp calib.'s list: the lid's five NTCs (api.md 9.2)
BOARD_MAX = 60.0     # °C: the unit cuts the duty at a board NTC this hot
HOT = 55.0           # °C: a board reading this close to it shows in red
MEDIUM_MAX = 40.0    # lid.medium_max's default, until the unit has said its own
MEDIUM_WARN = 1.0    # K: a probe reading this close to lid.medium_max shows in red


def temp_text(v, board: bool = False, warn: float | None = None) -> tuple[str, str]:
    """One of the lid's temperatures as listed (a readout's mean): `24.31 °C`, in red from `warn` up (a board's: HOT);
    `open` (on a probe: not fitted), `short`; `-` (not said)."""
    if warn is None and board:
        warn = HOT
    if isinstance(v, (int, float)):
        return f"{v:.2f} °C", "alert" if warn is not None and v >= warn else "ok"
    if v is None:
        return "-", "off"
    return str(v), "off" if v == "open" and not board else "alert"


def sd_text(v) -> str:
    """A readout's SD, listed under its mean: `sd 4 mK`, `sd 1.2 K`; nothing without one."""
    if not isinstance(v, (int, float)):
        return ""
    return f"sd {v * 1000:.0f} mK" if v < 1 else f"sd {v:.1f} K"


def lid_line(c, menu) -> tuple[str, str]:
    """Temp calib.'s last line: what the unit's lid is doing, or why the switch went off."""
    if not c.connected:
        return "the unit: not connected", "off"
    if menu.heat_note and not menu.heat_on:
        return menu.heat_note, "alert"
    lid = c.lid
    mode, duty = lid.get("mode"), lid.get("duty", 0)
    if not mode:
        return "the lid: not said yet", "off"
    if mode == "absent":
        return "the lid isn't answering: is it plugged in?", "alert"
    if mode == "fault":
        return "a sensor reads wrong: the lid can't be driven", "alert"
    held = f": {abs(duty):g} % held" if duty else ""
    if lid.get("sw") == "off":
        return f"LID HEAT (SW10) is off, nothing driven{held}", "off"
    if duty:
        return f"the lid: {'heating' if duty > 0 else 'cooling'} at {abs(duty):g} %", "busy"
    return "the lid: off", "off"


def _merged(line: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for text, level in line:
        if out and out[-1][1] == level:
            out[-1] = (out[-1][0] + text, level)
        else:
            out.append((text, level))
    return out

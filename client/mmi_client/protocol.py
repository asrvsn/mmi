"""The unit's line protocol (mmi/firmware/api.md), as pure functions and data: no sockets, no threads.

Server lines start with one character that says what they are:
    @<tag> ok [key=value ...]        a reply, exactly one per command (except jog)
    @<tag> err <code> [text]
    #<tag> <data>                    a line of a multi-line answer, just before its @
    !<name> [args] [key=value ...]   an event, at any time
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

API_MAJOR = 0          # the major version this client speaks (api.md: a major change breaks compatibility)
API_MIN_MINOR = 4      # jog + halt; `get` from 0.5; the limit and stall state after !hello from 0.8; `light` from 0.9;
                       # `lid` from 0.10; its readouts (`!lid` once a second, unwatched) from 0.11
LIGHT_STEPS = 4096     # the lights' set-points are 12-bit: 100 % is step 4096, 0.024 % a step (api.md 9.1)
LID_STEPS = 4096       # the lid's PWM is 12-bit too: a duty is set to the nearest 0.024 % (api.md 9.2)
LID_FLOOR = 2.0        # %: under it, the unit sets 0

_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")
_INT = re.compile(r"^[+-]?\d+$")


def split_words(text: str) -> list[str]:
    """Splits on spaces, keeping a double-quoted value (key="lab one") as one word, without its quotes."""
    words, cur, quoted, any_char = [], [], False, False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            any_char = True
        elif ch == " " and not quoted:
            if any_char:
                words.append("".join(cur))
            cur, any_char = [], False
        else:
            cur.append(ch)
            any_char = True
    if any_char:
        words.append("".join(cur))
    return words


def typed(value: str) -> Any:
    """'12' -> 12, '0.5' -> 0.5, '0,70,260' -> [0.0, 70.0, 260.0], anything else stays a string."""
    if _INT.match(value):
        return int(value)
    if _NUMBER.match(value):
        return float(value)
    if "," in value:
        parts = value.split(",")
        if all(_NUMBER.match(p) for p in parts):
            return [float(p) for p in parts]
    return value


def plain_number(text: str) -> float | None:
    """A number as the unit reads one (api.md 3): a plain decimal, an optional sign, no exponent; else None."""
    return float(text) if _NUMBER.match(text) else None


def parse_values(words: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Positional words, and key=value pairs (typed)."""
    pos, kv = [], {}
    for w in words:
        k, eq, v = w.partition("=")
        if eq and k:
            kv[k] = typed(v)
        else:
            pos.append(w)
    return pos, kv


@dataclass
class Reply:
    tag: str
    ok: bool
    code: str = ""                                     # the error's code, e.g. "unknown", "arg", "motors-off"
    text: str = ""                                     # everything after ok / err <code>
    values: dict[str, Any] = field(default_factory=dict)
    info: list[str] = field(default_factory=list)     # the # lines that came before it


@dataclass
class Event:
    name: str                                          # "limit", "halt", "drive", "motors", ...
    args: list[str]
    values: dict[str, Any]
    raw: str                                           # the line without its "!"


@dataclass
class Info:
    tag: str
    text: str


def parse_line(line: str) -> Reply | Event | Info | None:
    """One server line, without its newline. None for anything that isn't the protocol."""
    line = line.rstrip("\r\n")
    if not line:
        return None
    kind, body = line[0], line[1:]
    if kind == "@":
        tag, _, rest = body.partition(" ")
        word, _, tail = rest.partition(" ")
        if word == "ok":
            _, values = parse_values(split_words(tail))
            return Reply(tag=tag, ok=True, text=tail, values=values)
        if word == "err":
            code, _, text = tail.partition(" ")
            return Reply(tag=tag, ok=False, code=code, text=text)
        return None
    if kind == "#":
        tag, _, text = body.partition(" ")
        return Info(tag=tag, text=text)
    if kind == "!":
        words = split_words(body)
        if not words:
            return None
        pos, values = parse_values(words[1:])
        return Event(name=words[0], args=pos, values=values, raw=body)
    return None


def nest(values: dict[str, Any]) -> dict[str, Any]:
    """{'x.mode': 'jog', 't': 5} -> {'x': {'mode': 'jog'}, 't': 5}."""
    out: dict[str, Any] = {}
    for key, v in values.items():
        node = out
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    return out


def _s(v: Any) -> str:
    return str(v)


def halves(text: str) -> tuple[frozenset, bool]:
    """A limit or stall state as the unit writes it ('0', '-', '+', '-+', '?', '-?', ...): the directions it blocks
    (-1, +1), and whether a fine axis's switch is open at an end the unit can't tell ('?')."""
    return frozenset(d for c, d in (("-", -1), ("+", 1)) if c in text), "?" in text


@dataclass(frozen=True)
class Blocks:
    """What stops one axis, as the unit reports it (api.md 6.5): the directions (-1, +1) its limit switches block, the
    effector's on fine x included; those a stall blocks; and `unknown`, a fine axis's own switch open at an end the
    unit can't tell, so both ways run at no more than fine.vunk."""
    limit: frozenset = frozenset()
    stall: frozenset = frozenset()
    unknown: bool = False

    def why(self, v: float) -> str | None:
        """Why a velocity this way can't run: 'limit', 'stall', or None if it can."""
        d = 1 if v > 0 else -1 if v < 0 else 0
        if d in self.limit:
            return "limit"
        if d in self.stall:
            return "stall"
        return None


@dataclass
class AxisState:
    mode: str = "off"
    v: float = 0.0         # µm/s commanded now (after ramps and clamps)
    d: float = 0.0         # duty driven (fine axes)
    lim: str = "0"         # what the limit switches block: 0, -, +, -+, then ? for an unknown end (api.md 6.5)
    blk: str = "0"         # every block: 0, +, -, -+
    i: float = 0.0         # A (fine axes)
    cal: int = 0


@dataclass
class RailState(AxisState):
    pos: float = 0.0       # µm counted since the unit's power-up
    homed: int = 0
    drv: str = "off"


@dataclass
class DriveState:
    """The drive record (api.md 6.6), typed."""
    t: int = 0
    motors: str = "off"
    halt: bool = False
    x: AxisState = field(default_factory=AxisState)
    y: AxisState = field(default_factory=AxisState)
    z: AxisState = field(default_factory=AxisState)
    r: RailState = field(default_factory=RailState)

    @classmethod
    def from_values(cls, values: dict[str, Any]) -> "DriveState":
        n = nest(values)

        def axis(d: dict[str, Any], rail: bool = False) -> AxisState:
            a = RailState() if rail else AxisState()
            for k, v in d.items():
                if not hasattr(a, k) or isinstance(v, (list, dict)):
                    continue
                cur = getattr(a, k)
                setattr(a, k, str(v) if isinstance(cur, str) else float(v) if isinstance(cur, float) else int(v))
            return a

        return cls(
            t=int(n.get("t", 0)),
            motors=_s(n.get("motors", "off")),
            halt=bool(n.get("halt", 0)),
            x=axis(n.get("x", {})),
            y=axis(n.get("y", {})),
            z=axis(n.get("z", {})),
            r=axis(n.get("r", {}), rail=True),  # type: ignore[arg-type]
        )


@dataclass
class Hello:
    api: str = ""
    fw: str = ""
    unit: str = ""
    reset: str = ""
    cfg: str = ""
    clock: str = ""
    motors: str = ""
    halt: bool = False     # a halt holds (e.g. the previous client was lost mid-move): the next jog must be all zero

    @property
    def api_version(self) -> tuple[int, int]:
        major, _, minor = self.api.partition(".")
        try:
            return int(major), int(minor or 0)
        except ValueError:
            return (-1, -1)

    @classmethod
    def from_event(cls, ev: Event) -> "Hello":
        # As written, not typed: api=0.10 is version 0.10, not the number 0.1.
        v = dict(w.split("=", 1) for w in split_words(ev.raw)[1:] if "=" in w)
        h = cls(**{k: v.get(k, "") for k in ("api", "fw", "unit", "reset", "cfg", "clock", "motors")})
        h.halt = v.get("halt", "0") == "1"
        return h


def fmt_speed(v: float) -> str:
    """A velocity as the protocol wants it: a plain decimal, 0.1 µm/s steps, no '-0'."""
    q = round(float(v), 1)
    if q == 0:
        return "0"
    s = f"{q:.1f}"
    return s[:-2] if s.endswith(".0") else s


def fmt_level(v: float) -> str:
    """A light level as the protocol writes it: to 0.01 %, without trailing zeros ('39.99', '12.5', '0')."""
    s = f"{float(v):.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def format_light(warm: float | None = None, cool: float | None = None) -> str:
    """'light warm=40 cool=12.5' (api.md 9.1): the channels given, one at least; one left out keeps its level."""
    parts = [f"{k}={fmt_level(v)}" for k, v in (("warm", warm), ("cool", cool)) if v is not None]
    if not parts:
        raise ValueError("light: warm, cool or both")
    return " ".join(["light"] + parts)


def format_lid(duty: float) -> str:
    """'lid duty=30' (api.md 9.2): % of full drive, signed (+ heats); 0 is off."""
    return f"lid duty={fmt_level(duty)}"


def format_jog(x: float = 0.0, y: float = 0.0, z: float = 0.0, r: float = 0.0) -> str:
    """'jog x=40 y=-12.5' (axes at 0 left out; all at 0 is just 'jog')."""
    parts = ["jog"]
    for name, v in (("x", x), ("y", y), ("z", z), ("r", r)):
        s = fmt_speed(v)
        if s != "0":
            parts.append(f"{name}={s}")
    return " ".join(parts)

"""From a gamepad's state to a jog vector in µm/s (api.md 8): deadbands, an exponential speed map, and the view.

- Right stick: fine x and y.
- Left stick, vertical: fine z (up = +z). Its horizontal is ignored.
- Triggers: the rail (left -x, right +x).

**Every axis is shaped on its own:** its own deadband, and its speed from its own deflection only. So one never
changes another: holding the stick right (or the right arrow) and adding a little up leaves x's speed as it was, and a
thumb's wobble inside the deadband doesn't drift the other axis. (A radial map, speed from the stick's whole
deflection, makes x jump as y joins in: on the keys, from 18 to 38 um/s.)

The map is exponential: fine control near the centre, full speed at the end.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .pad import PadState


@dataclass
class Shaping:
    deadband: float = 0.08    # fraction of the throw that does nothing
    v_min: float = 2.0        # µm/s just past the deadband
    v_max: float = 250.0      # µm/s at full deflection

    def speed(self, m: float) -> float:
        """A deflection 0..1 to µm/s: v_min just past the deadband, rising exponentially to v_max at the end."""
        m = min(abs(m), 1.0)
        if m <= self.deadband or self.v_max <= 0:
            return 0.0
        f = (m - self.deadband) / (1.0 - self.deadband)
        if not 0 < self.v_min < self.v_max:
            return self.v_max * f
        return self.v_min * (self.v_max / self.v_min) ** f


@dataclass
class Mapping:
    xy: Shaping = field(default_factory=lambda: Shaping(0.08, 2.0, 250.0))
    z: Shaping = field(default_factory=lambda: Shaping(0.08, 2.0, 250.0))
    r: Shaping = field(default_factory=lambda: Shaping(0.05, 20.0, 5000.0))
    rotation: int = 0         # degrees (0, 90, 180, 270): turns the stick's directions onto the unit's x and y
    mirror: bool = False      # then flips x

    def cap(self, params: dict) -> None:
        """Keeps full deflection within what the unit allows (its x/y/z/r.vmax, from `get`)."""
        def num(key: str) -> float | None:
            v = params.get(key)
            return float(v) if isinstance(v, (int, float)) else None
        xy = [v for v in (num("x.vmax"), num("y.vmax")) if v]
        if xy:
            self.xy.v_max = min(self.xy.v_max, *xy)   # the same for both, so x and y answer the stick alike
        for sh, key in ((self.z, "z.vmax"), (self.r, "r.vmax")):
            v = num(key)
            if v:
                sh.v_max = min(sh.v_max, v)


def axis1d(v: float, sh: Shaping) -> float:
    return math.copysign(sh.speed(v), v) if abs(v) > sh.deadband else 0.0


def triggers(lt: float, rt: float, sh: Shaping) -> float:
    return sh.speed(rt) - sh.speed(lt)


def view(x: float, y: float, rotation: int, mirror: bool) -> tuple[float, float]:
    a = math.radians(rotation % 360)
    c, s = round(math.cos(a), 12), round(math.sin(a), 12)
    x, y = c * x - s * y, s * x + c * y
    return (-x if mirror else x), y


def pad_to_jog(pad: PadState, m: Mapping) -> tuple[float, float, float, float]:
    sx, sy = axis1d(pad.rx, m.xy), axis1d(pad.ry, m.xy)   # the right stick: fine x and y, each on its own
    x, y = view(sx, sy, m.rotation, m.mirror)
    return x, y, axis1d(pad.ly, m.z), triggers(pad.lt, pad.rt, m.r)   # the left stick's vertical: fine z


def at_rest(pad: PadState, m: Mapping) -> bool:
    """Nothing deflected past its deadband: the operator has let go."""
    return pad_to_jog(pad, m) == (0.0, 0.0, 0.0, 0.0)

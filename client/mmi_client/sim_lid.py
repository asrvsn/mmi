"""The lid, for the test server (api.md 9.2), as the firmware has it:
- `lid duty=<%>` / `lid off`, strict: capped at lid.duty_max, set to the nearest of 4096 steps, under 2 % none;
- each NTC sampled 16 times a second, and read out once a second: the mean, and the SD about the window's line;
- cut by the unit at a 60 °C board, a medium probe at lid.medium_max, a sensor that reads wrong (a probe open from
  the lid's first readout is only not fitted; one gone open since, or shorted, is wrong), or the lid unplugged, and
  refused while that lasts;
- held off (the duty kept) while SW10 is off;
- dropped when the session that set it ends.

Behind it, the lumped thermal model the lid was designed with (without its
material fits): the board (the chamber side), the top plate and the medium, the Peltier string pumping between board
and plate, in real time. Half duty takes the board ~30 K over the room in an hour or so; the medium follows it far
behind. Each sample carries ~4 mK of noise, about an ADC code's worth.
"""
from __future__ import annotations

import math
import random

from .protocol import LID_FLOOR, LID_STEPS, fmt_level, plain_number

NAMES = ("board.a", "board.b", "board.edge", "med.j2", "med.j3")
BOARD = NAMES[:3]
BOARD_MAX = 60.0          # °C: a board NTC at this cuts the duty
CEIL = 80.0               # %: lid.duty_max can't pass it
V33 = 3.3
WINDOW = 16               # samples a readout
READOUT_S = 1.0           # a readout a second
NOISE = 0.004             # °C rms a sample

# The model's constants: the string (TES1-4902 x 6), its drive, the couplings (W/K) and heat capacities (J/K)
T0 = 273.15
S, R, K, VB, RS = 0.118, 13.7, 0.41, 9.16, 0.70
G_BM, G_BR, G_CAV, G_MR, G_PR = 0.040, 0.015, 0.040, 0.15, 0.09
C_B, C_P, C_M = 41.0, 40.0, 155.0


def duty_code(pct: float, cap: float) -> int:
    """As the firmware's lidCode(): the nearest step of |pct|; under the floor 0; never past the cap or the ceiling."""
    a = abs(pct)
    if not a >= LID_FLOOR:
        return 0
    top = math.floor(min(cap, CEIL) * LID_STEPS / 100 + 1e-3)
    return min(math.floor(a * LID_STEPS / 100 + 0.5), top)


def window_stats(xs: list[float]) -> tuple[float, float]:
    """As the firmware's LidStat: the mean of a window's samples (one a slot), and their SD about its straight line."""
    n = len(xs)
    xm = sum(xs) / n
    if n < 3:
        return xm, 0.0
    um = (n - 1) / 2
    suu = sum((u - um) ** 2 for u in range(n))
    sux = sum((u - um) * (x - xm) for u, x in enumerate(xs))
    ss = sum((x - xm) ** 2 for x in xs) - sux * sux / suu
    return xm, math.sqrt(max(ss, 0.0) / (n - 2))


class Lid:
    def __init__(self, params: dict[str, str], room: float = 22.0, seed: int = 7, readout_s: float = READOUT_S):
        self.params = params                 # the server's, as `get` sends them: lid.duty_max, lid.medium_max
        self.room = room
        self.readout_s = readout_s
        self.board = self.plate = self.medium = room   # °C
        self.sw = True                       # SW10, LID HEAT
        self.plugged = True
        self.present = False                 # a readout made since the lid last answered
        self.broken: dict[str, str] = {}     # a sensor that reads "open" or "short"
        self.fitted: set[str] = set()        # the probes that have read since the lid was plugged in
        self.v33 = V33
        self.code, self.sign = 0, 1          # the duty in force: its 12-bit step and its sign (+ heats)
        self.owned = False                   # set by the session that's connected (the server has one at a time)
        self.cut = "0"                       # why the unit itself last set it to 0, since the last command
        self.fault = self.hot = self.warm = ""   # what's wrong, as `err fault` / `err hot` / `err medium` say it
        self.readings: dict[str, tuple[float, float] | str] = {}   # the last readout: (mean, SD), or open / short
        self.read_at = 0                     # ms: when
        self.fresh = False                   # a readout not sent yet
        self._rng = random.Random(seed)
        self._since = 0.0                    # s since the last sample
        self.restart_window()
        for _ in range(WINDOW):              # up and read before anyone connects
            self._sample()
        self._readout(0)

    # ---- the state
    @property
    def cap(self) -> float:
        v = plain_number(str(self.params.get("lid.duty_max", "50")))
        return 50.0 if v is None else v

    @property
    def medium_max(self) -> float:
        v = plain_number(str(self.params.get("lid.medium_max", "40")))
        return 40.0 if v is None else v

    @property
    def duty(self) -> float:
        return self.sign * self.code * 100 / LID_STEPS if self.code else 0.0

    def mode(self) -> str:
        return "absent" if not self.present else "fault" if self.fault else "manual" if self.code else "idle"

    def state(self) -> tuple:
        """What clients mirror: a change goes out as !lid at once (and every readout anyway)."""
        return self.sw, self.mode(), self.code, self.sign, self.cut

    def record(self, t_ms: int) -> str:
        """The last readout and the state now; t is the readout's (while there's one), else t_ms."""
        head = (f"t={self.read_at if self.present else t_ms} sw={'on' if self.sw else 'off'} mode={self.mode()} "
                f"duty={fmt_level(self.duty)} cut={self.cut}")
        if not self.present:
            return head
        vals = " ".join(f"{k}={v}" if isinstance(v, str) else f"{k}={v[0]:.3f} {k}.sd={v[1]:.3f}"
                        for k, v in self.readings.items())
        return f"{head} {vals} v33={self.v33:.3f}"

    # ---- the command (api.md 9.2)
    def command(self, args: list[str]) -> str:
        """`lid duty=<%>` or `lid off`: the reply, without its tag. Refused, nothing changes."""
        if len(args) != 1:
            return "err arg lid duty=<%> or lid off"
        a = args[0]
        if a == "off":
            pct: float | None = 0.0
        elif a.startswith("duty="):
            pct = plain_number(a[5:])
            if pct is None:
                return "err arg not a number"
        else:
            return "err arg unknown key (duty=)"
        if abs(pct) > self.cap:
            c = fmt_level(self.cap)
            return f"err range duty -{c} to {c} (lid.duty_max)"
        code = duty_code(pct, self.cap)
        if code:
            if not self.present:
                return "err absent the lid isn't answering"
            if self.fault:
                return f"err fault {self.fault}"
            if self.hot:
                return f"err hot {self.hot}"
            if self.warm:
                return f"err medium {self.warm}"
        self.code, self.sign = code, -1 if code and pct < 0 else 1
        self.owned = bool(code)
        self.cut = "0"
        return f"ok duty={fmt_level(self.duty)}"

    def lost(self, cause: str) -> bool:
        """The session has ended: a duty it set goes, cut=<cause>. True if there was one."""
        if not (self.code and self.owned):
            return False
        self._cut(cause)
        return True

    # ---- the world, for tests and the console
    def unplug(self) -> None:
        self.plugged = self.present = False
        self.fitted.clear()
        if self.code:
            self._cut("absent")
        self.restart_window()

    def plug(self) -> None:
        self.plugged = True                  # read out a second later, as the firmware's first window
        self.restart_window()

    def restart_window(self) -> None:
        """The samples so far dropped, as if the temperatures had been where they are now all along."""
        self._win: dict[str, list[float]] = {k: [] for k in NAMES}
        self._worst: dict[str, str] = {}
        self._slot = 0

    # ---- time
    def step(self, dt: float, t: float = 0.0) -> None:
        """dt s on, at t s (the server's clock: the readouts' t)."""
        driving = self.code and self.sw and self.plugged
        self._heat(dt, self.code / LID_STEPS if driving else 0.0, self.sign)
        if not self.plugged:
            return
        self._since += dt
        poll = self.readout_s / WINDOW
        while self._since >= poll:
            self._since -= poll
            self._sample()
            if self._slot == WINDOW:
                self._readout(int(t * 1000))

    def _cut(self, why: str) -> None:
        self.code, self.sign, self.owned, self.cut = 0, 1, False, why

    def _sample(self) -> None:
        rise = self.board - self.room
        true = {"board.a": self.board + 0.15 + 0.02 * rise, "board.b": self.board + 0.10,
                "board.edge": self.board - 0.05 * rise, "med.j2": self.medium + 0.05, "med.j3": self.medium - 0.05}
        for k, v in true.items():
            if k in self.broken:
                self._worst.setdefault(k, self.broken[k])
            else:
                self._win[k].append(v + self._rng.gauss(0.0, NOISE))
        self._slot += 1

    def _readout(self, t_ms: int) -> None:
        """The window's means and SDs, then the cuts (as the firmware's readout())."""
        self.readings = {k: self._worst[k] if k in self._worst else window_stats(self._win[k]) for k in NAMES}
        self.fault = self.hot = self.warm = ""
        if abs(self.v33 - V33) > 0.05 * V33:
            self.fault = f"v33 {self.v33:.3f} V"
        for k in NAMES:
            v = self.readings[k]
            bad = ((isinstance(v, str) or not 0 <= v[0] <= 100) if k in BOARD
                   else v == "short" or (v == "open" and k in self.fitted))
            if bad and not self.fault:
                self.fault = f"{k} {v}" if isinstance(v, str) else f"{k} {v[0]:.2f} C"
            if k not in BOARD and not isinstance(v, str):
                self.fitted.add(k)
        if not self.fault:
            k = max(BOARD, key=lambda n: self.readings[n][0])
            if self.readings[k][0] >= BOARD_MAX:
                self.hot = f"{k} {self.readings[k][0]:.2f} C, the limit is {BOARD_MAX:.0f}"
            probes = [n for n in NAMES[3:] if not isinstance(self.readings[n], str)]
            if probes:
                k = max(probes, key=lambda n: self.readings[n][0])
                if self.readings[k][0] >= self.medium_max:
                    self.warm = (f"{k} {self.readings[k][0]:.2f} C, the limit is {fmt_level(self.medium_max)} "
                                 f"(lid.medium_max)")
        self.present, self.read_at, self.fresh = True, t_ms, True
        if self.code and self.fault:
            self._cut("fault")
        elif self.code and self.hot:
            self._cut("hot")
        elif self.code and self.warm:
            self._cut("medium")
        self.restart_window()

    def _heat(self, dt: float, d: float, s: int) -> None:
        """The model's rhs, coast PWM in one direction (s = +1 pumps heat into the board), Euler."""
        tb, tp, tm, tr = self.board + T0, self.plate + T0, self.medium + T0, self.room + T0
        dt_bp = tb - tp
        on = (s * VB - S * dt_bp) / (R + RS)        # the string's current during a pulse
        i_av, i2, p_out = d * on, d * on * on, d * s * VB * on
        q_b = S * i_av * tb + 0.5 * R * i2 - K * dt_bp
        q_p = S * i_av * tp - 0.5 * R * i2 - K * dt_bp
        p_buck = 0.10 + 0.11 * max(p_out, 0.0)      # the lid's own buck, on the board
        self.board += (q_b + i2 * RS + p_buck - G_BM * (tb - tm) - G_BR * (tb - tr) - G_CAV * dt_bp) / C_B * dt
        self.plate += (-q_p - G_PR * (tp - tr) + G_CAV * dt_bp) / C_P * dt
        self.medium += (G_BM * (tb - tm) - G_MR * (tm - tr)) / C_M * dt

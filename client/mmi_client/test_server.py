"""A test server: a stand-in for the stage-top unit at the protocol level (api.md 0.11), for the tests and for trying a
client without the hardware.

    python -m mmi_client.test_server                  # on 127.0.0.1:5000, then point a client (mmi_app) at it
    python -m mmi_client.test_server 0.0.0.0:5000     # reachable from other machines too

It keeps the rules a client sees from the real unit:
- the hello;
- the 250 ms jog lease: a non-zero jog not renewed in time halts, held until an all-zero jog;
- halt, held until an all-zero jog;
- the session lost while its jog is in force (closed, replaced, the cable pulled): a halt, held the same way;
- `!jog refused` once per reason;
- `get`, `status drive`, `watch drive`;
- the newest connection replacing the old;
- **the limit switches** (sim_stage.py): the stage's travel and switches as built, the end of a fine axis's trip
  inferred from its motion, the effector's switch (MIN) blocking fine x too; `!limit` and `!stall` whenever their
  state changes, and the whole state after every `!hello`;
- **the lights** (api.md 9.1): `light [warm=<%>] [cool=<%>]`, strict as the firmware, set to its 12-bit steps; dark
  at the start; `!light` after every `!hello` and on every change; `status light`, `watch light`;
- **the lid by hand** (api.md 9.2, sim_lid.py): `lid duty=<%>` / `lid off`, strict, capped at lid.duty_max; its cuts
  (a 60 C board, a medium probe at lid.medium_max, a bad sensor, the lid unplugged), SW10, and the duty dropped with
  its session; the five temperatures from the lid's thermal model, read out once a second (each a mean and an SD);
  `!lid` after every `!hello`, with every readout and on every change; `status lid` (it isn't watched).

Beyond the firmware: **every command accepted,** run from the command line. Anything else gets `@<tag> ok`, as though
the unit had it. `--strict` (and the class's default) answers `err unknown`, as the firmware does.

Motion is crude otherwise: no ramps, so positions are velocity x time, up to the switches.

Run from the command line, it also mirrors the stage into Fusion 360 whenever Fusion is up with mmi and its
stage joints open (fusion_mirror.py; --no-fusion to stop it, --fusion-hz to cap its rate).
"""
from __future__ import annotations

import argparse
import math
import signal
import socket
import threading
import time

from .client import parse_addr
from .protocol import LIGHT_STEPS, fmt_level, plain_number
from .sim_lid import Lid
from .sim_stage import START as STAGE_START
from .sim_stage import Stage
from .sim_stage import token as sim_token

RECORDS = ("drive", "light", "lid")   # status <topic>
WATCHED = ("drive", "light")          # watch <topic> <Hz>: the lid's comes by itself, with every readout


def default_params() -> dict[str, str]:
    """The firmware's built-in defaults, as `get` sends them (mmi/firmware/sd/parameters.yaml)."""
    p = {"format": "1", "unit": "unset", "fine.pwm_hz": "40000", "fine.stall_i": "0.7", "fine.stall_k": "0.6",
         "fine.stall_floor": "0.3", "fine.stall_ms": "50", "fine.inrush_ms": "30", "fine.vunk": "20",
         "fine.cal_ratio": "2"}
    for c in "xyz":
        p.update({f"{c}.sign": "1", f"{c}.vmax": "250", f"{c}.acc": "2000", f"{c}.dec": "4000",
                  f"{c}.duty_max": "0.92", f"{c}.duty_min": "0", f"{c}.r_m": "3.5", f"{c}.model.duty": "0.038,0.92",
                  f"{c}.model.speed": "0,289", f"{c}.cal.source": "model", f"{c}.cal.fwd.duty": "0.038,0.92",
                  f"{c}.cal.fwd.speed": "0,289", f"{c}.cal.rev.duty": "0.038,0.92", f"{c}.cal.rev.speed": "0,289"})
    p.update({"r.sign": "1", "r.vmax": "5000", "r.acc": "20000", "r.dec": "20000", "r.lead": "1000", "r.steps": "200",
              "r.microsteps": "16", "r.irun": "18", "r.detent_phase": "128", "r.effector": "min", "r.release": "2000",
              "jog.lease": "250", "lid.duty_max": "50", "lid.medium_max": "40", "lid.sign": "1"})
    return p


_number = plain_number


class TestServer:
    __test__ = False   # a server, not a pytest test class

    def __init__(self, host: str = "127.0.0.1", port: int = 0, *, motors: bool = True, lease: float = 0.25,
                 api: str = "0.11", fw: str = "test", params: dict[str, str] | None = None, cfg: tuple = (),
                 faults: tuple = (), verbose: bool = False, accept_all: bool = False, start: tuple = STAGE_START):
        self.host, self.port = host, port
        self.motors = motors
        self.lease = lease
        self.api, self.fw = api, fw
        self.params = dict(default_params() if params is None else params)
        self.cfg, self.faults = list(cfg), list(faults)
        self.verbose = verbose
        self.accept_all = accept_all
        self.received: list[tuple[float, str]] = []   # (seconds since start, line) from clients
        self.events: list[tuple[float, str]] = []     # events sent
        self.targets = [0.0, 0.0, 0.0, 0.0]           # um/s, x y z r: the jog in force
        self.stage = Stage(self.params, start)        # the axes and their switches (sim_stage.py)
        self.light = {"warm": 0.0, "cool": 0.0}       # %, as set: the nearest of the firmware's 4097 steps; dark
        self.lid = Lid(self.params)                   # the Peltiers by hand and the five NTCs (sim_lid.py)
        self._lid_told = self.lid.state()             # the lid's state as last sent
        self.pos = self.stage.pos                     # um from the as-built pose, x y z r (fusion_mirror.py reads it)
        self.halted = False
        self.watches = {name: 0.0 for name in WATCHED}   # Hz per record (0: off)
        self.connections = 0                          # accepted so far
        self.link = True                              # False while the cable is out (unplug / replug)
        self._dead: list[socket.socket] = []          # sessions cut by an unplug: open but silent, as a pulled cable
        self._lock = threading.RLock()
        self._conn: socket.socket | None = None
        self._stamp = 0.0
        self._refused: str | None = None
        self._t0 = time.monotonic()
        self._last_jog = "jog"                        # for the console: a jog prints only when it changes
        self._stop = threading.Event()
        self._srv: socket.socket | None = None

    # ---- running it
    def start(self) -> tuple[str, int]:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(4)
        self._srv.settimeout(0.2)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._tick_loop, daemon=True).start()
        return self.host, self.port

    def stop(self) -> None:
        self._stop.set()
        self.drop()
        if self._srv:
            self._srv.close()

    def __enter__(self) -> "TestServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ---- hooks for tests
    def now(self) -> float:
        return time.monotonic() - self._t0

    def set_motors(self, on: bool) -> None:
        with self._lock:
            self.motors = on
            if not on:
                self.targets = [0.0] * 4
            self._event(f"motors {'on' if on else 'off'}")

    def event(self, text: str) -> None:
        with self._lock:
            self._event(text)

    def place(self, **um: float) -> None:
        """Puts axes where given, in um from the as-built pose (place(x=-500, r=-109000)), as if moved by hand: the
        switches then read as they stand there."""
        with self._lock:
            for e in self.stage.place(**um):
                self._event(e)

    def set_lid_switch(self, on: bool) -> None:
        """SW10 (LID HEAT): off holds the drive off, the duty kept."""
        with self._lock:
            self.lid.sw = on
            self._lid_tell()

    def unplug_lid(self) -> None:
        with self._lock:
            self.lid.unplug()
            self._lid_tell()

    def plug_lid(self) -> None:
        """Back in: read out a second later, as the firmware's first window after it answers again."""
        with self._lock:
            self.lid.plug()
            self._lid_tell()

    def lid_sensor(self, name: str, reading: str | None) -> None:
        """A sensor (board.a, ... med.j3) that reads "open" or "short" from the next readout on; None mends it."""
        with self._lock:
            if reading is None:
                self.lid.broken.pop(name, None)
            else:
                self.lid.broken[name] = reading
            self.lid.restart_window()

    def lid_temps(self, board: float | None = None, medium: float | None = None) -> None:
        """Puts the lid's board or medium at a temperature (°C), as if it had got there: the next readout says so."""
        with self._lock:
            if board is not None:
                self.lid.board = self.lid.plate = board
            if medium is not None:
                self.lid.medium = medium
            self.lid.restart_window()

    def halt_elsewhere(self) -> None:
        """A halt from another port (the USB serial session): held until an all-zero jog, as any halt."""
        with self._lock:
            self.targets = [0.0] * 4
            self.halted = True
            self._event("halt on")

    def drop(self, cause: str = "closed") -> None:
        """The unit ends the session (as when its client stops reading, or says bye): the connection closes."""
        with self._lock:
            conn, self._conn = self._conn, None
            if conn:
                self._lost(cause)
        if conn:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def unplug(self) -> None:
        """The cable pulled. The unit sees its link go and ends the session (a halt if its jog was in force); the
        client sees nothing at all: its socket stays open, unread and unanswered, until it gives up on it."""
        with self._lock:
            self.link = False
            if self._conn is not None:
                self._dead.append(self._conn)
                self._conn = None
                self._lost("link")

    def replug(self) -> None:
        with self._lock:
            self.link = True
            dead, self._dead = self._dead, []
        for c in dead:
            c.close()

    def commands(self, verb: str | None = None) -> list[str]:
        """What clients sent, without the tags; only lines of that verb if given."""
        with self._lock:
            out = []
            for _, line in self.received:
                head, _, rest = line.partition(" ")
                body = rest if head.isdigit() and rest else line
                if verb is None or body == verb or body.startswith(verb + " "):
                    out.append(body)
            return out

    # ---- the incubator
    def light_record(self) -> str:
        return (f"t={int(self.now() * 1000)} warm={fmt_level(self.light['warm'])} "
                f"cool={fmt_level(self.light['cool'])}")

    def lid_record(self) -> str:
        return self.lid.record(int(self.now() * 1000))

    def _lid_tell(self) -> None:
        """!lid with every readout, and whenever its state changes (api.md 9.2: sw, mode, duty, cut)."""
        state = self.lid.state()
        if state != self._lid_told or self.lid.fresh:
            self._lid_told = state
            self.lid.fresh = False
            self._event("lid " + self.lid_record())

    def _record(self, name: str) -> str:
        return {"drive": self.record, "light": self.light_record, "lid": self.lid_record}[name]()

    # ---- the wire
    def _send(self, line: str) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            conn.sendall((line + "\n").encode("ascii"))
        except OSError:
            pass
        if self.verbose and not line.startswith(("!drive", "!light", "!lid", "@", "#")):   # the records, replies
            print(f"  -> {line}", flush=True)                                               # and lists would drown it

    def _event(self, text: str) -> None:
        self.events.append((self.now(), text))
        self._send("!" + text)

    def _lost(self, cause: str) -> None:
        """The session has ended. If its jog was in force, the operator is gone mid-move: a halt (api.md 2)."""
        if any(self.targets):
            self.halted = True
            self.events.append((self.now(), f"halt on cause={cause}"))   # no session left to tell
            if self.verbose:
                print(f"  -- session ended mid-move ({cause}): halt on", flush=True)
        self.targets = [0.0] * 4
        if self.lid.lost(cause):   # a lid duty it set goes with it (api.md 9.2)
            self._lid_tell()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, peer = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                if not self.link:            # the cable is out: nobody gets through
                    conn.close()
                    continue
                if self._conn is not None:   # the newest connection wins
                    self._send("!bye replaced")
                    old = self._conn
                    self._conn = None
                    old.close()
                    self._lost("replaced")
                self._conn = conn
                self.connections += 1
                self.watches = {name: 0.0 for name in WATCHED}
                self._refused = None
                self._last_jog = "jog"
                if self.verbose:
                    print(f"  -- client {peer[0]}:{peer[1]} connected", flush=True)
                self._send(f"!hello api={self.api} fw={self.fw} unit={self.params.get('unit', 'unset')} reset=power "
                           f"cfg=card clock=unset motors={'on' if self.motors else 'off'} halt={int(self.halted)}")
                for c in self.cfg:
                    self._send("!cfg " + c)
                for f in self.faults:
                    self._send("!" + f)
                for line in self.stage.snapshot():   # the state a client mirrors, then kept up by events
                    self._send("!" + line)
                self._send("!light " + self.light_record())
                self._lid_told = self.lid.state()
                self._send("!lid " + self.lid_record())
            threading.Thread(target=self._read_loop, args=(conn,), daemon=True).start()

    def _read_loop(self, conn: socket.socket) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                with self._lock:
                    if self._conn is not conn:
                        return
                    self._dispatch(raw.decode("ascii", "replace").rstrip("\r"))
        with self._lock:
            if self._conn is conn:   # the client closed it
                self._conn = None
                if self.verbose:
                    print("  -- client gone", flush=True)
                self._lost("closed")

    def _tick_loop(self) -> None:
        last = time.monotonic()
        nxt = {name: 0.0 for name in WATCHED}
        while not self._stop.is_set():
            time.sleep(0.005)
            now = time.monotonic()
            with self._lock:
                dt, last = now - last, now
                for e in self.stage.step(self.targets, dt):
                    self._event(e)
                self.lid.step(dt, self.now())
                self._lid_tell()
                if any(self.targets) and now - self._stamp > self.lease:   # its client went quiet: a halt
                    self.targets = [0.0] * 4
                    self.halted = True
                    self._event("halt on cause=lease")
                for name, hz in self.watches.items():
                    if hz and now >= nxt[name]:
                        nxt[name] = now + 1.0 / hz
                        self._event(f"{name} {self._record(name)}")
                    elif not hz:
                        nxt[name] = 0.0

    def record(self) -> str:
        st = self.stage
        parts = [f"t={int(self.now() * 1000)} motors={'on' if self.motors else 'off'} halt={int(self.halted)} acq=off"]
        for i, c in enumerate("xyz"):
            v = st.v[i]
            mode = "off" if not self.motors else ("jog" if v else "idle")
            blk = sim_token(st.blocked(c))
            parts.append(f"{c}.mode={mode} {c}.v={v:.1f} {c}.d=0.000 {c}.lim={st.limit(c)} {c}.blk={blk} "
                         f"{c}.i=0.000 {c}.cal=0")
        v = st.v[3]
        mode = "off" if not self.motors else ("jog" if v else "idle")
        parts.append(f"r.mode={mode} r.v={v:.0f} r.pos={st.counted():.0f} r.homed=0 r.lim={st.limit('r')} "
                     f"r.blk={sim_token(st.blocked('r'))} r.drv={'ok' if self.motors else 'off'}")
        return " ".join(parts)

    # ---- the commands (api.md 6.4)
    def _dispatch(self, line: str) -> None:
        self.received.append((self.now(), line))
        words = line.split()
        if not words:
            return
        tag = words.pop(0) if words[0].isdigit() else ""
        if not words:
            self._send(f"@{tag} err syntax no command")
            return
        verb, args = words[0], words[1:]
        if self.verbose and verb != "ping" and (verb != "jog" or line != self._last_jog):
            print(f"<-  {line}", flush=True)   # a jog only when it changes: not the lease's re-sends
        if verb == "jog":
            self._last_jog = line
            self._jog(args)
        elif verb == "halt":
            self.targets = [0.0] * 4
            self.halted = True
            self._event("halt on")
            self._send(f"@{tag} ok")
        elif verb == "ping":
            self._send(f"@{tag} ok t={int(self.now() * 1000)}")
        elif verb == "status":
            if len(args) == 1 and args[0] in RECORDS:
                self._send(f"@{tag} ok {self._record(args[0])}")
            else:
                self._send(f"@{tag} err arg status drive|light|lid")
        elif verb == "watch":
            hz = _number(args[1]) if len(args) == 2 else None
            if len(args) == 2 and args[0] == "lid":
                self._send(f"@{tag} err arg the lid isn't watched: !lid comes with every readout, once a second")
            elif len(args) == 2 and args[0] in WATCHED and hz is not None and 0 <= hz <= 50:
                self.watches[args[0]] = hz
                self._send(f"@{tag} ok")
            else:
                self._send(f"@{tag} err arg watch drive|light <0-50 Hz>")
        elif verb == "get":
            self._get(tag, args)
        elif verb == "light":
            self._light(tag, args)
        elif verb == "lid":
            self._send(f"@{tag} {self.lid.command(args)}")
            self._lid_tell()
        elif verb == "help":
            for h in ("jog [x=v] [y=v] [z=v] [r=v]", "halt", "light [warm=%] [cool=%]", "lid duty=% | lid off",
                      "get [key ...]", "ping", "status drive|light|lid", "watch drive|light <Hz>"):
                self._send(f"#{tag} {h}")
            self._send(f"@{tag} ok")
        elif verb == "bye":
            self._send(f"@{tag} ok")
            self.drop("bye")
        elif self.accept_all:
            self._send(f"@{tag} ok")   # as though the unit had it
        else:
            self._send(f"@{tag} err unknown {verb}")

    def _jog(self, args: list[str]) -> None:
        t = [0.0] * 4
        seen = set()
        for a in args:
            k, eq, v = a.partition("=")
            n = _number(v) if eq else None
            if k not in ("x", "y", "z", "r") or n is None or k in seen:
                self.targets = [0.0] * 4
                self._event("error jog arg bad argument")
                return
            seen.add(k)
            t["xyzr".index(k)] = n
        nonzero = any(t)
        if self.halted:
            if nonzero:
                self._refuse("halted")
                return
            self.halted = False
            self._event("halt off")
        if nonzero and not self.motors:
            self.targets = [0.0] * 4
            self._refuse("motors-off")
            return
        self._refused = None
        self.targets = t
        self._stamp = time.monotonic()

    def _light(self, tag: str, args: list[str]) -> None:
        """light [warm=<%>] [cool=<%>] (api.md 9.1), as the firmware takes it: strict, all or nothing, each level set to
        the nearest of 4097 steps; a channel left out keeps its level. A change goes to the session as !light."""
        given: dict[str, float] = {}
        for a in args:
            k, eq, v = a.partition("=")
            n = _number(v) if eq else None
            if k not in ("warm", "cool"):
                self._send(f"@{tag} err arg unknown key (warm= cool=)")
                return
            if k in given:
                self._send(f"@{tag} err arg a channel given twice")
                return
            if n is None:
                self._send(f"@{tag} err arg not a number")
                return
            if not 0 <= n <= 100:
                self._send(f"@{tag} err range {k} 0-100")
                return
            given[k] = n
        if not given:
            self._send(f"@{tag} err arg light [warm=<%>] [cool=<%>]: one or both")
            return
        before = dict(self.light)
        for k, n in given.items():
            self.light[k] = math.floor(n * LIGHT_STEPS / 100 + 0.5) * 100 / LIGHT_STEPS   # the firmware's lroundf
        self._send(f"@{tag} ok warm={fmt_level(self.light['warm'])} cool={fmt_level(self.light['cool'])}")
        if self.light != before:
            self._event("light " + self.light_record())

    def _refuse(self, why: str) -> None:
        if self._refused != why:
            self._event(f"jog refused {why}")
        self._refused = why

    def _get(self, tag: str, keys: list[str]) -> None:
        q = lambda v: f'"{v}"' if (" " in v or not v) else v
        if not keys:
            for k, v in self.params.items():
                self._send(f"#{tag} {k}={q(v)}")
            self._send(f"@{tag} ok")
            return
        out = []
        for k in keys:
            if k not in self.params:
                self._send(f"@{tag} err arg unknown key {k}")
                return
            out.append(f"{k}={q(self.params[k])}")
        self._send(f"@{tag} ok " + " ".join(out))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m mmi_client.test_server",
                                 description="A test server: the stage-top unit at the protocol level, no hardware.")
    ap.add_argument("addr", nargs="?", default="127.0.0.1:5000",
                    help="where to listen: host:port, host or port (default 127.0.0.1:5000)")
    ap.add_argument("--motors-off", action="store_true", help="start as if SW11 (MOTORS) were off")
    ap.add_argument("--strict", action="store_true",
                    help="answer err unknown to commands the firmware doesn't have, as it does")
    ap.add_argument("-q", "--quiet", action="store_true", help="don't print the traffic")
    ap.add_argument("--no-fusion", action="store_true",
                    help="don't mirror the stage into Fusion's mmi (by default it does, whenever Fusion is up "
                         "with mmi and its stage joints open: fusion_mirror.py)")
    ap.add_argument("--fusion-hz", type=float, default=0.0,
                    help="cap the Fusion updates a second (default: none; one at a time, as fast as Fusion answers, "
                         "~5 Hz)")
    a = ap.parse_args(argv)
    host, port = parse_addr(a.addr, "127.0.0.1")
    unit = TestServer(host, port, motors=not a.motors_off, verbose=not a.quiet, accept_all=not a.strict)
    unit.start()
    print(f"test server on {unit.host}:{unit.port}: fw {unit.fw}, api {unit.api}, MOTORS "
          f"{'on' if unit.motors else 'off'} (Ctrl-C to stop)", flush=True)
    mirror = None
    if not a.no_fusion:   # started after the line above, which says where the server listens
        from .fusion_mirror import FusionMirror
        mirror = FusionMirror(unit, a.fusion_hz, verbose=not a.quiet).start()
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):   # Ctrl-C, or a kill, however it was started
        signal.signal(sig, lambda *_: done.set())
    while not done.wait(0.5):
        pass
    if mirror:
        mirror.stop()     # puts mmi's joints back to the as-built pose
    unit.stop()


if __name__ == "__main__":
    main()

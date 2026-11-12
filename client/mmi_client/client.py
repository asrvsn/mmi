"""MmiClient: a client of the stage-top unit (mmi/firmware/api.md), with what api.md asks of a client handled here.

- **The link:** TCP with Nagle off; one reply per command, matched by tag; events dispatched as they arrive.
- **Jogs go through a mailbox, not a queue.** `jog()` only sets the latest vector. A sender thread sends it when it
  changes (at most every `jog_min_interval`), and re-sends a non-zero one every `jog_refresh`, inside the unit's
  250 ms lease (a lease that runs out halts the unit). Jogs can never pile up behind each other or behind a halt.
- **The vector holds until the caller changes it, or `close()`,** whatever the caller's thread is doing meanwhile.
  For a front end that calls `jog()` from a loop (a gamepad), `deadman` turns that around: a non-zero vector not
  renewed within `deadman` s halts the unit, so a loop that hangs (and the halt button it reads) can't leave a move
  running.
- **halt goes now:** written to the socket from the caller's own thread, ahead of any jog.
- **No surprise motion.** After a halt, a (re)connect or MOTORS coming on, a vector the caller set before it is not
  sent: nothing more goes out until the caller asks for zero. That zero is sent before anything else, and it's what
  releases the unit's halt. A halt from elsewhere (the USB port) the unit holds by itself; a zero goes out as soon as
  the caller is at zero.
- **A dead link is noticed:** a pulled cable gives no error until TCP gives up, minutes later. So when nothing has
  come from the unit for `keepalive` s, it's pinged, and the link dropped if no reply comes within `timeout`. (The
  unit has halted by then: it sees the link go, or the jog lease runs out.)
- **Reconnection** in the background (`reconnect=True`), except after `!bye replaced`: another client took the unit
  on purpose, so this one stays off.
- **Closing** while a non-zero vector is asked for abandons the move, and the unit halts (api.md 2). A zero asked for
  last goes out first, so `jog()` then `close()` is a ramped stop.
- **Records** (`watch(hz, topic)`): the drive's in `state`, every other topic's latest in `records`. Watches are
  remembered and set again after a reconnect; a topic the unit doesn't have yet is noted in `unsupported` rather than
  failing the connection.
- **The limits and stalls, mirrored** (api.md 6.5): `limits` and `stalls`, and `blocks(axis)`, kept from the unit's
  events alone. It sends the whole state after its `!hello`, then each change as it happens, so nothing is polled and
  nothing rides on the jogs. `blocks("x").why(-40)` says whether a jog that way would run.
- **The lights** (api.md 9.1): `light(warm=, cool=)` sets them. By default it returns at once, for a front end's main
  thread: a sender thread sends the latest asked for (channel by channel, so a quick warm then cool both go), and
  never queues, as the jog mailbox. What's in force is `lights`, mirrored from the unit's `!light`; a refusal is in
  `set_errors`. `wait=True` sends it now and returns the levels set, or raises.
- **The lid by hand** (api.md 9.2): `lid_duty(duty)`, through the same mailbox. `lid` is its latest `!lid` (the state,
  `sw`, `mode`, `duty`, `cut`, and the five temperatures, each a mean and its SD: `board.a`, `board.a.sd`, ...), which
  the unit sends with every readout, once a second, unasked. `set_pending("lid")` says a duty is still on its way, so a
  front end knows when `lid` and `set_errors` speak of it. Unlike a light level, a duty lost with its link isn't sent
  again on the next one: the unit drops a duty with the session that set it, and so does this.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from typing import Callable

from .protocol import (API_MAJOR, API_MIN_MINOR, Blocks, DriveState, Event, Hello, Info, Reply, format_jog,
                       format_lid, format_light, halves, parse_line, split_words, typed)

log = logging.getLogger("mmi_client")

DEFAULT_HOST = "192.168.50.2"
DEFAULT_PORT = 5000
ZERO = (0.0, 0.0, 0.0, 0.0)
NO_LIMITS = {"x": "0", "y": "0", "z": "0", "r": "0"}
NO_STALLS = {"x": "0", "y": "0", "z": "0"}
SET_LINES = {"light": lambda v: format_light(**v), "lid": lambda v: format_lid(v["duty"])}   # the mailbox's topics
RESEND = {"light"}   # set-points sent again if their link went before the reply; a lid duty is that session's alone


def parse_addr(text: str, default_host: str = DEFAULT_HOST, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """'host:port', 'host', ':port' or 'port' to (host, port)."""
    text = text.strip()
    if text.isdigit():
        return default_host, int(text)
    host, sep, port = text.rpartition(":")
    if not sep:
        return text or default_host, default_port
    return host or default_host, int(port)


class MmiError(Exception):
    """The unit refused a command (`code` is its error code), or the client couldn't do what was asked."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class MmiTimeout(MmiError):
    pass


class MmiDisconnected(MmiError):
    pass


class _Pending:
    __slots__ = ("done", "reply", "info", "apply")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.reply: Reply | None = None
        self.info: list[str] = []
        self.apply: Callable[[Reply], None] | None = None   # run on an ok reply, in the reader's turn


class MmiClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *, reconnect: bool = True,
                 watch_hz: int = 0, jog_refresh: float = 0.05, jog_min_interval: float = 0.02, timeout: float = 1.0,
                 connect_timeout: float = 2.0, retry_s: float = 1.0, keepalive: float = 1.0,
                 deadman: float | None = None):
        self.host, self.port = host, port
        self.reconnect = reconnect
        self.keepalive = keepalive        # s of silence from the unit before a ping checks the link (0: never)
        self.deadman = deadman            # s: a non-zero vector not renewed by jog() in this long halts (None: never)
        self.deadman_trips = 0
        self.watches: dict[str, int] = {"drive": int(watch_hz)} if watch_hz else {}   # topic -> Hz, kept across links
        self.jog_refresh, self.jog_min_interval = jog_refresh, jog_min_interval
        self.timeout, self.connect_timeout, self.retry_s = timeout, connect_timeout, retry_s

        # what the unit has told us
        self.hello: Hello | None = None
        self.params: dict = {}            # every parameter (get), typed: {"x.vmax": 250, "x.model.duty": [0.038, 0.92]}
        self.cfg: list[str] = []          # the !cfg lines after the hello
        self.faults: list[str] = []       # latched !fault lines
        self.state: DriveState | None = None   # the latest drive record (status, or !drive while watching)
        self.limits: dict[str, str] = dict(NO_LIMITS)   # per axis, what its limit switches block: "0", "-", "+?" ...
        self.stalls: dict[str, str] = dict(NO_STALLS)   # per fine axis, what a stall blocks
        self.lights: dict[str, float] = {}    # the lights in force, as the unit last said: {"warm": 39.99, "cool": 0.0}
        self.lid: dict = {}                   # the lid's latest record: {"sw": "on", "mode": "idle", "duty": 0, ...}
        self.set_errors: dict[str, str] = {}  # a set-point sent without waiting that the unit refused, by topic
        self.records: dict[str, dict] = {}    # the latest record of every other watched topic: {"lid": {"medium": 24.8}}
        self.unsupported: set[str] = set()    # watched topics this unit refused (not in its firmware yet)
        self.sent_vector = ZERO               # the jog vector the unit was last sent (µm/s; ZERO after a halt)
        self.motors = "off"
        self.halted = False
        self.replaced = False             # another client took over (!bye replaced): no reconnecting

        self._sock: socket.socket | None = None
        self._sock_lock = threading.Lock()     # guards _sock and every write to it
        self._open_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._tag = 0
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._down = threading.Event()
        self._down.set()
        self._hello_evt = threading.Event()
        self._event_cbs: list[Callable[[Event], None]] = []
        self._connect_cbs: list[Callable[["MmiClient"], None]] = []
        self._disconnect_cbs: list[Callable[["MmiClient", str], None]] = []
        self._events: deque[Event] = deque(maxlen=1000)
        self._events_cv = threading.Condition()
        self._last_rx = 0.0               # when the unit last sent anything (time.monotonic)

        # the jog mailbox (lock order: _sock_lock, then _jog_cv)
        self._jog_cv = threading.Condition()
        self._jog = (0.0, 0.0, 0.0, 0.0)    # what the caller asks for
        self._armed = False                 # False: nothing goes out until the caller asks for zero
        self._zero_due = False              # the caller has let go: a zero goes out before anything else
        self._jog_sent: str | None = "jog"  # what the unit was last sent (None: unknown, send even a zero)
        self._jog_gen = 0                   # bumped by every reset above, so a send in flight can't undo one
        self._halts_out = 0                 # our halts whose !halt on hasn't come back yet
        self._jog_at = 0.0                  # when the caller last called jog() (the deadman)
        self._last_jog_at = 0.0
        self._jog_thread: threading.Thread | None = None
        self._supervisor: threading.Thread | None = None

        # the set-point mailbox (light(), lid_duty()): per topic, the latest value of each key not yet sent
        self._sets: dict[str, dict[str, float]] = {}
        self._inflight: set[str] = set()      # topics whose request is out, its reply not back yet
        self._sets_cv = threading.Condition()
        self._set_thread: threading.Thread | None = None

    # ---------------------------------------------------------------- lifecycle

    def connect(self, timeout: float | None = None) -> Hello:
        """Connects and shakes hands now, raising if it can't. With `reconnect`, a dropped link is restored in the
        background from then on."""
        self._stop.clear()
        self.replaced = False
        self._start_jog_thread()
        with self._open_lock:
            if not self._connected.is_set():
                self._open(timeout or self.connect_timeout)
        self._start_supervisor()   # the keepalive, and with `reconnect`, the reconnecting
        assert self.hello is not None
        return self.hello

    def start(self) -> None:
        """Connects in the background and keeps reconnecting (for front ends that run with or without the unit)."""
        self._stop.clear()
        self.replaced = False
        self.reconnect = True
        self._start_jog_thread()
        self._start_supervisor()

    def wait_connected(self, timeout: float | None = None) -> bool:
        return self._connected.wait(timeout)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def close(self) -> None:
        """Closes the link. If a non-zero vector is still asked for, the unit halts (api.md 2): the move is abandoned. A
        zero asked for last goes out first, so jog() then close() is a ramped stop."""
        self._send_zero_due()
        self._send_sets_due()
        self._stop.set()
        with self._sets_cv:
            self._sets_cv.notify_all()
        self._drop("closed")
        for t in (self._jog_thread, self._supervisor, self._set_thread):
            if t and t is not threading.current_thread():
                t.join(timeout=2)
        self._jog_thread = self._supervisor = self._set_thread = None

    def __enter__(self) -> "MmiClient":
        if not self.connected:
            self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- callbacks

    def on_event(self, fn: Callable[[Event], None]) -> Callable[[Event], None]:
        """fn(event) for every event, from the reader thread: keep it quick."""
        self._event_cbs.append(fn)
        return fn

    def on_connect(self, fn: Callable[["MmiClient"], None]) -> Callable[["MmiClient"], None]:
        self._connect_cbs.append(fn)
        return fn

    def on_disconnect(self, fn: Callable[["MmiClient", str], None]) -> Callable[["MmiClient", str], None]:
        self._disconnect_cbs.append(fn)
        return fn

    def get_event(self, timeout: float | None = None) -> Event | None:
        """The oldest event not yet taken (the last 1000 are kept), or None after timeout."""
        with self._events_cv:
            if not self._events:
                self._events_cv.wait(timeout)
            return self._events.popleft() if self._events else None

    # ---------------------------------------------------------------- commands

    def jog(self, x: float = 0.0, y: float = 0.0, z: float = 0.0, r: float = 0.0) -> None:
        """The velocity (µm/s) every axis should have now. Sets the mailbox and returns at once (api.md 6.4). After a
        halt, a (re)connect or MOTORS coming on, nothing goes out until this has been asked for zero."""
        vec = (float(x), float(y), float(z), float(r))
        with self._jog_cv:
            self._jog = vec
            self._jog_at = time.monotonic()
            if not self._armed and _is_zero(vec):   # let go: kept now, however soon the next push follows
                self._armed = self._zero_due = True
            self._jog_cv.notify()

    def halt(self, wait: bool = False, timeout: float | None = None) -> Reply | None:
        """Stops everything now (api.md 6.4, halt). Sent from this thread, ahead of any jog. Afterwards nothing more
        goes out until the caller asks for zero; that zero releases the unit's halt (at once if the caller is at zero
        already)."""
        tag = self._next_tag()
        p = self._register(tag)
        with self._sock_lock:   # the reset and the line in one piece: the jog sender can't put a line between them
            sock = self._sock
            sent = False
            if sock is not None:
                self._disarm(halt=True)
                try:
                    sock.sendall(f"{tag} halt\n".encode("ascii"))
                    sent = True
                    self.sent_vector = ZERO
                except OSError:
                    pass
        if not sent:
            self._unregister(tag)
            if sock is not None:
                self._drop("send failed")
            raise MmiDisconnected("not connected (the unit stops all motion by itself when the link drops)")
        return self._wait(tag, p, "halt", timeout) if wait else None

    def request(self, command: str, timeout: float | None = None) -> Reply:
        """Any command that gets a reply; raises MmiError if the unit refuses it."""
        if not self.connected:
            raise MmiDisconnected("not connected")
        return self._request(command, timeout)

    def ping(self) -> float:
        """The round trip, in seconds."""
        t0 = time.monotonic()
        self.request("ping")
        return time.monotonic() - t0

    def status(self) -> DriveState:
        self.state = DriveState.from_values(self.request("status drive").values)
        return self.state

    def get(self, *keys: str) -> dict:
        """Parameters (parameters.md): the keys given, or all of them."""
        if keys:
            return dict(self.request("get " + " ".join(keys)).values)
        return self._parse_params(self.request("get"))

    def light(self, warm: float | None = None, cool: float | None = None, *, wait: bool = False,
              timeout: float | None = None) -> dict | None:
        """The lights' levels, % of full scale (api.md 9.1): either or both; a channel left out keeps its level.

        wait=False (the default) returns at once: the latest asked for goes out from the client's own thread, when
        connected, and what's in force shows in `lights`. A refusal lands in `set_errors["light"]`. wait=True sends
        it now and returns the levels set ({"warm": 39.99, "cool": 0.0}), or raises MmiError."""
        values = {k: float(v) for k, v in (("warm", warm), ("cool", cool)) if v is not None}
        if not values:
            raise ValueError("light: warm, cool or both")
        if wait:
            return {k: float(v) for k, v in self.request(format_light(**values), timeout).values.items()}
        self._queue_set("light", values)
        return None

    def lid_duty(self, duty: float, *, wait: bool = False, timeout: float | None = None) -> float | None:
        """The lid's Peltiers by hand (api.md 9.2): % of full drive, signed, + heats; 0 is off. The unit caps it at
        lid.duty_max (`params`) and drops it when this session ends, or cuts it itself (`lid["cut"]`).

        wait=False (the default) returns at once, as light(); what's in force shows in `lid`, a refusal in
        `set_errors["lid"]`, and `set_pending("lid")` is True until the reply is back. wait=True sends it now and
        returns the duty set, or raises MmiError (`code` "range", "absent", "fault", "hot")."""
        if wait:
            if not self.connected:
                raise MmiDisconnected("not connected")
            return float(self._request(format_lid(duty), timeout, apply=self._lid_set).values["duty"])
        self._queue_set("lid", {"duty": float(duty)})
        return None

    def set_pending(self, topic: str) -> bool:
        """A set-point for this topic ("light", "lid") is still to go, or its reply isn't back: until then, the
        mirrored state and `set_errors` don't speak of it yet."""
        with self._sets_cv:
            return topic in self._sets or topic in self._inflight

    def watch(self, hz: int, topic: str = "drive") -> None:
        """A topic's record as !<topic> events, hz per second (0 = off); kept across reconnects. Before connecting
        it's only remembered; while connected it's sent now, and a refusal raises MmiError."""
        self.watches[topic] = int(hz)
        if self.connected:
            self.request(f"watch {topic} {int(hz)}")

    def blocks(self, axis: str) -> Blocks:
        """What stops this axis now, as the unit last said (api.md 6.5); nothing if it hasn't said."""
        limit, unknown = halves(self.limits.get(axis, "0"))
        stall, _ = halves(self.stalls.get(axis, "0"))
        return Blocks(limit, stall, unknown)

    @property
    def awaiting_release(self) -> bool:
        """A non-zero vector is being held back until the caller asks for zero (after a halt, a reconnect, MOTORS
        on)."""
        with self._jog_cv:
            return not self._armed and not _is_zero(self._jog)

    def help(self) -> list[str]:
        return self.request("help").info

    # ---------------------------------------------------------------- internals: the link

    def _start_jog_thread(self) -> None:
        if not self._jog_thread or not self._jog_thread.is_alive():
            self._jog_thread = threading.Thread(target=self._jog_loop, name="mmi-jog", daemon=True)
            self._jog_thread.start()
        if not self._set_thread or not self._set_thread.is_alive():
            self._set_thread = threading.Thread(target=self._set_loop, name="mmi-set", daemon=True)
            self._set_thread.start()

    def _start_supervisor(self) -> None:
        if not self._supervisor or not self._supervisor.is_alive():
            self._supervisor = threading.Thread(target=self._supervise, name="mmi-link", daemon=True)
            self._supervisor.start()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            if self._connected.is_set():
                self._down.wait(0.1)
                self._check_alive()
                continue
            if self.replaced or not self.reconnect:
                self._stop.wait(0.2)
                continue
            try:
                with self._open_lock:
                    if not self._connected.is_set() and not self._stop.is_set():
                        self._open(self.connect_timeout)
            except (OSError, MmiError) as e:
                log.debug("connect to %s:%s failed: %s", self.host, self.port, e)
                self._stop.wait(self.retry_s)

    def _check_alive(self) -> None:
        """The keepalive: after `keepalive` s with nothing from the unit, a ping; no reply within `timeout` drops the
        link (and with `reconnect` it's restored once the unit answers again)."""
        if not self.keepalive or not self._connected.is_set() or time.monotonic() - self._last_rx < self.keepalive:
            return
        try:
            self._request("ping")
        except MmiTimeout:
            log.info("no reply to a keepalive ping: dropping the link")
            self._drop("no reply to a ping")
        except MmiError:
            pass   # the link went meanwhile

    def _open(self, timeout: float) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(0.25)
        self.hello, self.cfg, self.faults = None, [], []
        self._hello_evt.clear()
        self._last_rx = time.monotonic()
        with self._sock_lock:
            self._sock = sock
        self._down.clear()
        threading.Thread(target=self._read_loop, args=(sock,), name="mmi-read", daemon=True).start()
        try:
            end = time.monotonic() + timeout
            while not self._hello_evt.wait(0.02):
                if self._down.is_set():
                    raise MmiDisconnected("the unit closed the connection")
                if time.monotonic() > end:
                    raise MmiTimeout("no !hello from the unit")
            assert self.hello is not None
            major, minor = self.hello.api_version
            if major != API_MAJOR or minor < API_MIN_MINOR:
                raise MmiError(f"the unit speaks api {self.hello.api}; this client needs {API_MAJOR}.{API_MIN_MINOR}+",
                               "api")
            self._request("ping", timeout)   # every line sent with the hello (!cfg, !fault) has now arrived
            self.params = self._parse_params(self._request("get", timeout)) if minor >= 5 else {}
            self.state = DriveState.from_values(self._request("status drive", timeout).values)
            self.motors, self.halted = self.state.motors, self.state.halt
            self.unsupported = set()
            for topic, hz in list(self.watches.items()):
                if not hz:
                    continue
                try:
                    self._request(f"watch {topic} {hz}", timeout)
                except (MmiTimeout, MmiDisconnected):
                    raise
                except MmiError as e:
                    if topic == "drive":
                        raise
                    self.unsupported.add(topic)   # a topic this firmware doesn't have yet: not a reason to fail
                    log.info("the unit has no %s records yet (%s)", topic, e)
        except BaseException:
            self._drop("handshake failed")
            raise
        with self._jog_cv:
            self._halts_out = 0   # any still out went with the old link
        self.sent_vector = ZERO
        self._disarm()            # the unit holds no jog after a (re)connect; nothing moves until the caller asks for zero
        self._connected.set()
        log.info("connected to %s:%s: fw %s, api %s, motors %s", self.host, self.port, self.hello.fw, self.hello.api,
                 self.motors)
        for fn in list(self._connect_cbs):
            self._safe(fn, self)

    def _drop(self, reason: str) -> None:
        with self._sock_lock:
            sock = self._sock
        if sock:
            self._closed(sock, reason)   # first: the reason is this one, not the reader's error from the close below
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _closed(self, sock: socket.socket, reason: str) -> None:
        with self._sock_lock:
            if self._sock is not sock:
                return
            self._sock = None
        was = self._connected.is_set()
        self._connected.clear()
        self._down.set()
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for p in pending.values():
            p.done.set()
        if was:
            log.info("disconnected: %s", reason)
            for fn in list(self._disconnect_cbs):
                self._safe(fn, self, reason)

    def _read_loop(self, sock: socket.socket) -> None:
        buf, reason = b"", "closed by the unit"
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError as e:
                    reason = str(e)
                    break
                if not chunk:
                    break
                self._last_rx = time.monotonic()
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self._handle(raw.decode("ascii", "replace"))
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._closed(sock, reason)

    def _send_line(self, line: str) -> bool:
        with self._sock_lock:
            sock = self._sock
            if sock is None:
                return False
            try:
                sock.sendall((line + "\n").encode("ascii"))
                return True
            except OSError:
                pass
        self._drop("send failed")
        return False

    # ---------------------------------------------------------------- internals: replies and events

    def _next_tag(self) -> str:
        with self._pending_lock:
            self._tag = self._tag % 999_999_999 + 1
            return str(self._tag)

    def _register(self, tag: str) -> _Pending:
        p = _Pending()
        with self._pending_lock:
            self._pending[tag] = p
        return p

    def _unregister(self, tag: str) -> None:
        with self._pending_lock:
            self._pending.pop(tag, None)

    def _request(self, command: str, timeout: float | None = None,
                 apply: Callable[[Reply], None] | None = None) -> Reply:
        tag = self._next_tag()
        p = self._register(tag)
        p.apply = apply
        if not self._send_line(f"{tag} {command}"):
            self._unregister(tag)
            raise MmiDisconnected("not connected")
        return self._wait(tag, p, command, timeout)

    def _wait(self, tag: str, p: _Pending, command: str, timeout: float | None) -> Reply:
        if not p.done.wait(timeout or self.timeout):
            self._unregister(tag)
            raise MmiTimeout(f"no reply to {command!r}")
        r = p.reply
        if r is None:
            raise MmiDisconnected(f"the link closed before the reply to {command!r}")
        if not r.ok:
            raise MmiError(f"{command!r} refused: {r.code} {r.text}".strip(), r.code)
        return r

    @staticmethod
    def _parse_params(reply: Reply) -> dict:
        out = {}
        for line in reply.info:
            for word in split_words(line):
                k, eq, v = word.partition("=")
                if eq:
                    out[k] = typed(v)
        return out

    def _handle(self, line: str) -> None:
        item = parse_line(line)
        if isinstance(item, Info):
            with self._pending_lock:
                p = self._pending.get(item.tag)
            if p:
                p.info.append(item.text)
        elif isinstance(item, Reply):
            with self._pending_lock:
                p = self._pending.pop(item.tag, None)
            if p:
                item.info = p.info
                p.reply = item
                if p.apply and item.ok:
                    self._safe(p.apply, item)
                p.done.set()
        elif isinstance(item, Event):
            self._on_event(item)

    def _on_event(self, ev: Event) -> None:
        arg = ev.args[0] if ev.args else ""
        if ev.name == "hello":
            self.hello = Hello.from_event(ev)
            self.motors, self.halted = self.hello.motors, self.hello.halt
            self.limits, self.stalls = dict(NO_LIMITS), dict(NO_STALLS)   # a new session: its state follows
            self.lights, self.lid = {}, {}
            self._hello_evt.set()
        elif ev.name == "cfg":
            self.cfg.append(ev.raw[4:])
        elif ev.name == "fault":
            self.faults.append(ev.raw)
        elif ev.name == "drive":
            self.state = DriveState.from_values(ev.values)
            self.motors, self.halted = self.state.motors, self.state.halt
        elif ev.name == "motors":
            self.motors = arg
            if arg == "on":
                self._disarm()
        elif ev.name == "halt":
            self.halted = arg == "on"
            if self.halted:
                self._halted_elsewhere()
        elif ev.name == "limit" and len(ev.args) >= 2 and ev.args[0] in self.limits:
            self.limits = {**self.limits, ev.args[0]: ev.args[1]}   # a new dict: a reader never sees half a change
        elif ev.name == "stall" and len(ev.args) >= 2 and ev.args[0] in self.stalls:
            self.stalls = {**self.stalls, ev.args[0]: ev.args[1]}
        elif ev.name == "light":   # a state (api.md 9.1): after the hello, on every change, and at a watch's rate
            self.records["light"] = dict(ev.values)
            self.lights = {k: float(ev.values[k]) for k in ("warm", "cool") if isinstance(ev.values.get(k), (int, float))}
        elif ev.name == "lid":     # a state too (api.md 9.2), with the temperatures: also with every readout (1 Hz)
            self.records["lid"] = self.lid = dict(ev.values)
        elif ev.name == "bye" and arg == "replaced":
            self.replaced = True
        elif ev.name in self.watches:
            self.records[ev.name] = dict(ev.values)
        with self._events_cv:
            self._events.append(ev)
            self._events_cv.notify_all()
        for fn in list(self._event_cbs):
            self._safe(fn, ev)

    def _lid_set(self, r: Reply) -> None:
        """A lid command taken: its duty is in force and its cut back to 0 (api.md 9.2). Run as the reply is read, so
        `lid` says so by the time the caller hears back, and a !lid read after it (a cut, say) overrides it."""
        duty = r.values.get("duty")
        if not isinstance(duty, (int, float)) or not self.lid:
            return
        mode = self.lid.get("mode")
        if mode in ("idle", "manual"):
            mode = "manual" if duty else "idle"
        self.records["lid"] = self.lid = {**self.lid, "duty": duty, "cut": 0, "mode": mode}

    @staticmethod
    def _safe(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:   # a caller's callback must never take the link down
            log.exception("callback %r failed", fn)

    # ---------------------------------------------------------------- internals: the set-point mailbox

    def _queue_set(self, topic: str, values: dict) -> None:
        with self._sets_cv:
            self._sets.setdefault(topic, {}).update(values)
            self.set_errors.pop(topic, None)   # whatever was refused before, this is what's asked for now
            self._sets_cv.notify_all()

    def _set_loop(self) -> None:
        """Sends what light() and lid_duty() asked for without waiting: the latest per key, one request at a time,
        whenever connected. A refusal is kept in set_errors; a light level whose link went on the way goes again
        (unless overtaken)."""
        while not self._stop.is_set():
            with self._sets_cv:
                while not self._stop.is_set() and not (self._sets and self._connected.is_set()):
                    self._sets_cv.wait(0.1)
                if self._stop.is_set():
                    return
                topic, values = next(iter(self._sets.items()))
                del self._sets[topic]
                self._inflight.add(topic)
            try:
                self._send_set(topic, values)
            finally:
                with self._sets_cv:
                    self._inflight.discard(topic)

    def _send_set(self, topic: str, values: dict, timeout: float | None = None) -> None:
        try:
            self._request(SET_LINES[topic](values), timeout, apply=self._lid_set if topic == "lid" else None)
            self.set_errors.pop(topic, None)
        except (MmiTimeout, MmiDisconnected) as e:
            if topic not in RESEND:   # the unit drops a lid duty with its session: a new one starts from 0
                self.set_errors[topic] = f"the link went before the unit answered ({e})"
                return
            with self._sets_cv:   # not known to have gone: again, unless something newer was asked for meanwhile
                pending = self._sets.setdefault(topic, {})
                for k, v in values.items():
                    pending.setdefault(k, v)
        except MmiError as e:
            self.set_errors[topic] = str(e)
            log.warning("%s", e)

    def _send_sets_due(self, timeout: float = 0.5) -> None:
        """Whatever set-points are still to go, now (close() sends them before it closes: the lights or the lid
        switched off on the way out go off)."""
        if not self._connected.is_set():
            return
        with self._sets_cv:
            due, self._sets = self._sets, {}
        for topic, values in due.items():
            self._send_set(topic, values, timeout)

    # ---------------------------------------------------------------- internals: the jog mailbox

    def _disarm(self, halt: bool = False) -> None:
        """A halt of ours, a (re)connect or MOTORS on: what the caller set before doesn't go out. If the caller is at
        zero already, that zero goes out next (it releases the unit's halt); otherwise nothing until they let go."""
        with self._jog_cv:
            self._armed = self._zero_due = _is_zero(self._jog)
            self._jog_sent = None
            self._jog_gen += 1
            if halt:
                self._halts_out += 1
            self._jog_cv.notify()

    def _halted_elsewhere(self) -> None:
        """!halt on. Ours has been dealt with as it went out; for one from elsewhere the unit holds the halt, and the
        caller's zero, once they're at zero, is sent again to release it."""
        with self._jog_cv:
            if self._halts_out > 0:
                self._halts_out -= 1
                return
            self._jog_sent = None
            self._jog_gen += 1
            self._jog_cv.notify()

    def _jog_loop(self) -> None:
        while not self._stop.is_set():
            with self._jog_cv:
                self._jog_cv.wait(timeout=0.01)
            if not self._connected.is_set():
                continue
            if self._deadman_due():
                self.deadman_trips += 1
                log.warning("jog() not renewed for %.2f s: halting (deadman)", self.deadman)
                try:
                    self.halt()
                except MmiDisconnected:
                    pass   # the link went too: the unit has halted by itself
            elif not self._jog_once():
                self._drop("send failed")

    def _deadman_due(self) -> bool:
        if not self.deadman:
            return False
        with self._jog_cv:
            return self._armed and not _is_zero(self._jog) and time.monotonic() - self._jog_at > self.deadman

    def _send_zero_due(self) -> None:
        """The zero the caller asked for last, if it hasn't gone yet (close() sends it before it closes)."""
        with self._sock_lock:
            sock = self._sock
            if sock is None:
                return
            with self._jog_cv:
                if not (self._zero_due or (self._armed and _is_zero(self._jog) and self._jog_sent != "jog")):
                    return
                self._zero_due = False
                self._jog_sent = "jog"
            try:
                sock.sendall(b"jog\n")
                self.sent_vector = ZERO
            except OSError:
                pass

    def _jog_once(self) -> bool:
        """Sends the mailbox if a line is due; False if the send failed. Decided and sent under the socket lock, so a
        halt goes either before the decision or after the line, never between them."""
        now = time.monotonic()
        with self._sock_lock:
            sock = self._sock
            if sock is None:
                return True
            with self._jog_cv:
                due = self._zero_due
                vec = ZERO if due else self._jog
                if due:
                    line = "jog"
                elif not self._armed:
                    return True   # holding: the caller hasn't asked for zero since the halt / connect / MOTORS on
                else:
                    line = format_jog(*self._jog)
                    changed = line != self._jog_sent
                    if not changed and line == "jog":
                        return True   # a zero goes once
                    if now - self._last_jog_at < (self.jog_min_interval if changed else self.jog_refresh):
                        return True
                gen = self._jog_gen
            try:
                sock.sendall((line + "\n").encode("ascii"))
            except OSError:
                return False
            self._last_jog_at = now
            with self._jog_cv:
                if self._jog_gen == gen:   # nothing reset it while the line went out
                    self._jog_sent = line
                    self.sent_vector = vec
                    if due:
                        self._zero_due = False
        return True


def _is_zero(vec: tuple) -> bool:
    return format_jog(*vec) == "jog"   # as the unit will see it: below 0.05 um/s is 0

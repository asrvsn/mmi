"""Mirror the test server's simulated stage into Fusion 360, so a jog from mmi_app (keys or joystick) moves the
assembly on screen.

It runs only while Fusion is up with `mmi` open (`mmi.f3d` in the repo's root), with its stage joints "kin: rail",
"kin: X", "kin: Z", "kin: Y". It talks to Fusion through the MCP add-in at http://127.0.0.1:27182/mcp, with the
standard library only.

- **No queue.** One update is in flight at a time, and each carries the newest simulated pose (only the joints that
  changed). The next goes as soon as Fusion has answered the last, so the rate is whatever Fusion manages. A round trip
  is ~0.2 s (2026-10-21: ~120 ms for the MCP call, ~30 ms per joint solved, ~50 ms to redraw), so ~5 Hz for one axis.
  A busier Fusion means fewer, bigger steps; the picture never lags behind stale motion. `hz` optionally caps it.
- **The frames** (api.md 6.2): the unit's x and r are CAD -x, y is CAD -y, z is CAD +z. The joints are mm from the
  as-built pose, positive +x / +x / +z / +y, and so are the server's positions: all zero is the as-built pose. By
  default the server starts there, but with fine X 1 mm off its stop.
- **Limits.** The server's own switches stop each axis short of its hard stop (sim_stage.py). The joints also stop at
  the hard stops, in case: a joint takes the server's motion since it last looked (every 20 ms), stops at its stop,
  and leaves it as soon as the motion reverses. The console says so once each time an axis runs into one.
- **Linking.** It probes every few seconds until Fusion answers with the document and joints, and drops back to probing
  on any failure (Fusion closed, document closed, a timeout).
- **On stop** it puts the joints back to the as-built pose and reverts the pending position change, so `mmi` is left as it
  was found.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:27182/mcp"
DOC = "mmi"
JOINTS = ("rail", "X", "Z", "Y")
DEFAULT_LIMITS = {"rail": (-2.1, 149.4), "X": (0.0, 14.0), "Z": (-0.55, 13.45), "Y": (-6.5, 7.5)}   # mm, the kin: joints' limits in mmi
DEADBAND = 0.0005          # mm: a change smaller than this isn't sent
TRACK_S = 0.02             # s: how often the server's stage is followed, its stops applied


def joint_values(pos_um) -> dict[str, float]:
    """The server's position (x, y, z, r in um, the unit's frame) as joint values (mm, CAD +x / +x / +z / +y)."""
    x, y, z, r = pos_um
    return {"rail": -r / 1000.0, "X": -x / 1000.0, "Z": z / 1000.0, "Y": -y / 1000.0}


_PRELUDE = r'''
import adsk.core, adsk.fusion, sys
# Fusion's MCP add-in wraps stdout/stderr on every run and never unwraps; at our update rates the chain passes
# Python's recursion limit within minutes and any print then fails. Point this run's wrapper at the innermost stream.
for _n in ('stdout', 'stderr'):
    _s = _c = getattr(sys, _n)
    while hasattr(_c, '_original') and hasattr(_c._original, 'write'):
        _c = _c._original
    if _c is not _s and hasattr(_s, '_original'):
        _s._original = _c
def _joints():
    app = adsk.core.Application.get()
    doc = next((d for d in app.documents if d.name == %(doc)r), None)
    if doc is None: raise RuntimeError('no document named %(doc)s is open')
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    J = {j.name[5:]: j for j in des.rootComponent.allAsBuiltJoints if j.name.startswith('kin: ')}
    missing = [k for k in %(joints)r if k not in J]
    if missing: raise RuntimeError('stage joints missing: %%s (open mmi.f3d)' %% missing)
    return app, doc, des, J
'''

_PROBE = r'''
def run(_context):
    app, doc, des, J = _joints()
    out = {k: [J[k].jointMotion.slideLimits.minimumValue * 10, J[k].jointMotion.slideLimits.maximumValue * 10,
               J[k].jointMotion.slideValue * 10] for k in J}
    out['_active'] = app.activeDocument == doc
    with open(%(out)r, 'w') as f: f.write(__import__('json').dumps(out))
'''

_SET = r'''
def run(_context):
    app, doc, des, J = _joints()
    for k, v in %(values)r.items(): J[k].jointMotion.slideValue = v / 10
    if app.activeDocument == doc:
        adsk.doEvents(); app.activeViewport.refresh()
'''

_RESET = r'''
def run(_context):
    app, doc, des, J = _joints()
    for j in J.values(): j.jointMotion.slideValue = 0
    if des.snapshots.hasPendingSnapshot: des.snapshots.revertPendingSnapshot()
    if app.activeDocument == doc:
        adsk.doEvents(); app.activeViewport.refresh()
'''


class FusionError(Exception):
    pass


class McpLink:
    """A Fusion MCP session: `run(script)` runs a Fusion script and raises FusionError if it fails."""

    def __init__(self, url: str = URL, timeout: float = 5.0):
        self.url, self.timeout = url, timeout
        self._sid: str | None = None
        self._id = 0

    def _call(self, method: str, params=None, notify: bool = False):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._sid:
            headers["Mcp-Session-Id"] = self._sid
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            self._sid = r.headers.get("Mcp-Session-Id") or self._sid
            raw = r.read().decode()
        if notify:
            return None
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:]
        return json.loads(raw) if raw.strip() else None

    def open(self) -> None:
        self._sid = None
        self._call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                  "clientInfo": {"name": "mmi-test-server", "version": "1"}})
        self._call("notifications/initialized", notify=True)

    def run(self, script: str) -> None:
        r = self._call("tools/call", {"name": "fusion_mcp_execute",
                                      "arguments": {"featureType": "script", "object": {"script": script, "readOnly": False}}})
        try:
            text = r["result"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise FusionError("unexpected reply: %r" % (r,)) from None
        try:
            j = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(j, dict) and not j.get("success", True):
            err = str(j.get("error", ""))
            last = [l for l in err.strip().splitlines() if l.strip()]
            raise FusionError(last[-1] if last else "script failed")


class FusionMirror:
    """Mirrors `unit.pos` (a TestServer) into mmi's stage joints. `start()`, then `stop()`."""

    def __init__(self, unit, hz: float = 0.0, *, verbose: bool = True, link: McpLink | None = None,
                 probe_every: float = 3.0, log=print):
        self.unit, self.period = unit, (1.0 / hz if hz > 0 else 0.0)     # 0: as fast as Fusion answers
        self.verbose, self.probe_every, self.log = verbose, probe_every, log
        self.link = link or McpLink()
        self.linked = False
        self.limits = dict(DEFAULT_LIMITS)
        self.shown = {k: 0.0 for k in JOINTS}      # the stage as it would be, stopped at its stops (joint mm)
        self.sent: dict[str, float] = {}           # what Fusion shows now (joint mm)
        self.updates = 0                           # sent so far
        self.round_trip = 0.0                      # last one, s
        self._seen: dict[str, float] | None = None   # the server's pose at the last look, as joint mm
        self._at_limit: dict[str, bool] = {}
        self._burst = None                         # (start, updates at start, round-trip sum) of the motion under way
        self._reason: str | None = None
        self._last = 0.0
        self._lock = threading.Lock()              # shown, limits: the tracker and the sender
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._dir = tempfile.mkdtemp(prefix="fusion_mirror_")

    # ---- running it
    def start(self) -> "FusionMirror":
        self._threads = [threading.Thread(target=self._track_loop, daemon=True),
                         threading.Thread(target=self._loop, daemon=True)]
        for t in self._threads:
            t.start()
        return self

    def stop(self, reset: bool = True) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)
        if reset and self.linked:
            try:
                self.fusion_reset()
                self._say("fusion: stage joints back to the as-built pose")
            except Exception as e:                       # noqa: BLE001 - best effort on the way out
                self._say("fusion: couldn't reset the joints (%s)" % e)
        self.linked = False
        shutil.rmtree(self._dir, ignore_errors=True)

    # ---- the three things it asks of Fusion (tests replace these)
    def fusion_probe(self) -> dict:
        """{joint: [min, max, value] (mm)} and '_active', or raises if Fusion, the document or the joints are missing"""
        out = os.path.join(self._dir, "probe.json")
        try:
            os.remove(out)
        except OSError:
            pass
        self.link.open()
        self.link.run(self._script(_PROBE, out=out))
        with open(out) as f:
            return json.load(f)

    def fusion_set(self, values: dict[str, float]) -> None:
        self.link.run(self._script(_SET, values=values))

    def fusion_reset(self) -> None:
        self.link.run(self._script(_RESET))

    # ---- internals
    def _say(self, text: str) -> None:
        if self.verbose:
            self.log(text, flush=True) if self.log is print else self.log(text)

    def _script(self, body: str, **kw) -> str:
        return _PRELUDE % {"doc": DOC, "joints": JOINTS} + body % kw if kw else _PRELUDE % {"doc": DOC, "joints": JOINTS} + body

    def _unlink(self, why: str) -> None:
        if self.linked or why != self._reason:
            self._say("fusion: not mirroring (%s)" % why)
        self.linked, self._reason = False, why

    def _probe(self) -> None:
        try:
            info = self.fusion_probe()
        except (OSError, urllib.error.URLError, FusionError, ValueError, KeyError) as e:
            self._unlink(str(getattr(e, "reason", e)))
            return
        with self._lock:
            self.limits = {k: (info[k][0], info[k][1]) for k in JOINTS}
            self.shown = {k: min(max(v, self.limits[k][0]), self.limits[k][1]) for k, v in self.shown.items()}
        self.sent = {k: info[k][2] for k in JOINTS}
        self.linked, self._reason = True, None
        self._say("fusion: mirroring into %s (rail, X, Z, Y joints)%s, %s" % (
            DOC, "" if info.get("_active") else "; switch to its tab to watch",
            "as fast as Fusion answers" if not self.period else "at most %.0f Hz" % (1.0 / self.period)))

    def track(self) -> dict[str, float]:
        """Follow the server's stage; returns the pose Fusion should show (joint mm). Each joint takes the server's
        motion since the last look, stopping at its stop, so reversing leaves the stop at once, as a real stage does.
        The first look takes the server's position as it is (its power-up position is the as-built pose)."""
        with self.unit._lock:
            now = joint_values(self.unit.pos)
        with self._lock:
            seen, self._seen = self._seen, now
            for k, v in now.items():
                lo, hi = self.limits[k]
                free = v if seen is None else self.shown[k] + v - seen[k]
                c = min(max(free, lo), hi)
                hit = c != free
                if hit != self._at_limit.get(k, False):
                    self._at_limit[k] = hit
                    if hit and self.linked:
                        self._say("fusion: %s at its %s stop (%.2f mm)" % (k, "low" if free < lo else "high", c))
                self.shown[k] = c
            return dict(self.shown)

    def _track_loop(self) -> None:
        while not self._stop.is_set():
            self.track()
            self._stop.wait(TRACK_S)

    def _loop(self) -> None:
        next_probe = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if not self.linked:
                if now >= next_probe:
                    self._probe()
                    next_probe = time.monotonic() + self.probe_every
                self._stop.wait(0.1)
                continue
            with self._lock:
                want = dict(self.shown)
            delta = {k: v for k, v in want.items() if abs(v - self.sent.get(k, 0.0)) > DEADBAND}
            if not delta:
                if self._burst and now - self._last > 1.0:     # motion over: one line on how it went
                    t_start, n0, rts = self._burst
                    n = self.updates - n0
                    if n:
                        self._say("fusion: %d updates in %.1f s (%.1f Hz), round trip %.0f ms" % (
                            n, self._last - t_start, n / max(self._last - t_start, 1e-9), 1000 * rts / n))
                    self._burst = None
                self._stop.wait(0.05)
                continue
            if self._burst is None:
                self._burst = (now, self.updates, 0.0)
            t0 = time.monotonic()
            try:
                self.fusion_set(delta)
            except (OSError, urllib.error.URLError, FusionError, ValueError) as e:
                self._unlink(str(getattr(e, "reason", e)))
                next_probe = time.monotonic() + self.probe_every
                continue
            self.sent.update(delta)
            self.updates += 1
            self.round_trip = time.monotonic() - t0
            self._last = time.monotonic()
            self._burst = (self._burst[0], self._burst[1], self._burst[2] + self.round_trip)
            if self.period:                                             # an optional cap; one in flight either way
                self._stop.wait(max(0.0, self.period - self.round_trip))

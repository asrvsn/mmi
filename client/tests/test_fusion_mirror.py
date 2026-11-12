"""The Fusion mirror without Fusion: its three calls to Fusion replaced by fakes."""
import threading
import time

from mmi_client.fusion_mirror import FusionError, FusionMirror, joint_values
from mmi_client.test_server import TestServer

LIMITS = {"rail": [-2.1, 149.4, 0.0], "X": [0.0, 14.0, 0.0], "Z": [-0.55, 13.45, 0.0], "Y": [-6.5, 7.5, 0.0]}
AS_BUILT = (0.0, 0.0, 0.0, 0.0)   # the server started on the as-built pose (by default fine x starts 1 mm off its stop)


class FakeFusion(FusionMirror):
    """Records what it would send; each set takes `rt` seconds, like a round trip."""

    def __init__(self, unit, hz=0.0, rt=0.1, up=True):
        super().__init__(unit, hz, verbose=False, probe_every=0.1)
        self.rt, self.up = rt, up
        self.sets: list[tuple[float, dict]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.resets = 0
        self.lock = threading.Lock()

    def fusion_probe(self):
        if not self.up:
            raise FusionError("no document named mmi is open")
        return dict(LIMITS, _active=True)

    def fusion_set(self, values):
        with self.lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        time.sleep(self.rt)
        with self.lock:
            self.in_flight -= 1
            self.sets.append((time.monotonic(), dict(values)))

    def fusion_reset(self):
        self.resets += 1


def wait_for(cond, timeout=3.0):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_the_frames():
    # the unit's x and r are CAD -x, y is CAD -y, z is CAD +z (api.md 6.2); um to mm
    assert joint_values([1000, 2000, 3000, 4000]) == {"rail": -4.0, "X": -1.0, "Z": 3.0, "Y": -2.0}


def test_the_server_starts_fine_x_off_its_stop():
    m = FakeFusion(TestServer())                    # by default: the as-built pose, fine x 1 mm off its stop
    assert m.track() == {"rail": 0.0, "X": 1.0, "Z": 0.0, "Y": 0.0}


def test_newest_pose_one_at_a_time():
    unit = TestServer(start=AS_BUILT)
    m = FakeFusion(unit, rt=0.15).start()
    try:
        assert wait_for(lambda: m.linked)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:          # the stage keeps moving faster than Fusion can follow
            with unit._lock:
                unit.pos[3] -= 500.0                # r -0.5 mm: rail +0.5 mm
            time.sleep(0.01)
        final = joint_values(unit.pos)["rail"]
        assert wait_for(lambda: m.sets and abs(m.sets[-1][1].get("rail", -1) - final) < 1e-9)
        assert m.max_in_flight == 1                 # never a queue
        gaps = [b[0] - a[0] for a, b in zip(m.sets, m.sets[1:])]
        assert min(gaps) >= 0.15 - 0.02             # paced by the round trip alone
        assert len(m.sets) <= 1.0 / 0.15 + 2        # ~1 s at a 0.15 s round trip: a few big steps, not hundreds
        assert all(set(v) == {"rail"} for _, v in m.sets)   # only the joint that moved
    finally:
        m.stop()
    assert m.resets == 1                            # the as-built pose put back on the way out


def test_clamped_to_the_joints_limits():
    unit = TestServer(start=AS_BUILT)
    m = FakeFusion(unit, rt=0.01).start()
    try:
        assert wait_for(lambda: m.linked)
        with unit._lock:
            unit.pos[0] = 20000.0                   # x +20 mm: X -20, past its 0 stop
            unit.pos[2] = 20000.0                   # z +20 mm: Z +20, past its 13.45 stop
        assert wait_for(lambda: m.sent.get("X") == 0.0 and m.sent.get("Z") == 13.45)
        with unit._lock:
            unit.pos[0] = 19000.0                   # x back 1 mm: X off its stop at once, not after 19 mm
        assert wait_for(lambda: abs(m.sent.get("X", 0.0) - 1.0) < 1e-9)
    finally:
        m.stop()


def test_a_stop_holds_and_reversing_leaves_it():
    unit = TestServer(start=AS_BUILT)
    m = FakeFusion(unit)                            # not started: looked at by hand
    assert m.track() == {"rail": 0.0, "X": 0.0, "Z": 0.0, "Y": 0.0}   # where the server started: the as-built pose
    unit.pos[0] = -20000.0                          # x -20 mm: X +20, past its 14 stop
    assert m.track()["X"] == 14.0
    unit.pos[0] = -19000.0                          # +1 mm: X leaves the stop at once
    assert m.track()["X"] == 13.0
    unit.pos[0] = 5000.0                            # +24 mm: X -24 from 13, onto its 0 stop
    assert m.track()["X"] == 0.0
    unit.pos[0] = 4000.0
    assert m.track()["X"] == 1.0
    unit.pos[3] = -10000.0                          # r -10 mm: the rail +10; the others untouched
    assert m.track() == {"rail": 10.0, "X": 1.0, "Z": 0.0, "Y": 0.0}


def test_not_linked_without_fusion_and_no_reset():
    unit = TestServer(start=AS_BUILT)
    m = FakeFusion(unit, up=False).start()
    try:
        with unit._lock:
            unit.pos[3] = -10000.0
        time.sleep(0.4)
        assert not m.linked and m.sets == []
    finally:
        m.stop()
    assert m.resets == 0


def test_an_optional_cap():
    unit = TestServer(start=AS_BUILT)
    m = FakeFusion(unit, hz=4.0, rt=0.02).start()
    try:
        assert wait_for(lambda: m.linked)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            with unit._lock:
                unit.pos[3] -= 100.0
            time.sleep(0.01)
        time.sleep(0.3)
        gaps = [b[0] - a[0] for a, b in zip(m.sets, m.sets[1:])]
        assert gaps and min(gaps) >= 0.25 - 0.03    # 4 Hz, though Fusion could have gone faster
    finally:
        m.stop()

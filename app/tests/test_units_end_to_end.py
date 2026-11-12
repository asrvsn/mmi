"""Units, end to end, with Fusion stood in for:
- the 8BitDo's stick held for a known time;
- the app's jog in µm/s, and the line on the wire;
- the test server's stage in µm;
- the Fusion mirror's joints in mm;
- Fusion's own unit, cm.

The mirror's own Fusion scripts run here, against a stand-in for as much of the adsk API as they use.
"""
import sys
import time
import types

from helpers import wait_for

from mmi_client import MmiClient
from mmi_client.fusion_mirror import DOC, FusionMirror
from mmi_client.test_server import TestServer

from mmi_app.hand import HandControl
from mmi_app.inputs import Inputs, Keyboard
from mmi_app.pad import PadState
from mmi_app.shaping import Mapping

ULTIMATE = dict(connected=True, name="8BitDo Ultimate 2C Wired Controller", ids=(0x2DC8, 0x310A))
STOPS_MM = {"rail": (-2.1, 149.4), "X": (0.0, 14.0), "Z": (-0.55, 13.45), "Y": (-6.5, 7.5)}   # the kin: joints' limits in mmi


def stand_in_fusion(monkeypatch) -> dict:
    """adsk as the mirror's scripts see it: mmi open, with its four stage joints. Returns their motions, whose
    values and limits are in cm, as Fusion keeps lengths."""
    motions = {k: types.SimpleNamespace(slideValue=0.0, slideLimits=types.SimpleNamespace(
        minimumValue=lo / 10, maximumValue=hi / 10)) for k, (lo, hi) in STOPS_MM.items()}
    joints = [types.SimpleNamespace(name="kin: " + k, jointMotion=m) for k, m in motions.items()]
    design = types.SimpleNamespace(rootComponent=types.SimpleNamespace(allAsBuiltJoints=joints),
                                   snapshots=types.SimpleNamespace(hasPendingSnapshot=False,
                                                                   revertPendingSnapshot=lambda: None))
    doc = types.SimpleNamespace(name=DOC, products=types.SimpleNamespace(itemByProductType=lambda _kind: design))
    app = types.SimpleNamespace(documents=[doc], activeDocument=doc,
                                activeViewport=types.SimpleNamespace(refresh=lambda: None))
    adsk = types.ModuleType("adsk")
    adsk.core, adsk.fusion = types.ModuleType("adsk.core"), types.ModuleType("adsk.fusion")
    adsk.core.Application = types.SimpleNamespace(get=lambda: app)
    adsk.fusion.Design = types.SimpleNamespace(cast=lambda product: product)
    adsk.doEvents = lambda: None
    for name, module in (("adsk", adsk), ("adsk.core", adsk.core), ("adsk.fusion", adsk.fusion)):
        monkeypatch.setitem(sys.modules, name, module)
    return motions


class HereLink:
    """The MCP link, except each script runs here, the way Fusion runs one: its `run(context)`."""

    def open(self) -> None:
        pass

    def run(self, script: str) -> None:
        ns: dict = {}
        exec(compile(script, "<fusion script>", "exec"), ns)
        ns["run"](None)


class Pad:
    def __init__(self):
        self.state = PadState(**ULTIMATE)

    def poll(self) -> PadState:
        return self.state


def body(line: str) -> str:
    head, _, rest = line.partition(" ")
    return rest if head.isdigit() and rest else line


def test_a_stick_held_moves_fusion_by_speed_times_time(monkeypatch):
    motions = stand_in_fusion(monkeypatch)
    with TestServer("127.0.0.1", 0) as unit:
        mirror = FusionMirror(unit, verbose=False, link=HereLink(), probe_every=0.05).start()
        client = MmiClient(unit.host, unit.port, reconnect=False)
        client.connect()
        pad = Pad()
        mapping = Mapping()
        mapping.cap(client.params)
        hand = HandControl(client, Inputs(Keyboard(), pad), mapping)

        def hold(state: PadState, seconds: float) -> None:   # the app's input timer, every 10 ms
            pad.state = state
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                hand.step()
                time.sleep(0.01)

        try:
            assert wait_for(lambda: mirror.linked, timeout=2.0)
            hold(PadState(**ULTIMATE), 0.1)                              # at rest first: the client's let-go
            hold(PadState(**ULTIMATE, rx=-1.0, ly=1.0, lt=1.0), 0.6)     # right stick left, left stick up, the rail -x
            hold(PadState(**ULTIMATE), 0.2)

            # the app: full deflection is each axis's top speed, in µm/s, and that is what goes on the wire
            moving = "jog x=-250 z=250 r=-5000"
            assert moving in unit.commands("jog") and unit.commands("jog")[-1] == "jog"
            lines = [(t, body(l)) for t, l in unit.received]
            on = next(t for t, b in lines if b == moving)
            off = next(t for t, b in lines if t > on and b == "jog")
            held = off - on                                              # s, by the server's clock

            # the server: µm/s for that long, in µm (to within its 5 ms tick at each end), from where it started
            x, y, z, r = unit.pos
            x0, y0, z0, r0 = unit.stage.start               # the as-built pose, fine x 1 mm off its stop
            for got, speed in ((x - x0, -250.0), (z - z0, 250.0), (r - r0, -5000.0)):
                assert abs(got - speed * held) <= abs(speed) * 0.012, (got, speed, held)
            assert y == y0 == 0.0

            # Fusion: the joints in cm (api.md 6.2: rail = -r, X = -x, Z = +z, Y = -y), from the as-built pose
            want_cm = {"rail": -r / 1e4, "X": -x / 1e4, "Z": z / 1e4, "Y": -y / 1e4}
            assert wait_for(lambda: all(abs(motions[k].slideValue - v) < 1e-4 for k, v in want_cm.items()))
            moved_x = motions["X"].slideValue + x0 / 1e4    # X began 0.1 cm along
            assert abs(moved_x - 0.025 * held) < 0.025 * held * 0.02                  # 0.25 mm/s, towards CAD +x
            assert abs(motions["rail"].slideValue - 0.5 * held) < 0.5 * held * 0.02   # 5 mm/s, towards the motor
            assert motions["Z"].slideValue > 0 and motions["Y"].slideValue == 0
        finally:
            client.close()
            mirror.stop()
    assert all(m.slideValue == 0 for m in motions.values())   # the as-built pose put back on the way out

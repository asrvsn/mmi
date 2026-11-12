"""The test server (its defaults, the simulated incubator, the command line) and the client's records and drive state."""
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import wait_for

from mmi_client import ZERO, MmiClient, MmiError, parse_addr
from mmi_client.test_server import TestServer, default_params


def _flatten(d, prefix=""):
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            yield from _flatten(v, key + ".")
        else:
            yield key, v


def _as_firmware_sends(v):
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, list):
        return ",".join(_as_firmware_sends(x) for x in v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return f"{v:.7g}" if isinstance(v, float) else str(v)


def test_defaults_match_the_firmware():
    yaml = pytest.importorskip("yaml")
    path = Path(__file__).resolve().parents[2] / "firmware" / "sd" / "parameters.yaml"   # mmi/firmware
    if not path.exists():
        pytest.skip("no firmware/ beside the client")
    with open(path) as f:
        firmware = dict(_flatten(yaml.safe_load(f)))
    assert {k: _as_firmware_sends(v) for k, v in firmware.items()} == default_params()


def test_parse_addr():
    assert parse_addr("10.0.0.5:6000") == ("10.0.0.5", 6000)
    assert parse_addr("10.0.0.5") == ("10.0.0.5", 5000)
    assert parse_addr(":6000") == ("192.168.50.2", 6000)
    assert parse_addr("6000", "127.0.0.1") == ("127.0.0.1", 6000)


def test_records_and_a_topic_the_unit_lacks(unit):
    c = MmiClient(unit.host, unit.port, reconnect=False, watch_hz=5)
    c.watch(10, "light")
    c.watch(1, "bogus")                       # a topic this firmware doesn't have
    c.connect()
    try:
        assert c.unsupported == {"bogus"} and c.connected
        assert wait_for(lambda: "light" in c.records and "lid" in c.records and c.state is not None)   # the lid unasked
        light, lid = c.records["light"], c.records["lid"]
        assert light["warm"] == 0 and light["cool"] == 0          # dark, as the unit starts
        assert lid["mode"] == "idle" and lid["duty"] == 0 and 20 < lid["med.j2"] < 26
        with pytest.raises(MmiError) as e:    # asked for while connected: the refusal is the caller's to see
            c.watch(1, "bogus")
        assert e.value.code == "arg"
    finally:
        c.close()


def test_every_command_accepted_or_not(unit):
    c = MmiClient(unit.host, unit.port, reconnect=False)
    c.connect()
    try:
        with pytest.raises(MmiError) as e:    # strict, as the firmware: err unknown
            c.request("prog load day")
        assert e.value.code == "unknown"
        unit.accept_all = True                # as from the command line
        assert c.request("prog load day").ok
        with pytest.raises(MmiError) as e:    # a command it has is the firmware's, strict either way
            c.request("lid temp=25")
        assert e.value.code == "arg"
    finally:
        c.close()


def test_sent_vector_and_awaiting_release(client, unit):
    client.jog(x=40, r=-500)
    assert wait_for(lambda: client.sent_vector == (40.0, 0.0, 0.0, -500.0))
    client.halt()
    assert client.sent_vector == ZERO
    client.jog(x=40)                          # still pushing after the halt: held back
    assert client.awaiting_release
    client.jog()
    assert not client.awaiting_release
    assert wait_for(lambda: not unit.halted)


def test_the_console_shows_each_command_once(capsys):
    with TestServer(verbose=True) as unit:
        c = MmiClient(unit.host, unit.port, reconnect=False, watch_hz=10)
        c.connect()
        try:
            c.jog(x=10)
            assert wait_for(lambda: unit.targets[0] == 10)
            time.sleep(0.3)                   # six re-sends inside the lease
            c.jog()
            assert wait_for(lambda: unit.targets[0] == 0)
        finally:
            c.close()
    out = capsys.readouterr().out
    assert out.count("<-  jog x=10") == 1 and "!drive" not in out and "connected" in out


def test_the_command_line():
    p = subprocess.Popen([sys.executable, "-m", "mmi_client.test_server", "127.0.0.1:0", "-q", "--no-fusion"],
                         stdout=subprocess.PIPE, text=True)   # never the Fusion that may be open on this computer
    try:
        first = p.stdout.readline()
        port = int(re.search(r"127\.0\.0\.1:(\d+)", first).group(1))
        c = MmiClient("127.0.0.1", port, reconnect=False)
        c.connect()
        try:
            assert c.hello.fw == "test" and c.request("prog start").ok   # every command accepted
        finally:
            c.close()
    finally:
        p.terminate()
        p.wait(timeout=5)

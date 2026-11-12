import pytest

from mmi_client import MmiClient
from mmi_client.test_server import TestServer


@pytest.fixture
def unit():
    u = TestServer("127.0.0.1", 0, cfg=("x.vmax rejected (line 3): '999' out of range",))
    u.start()
    yield u
    u.stop()


@pytest.fixture
def client(unit):
    c = MmiClient(unit.host, unit.port, reconnect=True, retry_s=0.05)
    c.connect()
    yield c
    c.close()

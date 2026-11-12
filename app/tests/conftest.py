import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no window on the screen while testing
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt6")
os.environ.setdefault("QT_API", "pyqt6")   # QtAwesome's QtPy

import pytest  # noqa: E402

from mmi_client import MmiClient  # noqa: E402
from mmi_client.test_server import TestServer  # noqa: E402


@pytest.fixture
def unit():
    u = TestServer("127.0.0.1", 0)
    u.start()
    yield u
    u.stop()


@pytest.fixture
def client(unit):
    c = MmiClient(unit.host, unit.port, reconnect=True, retry_s=0.05)
    c.connect()
    yield c
    c.close()

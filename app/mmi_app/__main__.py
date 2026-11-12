"""python -m mmi_app: the operator's window onto the mmi, the stage-top unit.

    python -m mmi_app                       # the unit at 192.168.50.2:5000
    python -m mmi_app 127.0.0.1             # a test server on this computer (python -m mmi_client.test_server)
    python -m mmi_app --config my.toml      # settings: config.example.toml

It drives with the 8BitDo Ultimate 2C or a PS5 DualSense whenever one's attached (B or Circle halts, HOME or PS is
the menu), else with the keyboard (profiles.py): plugging one in or out switches by itself.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from mmi_client import MmiClient, parse_addr

from .config import load


def no_app_nap() -> None:
    """On a Mac: opt out of App Nap, which slows the timers of an app whose window is hidden (behind Fusion, say). The
    input timer would stall, and the deadman halt the unit. Elsewhere it does nothing."""
    if sys.platform != "darwin":
        return
    try:
        import appnope
        appnope.nope()
    except Exception as e:   # not installed, or an old macOS
        logging.warning("App Nap stays on (%s): keep the window in view while driving", e)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mmi_app", description=__doc__.split("\n\n")[0])
    ap.add_argument("addr", nargs="?", help="the unit: host:port, host or port (default 192.168.50.2:5000, or the "
                                            "config's)")
    ap.add_argument("--config", help="a TOML settings file (see config.example.toml)")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="log connects, drops, the input and the menu button")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load(a.config)
    host, port = parse_addr(a.addr, cfg.host, cfg.port) if a.addr else (cfg.host, cfg.port)

    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt6")
    os.environ.setdefault("QT_API", "pyqt6")   # QtAwesome's QtPy, likewise
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from .controller import Controller
    from .profiles import pad_ids
    from .window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("mmi")
    no_app_nap()
    pad = None
    try:
        from .pad import SdlGamepad
        pad = SdlGamepad(accept=pad_ids())   # profiles.PADS only; any other pad is ignored
    except Exception as e:   # no pygame, no SDL: the keyboard still drives
        logging.warning("no gamepad reader (%s): the keyboard only", e)
    client = MmiClient(host, port, reconnect=True, watch_hz=cfg.drive_hz, deadman=cfg.deadman, retry_s=0.5)
    client.watch(cfg.env_hz, "light")   # the lid's readouts come by themselves, once a second
    window = MainWindow(cfg.window_s, (cfg.width, cfg.height))
    controller = Controller(window, client, cfg, pad)
    for sig in (signal.SIGINT, signal.SIGTERM):           # Ctrl-C in the terminal, or systemd stopping it: quit,
        signal.signal(sig, lambda *_: app.quit())         # letting go and closing the link on the way out
    wake = QTimer()
    wake.timeout.connect(lambda: None)                    # so Python gets to see that signal
    wake.start(200)
    if a.fullscreen or cfg.fullscreen:
        window.showFullScreen()
    else:
        window.show()
    controller.start()
    try:
        return app.exec()
    finally:
        controller.stop()


if __name__ == "__main__":
    sys.exit(main())

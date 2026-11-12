"""The app's settings: built-in defaults, overridden by a TOML file (see config.example.toml)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from mmi_client import DEFAULT_HOST, DEFAULT_PORT

from .shaping import Mapping, Shaping


@dataclass
class Config:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    drive_hz: int = 5          # how often the unit sends the drive record
    env_hz: int = 2            # ... and the light record, for the plots (the lid's comes by itself, once a second)
    poll_hz: int = 100         # how often the controls are read (the buttons are the input profile's: profiles.py)
    deadman: float = 0.25      # s: the window stalling this long mid-move halts the unit
    slow: float = 0.5          # a key's deflection, as a fraction of a stick's throw
    fast: float = 1.0          # ... with Shift held
    width: int = 800           # the HyperPixel's landscape size
    height: int = 480
    fullscreen: bool = False
    window_s: float = 600.0    # how much history the plots show
    mapping: Mapping = field(default_factory=Mapping)


def _fraction(v, what: str) -> float:
    v = float(v)
    if not 0 < v <= 1:
        raise ValueError(f"{what}: more than 0, at most 1")
    return v


def load(path: str | None) -> Config:
    cfg = Config()
    if not path:
        return cfg
    with open(path, "rb") as f:
        d = tomllib.load(f)
    unit, pad, kb = d.get("unit", {}), d.get("gamepad", {}), d.get("keyboard", {})
    view, screen, plots = d.get("view", {}), d.get("screen", {}), d.get("plots", {})
    cfg.host = unit.get("host", cfg.host)
    cfg.port = int(unit.get("port", cfg.port))
    cfg.drive_hz = int(unit.get("drive_hz", cfg.drive_hz))
    cfg.env_hz = int(unit.get("env_hz", cfg.env_hz))
    cfg.poll_hz = int(pad.get("poll_hz", cfg.poll_hz))
    cfg.deadman = float(pad.get("deadman", cfg.deadman))
    if not 0.05 <= cfg.deadman <= 1.0:
        raise ValueError("[gamepad] deadman: 0.05 to 1 s")
    cfg.slow = _fraction(kb.get("slow", cfg.slow), "[keyboard] slow")
    cfg.fast = _fraction(kb.get("fast", cfg.fast), "[keyboard] fast")
    for name in ("xy", "z", "r"):
        sh: Shaping = getattr(cfg.mapping, name)
        for k, v in d.get(name, {}).items():
            if not hasattr(sh, k):
                raise ValueError(f"[{name}] {k}: one of deadband, v_min, v_max")
            setattr(sh, k, float(v))
    cfg.mapping.rotation = int(view.get("rotation", 0))
    if cfg.mapping.rotation % 90:
        raise ValueError("[view] rotation: 0, 90, 180 or 270")
    cfg.mapping.mirror = bool(view.get("mirror", False))
    cfg.width = int(screen.get("width", cfg.width))
    cfg.height = int(screen.get("height", cfg.height))
    cfg.fullscreen = bool(screen.get("fullscreen", cfg.fullscreen))
    cfg.window_s = float(plots.get("window_s", cfg.window_s))
    return cfg

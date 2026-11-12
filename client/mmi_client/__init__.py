"""mmi_client: a client for the stage-top micromanipulator-incubator unit (mmi/firmware/api.md).

    from mmi_client import MmiClient
    with MmiClient("192.168.50.2") as unit:      # connects, shakes hands, reads the parameters
        unit.jog(x=40)                            # µm/s; kept alive inside the unit's lease until changed
        ...
        unit.halt()                               # now; then nothing moves until jog() is asked for zero

Anything that drives the unit (the app, a script, an agent) goes through this; it's the standard library only.
`python -m mmi_client.test_server` stands in for the hardware.
"""
from .client import DEFAULT_HOST, DEFAULT_PORT, ZERO, MmiClient, MmiDisconnected, MmiError, MmiTimeout, parse_addr
from .protocol import (AxisState, Blocks, DriveState, Event, Hello, RailState, Reply, format_jog, format_lid,
                       format_light)

__all__ = ["MmiClient", "MmiError", "MmiTimeout", "MmiDisconnected", "DriveState", "AxisState", "RailState", "Event",
           "Hello", "Reply", "Blocks", "format_jog", "format_light", "format_lid", "parse_addr", "ZERO", "DEFAULT_HOST",
           "DEFAULT_PORT"]
__version__ = "0.5.0"

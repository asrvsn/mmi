# mmi_client

The Python client library for the mmi (the stage-top unit, `../firmware/`), and a test server that stands in for it.
It's the standard library only, so any Python 3.11+ can use it: the operator's app (`../app/`), a script, a
ZEN-side hook, an MCP server.

| | |
|---|---|
| `mmi_client.MmiClient` | the link, and the client rules of `../firmware/api.md` (0.11) |
| `mmi_client.protocol` | the line format: parsing and formatting, no I/O |
| `mmi_client.test_server` | the unit at the protocol level, with no hardware: `python -m mmi_client.test_server` |

**Install:** the `mmi` conda env installs it (`../env.yml`, both packages, editable). For any other Python:
`pip install -e mmi/client`.

## The test server

```sh
python -m mmi_client.test_server                  # on 127.0.0.1:5000
python -m mmi_client.test_server 0.0.0.0:5000     # reachable from other machines too (e.g. from the Pi)
python -m mmi_client.test_server --motors-off     # as if SW11 were off
python -m mmi_client.test_server --strict         # err unknown to what the firmware doesn't have, as it does
```

- **The drive as the firmware has it:**
  - `jog` with its lease (a jog not renewed within 250 ms halts);
  - `halt` and its hold, until an all-zero jog;
  - a session lost mid-move halts;
  - `!jog refused` once per reason;
  - MOTORS, `get`, `status drive`, `watch drive`;
  - the newest connection replacing the old.
- **The stage's travel and limit switches** (`mmi_client/sim_stage.py`), where the model puts them:
  - Fine x, y and z trip 0.4 mm inside their hard stops. As on the unit, a fine axis's end is inferred from the way
    it was moving; an input found open with nothing moving is `?` (both ways, at no more than 20 µm/s).
  - The rail's MAX (the carriage's, the bearing end) trips at r = +0.6 mm. MIN (the effector's, the motor end) trips
    when rail + fine x reach -109.9 mm, and blocks -x on both.
  - A switch closes again 0.2 mm back (snap action; an assumed figure). A fine axis driven into its hard stop
    (only from `?`) stalls after 50 ms.
  - `!limit` and `!stall` go out whenever their state changes, and in full after every `!hello`, as from the unit.
  - **Where it starts:** the as-built pose, except fine x 1 mm off its stop (there, its switch would be open). So
    the rail's MAX is 0.6 mm to the right, z's bottom switch 0.15 mm down, fine x's + switch 0.6 mm to the right.
  - `TestServer(start=...)` and `place(x=..., r=...)` (µm from the as-built pose) put it elsewhere, for tests.
- **The lights, as the firmware has them** (`api.md` 9.1): `light [warm=<%>] [cool=<%>]`, strict, each level set to
  the nearest of its 4097 steps; dark at the start; `!light` after every `!hello` and on every change; `status light`
  and `watch light`.
- **The lid by hand, as the firmware has it** (`api.md` 9.2, `mmi_client/sim_lid.py`):
  - `lid duty=<%>` / `lid off`, strict; capped at `lid.duty_max` (50), set to the nearest of 4096 steps, under 2 % 0;
  - the unit's cuts: a board NTC at 60 °C, a medium probe at `lid.medium_max` (40), a board sensor open, shorted or
    out of 0-100 °C, a probe shorted or come loose, the lid unplugged; each refuses another duty while it lasts, and 0
    always goes;
  - SW10 off holds the drive off and keeps the duty; the duty dropped when the session that set it ends;
  - the five NTCs sampled at 16 Hz and read out once a second, each a mean and its SD (`board.a`, `board.a.sd`, ...);
  - `!lid` after every `!hello`, with every readout and on every change of `sw`, `mode`, `duty` or `cut`;
    `status lid`; `watch lid` refused, as the firmware does;
  - **behind it, the lid's thermal model** (a simplified thermal model of the board), in real
    time from a 22 °C room: at 50 % the board warms ~3 K in 10 s and ~10 K in two minutes, near 53 °C within the
    hour; the medium follows far behind. Each sample carries ~4 mK of noise.
  - **For tests:** `set_lid_switch(on)`, `unplug_lid()` / `plug_lid()`, `lid_sensor(name, "open" | "short" | None)`,
    `lid_temps(board=, medium=)`. The last two restart the readout's second, so the next readout says them whole.
- **Every other command is answered `ok`,** as though the unit had it (unless `--strict`).
- **The stage, mirrored in Fusion 360** (`mmi_client/fusion_mirror.py`): whenever Fusion is up with `mmi` and its
  stage joints open (`mmi.f3d` in the repo's root), the simulated stage drives the "kin: rail / X / Z / Y" joints, so a
  jog from `mmi_app` moves the model on screen.
  - **One update at a time, always the newest pose.** Fusion sets the pace, with no queue. Measured 2026-10-21:
    - Y ~8 Hz, X ~5 Hz (130-180 ms round trips);
    - the rail ~2.5 Hz, since it carries the whole carriage;
    - `--fusion-hz` can cap it.
  - **Frames and limits.** The unit's frame is mapped to the CAD's (api.md 6.2). The server's positions are µm from
    the as-built pose; it starts there, with fine X 1 mm off its stop.
    - The server's switches stop each axis short of its hard stop, as the real stage's would. The mirror also stops
      each joint at its own hard stop, in case.
  - **On Ctrl-C** the joints go back to the as-built pose.
  - **Off with `--no-fusion`.** It also stays off on its own when Fusion isn't running. It isn't a collision check.
- **Its console** shows each command once: the lease's re-sends of the same jog, the periodic records, the replies
  and the pings are left out.

## The library

```python
import time
from mmi_client import MmiClient

with MmiClient("192.168.50.2") as unit:   # connects, shakes hands, reads every parameter
    print(unit.hello.fw, unit.params["x.vmax"], unit.status().r.drv)
    unit.jog(x=20)                         # µm/s, every axis at once; kept alive until changed
    time.sleep(2)
    unit.jog()                             # all zero: a ramped stop
    unit.jog(z=-5)
    unit.halt()                            # stop now; nothing more moves until jog() is asked for zero
```

What it does under the hood, so a front end needn't:
- **Jogs go through a mailbox.** `jog()` only sets the latest vector and returns.
  - A sender thread sends it on a change (at most every 20 ms), and re-sends a non-zero one every 50 ms, inside the
    unit's 250 ms lease (a lease left to run out halts the unit).
  - A zero goes once. Jogs never queue, so nothing lags behind a stale vector.
- **A vector holds until it's changed or the client is closed,** whatever the calling thread does meanwhile
  (`time.sleep`, a long computation).
  - `del` alone doesn't close it: the client's own threads keep it alive. End with `close()` or a `with` block.
  - A program that exits or dies mid-move closes its connection, and the unit halts.
- **The deadman, for a loop:** with `deadman=s`, a non-zero vector that `jog()` hasn't renewed within s seconds
  halts the unit. So a front end whose loop hangs (and the halt button it reads with it) can't leave a move running.
  The app sets 0.25 s; a script that sets a vector and sleeps leaves it off.
- **`halt()` goes now:** written to the socket from the caller's own thread, ahead of any jog.
- **Nothing moves until the caller lets go.** After a halt, a (re)connect or MOTORS coming on, a vector set before it
  isn't sent. Nothing goes out until `jog()` has been asked for zero, and that zero goes first: it's what releases
  the unit's halt. `awaiting_release` says when a vector is being held back like this.
- **A dead link is noticed.**
  - A pulled cable shows no error for minutes (TCP keeps retrying). So after `keepalive` seconds (1) with nothing
    from the unit, a ping goes out, and no reply within `timeout` (1 s) drops the link.
  - The unit has halted by then: it saw the link go, or its jog lease ran out.
- **The link:**
  - TCP with Nagle off; replies matched to commands by tag; an `api` version check.
  - With `reconnect=True` (the default), a dropped link is restored in the background. The exception is when another
    client has taken the unit (`!bye replaced`): then it stays off.
- **Records:** `watch(hz, topic)` for any topic.
  - The drive's record is in `state`; every other topic's latest is in `records`.
  - Watches are set again after a reconnect. A topic the unit doesn't have yet (the firmware has only `drive` so
    far) is noted in `unsupported` rather than failing the connection.
- **Closing** while a non-zero vector is asked for abandons the move: the unit halts. A zero asked for last goes out
  before the link closes, so `jog()` then `close()` is a ramped stop. Lights and a lid duty still to be set go out
  first too.
- **The lights** (api.md 9.1): `light(warm=40, cool=0)`, % of full scale; a channel left out keeps its level.
  - By default it returns at once, for a front end's main thread: a sender thread sends the latest asked for, merged
    channel by channel, and never queues (a slider dragged across sends a handful, not a hundred).
  - `lights` is what the unit has in force, mirrored from its `!light` (after the hello, and on every change from any
    client). A refusal lands in `set_errors["light"]`.
  - `light(..., wait=True)` sends it now and returns the levels set (`{"warm": 39.99, "cool": 0.0}`: the unit's nearest
    step), or raises `MmiError`.
- **The lid by hand** (api.md 9.2): `lid_duty(30)`, % of full drive, signed: + heats, - cools; 0 is off.
  - Through the same mailbox as the lights, so it returns at once. `lid` is the unit's latest `!lid` (`sw`, `mode`,
    `duty`, `cut`, the five temperatures each with its SD, `v33`), which comes once a second unasked; a refusal lands
    in `set_errors["lid"]` (its code: `range`, `absent`, `fault`, `hot`, `medium`).
  - `set_pending("lid")` is True until the reply to the latest duty is in. Then `lid` already says what it set (the
    reply is applied as it's read, and a `!lid` after it overrides it), so a front end can trust `lid` and
    `set_errors` from then on.
  - **A duty lost with its link isn't sent again,** unlike a light level: the unit drops a duty with the session that
    set it, so a new session starts from 0. `set_errors["lid"]` says so.
  - `lid_duty(..., wait=True)` sends it now and returns the duty set, or raises `MmiError`.
- **The limits and stalls, mirrored** (api.md 6.5). The unit sends the whole state after its `!hello`, then each
  change as it happens. The client keeps `limits` (`{"x": "0", "y": "+", "z": "?", "r": "-"}`) and `stalls` from those
  events alone: nothing is polled, and nothing rides on the jogs.
  - `blocks("x")` is a `Blocks`. `.why(v)` says why a velocity that way wouldn't run: `"limit"`, `"stall"` or None.
    `.unknown` is the axis's own switch open at an end the unit can't tell.
  - The unit does the mechanics: MIN, on the effector, comes as `limit x -` and `limit r -` together.

| | |
|---|---|
| `MmiClient(host, port, reconnect=True, watch_hz=0, keepalive=1.0, deadman=None)` | `connect()` now (raises if it can't), or `start()` to connect in the background |
| `jog(x, y, z, r)`, `halt(wait=False)` | motion |
| `get(*keys)`, `status()`, `ping()`, `watch(hz, topic)`, `help()`, `request(line)` | everything with a reply; a refusal raises `MmiError` (its `.code` is the unit's error code) |
| `hello`, `params`, `cfg`, `faults`, `state`, `records`, `motors`, `halted`, `connected` | what the unit has said, kept current from its events |
| `limits`, `stalls`, `blocks(axis)` | the limit and stall state, mirrored from the unit's events alone (below) |
| `light(warm, cool, wait=False)`, `lights`, `set_errors` | the lights: set (without waiting, by default), and as the unit has them |
| `lid_duty(duty, wait=False)`, `lid`, `set_pending(topic)` | the lid by hand: a duty, the unit's lid as it says it, and whether a set-point's reply is still out |
| `sent_vector`, `awaiting_release` | what the unit was last sent, and whether a vector is being held back |
| `on_event(fn)`, `on_connect(fn)`, `on_disconnect(fn)`, `get_event(timeout)` | events as they arrive (callbacks run on the client's reader thread) |
| `parse_addr("host:port")` | `host:port`, `host` or `port` to (host, port) |

`MmiTimeout` and `MmiDisconnected` are `MmiError`s too.

## Tests

`pytest`, from this folder, in the `mmi` env. No hardware needed.
- **The protocol:** the parsing and the line formats.
- **The limits, end to end:** each switch's state from the hello on; a fine limit, once each way, nothing in
  between; the effector's switch on fine x and the rail, the carriage's on the rail alone; a stall at a hard stop; the
  record agreeing; a new session's snapshot replacing the old.
- **The lights:** the line and the levels as written; dark, and said after the hello; set and heard, a change only once;
  every refusal changing nothing; the mailbox never waiting and sending the latest; a refusal in `set_errors`; sent
  once connected, and before closing; another session's change heard.
- **The lid:** the line and the steps as the firmware's; said after the hello, with its sensors and their SDs; a
  readout a second, unasked, and `watch lid` refused; set and heard, under 2 % none, a change only once; every refusal
  changing nothing; the unit's cap; a hot board, and a warm medium, cutting either way and refusing until cooler; a
  bad sensor cutting (a probe shorted or come loose too), an unfitted probe not; unplugged and back, nothing resuming; SW10 holding
  it; a duty ending with its session, and the next client told why; the mailbox and `set_pending`; the mirror up to
  date by the reply; a duty lost with its link not sent again.
- **The client against the test server:**
  - the handshake, the lease kept alive, the mailbox never queueing;
  - halt now, held until a let-go, with the releasing zero sent first even after a let-go quicker than the sender's
    poll;
  - a halt from the USB port;
  - a session dropped mid-move and a pulled cable: the unit halts; the client notices, reconnects and waits for a
    let-go;
  - a stalled client (the unit's lease halts it), the deadman (the client halts it), and `close()` after letting go
    (a ramped stop) or mid-move (a halt);
  - a replaced session staying off;
  - the records, a topic the unit lacks, `sent_vector` and `awaiting_release`.
- **The test server:**
  - its defaults against `../firmware/sd/parameters.yaml`;
  - every command accepted, or not (a command it has stays strict);
  - its console;
  - `python -m mmi_client.test_server` itself.
- **The Fusion mirror,** with Fusion faked:
  - the frames;
  - one update in flight with the newest pose;
  - the stops, and leaving one as soon as the motion reverses;
  - no link without Fusion;
  - the optional cap.

## Files

| | |
|---|---|
| `pyproject.toml` | the package (`mmi-client`; no dependencies) |
| `mmi_client/` | `client.py` (the link and its rules), `protocol.py` (parsing, no I/O), `test_server.py`, `sim_stage.py` (its stage: travel, switches, the firmware's rules for them), `sim_lid.py` (its lid: the firmware's rules, the thermal model), `fusion_mirror.py` (the test server's stage into Fusion) |
| `tests/` | above |

from mmi_client.protocol import (DriveState, Event, Hello, Info, Reply, format_jog, nest, parse_line, split_words,
                                 typed)

RECORD = ("t=81234 motors=on halt=0 acq=off x.mode=jog x.v=40.0 x.d=0.160 x.lim=0 x.blk=0 x.i=0.140 x.cal=0 "
          "y.mode=idle y.v=0.0 y.d=0.000 y.lim=+ y.blk=+ y.i=0.000 y.cal=0 z.mode=stop z.v=-2.5 z.d=0.000 z.lim=? "
          "z.blk=0 z.i=0.000 z.cal=1 r.mode=idle r.v=0 r.pos=37215 r.homed=0 r.lim=-+ r.blk=-+ r.drv=ok")


def test_words_and_values():
    assert split_words('unit="lab one" x=1  y=2') == ["unit=lab one", "x=1", "y=2"]
    assert split_words('a="" b') == ["a=", "b"]
    assert typed("12") == 12 and typed("-0.5") == -0.5 and typed(".5") == 0.5
    assert typed("0,70,260") == [0.0, 70.0, 260.0]
    assert typed("min") == "min" and typed("1e3") == "1e3" and typed("-+") == "-+"
    assert nest({"x.mode": "jog", "x.v": 1.0, "t": 5, "x.cal.fwd.duty": [1]}) == {
        "x": {"mode": "jog", "v": 1.0, "cal": {"fwd": {"duty": [1]}}}, "t": 5}


def test_lines():
    r = parse_line("@12 ok t=345\n")
    assert isinstance(r, Reply) and r.ok and r.tag == "12" and r.values == {"t": 345}
    r = parse_line("@ err unknown bogus")
    assert isinstance(r, Reply) and not r.ok and r.tag == "" and r.code == "unknown" and r.text == "bogus"
    i = parse_line("#7 x.vmax=250")
    assert isinstance(i, Info) and i.tag == "7" and i.text == "x.vmax=250"
    e = parse_line("!limit r - pos=12")
    assert isinstance(e, Event) and e.name == "limit" and e.args == ["r", "-"] and e.values == {"pos": 12}
    assert parse_line("") is None and parse_line("hello") is None and parse_line("@3 maybe") is None


def test_record():
    s = DriveState.from_values(parse_line("@1 ok " + RECORD).values)
    assert s.t == 81234 and s.motors == "on" and s.halt is False
    assert s.x.mode == "jog" and s.x.v == 40.0 and s.x.i == 0.14 and s.x.lim == "0"
    assert s.y.lim == "+" and s.z.lim == "?" and s.z.v == -2.5 and s.z.cal == 1
    assert s.r.pos == 37215.0 and s.r.lim == "-+" and s.r.drv == "ok" and s.r.homed == 0


def test_hello():
    h = Hello.from_event(parse_line("!hello api=0.5 fw=0.3.0 unit=unset reset=power cfg=card clock=unset motors=off"))
    assert h.api_version == (0, 5) and h.fw == "0.3.0" and h.motors == "off" and h.cfg == "card"
    h = Hello.from_event(parse_line('!hello api=0.10 fw=0.8.0 unit="bench 2" reset=wdog cfg=card halt=1'))
    assert h.api == "0.10" and h.api_version == (0, 10) and h.unit == "bench 2" and h.halt   # a version, not 0.1


def test_format_jog():
    assert format_jog() == "jog"
    assert format_jog(x=40, y=-12.5) == "jog x=40 y=-12.5"
    assert format_jog(z=0.04, r=-0.04) == "jog"            # below 0.1 um/s rounds to nothing
    assert format_jog(x=1.26, r=5000.0) == "jog x=1.3 r=5000"
    assert format_jog(x=-0.0) == "jog"

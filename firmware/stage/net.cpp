#include "net.h"

#include <Arduino.h>
#include <QNEthernet.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "drive.h"
#include "lid.h"
#include "light.h"
#include "motion.h"
#include "text.h"

using namespace qindesign::network;

namespace {
constexpr uint16_t PORT = 5000;
constexpr size_t LINE_CAP = 512;
constexpr size_t OUT_CAP = 8192;
const char* const FW = "0.9.1";
const char* const API = "0.11";
// The records: status <topic>, and watch <topic> <Hz> for the drive and the lights (the lid's comes by itself, with
// every readout).
enum Topic : uint8_t { T_DRIVE, T_LIGHT, T_LID, T_N };
const char* const TOPIC[T_N] = {"drive", "light", "lid"};

struct Session {
  bool open = false, usb = false, closing = false;
  EthernetClient client;
  char in[LINE_CAP];
  size_t inLen = 0;
  bool overflow = false;
  char out[OUT_CAP];
  size_t head = 0, tail = 0;   // ring: [tail, head)
  uint32_t stuckSince = 0;     // ms the output has been full (USB: cleared after 1 s)
  bool lost = false;           // a line didn't fit (TCP: the session ends, so its client can't miss an event)
  uint16_t watchHz[T_N] = {};      // per topic: Hz, 0 = off
  uint32_t nextWatch[T_N] = {};
  Jog refused = Jog::OK;       // the last refusal reported: !jog refused goes once per reason
  uint32_t lastJogError = 0;
};

Session tcp, usb;
EthernetServer server(PORT);
uint8_t idOf(const Session& s) { return &s == &tcp ? 1 : 2; }   // the drive's name for a client (driveJog, driveLost)
BootInfo boot;
uint32_t lastCommand = 0;
bool anyCommand = false;

size_t used(const Session& s) { return (s.head + OUT_CAP - s.tail) % OUT_CAP; }

// Appends one whole line, or drops it (and marks the session) if it doesn't fit: never waits. A TCP session that has
// lost a line is closed (pollTcp): its client mirrors state from the events (api.md 3, rule 6), so it either gets every
// one or reconnects and starts again from the snapshot.
void put(Session& s, const char* line, size_t n) {
  if (!s.open) return;
  if (used(s) + n + 1 >= OUT_CAP) {
    s.lost = true;
    if (!s.stuckSince) s.stuckSince = millis() | 1;
    return;
  }
  for (size_t i = 0; i < n; i++) { s.out[s.head] = line[i]; s.head = (s.head + 1) % OUT_CAP; }
  s.out[s.head] = '\n';
  s.head = (s.head + 1) % OUT_CAP;
}

void sendf(Session& s, const char* fmt, ...) {
  char line[LINE_CAP + 128];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(line, sizeof line, fmt, ap);
  va_end(ap);
  if (n > 0) put(s, line, (size_t)n < sizeof line ? (size_t)n : sizeof line - 1);
}

void flush(Session& s) {
  while (s.open && s.head != s.tail) {
    size_t chunk = (s.head > s.tail) ? s.head - s.tail : OUT_CAP - s.tail;
    size_t room = s.usb ? (size_t)Serial.availableForWrite() : (size_t)s.client.availableForWrite();
    if (room == 0) break;
    if (chunk > room) chunk = room;
    size_t w = s.usb ? Serial.write((const uint8_t*)s.out + s.tail, chunk)
                     : s.client.write((const uint8_t*)s.out + s.tail, chunk);
    if (w == 0) break;
    s.tail = (s.tail + w) % OUT_CAP;
  }
  if (!s.usb && s.open) s.client.flush();   // push it out now (lwIP would otherwise wait to coalesce)
  if (s.head == s.tail) s.stuckSince = 0;
  else if (used(s) < OUT_CAP / 2) s.stuckSince = 0;
}

void hello(Session& s) {
  Motors m = driveMotors();
  sendf(s, "!hello api=%s fw=%s unit=%s reset=%s cfg=%s clock=unset motors=%s halt=%d", API, FW, boot.params->unit,
        boot.reset, boot.cfg, m == Motors::ON ? "on" : m == Motors::WAIT ? "wait" : "off", driveHalted() ? 1 : 0);
  for (uint8_t i = 0; i < boot.report->n; i++) sendf(s, "!cfg %s", boot.report->line[i]);
  if (boot.report->lost) sendf(s, "!cfg %u more messages didn't fit", boot.report->lost);
  for (uint8_t i = 0; i < driveFaultCount(); i++) sendf(s, "!%s", driveFaultLine(i));
  // The state a client mirrors, as things stand; events keep it current from here on (api.md 6.5).
  char ev[64];
  for (uint8_t a = 0; a < AX_N; a++)
    if (driveLimitEvent(a, ev, sizeof ev)) sendf(s, "!%s", ev);
  for (uint8_t a = 0; a < AX_R; a++)
    if (driveStallEvent(a, ev, sizeof ev)) sendf(s, "!%s", ev);
  char rec[LINE_CAP];
  lightRecord(rec, sizeof rec);
  sendf(s, "!light %s", rec);
  lidRecord(rec, sizeof rec);
  sendf(s, "!lid %s", rec);
}

void opened(Session& s) {
  s.open = true;
  s.closing = false;
  s.inLen = 0;
  s.overflow = false;
  s.head = s.tail = 0;
  s.stuckSince = 0;
  s.lost = false;
  for (uint8_t t = 0; t < T_N; t++) s.watchHz[t] = 0;
  s.refused = Jog::OK;
  hello(s);
}

// The session has ended: its client closed it, was replaced, stopped reading or said bye, or the link went down. If
// its jog was in force, the operator is gone mid-move: the drive halts, exactly as for the command (api.md 2). A lid
// duty it set goes too (api.md 9.2).
void closed(Session& s, const char* cause) {
  if (!s.open) return;
  s.open = false;
  if (driveLost(idOf(s))) netEvent("halt on cause=%s", cause);
  if (lidLost(idOf(s), cause)) lidPublish();
}

bool digits(const char* t) {
  if (!*t) return false;
  for (; *t; t++)
    if (*t < '0' || *t > '9') return false;
  return true;
}

void reply(Session& s, const char* tag, const char* fmt, ...) {
  char body[LINE_CAP];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(body, sizeof body, fmt, ap);
  va_end(ap);
  sendf(s, "@%s %s", tag, body);
}

void cmdHelp(Session& s, const char* tag) {
  static const char* const lines[] = {
      "jog [x=v] [y=v] [z=v] [r=v]  target velocities in um/s; axes left out 0; no reply; renew within 250 ms or halt",
      "halt                         stop every axis now, no ramp; non-zero jogs ignored until an all-zero one",
      "get [key ...]                the parameters (parameters.md): the keys given, or all of them as # lines",
      "ping                         @ ok t=<ms since start>",
      "light [warm=%] [cool=%]      the LED strips' levels, % of full scale; a channel left out keeps its level",
      "lid duty=% | lid off         the lid's Peltiers by hand, % of full drive: + heats, - cools; at most",
      "                             lid.duty_max; cut at a 60 C board or a lid.medium_max medium; ends with this session",
      "status drive|light|lid       that record (the lid's: its last readout, sent as !lid once a second anyway)",
      "watch drive|light <Hz>       that record as !drive or !light at that rate (0 off, max 50)",
      "help                         this",
      "bye                          close the session",
  };
  for (const char* l : lines) sendf(s, "#%s %s", tag, l);
  reply(s, tag, "ok");
}

// A value as a reply carries it: double-quoted if it holds a space (a free-text field), as-is otherwise.
void quoteInto(char* out, size_t cap, const char* v) {
  if (*v && !strchr(v, ' ')) snprintf(out, cap, "%s", v);
  else snprintf(out, cap, "\"%s\"", v);
}

struct GetCtx {
  Session* s;
  const char* tag;
};

void cmdGet(Session& s, const char* tag, char* const* args, size_t na) {
  if (na == 0) {   // all of them, one per # line
    GetCtx c{&s, tag};
    paramsList(*boot.params, [](void* x, const char* k, const char* v) {
      GetCtx* c = (GetCtx*)x;
      char q[176];
      quoteInto(q, sizeof q, v);
      sendf(*c->s, "#%s %s=%s", c->tag, k, q);
    }, &c);
    reply(s, tag, "ok");
    return;
  }
  char body[LINE_CAP];
  size_t len = 0;
  appendf(body, sizeof body, &len, "ok");
  for (size_t k = 0; k < na; k++) {
    char v[160], q[176];
    if (!paramsGet(*boot.params, args[k], v, sizeof v)) { reply(s, tag, "err arg unknown key %s", args[k]); return; }
    quoteInto(q, sizeof q, v);
    if (!appendf(body, sizeof body, &len, " %s=%s", args[k], q)) {
      reply(s, tag, "err arg too long for one line: ask for fewer keys");
      return;
    }
  }
  sendf(s, "@%s %s", tag, body);
}

int topicOf(const char* name) {
  for (uint8_t t = 0; t < T_N; t++)
    if (!strcmp(name, TOPIC[t])) return t;
  return -1;
}

size_t record(int t, char* buf, size_t cap) {
  return t == T_LIGHT ? lightRecord(buf, cap) : t == T_LID ? lidRecord(buf, cap) : driveRecord(buf, cap);
}

// light [warm=<%>] [cool=<%>] (api.md 9.1): either or both; a channel left out keeps its level. The reply has both,
// as set; every session hears a change as !light. An error changes nothing.
void cmdLight(Session& s, const char* tag, char* const* args, size_t na) {
  float v[LIGHT_N];
  bool given[LIGHT_N];
  const char *err = "arg", *why = "";
  if (!parseLight(args, na, v, given, err, why)) { reply(s, tag, "err %s %s", err, why); return; }
  bool changed = false;
  for (uint8_t c = 0; c < LIGHT_N; c++)
    if (given[c]) changed |= lightSet(c, v[c]);
  char w[12], c[12];
  fmtLevel(lightNow(LIGHT_WARM), w, sizeof w);
  fmtLevel(lightNow(LIGHT_COOL), c, sizeof c);
  reply(s, tag, "ok warm=%s cool=%s", w, c);
  if (changed) {
    char rec[96];
    lightRecord(rec, sizeof rec);
    netEvent("light %s", rec);
  }
}

// lid duty=<%> | lid off (api.md 9.2): the duty, signed, up to lid.duty_max. Refused, changing nothing, while the
// lid is absent, a sensor reads wrong or the board or the medium is at its limit, unless it's 0. Every session hears a
// change as !lid; the duty is this session's until it ends.
void cmdLid(Session& s, const char* tag, char* const* args, size_t na) {
  float duty;
  const char *err = "arg", *why = "";
  float cap = boot.params->lid.duty_max;
  if (!parseLid(args, na, cap, duty, err, why)) {
    if (!strcmp(err, "range")) {
      char c[12];
      fmtLevel(cap, c, sizeof c);
      reply(s, tag, "err range duty -%s to %s (lid.duty_max)", c, c);
    } else {
      reply(s, tag, "err %s %s", err, why);
    }
    return;
  }
  char what[64] = "";
  switch (lidSet(duty, idOf(s), what, sizeof what)) {
    case LidSet::ABSENT: reply(s, tag, "err absent the lid isn't answering"); return;
    case LidSet::FAULT: reply(s, tag, "err fault %s", what); return;
    case LidSet::HOT: reply(s, tag, "err hot %s", what); return;
    case LidSet::MEDIUM: reply(s, tag, "err medium %s", what); return;
    case LidSet::OK: break;
  }
  char d[12];
  fmtLevel(lidDuty(), d, sizeof d);
  reply(s, tag, "ok duty=%s", d);
  lidPublish();
}

void dispatch(Session& s, char* line) {
  for (char* p = line; *p; p++)
    if (*p < 0x20 || *p > 0x7E) { sendf(s, "@ err syntax non-printable character"); return; }
  char* tok[24];
  size_t n = tokenize(line, tok, 24);
  if (n == 0) return;
  const char* tag = "";
  size_t i = 0;
  if (digits(tok[0])) {
    if (strlen(tok[0]) > 9) { sendf(s, "@ err syntax tag longer than 9 digits"); return; }
    tag = tok[0];
    i = 1;
  }
  if (i >= n) { reply(s, tag, "err syntax no command"); return; }
  const char* verb = tok[i++];
  lastCommand = millis();
  anyCommand = true;
  if (!strcmp(verb, "halt")) {   // first, before any check or reply: the stop doesn't wait for anything
    driveHalt();
    netEvent("halt on");
    if (i < n) reply(s, tag, "err arg halt takes no arguments (halted anyway)");
    else reply(s, tag, "ok");
    return;
  }
  size_t na = n - i;
  char* const* args = tok + i;
  if (!strcmp(verb, "jog")) {   // a stream of set-points: never answered (api.md 6.4)
    float t[AX_N];
    const char* why = "";
    if (!parseJog(args, na, t, why)) {
      driveZeroJog();
      if (millis() - s.lastJogError > 1000) {
        s.lastJogError = millis();
        sendf(s, "!error jog arg %s", why);
      }
      return;
    }
    Jog r = driveJog(t, idOf(s));
    if (r != Jog::OK && r != s.refused)
      sendf(s, "!jog refused %s", r == Jog::HALTED ? "halted" : r == Jog::MOTORS_WAIT ? "motors-wait" : "motors-off");
    s.refused = r;
    return;
  }
  if (!strcmp(verb, "ping")) {
    if (na) { reply(s, tag, "err arg ping takes no arguments"); return; }
    reply(s, tag, "ok t=%lu", (unsigned long)millis());
    return;
  }
  if (!strcmp(verb, "light")) { cmdLight(s, tag, args, na); return; }
  if (!strcmp(verb, "lid")) { cmdLid(s, tag, args, na); return; }
  if (!strcmp(verb, "status")) {
    int t = na == 1 ? topicOf(args[0]) : -1;
    if (t < 0) { reply(s, tag, "err arg status drive|light|lid"); return; }
    char rec[LINE_CAP];
    record(t, rec, sizeof rec);
    reply(s, tag, "ok %s", rec);
    return;
  }
  if (!strcmp(verb, "watch")) {
    float hz;
    int t = na == 2 ? topicOf(args[0]) : -1;
    if (t == T_LID) {
      reply(s, tag, "err arg the lid isn't watched: !lid comes with every readout, once a second");
      return;
    }
    if (t < 0 || !parseNumber(args[1], hz) || hz < 0 || hz > 50 || hz != (int)hz) {
      reply(s, tag, "err arg watch drive|light <0-50 Hz>");
      return;
    }
    s.watchHz[t] = (uint16_t)hz;
    s.nextWatch[t] = millis();
    reply(s, tag, "ok");
    return;
  }
  if (!strcmp(verb, "get")) { cmdGet(s, tag, args, na); return; }
  if (!strcmp(verb, "help")) { cmdHelp(s, tag); return; }
  if (!strcmp(verb, "bye")) { reply(s, tag, "ok"); s.closing = true; return; }
  reply(s, tag, "err unknown %s", verb);
}

void readLines(Session& s, int c) {   // one byte at a time from either port
  if (c == '\n') {
    if (s.overflow) {
      sendf(s, "@ err syntax line longer than %u characters", (unsigned)(LINE_CAP - 1));
    } else {
      if (s.inLen && s.in[s.inLen - 1] == '\r') s.inLen--;
      s.in[s.inLen] = '\0';
      dispatch(s, s.in);
    }
    s.inLen = 0;
    s.overflow = false;
  } else if (!s.overflow) {
    if (s.inLen < LINE_CAP - 1) s.in[s.inLen++] = (char)c;
    else s.overflow = true;
  }
}

void watchPoll(Session& s) {
  if (!s.open) return;
  uint32_t now = millis();
  for (uint8_t t = 0; t < T_N; t++) {
    if (!s.watchHz[t] || (int32_t)(now - s.nextWatch[t]) < 0) continue;
    s.nextWatch[t] = now + 1000 / s.watchHz[t];
    if (used(s) > OUT_CAP / 2) continue;   // a slow reader: skip this one (the next replaces it), keep room for the rest
    char rec[LINE_CAP];
    record(t, rec, sizeof rec);
    sendf(s, "!%s %s", TOPIC[t], rec);
  }
}

void pollTcp() {
  EthernetClient c = server.accept();
  if (c) {
    if (tcp.open) {   // the newest connection wins; the old one is told and closed (a halt if its jog was in force)
      sendf(tcp, "!bye replaced");
      flush(tcp);
      tcp.client.close();
      closed(tcp, "replaced");
    }
    tcp.client = c;
    tcp.client.setNoDelay(true);
    tcp.usb = false;
    opened(tcp);
  }
  if (!tcp.open) return;
  if (!Ethernet.linkState()) {   // the cable or the far end went: no FIN will come, so end it now (QNEthernet reads
    tcp.client.abort();          // the PHY's link bit every 125 ms)
    closed(tcp, "link");
    return;
  }
  if (!tcp.client.connected()) { tcp.client.close(); closed(tcp, "closed"); return; }
  int avail = tcp.client.available();
  while (avail-- > 0) {
    int ch = tcp.client.read();
    if (ch < 0) break;
    readLines(tcp, ch);
    if (tcp.closing) break;
  }
  watchPoll(tcp);
  flush(tcp);
  if (tcp.closing || tcp.lost) {   // said bye, or stopped reading (a line didn't fit)
    tcp.client.close();
    closed(tcp, tcp.closing ? "bye" : "stuck");
  }
}

void pollUsb() {
  bool dtr = Serial.dtr();
  if (dtr && !usb.open) { usb.usb = true; opened(usb); }
  if (!dtr && usb.open) closed(usb, "closed");
  if (!usb.open) { while (Serial.available()) Serial.read(); return; }
  int avail = Serial.available();
  while (avail-- > 0) {
    int ch = Serial.read();
    if (ch < 0) break;
    readLines(usb, ch);
  }
  if (usb.closing) { closed(usb, "bye"); return; }
  watchPoll(usb);
  flush(usb);
  if (usb.stuckSince && millis() - usb.stuckSince > 1000) usb.head = usb.tail;   // nobody reading: drop it
}
}  // namespace

void netBegin(const BootInfo& b) {
  boot = b;
  Serial.begin(115200);
  Ethernet.begin(IPAddress(192, 168, 50, 2), IPAddress(255, 255, 255, 0), IPAddress(192, 168, 50, 1));
  server.begin();
}

void netPoll() {
  pollTcp();
  pollUsb();
}

void netEvent(const char* fmt, ...) {
  char line[LINE_CAP];
  line[0] = '!';
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(line + 1, sizeof line - 1, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  size_t len = 1 + ((size_t)n < sizeof line - 1 ? (size_t)n : sizeof line - 2);
  put(tcp, line, len);
  put(usb, line, len);
}

bool netConnected() { return tcp.open; }
uint32_t netSinceCommand() { return anyCommand ? millis() - lastCommand : 0xFFFFFFFFu; }

// Sessions (TCP 192.168.50.2:5000, and USB serial for the bench), the line protocol, and the commands that are
// online: jog, halt, light, lid, get, ping, help, status drive|light|lid, watch drive|light, bye; and !lid with every
// readout of the lid's sensors. api.md sections 2-4. A session lost while its jog is in force (closed, replaced,
// stuck, or the link down) halts the drive; a lid duty it set is dropped.
#pragma once
#include "params.h"

struct BootInfo {
  const char* reset;    // power, wdog, soft, other
  const char* cfg;      // card, default, nocard
  const Params* params;
  const Report* report; // replayed as !cfg lines after every !hello
};

void netBegin(const BootInfo& b);
void netPoll();
void netEvent(const char* fmt, ...);   // "!<text>" to every open session (main-loop context only)
bool netConnected();
uint32_t netSinceCommand();            // ms since any session last sent a command (the status LED's blip)

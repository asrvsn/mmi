#pragma once
#include <stdint.h>

bool simTimerBegin(void (*fn)(), float us);

class IntervalTimer {
 public:
  template <typename T>
  bool begin(void (*fn)(), T us) { return simTimerBegin(fn, (float)us); }
  void priority(uint8_t) {}
};

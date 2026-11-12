// A mocked Wire for host tests: just what lid.cpp uses of Teensy's TwoWire. The devices on the bus, the lid's two
// ADS1115, are teensy_sim.cpp's.
#pragma once
#include <stddef.h>
#include <stdint.h>

class TwoWire {
 public:
  void begin() {}
  void setClock(uint32_t) {}
  void beginTransmission(uint8_t addr);
  size_t write(uint8_t b);
  uint8_t endTransmission(bool stop = true);   // 0: sent; 2: no device answered its address
  uint8_t requestFrom(uint8_t addr, uint8_t n);
  int available();
  int read();
};
extern TwoWire Wire1;

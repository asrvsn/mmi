// A mocked Teensy 4.1 for host tests: just what drive.cpp and rail.cpp use. The world behind it is teensy_sim.cpp.
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define FALLING 2
#define RISING 3
#define CHANGE 4

void pinMode(uint8_t pin, uint8_t mode);
void digitalWriteFast(uint8_t pin, uint8_t val);
uint8_t digitalReadFast(uint8_t pin);
void analogWrite(uint8_t pin, int val);
int analogRead(uint8_t pin);
void analogWriteFrequency(uint8_t pin, float hz);
void analogWriteResolution(int bits);
void analogReadResolution(int bits);
void analogReadAveraging(int n);
uint32_t millis();
uint32_t micros();
void delayNanoseconds(uint32_t ns);
void noInterrupts();
void interrupts();
int digitalPinToInterrupt(uint8_t pin);
void attachInterrupt(int irq, void (*fn)(), int mode);
void attachInterruptVector(int irq, void (*fn)());

enum { IRQ_GPT2 = 101, IRQ_GPIO6789 = 157 };
#define NVIC_SET_PRIORITY(irq, prio) ((void)(irq), (void)(prio))
#define NVIC_ENABLE_IRQ(irq) ((void)0)

extern uint32_t sim_GPT2_CR, sim_GPT2_PR, sim_GPT2_SR, sim_GPT2_IR, sim_GPT2_OCR1, sim_CCM_CCGR0;
uint32_t sim_gpt_cnt();
#define GPT2_CR sim_GPT2_CR
#define GPT2_PR sim_GPT2_PR
#define GPT2_SR sim_GPT2_SR
#define GPT2_IR sim_GPT2_IR
#define GPT2_OCR1 sim_GPT2_OCR1
#define GPT2_CNT (sim_gpt_cnt())
#define GPT_CR_EN (1u << 0)
#define GPT_CR_ENMOD (1u << 1)
#define GPT_CR_CLKSRC(n) (((n) & 7u) << 6)
#define GPT_CR_FRR (1u << 9)
#define GPT_SR_OF1 (1u << 0)
#define GPT_IR_OF1IE (1u << 0)
#define CCM_CCGR0 sim_CCM_CCGR0
#define CCM_CCGR_ON 3
#define CCM_CCGR0_GPT2_BUS(n) (((n) & 3u) << 24)
#define CCM_CCGR0_GPT2_SERIAL(n) (((n) & 3u) << 26)

struct SimSerial {
  void begin(uint32_t baud);
  int available();
  int read();
  size_t write(const uint8_t* b, size_t n);
};
extern SimSerial Serial1;

// Small text helpers shared by the protocol and the parameter file. No Arduino dependencies (host-testable).
#pragma once
#include <stddef.h>
#include <stdint.h>

// A plain decimal: optional sign, digits, optional fraction ("250", "-12.5", "+0.8", ".5"). No exponent.
bool parseNumber(const char* s, float& out);
// A list of plain decimals, either "[a, b, c]" (YAML flow) or "a,b,c" (protocol). Returns false on any bad item.
bool parseList(const char* s, float* out, uint8_t maxn, uint8_t& n);
// Splits a line in place on runs of spaces; returns the token count (at most maxtok).
size_t tokenize(char* line, char** tok, size_t maxtok);
// Appends printf-style text to buf (of size cap) at *len; never overflows; returns false if truncated.
bool appendf(char* buf, size_t cap, size_t* len, const char* fmt, ...);

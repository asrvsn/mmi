#include "text.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

bool parseNumber(const char* s, float& out) {
  const char* p = s;
  if (*p == '+' || *p == '-') p++;
  int digits = 0;
  while (*p >= '0' && *p <= '9') { p++; digits++; }
  if (*p == '.') {
    p++;
    while (*p >= '0' && *p <= '9') { p++; digits++; }
  }
  if (digits == 0 || *p != '\0' || (p - s) > 16) return false;
  out = strtof(s, nullptr);
  return true;
}

bool parseList(const char* s, float* out, uint8_t maxn, uint8_t& n) {
  char buf[160];
  size_t len = strlen(s);
  if (len >= sizeof(buf)) return false;
  memcpy(buf, s, len + 1);
  char* p = buf;
  if (*p == '[') {
    if (len < 2 || buf[len - 1] != ']') return false;
    buf[len - 1] = '\0';
    p++;
  }
  n = 0;
  while (true) {
    while (*p == ' ') p++;
    char* item = p;
    while (*p && *p != ',') p++;
    char* end = p;
    while (end > item && end[-1] == ' ') end--;
    bool last = (*p == '\0');
    *end = '\0';
    if (*item == '\0') return false;   // an empty item: "[1,,2]" or "[]"
    if (n >= maxn || !parseNumber(item, out[n])) return false;
    n++;
    if (last) return true;
    p++;
  }
}

size_t tokenize(char* line, char** tok, size_t maxtok) {
  size_t n = 0;
  char* p = line;
  while (*p && n < maxtok) {
    while (*p == ' ') *p++ = '\0';
    if (!*p) break;
    tok[n++] = p;
    while (*p && *p != ' ') p++;
  }
  return n;
}

bool appendf(char* buf, size_t cap, size_t* len, const char* fmt, ...) {
  if (*len >= cap) return false;
  va_list ap;
  va_start(ap, fmt);
  int w = vsnprintf(buf + *len, cap - *len, fmt, ap);
  va_end(ap);
  if (w < 0) return false;
  if ((size_t)w >= cap - *len) { *len = cap - 1; return false; }
  *len += (size_t)w;
  return true;
}

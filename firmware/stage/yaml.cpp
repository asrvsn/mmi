#include "yaml.h"

#include <stdio.h>
#include <string.h>

namespace {
constexpr int MAX_DEPTH = 6;
constexpr size_t MAX_KEY = 24;

struct Level {
  int indent;   // the key's own indent (-1 for the root)
  int child;    // its children's indent, -1 until the first child
  char key[MAX_KEY];
};

bool fail(char* err, size_t cap, int line, const char* what) {
  snprintf(err, cap, "line %d: %s", line, what);
  return false;
}

bool keyChars(const char* k) {
  if (!*k) return false;
  for (; *k; k++) {
    char c = *k;
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return false;
  }
  return true;
}
}  // namespace

bool yamlFlatten(char* text, YamlSink sink, void* ctx, char* err, size_t errcap) {
  Level st[MAX_DEPTH + 1];
  int top = 0;
  st[0] = {-1, -1, ""};
  int lineno = 0;
  char* p = text;
  while (*p) {
    char* line = p;
    char* nl = strchr(p, '\n');
    if (nl) { *nl = '\0'; p = nl + 1; } else { p += strlen(p); }
    lineno++;
    if (strchr(line, '\t')) return fail(err, errcap, lineno, "tab character (indent with spaces)");
    bool quoted = false;   // strip a comment: '#' at the start or after a space, outside quotes
    for (char* q = line; *q; q++) {
      if (*q == '"') quoted = !quoted;
      else if (*q == '#' && !quoted && (q == line || q[-1] == ' ')) { *q = '\0'; break; }
    }
    size_t len = strlen(line);
    while (len && (line[len - 1] == ' ' || line[len - 1] == '\r')) line[--len] = '\0';
    int ind = 0;
    while (line[ind] == ' ') ind++;
    if (!line[ind]) continue;
    char* k = line + ind;
    if (*k == '-') return fail(err, errcap, lineno, "'- item' lists aren't supported (use [a, b])");
    char* colon = k;   // the first ':' that ends the line or is followed by a space
    while (*colon && !(*colon == ':' && (colon[1] == '\0' || colon[1] == ' '))) colon++;
    if (!*colon) return fail(err, errcap, lineno, "expected 'key: value' or 'key:'");
    *colon = '\0';
    char* v = colon + 1;
    while (*v == ' ') v++;
    if (!keyChars(k) || strlen(k) >= MAX_KEY) return fail(err, errcap, lineno, "bad key");
    while (top > 0 && st[top].indent >= ind) {   // close the mappings this line isn't inside
      if (st[top].child < 0) return fail(err, errcap, lineno, "a 'key:' with nothing under it");
      top--;
    }
    if (st[top].child < 0) {
      if (top == 0 && ind != 0) return fail(err, errcap, lineno, "the top level must start in column 1");
      st[top].child = ind;
    } else if (ind != st[top].child) {
      return fail(err, errcap, lineno, "inconsistent indent");
    }
    char path[MAX_DEPTH * MAX_KEY + 8];
    path[0] = '\0';
    for (int i = 1; i <= top; i++) { strcat(path, st[i].key); strcat(path, "."); }
    strcat(path, k);
    if (!*v) {
      if (top >= MAX_DEPTH) return fail(err, errcap, lineno, "nested too deep");
      top++;
      st[top].indent = ind;
      st[top].child = -1;
      strcpy(st[top].key, k);
      continue;
    }
    size_t vl = strlen(v);
    if (v[0] == '"') {
      if (vl < 2 || v[vl - 1] != '"') return fail(err, errcap, lineno, "unterminated quote");
      v[vl - 1] = '\0';
      v++;
    }
    sink(ctx, path, v, lineno);
  }
  while (top > 0) {
    if (st[top].child < 0) return fail(err, errcap, lineno, "a 'key:' with nothing under it");
    top--;
  }
  return true;
}

// A YAML subset, flattened to dotted keys ("x.cal.fwd.duty"), which are also the API's parameter names.
// Supported: block mappings by indentation (spaces), "key: value", "key:" opening a nested mapping, '#' comments,
// double-quoted scalars, and flow lists ("[0.038, 0.92]") passed through as the value text. Anything else (tabs,
// "- item" lists, anchors, multi-line values) is an error with its line number. No Arduino dependencies.
#pragma once
#include <stddef.h>

typedef void (*YamlSink)(void* ctx, const char* key, const char* value, int line);

// Parses text in place (it is modified). Calls sink for every scalar. On error returns false with a message.
bool yamlFlatten(char* text, YamlSink sink, void* ctx, char* err, size_t errcap);

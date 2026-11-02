#pragma once
#include <stdint.h>
#define DECL_CONSTANT(n,v)
#define DECL_CONSTANT_STR(n,v)
#define DECL_ENUMERATION(t,n,v)
#define DECL_COMMAND(f,s)
#define DECL_COMMAND_FLAGS(f,flags,s)
#define HF_IN_SHUTDOWN 1
void mock_shutdown(const char*) __attribute__((noreturn));
#define shutdown(s) mock_shutdown(s)
void sendf(const char*,...);
void* command_decode_ptr(uint32_t);

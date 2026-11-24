#pragma once
#include <stdint.h>
#define DECL_CONSTANT(n,v)
#define DECL_CONSTANT_STR(n,v)
#define DECL_ENUMERATION(t,n,v)
#define DECL_COMMAND(f,s)
#define DECL_COMMAND_FLAGS(f,flags,s)
#define HF_IN_SHUTDOWN 1
/* Stable IDs for the native shutdown classifier in register-only tests. */
#include <string.h>
#define _DECL_STATIC_STR(s) (!strcmp(s,"Timer too close")?1u: \
    !strcmp(s,"Stepper too far in past")?2u:!strcmp(s,"ADC out of range")?3u: \
    !strcmp(s,"Command request")?4u:99u)
void mock_shutdown(const char*) __attribute__((noreturn));
#define shutdown(s) mock_shutdown(s)
void sendf(const char*,...);
void* command_decode_ptr(uint32_t);

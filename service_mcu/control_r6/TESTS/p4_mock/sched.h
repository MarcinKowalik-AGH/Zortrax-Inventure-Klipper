#pragma once
#include <stdint.h>
struct timer {struct timer*next;uint_fast8_t(*func)(struct timer*);uint32_t waketime;};
struct task_wake {uint8_t wake;};
enum {SF_DONE=0,SF_RESCHEDULE=1};
void sched_add_timer(struct timer*);void sched_del_timer(struct timer*);
void sched_wake_task(struct task_wake*);uint8_t sched_check_wake(struct task_wake*);
#define DECL_INIT(f) static void(* const ref_init_##f)(void) __attribute__((used))=f
#define DECL_TASK(f) static void(* const ref_task_##f)(void) __attribute__((used))=f
#define DECL_SHUTDOWN(f) static void(* const ref_stop_##f)(void) __attribute__((used))=f

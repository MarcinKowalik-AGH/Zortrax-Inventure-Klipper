// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef INVENTURE_TRANSPORT_H
#define INVENTURE_TRANSPORT_H
#include <stdint.h>
#include <limits.h>
enum {IVT_STOP, IVT_AUTO, IVT_FORWARD, IVT_REVERSE, IVT_GATED, IVT_ASSIST};
struct ivt_state {
    uint8_t configured, cartridge, mode, monitor, running, direction, retry, fault;
    uint32_t starts, deadline, duration, assigned_budget, forward, reverse;
    int32_t budget;
};
static inline uint8_t ivt_due(uint32_t now, uint32_t end) {return (int32_t)(now-end)>=0;}
static inline void ivt_stop(struct ivt_state *s) {s->running=0;s->direction=1;}
static inline void ivt_mode(struct ivt_state *s, uint8_t mode) {ivt_stop(s);s->mode=mode;}
static inline void ivt_start(struct ivt_state *s, uint32_t now, uint8_t direction, uint8_t counted) {
    s->direction=direction;s->running=1;
    if(counted) {s->starts++;s->budget=s->assigned_budget;}
    s->deadline=now+s->duration;
}
static inline void ivt_tick(struct ivt_state *s, uint32_t now, uint8_t a, uint8_t b) {
    if(!s->configured || s->fault || s->mode==IVT_STOP) {ivt_stop(s);return;}
    if(s->mode==IVT_AUTO) {
        if(!s->cartridge) {ivt_stop(s);return;}
        if(s->running) {
            if(!ivt_due(now,s->deadline))return;
            ivt_stop(s);
            if(s->monitor) {if(!b)s->retry=0;else if(++s->retry>3)s->fault=3;}
            return;
        }
        if(a && b)ivt_start(s,now,1,1);
        return;
    }
    s->direction=s->mode==IVT_FORWARD;
    s->running=s->mode==IVT_FORWARD || s->mode==IVT_REVERSE || (a && !b);
}
static inline void ivt_e_step(struct ivt_state *s, uint8_t forward) {
    if(forward) {s->forward++;if(s->budget>INT32_MIN)s->budget--;}
    else {s->reverse++;if(s->budget<INT32_MAX)s->budget++;}
}
#endif

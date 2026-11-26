/* Inventure r0323: read-only OTP identity and loss-recoverable ID0 update.
 * Only sectors 2/3 (16 KiB each) are writable. Unknown settings survive.
 * SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef INVENTURE_FACTORY_INFO_H
#define INVENTURE_FACTORY_INFO_H
#include <stdint.h>
#include <string.h>
#define INFO_BANK_SIZE 16384u
#define INFO_SLOTS 53u
static const uint8_t info_lengths[INFO_SLOTS]={
 4,2,2,2,2,2,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,
 2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,4};
struct info_bank {uint16_t state;uint8_t valid,has[INFO_SLOTS];uint8_t value[INFO_SLOTS][4];};
static uint16_t info_u16(const uint8_t *p){return p[0]|(uint16_t)p[1]<<8;}
static uint32_t info_u32(const uint8_t *p){return info_u16(p)|(uint32_t)info_u16(p+2)<<16;}
static void info_put32(uint8_t *p,uint32_t x){for(unsigned i=0;i<4;i++)p[i]=x>>(8*i);}
static void info_decode(const uint8_t *raw,struct info_bank *out){
 memset(out,0,sizeof(*out));out->state=info_u16(raw);
 if(info_u16(raw+2)!=0xffff)return;
 if(out->state==0xffff){
  for(unsigned p=0;p<INFO_BANK_SIZE;p++)if(raw[p]!=0xff)return;
  out->valid=1;return;
 }
 if(out->state!=0x8888&&out->state!=0xcccc)return;
 unsigned p=4;
 while(p+4<=INFO_BANK_SIZE){
  unsigned id=info_u16(raw+p),len=info_u16(raw+p+2);
  if(id==0xffff){for(;p<INFO_BANK_SIZE;p++)if(raw[p]!=0xff)return;out->valid=1;return;}
  if(id>=INFO_SLOTS||len!=info_lengths[id]||p+4+len>INFO_BANK_SIZE)return;
  memcpy(out->value[id],raw+p+4,len);out->has[id]=1;p+=4+len;
 }
 for(;p<INFO_BANK_SIZE;p++)if(raw[p]!=0xff)return;
 out->valid=1;
}
static uint32_t info_crc_byte(uint32_t crc,uint8_t value){
 crc^=value;for(unsigned j=0;j<8;j++)crc=(crc>>1)^(0xedb88320u&-(crc&1u));return crc;
}
static uint32_t info_settings_crc(const struct info_bank *b){
 uint32_t crc=0xffffffffu;
 for(unsigned i=1;i<INFO_SLOTS;i++)if(b->has[i]){
  crc=info_crc_byte(crc,i);crc=info_crc_byte(crc,0);
  crc=info_crc_byte(crc,info_lengths[i]);crc=info_crc_byte(crc,0);
  for(unsigned j=0;j<info_lengths[i];j++)crc=info_crc_byte(crc,b->value[i][j]);
 }
 return crc^0xffffffffu;
}
/* Exact original c928/c944 boundary scan, then four serial bytes + HW byte.
 * Neither STM UID nor the host machine-id is substituted for the OEM serial. */
static int info_identity(const uint8_t *otp,const uint8_t *locks,uint32_t *serial,int *hardware){
 int block=14;while(block>=0&&!locks[block])block--;
 if(block<0)return 0;
 block++;
 if(block>14)return 0;
 const uint8_t *p=otp+32*block;
 *serial=(uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3];
 *hardware=(p[4]>=0xe0&&p[4]<=0xef)?p[4]&15:-1;return 1;
}
static int info_normalized(const struct info_bank b[2]){
 for(int i=0;i<2;i++)if(b[i].valid&&b[i].state==0x8888&&b[i].has[0]
   &&info_u32(b[i].value[0])!=0xffffffffu&&b[1-i].valid&&b[1-i].state==0xffff)return i;
 return -1;
}
/* Backend provides geometry/idle checks and sector-local erase/program.
 * Return 0=written, 1=already committed, 2=defer, 3=conflict, 4=I/O, 7=target. */
struct info_io {
 const uint8_t *bank[2];
 int (*begin)(void *);void (*end)(void *);
 int (*erase)(void *,unsigned);int (*program)(void *,unsigned,unsigned,uint16_t);
 void *context;
};
static int info_matches(const struct info_bank *b,uint32_t minutes,uint32_t crc){
 return b->valid&&b->state==0x8888&&b->has[0]&&info_u32(b->value[0])==minutes&&info_settings_crc(b)==crc;
}
static int info_commit(struct info_io *io,unsigned source,uint32_t expected,uint32_t target,uint32_t crc){
 if(source>1||expected>=target||target==0xffffffffu)return 3;
 struct info_bank b[2];for(unsigned i=0;i<2;i++)info_decode(io->bank[i],b+i);
 unsigned dest=1-source;
 if(info_matches(b+dest,target,crc)){
  if(b[source].valid&&b[source].state==0xffff)return 1;
  /* The last source erase can be torn. A complete, authenticated target
   * copy is sufficient to finish that erase; a conflicting valid copy is
   * refused instead of discarding a second settings history. */
  if(b[source].valid&&b[source].state!=0xffff
      &&(!b[source].has[0]||info_u32(b[source].value[0])!=expected
         ||info_settings_crc(b+source)!=crc))return 3;
  if(!io->begin(io->context))return 2;
  int ok=io->erase(io->context,source);io->end(io->context);return ok?0:4;
 }
 if(!info_matches(b+source,expected,crc)||b[dest].state==0x8888)return 3;
 /* Snapshot before acquiring the bounded flash window; no reads from source
  * are needed while an erase/program instruction stalls the flash bus. */
 info_put32(b[source].value[0],target);
 if(!io->begin(io->context))return 2;
 int ok=io->erase(io->context,dest);
 if(ok)ok=io->program(io->context,dest,0,0xcccc);
 unsigned p=4;
 for(unsigned i=0;ok&&i<INFO_SLOTS;i++)if(b[source].has[i]){
  ok=io->program(io->context,dest,p,i);p+=2;
  if(ok)ok=io->program(io->context,dest,p,info_lengths[i]);
  p+=2;
  for(unsigned j=0;ok&&j<info_lengths[i];j+=2){ok=io->program(io->context,dest,p,info_u16(b[source].value[i]+j));p+=2;}
 }
 struct info_bank verify;
 if(ok){info_decode(io->bank[dest],&verify);ok=verify.valid&&verify.state==0xcccc&&verify.has[0]
   &&info_u32(verify.value[0])==target&&info_settings_crc(&verify)==crc;}
 if(ok)ok=io->program(io->context,dest,0,0x8888);
 if(ok)ok=io->erase(io->context,source);
 io->end(io->context);
 if(!ok)return 4;
 for(unsigned i=0;i<2;i++)info_decode(io->bank[i],b+i);
 return info_normalized(b)==(int)dest&&info_matches(b+dest,target,crc)?0:4;
}
#endif

/* R25 Info sidecar. No Home, heater, fan or ownership transition is changed. */
#include "inventure_factory_info.h"
#line 28000 "inventure_information.inc"
DECL_CONSTANT("INVENTURE_FACTORY_INFO_ABI",1);
DECL_CONSTANT("INVENTURE_FACTORY_COUNTER_ABI",1);
extern uint8_t inventure_native_stepper_idle(void);
extern uint8_t sched_inventure_flash_ready(uint32_t now);
extern void sched_inventure_flash_rebase(uint32_t started);
static uint8_t info_idle(void){
 if(fault||sched_is_shutdown()||inventure_is_armed()||control_latched||control_mode!=CT_OFF
  ||(GPIOE->ODR&BIT(10))||!inventure_native_stepper_idle()
  ||held_service_pending||held_service_live||native_z_guard_active||native_z_refine_held
  ||native_z_refine_conflict||zh_active()||cm.active||fs.active||sl.active||motor_test.running
  ||xy_seek_busy()||t0_heat_busy()||fan_manual_active()||transport_live
  ||feeder_running[0]||feeder_running[1]||locks_on[0]||locks_on[1]
  ||locks_pulling[0]||locks_pulling[1]||assist_pending[0]||assist_pending[1])return 0;
 for(unsigned i=0;i<4;i++)if(control_heater_duty[i])return 0;
 for(unsigned i=0;i<10;i++)if(co[i].duty)return 0;
 return 1;
}
static int info_target_supported(void){
 return ((*(volatile uint32_t*)0xe0042000u&0xfffu)==0x411u
         &&*(volatile uint16_t*)0x1fff7a22u==1024u);
}
static int info_read_identity(uint32_t *serial,int *hardware){
 return info_identity((const uint8_t*)0x1fff7800u,(const uint8_t*)0x1fff7a00u,serial,hardware);
}
/* Executable SRAM, copied by the pinned linker/startup's .data.* handling.
 * A stuck Flash controller resets from SRAM with every actuator already OFF;
 * the host's durable pending transaction is retained for read-back recovery. */
__attribute__((section(".data.inventure_flash")))
static int noinline info_flash_hw(unsigned sector,uint32_t address,uint16_t value,int erase){
 volatile uint32_t *key=(void*)0x40023c04u,*sr=(void*)0x40023c0cu,*cr=(void*)0x40023c10u;
 volatile uint32_t *clock=(void*)0x40000024u;
 if(*cr&(1u<<31)){*key=0x45670123u;*key=0xcdef89abu;}
 *sr=0xf3u;
 *cr=(1u<<8)|(erase?((sector<<3)|(1u<<1)):1u);
 uint32_t started=*clock;
 if(erase)*cr|=1u<<16;else *(volatile uint16_t*)address=value;
 while(*sr&(1u<<16)){
  *(volatile uint32_t*)0x40003000u=0xaaaau;
  if((uint32_t)(*clock-started)>2u*CONFIG_CLOCK_FREQ){
   *(volatile uint32_t*)0xe000ed0cu=0x05fa0004u;
   for(;;)__asm__ __volatile__("nop");
  }
 }
 int ok=!(*sr&0xf2u);*cr=1u<<31;
 volatile uint32_t *acr=(void*)0x40023c00u;uint32_t saved=*acr;
 *acr=saved&~(3u<<9);*acr=(saved&~(3u<<9))|(3u<<11);*acr=saved;
 __asm__ __volatile__("dsb\n\tisb" ::: "memory");return ok;
}
struct info_flash_context {irqstatus_t flags;uint32_t started;};
static int info_begin(void *pointer){
 struct info_flash_context *ctx=pointer;ctx->flags=irq_save();ctx->started=timer_read_time();
 if(!info_idle()||!sched_inventure_flash_ready(ctx->started)){irq_restore(ctx->flags);return 0;}
 return 1;
}
static void info_end(void *pointer){
 struct info_flash_context *ctx=pointer;
 sched_inventure_flash_rebase(ctx->started);irq_restore(ctx->flags);
}
static int info_erase(void *unused,unsigned bank){(void)unused;return info_flash_hw(2+bank,0,0,1);}
static int info_program(void *unused,unsigned bank,unsigned offset,uint16_t value){
 (void)unused;if(bank>1||offset>INFO_BANK_SIZE-2||(offset&1))return 0;
 return info_flash_hw(0,0x08008000u+bank*INFO_BANK_SIZE+offset,value,0);
}
static void info_state(uint32_t token){
 uint32_t serial=0,minutes=0,crc=0;int hardware;unsigned source=255,state=0;
 if(info_read_identity(&serial,&hardware)&&serial&&serial!=0xffffffffu){
  struct info_bank banks[2];for(unsigned i=0;i<2;i++)info_decode((void*)(0x08008000u+i*INFO_BANK_SIZE),banks+i);
  int active=info_normalized(banks);state=active<0?2:1;
  if(active>=0){source=active;minutes=info_u32(banks[active].value[0]);crc=info_settings_crc(banks+active);}
 }else serial=0;
 sendf("inventure_factory_counter_runtime_state token=%u serial=%u state=%c source=%c print_minutes=%u settings_crc=%u",
       token,serial,state,source,minutes,crc);
}
void command_inventure_factory_counter_query(uint32_t *args){info_state(args[0]);}
DECL_COMMAND_FLAGS(command_inventure_factory_counter_query,HF_IN_SHUTDOWN,
 "inventure_factory_counter_query_runtime token=%u");
void command_inventure_factory_info_query(uint32_t *args){
 uint32_t serial=0;int hardware=-1;int valid=info_read_identity(&serial,&hardware);
 const volatile uint32_t *uid=(void*)0x1fff7a10u;
 sendf("inventure_factory_info token=%u valid=%c serial=%u hardware=%hi uid0=%u uid1=%u uid2=%u",
       args[0],valid,serial,hardware,uid[0],uid[1],uid[2]);
}
DECL_COMMAND_FLAGS(command_inventure_factory_info_query,HF_IN_SHUTDOWN,
 "inventure_factory_info_query token=%u");
struct info_request {uint32_t serial,source,expected,target,crc,deadline;uint8_t active,due;};
static struct info_request info_request;
static struct timer info_timer;
static struct task_wake info_wake;
static uint_fast8_t info_event(struct timer *timer){(void)timer;info_request.due=1;sched_wake_task(&info_wake);return SF_DONE;}
static void info_result(uint32_t deadline,unsigned result){
 sendf("inventure_factory_counter_runtime_result deadline=%u status=%c",deadline,result);
}
void command_inventure_factory_counter_commit(uint32_t *args){
 uint32_t now=timer_read_time(),deadline=args[5];
 if(info_request.active||!info_idle()||(int32_t)(deadline-now)<(int32_t)MS(10)
     ||(int32_t)(deadline-now)>(int32_t)MS(500)){info_result(deadline,2);return;}
 if(!info_target_supported()){info_result(deadline,7);return;}
 info_request=(struct info_request){args[0],args[1],args[2],args[3],args[4],deadline,1,0};
 info_timer.func=info_event;info_timer.waketime=deadline;sched_add_timer(&info_timer);
}
DECL_COMMAND(command_inventure_factory_counter_commit,
 "inventure_factory_counter_commit_runtime serial=%u source=%c expected=%u target=%u settings_crc=%u deadline=%u");
void inventure_info_task(void){
 if(!sched_check_wake(&info_wake)||!info_request.active||!info_request.due)return;
 struct info_request r=info_request;uint32_t serial=0;int hardware;unsigned result=3;
 if(!info_idle())result=2;
 else if(!info_target_supported())result=7;
 else if(info_read_identity(&serial,&hardware)&&serial==r.serial){
  struct info_flash_context ctx;
  struct info_io io={{(void*)0x08008000u,(void*)0x0800c000u},info_begin,info_end,info_erase,info_program,&ctx};
  result=info_commit(&io,r.source,r.expected,r.target,r.crc);
 }
 info_request.active=info_request.due=0;info_result(r.deadline,result);
}
DECL_TASK(inventure_info_task);

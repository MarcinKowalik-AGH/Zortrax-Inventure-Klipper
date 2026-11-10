/* Compile with production inventure_p4_baud.c. No MCU/MMIO access. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "inventure_p4_baud.h"
static uint8_t flash[32768];
static unsigned cases = 0;
#define EQ(a,b) do {unsigned va=(unsigned)(a),vb=(unsigned)(b); cases++; if(va!=vb) {fprintf(stderr,"FAIL line %d: %s=%u expected %u\n",__LINE__,#a,va,vb);exit(1);}}while(0)
static struct inventure_p4_selection read_status(void){struct inventure_p4_selection s;inventure_p4_from_image(flash,&s);return s;}
static void erase(void){memset(flash,0xff,sizeof(flash));}
static void mark(unsigned rev){const char *s=rev==1?"P4-PERSIST-r1":"P4-PERSIST-r2";memcpy(flash+12345,s,strlen(s)+1);}
static void test_generated(unsigned rev){
    static const uint8_t codes[5]={0x0f,0x33,0x55,0xaa,0xcc};
    static const uint32_t rates[5]={19200,57600,115200,250000,500000};
    erase();mark(rev);
    struct inventure_p4_selection s=read_status();EQ(s.revision,rev);EQ(s.baud,250000);EQ(s.slots_used,0);EQ(s.has_saved,0);
    for (unsigned i=0;i<5;i++) {
        flash[0x7f00+i]=codes[i];s=read_status();
        EQ(s.revision,rev);EQ(s.baud,rates[i]);EQ(s.saved_index,i);EQ(s.slots_used,i+1);EQ(s.invalid_slots,0);EQ(s.has_saved,1);
        EQ(inventure_p4_rate_at_index(i),rates[i]);
    }
    EQ(inventure_p4_rate_at_index(5),750000);
    erase();mark(rev);
    flash[0x7f00]=codes[0];flash[0x7f01]=0xfe;flash[0x7f02]=0;flash[0x7fff]=codes[4];
    s=read_status();EQ(s.baud,500000);EQ(s.saved_index,4);EQ(s.invalid_slots,2);EQ(s.slots_used,256);
    /* Torn record cannot masquerade as a second valid code; earlier wins. */
    erase();mark(rev);flash[0x7f00]=codes[1];flash[0x7f01]=0x3f;
    s=read_status();EQ(s.baud,57600);EQ(s.invalid_slots,1);EQ(s.slots_used,2);
    /* A hole does not stop the scan. */
    flash[0x7ffe]=codes[2];s=read_status();EQ(s.baud,115200);EQ(s.slots_used,255);
    /* An invalid last byte does not cancel the previous valid choice. */
    flash[0x7fff]=0x00;s=read_status();EQ(s.baud,115200);EQ(s.invalid_slots,2);EQ(s.slots_used,256);
}
int main(int argc,char **argv){
    erase();struct inventure_p4_selection s=read_status();EQ(s.revision,0);EQ(s.baud,250000);
    flash[0x7fff]=0x0f;s=read_status();EQ(s.revision,0);EQ(s.baud,250000);EQ(s.has_saved,0);
    for(unsigned rev=1;rev<=2;rev++)test_generated(rev);
    if(argc==3) {
        for(int n=1;n<3;n++) {
            FILE*f=fopen(argv[n],"rb");if(!f){perror("open");return 2;}
            if(fread(flash,1,sizeof(flash),f)!=sizeof(flash)||fgetc(f)!=EOF){fprintf(stderr,"bad boot fixture\n");return 2;}
            fclose(f);s=read_status();EQ(s.revision,n);EQ(s.baud,250000);EQ(s.slots_used,0);
            flash[0x7f00]=0x55;s=read_status();EQ(s.baud,115200);EQ(s.saved_index,2);
        }
    }
    printf("PASS p4_boot_journal_rc5 checks=%u\n",cases);return 0;
}

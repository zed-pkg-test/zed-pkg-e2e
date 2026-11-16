#!/usr/bin/env bash
# PUBLIC, SOURCE-FREE toolchain smoke; does not contain Atto source or compiler.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p .arm-qemu-smoke
cd .arm-qemu-smoke
cat > reset.c <<'C'
#include <stdint.h>
extern int main(void);
void Reset_Handler(void);
__attribute__((section(".isr_vector"),used))
const uintptr_t vectors[] = { (uintptr_t)0x20010000u, (uintptr_t)&Reset_Handler };
static __attribute__((noreturn)) void exit_qemu(uint32_t code) {
  uint32_t args[] = {0x20026u, code};
  register uint32_t r0 __asm__("r0") = 0x20u;
  register const uint32_t *r1 __asm__("r1") = args;
  __asm__ volatile("bkpt #0xab" : "+r"(r0) : "r"(r1) : "memory");
  for (;;) __asm__ volatile("wfi");
}
void Reset_Handler(void) { exit_qemu(main() == 42 ? 0 : 1); }
C
cat > flash.ld <<'LD'
ENTRY(Reset_Handler)
MEMORY {
  FLASH (rx) : ORIGIN = 0, LENGTH = 256K
  RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 64K
}
SECTIONS {
  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
  .text : { *(.text .text.*) *(.rodata .rodata.*) } > FLASH
  .data : { *(.data .data.*) } > RAM AT > FLASH
  .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) } > RAM
  /DISCARD/ : { *(.eh_frame*) *(.ARM.exidx*) *(.ARM.extab*) }
}
LD
clang-18 --target=armv7m-none-eabi -mcpu=cortex-m3 -mthumb -ffreestanding -fno-builtin \
  -fno-stack-protector -fno-exceptions -fno-unwind-tables -fno-asynchronous-unwind-tables \
  -c reset.c -o reset.o
for answer in 42 41; do
cat > guest.ll <<IR
target triple = "thumbv7m-none-eabi"
define i32 @main() {
entry:
  ret i32 $answer
}
IR
  clang-18 --target=armv7m-none-eabi -mcpu=cortex-m3 -mthumb -x ir \
    -ffreestanding -fno-builtin -fno-stack-protector \
    -fno-unwind-tables -fno-asynchronous-unwind-tables \
    -c guest.ll -o guest.o
  # Discard *only* exception-table metadata before linking. Retain actual
  # code relocations and reject any remaining undefined guest dependency.
  llvm-objcopy-18 --remove-section=.ARM.exidx --remove-section=.ARM.extab \
    --strip-unneeded guest.o guest_core.o
  llvm-objcopy-18 --remove-section=.ARM.exidx --remove-section=.ARM.extab \
    --strip-unneeded reset.o reset_core.o
  readelf -W -s guest_core.o | awk '$7 == "UND" && NF>=8 && $8!="" {print "GUEST_UNDEFINED",$8; bad=1} END{exit bad?1:0}'
  clang-18 --target=armv7m-none-eabi -mcpu=cortex-m3 -mthumb \
    -nostdlib -fuse-ld=lld -Wl,-T,flash.ld -Wl,--no-undefined -Wl,--gc-sections \
    reset_core.o guest_core.o -o "case_$answer.elf"
  echo "-- clang18 ARM input symbols and relocations --"
  readelf -W -s reset.o guest.o | tail -80
  readelf -W -r reset.o guest.o | tail -100
  echo "-- clang18 ARM linked symbol table and relocations --"
  readelf -W -s "case_$answer.elf" | tail -90
  readelf -W -r "case_$answer.elf" | tail -70
  readelf -W -h "case_$answer.elf" | grep -Eq 'Machine:[[:space:]]+ARM'
  readelf -W -s "case_$answer.elf" | awk '$7 == "UND" && NF >= 8 && $8 != "" {print "UNDEFINED",$8; bad=1} END {exit bad ? 1 : 0}'
  if readelf -W -l "case_$answer.elf" | grep -Eq '(INTERP|DYNAMIC)'; then exit 3; fi
  expected=1
  if [[ "$answer" == 42 ]]; then expected=0; fi
  set +e
  timeout 20 qemu-system-arm -M lm3s6965evb -nographic \
    -monitor none -serial none \
    -semihosting-config enable=on,target=native \
    -kernel "case_$answer.elf" >"case_$answer.log" 2>&1
  status=$?
  set -e
  cat "case_$answer.log"
  echo "QEMU synthetic return=$answer status=$status expected=$expected"
  [[ "$status" -eq "$expected" ]] || exit 4
  sha256sum "case_$answer.elf"
done
# Adversarial live-import control: exception-metadata cleanup must NOT
# remove an actual code call to a missing host service.
cat > host-import.ll <<'IR'
target triple = "thumbv7m-none-eabi"
declare i32 @host_io()
define i32 @main() {
entry:
  %v = call i32 @host_io()
  ret i32 %v
}
IR
clang-18 --target=armv7m-none-eabi -mcpu=cortex-m3 -mthumb \
  -x ir -ffreestanding -fno-unwind-tables -fno-asynchronous-unwind-tables \
  -O1 -c host-import.ll -o host-import.o
llvm-objcopy-18 --remove-section=.ARM.exidx --remove-section=.ARM.extab \
  --strip-unneeded host-import.o host-import-core.o
readelf -W -s host-import-core.o | grep -Eq 'UND[[:space:]]+host_io'

if clang-18 --target=armv7m-none-eabi -mcpu=cortex-m3 -mthumb \
  -nostdlib -fuse-ld=lld -Wl,-T,flash.ld -Wl,--no-undefined -Wl,--gc-sections \
  reset_core.o host-import-core.o -o denied-host-import.elf >denied-host-import.log 2>&1; then
  echo "FAIL: linker accepted a live missing host service" >&2
  exit 10
fi
test ! -e denied-host-import.elf || { echo "FAIL: linker leaked denied binary" >&2; exit 11; }
grep -q 'host_io' denied-host-import.log
echo "PASS: live external host call remains unresolved and is denied"
echo "SOURCE_FREE_ARM_QEMU_HARNESS_PASS guest42=0 guest41=1 host_negative=pass"

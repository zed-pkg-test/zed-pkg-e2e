# FPGA and ASIC admission: no CPU fallback

The registry contains capability-free `fpga` and `asic` profiles bound to the
`hdl` backend. These profiles are policy upper bounds, not proof that a compiler
can synthesize hardware. Generic `llvm` CPU closures must still reject both
targets; the negative tests in this PR enforce that boundary.

Before claiming executable hardware support, require fixed-width static types,
bounded loops/memory/topology, explicit clock/reset/CDC contracts, and rejection
of heap, GC, FFI, await and hot reload. Verify exact-source RTL lowering and
simulation separately from FPGA synthesis/place-route/bitstream/board execution
and ASIC mapping/physical layout/STA/DRC/LVS/fabrication.

Reject signedness/overflow surprises, missing timing contracts, dynamic effects,
arbitrary CPU fallback and unpinned tools. Preserve existing target budgets.

Status: specification and negative tests only; no synthesis capability certified.

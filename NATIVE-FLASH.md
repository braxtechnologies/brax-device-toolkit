# Native Python flashing development

The experimental engine talks directly to the MediaTek preloader and the stock
Download Agent. It does not start SP Flash Tool, load its DLLs, or import
mtkclient. A USB driver and the matching vendor Download Agent remain necessary.
The agent is supplied separately, not redistributed with this repository.

## Current scope

The Install OS advanced page offers native storage inspection. The development
CLI also supports bounded raw boot-image partition writes with a full backup,
a second pre-write read, and full-partition readback. Factory identity partitions,
bootloader storage, format, sparse images and full OS installation are excluded.
The production OS compatibility gate is unchanged. A chip identifier alone does
not establish firmware compatibility.

The current loader profile accepts one inspected stock DA SHA256:
`f8fc84afc9692e4ce3ea9ad5e2b0be2aaf9d83dc0627a5066ef1e03e897b55d3`.
It rejects requested loader authentication instead of bypassing it. Firmware
package signature verification is separate from this local agent hash check.

Use `python -m brax_snwriter.native_flash --help` for development arguments.
Start listening before reconnecting the fully powered-off phone. Android USB
debugging is not used for this transport. META mode is a different protocol.

Each write creates a new transaction directory containing the original partition,
hashes, target offset and storage identity. A write-started marker without a
successful result means the partition needs inspection or recovery. Never repeat
a failed write automatically. Reboot acknowledgment does not prove Android boot;
verify the phone separately after readback succeeds.

## Investigation evidence

REA 6.7.0 with Ghidra 12.1.4 decompiled SP Flash Tool V6.2404 `flash.dll`
function `0x10041ff0`. Its agent-entry sequence includes host capabilities,
hardware initialization, host information and BOOT-TO with load/jump addresses
and a memory transfer. Binary SHA256:
`463d8bfb566bd9722ee372872d031b3b907a863649b42564139a6b80b63a6b2a`.
REA evidence identifier:
`ev_007f33be0a1a964f8a7acb31d0b2a80966ecc25a8da1b404d802c74b40f6a9cf`.

Protocol framing was also cross-checked against the mtkclient reference and
live device responses. Its GPL implementation is not copied or bundled here.
Static analysis is not a substitute for live write/readback validation.

Live testing on 2026-11-10 reached both stock agent stages, read eMMC information
and 68 partitions, and completed a 64 MiB raw write to the inactive `boot_b`
partition. Full readback matched the original backup, SHA256
`3b6a07d0d404fab4e23b6d34bc6696a6a312dd92821332385e5af7c01c421351`.
The original partition was blank; this same-content test verifies the transfer
and readback path, not installation or booting of a different OS. The agent
accepted the reboot command. After reboot, ADB confirmed boot completion, the
unchanged BaseOS build 109 fingerprint (Android 16), active slot `_a`, and verified
boot state `green`. This is not a consumer-ready replacement for full SP Flash
Tool functionality.

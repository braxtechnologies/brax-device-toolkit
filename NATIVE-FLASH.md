# Native Python flashing development

The experimental engine talks directly to the MediaTek preloader and the stock
Download Agent. It does not start SP Flash Tool, load its DLLs, or import
mtkclient. A USB driver and the matching vendor Download Agent remain necessary.
The agent is supplied separately, not redistributed with this repository.

## Current scope

The Install OS advanced page offers native storage inspection and an experimental
BaseOS 1.0 installation. The reviewed package is
`baseos-1.0-20260826-brax3-spflash.zip`, SHA256
`5d5ac5987f3d96e4f0472c71e478899e531055b0a4183095370a4bba31965e4a`.
Other packages are rejected. The normal catalog compatibility gate is unchanged.
A chip identifier alone does not establish firmware compatibility.

The BaseOS path requires a matching recorded storage identity and partition
layout. The UI additionally checks BraX3 identity and active slot A in Android.
It resets userdata and encryption metadata, installs the selected eMMC user-area
firmware (including LK and TEE images), and preserves preloader boot regions and
factory identity partitions. It does not change slots or partition tables.

Sparse RAW and FILL ranges are inspected in bounded pieces; the stock agent
receives original sparse files through WRITE-PARTITIONS and may erase entire
selected partitions. DONT_CARE ranges are not compared during readback.
Sparse images are fully validated before connection. CRC-bearing
sparse images are rejected rather than silently ignoring their checksums. The
reviewed package does not require that feature. This installation resets data
but does not securely overwrite unused storage.

Full-install validation is pending. The first live attempt completed image-range
backup, then the stock agent denied raw WRITE-FLASH on `connsys_bt_a` before
requesting image data. The installer now uses WRITE-PARTITIONS and restricted
file-serving callbacks, as observed in the successful vendor-tool log. This
change still requires its live installation and first-boot test; automated tests
alone do not establish that it works on the phone.

Image-data ranges are backed up before the first write. Each image's RAW and FILL
data are read back and compared after download. Failed writes are not retried.
The saved plan, backup index and verified-range log support diagnosis, but are
not a complete backup or a validated full-phone recovery method: partition
erasure can affect ranges outside them. Do not retry interrupted installations
blindly or treat these files as backups of personal data.

To use the UI, open Install OS → Advanced recovery, check the powered-off
connection, then use Read storage using Python. Let Android start and authorize
USB debugging. Choose Review native BaseOS installation, select the reviewed
ZIP, and follow the power-off/reconnection instructions. Progress and debug
messages are shown in the existing firmware log. First boot must be checked
separately; transfer completion alone is not installation success.

The current loader profile accepts one inspected stock DA SHA256:
`f8fc84afc9692e4ce3ea9ad5e2b0be2aaf9d83dc0627a5066ef1e03e897b55d3`.
It rejects requested loader authentication instead of bypassing it. Firmware
package signature verification is separate from this local agent hash check.

Use `python -m brax_snwriter.native_flash --help` for development arguments.
Use `python -m brax_snwriter.native_install --help` for the reviewed package
installer. Without `--install-and-reset-userdata`, it prepares a plan only.
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

# Third-party components

Brax source code is covered by LICENSE. Dependencies retain their own licenses.
Packaged builds include Python (PSF license), Tcl/Tk (their BSD-style licenses),
pyserial (BSD license) and the PyInstaller bootloader (GPL with its distribution
exception). Preserve the notices supplied with these components. Their inclusion
does not change the license on Brax's code.

SP Flash Tool, MediaTek download agents, Android platform-tools, USB drivers and
OS images are external, separately licensed software. They are not in this source
repository or its current release bundle. Obtain these components from their approved distributors and retain their
license notices. The MIT license for this project does not grant permission to
redistribute them.

Brax branding identifies this project; MIT does not grant trademark rights.

Fallback notices (some Windows wheels omit them):
- pyserial 3.5: https://github.com/pyserial/pyserial/blob/v3.5/LICENSE.txt
- Tcl: https://github.com/tcltk/tcl/blob/core-8-6-14/license.terms

The build copies installed dependency notices and these fallbacks into its
`licenses` data folder. The ZIP exposes them under `_internal/licenses`.

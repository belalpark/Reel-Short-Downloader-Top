# Tcl/Tk license provenance

The Windows Python distribution may omit standalone Tcl/Tk `license.terms` files. The build first looks in the installed runtime, then uses these upstream Tcl/Tk 8.6 license texts when absent:

- `tcl8.6-license.terms`: https://github.com/tcltk/tcl/blob/core-8-6-13/license.terms — Git blob `d8049cd9e7ca055f7e584a76f88861a294b30c9c`.
- `tk8.6-license.terms`: https://github.com/tcltk/tk/blob/core-8-6-13/license.terms — Git blob `01264352c87ca7e34771bd1ee18bed5201ef139f`.

These are upstream license notices, not changes to Tcl/Tk. The Python runtime license is also retained in the application package.

# Third-party notices

The MIT licence in `LICENSE` covers DATUM's own source. A built and
installed DATUM also ships the libraries below, each unmodified, and each
under its own terms.

| Library | Used for | Licence |
| --- | --- | --- |
| Qt, via **PySide6** | The interface, the drawing painter, PDF and SVG output, printing | LGPL-3.0 |
| OpenCASCADE, via **cadquery-ocp** | The geometry kernel: every solid, boolean, fillet and hidden-line projection | LGPL-2.1 with the OCCT exception |
| **VTK** | Nothing, directly. OpenCASCADE's Python binding links it, so it has to be present or the kernel will not load at all | BSD 3-clause |
| **NumPy** | The least-squares solver behind sketches and assembly constraints | BSD 3-clause |
| **cryptography** | Verifying that an update really came from IITEG | Apache-2.0 / BSD 3-clause |
| **Python** | The runtime DATUM is written in | PSF |

## On the LGPL libraries

Qt and OpenCASCADE are LGPL, which asks that anybody who receives a binary
can replace those libraries with their own build.

A DATUM installation is one folder, and every one of these arrives as its
own file inside it rather than being linked into `DATUM.exe`:

```
DATUM.exe
_internal/
    PySide6/            Qt
    cadquery_ocp.libs/  OpenCASCADE
    vtk.libs/           VTK
    OCP/
```

Replacing any of them is a matter of swapping the files, so the
requirement is met by the layout rather than by a promise.

The exact versions are recorded in the `*.dist-info` folders under
`_internal/`. Each project publishes its own source; if you would rather
have it from us, write to <datum@iiteg.com>.

## Trademarks

Autodesk, Inventor, SolidWorks and Windows belong to their respective
owners and are referred to only to describe what DATUM is like. DATUM is
not affiliated with, endorsed by, or derived from any of them.

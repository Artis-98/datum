# DATUM on Linux (preview)

This is a preview. It is built and tested automatically on Ubuntu 22.04,
but DATUM is used day to day on Windows, so expect rough edges and please
report them at https://github.com/Artis-98/datum/issues.

## Running it

Unpack the archive anywhere you like and start `DATUM` inside it:

```bash
tar -xzf DATUM-*-linux-x86_64-preview.tar.gz
./DATUM/DATUM
```

Nothing is installed and nothing needs root. To remove it, delete the
folder.

It needs glibc 2.31 or newer (Ubuntu 20.04, Debian 11, Fedora 32 and
anything later) and an X11 or XWayland desktop with OpenGL. On a minimal
system Qt may ask for a few libraries; on Ubuntu and Debian these cover it:

```bash
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libgl1 libegl1
```

## What is different from Windows

* **No automatic updates.** The update feed carries Windows builds, so the
  Linux build does not check it. Download a new build from
  https://datum.iiteg.com when one is out and unpack it over this one.
* **SpaceMouse** support needs read access to the device. 3Dconnexion's
  own udev rule, or one granting your user the hidraw device, does that.
* Documents go in `~/Documents/DATUM` and settings under `~/.config`,
  the same places a Linux desktop keeps everything else.

## If the 3D view will not start

DATUM always runs through X11, XWayland on a Wayland desktop, because the
OpenCASCADE viewer draws into an X11 window. If it reports that it could
not get an OpenGL window, check that XWayland has working OpenGL:

```bash
glxinfo -B
```

It should name your graphics card. Please open an issue with what it
prints, your graphics card and driver, and the output of
`echo $XDG_SESSION_TYPE $QT_QPA_PLATFORM`. To try another Qt platform
anyway, set `DATUM_QT_PLATFORM` before starting it.

# KiCad IPC API — Connection Notes

## The Socket Problem

KiCad 9 exposes **two** Unix sockets in `/tmp/kicad/`:

| Socket | Owner | Handles |
|--------|-------|---------|
| `api.sock` | KiCad main process | `get_version()`, `ping()` only |
| `api-<PID>.sock` | PCB editor instance | `get_board()`, `get_footprints()`, `get_pads()`, everything else |

The default `KiCad()` constructor connects to `api.sock`. This **cannot** do
board operations — `get_board()` fails with:

```
no handler available for request of type kiapi.common.commands.GetOpenDocuments
```

## The Fix

Connect to the **editor socket** with the `ipc://` prefix:

```python
from kipy import KiCad

k = KiCad(socket_path='ipc:///tmp/kicad/api-<PID>.sock')
b = k.get_board()
```

The PID changes every time KiCad restarts. To find it:

```python
import glob
socks = glob.glob('/tmp/kicad/api-*.sock')
# Usually exactly one — the open PCB editor
```

## Helper: auto-detect editor socket

```python
import glob
from kipy import KiCad

def connect_kicad():
    socks = glob.glob('/tmp/kicad/api-*.sock')
    if not socks:
        raise RuntimeError('No KiCad PCB editor socket found in /tmp/kicad/')
    return KiCad(socket_path='ipc://' + socks[0])
```

## kipy internals

- kipy 0.4.0 uses **pynng** (nanomsg next-gen), not raw Unix sockets
- Socket paths must use `ipc:///path` format when passed explicitly
- The default path lookup reads `KICAD_API_SOCKET` env var, falls back to platform default (`api.sock`)
- `KiCadClient.__init__` takes `(socket_path, client_name, kicad_token, timeout_ms)`

## Checklist when KiCad API fails

1. Is KiCad running? (`ls /tmp/kicad/`)
2. Is the PCB editor open? (need `api-*.sock`, not just `api.sock`)
3. Is `/tmp/kicad/` owned by the right user? (`chown ubuntu:ubuntu` if root-owned)
4. Are you using `ipc://` prefix? (required for pynng dial)
5. Did KiCad restart? (PID in socket name changes — re-detect)

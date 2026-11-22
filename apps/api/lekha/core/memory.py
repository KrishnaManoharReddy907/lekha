"""Give freed memory back to the operating system after heavy work.

CPython frees objects into glibc's heap, which keeps the pages. After an OCR run or a demo
build that briefly used 100+ MB, RSS would stay high and the next spike would stack on top
of it. On a 512 MB host that is the difference between working and being killed.
"""

from __future__ import annotations

import ctypes
import gc
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _libc():
    if not sys.platform.startswith("linux"):
        return None
    try:
        return ctypes.CDLL("libc.so.6")
    except OSError:
        return None


def release_memory() -> None:
    gc.collect()
    libc = _libc()
    if libc is not None:
        try:
            libc.malloc_trim(0)
        except Exception:  # musl or an unusual libc: nothing to trim
            pass

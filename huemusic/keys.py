"""Non-blocking single-key input for the terminal (Windows and POSIX)."""
from __future__ import annotations

import os
import sys

if os.name == "nt":
    import msvcrt

    class KeyReader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def poll(self) -> list[str]:
            keys = []
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # arrow/function keys: ignore
                    msvcrt.getwch()
                    continue
                keys.append(ch)
            return keys
else:
    import select
    import termios
    import tty

    class KeyReader:
        def __enter__(self):
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            return self

        def __exit__(self, *exc):
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            return False

        def poll(self) -> list[str]:
            keys = []
            while select.select([sys.stdin], [], [], 0)[0]:
                keys.append(sys.stdin.read(1))
            return keys

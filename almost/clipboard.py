"""Send tmux's OSC 52 copies to a clipboard tool on the local computer."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys


OSC52 = b"\x1b]52;"
MAX_SEQUENCE = 8 * 1024 * 1024


class Clipboard:
    def __init__(self, command: list[str]):
        self.command = command
        self.pending = b""

    @classmethod
    def detect(cls) -> Clipboard | None:
        if sys.platform == "darwin":
            candidates = [["pbcopy"]]
        else:
            candidates = [["termux-clipboard-set"]]
            if os.environ.get("WAYLAND_DISPLAY"):
                candidates.append(["wl-copy"])
            if os.environ.get("DISPLAY"):
                candidates.extend([["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]])
        for command in candidates:
            if executable := shutil.which(command[0]):
                return cls([executable, *command[1:]])
        return None

    def copy(self, sequence: bytes) -> bool:
        selection, separator, encoded = sequence[len(OSC52):].partition(b";")
        if not separator or selection not in (b"", b"c") or encoded == b"?":
            return False
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError:
            return False
        try:
            subprocess.run(self.command, input=data, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2, check=True)
        except (OSError, subprocess.SubprocessError):
            # Leave the OSC sequence intact so the terminal can still handle it.
            return False
        return True

    def feed(self, data: bytes) -> bytes:
        self.pending += data
        output = bytearray()
        while self.pending:
            start = self.pending.find(OSC52)
            if start < 0:
                # Retain only a possible prefix split across two reads.
                keep = next((size for size in range(len(OSC52) - 1, 0, -1)
                             if self.pending.endswith(OSC52[:size])), 0)
                end = len(self.pending) - keep
                output.extend(self.pending[:end])
                self.pending = self.pending[end:]
                break
            output.extend(self.pending[:start])
            self.pending = self.pending[start:]
            ends = [(end, length) for end, length in
                    ((self.pending.find(b"\x07"), 1), (self.pending.find(b"\x1b\\"), 2)) if end >= 0]
            if not ends:
                if len(self.pending) > MAX_SEQUENCE:
                    output.extend(self.finish())
                break
            end, length = min(ends)
            sequence = self.pending[:end + length]
            if len(sequence) > MAX_SEQUENCE or not self.copy(self.pending[:end]):
                output.extend(sequence)
            self.pending = self.pending[end + length:]
        return bytes(output)

    def finish(self) -> bytes:
        pending, self.pending = self.pending, b""
        return pending

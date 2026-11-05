import base64
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from almost.clipboard import Clipboard, MAX_SEQUENCE
from almost.config import Profile
from almost.connect import run


def osc52(data, selection=b"", terminator=b"\x07"):
    return b"\x1b]52;" + selection + b";" + base64.b64encode(data) + terminator


class ClipboardTests(unittest.TestCase):
    def test_detects_local_clipboard_tools(self):
        cases = [
            ("darwin", {}, {"pbcopy"}, ["/bin/pbcopy"]),
            ("linux", {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
             {"wl-copy", "xclip"}, ["/bin/wl-copy"]),
            ("linux", {"DISPLAY": ":0"}, {"xclip"}, ["/bin/xclip", "-selection", "clipboard"]),
            ("linux", {"DISPLAY": ":0"}, {"xsel"}, ["/bin/xsel", "--clipboard", "--input"]),
            ("linux", {}, {"termux-clipboard-set"}, ["/bin/termux-clipboard-set"]),
            ("linux", {}, {"xclip", "wl-copy"}, None),
            ("linux", {"DISPLAY": ":0"}, set(), None),
        ]
        for platform, environment, installed, expected in cases:
            with self.subTest(platform=platform, environment=environment, installed=installed), \
                    patch("sys.platform", platform), patch.dict(os.environ, environment, clear=True), \
                    patch("almost.clipboard.shutil.which", side_effect=lambda name: "/bin/" + name if name in installed else None):
                clipboard = Clipboard.detect()
                self.assertEqual(clipboard.command if clipboard else None, expected)

    def test_fragmented_sequences_preserve_text_and_copy_exact_bytes(self):
        data = "copied Ω\nsecond line\n".encode()
        for terminator in (b"\x07", b"\x1b\\"):
            sequence = osc52(data, b"c", terminator)
            stream = b"before\x1b[32m" + sequence + b"after\x1b[0m"
            for split in range(len(stream) + 1):
                with self.subTest(terminator=terminator, split=split), \
                        patch("almost.clipboard.subprocess.run") as copy:
                    clipboard = Clipboard(["clipboard-tool"])
                    output = clipboard.feed(stream[:split]) + clipboard.feed(stream[split:]) + clipboard.finish()
                    self.assertEqual(output, b"before\x1b[32mafter\x1b[0m")
                    self.assertEqual(copy.call_count, 1)
                    self.assertEqual(copy.call_args.kwargs["input"], data)

    def test_multiple_copies_and_byte_at_a_time_delivery(self):
        clipboard = Clipboard(["clipboard-tool"])
        stream = b"a" + osc52(b"one") + b"b" + osc52(b"two\n") + b"c"
        with patch("almost.clipboard.subprocess.run") as copy:
            output = b"".join(clipboard.feed(bytes([byte])) for byte in stream) + clipboard.finish()
        self.assertEqual(output, b"abc")
        self.assertEqual([call.kwargs["input"] for call in copy.call_args_list], [b"one", b"two\n"])

    def test_queries_invalid_data_and_other_selections_pass_through(self):
        stream = (b"\x1b]52;c;?\x07" + b"\x1b]52;c;not-base64!\x07" +
                  osc52(b"primary selection", b"p") + b"\x1b]0;window title\x07" +
                  b"unfinished\x1b]52;;YQ")
        clipboard = Clipboard(["clipboard-tool"])
        with patch("almost.clipboard.subprocess.run") as copy:
            self.assertEqual(clipboard.feed(stream) + clipboard.finish(), stream)
        copy.assert_not_called()

    def test_failed_clipboard_command_preserves_terminal_fallback(self):
        sequence = osc52(b"copy me")
        for error in (OSError("missing tool"), subprocess.CalledProcessError(1, ["tool"]),
                      subprocess.TimeoutExpired(["tool"], 2)):
            with self.subTest(error=error), patch("almost.clipboard.subprocess.run", side_effect=error):
                clipboard = Clipboard(["clipboard-tool"])
                self.assertEqual(clipboard.feed(sequence) + clipboard.finish(), sequence)

    def test_oversized_sequence_does_not_buffer_unbounded_output(self):
        clipboard = Clipboard(["clipboard-tool"])
        stream = b"\x1b]52;;" + b"YQ==" * (MAX_SEQUENCE // 4 + 1)
        with patch("almost.clipboard.subprocess.run") as copy:
            self.assertEqual(clipboard.feed(stream) + clipboard.finish(), stream)
        copy.assert_not_called()

    def test_interactive_ssh_stream_reaches_local_clipboard_and_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "clipboard"
            clipboard = Clipboard([sys.executable, "-c",
                                   "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
                                   str(target)])
            data = "remote selection Ω\n".encode()
            stream = b"screen before\r\n" + osc52(data) + b"screen after\r\n"
            script = "import os; os.write(1, " + repr(stream) + ")"
            output = SimpleNamespace(buffer=io.BytesIO())
            watch = SimpleNamespace(check=lambda: None, cancelled=threading.Event())
            with patch("almost.connect.ssh.base", return_value=[sys.executable, "-c", script]), \
                    patch("almost.connect.sys.stdout", output), patch("sys.stdin.isatty", return_value=False):
                result = run(Profile("test", "unused", ()), "unused", watch,
                             interactive=True, clipboard=clipboard)
            self.assertEqual(result.code, 0)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(output.buffer.getvalue(), b"screen before\r\nscreen after\r\n")


if __name__ == "__main__":
    unittest.main()

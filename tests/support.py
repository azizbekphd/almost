import errno
import fcntl
import os
import pty
import select
import signal
import subprocess
import termios
import time


class Terminal:
    """A bounded, drained pseudo-terminal for exercising the actual CLI."""
    def __init__(self, args, env=None):
        self.master, slave = pty.openpty()
        self.output = bytearray()

        def setup():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        environment = dict(os.environ if env is None else env)
        environment["TERM"] = "xterm-256color"
        self.child = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave,
                                      env=environment, preexec_fn=setup, close_fds=True)
        os.close(slave)
        os.set_blocking(self.master, False)

    def pump(self):
        if self.master is None:
            return
        while select.select([self.master], [], [], 0)[0]:
            try:
                chunk = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    return
                raise
            if not chunk:
                return
            self.output.extend(chunk)
            if len(self.output) > 1_000_000:
                del self.output[:-1_000_000]

    @property
    def text(self):
        self.pump()
        return self.output.decode("utf-8", "replace")

    def send(self, value):
        os.write(self.master, value)

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            value = predicate()
            self.pump()
            if value:
                return value
            if self.child.poll() is not None:
                raise AssertionError(f"Terminal exited with {self.child.returncode}: {self.text[-4000:]}")
            time.sleep(0.1)
        raise AssertionError(f"Terminal timed out: {self.text[-4000:]}")

    def wait_exit(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            code = self.child.poll()
            if code is not None:
                return code
            time.sleep(0.05)
        raise AssertionError(f"Terminal failed to exit: {self.text[-4000:]}")

    def hangup(self):
        if self.master is not None:
            os.close(self.master)
            self.master = None

    def close(self):
        self.hangup()
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("Condition did not become true before timeout")

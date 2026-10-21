"""Process guardian. Kill the process group when the coordinating parent disappears."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    parent = int(sys.argv[1])
    child = subprocess.Popen(sys.argv[3:], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    while child.poll() is None:
        if os.getppid() != parent:
            os.killpg(os.getpgrp(), signal.SIGTERM)
            return 143
        time.sleep(0.1)
    return int(child.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

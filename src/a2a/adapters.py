from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

from .storage import BoundaryError, encode


class SyntheticAdapter:
    """Real subprocess I/O with synthetic content; never accepts a command from a prompt."""
    def call(self, request, timeout=3):
        environment = {key: value for key, value in os.environ.items()
                       if key in ("PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "LANG")}
        process = subprocess.Popen([sys.executable, "-m", "a2a.fake_engine"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=environment, start_new_session=True)
        try:
            out, err = process.communicate(encode(request), timeout=timeout)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise BoundaryError("synthetic adapter timeout") from error
        if process.returncode:
            raise BoundaryError("synthetic adapter failed")
        try:
            return json.loads(out)
        except json.JSONDecodeError as error:
            raise BoundaryError("invalid adapter JSON") from error

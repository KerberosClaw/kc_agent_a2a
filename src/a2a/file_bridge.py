"""Private local directory bridge: the network-disabled container never receives credentials."""
import json
import time
import uuid
from pathlib import Path

from .storage import BoundaryError, atomic_write, encode


class FileBridge:
    def __init__(self, root: Path): self.root = root

    def call(self, request, timeout=200):
        request_id = str(uuid.UUID(request['request_id']))
        path = self.root/'requests'/(request_id+'.json')
        response = self.root/'responses'/(request_id+'.json')
        atomic_write(path, encode(request))
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if response.exists():
                result = json.loads(response.read_text())
                if result.get('request_id') != request_id:
                    raise BoundaryError('bridge response identity mismatch')
                if result.get('error'): raise BoundaryError(result['error'])
                return result['result']
            time.sleep(0.1)
        raise BoundaryError('bridge timeout; native outcome unknown')

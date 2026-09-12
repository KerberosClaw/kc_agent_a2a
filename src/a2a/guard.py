"""macOS guard applied to the coordinator and its native descendants."""
import json
import platform
from pathlib import Path

from .storage import BoundaryError


def profile(protected_paths):
    paths=tuple(Path(p).resolve() for p in protected_paths)
    if not paths or any(str(p)=='/' for p in paths):raise BoundaryError('invalid protected paths')
    return '(version 1)\n(allow default)\n'+'\n'.join(
        '(deny file-write* (subpath '+json.dumps(str(p))+'))' for p in paths)


def native_guard(command, protected_paths):
    if platform.system() != 'Darwin' or not Path('/usr/bin/sandbox-exec').exists():
        raise BoundaryError('macOS write guard required for real-persona trial')
    return ['/usr/bin/sandbox-exec','-p',profile(protected_paths),*command]

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

from .storage import BoundaryError


def snapshot(root: Path, baseline: str, today: date, after_read=None):
    root = root.resolve()
    if Path(baseline).name != baseline:
        raise BoundaryError("baseline must be a filename")

    def paths():
        return [root / baseline, *sorted((root / "patches").glob("*.md")),
                *sorted(p for p in (root / "journal").glob("*.md")
                        if p.stem in {(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(5)})]

    files = paths()
    manifest, blocks = [], []
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise BoundaryError("persona path escaped root")
        data = path.read_bytes()
        if not data.strip():
            raise BoundaryError("empty required persona file")
        content = data.decode("utf-8")
        if path.parent.name == "journal":
            content = next(line for line in content.splitlines() if line.strip())
        manifest.append({"path": str(path.relative_to(root)), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        blocks.append(content)
    if after_read:
        after_read()
    if files != paths() or any(hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"] for path, item in zip(files, manifest)):
        raise BoundaryError("persona snapshot changed while reading")
    return {"manifest": manifest, "content": "\n\n".join(blocks)}

import os
import time
from pathlib import Path
from typing import Optional

from .common import LOG

def clean_snapshots(snapshot_dir: Path, older_than: Optional[float] = None) -> int:
    requested = snapshot_dir.expanduser()
    if requested.is_symlink():
        raise ValueError(f"Refusing symlink snapshot directory: {requested}")

    target = requested.resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve()}
    if target in forbidden:
        raise ValueError(f"Refusing unsafe snapshot directory: {target}")
    if not target.exists():
        LOG.info("Snapshot directory does not exist: %s", target)
        return 0
    if not target.is_dir():
        raise ValueError(f"Snapshot path is not a directory: {target}")

    removed = 0
    for current, dirnames, filenames in os.walk(target, topdown=False, followlinks=False):
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.is_symlink():
                LOG.warning("Skipping snapshot symlink: %s", path)
                continue
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}:
                if older_than is not None and path.stat().st_mtime >= older_than:
                    continue
                path.unlink()
                removed += 1
                LOG.info("Deleted snapshot: %s", path)

        for dirname in dirnames:
            path = current_path / dirname
            if path.is_symlink():
                LOG.warning("Skipping snapshot directory symlink: %s", path)
                continue
            try:
                path.rmdir()
                LOG.info("Removed empty snapshot directory: %s", path)
            except OSError:
                # Preserve directories containing non-snapshot files.
                pass

    LOG.info("Snapshot cleanup completed: %d image(s) deleted from %s", removed, target)
    return removed


def run_scheduled_snapshot_cleanup(cfg: dict, snapshot_dir: Path) -> int:
    retention_days = float(cfg.get("snapshot_retention_days", 7))
    if retention_days <= 0:
        LOG.debug("Automatic snapshot cleanup is disabled")
        return 0
    cutoff = time.time() - retention_days * 24 * 60 * 60
    LOG.info(
        "Cleaning snapshots older than %.2f day(s) from %s",
        retention_days, snapshot_dir,
    )
    return clean_snapshots(snapshot_dir, older_than=cutoff)



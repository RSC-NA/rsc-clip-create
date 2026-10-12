"""Reconcile the storage directory against the database.

Hard deletion unlinks files after committing the row, and a render that fails
partway can still leave bytes behind, so the two can drift. This reports the
drift in both directions and can repair it.

    python -m backend.app.maintenance audit
    python -m backend.app.maintenance audit --fix
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.logging_config import configure_logging
from backend.app.models import BrandSweep, Clip, Video


logger = logging.getLogger(__name__)
settings = get_settings()

# (folder under storage/, model, column holding the path)
TRACKED = (
    ("clips", Clip, "storage_path"),
    ("renders", Video, "storage_path"),
    ("sweeps", BrandSweep, "storage_path"),
)


@dataclass
class AuditReport:
    orphan_files: list[Path] = field(default_factory=list)
    missing_files: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.orphan_files and not self.missing_files


def audit(db: Session) -> AuditReport:
    """Files with no row, and rows pointing at files that are gone."""
    report = AuditReport()
    referenced: set[Path] = set()

    for folder, model, column in TRACKED:
        folder_references: set[Path] = set()
        for row in db.scalars(select(model)).all():
            raw = getattr(row, column)
            if not raw:
                continue
            path = Path(raw)
            normalized = path.resolve()
            referenced.add(normalized)
            folder_references.add(normalized)
            if not path.is_file():
                report.missing_files.append((model.__tablename__, row.id, raw))

        directory = settings.storage_root / folder
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.iterdir()):
            if not candidate.is_file() or candidate.name.startswith("."):
                continue
            if candidate.resolve() not in folder_references:
                report.orphan_files.append(candidate)

    # The legacy logos table is gone, but other rows can still point at an
    # uploaded logo. Only direct, ordinary files in this directory are eligible.
    logos = settings.storage_root / "logos"
    if logos.is_dir() and not logos.is_symlink():
        for candidate in sorted(logos.iterdir()):
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or candidate.name.startswith(".")
            ):
                continue
            if candidate.resolve() not in referenced:
                report.orphan_files.append(candidate)

    return report


def repair(db: Session, report: AuditReport) -> int:
    """Delete orphaned files. Rows pointing at missing files are reported only.

    Removing those rows would silently destroy render history, so that call is
    left to a human.
    """
    removed = 0
    for path in report.orphan_files:
        try:
            path.unlink()
            removed += 1
            logger.info("removed orphan %s", path)
        except OSError:
            logger.warning("could not remove %s", path, exc_info=True)
    return removed


def _render(report: AuditReport) -> None:
    if report.clean:
        print("Storage and database agree. Nothing to do.")
        return

    if report.orphan_files:
        total = sum(p.stat().st_size for p in report.orphan_files if p.is_file())
        print(f"\nFiles with no database row ({len(report.orphan_files)}, {total / 1e6:.1f} MB):")
        for path in report.orphan_files:
            print(f"  {path}")
        print("\n  Re-run with --fix to delete these.")

    if report.missing_files:
        print(f"\nRows pointing at missing files ({len(report.missing_files)}):")
        for table, row_id, path in report.missing_files:
            print(f"  {table} {row_id} -> {path}")
        print("\n  Not repaired automatically: deleting these would discard history.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit"])
    parser.add_argument("--fix", action="store_true", help="delete orphaned files")
    args = parser.parse_args()

    configure_logging()
    with SessionLocal() as db:
        report = audit(db)
        _render(report)
        if args.fix and report.orphan_files:
            removed = repair(db, report)
            print(f"\nRemoved {removed} orphaned file(s).")
    return 0 if report.clean else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

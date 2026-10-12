import sys

from sqlalchemy.orm import sessionmaker

from backend.app import maintenance
from backend.app.maintenance import audit, repair


def _run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["maintenance", "audit", *args])
    result = maintenance.main()
    return result, capsys.readouterr().out


def test_cli_audits_and_removes_legacy_logo_without_logos_table(
    engine, isolated_storage, monkeypatch, capsys
):
    logo = isolated_storage / "logos" / "old.png"
    logo.parent.mkdir()
    logo.write_bytes(b"old logo")
    monkeypatch.setattr(maintenance, "SessionLocal", sessionmaker(bind=engine))

    status, output = _run_cli(monkeypatch, capsys)
    assert status == 1
    assert "Files with no database row (1," in output
    assert str(logo) in output
    assert logo.read_bytes() == b"old logo"

    _, output = _run_cli(monkeypatch, capsys, "--fix")
    assert "Files with no database row (1," in output
    assert "Removed 1 orphaned file(s)." in output
    assert not logo.exists()

    status, output = _run_cli(monkeypatch, capsys)
    assert status == 0
    assert output == "Storage and database agree. Nothing to do.\n"


def test_cli_logo_cleanup_preserves_references_and_files_outside_logos(
    engine, db_session, make_clip, isolated_storage, tmp_path, monkeypatch, capsys
):
    logos = isolated_storage / "logos"
    logos.mkdir()
    referenced = logos / "in-use.png"
    referenced.write_bytes(b"keep")
    make_clip(storage_path=str(referenced))
    orphan = logos / "old.png"
    orphan.write_bytes(b"remove")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    link = logos / "outside-link.png"
    link.symlink_to(outside)
    nested = logos / "nested"
    nested.mkdir()
    (nested / "untouched.png").write_bytes(b"nested")
    monkeypatch.setattr(maintenance, "SessionLocal", sessionmaker(bind=engine))

    _, output = _run_cli(monkeypatch, capsys, "--fix")
    assert "Files with no database row (1," in output
    assert str(orphan) in output
    assert str(referenced) not in output
    assert str(outside) not in output
    assert "Removed 1 orphaned file(s)." in output
    assert not orphan.exists()
    assert referenced.read_bytes() == b"keep"
    assert outside.read_bytes() == b"outside"
    assert link.is_symlink()
    assert (nested / "untouched.png").read_bytes() == b"nested"

    status, output = _run_cli(monkeypatch, capsys)
    assert status == 0
    assert output == "Storage and database agree. Nothing to do.\n"




def test_a_clean_store_reports_nothing(db_session, isolated_storage):
    report = audit(db_session)
    assert report.clean
    assert report.orphan_files == [] and report.missing_files == []


def test_a_file_with_no_row_is_an_orphan(db_session, isolated_storage):
    stray = isolated_storage / "renders" / "left-behind.mp4"
    stray.write_bytes(b"partial render")

    report = audit(db_session)
    assert report.orphan_files == [stray]
    assert not report.clean


def test_a_row_with_no_file_is_reported(db_session, make_clip):
    make_clip(storage_path="storage/clips/vanished.mp4")

    report = audit(db_session)
    assert len(report.missing_files) == 1
    assert report.missing_files[0][0] == "clips"


def test_a_referenced_file_is_not_an_orphan(db_session, isolated_storage, make_clip):
    tracked = isolated_storage / "clips" / "Falcons-Ace-Gold.mp4"
    tracked.write_bytes(b"real clip")
    make_clip("Falcons-Ace-Gold.mp4", storage_path=str(tracked))

    report = audit(db_session)
    assert report.orphan_files == []
    assert report.missing_files == []


def test_repair_deletes_orphans_only(db_session, isolated_storage, make_clip):
    keep = isolated_storage / "clips" / "Falcons-Ace-Gold.mp4"
    keep.write_bytes(b"real clip")
    make_clip("Falcons-Ace-Gold.mp4", storage_path=str(keep))
    stray = isolated_storage / "renders" / "left-behind.mp4"
    stray.write_bytes(b"partial render")

    report = audit(db_session)
    assert repair(db_session, report) == 1

    assert keep.exists()
    assert not stray.exists()


def test_repair_leaves_rows_with_missing_files_alone(db_session, make_clip):
    """Deleting those rows would discard render history."""
    from backend.app.models import Clip

    make_clip(storage_path="storage/clips/vanished.mp4")
    report = audit(db_session)

    repair(db_session, report)
    assert db_session.query(Clip).count() == 1

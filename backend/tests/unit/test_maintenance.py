from backend.app.maintenance import audit, repair


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

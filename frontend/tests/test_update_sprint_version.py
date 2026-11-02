"""The release script that keeps the About page's sprint version current."""

import pytest

import update_sprint_version as script
from conftest import FakeConnection


CURRENT = {"metadata_id": 4, "sprint_version": "Sprint 7", "release_date": "2026-10-20"}


def run(args, row=CURRENT):
    conn = FakeConnection(fetchone_values=[row])
    code = script.main(args, connect=lambda: conn)
    return code, conn


def updates(conn):
    return [p for q, p in conn.cursor().executed if q.startswith("UPDATE Project_Metadata")]


def test_updates_the_row_the_about_page_shows(capsys):
    code, conn = run(["Sprint 8", "--release-date", "2026-11-14"])

    assert code == 0
    select = conn.cursor().executed[0][0]
    assert "ORDER BY metadata_id DESC" in select and "FOR UPDATE" in select
    assert updates(conn) == [("Sprint 8", "2026-11-14", 4)]
    assert conn.committed and conn.closed
    assert "Updated: Sprint 8" in capsys.readouterr().out


def test_release_date_defaults_to_today(monkeypatch):
    monkeypatch.setattr(script, "today", lambda: script.date(2026, 11, 2))
    _, conn = run(["Sprint 8"])
    assert updates(conn) == [("Sprint 8", "2026-11-02", 4)]


def test_dry_run_and_show_save_nothing(capsys):
    _, conn = run(["Sprint 8", "--dry-run"])
    assert updates(conn) == [] and not conn.committed
    assert "Nothing was saved" in capsys.readouterr().out

    _, conn = run(["--show"])
    assert updates(conn) == [] and not conn.committed
    assert "Sprint 7" in capsys.readouterr().out


def test_running_twice_is_harmless(capsys):
    _, conn = run(["Sprint 7", "--release-date", "2026-10-20"])
    assert updates(conn) == []
    assert "Already up to date" in capsys.readouterr().out


def test_missing_metadata_row_fails_loudly():
    code, conn = run(["Sprint 8"], row=None)
    assert code == 1
    assert updates(conn) == []


def test_version_is_tidied():
    _, conn = run(["  Sprint   8  ", "--release-date", "2026-11-14"])
    assert updates(conn)[0][0] == "Sprint 8"


@pytest.mark.parametrize("args", [
    [],
    ["   "],
    ["x" * (script.MAX_VERSION_LENGTH + 1)],
    ["Sprint 8", "--release-date", "11/14/2026"],
])
def test_bad_arguments_are_rejected_before_connecting(args):
    def connect():
        raise AssertionError("should not connect")
    with pytest.raises(SystemExit):
        script.main(args, connect=connect)


def test_failed_update_rolls_back():
    conn = FakeConnection(fetchone_values=[CURRENT])

    def boom(query, params=None):
        if query.lstrip().startswith("UPDATE"):
            raise RuntimeError("db down")
    conn.cursor().execute = boom

    with pytest.raises(RuntimeError):
        script.main(["Sprint 8", "--release-date", "2026-11-14"], connect=lambda: conn)
    assert conn.rolled_back and not conn.committed and conn.closed

"""scripts/cloud_job.sh against a fake bucket mount, in a scratch copy of the repo layout (never touches data/)."""
import json, os, pathlib, shutil, sqlite3, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPORT = ROOT / "tests" / "fixtures" / "export"

STUB = """#!/usr/bin/env python3
import os, shutil, sqlite3, sys
db = sqlite3.connect("data/tank.db"); db.execute("insert into t values ('collected today')"); db.commit()
mode = os.environ["STUB_MODE"]
if mode == "rebuild-fails":
    sys.exit(1)
shutil.copy(os.environ["REAL_EXPORT"] + "/assessment_snapshot.json", "data/export/")
if mode == "broken-export":
    open("data/export/price_daily.parquet", "wb").write(b"garbage")
else:
    shutil.copy(os.environ["REAL_EXPORT"] + "/price_daily.parquet", "data/export/")
open("data/raw/reddit_cursor.json", "w").write('{"hardware": 2}')
sys.exit(3 if mode == "collector-failed" else 0)
"""


def setup(tmp_path, seed=True):
    work, mount = tmp_path / "repo", tmp_path / "bucket"
    (work / "scripts").mkdir(parents=True)
    for f in ("cloud_job.sh", "check_export.py"):
        shutil.copy(ROOT / "scripts" / f, work / "scripts" / f)
    (work / "src").symlink_to(ROOT / "src")
    stub = work / "stub_cron.py"; stub.write_text(STUB); stub.chmod(0o755)
    (mount / "export").mkdir(parents=True); (mount / "raw").mkdir()
    (mount / "export" / "price_daily.parquet").write_bytes(b"previous")
    (mount / "raw" / "reddit_cursor.json").write_text('{"hardware": 1}')
    if seed:
        db = sqlite3.connect(mount / "tank.db"); db.execute("create table t (x)"); db.commit(); db.close()
    return work, mount


def run(work, mount, mode):
    path = f"{pathlib.Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"   # python3 = this interpreter
    env = {**os.environ, "PATH": path, "DATA_MOUNT": str(mount), "CRON": "./stub_cron.py", "STUB_MODE": mode, "REAL_EXPORT": str(EXPORT)}
    return subprocess.run(["bash", "scripts/cloud_job.sh"], cwd=work, env=env, capture_output=True, text=True)


def rows(mount):
    return sqlite3.connect(mount / "tank.db").execute("select count(*) from t").fetchone()[0]


def test_success_saves_tank_and_publishes_export(tmp_path):
    work, mount = setup(tmp_path)
    r = run(work, mount, "ok")
    assert r.returncode == 0, r.stderr
    assert rows(mount) == 1
    assert (mount / "export" / "price_daily.parquet").read_bytes() == (EXPORT / "price_daily.parquet").read_bytes()
    assert json.loads((mount / "raw" / "reddit_cursor.json").read_text()) == {"hardware": 2}


def test_failed_collector_still_publishes_but_fails_the_job(tmp_path):
    work, mount = setup(tmp_path)
    r = run(work, mount, "collector-failed")
    assert r.returncode == 3
    assert (mount / "export" / "assessment_snapshot.json").exists()


def test_failed_rebuild_keeps_collected_rows_and_old_export(tmp_path):
    work, mount = setup(tmp_path)
    r = run(work, mount, "rebuild-fails")
    assert r.returncode == 1
    assert rows(mount) == 1
    assert (mount / "export" / "price_daily.parquet").read_bytes() == b"previous"


def test_broken_export_is_not_published(tmp_path):
    work, mount = setup(tmp_path)
    r = run(work, mount, "broken-export")
    assert r.returncode == 1 and "export check failed" in r.stderr
    assert (mount / "export" / "price_daily.parquet").read_bytes() == b"previous"


def test_unseeded_bucket_stops_before_running(tmp_path):
    work, mount = setup(tmp_path, seed=False)
    r = run(work, mount, "ok")
    assert r.returncode == 1 and "seed the bucket" in r.stderr
    assert not (work / "data" / "tank.db").exists()

# 目的：研究檔與 SQLite 必須能一致備份及還原；拒絕活動程序、損壞與路徑逃逸。
# 邊界：只用 tmp_path；故障不覆寫來源／既有目的地，缺完整 manifest 的快照不可還原。
# 驗證：python -m pytest test/unit/test_research_backup.py -q；相同純檔案案例在 Linux 執行。
import hashlib
import json
import sqlite3
import pytest
from app.research_backup import (BackupError,acquire_runtime_lease,create_backup,restore_backup,verify_backup)


def seed(root):
    root.mkdir()
    for name in ('research.db','manuscript.db'):
        with sqlite3.connect(root/name) as db:
            db.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
            db.executemany('INSERT INTO records(body) VALUES(?)',[('作者決策',),('版本與教授回覆',)])
    files = {'SOLO-p/literature/papers/original/00_origins/source.pdf':b'%PDF-1.4\nSynthetic original',
             'SOLO-p/manuscript/citation_ledger.json':b'{"decisions":[{"author":"synthetic"}]}',
             'SOLO-p/manuscript/block/introduction/V0.1.json':b'{"content":"Original author thought"}',
             'SOLO-p/study/notes.json':b'{"human_decision":"keep conditions"}',
             'mentor_resources/review.pdf':b'%PDF-1.4\nSynthetic professor resource'}
    for name,content in files.items():
        path = root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
    return files


def test_backup_restore_covers_both_databases_original_files_and_decisions(tmp_path):
    source = tmp_path/'source'
    files = seed(source)
    (source/'SOLO-p/submit/empty').mkdir(parents=True)
    backup = tmp_path/'backup'
    receipt = create_backup(source,backup)
    assert receipt['complete'] is True and receipt['schema_version']==1
    assert len(receipt['databases'])==2
    assert verify_backup(backup)==receipt
    restored = tmp_path/'restored'
    restore_backup(backup,restored)
    assert (restored/'SOLO-p/submit/empty').is_dir()
    for name,content in files.items():
        assert (source/name).read_bytes()==(restored/name).read_bytes()==content
    for name in ('research.db','manuscript.db'):
        with sqlite3.connect(restored/name) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert db.execute('SELECT body FROM records ORDER BY id').fetchall()==[('作者決策',),('版本與教授回覆',)]


def test_active_application_lease_refuses_backup_without_publishing(tmp_path):
    source = tmp_path/'source'
    seed(source)
    lease = acquire_runtime_lease(source)
    try:
        with pytest.raises(BackupError):
            create_backup(source,tmp_path/'blocked')
        assert not (tmp_path/'blocked').exists()
    finally:
        lease.release()
    assert create_backup(source,tmp_path/'after-stop')['complete'] is True


def test_wal_committed_rows_are_in_a_self_contained_database_snapshot(tmp_path):
    source = tmp_path/'source'
    seed(source)
    connection = sqlite3.connect(source/'research.db')
    try:
        assert connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('INSERT INTO records(body) VALUES(?)',('尚在 WAL 的已提交作者決策',))
        connection.commit()
        backup = tmp_path/'wal-backup'
        manifest = create_backup(source,backup)
        assert not any(name.endswith(('-wal','-shm')) for name in manifest['files'])
        with sqlite3.connect(backup/'files/research.db') as restored:
            assert restored.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
            assert restored.execute('SELECT body FROM records WHERE id=3').fetchone()[0]=='尚在 WAL 的已提交作者決策'
    finally:
        connection.close()


@pytest.mark.parametrize('stage',['before_copy','after_database','before_manifest'])
def test_backup_failure_is_explicit_and_originals_remain_recoverable(tmp_path,stage):
    source = tmp_path/'source'
    files = seed(source)
    def fail(current,*_):
        if current==stage:
            raise OSError('Synthetic interrupted backup')
    with pytest.raises(BackupError):
        create_backup(source,tmp_path/'failed',fault=fail)
    assert not (tmp_path/'failed/manifest.json').exists()
    for name,content in files.items():
        assert (source/name).read_bytes()==content
    assert create_backup(source,tmp_path/'retry')['complete'] is True


def test_existing_destinations_and_nested_backup_are_never_overwritten(tmp_path):
    source = tmp_path/'source'
    seed(source)
    destination = tmp_path/'existing'
    destination.mkdir()
    sentinel = destination/'keep.txt'
    sentinel.write_text('Keep existing data',encoding='utf-8')
    with pytest.raises(BackupError):
        create_backup(source,destination)
    with pytest.raises(BackupError):
        create_backup(source,source/'nested')
    backup = tmp_path/'backup'
    create_backup(source,backup)
    with pytest.raises(BackupError):
        restore_backup(backup,destination)
    assert sentinel.read_text(encoding='utf-8')=='Keep existing data'


def test_modified_original_or_unlisted_file_in_backup_is_rejected_before_restore(tmp_path):
    source = tmp_path/'source'
    files = seed(source)
    backup = tmp_path/'backup'
    create_backup(source,backup)
    name = next(iter(files))
    original = (backup/'files'/name).read_bytes()
    (backup/'files'/name).write_bytes(original+b'TAMPERED')
    with pytest.raises(BackupError):
        restore_backup(backup,tmp_path/'tampered')
    assert not (tmp_path/'tampered').exists()
    (backup/'files'/name).write_bytes(original)
    (backup/'files/unlisted.txt').write_text('Unexpected payload',encoding='utf-8')
    with pytest.raises(BackupError):
        verify_backup(backup)


def test_manifest_traversal_cannot_write_outside_restore_root(tmp_path):
    source = tmp_path/'source'
    seed(source)
    backup = tmp_path/'backup'
    create_backup(source,backup)
    manifest_path = backup/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files']['../outside.txt'] = {'sha256':hashlib.sha256(b'evil').hexdigest(),'size':4}
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(BackupError):
        restore_backup(backup,tmp_path/'restored')
    assert not (tmp_path/'outside.txt').exists()


def test_restore_failure_never_publishes_complete_marker_or_changes_backup(tmp_path):
    source = tmp_path/'source'
    seed(source)
    backup = tmp_path/'backup'
    original = create_backup(source,backup)
    def fail(stage,*_):
        if stage=='before_restore_complete':
            raise OSError('Synthetic restore failure')
    with pytest.raises(BackupError):
        restore_backup(backup,tmp_path/'failed-restore',fault=fail)
    assert not (tmp_path/'failed-restore/.restore-complete.json').exists()
    assert verify_backup(backup)==original


def test_symlink_source_cannot_leak_outside_file(tmp_path):
    source = tmp_path/'source'
    seed(source)
    outside = tmp_path/'outside'
    outside.write_text('Synthetic private file',encoding='utf-8')
    try:
        (source/'escape').symlink_to(outside)
    except OSError:
        pytest.skip('此 Windows 帳號不允許建立符號連結；Linux 必須執行同一案例')
    with pytest.raises(BackupError):
        create_backup(source,tmp_path/'unsafe')


def test_incomplete_backup_and_database_corruption_are_not_accepted(tmp_path):
    source = tmp_path/'source'
    seed(source)
    bad = tmp_path/'incomplete'
    bad.mkdir()
    with pytest.raises(BackupError):
        restore_backup(bad,tmp_path/'restored')
    (source/'research.db').write_bytes(b'Not a valid database')
    with pytest.raises(BackupError):
        create_backup(source,tmp_path/'corrupt')


@pytest.mark.parametrize('mode',['application','backup'])
def test_cross_process_gate_and_process_death_recovery(tmp_path,mode):
    import subprocess,sys,time
    import app.research_backup as implementation
    source = tmp_path/'source'
    seed(source)
    ready = tmp_path/'ready'
    program = '''import importlib.util,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('backup_under_test',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
guard=m.acquire_runtime_lease(sys.argv[2]) if sys.argv[4]=='application' else m._offline(Path(sys.argv[2]))
if sys.argv[4]=='backup': guard.__enter__()
Path(sys.argv[3]).write_text('ready')
time.sleep(120)
'''
    process = subprocess.Popen([sys.executable,'-c',program,implementation.__file__,str(source),str(ready),mode],stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+25
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(0.05)
        assert ready.exists(), '合成工作程序沒有取得測試鎖'
        with pytest.raises(BackupError):
            create_backup(source,tmp_path/'blocked') if mode=='application' else acquire_runtime_lease(source)
    finally:
        process.kill()
        process.wait(timeout=15)
    assert create_backup(source,tmp_path/'after-crash')['complete'] is True


def test_hard_process_exit_during_backup_never_creates_a_trusted_snapshot(tmp_path):
    import subprocess,sys
    import app.research_backup as implementation
    source = tmp_path/'source'
    files = seed(source)
    destination = tmp_path/'interrupted'
    program = '''import importlib.util,sys,os
spec=importlib.util.spec_from_file_location('backup_under_test',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def die(stage,*_):
    if stage=='before_manifest': os._exit(77)
m.create_backup(sys.argv[2],sys.argv[3],fault=die)
'''
    result = subprocess.run([sys.executable,'-c',program,implementation.__file__,str(source),str(destination)],timeout=30)
    assert result.returncode==77
    with pytest.raises(BackupError):
        restore_backup(destination,tmp_path/'cannot-restore-partial')
    for name,data in files.items():
        assert (source/name).read_bytes()==data
    assert create_backup(source,tmp_path/'retry-after-crash')['complete'] is True


def test_external_database_cannot_be_silently_omitted_from_complete_backup(tmp_path):
    source = tmp_path/'source'
    seed(source)
    lease = acquire_runtime_lease(source,database_paths=[source/'research.db',tmp_path/'outside.db'])
    lease.release()
    with pytest.raises(BackupError):
        create_backup(source,tmp_path/'must-not-publish')
    assert not (tmp_path/'must-not-publish').exists()


def test_unreadable_research_directory_never_publishes_incomplete_backup(tmp_path,monkeypatch):
    import os
    from pathlib import Path
    source=tmp_path/'source'
    files=seed(source)
    scandir=os.scandir
    def denied(path):
        if Path(path)==source/'SOLO-p/study':
            raise PermissionError('Synthetic unreadable research directory')
        return scandir(path)
    monkeypatch.setattr(os,'scandir',denied)
    with pytest.raises(BackupError):
        create_backup(source,tmp_path/'must-not-be-complete')
    assert not (tmp_path/'must-not-be-complete/manifest.json').exists()
    for name,content in files.items():
        assert (source/name).read_bytes()==content

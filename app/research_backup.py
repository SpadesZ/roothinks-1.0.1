# 目的：研究資料根目錄的受控停機快照；涵蓋原件、JSON 歷史及同目錄 SQLite（含 WAL）。
# 上下游：app factory 持有 runtime lease；離線 CLI／測試 → lease gate → 快照、雜湊與全新目錄還原。
# 邊界：不讀 dotenv、不覆寫來源／既有目的地、不支援外部 DB 或多主機；備份本身是私密資料，不進 Git。
# 驗證：python -m pytest test/unit/test_research_backup.py -q；python app/research_backup.py --help。
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import sqlite3
from uuid import uuid4

from filelock import FileLock,Timeout

STATE = '.roothinks-runtime'
RESTORED = '.restore-complete.json'


class BackupError(RuntimeError):
    """無法證明完整性時明確失敗；來源及部分成果保留。"""


def _junction(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


def _state(root):
    path = root/STATE
    if _junction(path):
        raise BackupError('執行狀態目錄不可是符號連結或 junction。')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    return path


def acquire_runtime_lease(root,*,database_paths=None):
    """每個 app／worker 持有獨立 OS 鎖；註冊與備份共用 gate，支援單機多程序。"""
    root = Path(root).resolve()
    state = _state(root)
    try:
        with FileLock(str(state/'gate.lock'),timeout=0):
            if (state/'restore-pending.json').exists() and not (root/RESTORED).exists():
                raise BackupError('此目錄還原尚未完成；不可啟動或補建空資料庫。')
            lease = FileLock(str(state/f'lease-{uuid4().hex}.lock'),timeout=0,thread_local=False)
            lease.acquire()
            if database_paths is not None:
                paths = [Path(p).resolve() for p in database_paths]
                layout = dict(outside_root=any(not p.is_relative_to(root) for p in paths),
                    databases=[p.relative_to(root).as_posix() for p in paths if p.is_relative_to(root)])
                _publish_json(state/'storage.json',layout,replace=True)
            return lease
    except Timeout as exc:
        raise BackupError('資料正在備份或還原；應用程式尚未啟動。') from exc


@contextmanager
def _offline(root):
    state = _state(root)
    held = []
    try:
        with FileLock(str(state/'gate.lock'),timeout=0):
            try:
                for path in sorted(state.glob('lease-*.lock')):
                    lease = FileLock(str(path),timeout=0)
                    lease.acquire()
                    held.append(lease)
                yield
            finally:
                for lease in reversed(held):
                    lease.release()
    except Timeout as exc:
        raise BackupError('仍有應用程式／工作程序使用此資料；請完整停機後備份。') from exc


def _sync_dir(path):
    if os.name!='nt':
        fd = os.open(path,os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _publish_json(path,value,*,replace=False):
    temporary = path.parent/f'.manifest-{uuid4().hex}.tmp'
    with temporary.open('x',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,sort_keys=True,indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    if replace:
        os.replace(temporary,path)
    else:
        os.link(temporary,path)  # 不覆寫既有 marker；不支援 hard-link 時明確失敗。
        temporary.unlink()
    _sync_dir(path.parent)


def _relative(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise BackupError('備份包含不安全的相對路徑。')
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or any(p in {'..','.'} for p in path.parts) or str(path)!=name:
        raise BackupError('備份路徑逃出資料根目錄。')
    if path.parts[0] in {STATE,RESTORED} or path.name.startswith('.env') or path.name=='.flaskenv':
        raise BackupError('執行狀態或秘密設定檔不屬於研究快照。')
    return path


def _walk(root):
    if _junction(root):
        raise BackupError('快照內容根目錄不可是連結。')
    def unreadable(error):
        raise error
    directories,files = [],{}
    for current,names,leaves in os.walk(root,followlinks=False,onerror=unreadable):
        parent = Path(current)
        for name in list(names):
            path = parent/name
            if _junction(path):
                raise BackupError('研究快照不追蹤符號連結或 junction。')
            if parent==root and name==STATE:
                names.remove(name)
            else:
                relative = path.relative_to(root).as_posix()
                _relative(relative)
                directories.append(relative)
        for name in leaves:
            path = parent/name
            if parent==root and name==RESTORED:
                continue
            if _junction(path) or not path.is_file():
                raise BackupError('研究快照只支援一般檔案。')
            relative = path.relative_to(root).as_posix()
            _relative(relative)
            files[relative] = path
    all_names = directories+list(files)
    if len({p.casefold() for p in all_names})!=len(all_names):
        raise BackupError('路徑大小寫碰撞，不能保證跨平台完整還原。')
    return sorted(directories),files


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            digest.update(chunk)
    return dict(size=path.stat().st_size,sha256=digest.hexdigest())


def _connect(path):
    return sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)


def _database_state(path):
    with _connect(path) as connection:
        if connection.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise BackupError('SQLite 完整性檢查失敗。')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise BackupError('SQLite 有失去對應資料的外鍵。')
        tables = {}
        schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"'+name.replace('"','""')+'"'
            columns = len(connection.execute(f'PRAGMA table_info({quoted})').fetchall())
            digest,count = hashlib.sha256(),0
            for row in connection.execute(f'SELECT * FROM {quoted} ORDER BY '+','.join(str(i+1) for i in range(columns))):
                encoded = json.dumps(row,ensure_ascii=False,separators=(',',':'),default=lambda b:{'bytes':b.hex()}).encode()
                digest.update(len(encoded).to_bytes(8,'big')+encoded)
                count += 1
            tables[name] = dict(rows=count,sha256=digest.hexdigest())
        return dict(schema_sha256=hashlib.sha256(json.dumps(schema,ensure_ascii=False).encode()).hexdigest(),tables=tables)


def _copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    with source.open('rb') as incoming,target.open('xb') as outgoing:
        for chunk in iter(lambda:incoming.read(1024*1024),b''):
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def _fault(fault,stage,path):
    if fault:
        fault(stage,path)


def _new_destination(source,target):
    if _junction(Path(target)):
        raise BackupError('目的地不可是連結。')
    source,target = Path(source).resolve(),Path(target).resolve()
    if not source.is_dir() or source==target or source in target.parents or target in source.parents:
        raise BackupError('來源及全新目的地必須是互不包含的目錄。')
    if target.exists():
        raise BackupError('目的地已存在；不覆寫或合併任何資料。')
    return source,target


def create_backup(source,destination,*,fault=None):
    try:
        source,destination = _new_destination(source,destination)
        with _offline(source):
            if (source/STATE/'restore-pending.json').exists() and not (source/RESTORED).exists():
                raise BackupError('來源是尚未完成的還原，不可備份為正常研究資料。')
            layout_path = source/STATE/'storage.json'
            layout = json.loads(layout_path.read_text(encoding='utf-8')) if layout_path.exists() else {}
            if layout.get('outside_root'):
                raise BackupError('有研究資料庫位於資料根目錄外，本快照不能證明一致性。')
            directories,files = _walk(source)
            databases = set()
            for name,path in files.items():
                with path.open('rb') as handle:
                    if path.suffix.lower() in {'.db','.sqlite','.sqlite3'} or handle.read(16)==b'SQLite format 3\x00':
                        databases.add(name)
            if not databases or any(name not in databases for name in layout.get('databases',[])):
                raise BackupError('研究資料庫缺少，不能發布完整快照。')
            sidecars = {name+suffix for name in databases for suffix in ('-wal','-shm','-journal')}
            if any(name.endswith(('-wal','-shm','-journal')) and name not in sidecars for name in files):
                raise BackupError('有無法對應主資料庫的 SQLite sidecar。')
            destination.mkdir(mode=0o700)
            payload = destination/'files'
            payload.mkdir()
            for name in directories:
                (payload/name).mkdir(parents=True,exist_ok=True)
            manifest = dict(schema_version=1,complete=True,created_at=datetime.now(timezone.utc).isoformat(),
                scope='offline_research_root_with_embedded_sqlite',directories=directories,files={},databases={})
            original_files = {name:_hash(path) for name,path in files.items() if name not in databases|sidecars}
            for name,path in sorted(files.items()):
                if name in sidecars:
                    continue
                _fault(fault,'before_copy',name)
                target = payload/name
                target.parent.mkdir(parents=True,exist_ok=True)
                if name in databases:
                    if target.exists():
                        raise BackupError('目的資料庫已存在。')
                    with _connect(path) as incoming,sqlite3.connect(target) as outgoing:
                        incoming.backup(outgoing)
                        if outgoing.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
                            raise BackupError('SQLite 快照未能獨立保存 WAL 中的已提交資料。')
                    with target.open('r+b') as handle:
                        os.fsync(handle.fileno())
                    manifest['databases'][name] = _database_state(target)
                    _fault(fault,'after_database',name)
                else:
                    _copy(path,target)
                manifest['files'][name] = _hash(target)
            if _walk(source)[0]!=directories or set(_walk(source)[1])-sidecars!=set(files)-sidecars:
                raise BackupError('備份期間來源目錄變動；未發布。')
            if any(_hash(source/name)!=value for name,value in original_files.items()) or any(
                _database_state(source/name)!=value for name,value in manifest['databases'].items()):
                raise BackupError('備份期間來源資料變動；未發布。')
            _fault(fault,'before_manifest',destination)
            for directory,_,_ in os.walk(payload,topdown=False):
                _sync_dir(directory)
            _publish_json(destination/'manifest.json',manifest)
            verify_backup(destination)
            return manifest
    except BackupError:
        raise
    except (OSError,ValueError,sqlite3.Error) as exc:
        raise BackupError(f'備份未完成（{type(exc).__name__}）；來源不變，部分成果保留且不可當作完整備份。') from exc


def verify_backup(backup):
    try:
        backup = Path(backup).resolve()
        if _junction(backup/'manifest.json'):
            raise BackupError('Manifest 不可為連結。')
        manifest = json.loads((backup/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('schema_version')!=1 or manifest.get('complete') is not True:
            raise BackupError('快照沒有完成標記或格式不支援。')
        directories,files = _walk(backup/'files')
        for name in manifest['directories']+list(manifest['files']):
            _relative(name)
        if directories!=manifest['directories'] or set(files)!=set(manifest['files']):
            raise BackupError('Manifest 與實際檔案／目錄不一致。')
        if any(_hash(path)!=manifest['files'][name] for name,path in files.items()):
            raise BackupError('檔案雜湊或長度不符。')
        if not manifest['databases'] or any(name not in files or _database_state(files[name])!=state for name,state in manifest['databases'].items()):
            raise BackupError('資料庫結構、逐表內容或完整性不符。')
        return manifest
    except BackupError:
        raise
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        raise BackupError('快照缺少、損壞或無法驗證；不可還原。') from exc


def restore_backup(backup,destination,*,fault=None):
    try:
        backup,destination = _new_destination(backup,destination)
        original_manifest_hash = _hash(backup/'manifest.json')
        manifest = verify_backup(backup)
        destination.mkdir(mode=0o700)
        _publish_json(_state(destination)/'restore-pending.json',dict(complete=False))
        # 新目錄先持有 lease；完整性通過前，配合此版本的 app 不能啟動。
        with _offline(destination):
            for name in manifest['directories']:
                (destination/name).mkdir(parents=True,exist_ok=True)
            for name,state in manifest['files'].items():
                _copy(backup/'files'/name,destination/name)
                if _hash(destination/name)!=state:
                    raise BackupError('還原檔案指紋不符。')
            for name,state in manifest['databases'].items():
                if _database_state(destination/name)!=state:
                    raise BackupError('還原資料庫內容或完整性不符。')
            _fault(fault,'before_restore_complete',destination)
            if _hash(backup/'manifest.json')!=original_manifest_hash:
                raise BackupError('還原期間備份 manifest 已變動。')
            for directory,_,_ in os.walk(destination,topdown=False):
                _sync_dir(directory)
            _publish_json(destination/RESTORED,dict(complete=True,manifest_sha256=original_manifest_hash['sha256']))
        return manifest
    except BackupError:
        raise
    except (OSError,ValueError,sqlite3.Error) as exc:
        raise BackupError(f'還原未完成（{type(exc).__name__}）；原備份與既有資料不變。') from exc


def main():
    parser = argparse.ArgumentParser(description='RootThinks 受控停機備份／驗證／全新目錄還原；不讀取 dotenv。')
    parser.add_argument('action',choices=['backup','verify','restore'])
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path,nargs='?')
    args = parser.parse_args()
    try:
        if args.action=='verify':
            result = verify_backup(args.source)
        elif args.destination is None:
            parser.error('backup／restore 需要全新目的地')
        else:
            result = create_backup(args.source,args.destination) if args.action=='backup' else restore_backup(args.source,args.destination)
        print(json.dumps(dict(complete=True,files=len(result['files']),databases=len(result['databases'])),ensure_ascii=False))
    except BackupError as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':
    main()

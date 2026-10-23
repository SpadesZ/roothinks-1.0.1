# Roothinks source maintenance contract
# 目的：以獨立 OS 程序驗共享鎖、CAS 與寫入前程序死亡的可恢復性；僅使用 tmp。
# 驗證：python -m pytest test/unit/test_sai_process_revision.py -q
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def worker(spec):
    root = Path(spec['root'])
    os.environ['LOCK_ROOT'] = str(root/'locks')
    from app.core_pro.manuscript import manuscript_io as mio
    from app.security import check_and_save_json
    from app.services.literature_library import LiteratureLibrary
    from werkzeug.exceptions import Conflict
    mio._get_data_root = lambda: str(root)
    (root/f"ready-{spec['slot']}").write_text(str(os.getpid()))
    deadline = time.monotonic()+45
    while not (root/'go').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('parent barrier')
        time.sleep(.02)
    results = []
    for rev in range(3):
        marker = root/f"round-{rev}-{spec['slot']}"
        marker.touch()
        while len(list(root.glob(f'round-{rev}-*'))) != spec['writers']:
            if time.monotonic() > deadline:
                raise TimeoutError('round barrier')
            time.sleep(.02)
        try:
            if spec['kind'] == 'notes':
                result = check_and_save_json(str(root/'notes.json'), {'content':str(os.getpid())}, rev,
                                             updated_by=str(os.getpid()), history_dir=str(root/'history'))
            elif spec['kind'] == 'draft':
                result = mio.ManuscriptIO.save_draft('PROC-p','introduction','Synthetic',str(os.getpid()),
                                                      owner_key=17,base_rev=rev)
            else:
                result = LiteratureLibrary(str(root)).update_entry('PROC-p',spec['entry'],
                    {'reading_note':str(os.getpid())},actor=str(os.getpid()),base_rev=rev)
                result = {'ok':True,'rev':result['_rev']}
        except Conflict:
            result = {'ok':False,'conflict':True}
        results.append(result)
        (root/f"done-{rev}-{spec['slot']}").touch()
        while len(list(root.glob(f'done-{rev}-*'))) != spec['writers']:
            if time.monotonic() > deadline:
                raise TimeoutError('completion barrier')
            time.sleep(.02)
    (root/f"result-{spec['slot']}.json").write_text(json.dumps({'pid':os.getpid(),'results':results}))


@pytest.mark.parametrize('kind',['notes','draft','library'])
def test_multiple_process_writers_have_one_winner_per_revision(tmp_path,monkeypatch,kind):
    from app.services.literature_library import LiteratureLibrary
    monkeypatch.setenv('LOCK_ROOT',str(tmp_path/'locks'))
    lib = LiteratureLibrary(str(tmp_path))
    lib.merge_candidates('PROC-p',[{'title':'Synthetic'}])
    entry = next(iter(lib.load('PROC-p')['entries']))
    writers = 6
    children = []
    try:
        for slot in range(writers):
            spec = dict(root=str(tmp_path),slot=slot,kind=kind,entry=entry,writers=writers)
            children.append(subprocess.Popen([sys.executable,str(Path(__file__).resolve()),json.dumps(spec)],
                cwd=str(Path(__file__).resolve().parents[2]),stdout=subprocess.PIPE,stderr=subprocess.PIPE))
        deadline = time.monotonic()+45
        while len(list(tmp_path.glob('ready-*'))) != writers:
            assert time.monotonic() < deadline, 'workers failed to reach barrier'
            time.sleep(.03)
        (tmp_path/'go').touch()
        for child in children:
            out,err = child.communicate(timeout=60)
            (tmp_path/f'worker-{child.pid}.txt').write_bytes(out+b'\n'+err)
        assert all(child.returncode == 0 for child in children), str(tmp_path)
        outcomes = [json.loads(p.read_text()) for p in tmp_path.glob('result-*.json')]
        assert len({r['pid'] for r in outcomes}) == writers
        for rev in range(3):
            rows = [r['results'][rev] for r in outcomes]
            assert sum(bool(r['ok']) for r in rows) == 1
            assert sum(bool(r.get('conflict')) for r in rows) == writers-1
            assert next(r['rev'] for r in rows if r['ok']) == rev+1
        if kind == 'notes':
            assert json.loads((tmp_path/'notes.json').read_text())['_rev'] == 3
            assert {p.stem for p in (tmp_path/'history').glob('*.json')} == {'1','2'}
        elif kind == 'draft':
            from app.core_pro.manuscript import manuscript_io as mio
            monkeypatch.setattr(mio,'_get_data_root',lambda:str(tmp_path))
            assert mio.ManuscriptIO.draft_revision('PROC-p','introduction',owner_key=17) == 3
            assert mio.ManuscriptIO.save_draft('PROC-p','introduction','Synthetic','other owner',owner_key=18,base_rev=0)['ok']
        else:
            row = lib.load('PROC-p')['entries'][entry]
            assert row['_rev'] == 3 and len(row['history']) == 3
            assert row['last_edited_by'] in {str(r['pid']) for r in outcomes}
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()


def test_process_death_before_atomic_publish_preserves_revision_and_releases_lock(tmp_path,monkeypatch):
    from app.security import check_and_save_json
    monkeypatch.setenv('LOCK_ROOT',str(tmp_path/'locks'))
    target = tmp_path/'notes.json'
    history = tmp_path/'history'
    assert check_and_save_json(str(target),{'content':'original'},0,history_dir=str(history))['ok']
    original = target.read_bytes()
    code = '''import os,sys
from app import security
target,history=sys.argv[1:]
atomic=security._write_json_atomic
def crash(path,obj):
    if path==target: os._exit(79)
    return atomic(path,obj)
security._write_json_atomic=crash
security.check_and_save_json(target,{'content':'incomplete'},1,history_dir=history)
'''
    result = subprocess.run([sys.executable,'-c',code,str(target),str(history)],timeout=40)
    assert result.returncode == 79
    assert target.read_bytes() == original
    assert json.loads((history/'1.json').read_text())['content'] == 'original'
    assert check_and_save_json(str(target),{'content':'recovered'},1,history_dir=str(history))['rev'] == 2
    assert json.loads(target.read_text())['content'] == 'recovered'


if __name__ == '__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    worker(json.loads(sys.argv[1]))


@pytest.mark.parametrize('permanent',[False,True])
def test_windows_replace_denial_is_bounded_and_preserves_old_file(tmp_path,monkeypatch,permanent):
    from app import security
    target = tmp_path/'canonical.json'
    target.write_text('{"content":"original"}')
    replace = security.os.replace
    calls = []
    def denied(src,dst):
        calls.append(dst)
        if permanent or len(calls) < 3:
            exc = PermissionError('synthetic Windows sharing denial')
            exc.winerror = 5
            raise exc
        return replace(src,dst)
    monkeypatch.setattr(security.os,'replace',denied)
    monkeypatch.setattr(security.time,'sleep',lambda _:None)
    if permanent:
        with pytest.raises(PermissionError):
            security._write_json_atomic(str(target),{'content':'new'})
        assert len(calls) == 6
        assert json.loads(target.read_text())['content'] == 'original'
    else:
        security._write_json_atomic(str(target),{'content':'new'})
        assert len(calls) == 3 and json.loads(target.read_text())['content'] == 'new'
    assert list(tmp_path.iterdir()) == [target]

"""證據檔必須跨平台位元一致，否則獨立稽核在別的作業系統上重算不出同一個 hash。

本輪實測：I09 記錄的 inputs／gold／morph 引擎 hash 在 Linux 上重算全部不符，
把 LF 轉成 CRLF 後才逐一相符；凍結引擎副本因為用 write_bytes 而原本就相符。
驗證：python -m pytest test/unit/test_evidence_reproducibility.py -q
"""
import importlib.util
import sys
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'


def load(name):
    if str(SCRIPTS) not in sys.path:sys.path.insert(0,str(SCRIPTS))
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


bench=load('scientific_benchmark')


def test_written_evidence_rows_use_lf_on_every_platform(tmp_path):
    path=tmp_path/'rows.jsonl'
    bench.write(path,[dict(id=1,note='甲'),dict(id=2,note='乙')])
    raw=path.read_bytes()
    assert b'\r' not in raw,'行尾隨平台改變會讓記錄的 hash 無法被獨立重算'
    assert raw.endswith(b'\n') and raw.count(b'\n')==2


def test_experiment_engine_copies_are_byte_derived_from_the_frozen_source(tmp_path):
    diagnose=load('scientific_diagnose')
    baseline=bench.ENGINE
    inputs=tmp_path/'inputs.jsonl';gold=tmp_path/'gold.jsonl'
    claim='Treatment X increases survival in mice.'
    bench.write(inputs,[dict(id=1,claim=claim,sources=[dict(source_id='7',text=claim,
        locator={'doc_id':7},sentences=[dict(index=0,start=0,end=len(claim))])])])
    bench.write(gold,[dict(id=1,evidence={'7':[dict(label='SUPPORT',sentences=[0])]})])
    output=tmp_path/'out'
    diagnose.diagnose(baseline,inputs,gold,output)
    for name,source in (('I08',baseline),('I09',bench.ENGINE)):
        frozen=(output/(name+'-engine.py')).read_bytes()
        assert frozen==Path(source).read_bytes(),'凍結副本必須與原始碼位元相同'
        variant=(output/(name+'-morph-engine.py')).read_bytes()
        assert variant==frozen+diagnose.MORPH.encode('utf-8'),'實驗引擎必須由位元串接而成，不經平台行尾轉換'
        assert b'\r' not in variant

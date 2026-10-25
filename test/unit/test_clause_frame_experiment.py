"""I13 Gate A 對抗控制：子句範圍關係框架（實驗，不整合）。

依 docs/engineering/I13_GATE_A_PROTOCOL.md 在實作前寫定。這些是機械合成案例，
不是獨立科學 gold。NLTK 詞性與 VerbNet 資料不可用時相關案例略過，
另有專門案例驗證退回凍結基線。
同詞根 block 案例的 strict xfail 是在看到它失敗之後才標上的；預期結果沒有改，
失敗原因是詞性標註錯誤，屬於候選方法本身的缺陷（I13 Gate A 負結果的一部分）。
驗證：NLTK_DATA=<dir> python -m pytest test/unit/test_clause_frame_experiment.py -q
"""
import hashlib
import importlib.util
from pathlib import Path
import pytest
from app.core_pro.manuscript import scientific_advisory as product

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
spec=importlib.util.spec_from_file_location('clause_frame_experiment',SCRIPTS/'clause_frame_experiment.py')
lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)
FROZEN='4c2034bf8fdbf2ad49276d136a0d37c6201240bd2570bffca5b1fbc0f5f7bd82'


@pytest.fixture(scope='module',params=['C1','C2'])
def engine(request,tmp_path_factory):
    path=lab.engine(tmp_path_factory.mktemp(request.param)/'engine.py',variant=request.param)
    module=lab.load(path)
    if module._i13_resources() is None:pytest.skip('NLTK 詞性或 VerbNet 資料不可用')
    return module


def rel(engine,claim,quote):return engine.assess_relation(claim,quote)


def test_product_engine_is_still_the_frozen_baseline():
    assert hashlib.sha256(Path(product.__file__).read_bytes()).hexdigest()==FROZEN
    assert product.ENGINE_VERSION=='advisory-rules-4' and not hasattr(product,'_i13_resources')


SPLIT='Treatment X increases survival, whereas drug Z decreases weight.'


@pytest.mark.parametrize('claim,quote,expected',[
    # 子句範圍正確：同子句的主客體可比較。
    ('Treatment X decreases survival.',SPLIT,'possible_contradiction'),
    ('Treatment X increases survival.',SPLIT,'text_aligned'),
    # 跨子句不得拼接：Z 在第二子句、survival 在第一子句。
    ('Drug Z decreases survival.',SPLIT,'unknown'),
    # 仿案 837：關係子句與 and 之後無主詞的子句不得拼出 frame。
    ('Protein A reduced protein E.','Protein A is expressed in tissue B, where interference abrogated protein D and reduced protein E.','unknown'),
    # 子句內被動，by 施事者。
    ('Treatment X increased survival.','Survival was increased by treatment X, whereas weight was unchanged.','text_aligned'),
    # 同原形、非關係族動詞：只有同一動詞可比。
    # 已確認的候選失敗（量測後才加標記，預期未改）：NLTK tagger 把短句的 blocks 標成 NNS、
    # receptor 標成 VBP，主張側沒有謂詞。strict=True：若日後意外通過會報錯，紀錄不會悄悄失真。
    pytest.param('Drug X blocks receptor Y.','Drug X blocked receptor Y, whereas drug Z induced apoptosis.','text_aligned',
        marks=pytest.mark.xfail(strict=True,reason='I13 已確認：perceptron tagger 將 blocks 標為 NNS、receptor 標為 VBP')),
    # 輕動詞排除：案 674 仍不解。
    ('LDL cholesterol has no involvement in cardiovascular disease.','LDL cholesterol has a causal role in the development of cardiovascular disease.','unknown'),
])
def test_clause_scope(engine,claim,quote,expected):
    assert rel(engine,claim,quote)['relation']==expected


@pytest.mark.parametrize('claim,quote,expected',[
    # 否定對立：子句內「did not increase」對「increased」。
    ('Treatment X increased survival.','Treatment X did not increase survival, although drug Z increased weight.','possible_contradiction'),
    # 否定某方向不等於證實相反方向。
    ('Treatment X decreased survival.','Treatment X did not increase survival, although drug Z increased weight.','unknown'),
    # 同原形的否定對立。
    ('Drug X does not block receptor Y.','Drug X blocked receptor Y, whereas drug Z induced apoptosis.','possible_contradiction'),
])
def test_negation_controls(engine,claim,quote,expected):
    assert rel(engine,claim,quote)['relation']==expected


@pytest.mark.parametrize('claim,quote,reason',[
    ('Treatment X increases survival.','Treatment X may increase survival, whereas drug Z decreases weight.','source_hedged'),
    ('Protein X causes disease Y.','No evidence shows that protein X causes disease Y, whereas protein Z inhibits W.','source_does_not_establish_claim'),
    ('Treatment X increases survival.','Treatment X did not significantly increase survival, whereas drug Z decreased weight.','significance_is_not_direction'),
    ('Treatment X increased survival by 20%.','Survival was increased by 35% by treatment X, whereas weight was unchanged.','different_quantities'),
])
def test_existing_conservative_gates_are_never_overridden(engine,claim,quote,reason):
    judged=rel(engine,claim,quote)
    assert judged['relation']=='unknown' and judged['reason']==reason


def claim_row(engine,claim,quote):
    return engine.assess_claims(claim,[dict(source_id='synthetic',text=quote)])['claims'][0]


def test_condition_controls_keep_the_claim_unknown(engine):
    row=claim_row(engine,'Treatment X increases survival.',
        'At 300 K, treatment X increases survival, whereas drug Z decreases weight.')
    assert row['relation']=='unknown'
    assert 'missing_condition' in {f['code'] for f in row['findings']}
    row=claim_row(engine,'Treatment X increased survival in mice.',
        'Survival was increased by treatment X in rats, whereas weight was unchanged.')
    assert row['relation']=='unknown'


def test_researcher_autonomy_is_unchanged(engine):
    claim='Treatment X decreases survival.'
    report=engine.assess_claims(claim,[dict(source_id='synthetic',text=SPLIT)])
    assert report['original_text']==claim
    assert report['blocks_writing'] is False and report['scientific_proof'] is False
    assert report['advisory_only'] is True


def test_falls_back_to_the_frozen_baseline_without_nltk_data(engine,monkeypatch):
    monkeypatch.setitem(engine._I13_STATE,'ok',None)
    judged=rel(engine,'Treatment X decreases survival.',SPLIT)
    assert judged==product.assess_relation('Treatment X decreases survival.',SPLIT)


def test_experiment_engine_is_byte_derived_from_the_frozen_engine(tmp_path):
    built=lab.engine(tmp_path/'e.py',variant='C1').read_bytes()
    assert built.startswith(Path(product.__file__).read_bytes()) and b'\r' not in built

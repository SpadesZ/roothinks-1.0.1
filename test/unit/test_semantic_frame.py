"""I11 語意實驗的對抗控制。**該實驗未採用**，產品引擎必須維持凍結基線行為。

這些是機械合成案例，不是獨立科學 gold。WordNet 為選用相依；資料不可用時
相關案例略過，另有專門案例驗證退回基線行為。
驗證：python -m pytest test/unit/test_semantic_frame.py -q
"""
import hashlib
import importlib.util
from pathlib import Path
import pytest
from app.core_pro.manuscript import scientific_advisory as product

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
spec=importlib.util.spec_from_file_location('semantic_frame_experiment',SCRIPTS/'semantic_frame_experiment.py')
lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)

FROZEN_ENGINE_SHA256='4c2034bf8fdbf2ad49276d136a0d37c6201240bd2570bffca5b1fbc0f5f7bd82'


@pytest.fixture(scope='module')
def experiment(tmp_path_factory):
    return lab.load(lab.engine(tmp_path_factory.mktemp('semantic')/'experiment-engine.py'))


def wordnet_missing(module):
    return module.wordnet() is None


def source(text):return dict(source_id='synthetic',text=text)


def test_product_engine_stays_at_the_frozen_baseline():
    # I11_PROTOCOL.md 的效果門檻未達標，實驗不得進產品。
    digest=hashlib.sha256(Path(product.__file__).read_bytes()).hexdigest()
    assert digest==FROZEN_ENGINE_SHA256,'產品引擎已被改動；未採用的實驗不得進產品'
    assert product.ENGINE_VERSION=='advisory-rules-4'
    assert not hasattr(product,'wordnet') and not hasattr(product,'_semantic_frame')
    # 名詞化改述在產品裡仍然棄權，這是刻意的。
    judged=product.assess_relation('Treatment X reduces survival.',
        'Reduction of survival by treatment X was observed.')
    assert judged['relation']=='unknown' and judged['reason']=='unsupported_sentence_structure'


@pytest.mark.parametrize('claim,quote',[
    ('Treatment X reduces survival.','Reduction of survival by treatment X was observed.'),
    ('Treatment X increases survival.','Survival was increased by treatment X.'),
    ('Protein X inhibits protein Y.','Inhibition of protein Y by protein X was measured.'),
    ('Treatment X improves outcome.','Outcome was improved by treatment X.'),
])
def test_nominalized_and_passive_restatements_align(experiment,claim,quote):
    if wordnet_missing(experiment):pytest.skip('WordNet 資料不可用')
    assert experiment.assess_relation(claim,quote)['relation']=='text_aligned'


@pytest.mark.parametrize('claim,quote',[
    ('Treatment X reduces survival.','Reduction of treatment X by survival was observed.'),
    ('Treatment X increases survival.','Treatment X was increased by survival.'),
    ('Treatment X increases survival.','Survival was increased by treatment Z.'),
    ('Treatment X increases survival in mice.','Survival was increased by treatment X in rats.'),
])
def test_restored_arguments_must_still_match_in_order(experiment,claim,quote):
    if wordnet_missing(experiment):pytest.skip('WordNet 資料不可用')
    assert experiment.assess_relation(claim,quote)['relation']=='unknown'


@pytest.mark.parametrize('claim,quote',[
    ('Treatment X increases survival.','Survival was decreased by treatment X.'),
    ('Treatment X reduces survival.','Increase of survival by treatment X was observed.'),
])
def test_argument_restoration_never_hides_a_real_direction_conflict(experiment,claim,quote):
    if wordnet_missing(experiment):pytest.skip('WordNet 資料不可用')
    assert experiment.assess_relation(claim,quote)['relation']=='possible_contradiction'


def test_passive_without_an_agent_cannot_invent_a_subject(experiment):
    if wordnet_missing(experiment):pytest.skip('WordNet 資料不可用')
    judged=experiment.assess_relation('Treatment X increases survival.','Survival was increased.')
    assert judged['relation']=='unknown'


def test_nominalization_does_not_cross_the_relation_family(experiment):
    if wordnet_missing(experiment):pytest.skip('WordNet 資料不可用')
    report=experiment.assess_claims('Protein X causes disease Y.',
        [source('Protein X showed an association with disease Y.')])
    row=report['claims'][0]
    assert row['relation']=='unknown'
    assert 'causality_not_established' in {f['code'] for f in row['findings']}


def test_adjectival_forms_without_a_wordnet_verb_link_stay_unparsed(experiment):
    # causal／role 在 WordNet 沒有到 cause 的派生連結；本輪不為單一案例硬加詞條。
    if wordnet_missing(experiment):pytest.skip('WordNet 資料不可用')
    judged=experiment.assess_relation('LDL cholesterol has no involvement in cardiovascular disease.',
        'LDL cholesterol has a causal role in the development of cardiovascular disease.')
    assert judged['relation']=='unknown'


def test_experiment_degrades_to_the_frozen_baseline_without_wordnet(experiment,monkeypatch):
    monkeypatch.setattr(experiment,'_WORDNET',False)
    assert experiment.wordnet() is None
    # 基線本來就解析得出的句子行為不變。
    assert experiment.assess_relation('Treatment X increases survival in mice.',
        'Treatment X decreases survival in mice.')['relation']=='possible_contradiction'
    # 基線解析不出的句子仍然棄權，不得因為少了 WordNet 就改判。
    judged=experiment.assess_relation('Treatment X reduces survival.',
        'Reduction of survival by treatment X was observed.')
    assert judged['relation']=='unknown' and judged['reason']=='unsupported_sentence_structure'


def test_experiment_still_never_blocks_the_researcher(experiment):
    claim='Treatment X reduces survival.'
    report=experiment.assess_claims(claim,[source('Reduction of survival by treatment X was observed.')])
    assert report['original_text']==claim
    assert report['blocks_writing'] is False and report['scientific_proof'] is False
    assert report['advisory_only'] is True


def test_experiment_engine_is_byte_derived_from_the_frozen_product_engine(tmp_path):
    built=lab.engine(tmp_path/'engine.py')
    frozen=Path(product.__file__).read_bytes()
    assert built.read_bytes().startswith(frozen),'實驗引擎必須由凍結引擎位元串接而成'
    assert b'\r' not in built.read_bytes()

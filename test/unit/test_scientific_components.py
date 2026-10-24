"""I08 固定對抗控制；是作者設定的機械案例，不是專家金標。"""
import pytest
from app.core_pro.manuscript import scientific_advisory as advisory


@pytest.mark.parametrize('claim,quote,expected',[
    ('Treatment X increases survival in mice.','Treatment X increased survival in mice.','text_aligned'),
    ('Protein X activates protein Y.','Protein Y activates protein X.','unknown'),
    ('Treatment X reduced mortality in mice.','Treatment X did not reduce mortality in mice.','possible_contradiction'),
    ('Treatment X increases survival.','Treatment X may increase survival.','unknown'),
    ('Treatment X increases survival.','We hypothesize that treatment X increases survival.','unknown'),
    ('Treatment X increases survival.','Treatment X did not significantly increase survival.','unknown'),
    ('Protein X causes disease Y.','Protein X is associated with disease Y.','unknown'),
    ('Drug X increased survival.','Drug Y increased survival.','unknown'),
    ('Treatment X increases survival.','Treatment X did not decrease survival.','unknown'),
    ('Treatment X did not increase survival.','Treatment X did not decrease survival.','unknown'),
])
def test_relation_component_keeps_arguments_scope_hedges_and_uncertainty(claim,quote,expected):
    relation=advisory.assess_relation(claim,quote)
    assert relation['relation']==expected,relation
    assert relation['reason'] and relation['scientific_proof'] is False


def test_retrieval_is_separate_and_preserves_original_candidates():
    candidates=[dict(quote=q,start=0,end=len(q),sentence_index=0,source={'text':q,'source_id':str(i)})
        for i,q in enumerate(['Unrelated patient survival.','The silicon waveguide loss was measured.','Waveguide fabrication is discussed.'])]
    before=[dict(c) for c in candidates]
    ranked=advisory.rank_candidates('Silicon waveguide loss',candidates,method='bm25')
    assert ranked[0]['source']['source_id']=='1'
    assert candidates==before
    assert advisory.rank_candidates('Silicon waveguide loss',candidates,method='jaccard')[0]['source']['source_id']=='1'


@pytest.mark.parametrize('assessment',['compositional','advisory'])
def test_supporting_and_contradicting_evidence_are_both_visible(assessment):
    text='Treatment X increases survival in mice.'
    sources=[dict(text=text,source_id='support'),dict(text='Treatment X decreases survival in mice.',source_id='against')]
    report=advisory.assess_claims(text,sources,retrieval='bm25',assessment=assessment)
    assert report['claims'][0]['relation']=='mixed_evidence'
    assert {c['relation'] for c in report['claims'][0]['comparisons']}=={'text_aligned','possible_contradiction'}
    assert report['original_text']==text and report['blocks_writing'] is False


@pytest.mark.parametrize('claim,source,expected',[
    ('Device B was experimentally measured at 1550 nm.','Device B was simulated at 1550 nm. No physical device was fabricated.','modality_mismatch'),
    ('All samples improved by 3 dB.','Only sample A improved by 3 dB at 300 K.','unsupported_generalization'),
    ('Device B was simulated at 1600 nm with loss of 0.2 dB.','Device B was simulated at 1550 nm with loss of 0.1 dB.','numeric_mismatch'),
    ('A hypothesis without a citation.','', 'no_evidence'),
])
def test_scientific_warnings_remain_nonblocking(claim,source,expected):
    report=advisory.assess_claims(claim,[dict(text=source,source_id='selected')] if source else [],retrieval='bm25',assessment='compositional')
    assert expected in {f['code'] for c in report['claims'] for f in c['findings']}
    assert report['original_text']==claim and report['blocks_writing'] is False and report['scientific_proof'] is False


def test_sentence_local_conditions_do_not_import_another_experiment():
    claim='Treatment X increases survival in mice.'
    report=advisory.assess_claims(claim,[dict(text=claim+' An unrelated device experiment operated at 77 K.',source_id='one')],retrieval='bm25',assessment='compositional')
    assert '77 K' not in report['claims'][0]['comparisons'][0]['applicable_conditions']
    assert report['claims'][0]['relation']=='text_aligned'

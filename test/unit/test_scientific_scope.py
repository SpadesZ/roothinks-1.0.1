"""I09 合成反例；不是獨立科學 gold。"""
import pytest
from app.core_pro.manuscript import scientific_advisory as advisory


CASES=[
    ('Treatment X increases survival at 300 K.','Treatment X decreases survival at 77 K.'),
    ('Drug X increases survival.','Drug Y decreases survival.'),
    ('Protein X activates protein Y.','Protein Y does not activate protein X.'),
    ('Treatment X increases survival.','Treatment X may decrease survival.'),
    ('Treatment X increases survival.','Treatment X did not significantly increase survival.'),
    ('Protein X causes disease Y.','No evidence shows that protein X causes disease Y.'),
    ('Device A was measured at 300 K.','Device A was not measured at 300 K.'),
]


@pytest.mark.parametrize('claim,quote',CASES)
def test_unmatched_scope_cannot_become_decisive_contradiction(claim,quote):
    report=advisory.assess_claims(claim,[dict(source_id='synthetic',text=quote)])
    assert report['claims'][0]['relation']=='unknown'
    assert report['original_text']==claim and report['blocks_writing'] is False
    assert report['claims'][0]['comparisons'][0]['quote']==quote


@pytest.mark.parametrize('claim,quote',[
    ('Protein X causes disease Y.','No evidence shows that protein X causes disease Y.'),
    ('Treatment X increases survival.','It is unknown whether treatment X increases survival.'),
    ('Protein X causes disease Y.','The hypothesis that protein X causes disease Y was not tested.'),
    ('Protein X causes disease Y.','It is false that protein X causes disease Y.'),
])
def test_embedded_claim_is_not_source_endorsement(claim,quote):
    assert advisory.assess_relation(claim,quote)['relation']=='unknown'


def test_same_conditions_opposed_predicates_remain_visible():
    claim='Treatment X increases survival in mice.'
    report=advisory.assess_claims(claim,[dict(source_id='synthetic',text='Treatment X decreases survival in mice.')])
    assert report['claims'][0]['relation']=='possible_contradiction'
    assert report['scientific_proof'] is False


def test_other_experiment_conditions_are_context_not_claim_requirements():
    claim='Treatment X increases survival in mice.'
    source=claim+' An unrelated device experiment operated at 77 K.'
    row=advisory.assess_claims(claim,[dict(source_id='synthetic',text=source)])['claims'][0]
    assert not any(f['code']=='missing_condition' for f in row['findings'])
    assert row['comparisons'][0]['other_context_conditions']==['77 K']


def test_correlation_warning_does_not_assert_causal_contradiction():
    claim='Protein X causes disease Y.'
    report=advisory.assess_claims(claim,[dict(source_id='synthetic',text='Protein X is associated with disease Y.')])
    assert report['claims'][0]['relation']=='unknown'
    assert 'causality_not_established' in {f['code'] for f in report['claims'][0]['findings']}

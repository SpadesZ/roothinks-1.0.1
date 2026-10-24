"""I10 合成對抗控制：未解析的反證線索必須可見，且不得升格為矛盾判定。

這些是機械合成案例，不是獨立科學 gold；通過不代表跨領域語意能力。
驗證：python -m pytest test/unit/test_scientific_leads.py -q
"""
import pytest
from app.core_pro.manuscript import scientific_advisory as advisory


def source(text):return dict(source_id='synthetic',text=text)


def assess(claim,quote):return advisory.assess_claims(claim,[source(quote)])['claims'][0]


def codes(row):return {f['code'] for f in row['findings']}


def lead(row):return next(f for f in row['findings'] if f['code']=='counter_evidence_lead')


# 規則無法解析主客體（被動句、縮寫、不同實驗）但方向相反時，線索不得消失。
LEAD_CASES=[
    ('Treatment X reduces energy expenditure.','Energy expenditure was increased upon treatment X.',
     'direction_differs_arguments_unresolved'),
    ('Protein X causes disease Y.','It is false that protein X causes disease Y.','unresolved_stance_scope'),
    ('Protein X causes disease Y.','No evidence shows that protein X causes disease Y.',
     'source_does_not_establish_claim'),
    ('Treatment X increases survival.','Treatment X did not significantly increase survival.',
     'significance_is_not_direction'),
]


@pytest.mark.parametrize('claim,quote,signal',LEAD_CASES)
def test_unresolved_counter_evidence_stays_visible_without_becoming_a_verdict(claim,quote,signal):
    row=assess(claim,quote)
    assert row['relation']=='unknown'
    assert 'counter_evidence_lead' in codes(row)
    assert signal in lead(row)['leads'][0]['signals']
    # 線索不取代棄權說明；作者同時看到「未建立支持」與「有未解析的反證」。
    assert 'support_not_established' in codes(row)


@pytest.mark.parametrize('claim,quote',[
    ('Treatment X increases survival in mice.','Treatment X increases survival in mice.'),
    ('Protein X activates protein Y.','Protein Y activates protein X.'),
    ('Treatment X does not increase survival.','An unrelated device operated at 77 K.'),
])
def test_no_lead_for_same_direction_argument_swap_or_unrelated_text(claim,quote):
    assert 'counter_evidence_lead' not in codes(assess(claim,quote))


def test_decisive_contradiction_is_not_duplicated_as_an_unresolved_lead():
    row=assess('Treatment X increases survival in mice.','Treatment X decreases survival in mice.')
    assert row['relation']=='possible_contradiction'
    assert 'counter_evidence_lead' not in codes(row)


def test_lead_never_blocks_writing_claims_proof_or_edits_the_author_text():
    claim='Treatment X reduces energy expenditure.'
    report=advisory.assess_claims(claim,[source('Energy expenditure was increased upon treatment X.')])
    assert report['original_text']==claim
    assert report['blocks_writing'] is False and report['scientific_proof'] is False
    assert report['advisory_only'] is True


def test_lead_points_at_the_candidate_the_author_must_open():
    quote='Energy expenditure was increased upon treatment X.'
    row=assess('Treatment X reduces energy expenditure.',quote)
    entry=lead(row)['leads'][0]
    assert entry['source_id']=='synthetic' and entry['comparison']==0
    assert row['comparisons'][entry['comparison']]['quote']==quote


def test_missing_evidence_produces_no_lead_instead_of_an_error():
    row=advisory.assess_claims('A hypothesis with no selected evidence.',[])['claims'][0]
    assert 'no_evidence' in codes(row) and 'counter_evidence_lead' not in codes(row)


@pytest.mark.parametrize('claim,quote',[
    ('Treatment X increases survival at 300 K.','Treatment X decreases survival at 77 K.'),
    ('Drug X increases survival.','Drug Y decreases survival.'),
    ('Protein X activates protein Y.','Protein Y does not activate protein X.'),
    ('Treatment X increases survival.','Treatment X may decrease survival.'),
    ('Treatment X increases survival.','Treatment X did not significantly increase survival.'),
    ('Protein X causes disease Y.','No evidence shows that protein X causes disease Y.'),
    ('Device A was measured at 300 K.','Device A was not measured at 300 K.'),
])
def test_i09_scope_controls_keep_abstaining_after_leads_are_added(claim,quote):
    # I09 已證實的保守行為不得被線索改動；線索只新增說明，不改關係。
    assert assess(claim,quote)['relation']=='unknown'

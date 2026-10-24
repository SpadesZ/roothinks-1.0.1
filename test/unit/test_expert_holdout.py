"""只用假文獻驗封存／空白標註及授權邊界，不開封真 holdout。"""
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('prepare_expert_holdout',Path(__file__).resolve().parents[2]/'scripts/prepare_expert_holdout.py')
packet=importlib.util.module_from_spec(spec);spec.loader.exec_module(packet)


def source(doi='10.synthetic/one',domain='photonics'):
    return dict(doi=doi,source_id=doi,sampling_stratum=domain,text='Synthetic device A improved by 3 dB at 300 K. Only this sample was measured.')


def test_sealed_materials_have_no_expert_labels_or_exposed_holdout_text(tmp_path):
    one=source();two=source('10.synthetic/holdout')
    manifest=packet.seal(tmp_path,[one],[two],[],{})
    assert manifest['expert_judgments_created']==0 and manifest['expert_labels_complete'] is False
    assert two['doi'] not in json.dumps(manifest) and 'key' not in manifest
    assert b'Synthetic device' not in (tmp_path/'holdout.aesgcm').read_bytes()
    packet.open_packet(tmp_path,tmp_path/'synthetic-opened')
    unsealed=json.loads((tmp_path/'synthetic-opened/holdout-labeling.json').read_text(encoding='utf-8'))
    assert all(c['gold'] is None and c['adjudication'] is None and all(a['label'] is None and a['reviewer_id'] is None for a in c['annotations']) for c in unsealed['cases'])
    assert unsealed['sources']==[two]
    assert any(c['evidence_sources']==[] for c in unsealed['cases'])
    with pytest.raises(ValueError):packet.seal(tmp_path,[one],[one],[],{})


def test_seal_tampering_fails_before_export(tmp_path):
    packet.seal(tmp_path,[source()],[source('10.synthetic/holdout')],[],{})
    path=tmp_path/'holdout.aesgcm';path.write_bytes(path.read_bytes()+b'tampered')
    with pytest.raises(ValueError):packet.open_packet(tmp_path,tmp_path/'must-not-exist')
    assert not (tmp_path/'must-not-exist').exists()


@pytest.mark.parametrize('license_url,year',[('https://creativecommons.org/licenses/by-nc/4.0/',2023),('https://creativecommons.org/licenses/by/4.0/',2019)])
def test_license_and_source_age_are_verified_from_article_metadata(license_url,year):
    xml=f'<article article-type="research-article"><front><article-meta><permissions><license href="{license_url}"/></permissions><abstract>{"Synthetic original context. "*10}</abstract></article-meta></front></article>'.encode()
    with pytest.raises(ValueError):packet.article(xml,dict(pmcid='PMCsynthetic',doi='10.synthetic/source',pubYear=year),'photonics')


def test_nested_official_license_link_is_verified_without_relaxing_nc_rejection():
    xml=('<article article-type="research-article"><front><article-meta><permissions><license><ext-link href="https://creativecommons.org/licenses/by/4.0/">CC BY</ext-link></license></permissions><abstract>'+('Synthetic original context. '*10)+'</abstract></article-meta></front></article>').encode()
    row=packet.article(xml,dict(pmcid='PMCsynthetic',doi='10.synthetic/source',pubYear=2023),'photonics')
    assert row['license_url']=='https://creativecommons.org/licenses/by/4.0/'
    with pytest.raises(ValueError):packet.article(xml.replace(b'/by/4.0/',b'/by-nc/4.0/'),dict(pmcid='PMCsynthetic',doi='10.synthetic/source',pubYear=2023),'photonics')


def test_external_custodian_key_and_wrong_key_never_export_plaintext(tmp_path):
    data=tmp_path/'data';data.mkdir();custody=tmp_path/'custody';custody.mkdir();key=custody/'key.json'
    manifest=packet.seal(data,[source()],[source('10.synthetic/held')],[],{},key_file=key)
    assert key.exists() and not (data/'evaluator-key.json').exists()
    assert manifest['external_key_requested'] is True and manifest['independent_custodian_assigned'] is False
    wrong=custody/'wrong.json';packet.save(wrong,dict(key='AA=='))
    with pytest.raises(ValueError):packet.open_packet(data,tmp_path/'wrong-export',key_file=wrong)
    assert not (tmp_path/'wrong-export').exists()
    packet.open_packet(data,tmp_path/'correct-export',key_file=key)
    with pytest.raises(FileExistsError):packet.seal(data,[source()],[source('10.synthetic/held')],[],{},key_file=key)


def test_colocated_external_key_rejected_and_partial_seal_keeps_recovery_key(tmp_path,monkeypatch):
    data=tmp_path/'data';data.mkdir();custody=tmp_path/'custody';custody.mkdir();key=custody/'key.json'
    with pytest.raises(ValueError):packet.seal(data,[source()],[source('10.synthetic/held')],[],{},key_file=data/'key.json')
    original=packet.save
    def fail_manifest(path,value):
        if path.name=='public-manifest.json':raise OSError('synthetic disk failure')
        original(path,value)
    monkeypatch.setattr(packet,'save',fail_manifest)
    with pytest.raises(OSError):packet.seal(data,[source()],[source('10.synthetic/held')],[],{},key_file=key)
    assert key.exists() and (data/'holdout.aesgcm').exists()
    assert not (data/'public-manifest.json').exists()  # 不假稱封存完整；保留失敗供保管人恢復。

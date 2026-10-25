# Roothinks source maintenance contract
# 目的：固定 NLLB 的來源／目標語言、CPU batch、錯誤邊界；不要求已移除的 translation pipeline。
# 驗證：python -m pytest test/unit/test_nllb_generation_adapter.py -q
import contextlib
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def translator(monkeypatch, tmp_path):
    from app.core_pro.literature.literature_nllb import NLLBTranslator
    calls = []

    class Tokenizer:
        unk_token_id = 0

        def convert_tokens_to_ids(self, language):
            return {'zho_Hant': 42, 'fra_Latn': 43}.get(language, 0)

        def __call__(self, texts, **kwargs):
            assert self.src_lang == 'eng_Latn'
            assert kwargs.get('truncation') is not True, '不得無聲截掉來源條件'
            return {'texts': texts}

        def batch_decode(self, values, **kwargs):
            return values

    class Model:
        def eval(self):
            return self

        def generate(self, texts, **kwargs):
            calls.append((list(texts), kwargs))
            return ['translated:' + text for text in texts]

    def load(value):
        def loader(*args, **kwargs):
            assert kwargs['local_files_only'] is True
            assert kwargs['trust_remote_code'] is False
            return value
        return SimpleNamespace(from_pretrained=loader)

    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        AutoTokenizer=load(Tokenizer()), AutoModelForSeq2SeqLM=load(Model())))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(inference_mode=contextlib.nullcontext))
    monkeypatch.setenv('NLLB_MODEL_DIR', str(tmp_path))
    monkeypatch.setenv('NLLB_BATCH_SIZE', '2')
    (tmp_path / 'config.json').write_text('{}')
    monkeypatch.setattr(NLLBTranslator, '_instance', None)
    instance = NLLBTranslator()
    assert instance.ready
    return instance, calls


def test_explicit_target_batching_and_source_conditions(translator):
    instance, calls = translator
    source = ['Only sample A at 300 K.', 'Simulation only.', 'Not measured.']
    assert instance.translate_texts(source, 'fra_Latn') == ['translated:' + text for text in source]
    assert [len(texts) for texts, _ in calls] == [2, 1]
    assert all(kwargs['forced_bos_token_id'] == 43 and kwargs['max_length'] == 512 for _, kwargs in calls)
    assert instance.translate_text('Only at 300 K.') == 'translated:Only at 300 K.'


def test_unknown_target_fails_without_generating_wrong_language(translator):
    instance, calls = translator
    assert instance.translate_texts(['Only at 300 K.'], 'invalid') is None
    assert calls == []

"""翻譯目標與 OCR argv 邊界；只使用合成文字與暫存檔。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.core_pro.literature.literature_translator import HybridTranslator
from app.core_pro.literature.literature_translator import _QuickGoogleTranslator
from app.core_pro.literature.literature_latex_ocr import LatexOCREngine


def test_google_target_reaches_single_and_batch():
    class Google:
        def translate(self, text, *, target_lang):
            assert target_lang == "jpn_Jpan"
            return "翻譯結果"

        def translate_batch(self, texts, *, target_lang):
            return [self.translate(t, target_lang=target_lang) for t in texts]

    tr = HybridTranslator.__new__(HybridTranslator)
    tr._quick_translator = Google()
    tr._nllb = None
    tr._gemini_available = False
    assert tr._translate_quick_google("Synthetic text.", "jpn_Jpan") == (True, "翻譯結果")
    assert tr._translate_segments_forced(["Synthetic text."], "jpn_Jpan", "google") == (True, "翻譯結果")
    assert tr._translate_segments_small_batch(["Synthetic text."], "jpn_Jpan") == (True, "翻譯結果")


def _ocr(command):
    engine = LatexOCREngine.__new__(LatexOCREngine)
    engine.unimernet_cmd = command
    engine.unimernet_timeout_sec = 3
    return engine


def _run(engine, image):
    return engine._run_unimernet_command(image_path=image, seq_id="eq1", page_num=1,
        bbox=[1, 2, 3, 4], page_w=10, page_h=20)


def test_ocr_never_uses_shell(monkeypatch):
    def run(argv, **kwargs):
        assert kwargs["shell"] is False
        assert argv == ["ocr", "--image", 'a"; echo injected; #.png', "--bbox", "[1, 2, 3, 4]"]
        return subprocess.CompletedProcess(argv, 0, '{"latex":"x","confidence":1}', "")
    monkeypatch.setattr(subprocess, "run", run)
    assert _run(_ocr('ocr --image "{image}" --bbox "{bbox}"'), 'a"; echo injected; #.png')["latex"] == "x"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell injection reproduction")
def test_ocr_filename_cannot_execute_a_second_command(tmp_path):
    sentinel = tmp_path / "injected"
    helper = tmp_path / "ocr tool.py"
    helper.write_text("import json,sys;print(json.dumps({'latex':sys.argv[1]}))", encoding="utf-8")
    image = f'picture"; touch {sentinel}; #.png'
    result = _run(_ocr(f'"{sys.executable}" "{helper}" "{{image}}"'), image)
    assert not sentinel.exists()
    assert result == {"latex": image}


@pytest.mark.parametrize("command", [
    'sh -c "ocr {image}"', 'bash -c "ocr {image}"',
    'python -c "print(\'{image}\')"', 'cmd /c "ocr {image}"',
    'powershell -Command "ocr {image}"', 'node -e "ocr({image})"',
    'python "-cprint(\'{image}\')"', 'node "--eval=ocr({image})"',
    'ocr {image} | tee output', '{image} --version', 'ocr.bat {image}',
])
def test_ocr_rejects_code_templates(command, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe template reached process creation")
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert _run(_ocr(command), "synthetic.png") is None


def test_ocr_appends_unspecified_image_as_one_argument(monkeypatch):
    def run(argv, **kwargs):
        assert argv == ["ocr", "--json", "a b.png"]
        return subprocess.CompletedProcess(argv, 0, '{"latex":"x"}', "")
    monkeypatch.setattr(subprocess, "run", run)
    assert _run(_ocr("ocr --json"), "a b.png") == {"latex": "x"}


class _Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield self.body


def test_google_fixed_https_target_and_response(monkeypatch):
    def get(url, **kwargs):
        assert url == "https://translate.googleapis.com/translate_a/single"
        assert kwargs == dict(params={"client": "gtx", "sl": "en", "tl": "ja", "dt": "t", "q": "Synthetic."},
            timeout=(3, 10), allow_redirects=False, stream=True)
        return _Response(json.dumps([[["合成。", "Synthetic."]]]).encode())
    monkeypatch.setattr("requests.get", get)
    assert _QuickGoogleTranslator().translate("Synthetic.", target_lang="jpn_Jpan") == "合成。"


@pytest.mark.parametrize("body", [b"{}", b"[]", b"[[]]", b"[[[null]]]", b"[[[\"\"]]]", b"not JSON", b"x" * 131073])
def test_google_rejects_partial_or_oversized_responses(body, monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _Response(body))
    with pytest.raises(ValueError):
        _QuickGoogleTranslator().translate("Synthetic.")


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_google_does_not_follow_redirects_or_claim_success(status, monkeypatch):
    response = _Response(b'[[["misleading"]]]')
    response.status_code = status
    monkeypatch.setattr("requests.get", lambda *a, **k: response)
    with pytest.raises(ValueError, match="provider rejected"):
        _QuickGoogleTranslator().translate("Synthetic.")


def test_google_network_error_does_not_disclose_source(monkeypatch):
    import requests
    def fail(*a, **k):
        raise requests.Timeout("https://example.invalid/?q=PRIVATE_SYNTHETIC")
    monkeypatch.setattr("requests.get", fail)
    with pytest.raises(ValueError) as error:
        _QuickGoogleTranslator().translate("PRIVATE_SYNTHETIC")
    assert str(error.value) == "Google translation network unavailable"


def test_google_invalid_target_does_not_send_a_request(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("Unexpected request"))
    with pytest.raises(ValueError, match="target language"):
        _QuickGoogleTranslator().translate("Synthetic.", target_lang="https://example.invalid")

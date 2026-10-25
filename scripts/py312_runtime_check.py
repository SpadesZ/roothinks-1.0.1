#!/usr/bin/env python
# Roothinks source maintenance contract
# 檔案路徑: scripts/py312_runtime_check.py
# 模組定位: 維運/資料處理 CLI 層；由人工或隔離測試明確執行，不是常駐 request path。
# 主要責任: 在 Python 3.12 遷移候選映像內檢查直譯器、匯入、torch/torchvision、OpenCV、eventlet、NLLB 載入路徑、easyocr、tesseract/poppler。
# 上下游: docker run <image> python scripts/py312_runtime_check.py -> JSON 報告（stdout）；Owner 可在可連網機器重跑。
# 維護邊界: 只寫入 --work 指定的暫存目錄；不碰 data/、DB 或 .env。NLLB 只用合成小權重，不代表真模型品質。
# 驗證: python scripts/py312_runtime_check.py --work /tmp/p312-check
#路徑(./scripts/py312_runtime_check.py) #版本 v0.1 #更版時間 20261025
#功能概要:
#1. 每項檢查回報 passed/failed/skipped 與細節；任一 failed 則 exit 1。
#2. NLLB：造一個 HuggingFace 離線快取（合成小模型），讓 scripts/bake_nllb.py 原封不動跑完，再由 NLLBTranslator 載入。
#3. easyocr：照 literature_stacka 的參數載入 en+ch_tra；--allow-download 時允許從 GitHub 下載（easyocr 自行驗 md5）。
import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'facebook/nllb-200-distilled-600M'
MODULES = ['flask', 'flask_sqlalchemy', 'flask_socketio', 'flask_login', 'flask_wtf', 'flask_limiter', 'gunicorn',
           'eventlet', 'redis', 'cryptography', 'filelock', 'alembic', 'google.generativeai', 'openai',
           'deep_translator', 'dotenv', 'requests', 'httpx', 'sqlalchemy', 'werkzeug', 'socketio', 'PIL', 'cv2',
           'pytesseract', 'numpy', 'pdf2image', 'layoutparser', 'easyocr', 'transformers', 'sentencepiece',
           'torch', 'torchvision', 'safetensors', 'huggingface_hub']


def run(name, func, results):
    try:
        status, detail = func()
    except Exception as exc:  # 檢查本身失敗即記為 failed，保留原因
        status, detail = 'failed', f'{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}'
    results.append(dict(name=name, status=status, detail=detail))


def check_interpreter():
    ok = sys.version_info[:2] == (3, 12)
    return ('passed' if ok else 'failed'), sys.version


def check_imports():
    failed, versions = {}, {}
    for name in MODULES:
        try:
            module = importlib.import_module(name)
            versions[name] = str(getattr(module, '__version__', ''))
        except Exception as exc:
            failed[name] = f'{type(exc).__name__}: {exc}'
    return ('failed' if failed else 'passed'), dict(failed=failed, versions=versions)


def check_torch():
    import torch
    import torchvision
    from torchvision.ops import nms
    x = torch.randn(2, 3, 32, 32)
    y = torch.nn.Conv2d(3, 4, 3)(x)
    boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 10, 10], [20, 20, 30, 30]], dtype=torch.float32)
    kept = nms(boxes, torch.tensor([0.9, 0.8, 0.7]), 0.5).tolist()
    ok = tuple(y.shape) == (2, 4, 30, 30) and kept == [0, 2]
    return ('passed' if ok else 'failed'), dict(torch=torch.__version__, torchvision=torchvision.__version__,
                                                 cuda_available=torch.cuda.is_available(), nms=kept,
                                                 threads=torch.get_num_threads())


def check_opencv(work):
    import cv2
    import numpy as np
    from PIL import Image
    path = work / 'cv.png'
    Image.new('RGB', (40, 20), 'white').save(path)
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    ok = binary.shape == (20, 40) and int(binary.max()) == 255
    return ('passed' if ok else 'failed'), dict(version=cv2.__version__, file=cv2.__file__)


EVENTLET_PROBE = r'''
import eventlet
eventlet.monkey_patch()
import json, ssl, urllib.request
from eventlet import wsgi
def app(env, start):
    start('200 OK', [('Content-Type', 'text/plain')])
    return [b'green-ok']
sock = eventlet.listen(('127.0.0.1', 0))
port = sock.getsockname()[1]
eventlet.spawn(wsgi.server, sock, app, log_output=False)
body = urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=5).read()
context = ssl.create_default_context()
print(json.dumps(dict(body=body.decode(), eventlet=eventlet.__version__, ssl_context=type(context).__module__)))
'''


def check_eventlet():
    done = subprocess.run([sys.executable, '-c', EVENTLET_PROBE], capture_output=True, text=True, timeout=60)
    if done.returncode:
        return 'failed', done.stderr[-2000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])
    return ('passed' if report['body'] == 'green-ok' else 'failed'), report


def fake_hub(cache):
    """建立 MODEL 的離線 HuggingFace 快取：合成小模型 + 合成 sentencepiece，不連網。"""
    import sentencepiece as spm
    import torch
    from transformers import AutoModelForSeq2SeqLM, M2M100Config, NllbTokenizer
    snapshot = cache / f"models--{MODEL.replace('/', '--')}" / 'snapshots' / ('0' * 40)
    snapshot.mkdir(parents=True)
    (snapshot.parents[1] / 'refs').mkdir()
    (snapshot.parents[1] / 'refs' / 'main').write_text('0' * 40)
    corpus = cache / 'corpus.txt'
    corpus.write_text('\n'.join(['The measured improvement applies only to sample A.',
                                 'Synthetic translation smoke test for the migration.'] * 20), encoding='utf-8')
    spm.SentencePieceTrainer.train(input=str(corpus), model_prefix=str(cache / 'sentencepiece.bpe'), vocab_size=60,
                                   model_type='bpe', character_coverage=1.0)
    tokenizer = NllbTokenizer(vocab_file=str(cache / 'sentencepiece.bpe.model'), src_lang='eng_Latn', tgt_lang='zho_Hant')
    tokenizer.save_pretrained(snapshot)
    config = M2M100Config(vocab_size=len(tokenizer), d_model=16, encoder_layers=1, decoder_layers=1,
                          encoder_attention_heads=2, decoder_attention_heads=2, encoder_ffn_dim=32, decoder_ffn_dim=32,
                          max_position_embeddings=64, pad_token_id=tokenizer.pad_token_id, bos_token_id=0,
                          eos_token_id=2, decoder_start_token_id=2, max_length=16)
    config.architectures = ['M2M100ForConditionalGeneration']
    config.save_pretrained(snapshot)
    torch.save(AutoModelForSeq2SeqLM.from_config(config).state_dict(), snapshot / 'pytorch_model.bin')


TRANSLATOR_PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from app.core_pro.literature.literature_nllb import NLLBTranslator
translator = NLLBTranslator()
single = translator.translate_text('The measured improvement applies only to sample A.')
batch = translator.translate_texts(['Synthetic translation smoke test.', 'Second sentence.'])
print(json.dumps(dict(ready=translator.ready, single_type=type(single).__name__,
                      batch=None if batch is None else len(batch))))
'''


def check_nllb(work):
    cache, out = work / 'hf', work / 'nllb'
    fake_hub(cache)
    env = dict(os.environ, NLLB_HF_CACHE=str(cache), NLLB_MODEL_DIR=str(out), HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1')
    bake = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'bake_nllb.py')], capture_output=True, text=True,
                          env=env, timeout=600)
    if bake.returncode:
        return 'failed', dict(stage='bake_nllb.py', stderr=bake.stderr[-3000:])
    probe = subprocess.run([sys.executable, '-c', TRANSLATOR_PROBE, str(ROOT)], capture_output=True, text=True,
                           env=env, timeout=600)
    if probe.returncode:
        return 'failed', dict(stage='NLLBTranslator', stderr=probe.stderr[-3000:])
    report = json.loads(probe.stdout.strip().splitlines()[-1])
    report['baked_files'] = sorted(os.listdir(out))
    report['bake_tail'] = bake.stdout.strip().splitlines()[-1]
    ok = report['ready'] and report['single_type'] == 'str' and report['batch'] == 2 and \
        any(name.endswith('.safetensors') for name in report['baked_files'])
    report['note'] = '合成小權重：只驗載入與推論路徑，不代表翻譯品質；真 NLLB 權重需 HuggingFace'
    return ('passed' if ok else 'failed'), report


def text_image(path, text):
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGB', (900, 160), 'white')
    try:
        font = ImageFont.load_default(size=72)
    except TypeError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text((30, 30), text, fill='black', font=font)
    image.save(path)
    return path


def check_easyocr(work, model_dir, allow_download):
    import easyocr
    if not model_dir:
        return 'skipped', '未提供 --easyocr-dir'
    reader = easyocr.Reader(['en', 'ch_tra'], gpu=False, model_storage_directory=model_dir,
                            download_enabled=allow_download, verbose=False)
    found = ' '.join(text for _, text, _ in reader.readtext(str(text_image(work / 'ocr.png', 'ROOTHINKS 2026'))))
    ok = 'ROOTHINKS' in found.upper().replace(' ', '') and '2026' in found
    return ('passed' if ok else 'failed'), dict(version=easyocr.__version__, read=found,
                                                 models=sorted(os.listdir(model_dir)))


def check_tesseract_poppler(work):
    if not shutil.which('tesseract') or not shutil.which('pdftoppm'):
        return 'skipped', '映像沒有 tesseract 或 poppler（生產 Dockerfile 由 apt 安裝）'
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import Image
    image = text_image(work / 'tess.png', 'ROOTHINKS 2026')
    read = pytesseract.image_to_string(Image.open(image))
    pdf = work / 'page.pdf'
    Image.open(image).save(pdf, format='PDF')
    pages = convert_from_path(str(pdf), dpi=72)
    ok = 'ROOTHINKS' in read.upper() and len(pages) == 1
    return ('passed' if ok else 'failed'), dict(tesseract=str(pytesseract.get_tesseract_version()),
                                                 read=read.strip(), pdf_pages=len(pages))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', required=True)
    parser.add_argument('--easyocr-dir')
    parser.add_argument('--allow-download', action='store_true')
    parser.add_argument('--skip', action='append', default=[])
    args = parser.parse_args()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=False)
    checks = [('interpreter', check_interpreter), ('imports', check_imports), ('torch', check_torch),
              ('opencv', lambda: check_opencv(work)), ('eventlet', check_eventlet),
              ('nllb_load_path', lambda: check_nllb(work)),
              ('easyocr', lambda: check_easyocr(work, args.easyocr_dir, args.allow_download)),
              ('tesseract_poppler', lambda: check_tesseract_poppler(work))]
    results = []
    for name, func in checks:
        if name in args.skip:
            results.append(dict(name=name, status='skipped', detail='--skip'))
        else:
            run(name, func, results)
    print(json.dumps(dict(python=sys.version, results=results), ensure_ascii=False, indent=2))
    return 1 if any(r['status'] == 'failed' for r in results) else 0


if __name__ == '__main__':
    sys.exit(main())

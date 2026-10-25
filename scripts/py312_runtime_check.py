# Roothinks source maintenance contract
# 檔案路徑: scripts/py312_runtime_check.py
# 主要責任: 逐項驗證整合映像 CPU/runtime 能力與真 NLLB；不宣稱科學品質。
# 上下游: 明示候選 image -> capabilities/model -> 新 JSON 證據。
# 維護邊界: 不開網路、不掛正式資料、結果不可覆寫；OCR 真權重另由 easyocr_probe check。
# 驗證: python scripts/py312_runtime_check.py capabilities /tmp/new-check.json
"""候選 image 的真相依／CPU 運算／公開模型檢查。不是科學品質評分。"""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def capabilities():
    import cv2
    import numpy as np
    import torch
    import torchvision
    installed={d.metadata['Name'].casefold():d.version for d in metadata.distributions()}
    assert 'opencv-python' not in installed and 'opencv-contrib-python' not in installed
    assert installed['opencv-python-headless']=='4.11.0.86'
    assert sys.version_info[:2]==(3,12)
    assert torch.__version__=='2.13.0+cpu' and torchvision.__version__=='0.28.0+cpu'
    assert torch.version.cuda is None and not torch.cuda.is_available()
    kept=torchvision.ops.nms(torch.tensor([[0.,0.,10.,10.],[1.,1.,9.,9.]]),torch.tensor([.9,.8]),.5)
    assert kept.tolist()==[0]
    image=np.zeros((12,12,3),dtype=np.uint8);image[3:9,3:9]=255
    ok,encoded=cv2.imencode('.png',image);assert ok
    assert np.array_equal(image,cv2.imdecode(encoded,cv2.IMREAD_COLOR))
    from easyocr import Reader  # 不自動下載 OCR 權重；本輪實際模型載入另驗 NLLB。
    assert Reader is not None
    command=[sys.executable,'-c',"import eventlet; eventlet.monkey_patch(); from gunicorn.workers.geventlet import EventletWorker; p=eventlet.GreenPool(1); assert p.spawn(lambda: 7).wait()==7; print('eventlet worker import and green task passed')"]
    child=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr
    assert shutil.which('pdftoppm') and shutil.which('tesseract')
    checked=['torch','torchvision','eventlet','opencv-python-headless','numpy','easyocr','transformers','sentencepiece','huggingface-hub','flask','python-socketio','gunicorn']
    return dict(python=sys.version,platform=platform.platform(),versions={k:metadata.version(k) for k in checked},
        cpu_only=True,torchvision_native_nms=True,cv2_png_roundtrip=True,single_cv2_provider=True,
        eventlet_subprocess=child.stdout.strip(),easyocr_import=True,easyocr_weights_loaded=False,
        system_pdf_ocr_tools=True,scientific_quality_measured=False)


def model():
    from app.core_pro.literature.literature_nllb import NLLBTranslator
    translator=NLLBTranslator();assert translator.ready,'Real baked model did not initialize'
    source='The sample was measured at 300 K.'
    translated=translator.translate_text(source)
    assert translated!=source and any('\u4e00'<=c<='\u9fff' for c in translated),repr(translated)
    batch=translator.translate_texts([source,'This is a local test.'])
    assert batch and len(batch)==2 and all(any('\u4e00'<=c<='\u9fff' for c in v) for v in batch),repr(batch)
    return dict(ready=True,adapter='NLLBTranslator',source=source,translated=translated,batch=batch,
        offline=True,scientific_quality_measured=False,translation_quality_measured=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['capabilities','model'])
    parser.add_argument('output',type=Path);args=parser.parse_args()
    value=capabilities() if args.mode=='capabilities' else model()
    with args.output.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    print(json.dumps(value,ensure_ascii=False))

"""只讀合成 fixture 的有效 PDF：原件指紋、文字、轉圖及 Tesseract。

由 pdf_browser.cjs 透過同一容器 stdin 執行；不使用真研究文件。
"""
import hashlib
import json
from pathlib import Path
import sys
import subprocess

from pdf2image import convert_from_path
import pytesseract

source = Path(sys.argv[1]).resolve()
if not str(source).startswith(('/qa-tmp/roothinks-sai-browser-', '/tmp/roothinks-sai-browser-')) or source.name != 'source.pdf':
    raise ValueError('Only the synthetic browser original is allowed')
metadata = json.loads(source.with_name('metadata.json').read_text())
digest = hashlib.sha256(source.read_bytes()).hexdigest()
assert digest == metadata['sha256']
text = subprocess.check_output(['pdftotext', str(source), '-'], text=True).strip()
assert text == 'Local PDF 300 K', text
images = convert_from_path(source, dpi=150)
assert len(images) == 1
ocr = pytesseract.image_to_string(images[0], lang='eng', config='--psm 6').strip()
assert ocr == 'Local PDF 300 K', ocr
print(json.dumps(dict(sha256=digest, text=text, pages=len(images), tesseract=ocr,
                     registration=metadata['registration'])))

"""固定合成控制的舊／新模型診斷；不讀 gold/holdout，也不把失誤當成科學品質通過。

render <new-dir>、translation <controls> <new-json>、ocr <rendered-dir> <new-json>。
render 只在有 Noto CJK font 的 verification image 執行；兩環境讀同一組像素。
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import unicodedata


def save(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def normalized(value):
    return ''.join(unicodedata.normalize('NFKC', value).upper().split())


def distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        row = [i]
        for j, b in enumerate(right, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j-1] + (a != b)))
        previous = row
    return previous[-1]


mode, *arguments = sys.argv[1:]
if mode == 'render':
    from PIL import Image, ImageDraw, ImageFont
    destination = Path(arguments[0]); destination.mkdir(exist_ok=False)
    controls = json.loads(Path(__file__).with_name('quality_controls.json').read_text(encoding='utf-8'))
    font = ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 44, index=3)
    for row in controls['ocr']:
        image = Image.new('RGB', (1100, 150), 'white')
        ImageDraw.Draw(image).text((30, 35), row['text'], font=font, fill='black')
        image.save(destination / (row['id'] + '.png'))
    save(destination / 'controls.json', controls)
elif mode == 'translation':
    from app.core_pro.literature.literature_nllb import NLLBTranslator
    source, destination = map(Path, arguments)
    controls = json.loads(source.read_text(encoding='utf-8'))
    translator = NLLBTranslator(); assert translator.ready
    outputs = translator.translate_texts(controls['translation'])
    assert outputs and len(outputs) == len(controls['translation'])
    rows = []
    for original, translated in zip(controls['translation'], outputs):
        numbers = lambda value: re.findall(r'-?\d+(?:\.\d+)?', unicodedata.normalize('NFKC', value))
        rows.append(dict(source=original, output=translated, source_numbers=numbers(original),
            output_numbers=numbers(translated), numeric_tokens_identical=numbers(original) == numbers(translated),
            cjk=any('\u4e00' <= char <= '\u9fff' for char in translated)))
    save(destination, dict(controls_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), rows=rows,
        numeric_exact=sum(row['numeric_tokens_identical'] for row in rows), count=len(rows),
        semantic_condition_fidelity='NOT MEASURED: requires human review', scientific_quality_pass=False))
elif mode == 'ocr':
    import easyocr
    import pytesseract
    from PIL import Image
    source, destination = map(Path, arguments)
    controls = json.loads((source / 'controls.json').read_text(encoding='utf-8'))
    rows = []
    available_languages = pytesseract.get_languages()
    tesseract_lang = 'eng+chi_tra' if 'chi_tra' in available_languages else 'eng'
    reader = easyocr.Reader(['en', 'ch_tra'], gpu=False,
        model_storage_directory=os.environ['EASYOCR_MODULE_PATH'],
        user_network_directory='/tmp/quality-easyocr', download_enabled=False, verbose=False)
    for row in controls['ocr']:
        path = source / (row['id'] + '.png')
        with Image.open(path) as image:
            tesseract = pytesseract.image_to_string(image, lang=tesseract_lang, config='--psm 6').strip()
        output = ' '.join(reader.readtext(str(path), detail=0, workers=0))
        expected = normalized(row['text'])
        rows.append(dict(**row, image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            easyocr=output, tesseract=tesseract,
            easyocr_cer=distance(expected, normalized(output)) / len(expected),
            tesseract_cer=distance(expected, normalized(tesseract)) / len(expected)))
    save(destination, dict(rows=rows, tesseract_languages=available_languages, tesseract_used=tesseract_lang,
        missing_chi_tra='chi_tra' not in available_languages, independent_validation=False, scientific_quality_pass=False))
else:
    raise ValueError(mode)

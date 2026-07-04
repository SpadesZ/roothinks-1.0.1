# OCR Ground Truth

Place small synthetic or manually created OCR samples here. Do not commit large PDFs or copyrighted paper pages.

Suggested layout:

- `samples/sample_001_page_001.png`: sample page image.
- `annotations/sample_001_page_001.gt.txt`: exact ground-truth text.
- `annotations/sample_001_page_001.layout.json`: optional layout boxes.
- `../ocr_results/sample_001_page_001.pred.txt`: OCR prediction text.

Run:

```bash
python scripts/evaluate_ocr.py --pred-dir evaluation/ocr_results --gt-dir evaluation/ocr_ground_truth/annotations
```

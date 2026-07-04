# OCR Evaluation

產生時間: 2026-07-04 19:24 +08:00

## Ground Truth Layout

- `evaluation/ocr_ground_truth/samples/`: small sample page images.
- `evaluation/ocr_ground_truth/annotations/`: `*.gt.txt` ground-truth text and optional layout JSON.
- `evaluation/ocr_results/`: OCR prediction text and generated summaries.

Do not commit large PDFs or copyrighted paper pages.

## Metrics

CER is character edit distance divided by ground-truth character count. WER is word edit distance divided by ground-truth word count. Both use normalized whitespace and lowercase text.

## Run

```bash
python scripts/evaluate_ocr.py --pred-dir evaluation/ocr_results --gt-dir evaluation/ocr_ground_truth/annotations
```

The script writes `evaluation/ocr_results/summary.json`.

## Adding Samples

Use names like:

- `sample_001_page_001.png`
- `sample_001_page_001.gt.txt`
- `sample_001_page_001.layout.json`

## Future Calibration

Once enough samples exist, compare StackA, StackB, Surya, and Arbiter outputs and tune thresholds against CER/WER and optional layout IoU.

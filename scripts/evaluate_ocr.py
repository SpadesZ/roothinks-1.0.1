# 檔案路徑: scripts/evaluate_ocr.py
# 產生時間: 2026-07-04 19:14 +08:00
# 版本: v0.1
# 模組定位:
#   OCR ground-truth evaluation CLI。
# 主要責任:
#   1. 讀取 *.gt.txt annotation。
#   2. 對應 prediction txt，計算 CER/WER。
#   3. 輸出 evaluation/ocr_results/summary.json。
# 維護提醒:
#   - 本 script 不直接呼叫 OCR pipeline，避免評測框架改動既有處理流程。
# -----------------------------------------------------------------------------

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evaluation.ocr_metrics import character_error_rate, word_error_rate


def _prediction_candidates(pred_dir: Path, sample_id: str) -> list[Path]:
    return [
        pred_dir / f"{sample_id}.pred.txt",
        pred_dir / f"{sample_id}.txt",
        pred_dir / f"{sample_id}.ocr.txt",
    ]


def evaluate_ocr(pred_dir: Path, gt_dir: Path, output_path: Path) -> dict:
    samples = []
    for gt_path in sorted(gt_dir.glob("*.gt.txt")):
        sample_id = gt_path.name[: -len(".gt.txt")]
        pred_path = next((p for p in _prediction_candidates(pred_dir, sample_id) if p.exists()), None)
        gt_text = gt_path.read_text(encoding="utf-8")
        pred_text = pred_path.read_text(encoding="utf-8") if pred_path else ""
        samples.append(
            {
                "sample_id": sample_id,
                "cer": character_error_rate(pred_text, gt_text),
                "wer": word_error_rate(pred_text, gt_text),
                "prediction_path": str(pred_path) if pred_path else "",
                "ground_truth_path": str(gt_path),
            }
        )

    avg_cer = sum(x["cer"] for x in samples) / len(samples) if samples else 0.0
    avg_wer = sum(x["wer"] for x in samples) / len(samples) if samples else 0.0
    payload = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "samples": samples,
        "average_cer": avg_cer,
        "average_wer": avg_wer,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(output_path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OCR predictions against ground truth text.")
    parser.add_argument("--pred-dir", default="evaluation/ocr_results")
    parser.add_argument("--gt-dir", default="evaluation/ocr_ground_truth/annotations")
    parser.add_argument("--output", default="evaluation/ocr_results/summary.json")
    args = parser.parse_args()
    summary = evaluate_ocr(Path(args.pred_dir), Path(args.gt_dir), Path(args.output))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

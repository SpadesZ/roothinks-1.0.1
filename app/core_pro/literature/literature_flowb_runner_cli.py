#路徑(app/core_pro/literature/literature_flowb_runner_cli.py) #版本 v0.1 #更版時間 20260429
#功能概要:
#1. Flow B 子程序 CLI 入口（由主程序以 subprocess 呼叫）。
#2. 接收 input/output/result 參數並執行翻譯流程。
#3. 將執行結果寫入 result JSON 供主程序回收狀態。

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_result(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Flow B subprocess runner")
    p.add_argument("--data-root", required=True)
    p.add_argument("--pid", required=True)
    p.add_argument("--paper-id", required=True)
    p.add_argument("--input-path", required=True)
    p.add_argument("--output-path", required=True)
    p.add_argument("--result-path", required=True)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    started = time.time()
    payload = {
        "ok": False,
        "pid": args.pid,
        "paper_id": args.paper_id,
        "started_at": _now_iso(),
        "process_id": os.getpid(),
        "input_path": args.input_path,
        "output_path": args.output_path,
    }

    try:
        if not os.path.exists(args.input_path):
            payload["error"] = "input file not found"
            return 1

        os.makedirs(os.path.dirname(args.output_path), exist_ok=True)

        from app.core_pro.literature.literature_translator import TranslationContext, get_translator

        translator = get_translator()
        if translator is None:
            payload["error"] = "translator unavailable"
            return 1

        ok = bool(
            translator.translate_file(
                args.input_path,
                args.output_path,
                TranslationContext.LITERATURE_BATCH,
            )
        )

        if not ok:
            payload["error"] = "translator returned false"
            return 1

        if not os.path.exists(args.output_path):
            payload["error"] = "output file missing after translation"
            return 1

        payload["ok"] = True
        payload["translator_status"] = getattr(translator, "status", None)
        return 0
    except Exception as e:
        payload["error"] = f"runner exception: {e}"
        return 1
    finally:
        payload["ended_at"] = _now_iso()
        payload["elapsed_sec"] = round(time.time() - started, 3)
        _write_result(args.result_path, payload)


if __name__ == "__main__":
    sys.exit(main())

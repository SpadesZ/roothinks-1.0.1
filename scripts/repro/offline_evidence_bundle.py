# Roothinks source maintenance contract
# 檔案路徑: scripts/repro/offline_evidence_bundle.py
# 目的: 把「不需要模型、不需要網路、不花錢」的評估證據打包成可重現的一包，讓稽核者在另一台機器重跑比對。
# 內容: Study Tutor 評估協議 v2 的負向控制與忠實／作假替身（工具自我檢查，**不是模型品質**）、
#       翻譯保護層量測（**模擬**佔位符損壞，不是任何真引擎的品質）。
# 輸出: <out>/manifest.json（程式 SHA、工作樹是否乾淨、Python 版本、每個輸出的穩定雜湊）＋每個輸出 JSON。
#       「穩定雜湊」先移除只代表執行時間的欄位（started_at 等）與程式 SHA，同一份程式與資料在任何時間重跑都相同。
# 用法: python scripts/repro/offline_evidence_bundle.py --out <dir>
#       python scripts/repro/offline_evidence_bundle.py --verify <dir>   # 重跑並比對；不一致時 exit 1
# 邊界: 不讀正式資料、不讀 sealed holdout、不啟動 Gate D、不呼叫任何模型。
import argparse
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VOLATILE = {"started_at", "generated_at", "timestamp", "code_sha", "worktree_dirty"}


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def stable_hash(value):
    return hashlib.sha256(json.dumps(_strip(value), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def collect():
    """在本程序內重跑全部離線評估；回傳 {名稱: 結果}。"""
    import scripts.study_r2_eval as ev
    from scripts import translation_guard_benchmark as bench
    dataset, digest = ev.load_dataset()
    faithful = ev.run(dataset, lambda item: ev.FaithfulStub(item), "faithful")
    adversarial = ev.run(dataset, lambda item: ev.AdversarialStub(item), "adversarial")
    return {
        "study_eval_negative_controls": {"nature": "TOOL SELF-CHECK on stubs; not a model-quality result",
                                         "dataset_sha256": digest, "controls": ev.negative_controls(dataset)},
        "study_eval_stub_gates": {"nature": "TOOL SELF-CHECK on stubs; not a model-quality result", "dataset_sha256": digest,
                                  "faithful": {"metrics": faithful["metrics"], "gates": faithful["gates"]},
                                  "adversarial": {"metrics": adversarial["metrics"], "gates": adversarial["gates"]}},
        "translation_guard_benchmark": _quiet(bench.main, []),
    }


def _quiet(fn, *args):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


def build(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    results = collect()
    manifest = {"schema": "roothinks.offline_evidence_bundle/1", "code_sha": _git("rev-parse", "HEAD"),
                "worktree_dirty": bool(_git("status", "--porcelain")), "python": platform.python_version(),
                "outputs": {}}
    for name, value in results.items():
        path = out / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest["outputs"][name] = {"file": path.name, "stable_sha256": stable_hash(value)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    fresh = collect()
    report = {"bundle_code_sha": manifest.get("code_sha"), "current_code_sha": _git("rev-parse", "HEAD"), "outputs": {}}
    for name, meta in manifest["outputs"].items():
        stored = json.loads((directory / meta["file"]).read_text(encoding="utf-8"))
        report["outputs"][name] = {
            "file_matches_manifest": stable_hash(stored) == meta["stable_sha256"],   # 檔案有沒有被改過
            "rerun_matches": name in fresh and stable_hash(fresh[name]) == meta["stable_sha256"],  # 重跑結果是否相同
        }
    report["ok"] = all(v["file_matches_manifest"] and v["rerun_matches"] for v in report["outputs"].values())
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--out")
    group.add_argument("--verify")
    args = ap.parse_args(argv)
    if args.out:
        print(json.dumps(build(args.out), ensure_ascii=False, indent=1))
        return 0
    report = verify(args.verify)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

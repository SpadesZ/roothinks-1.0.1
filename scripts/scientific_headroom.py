#!/usr/bin/env python
# Roothinks source maintenance contract
# 檔案路徑: scripts/scientific_headroom.py
# 模組定位: 維運/資料處理 CLI 層；由人工或隔離測試明確執行，不是常駐 request path。
# 主要責任: 依 docs/engineering/SCI_HEADROOM_PROTOCOL_2026-10-26.md，量凍結引擎在 gold SUPPORT／CONTRADICT 主張上的天花板分類。
# 上下游: scientific_benchmark.py 的 inputs、predictions 與 evaluator-only gold -> 本工具 -> JSON（只含 id 與計數）。
# 維護邊界: 只讀三個指定檔案；不改引擎、不讀 sealed holdout、不輸出原文。
# 驗證: python scripts/scientific_headroom.py <inputs.jsonl> <predictions.jsonl> <gold.jsonl> <new-output.json>
#路徑(./scripts/scientific_headroom.py) #版本 v0.1 #更版時間 20261026
import collections
import hashlib
import json
import sys
from pathlib import Path

TRIGGER = {'unsupported_sentence_structure', 'different_arguments_or_conditions'}


def read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def gold_label(row):
    labels = {group['label'] for groups in row['evidence'].values() for group in groups}
    return next(iter(labels)) if len(labels) == 1 else 'NEI' if not labels else 'AMBIGUOUS'


def evidence_comparisons(source_rows, prediction, expected):
    """回傳落在 gold 證據句上的 comparison（與 scientific_benchmark.score 相同的重疊判定）。"""
    hits = []
    for claim in prediction['report']['claims']:
        for comparison in claim['comparisons']:
            source = source_rows[comparison['source_id']]
            for sentence in source['sentences']:
                overlap = max(sentence['start'], comparison['start']) < min(sentence['end'], comparison['end'])
                if overlap and (str(source['source_id']), sentence['index']) in expected:
                    hits.append(comparison)
                    break
    return hits


def classify(inputs, predictions, gold):
    by_input = {row['id']: row for row in inputs}
    by_prediction = {row['id']: row for row in predictions}
    result = {label: collections.defaultdict(list) for label in ('SUPPORT', 'CONTRADICT')}
    reasons = {label: collections.Counter() for label in ('SUPPORT', 'CONTRADICT')}
    for row in gold:
        label = gold_label(row)
        if label not in result:
            continue
        prediction = by_prediction[row['id']]
        expected = {(str(doc), sentence) for doc, groups in row['evidence'].items() for group in groups
                    for sentence in group['sentences']}
        sources = {source['source_id']: source for source in by_input[row['id']]['sources']}
        hits = evidence_comparisons(sources, prediction, expected)
        reasons[label].update(comparison['reason'] for comparison in hits)
        if prediction['label'] == label:
            category = 'H0_already_correct'
        elif not hits:
            category = 'H3_not_retrieved'
        elif any(comparison['reason'] in TRIGGER for comparison in hits):
            category = 'H1_structurally_reachable'
        else:
            category = 'H2_blocked_by_conservative_gates'
        result[label][category].append(row['id'])
    return result, reasons


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(inputs, predictions, gold, output):
    if Path(output).exists():
        raise SystemExit('輸出檔必須是新的')
    result, reasons = classify(read(inputs), read(predictions), read(gold))
    counts = {label: {k: len(v) for k, v in sorted(groups.items())} for label, groups in result.items()}
    h1 = sum(counts[label].get('H1_structurally_reachable', 0) for label in counts)
    h3 = sum(counts[label].get('H3_not_retrieved', 0) for label in counts)
    decision = ('Gate D 選項 1 不可能在 dev 上過 G6' if h1 < 6 else 'Gate D 選項 1 的天花板 ≥ 6，值得在核准後實測')
    if h3 > h1:
        decision += '；沒檢索到的主張多於結構可救，檢索應優先'
    report = dict(protocol='docs/engineering/SCI_HEADROOM_PROTOCOL_2026-10-26.md', diagnostic_only=True,
                  inputs_sha256=digest(inputs), predictions_sha256=digest(predictions), gold_sha256=digest(gold),
                  counts=counts, h1_total=h1, h3_total=h3, decision=decision,
                  evidence_comparison_reasons={label: dict(c.most_common()) for label, c in reasons.items()},
                  ids={label: {k: sorted(v) for k, v in groups.items()} for label, groups in result.items()})
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(counts=counts, h1_total=h1, h3_total=h3, decision=decision), ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) != 5:
        raise SystemExit(__doc__ or '用法見檔頭')
    main(*sys.argv[1:])

# Roothinks source maintenance contract
# 檔案路徑: scripts/translation_guard_benchmark.py
# 目的: 量測翻譯保護層（translation_guard）相對舊作法（精確取代）的效果。
# 性質: **模擬**。雲端沒有 torch（NLLB 無法執行）、線上 Google 回 403，因此用「模擬翻譯引擎」重現已知的佔位符損壞型態：
#       原樣保留、加空白、改大小寫＋全形底線、整個丟掉、改動數字。結果只說明保護層的機制，不代表任何真引擎的翻譯品質。
# 語料: 40 句本專案自寫的合成科學句（公式、URL、DOI、引用、上下標、單位數字）。
# 執行: python scripts/translation_guard_benchmark.py [--out result.json]
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core_pro.literature import translation_guard as guard  # noqa: E402

CORPUS = [
    "The energy is $E = mc^2$ for the rest frame [12].",
    "We minimize $\\|x - y\\|_2$ subject to $x \\ge 0$, see https://example.org/method.",
    "Yield rose to 78 percent at pH 6.5 (Smith et al., 2020).",
    "Samples were annealed at 10² Pa and measured at 300 K [3, 4].",
    "The H₂O content was below 0.2 percent [5–7].",
    "The dataset is available at doi:10.1234/abcd.5678 under CC-BY.",
    "Let \\(\\alpha\\) denote the learning rate and \\(\\beta\\) the momentum.",
    "The loss $$L = \\sum_i (y_i - \\hat y_i)^2$$ was minimized for 40 epochs.",
    "Accuracy reached 91.2 F1 on the in-domain test set (Lee and Park, 2019).",
    "The model of Chen et al. (2021) predicts a 15 percent drop by 2080 [8].",
    "We report $p = 0.003$ before correction for 20000 variants.",
    "Resistivity at 10³ Pa exceeded that at 10² Pa by 12 percent.",
    "The CO₂ flux was 3.4 mol per square metre per day [21].",
    "Code is at https://github.com/example/repo and data at 10.5281/zenodo.123456.",
    "The bound $\\epsilon < 10^{-3}$ held in 95 percent of trials [2].",
    "Participants (n = 24) were tested twice (Garcia, 2018).",
    "The ratio $a/b$ stayed near 1.5 across all 3 runs.",
    "We follow the protocol in [14–16] with $T = 300$ K.",
    "Plasma levels rose linearly between 10 and 50 mg [9].",
    "The constant \\[k_B T / h\\] sets the time scale.",
    "Fe₃O₄ nanoparticles were dispersed in water [11].",
    "The coefficient $\\gamma = 0.8$ was assumed (Novak et al., 2017).",
    "See doi: 10.1000/xyz123 for the full derivation.",
    "Mean error fell from 8.1 to 6.4 percent [17, 18].",
    "The term $\\nabla \\cdot \\mathbf{u} = 0$ enforces incompressibility.",
    "Detection required SNR above 5 within 2 arcseconds [6].",
    "Grain size stayed near 12 micrometres after rolling (Ito, 2022).",
    "The x² term dominated for x above 4.",
    "We used $N = 600$ participants and a 6-month follow-up [30].",
    "LDL fell by 25 percent at 12 weeks (Brown and Silva, 2016).",
    "The circuit used a 4.7 kΩ resistor; see https://example.com/schematic.pdf.",
    "Equation $f(x) = \\int_0^x g(t)\\,dt$ defines the cumulative response.",
    "Counts were normalized per million words [4].",
    "Surface temperature rose by 0.3 C during the 2021 heatwave.",
    "The m³ volume was corrected by 2 percent [7, 12, 19].",
    "We set $\\lambda = 10^{-4}$ for all 5 folds (Kim et al., 2023).",
    "The pₓ component vanished at 40 N (Rossi, 2015).",
    "Doses above 50 mg were not studied [22].",
    "Hardness increased by 18 percent; see 10.1016/j.actamat.2020.01.001.",
    "The ratio $R_{1/2}$ was 0.45 in restored sites [1].",
]


def old_restore(text, mapping):
    """舊作法：只做精確取代（literature_translator 改版前的 _restore_nonlinguistic_segments）。"""
    out = text
    for token, original in mapping.items():
        out = out.replace(token, original)
    return out


def engine_faithful(masked, i):
    return "【譯】" + masked


def engine_spacing(masked, i):
    return "【譯】" + re.sub(r"__RTK_KEEP_(\d+)__", r"__ RTK_KEEP_\1 __", masked)


def engine_case_fullwidth(masked, i):
    return "【譯】" + re.sub(r"__RTK_KEEP_(\d+)__", lambda m: f"＿＿rtk_keep_{m.group(1)}＿＿", masked)


def engine_drop(masked, i):
    tokens = re.findall(r"__RTK_KEEP_\d+__", masked)
    return "【譯】" + (masked.replace(tokens[-1], "", 1) if tokens else masked)


def engine_digit(masked, i):
    return "【譯】" + re.sub(r"(?<![\w_])(\d+)(?![\w_])", lambda m: str(int(m.group(1)) + 1), masked, count=1)


ENGINES = {"faithful": engine_faithful, "spacing": engine_spacing, "case_fullwidth": engine_case_fullwidth,
           "drop_last_token": engine_drop, "digit_change": engine_digit}


def evaluate(extended):
    results = {}
    for name, engine in ENGINES.items():
        stats = {"sentences": 0, "spans": 0, "old_spans_preserved": 0, "new_spans_preserved": 0,
                 "old_silent_loss_sentences": 0, "new_silent_loss_sentences": 0, "new_loss_flagged_sentences": 0,
                 "number_change_detected": 0, "number_changed_sentences": 0}
        for i, src in enumerate(CORPUS):
            masked, mapping = guard.protect(src, extended=extended)
            out_raw = engine(masked, i)
            old = old_restore(out_raw, mapping)
            new, report = guard.restore(out_raw, mapping, source=src)
            stats["sentences"] += 1
            stats["spans"] += len(mapping)
            stats["old_spans_preserved"] += sum(1 for o in mapping.values() if o in old)
            stats["new_spans_preserved"] += sum(1 for o in mapping.values() if o in new.split("〔")[0])
            old_lost = any(o not in old for o in mapping.values())
            stats["old_silent_loss_sentences"] += int(old_lost)  # 舊作法遺失時沒有任何標示
            new_lost_unflagged = any(o not in new for o in mapping.values())
            stats["new_silent_loss_sentences"] += int(new_lost_unflagged)
            stats["new_loss_flagged_sentences"] += int(bool(report.lost))
            if name == "digit_change":
                changed = guard.number_mismatch(src, out_raw.replace("【譯】", ""), mapping)
                plain_changed = out_raw != engine_faithful(masked, i)
                stats["number_changed_sentences"] += int(plain_changed)
                stats["number_change_detected"] += int(bool(changed) and plain_changed)
        results[name] = stats
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    result = {"nature": "SIMULATION of placeholder damage; not a measurement of any real translation engine",
              "corpus_sentences": len(CORPUS), "base_patterns": evaluate(False), "extended_patterns": evaluate(True)}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return result


if __name__ == "__main__":
    main()

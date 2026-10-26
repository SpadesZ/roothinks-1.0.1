# Roothinks source maintenance contract
# 目的：翻譯保護層（translation_guard）的行為：基本遮罩與舊作法完全相同（相容）；佔位符變形可還原；
#       真的遺失時補回並標示（不默默消失）；數字變動可偵測；擴充保護預設關閉；HybridTranslator 已委派。
# 驗證：python -m pytest test/unit/test_translation_guard.py -q
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core_pro.literature import translation_guard as guard
from scripts import translation_guard_benchmark as bench

OLD_PATTERNS = [r"\$\$[\s\S]*?\$\$", r"\\\([\s\S]*?\\\)", r"\\\[[\s\S]*?\\\]", r"\$[^$\n]{1,200}\$",
                r"https?://\S+", r"doi:\s*\S+", r"10\.\d{4,9}/\S+"]


def old_protect(text):
    mapping, idx = {}, [0]

    def repl(m):
        token = f"__RTK_KEEP_{idx[0]}__"
        mapping[token] = m.group(0)
        idx[0] += 1
        return token
    return re.sub("|".join(f"({p})" for p in OLD_PATTERNS), repl, text), mapping


@pytest.mark.parametrize("text", bench.CORPUS)
def test_base_masking_is_identical_to_the_previous_implementation(monkeypatch, text):
    monkeypatch.delenv("TRANSLATION_GUARD_EXTENDED", raising=False)
    assert guard.protect(text) == old_protect(text)


def test_extended_protection_is_off_by_default_and_opt_in(monkeypatch):
    text = "Samples at 10² Pa improved [3, 4] (Smith et al., 2020)."
    monkeypatch.delenv("TRANSLATION_GUARD_EXTENDED", raising=False)
    assert guard.protect(text)[1] == {}
    monkeypatch.setenv("TRANSLATION_GUARD_EXTENDED", "1")
    assert set(guard.protect(text)[1].values()) == {"10²", "[3, 4]", "(Smith et al., 2020)"}


@pytest.mark.parametrize("damaged", ["__ RTK_KEEP_0 __", "__rtk_keep_0__", "＿＿RTK＿KEEP＿0＿＿", "_ RTK KEEP 0 _"])
def test_damaged_placeholders_are_recognised(damaged):
    masked, mapping = guard.protect("Energy $E = mc^2$ holds.")
    out, report = guard.restore(masked.replace("__RTK_KEEP_0__", damaged), mapping)
    assert "$E = mc^2$" in out and report.ok and "〔" not in out


def test_lost_spans_are_reappended_and_flagged_not_silently_dropped():
    masked, mapping = guard.protect("See $a+b$ and https://x.org/p now.")
    out, report = guard.restore("請看與現在。", mapping)
    assert report.lost == ["$a+b$", "https://x.org/p"]
    assert out.startswith("請看與現在。") and "翻譯時遺失、已補回的原文片段" in out and "$a+b$" in out


def test_number_changes_are_reported_but_numbers_inside_protected_spans_are_ignored():
    masked, mapping = guard.protect("Yield was 78 percent at $T = 300$ K.")
    out, report = guard.restore(masked.replace("78", "87"), mapping, source="Yield was 78 percent at $T = 300$ K.")
    assert report.number_mismatch == {"78": 1}


def test_hybrid_translator_delegates_to_the_guard(monkeypatch):
    from app.core_pro.literature.literature_translator import HybridTranslator
    translator = HybridTranslator.__new__(HybridTranslator)  # 不初始化引擎
    masked, mapping = translator._protect_nonlinguistic_segments("Energy $E = mc^2$ holds.")
    assert masked == "Energy __RTK_KEEP_0__ holds."
    assert translator._restore_nonlinguistic_segments("能量 __ rtk_keep_0 __ 成立。", mapping) == "能量 $E = mc^2$ 成立。"
    assert translator._restore_nonlinguistic_segments("沒有佔位符", {}) == "沒有佔位符"


def test_benchmark_claims_are_reproducible():
    result = bench.main([])
    base = result["base_patterns"]
    assert base["spacing"]["old_spans_preserved"] == 0 and base["spacing"]["new_spans_preserved"] == base["spacing"]["spans"]
    assert base["drop_last_token"]["old_silent_loss_sentences"] > 0 and base["drop_last_token"]["new_silent_loss_sentences"] == 0
    assert base["faithful"]["new_loss_flagged_sentences"] == 0, "沒有損壞時不能誤報"
    assert result["nature"].startswith("SIMULATION")


# ---------------- R4 邊界案例（重複公式、改壞的佔位符、還原位置）----------------

def r3_lost(text, mapping):
    """R3 版的遺失判定（以「原文片段是否出現在譯文」）：作為負向控制，證明舊判定看不到重複片段的遺失。"""
    out = text
    for token, original in mapping.items():
        out = out.replace(token, original)
    return [o for o in mapping.values() if o not in out]


def test_repeated_identical_formula_loss_is_detected_where_r3_was_blind():
    masked, mapping = guard.protect("Set $x$ to zero, then $x$ again.")
    damaged = masked.replace("__RTK_KEEP_1__", "")
    assert r3_lost(damaged, mapping) == [], "負向控制：舊判定以為沒有遺失"
    out, report = guard.restore(damaged, mapping)
    assert report.lost == ["$x$"] and "翻譯時遺失" in out


def test_renumbered_placeholder_is_restored_in_place_not_appended():
    masked, mapping = guard.protect("A $a$ then B $b$ end.")
    out, report = guard.restore("甲 __RTK_KEEP_0__ 然後 乙 __RTK_KEEP_7__ 結束。", mapping)
    assert out == "甲 $a$ 然後 乙 $b$ 結束。" and report.repositioned == 1 and report.ok


def test_placeholder_without_number_and_stripped_underscores():
    masked, mapping = guard.protect("A $a$ then $b$.")
    out, report = guard.restore("甲 RTK KEEP 0 然後 __RTK_KEEP__。", mapping)
    assert out == "甲 $a$ 然後 $b$。" and report.restored_variant == 1 and report.repositioned == 1


def test_duplicated_placeholder_is_not_inserted_twice():
    masked, mapping = guard.protect("A $a$ end.")
    out, report = guard.restore("甲 __RTK_KEEP_0__ __RTK_KEEP_0__ 結束。", mapping)
    assert out.count("$a$") == 1 and report.duplicated == 1


def test_ambiguous_orphans_fall_back_to_flagged_append_without_residue():
    masked, mapping = guard.protect("A $a$ then $b$ and $c$.")
    out, report = guard.restore("__RTK_KEEP__ 甲 __RTK_KEEP_1__ __RTK_KEEP__ __RTK_KEEP_2__", mapping)
    assert report.lost == ["$a$"] and "RTK" not in out.upper().replace("〔", "")


def test_restored_originals_are_not_rescanned_as_placeholders():
    masked, mapping = guard.protect("See https://x.org/rtk_keep_1_ and $b$.")
    out, report = guard.restore(masked, mapping)
    assert out == "See https://x.org/rtk_keep_1_ and $b$." and report.ok and report.duplicated == 0


def test_faithful_engine_has_no_false_flags_on_repeated_spans():
    result = bench.main([])
    rep = result["repeated_spans"]
    assert rep["faithful"]["new_loss_flagged_sentences"] == 0 and rep["faithful"]["new_repositioned"] == 0
    assert rep["drop_last_token"]["new_silent_loss_sentences"] == 0
    assert all(v["new_token_residue_sentences"] == 0 for v in rep.values())
    assert result["base_patterns"]["renumber_last_token"]["new_spans_preserved"] == result["base_patterns"]["renumber_last_token"]["spans"]

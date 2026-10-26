# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/translation_guard.py
# 模組定位: Literature 翻譯的保護層；翻譯前把不該翻的片段換成佔位符，翻譯後容錯還原並檢查有沒有遺失。
# 主要責任: 公式、URL、DOI 一律保護（與舊行為相同）；引用標號、作者年份、上下標與化學式只在 TRANSLATION_GUARD_EXTENDED=1 時保護。
#           翻譯引擎常把佔位符改寫（加空白、改大小寫、全形底線）；舊作法只做精確取代，公式會默默消失。
#           本層接受這些變體；仍找不到的片段補回譯文尾端並明確標示，不讓讀者以為譯文完整。
# 上下游: literature_translator.HybridTranslator 的 _protect／_restore 委派到這裡；不呼叫任何翻譯引擎、不讀寫檔案。
# 授權: 概念與 BabelDOC 等工具的佔位符作法相同，但程式碼為本專案獨立撰寫，沒有引入 AGPL 程式碼或相依。
# 驗證: python -m pytest test/unit/test_translation_guard.py -q；量測：python scripts/translation_guard_benchmark.py
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger("TranslationGuard")

TOKEN_FMT = "__RTK_KEEP_{}__"
LOST_MARKER = "〔翻譯時遺失、已補回的原文片段：{}〕"

# 舊行為（不變）：公式、URL、DOI。
BASE_PATTERNS = [
    r"\$\$[\s\S]*?\$\$",
    r"\\\([\s\S]*?\\\)",
    r"\\\[[\s\S]*?\\\]",
    r"\$[^$\n]{1,200}\$",
    r"https?://\S+",
    r"doi:\s*\S+",
    r"10\.\d{4,9}/\S+",
]
# 擴充（預設關閉，需在真引擎上量測後才開）：引用、作者年份、上下標／化學式。
EXTENDED_PATTERNS = [
    r"\[\d+(?:\s*[,–—-]\s*\d+)*\]",
    r"\([A-Z][A-Za-z'’-]+(?: et al\.| and [A-Z][A-Za-z'’-]+)?,? (?:19|20)\d{2}[a-z]?\)",
    r"[A-Za-z0-9()]*[⁰-₟²³¹][A-Za-z0-9()⁰-₟²³¹]*",
]
# 佔位符的常見變形：前後底線數量（含被整個刪掉）、空白、大小寫、全形底線。數字可能被改掉或刪掉（孤兒佔位符）。
_TOKEN_ANY = re.compile(r"(?:[_＿]{1,3}\s*)?rtk\s*[_＿]?\s*keep\s*[_＿]?\s*(\d*)(?:\s*[_＿]{1,3})?", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def extended_enabled():
    return os.environ.get("TRANSLATION_GUARD_EXTENDED", "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class GuardReport:
    """一次還原的結果。

    lost：翻譯後找不到、已補回尾端並標示的原文片段（依佔位符編號，不依文字比對；重複的相同公式各算一次）。
    duplicated：同一個佔位符被引擎重複輸出的次數（只還原一次，多的刪掉）。
    repositioned：佔位符編號被改壞，但「壞掉的位置」與「遺失的片段」一一對應，於原位置還原的數量。
    """
    protected: int = 0
    restored_exact: int = 0
    restored_variant: int = 0
    repositioned: int = 0
    duplicated: int = 0
    lost: list = field(default_factory=list)
    number_mismatch: dict = field(default_factory=dict)

    @property
    def ok(self):
        return not self.lost


def protect(text, extended=None):
    """回傳 (遮罩後文字, {token: 原文})。extended=None 時依環境變數。"""
    if not text:
        return text, {}
    patterns = BASE_PATTERNS + (EXTENDED_PATTERNS if (extended_enabled() if extended is None else extended) else [])
    combined = re.compile("|".join(f"(?:{p})" for p in patterns))
    mapping = {}

    def repl(match):
        token = TOKEN_FMT.format(len(mapping))
        mapping[token] = match.group(0)
        return token

    return combined.sub(repl, text), mapping


_ORPHAN = "\x00RTK_ORPHAN\x00"


def restore(text, mapping, source=None):
    """容錯還原。回傳 (還原後文字, GuardReport)。

    單次掃描（還原進去的原文不會再被當成佔位符）：
    1. 精確佔位符與常見變形 → 依編號換回原文；每個編號只還原一次，重複的刪掉並計入 duplicated。
    2. 編號被刪掉或改成不存在的編號 → 孤兒位置。若孤兒數量剛好等於遺失數量，依出現順序在原位置還原（repositioned）；
       否則刪掉孤兒，遺失片段補在尾端並標示。不默默丟掉公式或引用，也不留下佔位符殘渣。
    3. 遺失以「編號是否被還原」判定，而不是「文字是否出現」：句中有兩個相同公式時，少了一個也會被抓到。
    4. 若給了 source，另外比對原文與譯文的數字（只報告，不改寫：中文可能合理地改寫數字格式）。
    """
    report = GuardReport(protected=len(mapping or {}))
    out = text or ""
    if mapping:
        index = {int(re.search(r"\d+", t).group(0)): (t, original) for t, original in mapping.items()}
        used = set()

        def fix(match):
            digits = match.group(1)
            n = int(digits) if digits else None
            if n in index and n not in used:
                used.add(n)
                if match.group(0) == index[n][0]:
                    report.restored_exact += 1
                else:
                    report.restored_variant += 1
                return index[n][1]
            if n in index:
                report.duplicated += 1
                return ""
            return _ORPHAN

        out = _TOKEN_ANY.sub(fix, out)
        missing = [n for n in sorted(index) if n not in used]
        orphans = out.count(_ORPHAN)
        if missing and orphans == len(missing):
            for n in missing:
                out = out.replace(_ORPHAN, index[n][1], 1)
            report.repositioned = len(missing)
            missing = []
        out = out.replace(_ORPHAN, "")
        report.lost = [index[n][1] for n in missing]
        if report.lost:
            out = out.rstrip() + " " + LOST_MARKER.format("；".join(report.lost))
            logger.warning("[TranslationGuard] %d protected span(s) lost in translation and re-appended", len(report.lost))
    if source is not None:
        report.number_mismatch = number_mismatch(source, out, mapping)
    return out, report


def number_mismatch(source, translated, mapping=None):
    """原文中出現、譯文中沒有出現的數字（受保護片段內的數字除外：那些已逐字還原）。"""
    plain = source
    for original in (mapping or {}).values():
        plain = plain.replace(original, " ")
    wanted = {}
    for n in _NUMBER.findall(plain):
        wanted[n] = wanted.get(n, 0) + 1
    got = {}
    for n in _NUMBER.findall(translated or ""):
        got[n] = got.get(n, 0) + 1
    return {n: c - got.get(n, 0) for n, c in wanted.items() if got.get(n, 0) < c}

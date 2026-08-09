# Roothinks source maintenance contract
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/services/text_tokenize.py
# 產生時間: 2026-07-19
# 版本: v0.1
# 模組定位:
#   共用 lexical tokenizer（evidence index 與 context inject 共用同一份，
#   避免 query 端與 document 端斷詞不一致）。
# 主要責任: 提供 Evidence Index 共用的 Unicode-aware lexical tokenizer，確保建索引與查詢採相同規則。
#   1. 拉丁字母/數字詞維持整詞。
#   2. CJK 連續字串切成 character bigram，讓中文子字串查詢可命中。
# 維護提醒:
#   - query 與 document 必須用同一個 tokenize，改這裡等於同時改兩端。
# -----------------------------------------------------------------------------

from __future__ import annotations

import re

_CJK_START = "一"
_CJK_END = "鿿"

# CJK run 優先擷取；其餘 word 字元（排除 CJK）為一般詞。
_TOKEN_RE = re.compile(r"[一-鿿]+|[^\W一-鿿]+")


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for match in _TOKEN_RE.finditer(str(text or "").lower()):
        token = match.group(0)
        if _CJK_START <= token[0] <= _CJK_END:
            if len(token) == 1:
                out.append(token)
            else:
                # bigram：查詢「深度學習」可命中文件「深度學習模型」
                out.extend(token[i : i + 2] for i in range(len(token) - 1))
        else:
            out.append(token)
    return out

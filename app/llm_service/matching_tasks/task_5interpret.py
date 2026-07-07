# 檔案路徑: roothinks/app/llm_service/matching_tasks/task_5interpret.py
# 產生時間: 2026-07-05 04:20 +08:00
# 版本: v1.1-Lite
# 模組定位:
#   Task 5: Interpretation & Summarization(不含翻譯)。
#   Merge page JSONs -> full_text.json(Fusion);structured summary。
# 主要責任:
#   1. run_fusion():逐頁 03_recognizes -> 05_interprets/fusion/full_text.json。
#   2. run_summary():全文 -> summary.json。
# 維護提醒:
#   - v1.1 修復 _simple_merge 的「無條件 Body-Body 合併」:
#     實測(CTC 論文)首頁 12 個 block 全是 Body,被壓成單一 3730 字大塊,
#     是 UI「title+authors+abstract 黏成一塊」與 reflow 首頁丟失的資料源。
#     現在:heading 樣式的短 Body(<150 字元且非句號結尾)與 is_page_noise
#     block 不參與合併,只有真正的正文段落才會併頁內接續。
#   - 徹底修復依賴 VNS v3.17 的 heading type(MainTitle/Subtitle);
#     舊論文需重跑 VNS(seg_version 快取會自動失效)。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_fusion_merge.py -q
# ------------------------------------------------------------------------------
"""
Task 5: Interpretation & Summarization (不含翻譯)
1. Merge page JSONs → full_text.json (Fusion)
2. Generate structured summary → summary.json
"""
import json
import os
import re
import logging
import app.llm_service.llm_bus as llm_bus
from app.core_pro.storage_layout import resolve_literature_paper_dir
from app.core_pro.literature.literature_processing_ops import retag_section_titles

logger = logging.getLogger(__name__)


class Interpreter:
    TASK_ID = "task_5interpret"

    def run_fusion(self, pid, paper_id, data_root):
        paper_dir = resolve_literature_paper_dir(
            data_root,
            pid,
            paper_id,
            for_write=True,
            migrate_legacy=True,
        )
        recog_dir = os.path.join(paper_dir, "03_recognizes")
        fusion_dir = os.path.join(paper_dir, "05_interprets", "fusion")
        os.makedirs(fusion_dir, exist_ok=True)

        out_path = os.path.join(fusion_dir, "full_text.json")
        if not os.path.exists(recog_dir):
            return None

        # [Fix Bug4] per-page fixed/raw fallback：每頁優先用 fixed，沒有才用 raw
        # 避免「有任何 fixed 就全用 fixed」的全或無邏輯，確保 partial-fix 後也能拼完整全文
        all_raws = {
            int(re.search(r'(\d+)', f).group(1)): f
            for f in os.listdir(recog_dir)
            if f.endswith('_raw.json') and re.search(r'(\d+)', f)
        }
        all_fixed = {
            int(re.search(r'(\d+)', f).group(1)): f
            for f in os.listdir(recog_dir)
            if f.endswith('_fixed.json') and re.search(r'(\d+)', f)
        }
        all_page_nums = sorted(set(all_raws) | set(all_fixed))

        pages_content = []
        for page_num in all_page_nums:
            fname = all_fixed.get(page_num) or all_raws.get(page_num)
            if not fname:
                continue
            p_path = os.path.join(recog_dir, fname)
            try:
                with open(p_path, 'r', encoding='utf-8') as f:
                    blocks = json.load(f)
                    merged = self._simple_merge(blocks)
                    retag_section_titles(merged)
                    pages_content.append({"page": page_num, "blocks": merged})
            except Exception:
                pass

        full_data = {
            "paper_id": paper_id,
            "fusion_timestamp": "",
            "content": pages_content
        }

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(full_data, f, ensure_ascii=False, indent=2)

        return out_path

    @staticmethod
    def _is_mergeable_body(b):
        """只有真正的正文段落可參與合併。
        heading 樣式短塊(標題/章節名/作者列常 <150 字且非句號結尾)
        與頁面雜訊塊必須保持獨立,否則首頁 title+abstract 會黏成大塊。"""
        if not isinstance(b, dict) or b.get('type') != 'Body':
            return False
        if bool(b.get('is_page_noise')):
            return False
        t = str(b.get('content', '') or '').strip()
        if len(t) < 150 and not re.search(r'[.!?。!?]$', t):
            return False
        return True

    def _simple_merge(self, blocks):
        if not blocks:
            return []
        merged = []
        curr = None

        for b in blocks:
            if not curr:
                curr = b
                continue

            if self._is_mergeable_body(curr) and self._is_mergeable_body(b):
                curr['content'] += " " + b.get('content', '')
                curr['bbox'][0] = min(curr['bbox'][0], b['bbox'][0])
                curr['bbox'][1] = min(curr['bbox'][1], b['bbox'][1])
                curr['bbox'][2] = max(curr['bbox'][2], b['bbox'][2])
                curr['bbox'][3] = max(curr['bbox'][3], b['bbox'][3])
            else:
                merged.append(curr)
                curr = b

        if curr:
            merged.append(curr)
        return merged

    def run_summary(self, fusion_path):
        logger.info(f"[Task5] run_summary for {fusion_path}")

        if not os.path.exists(fusion_path):
            return

        try:
            with open(fusion_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[Task5] Load fusion failed: {e}")
            return

        raw_text = ""
        for page in data.get('content', []):
            for b in page.get('blocks', []):
                if b.get('content'):
                    raw_text += b['content'] + "\n"

        prompt = f"""
        Summarize the following academic paper into structured JSON.
        
        Text:
        {raw_text[:12000]}... (Truncated)
        
        Output Strict JSON:
        {{
            "abstract_zh": "中文摘要 (約 300 字)",
            "key_findings": ["Finding 1", "Finding 2", "Finding 3"],
            "conclusion": "結論總結"
        }}
        """

        try:
            bus = llm_bus.get_bus()
            success, reply = bus.dispatch_task(self.TASK_ID, prompt, priority=5)
        except Exception as e:
            logger.error(f"[Task5] LLM dispatch failed: {e}")
            success = False
            reply = str(e)

        out_dir = os.path.dirname(os.path.dirname(fusion_path))
        out_path = os.path.join(out_dir, "summary.json")

        summary_data = {}
        if success:
            try:
                summary_data = self._parse_summary_payload(reply, raw_text)
            except Exception as e:
                logger.warning(f"[Task5] LLM summary parse failed, fallback synthetic: {e}")
                summary_data = self._generate_fallback_summary(raw_text)
                summary_data["mode"] = "fallback_llm_parse_error"
                summary_data["error"] = "summary_json_parse_failed"
                summary_data["raw_reply_excerpt"] = str(reply or "")[:600]
        else:
            summary_data = self._generate_fallback_summary(raw_text)
            summary_data["error"] = str(reply or "llm_dispatch_failed")[:300]

        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Task5] Save summary failed: {e}")

    def _strip_code_fence(self, text):
        out = str(text or "").strip()
        if out.startswith("```"):
            out = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", out)
            out = re.sub(r"\s*```$", "", out)
        return out.strip()

    def _extract_first_json_object(self, text):
        src = str(text or "")
        if not src:
            return ""

        in_str = False
        esc = False
        depth = 0
        start = -1
        for i, ch in enumerate(src):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        return src[start : i + 1]
        return ""

    def _normalize_summary_fields(self, payload, raw_text):
        data = payload if isinstance(payload, dict) else {}
        abstract_zh = str(data.get("abstract_zh", "") or "").strip()
        key_findings = data.get("key_findings", [])
        if not isinstance(key_findings, list):
            key_findings = [str(key_findings)]
        key_findings = [str(x).strip() for x in key_findings if str(x).strip()]
        conclusion = str(data.get("conclusion", "") or "").strip()

        if not abstract_zh and raw_text:
            abstract_zh = "自動提取，完整分析請參考全文。"
        if not key_findings:
            fallback = self._generate_fallback_summary(raw_text)
            key_findings = fallback.get("key_findings", [])
            if not conclusion:
                conclusion = str(fallback.get("conclusion", "") or "")

        out = {
            "abstract_zh": abstract_zh,
            "key_findings": key_findings[:5],
            "conclusion": conclusion,
        }
        # 僅保留必要欄位，避免非結構化回覆污染下游判定。
        if "mode" in data and str(data.get("mode") or "").strip():
            out["mode"] = str(data.get("mode")).strip()
        return out

    def _parse_summary_payload(self, reply, raw_text):
        clean = self._strip_code_fence(reply)
        if not clean:
            raise ValueError("empty_summary_reply")

        # 1) 直接當 JSON 解析
        try:
            data = json.loads(clean)
            return self._normalize_summary_fields(data, raw_text)
        except Exception:
            pass

        # 2) 回覆含雜訊時，擷取第一個 JSON object
        json_blob = self._extract_first_json_object(clean)
        if json_blob:
            try:
                data = json.loads(json_blob)
                return self._normalize_summary_fields(data, raw_text)
            except Exception:
                pass

        # 3) 最後再嘗試從簡單 key:value 文字抽取（容錯）
        manual = {}
        m_abs = re.search(r"abstract_zh\s*[:：]\s*(.+)", clean, flags=re.IGNORECASE)
        m_conc = re.search(r"conclusion\s*[:：]\s*(.+)", clean, flags=re.IGNORECASE)
        if m_abs:
            manual["abstract_zh"] = m_abs.group(1).strip()[:1000]
        if m_conc:
            manual["conclusion"] = m_conc.group(1).strip()[:1000]
        findings = re.findall(r"(?:key_findings?|finding)\s*[:：]\s*(.+)", clean, flags=re.IGNORECASE)
        if findings:
            manual["key_findings"] = [x.strip()[:300] for x in findings if x.strip()]
        if manual:
            manual["mode"] = "fallback_partial_parse"
            return self._normalize_summary_fields(manual, raw_text)

        raise ValueError("unable_to_parse_summary_json")

    def _generate_fallback_summary(self, text):
        try:
            sentences = [s.strip() for s in text.split('.') if s.strip()]
            key_sentences = sorted(sentences, key=len, reverse=True)[:3]
            return {
                "abstract_zh": "自動提取，完整分析請參考全文。",
                "key_findings": key_sentences[:3],
                "conclusion": sentences[-1] if sentences else "",
                "mode": "fallback_synthetic"
            }
        except:
            return {
                "abstract_zh": "無法生成摘要",
                "key_findings": [],
                "mode": "fallback_empty"
            }

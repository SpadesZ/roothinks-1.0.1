# Roothinks source maintenance contract
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 檔案路徑: roothinks/app/llm_service/matching_tasks/task_4cv.py
# 產生時間: 2026-07-05 01:40 +08:00
# 版本: v1.2
# 模組定位:
#   Task 4: CV Semantic Correction。metadata 抽取、術語 guide 生成、
#   OCR 文字塊批次修正(純文字 LLM,無 vision)。
# 主要責任: Task 4: CV Semantic Correction。
#   1. extract_metadata / generate_context_guide。
#   2. fix_batch():批次修正 OCR 錯字/斷字/空格。
# 維護提醒:
#   - v1.2 起 Equation block 明確排除於純文字修正之外:LLM 看不到圖,
#     對亂碼公式的「修正」只會是幻覺。低信心公式(equation_failed、
#     latex_confidence<0.6 或 equation_source 含 fallback)會在 fixed 檔
#     標記 needs_equation_review=true,由上層決定帶圖重試或人工複核,
#     原文一律保留不改。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_arbiter_equation_chain.py -q
# ------------------------------------------------------------------------------
"""
Task 4: CV Semantic Correction
1. Extract Metadata
2. Generate Context Guide
3. Fix OCR page by page
"""
import json
import os
import logging
import app.llm_service.llm_bus as llm_bus
from app.core_pro.storage_layout import resolve_literature_paper_dir

logger = logging.getLogger("Task4CV")


class SemanticCorrector:
    TASK_ID = "task_4cv"
    TEXTUAL_TYPES = {
        "title",
        "body",
        "text",
        "maintitle",
        "subtitle",
        "subsubtitle",
        "figurecaption",
        "tablecaption",
    }

    def _is_textual_block(self, block):
        if not isinstance(block, dict):
            return False
        t = str(block.get("type", "") or "").replace("_", "").strip().lower()
        # v1.2: 公式不進純文字修正——無 vision 的 LLM 只能幻覺式「修」公式。
        if t == "equation" or bool(block.get("is_equation")):
            return False
        if t in self.TEXTUAL_TYPES:
            return True
        if "caption" in t:
            return True
        content = str(block.get("content", "") or "").strip()
        return len(content) > 0 and t not in {"figure", "table"}

    @staticmethod
    def _is_low_confidence_equation(block):
        """低信心公式:OCR 失敗、信心不足或來自 fallback 引擎。"""
        if not isinstance(block, dict):
            return False
        t = str(block.get("type", "") or "").replace("_", "").strip().lower()
        if t != "equation" and not bool(block.get("is_equation")):
            return False
        if bool(block.get("equation_failed")):
            return True
        try:
            conf = float(block.get("latex_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.6:
            return True
        source = str(block.get("equation_source", "") or "").lower()
        return "fallback" in source or "failed" in source

    def extract_metadata(self, pid, paper_id, data_root):
        paper_dir = resolve_literature_paper_dir(
            data_root,
            pid,
            paper_id,
            for_write=False,
            migrate_legacy=True,
        )
        raw_dir = os.path.join(paper_dir, "03_recognizes")
        target_file = os.path.join(raw_dir, "text_1_raw.json")

        if not os.path.exists(target_file):
            return None

        content_snippet = ""
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for block in data[:10]:
                        content_snippet += block.get('content', '') + "\n"
        except:
            return None

        if not content_snippet.strip():
            return None

        prompt = f"""
        Extract academic metadata from the following text (OCR output from Page 1).
        
        Text:
        {content_snippet[:2000]}
        
        Output Strict JSON:
        {{
            "title": "Full Paper Title",
            "authors": "Author 1, Author 2...",
            "journal": "Journal Name (if found, else null)",
            "publish_date": "YYYY-MM-DD (or YYYY)"
        }}
        """

        bus = llm_bus.get_bus()
        success, reply = bus.dispatch_task(self.TASK_ID, prompt, priority=3)

        if success:
            try:
                clean = reply.replace("```json", "").replace("```", "").strip()
                return json.loads(clean)
            except:
                pass
        return None

    def generate_context_guide(self, pid, paper_id, data_root):
        paper_dir = resolve_literature_paper_dir(
            data_root,
            pid,
            paper_id,
            for_write=False,
            migrate_legacy=True,
        )
        raw_dir = os.path.join(paper_dir, "03_recognizes")
        if not os.path.exists(raw_dir):
            return None

        full_text = ""
        files = sorted([f for f in os.listdir(raw_dir) if f.endswith('_raw.json')])
        selected = files[:3] + files[-2:] if len(files) > 5 else files

        for fname in selected:
            try:
                with open(os.path.join(raw_dir, fname), 'r', encoding='utf-8') as f:
                    blocks = json.load(f)
                    for b in blocks:
                        if self._is_textual_block(b):
                            full_text += b.get('content', '') + "\n"
            except:
                pass

        prompt = f"""
        Analyze the following academic text fragments to build a 'Context Guide' for OCR correction.
        Identify specialized terminology, variable names, and proper nouns.
        
        Text:
        {full_text[:8000]}... (Truncated)
        
        Output Strict JSON:
        {{
            "terminology": ["term1", "term2", ...],
            "variables": ["var1", "var2", ...],
            "proper_nouns": ["name1", "name2", ...]
        }}
        """

        bus = llm_bus.get_bus()
        success, reply = bus.dispatch_task(self.TASK_ID, prompt, priority=5)

        guide_path = os.path.join(raw_dir, "temp", "full_text_context_guide.json")
        os.makedirs(os.path.dirname(guide_path), exist_ok=True)

        guide_data = {}
        if success:
            try:
                clean = reply.replace("```json", "").replace("```", "").strip()
                guide_data = json.loads(clean)
            except:
                guide_data = {"error": "JSON Parse Failed", "raw": reply}

        with open(guide_path, 'w', encoding='utf-8') as f:
            json.dump(guide_data, f, ensure_ascii=False, indent=2)

        return guide_path

    def fix_content(self, src_path, dst_path, guide_path=None):
        if not os.path.exists(src_path):
            return

        with open(src_path, 'r', encoding='utf-8') as f:
            blocks = json.load(f)

        guide_context = ""
        if guide_path and os.path.exists(guide_path):
            try:
                with open(guide_path, 'r', encoding='utf-8') as f:
                    g = json.load(f)
                    guide_context = f"Terminology Guide: {json.dumps(g)}"
            except:
                pass

        text_map = {}
        input_text = ""
        for i, b in enumerate(blocks):
            if self._is_textual_block(b) and len(b.get('content', '')) > 5:
                idx_key = str(i)
                text_map[idx_key] = b['content']
                input_text += f"[{idx_key}] {b['content']}\n"

        if not input_text:
            with open(dst_path, 'w', encoding='utf-8') as f:
                json.dump(blocks, f, indent=2, ensure_ascii=False)
            return

        prompt = f"""
        You are an academic editor. Fix OCR typos, hyphenation issues, and spacing errors.
        Use the provided Terminology Guide.
        
        {guide_context}
        
        Input: [ID] Text...
        Output: Strict JSON mapping ID to Fixed Text. {{ "0": "Fixed text...", "2": "..." }}
        
        Text Blocks:
        {input_text[:6000]}
        """

        bus = llm_bus.get_bus()
        success, reply = bus.dispatch_task(self.TASK_ID, prompt, priority=5)

        if success:
            try:
                clean = reply.replace("```json", "").replace("```", "").strip()
                fixed_map = json.loads(clean)
                for i, b in enumerate(blocks):
                    idx_key = str(i)
                    if idx_key in fixed_map:
                        b['content'] = fixed_map[idx_key]
                        b['source'] = b.get('source', '') + "_fixed"
            except Exception as e:
                print(f"[{self.TASK_ID}] Fix Parse Error: {e}")

        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        with open(dst_path, 'w', encoding='utf-8') as f:
            json.dump(blocks, f, ensure_ascii=False, indent=2)

    def fix_batch(self, page_pairs, guide_path=None):
        """
        批次修正多頁 OCR 內容 — 一次 LLM 呼叫處理多頁，大幅減少 API 次數。
        page_pairs: list of (src_path, dst_path)
        """
        if not page_pairs:
            return

        guide_context = ""
        if guide_path and os.path.exists(guide_path):
            try:
                with open(guide_path, 'r', encoding='utf-8') as f:
                    g = json.load(f)
                    guide_context = f"Terminology Guide: {json.dumps(g)}"
            except:
                pass

        # 讀入所有頁面的 block 資料
        all_pages_blocks = []
        for src_path, dst_path in page_pairs:
            if not os.path.exists(src_path):
                all_pages_blocks.append(None)
                continue
            with open(src_path, 'r', encoding='utf-8') as f:
                try:
                    all_pages_blocks.append(json.load(f))
                except:
                    all_pages_blocks.append(None)

        # 建立複合索引 "p{i}-{j}" → block
        text_map = {}   # "p0-3" → original content
        input_lines = []
        for pi, blocks in enumerate(all_pages_blocks):
            if blocks is None:
                continue
            for bi, b in enumerate(blocks):
                if self._is_textual_block(b) and len(b.get('content', '')) > 5:
                    key = f"p{pi}-{bi}"
                    text_map[key] = b['content']
                    input_lines.append(f"[{key}] {b['content']}")

        if not input_lines:
            # 沒有需修正的 block，直接複製
            for (src_path, dst_path), blocks in zip(page_pairs, all_pages_blocks):
                if blocks is not None:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    with open(dst_path, 'w', encoding='utf-8') as f:
                        json.dump(blocks, f, ensure_ascii=False, indent=2)
            return

        combined_input = "\n".join(input_lines)

        prompt = f"""
You are an academic editor. Fix OCR typos, hyphenation issues, and spacing errors in the following text blocks.
Use the Terminology Guide if provided.

{guide_context}

Input format: [pPAGE_IDX-BLOCK_IDX] text...
Output: Strict JSON mapping each key to its corrected text.
Example: {{"p0-0": "Corrected text...", "p1-3": "Another fix..."}}

Only include blocks that need correction. Omit unchanged blocks.

Text Blocks:
{combined_input[:8000]}
"""

        bus = llm_bus.get_bus()
        # [Fix Bug3] dispatch_task 本身可能拋例外，捕捉後視同 success=False
        try:
            success, reply = bus.dispatch_task(self.TASK_ID, prompt, priority=5)
        except Exception as _dispatch_err:
            logger.warning("[%s] fix_batch dispatch_task raised: %s", self.TASK_ID, _dispatch_err)
            success, reply = False, str(_dispatch_err)

        fixed_map = {}
        if success:
            try:
                clean = reply.replace("```json", "").replace("```", "").strip()
                fixed_map = json.loads(clean)
            except Exception as e:
                print(f"[{self.TASK_ID}] fix_batch Parse Error: {e}")

        # 把修正結果寫回各頁 dst
        for pi, ((src_path, dst_path), blocks) in enumerate(zip(page_pairs, all_pages_blocks)):
            if blocks is None:
                continue
            for bi, b in enumerate(blocks):
                key = f"p{pi}-{bi}"
                if key in fixed_map:
                    b['content'] = fixed_map[key]
                    b['source'] = b.get('source', '') + "_fixed"
                # v1.2: 低信心公式標記待複核,原文保留,供上層帶圖重試或人工確認。
                if self._is_low_confidence_equation(b):
                    b['needs_equation_review'] = True
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            with open(dst_path, 'w', encoding='utf-8') as f:
                json.dump(blocks, f, ensure_ascii=False, indent=2)

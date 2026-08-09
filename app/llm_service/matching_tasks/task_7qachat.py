# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/matching_tasks/task_7qachat.py
# 模組定位: LLM task 業務層；組合特定任務 prompt，經 dispatcher 呼叫已綁定模型。
# 主要責任: 依 Study evidence 回答閱讀問題，限制回答只使用當前專案/論文 context。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/llm_service/matching_tasks/task_7qachat.py) #版本 v2.0-ContextAware #更版時間 20260208-0215
# [MVP+Prototype Handoff Header]
# 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
# 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
# 與 Study Task7 context/focus_pids 行為調整相關，後續請由人類團隊做產品級測試與治理。
import json
import logging
import app.llm_service.llm_bus as llm_bus
# [Import] 引入 Loader 以獲取真實文獻內容
from app.core_pro.study import study_loader

# 設定 Log
logger = logging.getLogger("Task7_Tutor")

class TutorEngine:
    """
    [Task 7] 學習互動問答引擎 (Context-Aware Tutor)
    職責:
    1. 接收使用者的提問與「當前關注焦點」(Focus)。
    2. 從 Study Loader 提取對應的文獻片段 (Grounding Data)。
    3. 生成基於事實的回答，扮演學術家教角色。
    """

    def __init__(self):
        self.bus = llm_bus.get_bus()

    def process_query(self, query, context_payload):
        """
        處理對話請求
        :param query: 使用者的自然語言提問 (e.g. "這篇論文的學習率設為多少？")
        :param context_payload: 前端傳來的上下文資訊
               {
                   "focus_pids": ["pid1", "pid2"],  # 使用者當前關注的論文 ID 列表
                   "dimension": "Methodology",      # 當前所在的矩陣維度 (可選)
                   "history": [...]                 # (可選) 對話歷史
               }
        :return: 回覆字串 (String)
        """
        task_name = "task_7_qachat"
        
        # 1. 準備背景知識 (Context Retrieval)
        # 這是 RAG (Retrieval-Augmented Generation) 的輕量化實作
        knowledge_snippet = self._fetch_grounding_data(context_payload)
        user_notes = str(context_payload.get('user_notes', '') or '').strip()[:2800]
        matrix_cell_context = str(context_payload.get('content', '') or '').strip()[:1400]
        chain_hint = self._format_chain_context(context_payload.get('chain_context'))

        # 2. 組裝 Prompt
        prompt = self._construct_prompt(
            query=query,
            knowledge=knowledge_snippet,
            dimension=context_payload.get('dimension', 'General'),
            matrix_cell_context=matrix_cell_context,
            user_notes=user_notes,
            chain_hint=chain_hint,
        )

        # 3. 呼叫 LLM
        success, response_text = self.bus.dispatch_task(task_name, prompt, max_retries=1)

        if not success:
            logger.error(f"[Task7] LlmBus failed: {response_text}")
            return "抱歉，AI 家教目前無法連線，請稍後再試。"

        return response_text

    def _normalize_focus_pids(self, payload):
        raw = payload.get('focus_pids', [])
        if not isinstance(raw, list):
            raw = [raw]
        out = []
        for v in raw:
            s = str(v or '').strip()
            if not s:
                continue
            if s.lower() in {'synthesis', 'paper_a', 'paper_b'}:
                continue
            if s not in out:
                out.append(s)
        pid_hint = str(payload.get('pid') or '').strip()
        if not out and pid_hint and pid_hint.lower() not in {'synthesis', 'paper_a', 'paper_b'}:
            out.append(pid_hint)
        return out

    def _fetch_grounding_data(self, payload):
        """
        根據 PID 列表從 Disk 讀取文獻內容。
        策略: 若指定了 Dimension，嘗試讀取該章節；否則讀取 Summary 或 Full Text。
        """
        pids = self._normalize_focus_pids(payload)
        project_pid = str(payload.get('project_pid') or payload.get('project_id') or '').strip() or None
        if not project_pid:
            return "No project pid provided."
        if not pids:
            return "No specific paper selected."

        snippets = []
        for paper_id in pids:
            try:
                # 嘗試讀取文獻的「優化翻譯 (Translo)」或「摘要」
                # 這裡調用 study_loader (需確保該模組已實作 fetch_paper_data)
                paper_data = study_loader.fetch_paper_data(paper_id, data_type='translo', pid=project_pid)
                
                if not paper_data:
                    # Fallback to summary
                    paper_data = study_loader.fetch_paper_data(paper_id, data_type='summary', pid=project_pid)

                if paper_data:
                    title = paper_data.get('title', paper_id)
                    # 簡化內容以適應 Context Window (取前 3000 字或特定欄位)
                    # 實務上應根據 Dimension 過濾 (例如只取 'methodology' key)
                    raw_content = paper_data.get('content', '')
                    if isinstance(raw_content, list):
                        parts = []
                        for item in raw_content[:8]:
                            if isinstance(item, dict):
                                blocks = item.get('blocks', [])
                                if isinstance(blocks, list) and blocks:
                                    for b in blocks[:10]:
                                        if isinstance(b, dict):
                                            txt = b.get('content') or b.get('translation') or b.get('content_zh') or ''
                                            if txt:
                                                parts.append(str(txt))
                                else:
                                    txt = item.get('content') or item.get('translation') or item.get('content_zh') or ''
                                    if txt:
                                        parts.append(str(txt))
                            elif item:
                                parts.append(str(item))
                        content_text = "\n".join(parts)[:3200]
                    elif isinstance(raw_content, dict):
                        content_text = str(raw_content.get('text') or json.dumps(raw_content, ensure_ascii=False))[:3200]
                    else:
                        content_text = str(raw_content)[:3200]
                    snippets.append(f"--- Paper: {title} ---\n{content_text}\n")
            except Exception as e:
                logger.warning(f"[Task7] Failed to load data for {paper_id}: {e}")
        
        return "\n".join(snippets) if snippets else "No specific paper selected."

    def _format_chain_context(self, chain_context):
        if not isinstance(chain_context, dict):
            return ""
        brief = str(chain_context.get('brief', '') or '').strip()
        vector_facts = chain_context.get('vector_facts') or []
        kg_relations = chain_context.get('kg_relations') or []

        lines = []
        if brief:
            lines.append(f"[Project Brief]\n{brief[:500]}")

        if isinstance(vector_facts, list) and vector_facts:
            facts = [str(x).strip() for x in vector_facts if str(x).strip()]
            if facts:
                lines.append("[Vector Facts]\n" + "\n".join(f"- {f[:220]}" for f in facts[:5]))

        if isinstance(kg_relations, list) and kg_relations:
            rels = []
            for r in kg_relations[:5]:
                if not isinstance(r, dict):
                    continue
                s = str(r.get('s') or '').strip()
                p = str(r.get('p') or '').strip()
                o = str(r.get('o') or '').strip()
                if s and p and o:
                    rels.append(f"- {s} --{p}--> {o}")
            if rels:
                lines.append("[KG Relations]\n" + "\n".join(rels))

        return "\n\n".join(lines).strip()

    def _construct_prompt(self, query, knowledge, dimension, matrix_cell_context="", user_notes="", chain_hint=""):
        """
        建構家教模式 Prompt
        """
        return f"""
You are an expert Academic Tutor specializing in the field of this research.
The user is asking a question about specific papers.
Current Focus Dimension: {dimension}

[Matrix Cell Context]:
{matrix_cell_context or "(none)"}

[User Study Notes]:
{user_notes or "(none)"}

[Context Chain Hints]:
{chain_hint or "(none)"}

[Grounding Knowledge / Context]:
{knowledge}

[User Question]:
{query}

[Instructions]:
1. Answer the question STRICTLY based on [Grounding Knowledge], then use [Matrix Cell Context]/[User Study Notes]/[Context Chain Hints] as supporting cues.
2. If the answer is not in the context, imply state "根據目前文獻資料，無法找到相關資訊" (Do not hallucinate).
3. Use a helpful, encouraging, and academic tone (Traditional Chinese).
4. If referring to a specific paper, cite its title explicitly.
5. Keep the answer concise (under 500 words) unless detailed explanation is requested.
"""

# 用於測試
if __name__ == "__main__":
    logger.info("Task 7 Tutor Engine Loaded.")

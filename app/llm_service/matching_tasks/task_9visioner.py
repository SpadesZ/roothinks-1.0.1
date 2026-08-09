# 檔案路徑: app/llm_service/matching_tasks/task_9visioner.py
# 版本: v0.8；更新時間: 2026-08-10 +08:00
# 模組定位: Task8Drafter 的圖像／架構圖子任務，輸出 Mermaid 或 image prompt。
# 主要責任: 將使用者需求、受限 context 與附件摘要組成視覺化 prompt並解析回傳格式。
# 上下游: Task8Drafter -> Task9Visioner -> dispatch_task(task_9visioner)。
# 安全邊界: 附件只截取有限內容；取消訊號必須原樣交給 dispatcher。
# 驗證: python -m pytest test/unit/test_llm_cancellation.py -q

import json
from app.llm_service.llm_dispatcher import dispatch_task

class Task9Visioner:
    """
    Task 9: Visioner (系統架構視覺化專家)
    Identity: task_9visioner
    Capabilities: Mermaid Graphing, Attachment Base64/Text Parsing
    """
    TASK_ID = "task_9visioner" 

    def generate_image(self, user_prompt, context_text="", attachment=None, cancel_event=None):
        file_info = ""
        
        # 解析由前端傳入的一體化附件 Payload
        if attachment:
            file_name = attachment.get("name", "Unknown_File")
            file_content = attachment.get("content", "")
            file_info = f"\n\n[USER UPLOADED FILE: {file_name}]\n"
            
            # 若為非圖片的 Base64 或純文字 (例如 Excel CSV, MD, Code)，直接注入提示詞
            if file_content and not str(file_content).startswith("data:image"):
                file_info += f"FILE CONTENT (Raw/Base64 Text):\n{file_content[:5000]}\n"
            else:
                file_info += "(Image/Binary file attached. Extract system logic from it if multi-modal LLM is active.)"

        system_prompt = f"""
        Act as a Senior System Architect & UI Designer.
        User Request: "{user_prompt}"
        Context: "{context_text[:1500]}"
        {file_info}
        
        Mission:
        Generate professional Mermaid.js `graph TD` (or LR) code based EXACTLY on the uploaded file or context. Do not hallucinate logic.
        
        Visual Style Guide (Trailblazer Standard):
        1. **Grouping (Phases)**: 
           - MUST use `subgraph` to group nodes (e.g., "Phase 1: Input").
        2. **Semantic Shapes**:
           - Database -> `id[(Name)]`
           - User -> `id{{{{Name}}}}` or `id([Name])`
           - File -> `id>Name]`
           - Process -> `id(Name)`
        3. **Styling (ClassDef)**:
           - Define classes: `classDef db fill:#e1f5fe,stroke:#01579b;`
           - Apply classes: `class NodeA db;`
           
        Output Format:
        Return ONLY valid Mermaid code wrapped in ```mermaid ... ```.
        """
        
        # NOTE(NOTE-002): 子 task 不能在 parent 已取消後另開不可中止的 provider call。
        res = dispatch_task(self.TASK_ID, system_prompt, cancel_event=cancel_event)
        
        if not res.get("ok"):
            return {"type": "error", "content": res.get("msg")}
            
        text = res.get("text", "")
        
        if "```mermaid" in text:
            code = text.split("```mermaid")[1].split("```")[0].strip()
            return {"type": "image_mermaid", "content": code, "raw_text": text}
        elif "PROMPT:" in text:
            return {"type": "image_prompt", "content": text.split("PROMPT:")[1].strip()}
        else:
            return {"type": "image_mermaid", "content": text, "raw_text": text}

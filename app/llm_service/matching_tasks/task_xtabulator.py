#路徑(./app/llm_service/matching_tasks/task_xtabulator.py) 
#版本 v0.6 (Attachment Data to Strict JSON Table)
#更版時間 20260319-1045

import json
from app.llm_service.llm_dispatcher import dispatch_task

class TaskXTabulator:
    """
    Task X: Tabulator (數據製表專家)
    Identity: task_xtabulator
    Capabilities: Strict JSON Tabulation from Uploaded Excel/Drive Files
    """
    TASK_ID = "task_xtabulator" 

    def generate_sheet(self, user_prompt, context_text="", attachment=None):
        file_info = ""
        
        # 攔截附件數據，提供給製表專家使用
        if attachment:
            file_name = attachment.get("name", "Unknown_Data")
            file_content = attachment.get("content", "")
            file_info = f"\n\n[USER UPLOADED DATA: {file_name}]\n"
            
            # 將上傳的 Excel Base64 / CSV 純文字數據直接提供給 LLM 作為製表來源
            if file_content and not str(file_content).startswith("data:image"):
                file_info += f"DATA CONTENT (Parse the following values):\n{file_content[:10000]}\n"
            else:
                file_info += "(Image file attached. Please extract tabular data from the image via OCR vision.)"

        system_prompt = f"""
        Act as a Data Structuring Specialist.
        User Request: "{user_prompt}"
        Source Text: "{context_text[:3000]}"
        {file_info}
        
        Mission:
        Extract and convert the provided information or attached data into a structured 2D Table (JSON). 
        You MUST rely strictly on the UPLOADED DATA if provided.
        
        Protocol (STRICT):
        1. Output MUST be valid RAW JSON string.
        2. NO Markdown code blocks (NO ```json).
        3. NO comments outside JSON.
        
        JSON Schema:
        {{
            "title": "Table Title",
            "headers": ["Col A", "Col B"],
            "rows": [ ["Val A1", "Val B1"] ]
        }}
        """
        
        res = dispatch_task(self.TASK_ID, system_prompt)
        
        if not res.get("ok"):
            return {"type": "error", "content": res.get("msg")}
            
        raw_text = res.get("text", "").strip()
        clean_text = raw_text.replace("```json", "").replace("```", "").strip()
        
        try:
            table_data = json.loads(clean_text)
            if "headers" not in table_data or "rows" not in table_data:
                raise ValueError("Invalid JSON Structure")
                
            return {
                "type": "sheet",
                "content": table_data,
                "meta": {"generator": "task_xtabulator"}
            }
        except Exception as e:
            return {"type": "error", "content": f"Tabulation Error: {str(e)}"}
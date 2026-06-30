#路徑(./app/core_proc/study/study_tutor.py) #版本 v1.3 #更版時間 20260207-0025
# [Import] 使用重構後的 AcademicTutor
from app.llm_service.matching_tasks.task_7qachat import AcademicTutor

class TutorEngine:
    """
    Study Tutor Engine (v1.3)
    職責: 準備 Context，呼叫 Tutor 進行問答。
    """
    def __init__(self):
        self.ai_tutor = AcademicTutor()

    def chat(self, query, context_data_list, history=[]):
        """
        :param query: User question
        :param context_data_list: List of Paper Objects (Full Text or Summary)
        :param history: Chat history list
        """
        if not query: return {"error": "Empty Query"}

        # 1. 構建 Context String
        # 策略: 若只有一篇，提供詳細全文；若多篇，提供標題+摘要
        full_context = ""
        
        is_single = (len(context_data_list) == 1)
        
        for p in context_data_list:
            title = p['metadata'].get('title', p['paper_id'])
            full_context += f"### 文獻: {title}\n"
            
            if is_single:
                # 單篇: 嘗試組裝 Content Blocks
                blocks = p.get('content', [])
                # 取前 15000 字元避免爆 Token
                text_content = "\n".join([b.get('content', '') for b in blocks])
                full_context += f"【全文片段】:\n{text_content[:15000]}\n\n"
            else:
                # 多篇: 僅提供摘要
                summary = p.get('summary', {}).get('abstract_zh', '無摘要')
                findings = p.get('summary', {}).get('key_findings', [])
                full_context += f"【摘要】: {summary}\n"
                full_context += f"【關鍵發現】: {', '.join(findings)}\n\n"

        # 2. 呼叫 AI
        response_text = self.ai_tutor.ask_with_history(query, full_context, history)

        return {
            "ok": True,
            "role": "ai",
            "content": response_text
        }

#路徑(./app/llm_service/matching_tasks/task2A_paqchat.py) #版本 v0.4 #更版時間 20260223-1530
import json
from app.llm_service.llm_dispatcher import dispatcher

def execute_paq_chat(project_context: dict, chat_history: list, user_input: str, taxonomy_data: dict = None, cube_data: list = None):
    """
    Task 2A: PAQ Co-Pilot Chat
    針對專案背景與使用者進行對話，協助釐清定義或回答 Cube 相關問題。
    [v0.4 Update] 接收並解析 Taxonomy 與 Cube 最新數據，嵌入 Prompt 供 LLM 作為對話基礎。
    [v0.3 Fix] 修正回傳格式為字典，解決 'str' object has no attribute 'get' 錯誤。
    """
    
    # 1. 準備 Context
    title = project_context.get('research_title') or project_context.get('name')
    background = project_context.get('background', '')
    
    # 格式化歷史紀錄 (取最近 5 輪以節省 Token)
    history_text = ""
    if chat_history:
        for msg in chat_history[-5:]:
            role = "User" if msg.get('role') == 'user' else "AI"
            content = msg.get('content', '')
            history_text += f"{role}: {content}\n"

    # 準備 Taxonomy 與 Cube 字串 (JSON 格式)，若無資料則安全處理
    tax_str = "Not generated yet"
    if taxonomy_data and (taxonomy_data.get('axis_labels') or taxonomy_data.get('axis_tags')):
        tax_str = json.dumps(taxonomy_data, ensure_ascii=False, indent=2)
        
    cube_str = "Not generated yet"
    if cube_data:
        cube_str = json.dumps(cube_data, ensure_ascii=False, indent=2)

    # 2. 構建 Prompt
    prompt = f"""
    You are an intelligent research assistant (Co-Pilot) for the project "{title}".
    
    ## Project Context
    Background: {background}
    
    ## Latest Taxonomy (Task 1)
    {tax_str}
    
    ## Latest Academic Cube (Task 2)
    {cube_str}
    
    ## Conversation History
    {history_text}
    
    ## Current User Input
    User: {user_input}
    
    ## Instructions
    - Provide a concise, helpful answer relevant to academic research label.
    - If the user asks about the "Cube" or "Taxonomy", explain the concepts of X/Y/Z axes based on the provided latest taxonomy.
    - Keep the tone professional yet encouraging.
    - Reply in the same language as the user (Traditional Chinese preferred if user asks in Chinese).
    """

    # 3. 呼叫 Dispatcher
    ok, res, err = dispatcher.execute("task_2a_chat", text=prompt)
    
    if not ok:
        return False, f"LLM Error: {err}"

    # 4. 解析結果
    try:
        reply = res.get('text', '').strip()
        
        # [Fix v0.3] 必須回傳字典格式，供 paq_routes.py 使用
        return True, {
            "reply": reply
        }

    except Exception as e:
        return False, f"Chat Processing Error: {str(e)}"
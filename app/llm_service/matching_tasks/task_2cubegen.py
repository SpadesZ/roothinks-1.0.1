#路徑(./app/llm_service/matching_tasks/task_2cubegen.py) #版本 v0.5 #更版時間 20260218-1830
import json
from app.llm_service.llm_dispatcher import dispatcher

def execute_cube_gen(project_context: dict, taxonomy: dict):
    """
    Task 2: Academic Cube Generation (Value Assessment)
    基於 Task 1 的 Taxonomy，評估每個交集 (X,Y,Z) 的「研究潛力價值」。
    採用 NIH/NSF Merit Review Criteria 作為評分邏輯。
    [v0.5 Fix] 讀取 axis_labels，並維持嚴格的 JSON 輸出檢查。
    """

    # 1. 準備輸入資料
    title = project_context.get('research_title') or project_context.get('name')
    background = project_context.get('background', '') or project_context.get('context_background', '')
    
    # [v0.5 Fix] 提取軸向定義 (優先讀取 axis_labels)
    defs = taxonomy.get('axis_labels', {})
    if not defs:
        # Fallback: 支援舊格式
        defs = taxonomy.get('axis_definitions', {})

    tags = taxonomy.get('axis_tags', {})
    
    x_tags = tags.get('x', [])
    y_tags = tags.get('y', [])
    z_tags = tags.get('z', [])

    # 防呆：若無標籤則無法執行
    if not (x_tags and y_tags and z_tags):
        return False, "Missing taxonomy tags. Please complete Task 1 first."

    # 2. 建構 Prompt (核心價值邏輯)
    prompt = f"""
    You are an AI Research Evaluator. 
    Evaluate the "Research Potential" for the intersection of 3 axes for project "{title}".

    ## Axes Labels
    - X (Method): {json.dumps(x_tags)}
    - Y (Domain): {json.dumps(y_tags)}
    - Z (Attribute): {json.dumps(z_tags)}
    
    ## Labels Definitions
    {json.dumps(defs)}

    ## Evaluation Criteria (NIH/NSF)
    1. **Novelty**: Is this combination unexplored?
    2. **Relevance**: Does it solve the core problem in "{background}"?
    3. **Feasibility**: Is it technically viable?

    ## Task
    Generate a 3D Voxel dataset where each point (x, y, z) has a "val" (Density/Potential) score.
    Only generate HIGH POTENTIAL points (val > 0.6) to save tokens.

    ## STRICT OUTPUT FORMAT
    - OUTPUT RAW JSON ONLY. 
    - DO NOT USE MARKDOWN BLOCKS (e.g. ```json).
    - NO PREAMBLE OR EXPLANATION.

    ## JSON Schema
    {{
        "voxels": [
            {{
                "x": "Tag X name (String)",
                "y": "Tag Y name (String)",
                "z": "Tag Z name (String)",
                "val": 0.95, 
                "hover": "主題名稱<br>Topic Title<br>簡短理由 / Brief reason (≤30 chars total)" 
            }},
            ...
        ],
        "summary": "15-20 chars summary of the heatmap."
    }}
    """

    # 3. 呼叫 LLM
    ok, res, err = dispatcher.execute("task_2cubegen", text=prompt)
    
    if not ok:
        return False, f"LLM Error: {err}"

    # 4. 解析與後處理 [Enhanced Cleaning]
    try:
        raw_text = res.get('text', '').strip()
        
        # 強力清洗 Markdown 標記
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        
        raw_text = raw_text.strip()

        data = json.loads(raw_text)
        
        # 驗證結構
        if 'voxels' not in data:
             return False, "Invalid JSON: Missing 'voxels' key."

        # 保留 Summary for CoC
        summary = data.get('summary', 'Cube Generated')

        return True, {
            "voxels": data['voxels'],
            "summary": summary
        }

    except json.JSONDecodeError:
        return False, f"JSON Parse Failed. Raw LLM Output: {raw_text[:100]}..."
    except Exception as e:
        return False, f"Process Error: {str(e)}"
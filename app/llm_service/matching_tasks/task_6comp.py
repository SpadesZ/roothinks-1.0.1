#路徑(app/llm_service/matching_tasks/task_6comp.py) #版本 v2.0-Academic #更版時間 20260208-0130
import json
import re
import logging
from logging.handlers import RotatingFileHandler
import app.llm_service.llm_bus as llm_bus
from flask import has_app_context, current_app

# 設定 Log
logger = logging.getLogger("Task6_Comp")

# 添加檔案日誌以追蹤執行
import os
log_file = os.path.join(os.path.dirname(__file__), "task_6_debug.log")
_file_logger = logging.getLogger("Task6_Comp_File")
if not _file_logger.handlers:
    _handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _file_logger.addHandler(_handler)
    _file_logger.setLevel(logging.INFO)
    _file_logger.propagate = False

def _is_debug_enabled():
    if has_app_context():
        return bool(current_app.debug)
    return os.environ.get("FLASK_DEBUG", "0") == "1" or os.environ.get("FLASK_ENV", "").lower() == "development"

def _debug_log(line):
    if not _is_debug_enabled():
        return
    _file_logger.info("%s", line)

class Comparator:
    """
    [Task 6] 多文獻比較引擎 (Multi-Paper Comparison Engine)
    職責:
    1. 接收兩篇論文的數據包 (Base vs Target)。
    2. 組裝學術審閱級別的 Prompt。
    3. 呼叫 LlmBus 執行推論。
    4. 清洗並回傳標準化 JSON。
    """

    def __init__(self):
        self.bus = llm_bus.get_bus()
        self.default_dimensions = [
            "研究問題與目標",
            "方法與系統設計",
            "資料與實驗設定",
            "結果與證據強度",
            "限制與風險",
        ]
        # 定義嚴格的輸出格式 (One-Shot Example in Prompt)
        self.output_schema = {
            "comparison_points": [
                {
                    "dimension": "固定維度之一：研究問題與目標/方法與系統設計/資料與實驗設定/結果與證據強度/限制與風險",
                    "baseline_view": "基準論文 (Paper A) 的具體作法或數據",
                    "target_view": "對照論文 (Paper B) 的具體作法或數據",
                    "synthesis": "兩者差異的學術評析 (Gap Analysis) 與優劣判斷",
                    "significance": "此差異的重要性 (High/Medium/Low)"
                }
            ],
            "overall_summary": "針對本次比較維度的總體結語 (100字內)"
        }

    def compare_pair(self, base_pkg, target_pkg, criteria):
        """
        執行單一配對比較
        :param base_pkg: {id, title, text}
        :param target_pkg: {id, title, text}
        :param criteria: 使用者關注的比較維度 (e.g. "Methodology", "Experimental Results")
        :return: JSON Result
        """
        # LOG TO FILE
        _debug_log(f"[compare_pair] START for {target_pkg['id']}")
        
        task_name = "task_6_comparison"
        
        # 1. 組裝 Prompt
        prompt = self._construct_prompt(base_pkg, target_pkg, criteria)
        
        # [DEBUG Log]
        logger.info(f"[Task6.DEBUG] Starting comparison for {target_pkg['id']}")
        
        # 2. 透過 Bus 發送請求 (設有重試機制)
        success, response_text = self.bus.dispatch_task(task_name, prompt, max_retries=2)
        _debug_log(f"[compare_pair] dispatch_task returned: success={success}")
        
        logger.info(f"[Task6.DEBUG] dispatch_task returned: success={success}, response_len={len(response_text) if isinstance(response_text, str) else 'N/A'}")
        
        # [Fix] ALWAYS prepare fallback data
        base_len = len(base_pkg.get('text', ''))
        target_len = len(target_pkg.get('text', ''))
        
        if not success:
            _debug_log("[compare_pair] NOT SUCCESS - returning fallback")
            logger.warning(f"[Task6] LlmBus failed for {target_pkg['id']}: {response_text}")
            # Use deterministic fallback data when LLM fails
            base_text = base_pkg.get('text', '') or ''
            target_text = target_pkg.get('text', '') or ''

            base_tokens = set(re.findall(r"[A-Za-z]{4,}", base_text.lower()))
            target_tokens = set(re.findall(r"[A-Za-z]{4,}", target_text.lower()))
            overlap = len(base_tokens & target_tokens)
            union = len(base_tokens | target_tokens) or 1
            overlap_ratio = round((overlap / union) * 100, 1)

            base_preview = re.sub(r"\s+", " ", base_text).strip()[:140]
            target_preview = re.sub(r"\s+", " ", target_text).strip()[:140]

            fallback_result = {
                "comparison_points": [
                    {
                        "dimension": "內容覆蓋範圍",
                        "baseline_view": f"內容長度: {base_len} 字符",
                        "target_view": f"內容長度: {target_len} 字符",
                        "synthesis": "基準與對照可比較，內容規模存在差異。",
                        "significance": "Low"
                    },
                    {
                        "dimension": "主題重疊度",
                        "baseline_view": f"關鍵詞數量: {len(base_tokens)}",
                        "target_view": f"關鍵詞數量: {len(target_tokens)}",
                        "synthesis": f"兩文關鍵詞重疊率約 {overlap_ratio}%（僅統計字面詞）。",
                        "significance": "Medium"
                    }
                ],
                "overall_summary": f"暫以規則式比較呈現：{base_pkg['title']} 對照 {target_pkg['title']}。",
                "target_paper_id": target_pkg['id'],
                "target_paper_title": target_pkg['title'],
                "analysis": (
                    f"【規則式比較】\n"
                    f"基準論文: {base_pkg['title']}\n"
                    f"對照論文: {target_pkg['title']}\n"
                    f"內容長度: {base_len} vs {target_len} 字符\n"
                    f"主題重疊率: {overlap_ratio}%\n"
                    f"基準片段: {base_preview}\n"
                    f"對照片段: {target_preview}"
                )
            }
            _debug_log(f"[compare_pair] Returning fallback with keys: {list(fallback_result.keys())}")
            fallback_result = self._normalize_result(fallback_result, criteria, base_pkg, target_pkg)
            return fallback_result

        # 3. 解析與清洗
        try:
            data = self._parse_json(response_text)
            # 注入 Metadata 以便前端渲染
            data['target_paper_id'] = target_pkg['id']
            data['target_paper_title'] = target_pkg['title']

            return self._normalize_result(data, criteria, base_pkg, target_pkg)
        except Exception as e:
            logger.error(f"[Task6] JSON Parse Error: {e}\nRaw: {response_text[:200]}")
            # Return fallback when parsing fails too
            err = {
                "comparison_points": [{
                    "dimension": "解析失敗",
                    "baseline_view": base_pkg.get('title', 'N/A'),
                    "target_view": target_pkg.get('title', 'N/A'),
                    "synthesis": "無法解析 AI 回應",
                    "significance": "Low"
                }],
                "target_paper_id": target_pkg['id'],
                "target_paper_title": target_pkg['title'],
                "analysis": f"[錯誤] 無法解析 AI 分析結果。\n基準: {base_pkg['title']}\n對照: {target_pkg['title']}"
            }
            return self._normalize_result(err, criteria, base_pkg, target_pkg)

    def _construct_prompt(self, base, target, criteria):
        """
        建構高學術價值的 Prompt
        """
        return f"""
You are an expert academic reviewer for a top-tier journal (e.g., IEEE/Nature).
Your task is to conduct a rigorous comparative analysis between two research papers focusing strictly on the dimension: "{criteria}".

[Paper A (Baseline)]:
Title: {base['title']}
Content Snippet:
{base['text'][:2500]} ... (truncated)

[Paper B (Target)]:
Title: {target['title']}
Content Snippet:
{target['text'][:2500]} ... (truncated)

[Goal]:
Compare Paper B against Paper A regarding "{criteria}". Identify specific differences, improvements, or regressions.
Do not just summarize; perform a "Gap Analysis".

[Output Requirement]:
1. Language: Traditional Chinese (繁體中文).
2. Format: STRICT JSON format only. No markdown text outside JSON.
3. Schema:
{json.dumps(self.output_schema, ensure_ascii=False, indent=4)}

[Constraint]:
- If information is missing in the text, state "N/A" in the view field but do not hallucinate.
- The "synthesis" field must explain *why* the difference matters.
- Use exactly 5 comparison points and keep each field concise (<=120 Chinese characters preferred).
- The dimensions should follow this order exactly:
  1) 研究問題與目標
  2) 方法與系統設計
  3) 資料與實驗設定
  4) 結果與證據強度
  5) 限制與風險
"""

    def _normalize_result(self, data, criteria, base_pkg, target_pkg):
        if not isinstance(data, dict):
            data = {}

        raw_points = data.get('comparison_points')
        if not isinstance(raw_points, list):
            raw_points = []

        by_dim = {}
        for p in raw_points:
            if not isinstance(p, dict):
                continue
            dim = str(p.get('dimension', '') or '').strip()
            if dim:
                by_dim[dim] = p

        normalized = []
        for dim in self.default_dimensions:
            src = by_dim.get(dim)
            if not src:
                # try fuzzy match once
                for k, v in by_dim.items():
                    if dim[:2] in k or k[:2] in dim:
                        src = v
                        break

            if src:
                baseline_view = str(src.get('baseline_view', 'N/A') or 'N/A').strip()
                target_view = str(src.get('target_view', 'N/A') or 'N/A').strip()
                synthesis = str(src.get('synthesis', 'N/A') or 'N/A').strip()
                significance = str(src.get('significance', 'Medium') or 'Medium').strip()
            else:
                baseline_view = 'N/A'
                target_view = 'N/A'
                synthesis = '資料不足，待補充比較證據。'
                significance = 'Low'

            normalized.append({
                'dimension': dim,
                'baseline_view': baseline_view[:220],
                'target_view': target_view[:220],
                'synthesis': synthesis[:260],
                'significance': significance,
            })

        data['comparison_points'] = normalized
        data['target_paper_id'] = target_pkg['id']
        data['target_paper_title'] = target_pkg['title']
        if not data.get('overall_summary'):
            data['overall_summary'] = f"比較維度：{criteria}。已完成 {base_pkg['title']} 與 {target_pkg['title']} 的對照。"

        analysis_parts = []
        for point in normalized:
            analysis_parts.append(
                f"【{point['dimension']}】\n"
                f"基準: {point['baseline_view']}\n"
                f"對照: {point['target_view']}\n"
                f"評析: {point['synthesis']}"
            )
        data['analysis'] = "\n\n".join(analysis_parts)
        return data

    def _parse_json(self, text):
        """
        強健的 JSON 清洗器 (Anti-Hallucination / Anti-Markdown)
        """
        text = text.strip()
        
        # 1. 移除 Markdown Code Block 標記
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[0] # 簡單處理
            
        # 2. 嘗試尋找 JSON 的起止點 (處理前後廢話)
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end != -1:
                text = text[start:end]
        except Exception as e:
            logger.warning("[task_6comp._parse_json] boundary trim failed: %s", e)

        # 3. 載入
        return json.loads(text)

# 用於測試的 main block
if __name__ == "__main__":
    logger.info("Task 6 Comparator Module Loaded.")

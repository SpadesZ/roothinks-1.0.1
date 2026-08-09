# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/paq/paq_matrix.py
# 模組定位: PAQ 核心層；管理研究題目、分類矩陣與 provisional/formal 專案銜接。
# 主要責任: PaqMatrix: 資料轉譯與視圖適配器 (Data Transformer & View Adapter)。
# 上下游: Dashboard/PAQ 頁面 -> PAQ routes/core -> Project 與 data/<pid>/literature/paq_records。
# 維護邊界: 設定與共享狀態只能在既定初始化邊界改動；錯誤不得以表面成功掩蓋資料或授權不完整。
#路徑(./app/core_pro/paq/paq_matrix.py) #版本 v0.1 #更版時間 20260213-0200
"""
PaqMatrix: 資料轉譯與視圖適配器 (Data Transformer & View Adapter)
職責:
1. 將 LLM (Task 2) 的邏輯產出轉換為前端 (Plotly) 可用的視圖數據。
2. 生成扁平化的 RAG Context 列表，供 Chatbot (Task 2A) 使用。
"""
import json

class PaqMatrix:
    
    @staticmethod
    def transform(taxonomy_data: dict, llm_raw_data: dict):
        """
        核心轉換函數
        :param taxonomy_data: Task 1 產出的軸向定義 (包含 axis_tags)
        :param llm_raw_data: Task 2 產出的原始 Voxel JSON
        :return: dict {'view_data': [...], 'rag_context': [...]}
        """
        # 1. 提取原始 Voxels
        raw_voxels = llm_raw_data.get('voxels', [])
        if not raw_voxels:
            return {'view_data': [], 'rag_context': []}

        # 2. 準備座標系 (Coordinate System) - 這裡主要用於驗證
        # Plotly Scatter3d 可以直接吃 String Category，但若要精確控制順序，可在此轉 index
        # 目前版本 v0.1 採直接映射策略 (Direct Mapping)
        
        view_data = []
        rag_context = []

        # 3. 遍歷數據進行轉換
        for v in raw_voxels:
            # A. 視圖數據處理 (For Frontend)
            # 確保 val 在 0.0 - 1.0 之間
            try:
                rpi = float(v.get('val', 0))
                rpi = max(0.0, min(1.0, rpi))
            except ValueError:
                rpi = 0.0

            # 格式化 Hover Text (HTML)
            # 將 LLM 可能產生的冗長文字轉為精簡 HTML
            raw_hover = v.get('hover', '')
            formatted_hover = raw_hover.replace('\n', '<br>')
            
            # 組裝 View Object
            view_obj = {
                "x": v.get('x', 'Unknown'),
                "y": v.get('y', 'Unknown'),
                "z": v.get('z', 'Unknown'),
                "val": rpi,
                "hover": formatted_hover
            }
            view_data.append(view_obj)

            # B. RAG 上下文處理 (For Chatbot)
            # 將 3D 空間扁平化為自然語言描述
            # 格式: "[RPI: 0.95] Topic (X+Y+Z): Description"
            rag_item = (
                f"[RPI: {rpi:.2f}] "
                f"Coordinates: {v.get('x')} + {v.get('y')} + {v.get('z')}. "
                f"Details: {raw_hover}"
            )
            rag_context.append(rag_item)

        return {
            'view_data': view_data,
            'rag_context': rag_context
        }
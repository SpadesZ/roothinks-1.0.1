# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_matrix.py
# 模組定位: Study 核心層；協調論文閱讀、筆記/矩陣與 AI tutor 的專案內狀態。
# 主要責任: 建立、更新並驗證 Study 比較矩陣，保留欄列 identity 與可持久化格式。
# 上下游: Study routes/static JS 呼叫本層，讀取 Literature 素材並把筆記、對話或矩陣保存到 data/<pid>/study。
# 維護邊界: 所有讀寫保留 PID、user 與 section scope；草稿/版本/快取不得跨使用者、跨章或以舊非同步回應覆蓋新狀態。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/core_proc/study/study_matrix.py) #版本 v1.6 #更版時間 20260209-0030
import json
import hashlib
import os
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
# [Import] 使用重構後的 Comparator
from app.llm_service.matching_tasks.task_6comp import Comparator

logger = logging.getLogger("StudyMatrix")

class MatrixEngine:
    """
    Study Matrix Engine (v1.6)
    職責: 協調 Comparator 執行多文獻的「基準比較 (Baseline Strategy)」。
    流程:
    1. 接收 N 篇論文數據。
    2. 設定 Paper 0 為 Baseline。
    3. 並行呼叫 Comparator 比較 (Baseline vs Paper i)。
    4. 聚合結果為 Matrix JSON。
    5. [Updated v1.6] 存儲於 data/<pid>/study/papers_comp/ 下，並處理檔名長度問題。
    """
    def __init__(self, data_root="./data"):
        self.comparator = Comparator()
        self.data_root = data_root

    def generate_matrix(self, pid, papers_data, criteria="Methodology"):
        """
        :param pid: Project ID
        :param papers_data: List of Paper Objects (from Loader)
        :param criteria: User defined dimension
        """
        import os as _os
        log_path = _os.path.join(_os.path.dirname(__file__), "matrix_debug.log")
        
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n[generate_matrix] STARTED\n")
            f.write(f"[generate_matrix] papers_count={len(papers_data)}\n")
        
        if len(papers_data) < 2:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"[generate_matrix] ERROR: less than 2 papers\n")
            return {"error": "至少需要兩篇文獻 (Need > 1 papers)"}

        # 1. 定義角色
        baseline = papers_data[0]
        targets = papers_data[1:]
        
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[generate_matrix] baseline={baseline.get('paper_id', 'N/A')}, targets={len(targets)}\n")
        
        # 2. 準備數據包 (Data Packaging)
        def _prep(p):
            # 優先使用全文，若無則用摘要
            txt = ""
            if p.get('content') and isinstance(p['content'], list):
                # 簡單合併 content list string, 取前 30 個 blocks 避免 token 爆炸
                blocks = [b.get('content', '') for b in p['content'] if isinstance(b, dict)]
                txt = "\n".join(blocks[:30])
            elif p.get('content') and isinstance(p['content'], str):
                full = str(p['content'])
                n = len(full)
                if n <= 12000:
                    txt = full
                else:
                    seg = 3800
                    mid = max(0, (n // 2) - (seg // 2))
                    tail = max(0, n - seg)
                    txt = "\n\n".join([
                        full[:seg],
                        full[mid:mid + seg],
                        full[tail:tail + seg],
                    ])
            else:
                txt = str(p.get('summary', ''))
            
            return {
                "id": p['paper_id'],
                "title": p.get('metadata', {}).get('title', p['paper_id']),
                "text": txt
            }

        base_pkg = _prep(baseline)
        target_pkgs = [_prep(t) for t in targets]
        
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[generate_matrix] base_pkg prepared, target_pkgs={len(target_pkgs)}\n")

        # 3. 並行執行比較 (Parallel Execution)
        # 使用 ThreadPool 避免 IO 阻塞
        results = []
        per_future_timeout_sec = int(os.environ.get("STUDY_MATRIX_FUTURE_TIMEOUT_SEC", "300"))
        total_wait_timeout_sec = int(os.environ.get("STUDY_MATRIX_TOTAL_TIMEOUT_SEC", "360"))
        executor = ThreadPoolExecutor(max_workers=3)
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"[generate_matrix] ThreadPool starting\n")
            
            future_map = {
                executor.submit(
                    self.comparator.compare_pair, base_pkg, t_pkg, criteria
                ): t_pkg['id'] for t_pkg in target_pkgs
            }
            
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"[generate_matrix] Submitted {len(future_map)} jobs\n")
            completed = set()
            try:
                for future in as_completed(future_map, timeout=total_wait_timeout_sec):
                    completed.add(future)
                    target_id = future_map[future]
                    try:
                        res = future.result(timeout=per_future_timeout_sec)
                        with open(log_path, 'a', encoding='utf-8') as f:
                            f.write(f"[generate_matrix] Got result from {target_id}, type={type(res)}\n")

                        # [Fix] Ensure all results have 'analysis' field for frontend rendering
                        if isinstance(res, dict):
                            with open(log_path, 'a', encoding='utf-8') as f:
                                f.write(f"[generate_matrix] res keys before: {list(res.keys())}\n")
                            # If error but no analysis, add fallback analysis
                            if 'error' in res and 'analysis' not in res:
                                res['analysis'] = f"[系統訊息] {res.get('error', '無法進行深度分析')}"
                                with open(log_path, 'a', encoding='utf-8') as f:
                                    f.write(f"[generate_matrix] Added analysis field\n")
                            results.append(res)
                        else:
                            error_obj = {
                                "error": f"Unexpected result type: {type(res)}",
                                "target": target_id,
                                "analysis": "無法生成比較結果"
                            }
                            results.append(error_obj)
                    except Exception as e:
                        with open(log_path, 'a', encoding='utf-8') as f:
                            f.write(f"[generate_matrix] Exception in thread for {target_id}: {e}\n")
                        error_result = {
                            "error": str(e),
                            "target": target_id,
                            "analysis": f"[系統錯誤] 無法生成比較: {str(e)}"
                        }
                        results.append(error_result)
            except FuturesTimeoutError:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"[generate_matrix] Timeout waiting futures after {total_wait_timeout_sec}s\n")
            finally:
                unfinished = [fut for fut in future_map.keys() if fut not in completed]
                for fut in unfinished:
                    target_id = future_map[fut]
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    results.append({
                        "error": f"timeout after {total_wait_timeout_sec}s",
                        "target": target_id,
                        "analysis": "比較任務逾時，請稍後重試。"
                    })
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        # 4. 聚合結果 (Aggregation)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[generate_matrix] Aggregating results: {len(results)} items\n")
        
        final_matrix = {
            "pid": pid,
            "criteria": criteria,
            "baseline": {
                "id": base_pkg['id'],
                "title": base_pkg['title']
            },
            "comparisons": results # List of AI analysis JSON
        }

        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[generate_matrix] final_matrix created with {len(final_matrix['comparisons'])} comparisons\n")
            if len(final_matrix['comparisons']) > 0:
                f.write(f"[generate_matrix] First comparison keys: {list(final_matrix['comparisons'][0].keys())}\n")

        # 5. 存檔 (Persist) - [Fix Path & Filename]
        self._save_matrix_result(pid, papers_data, criteria, final_matrix)

        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[generate_matrix] COMPLETE - returning matrix\n")
        
        return final_matrix

    def _save_matrix_result(self, pid, papers_data, criteria, matrix_data):
        """
        [Helper] 將矩陣結果寫入指定目錄
        path: data/<pid>/study/papers_comp/<combo_id>.json
        """
        try:
            save_dir = os.path.join(self.data_root, pid, "study", "papers_comp")
            os.makedirs(save_dir, exist_ok=True)

            # 提取所有參與比較的 Paper ID 並排序 (Canonical ID)
            all_ids = sorted([p['paper_id'] for p in papers_data])
            ids_str = "+".join(all_ids)
            
            # 處理檔名過長問題 (Windows limit ~250 chars)
            clean_crit = "".join([c if c.isalnum() or c in (" ", "-", "_") else "_" for c in criteria])
            if len(clean_crit) > 30:
                clean_crit = clean_crit[:30] + "_" + hashlib.md5(criteria.encode()).hexdigest()[:6]

            filename = f"{clean_crit}_{ids_str}"
            if len(filename) > 100:
                # 若過長，使用 Hash 縮短
                hash_obj = hashlib.md5(ids_str.encode())
                ids_hash = hash_obj.hexdigest()[:12] # 取前 12 碼
                # 保留前兩個 ID 作為識別，加上 Hash
                prefix = "+".join(all_ids[:2])
                if len(prefix) > 50: prefix = prefix[:50]
                filename = f"{clean_crit}_{prefix}_etc_{ids_hash}"
            
            # 加上副檔名
            full_path = os.path.join(save_dir, f"{filename}.json")

            with open(full_path, 'w', encoding='utf-8') as f:
                json.dump(matrix_data, f, ensure_ascii=False, indent=4)
                
            logger.info(f"[Matrix] Saved to: {full_path}")
            
        except Exception as e:
            logger.error(f"[Matrix] Save Error: {e}")

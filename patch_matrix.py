# 檔案路徑: patch_matrix.py
# 模組定位: 歷史一次性 source patch 工具；以文字比對修補 Study Matrix executor 關閉流程，不屬於線上 request path。
# 主要責任: 讀取 study_matrix.py，在唯一已知程式片段插入 try/finally 與非阻塞 executor shutdown，再覆寫目標檔。
# 上下游: 維護者於受控 checkout 明確執行 -> app/core_pro/study/study_matrix.py；輸出必須以 Git diff 人工複核。
# 維護邊界: 此工具不是通用 migration；執行前必須備份並確認精確基線，找不到唯一 anchor 時不得套用或碰正式 data。
# 驗證: python -m py_compile patch_matrix.py；在 disposable worktree 執行後以 git diff --check 驗證唯一預期變更。

import os

with open('app/core_pro/study/study_matrix.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if "with ThreadPoolExecutor(max_workers=3) as executor:" in line:
        new_lines.append("        executor = ThreadPoolExecutor(max_workers=3)\n")
        new_lines.append("        try:\n")
    elif '                    })\n' in line and lines[i+2] == '        # 4. 聚合結果 (Aggregation)\n':
        new_lines.append(line)
        new_lines.append("        finally:\n")
        new_lines.append("            executor.shutdown(wait=False, cancel_futures=True)\n")
    else:
        new_lines.append(line)

with open('app/core_pro/study/study_matrix.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

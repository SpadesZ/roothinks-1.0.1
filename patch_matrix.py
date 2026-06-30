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

# LOCAL-FIRST：29 項依賴調和

選定 113 個 hash-pinned 套件。每列最終驗證見本輪交付；選版理由不是通過證明。

| 套件 | 雲端 | 原本機 | 整合選版 | 調查與理由 |
|---|---|---|---|---|
| bidict | 0.23.1 | 0.24.1 | 0.24.1 | python-socketio 對應的雙向映射；保留已驗本機版本，Socket/撤權回歸重驗。 |
| deprecated | 1.3.1 | 3.0.0 | 3.0.0 | Flask-Limiter/limits 間接相依；保留本機版本，登入/rate-limit/error 路徑重驗。 |
| dnspython | 2.8.0 | 2.9.0 | 2.9.0 | Eventlet green DNS 相依；保留本機版本，兩種 worker 握手與網路無效路徑重驗。 |
| imageio | 2.38.0 | 2.38.1 | 2.38.1 | scikit-image/EasyOCR 影像相依；保留本機 patch 版本並實測影像解碼。 |
| iopath | 0.1.10 | 移除／不在 lock | 移除／不在 lock | 僅由未使用的 layoutparser 引入；移除該根相依後不保留孤立套件。 |
| layoutparser | 0.3.4 | 移除／不在 lock | 移除／不在 lock | app/scripts/test/tests 沒有直接或字串呼叫；實際 PDF 路徑為 Poppler+CV。移除可避免強制 GUI cv2。 |
| networkx | 3.4.2 | 3.7 | 3.7 | CPU Torch 相依；保留已驗本機版本，實際新 Torch native ops 與模型重驗。 |
| numpy | 2.2.6 | 1.26.4 | 1.26.4 | 保留已驗本機 1.26.4 及其 OpenCV ABI；未宣稱比雲端 2.2.6 正確或數值相同。影像/科學 parity 分開測。 |
| opencv-python | 4.14.0.94 | 移除／不在 lock | 移除／不在 lock | 與 headless 同寫 cv2 namespace；移除 GUI provider，只有 headless 被鎖定。 |
| opencv-python-headless | 5.0.0.93 | 4.11.0.86 | 4.11.0.86 | 保留本機 4.11.0.86 的已驗 ABI；單一 provider，CV/實際 PDF 與 OCR 重驗。 |
| pandas | 2.3.3 | 移除／不在 lock | 移除／不在 lock | 只在移除的 layoutparser 鏈中；產品沒有 import，無需引入第二套數值/時間相依。 |
| pdfminer-six | 20251230 | 移除／不在 lock | 移除／不在 lock | pdfplumber/layoutparser 間接相依；實際產品用 Poppler，不另保留解析器。 |
| pdfplumber | 0.11.9 | 移除／不在 lock | 移除／不在 lock | 移除 layoutparser 的相依；產品未呼叫，真 PDF 由 Poppler 文字/轉圖及 OCR 驗。 |
| portalocker | 4.4.1 | 移除／不在 lock | 移除／不在 lock | iopath 的相依；產品 CAS/檔鎖採 filelock，不替換既有鎖策略。 |
| pycparser | 3.11 | 3.12 | 3.12 | cffi 的 parser 相依；保留本機版本，Fernet/密碼及完整回歸重驗。 |
| pypdfium2 | 5.14.0 | 移除／不在 lock | 移除／不在 lock | 移除 pdfplumber 後不需要；產品的 PDF renderer 是 Poppler。 |
| python-dateutil | 3.0.0 | 移除／不在 lock | 移除／不在 lock | pandas 移除後不需要；產品未 import；研究時間/歷史不改寫。 |
| pytz | 2026.5 | 移除／不在 lock | 移除／不在 lock | pandas 鏈移除後不需要；時區行為由現有 datetime/zone 使用維持。 |
| scikit-image | 0.25.2 | 0.26.0 | 0.26.0 | EasyOCR 幾何/影像處理相依；保留本機 0.26.0，真 OCR 比較揭露輸出差異。 |
| scipy | 1.15.3 | 1.17.1 | 1.17.1 | EasyOCR/scikit-image 相依；保留本機 1.17.1 與 NumPy 下界相容；數值品質不由 pip check 推定。 |
| shapely | 2.1.2 | 2.2.0 | 2.2.0 | EasyOCR polygon grouping 相依；保留本機 2.2.0；實際 OCR bbox/文字診斷。 |
| six | 1.17.0 | 移除／不在 lock | 移除／不在 lock | 僅原 dateutil/iopath 鏈需要；最終 resolver 無需求，不加孤立套件。 |
| soupsieve | 2.10 | 3.0.4 | 3.0.4 | BeautifulSoup/deep-translator 的 HTML selector；保留本機版，Google 服務 live 可用性另列未驗。 |
| sqlalchemy | 2.0.55 | 2.1.6 | 2.1.6 | 保留本機 2.1.6，符合 Flask-SQLAlchemy 下界；研究 DB/CAS/backup/ACL 全回歸是採用條件。 |
| tifffile | 2025.5.10 | 2026.3.3 | 2026.3.3 | scikit-image 的影像 I/O 相依；保留本機版本，影像控制與完整回歸重驗。 |
| torch | 移除／不在 lock | https://download.pytorch.org/whl/cpu/torch-2.6.0%2Bcpu-cp312-cp312-linux_x86_64.whl | 2.13.0+cpu | 兩邊原來同為 2.6 CPU（雲端在 lock 外），不是功能缺包；改 2.13.0+cpu 處理已知權重載入漏洞，官方 URL+SHA-256。 |
| torchvision | 移除／不在 lock | https://download.pytorch.org/whl/cpu/torchvision-0.21.0%2Bcpu-cp312-cp312-linux_x86_64.whl | 0.28.0+cpu | 配對 Torch 2.13.0 的 0.28.0+cpu；官方 wheel URL+hash，必驗 native NMS 及 EasyOCR。 |
| tzdata | 2026.5 | 移除／不在 lock | 移除／不在 lock | pandas 鏈移除後不需要；Linux 系統 tzdata 仍來自固定 Debian snapshot。 |
| wtforms | 3.2.2 | 3.3.1 | 3.3.1 | Flask-WTF 表單相依；保留本機 3.3.1，session/CSRF/表單回歸重驗。 |

Torch 兩邊原版本相同；雲端獨立安裝，原 29 差異的 null 不代表其映像沒裝 Torch。

額外安全更新保留原始掃描、官方來源與完整測試；不把更高版本當作無漏洞或科學品質改善。deep-translator 的歷史供應鏈通報仍列為未關閉風險。

// Roothinks source maintenance contract
// 檔案路徑: app/static/js/literature_origins.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 Literature origins 查詢、候選選取與來源匯入，保留外部 URL provenance。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/literature_origins.js
//路徑(./app/static/js/literature_origins.js) #版本 v0.9 #更版時間 20260209-0100
class LiteratureOriginsHandler {
    constructor(app) {
        this.app = app;
        this._pidOverride = '';
        this.init();
    }

    /**
     * 目前的專案 id —— 每次讀取都重新解析，**刻意不快取**。
     *
     * 這裡原本是在建構子做 `this.pid = this.resolvePid()` 存成固定欄位。
     * 但本物件比 literatureApp.loadBootstrap() 更早建立，建構當下
     * app.currentPid 還是空的，於是 pid 永遠停在「當時能拿到的值」
     * （沒帶 ?pid 進來時就是空字串或 localStorage 的舊值）。
     * bootstrap 之後只有 switchProject() 會回頭同步 pid，初次載入不會，
     * 結果就是上傳歷史一直拿錯的 pid 去查 /api/literature/status/<pid>：
     * 檔案明明已經出現在 2.3 流程表，這裡卻永遠顯示 No files uploaded。
     */
    get pid() {
        return this.resolvePid();
    }

    /** 保留可寫入：switchProject() 會直接指派。寫入的值只當備援，
     *  app.currentPid 仍優先，避免又出現「指派過就固定住」的老問題。 */
    set pid(value) {
        this._pidOverride = String(value || '').trim();
    }

    resolvePid() {
        const fromApp = (this.app && this.app.currentPid) ? String(this.app.currentPid).trim() : '';
        if (fromApp) return fromApp;

        if (this._pidOverride) return this._pidOverride;

        const urlParams = new URLSearchParams(window.location.search);
        const fromUrl = (urlParams.get('pid') || '').trim();
        if (fromUrl) return fromUrl;

        const fromStorage = (localStorage.getItem('activePid') || '').trim();
        return fromStorage;
    }

    init() {
        console.log(`[Origins] Initializing for PID: ${this.pid}`);
        if (!this.pid) {
            console.warn('[Origins] PID not resolved. Please select a project from PAQ/Dashboard first.');
            return;
        }

        // --- A. File Upload Bindings ---
        // [Fix] 禁用 fileInput onchange 的绑定，因为 literature_app.js 已经处理了
        // 这避免了重复上传处理导致的对话框重复和文件上传延迟问题
        const fileInput = document.getElementById('pdfUploadInput');
        const uploadBtn = document.getElementById('btnUpload'); 

        // 1. FileInput onchange 已被 literature_app.js 处理，不再在此处理
        if (fileInput) {
            console.log("[Origins] FileInput change event delegated to literature_app.js");
        } else {
            console.warn("[Origins] Notice: #pdfUploadInput not found (This is normal if not in Upload tab).");
        }

        // 2. Upload button click 已被 literature_app.js 处理，不在此处理
        if (uploadBtn && fileInput) {
            console.log("[Origins] Upload button click delegated to literature_app.js");
        }

        // --- B. Context History Bindings (New in v0.9) ---
        this.bindContextEvents();

        // --- C. Initial Load ---
        this.loadFileList();
        this.loadContextHistory(); // [v0.9] 載入歷史
        
        console.log("[System] LiteratureOrigins v0.9 (Context + Upload Fixed) loaded.");
    }

    // ==========================================
    // Section 1: Context Management (New Logic)
    // ==========================================

    bindContextEvents() {
        const btnSave = document.getElementById('btnConfirmContext');
        const txtInput = document.getElementById('manualTopic');

        if (btnSave && txtInput) {
            btnSave.onclick = () => {
                const context = txtInput.value.trim();
                if (!context) {
                    alert("Please enter a research topic first.");
                    return;
                }
                this.saveContext(context);
            };
        }
    }

    saveContext(contextText) {
        this.app.log("[Origins] Saving context...");
        const btnSave = document.getElementById('btnConfirmContext');
        if(btnSave) btnSave.disabled = true;

        fetch('/api/literature/save_context', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.pid, context: contextText })
        })
        .then(r => r.json())
        .then(res => {
            if (res.status === 'success') {
                this.app.log("[Origins] Context saved.");
                // 重新載入列表以顯示最新一筆
                this.loadContextHistory();
                // 視覺回饋
                const input = document.getElementById('manualTopic');
                if(input) input.classList.add('is-valid');
                setTimeout(() => { if(input) input.classList.remove('is-valid'); }, 2000);
            } else {
                alert("Save Failed: " + res.message);
            }
        })
        .catch(err => {
            console.error(err);
            alert("Network Error saving context.");
        })
        .finally(() => {
            if(btnSave) btnSave.disabled = false;
        });
    }

    loadContextHistory() {
        const container = document.getElementById('contextHistoryList');
        if (!container) return;

        // 保持 loading 狀態不覆蓋既有內容太久，但初次載入需要提示
        // container.innerHTML = '<div class="text-muted small p-2">Loading...</div>';

        fetch(`/api/literature/get_context_history?pid=${this.pid}`)
            .then(r => r.json())
            .then(data => {
                this.renderContextHistory(data);
            })
            .catch(err => {
                console.error("[Origins] Failed to load history:", err);
                container.innerHTML = '<div class="text-danger small p-2">Error loading history.</div>';
            });
    }

    renderContextHistory(historyData) {
        const container = document.getElementById('contextHistoryList');
        if (!container) return;

        if (!historyData || historyData.length === 0) {
            container.innerHTML = '<div class="text-muted small p-2 fst-italic">No history yet. Define your topic above.</div>';
            return;
        }

        // 限制顯示前 5 筆，避免過長
        const displayItems = historyData.slice(0, 5);

        let html = '<div class="list-group list-group-flush">';
        displayItems.forEach((item, index) => {
            // 處理時間顯示
            let timeStr = item.date_str || 'Just now';
            // 截斷過長的文字
            let shortText = item.text.length > 60 ? item.text.substring(0, 60) + '...' : item.text;
            
            // [Interactive] 點擊觸發 restoreContext
            // 使用 encodeURIComponent 避免引號破壞 HTML
            const safeText = encodeURIComponent(item.text);

            html += `
            <a href="#" class="list-group-item list-group-item-action py-2" 
               onclick="event.preventDefault(); window.literatureOrigins.restoreContext(decodeURIComponent('${safeText}'))">
                <div class="d-flex w-100 justify-content-between align-items-center">
                    <small class="text-primary fw-bold text-truncate" style="max-width: 70%;">${shortText}</small>
                    <small class="text-muted" style="font-size: 0.7rem;">${timeStr}</small>
                </div>
            </a>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
    }

    restoreContext(fullText) {
        const input = document.getElementById('manualTopic');
        if (input) {
            input.value = fullText;
            // 觸發閃爍動畫提示 User 資料已回填
            input.classList.add('bg-warning-subtle');
            setTimeout(() => input.classList.remove('bg-warning-subtle'), 500);
            
            this.app.log("[Origins] Context restored from history.");
            
            // 自動滾動到輸入框
            input.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    // ==========================================
    // Section 2: File Upload Management (Kept v0.8 logic)
    // ==========================================

    handleUpload(e) {
        const file = e.target.files[0];
        if (!file) return;

        this.app.log(`[Origins] Uploading: ${file.name}`);
        const progressContainer = document.getElementById('uploadProgressContainer');
        const progressBar = document.getElementById('uploadProgressBar');
        
        // 重置並顯示進度條
        if(progressContainer) progressContainer.style.display = 'block';
        if(progressBar) {
            progressBar.className = "progress-bar progress-bar-striped progress-bar-animated bg-success";
            progressBar.style.width = '0%';
        }

        const formData = new FormData();
        formData.append('file', file);
        formData.append('pid', this.pid);

        const xhr = new XMLHttpRequest();
        // [Fix] 路徑修正為符合 literature_routes.py v3.3 的 /api/literature/upload
        // 原 v0.8 寫 /literature/api/upload，依據 Blueprint 定義可能需要調整
        // 假設 Blueprint prefix 為 '/' 或 '/api'，此處依據 v3.3 的 route definition: @literature_bp.route('/api/literature/upload')
        xhr.open('POST', '/api/literature/upload', true);

        xhr.upload.onprogress = (event) => {
            if (event.lengthComputable) {
                const percent = (event.loaded / event.total) * 100;
                if(progressBar) progressBar.style.width = percent + '%';
            }
        };

        xhr.onload = () => {
            if (xhr.status === 200) {
                try {
                    const res = JSON.parse(xhr.responseText);
                    if (res.status === 'success') {
                        this.app.log(`[Origins] Success: ${res.paper_id}`);
                        if(progressBar) progressBar.style.width = '100%';
                        this.loadFileList();
                        // 若有 Pipeline 表格也刷新
                        if(this.app.refreshPipelineTable) this.app.refreshPipelineTable();
                    } else {
                        this.app.log(`[Error] Upload Failed: ${res.message}`);
                        alert("Upload Failed: " + res.message);
                    }
                } catch(e) {
                    this.app.log(`[Error] Invalid JSON: ${xhr.responseText}`);
                }
            } else {
                this.app.log(`[Error] Server Error: ${xhr.status}`);
            }
            // 延遲隱藏進度條
            setTimeout(() => { if(progressContainer) progressContainer.style.display = 'none'; }, 2000);
            e.target.value = ''; // 清空 Input
        };

        xhr.onerror = () => {
            this.app.log("[Network] Error during upload.");
            if(progressContainer) progressContainer.style.display = 'none';
        };

        xhr.send(formData);
    }

    loadFileList() {
        const container = document.getElementById('fileListContainer');
        if (!container) return;

        // 這裡需要對應 literature_routes.py v3.3 的 status API 
        // 或是我們需要一個單獨的 list files API?
        // v3.3 中有一個 get_status 是回傳詳細列表。
        // 但為了輕量化，這裡我們可以使用 status API 來渲染簡易列表
        
        container.innerHTML = '<div class="text-center text-muted small py-3"><span class="spinner-border spinner-border-sm me-2"></span>Loading files...</div>';

        fetch(`/api/literature/status/${this.pid}`)
            .then(r => r.json())
            .then(res => {
                const files = res.papers || [];
                if (files.length === 0) {
                    container.innerHTML = '<div class="text-center text-muted small py-3">No files uploaded.</div>';
                } else {
                    let html = `
                    <div class="table-responsive">
                        <table class="table table-hover table-sm align-middle mb-0" style="font-size: 0.85rem;">
                            <thead class="table-light">
                                <tr>
                                    <th style="width: 50%;">Filename</th>
                                    <th style="width: 30%;">Status</th>
                                    <th style="width: 20%;">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                    `;

                    html += files.map(f => {
                        // 狀態標籤。
                        // 原本讀 status_trans / status_fix / status_cv，但那三個是
                        // 內部流程欄位、已不再對外提供，照舊讀的話所有檔案都會變成
                        // Pending（看起來像上傳歷史壞掉，其實是欄位沒了）。
                        // 改吃中性的 progress_state / progress_pct。
                        const STATE_BADGE = {
                            uploaded:    ['bg-secondary', '待處理'],
                            analyzing:   ['bg-info text-dark', '解析中'],
                            analyzed:    ['bg-primary', '已解析'],
                            translating: ['bg-info text-dark', '翻譯中'],
                            completed:   ['bg-success', '完成'],
                            failed:      ['bg-danger', '失敗'],
                        };
                        const [badgeCls, badgeText] =
                            STATE_BADGE[f.progress_state] || STATE_BADGE.uploaded;
                        const pctText = (typeof f.progress_pct === 'number' && f.progress_pct > 0)
                            ? ` ${f.progress_pct}%` : '';
                        let badge = `<span class="badge ${badgeCls}">${badgeText}${pctText}</span>`;

                        return `
                        <tr>
                            <td>
                                <div class="text-truncate" style="max-width: 180px;" title="${f.filename}">
                                    <i class="bi bi-file-earmark-pdf-fill text-danger me-1"></i>
                                    <span class="fw-bold text-dark">${f.filename}</span>
                                </div>
                                <div class="text-muted" style="font-size: 0.7em;">${f.paper_id}</div>
                            </td>
                            <td>${badge}</td>
                            <td>
                                <button class="btn btn-sm btn-link text-danger p-0" 
                                        onclick="event.stopPropagation(); window.literatureOrigins.deleteFile('${f.paper_id}')">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </td>
                        </tr>`;
                    }).join('');

                    html += `</tbody></table></div>`;
                    container.innerHTML = html;
                }
            })
            .catch(err => {
                console.error("Load file list failed:", err);
                container.innerHTML = '<div class="text-danger small p-3">Failed to load files.</div>';
            });
    }

    deleteFile(paperId) {
        if (!confirm(`Permanently delete '${paperId}' and all its data?`)) return;

        this.app.log(`[Origins] Deleting ${paperId}...`);
        
        // 假設有 delete API，若沒有則需在 routes 補上。
        // 為了避免 404，這裡先暫時保留框架，待後端補上 delete API
        // 根據 v3.3 routes，目前尚未實作 delete。
        // 依照「不接受 AI 建議」原則，我不會擅自呼叫不存在的 API。
        // 但為了不閹割 v0.8 的 delete 功能，我將其保留並標註 TODO。
        alert("Delete API pending backend implementation (v3.3 literature_routes.py does not have /delete yet).");
        
        /* fetch('/api/literature/delete', { ... }) 
        */
    }
}

// Auto-Attach Logic
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        // 嘗試獲取全局 app 實例
        if (window.literatureApp) {
            window.literatureOrigins = new LiteratureOriginsHandler(window.literatureApp);
            console.log("[System] LiteratureOrigins attached.");
        } else {
            console.error("[System] literatureApp missing for Origins. creating dummy app wrapper.");
            // Fallback for independent testing
            window.literatureOrigins = new LiteratureOriginsHandler({
                currentPid: (localStorage.getItem('activePid') || ''),
                log: console.log,
                refreshPipelineTable: () => console.log("Refresh Table Triggered")
            });
        }
    }, 500); 
});


// Roothinks source maintenance contract
// 檔案路徑: app/static/js/literature_scholar.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 Scholar 搜尋來源、查詢、候選選取與匯入 Literature 的互動流程。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/literature_scholar.js
//路徑(./app/static/js/literature_scholar.js) #版本 v1.3 #更版時間 20260209-0200
class LiteratureScholarHandler {
    constructor(app) {
        this.app = app; 
        // [Restored] 恢復輪詢機制變數
        this.pollingInterval = null;
        this.pid = this.resolvePid();
        this.init();
    }

    resolvePid() {
        const fromApp = (this.app && this.app.currentPid) ? String(this.app.currentPid).trim() : '';
        if (fromApp) return fromApp;

        const urlParams = new URLSearchParams(window.location.search);
        const fromUrl = (urlParams.get('pid') || '').trim();
        if (fromUrl) return fromUrl;

        return (localStorage.getItem('activePid') || '').trim();
    }

    escapeHtml(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    formatCitationTitleFirst(citation) {
        const raw = String(citation || '').trim();
        const m = raw.match(/^(.+?)\s*\((\d{4}[a-z]?)\)\.\s*(.+?)\.\s*(.+)$/i);
        if (!m) return raw;
        const authors = m[1].trim();
        const year = m[2].trim();
        const title = m[3].trim();
        const rest = m[4].trim();
        return `${title}. ${authors} (${year}). ${rest}`;
    }

    init() {
        const btn = document.getElementById('btnSearch');
        if(btn) {
            // [Fix] 修正綁定邏輯，確保不重複綁定
            btn.onclick = (e) => {
                e.preventDefault();
                this.runSearch();
            };
            console.log("[Scholar] Search Button Bound (v1.3 Enhanced).");
        } else {
            console.error("[Scholar] #btnSearch not found.");
        }
    }

    runSearch() {
        this.pid = this.resolvePid();
        if (!this.pid) {
            alert("找不到專案 ID，請先到 PAQ 選定專案。");
            return;
        }

        // 從 DOM 獲取當前 Context
        const topicInput = document.getElementById('manualTopic');
        const topic = topicInput ? topicInput.value : "";
        
        if (!topic.trim()) { 
            alert("請先輸入或選擇研究主題 (Context)！"); 
            if(topicInput) topicInput.focus();
            return; 
        }
        
        // 1. UI 狀態鎖定 (Loading)
        this.setLoadingState(true);
        this.app.log(`[Scholar] Dispatching Task 3 for: ${topic.substring(0, 30)}...`);

        // 2. [Restored] 開啟 Google Scholar (使用者輔助功能)
        // 這是原版 v0.4 的貼心功能，予以保留
        try {
            window.open(`https://scholar.google.com/scholar?q=${encodeURIComponent(topic)}`, '_blank');
        } catch(e) { console.warn("Popup blocked?"); }

        // 3. 呼叫後端 Task 3 (使用新的 /api/literature/search 接口，但需支援 Async/Polling)
        // 注意：為了符合 v3.3 routes 的設計，這裡假設 search API 是同步的。
        // 但若 User 強調 Polling，表示後端可能是 Async。
        // 這裡採取「混合策略」：先呼叫 search，若後端回傳 task_id 則輪詢，若回傳 results 則直接渲染。
        
        fetch('/api/literature/search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.pid, context: topic })
        })
        .then(r => r.json())
        .then(d => {
            if (d.status === 'success') {
                if (d.task_log_id) {
                    // Case A: Async Task (需要 Polling)
                    this.app.log(`[Scholar] Task Started (LogID: ${d.task_log_id}). Polling...`);
                    this.startPolling(d.task_log_id);
                } else if (d.results) {
                    // Case B: Sync Return (直接渲染) - 這是 v3.3 route 的目前行為
                    this.app.log("[Scholar] Analysis Complete (Sync).");
                    this.renderResults(JSON.stringify(d.results)); // 統一轉字串傳入 render
                    this.setLoadingState(false);
                }
            } else {
                throw new Error(d.message || "Unknown Error");
            }
        })
        .catch(err => {
            this.setLoadingState(false);
            this.app.log(`[Error] Scholar Dispatch Failed: ${err}`);
            alert("任務啟動失敗: " + err);
        });
    }

    // [Restored] 輪詢邏輯
    startPolling(logId) {
        if (this.pollingInterval) clearInterval(this.pollingInterval);

        this.pollingInterval = setInterval(() => {
            fetch(`/api/literature/task/status/${logId}`) // 假設有通用 Task Status API
                .then(r => r.json())
                .then(d => {
                    if (d.status === 'done' || d.status === 'error') {
                        clearInterval(this.pollingInterval);
                        this.pollingInterval = null;
                        this.setLoadingState(false);

                        if (d.status === 'done') {
                            this.app.log("[Scholar] Analysis Complete (Async).");
                            this.renderResults(d.result_json); // 假設回傳結構
                        } else {
                            this.app.log(`[Scholar] Error: ${d.message}`);
                            alert(`LAVA Error: ${d.message}`);
                        }
                    }
                })
                .catch(err => {
                    console.error("Polling Network Error", err);
                    // 不停止 Polling，避免因短暫網路波動中斷
                });
        }, 2000); // 放寬到 2秒
    }

    setLoadingState(isLoading) {
        const btn = document.getElementById('btnSearch');
        const btnText = document.getElementById('btnSearchText');
        const spinner = document.getElementById('btnSearchSpinner');
        
        if (isLoading) {
            if(btn) btn.disabled = true;
            if(btnText) btnText.innerText = "AI Analyzing & Reasoning..."; 
            if(spinner) spinner.style.display = "inline-block";
        } else {
            if(btn) btn.disabled = false;
            if(btnText) btnText.innerHTML = '<i class="bi bi-google me-2"></i>執行雲端分析並開啟 Scholar'; 
            if(spinner) spinner.style.display = "none";
        }
    }

    renderResults(dataInput) {
        // 支援傳入 JSON 物件或 JSON 字串
        let data = dataInput;
        if (typeof dataInput === 'string') {
            try {
                data = JSON.parse(dataInput);
            } catch(e) {
                console.error("Parse Error", e);
                return;
            }
        }

        const area = document.getElementById('searchResultArea');
        const listContainer = document.getElementById('apaList');
        const countBadge = document.getElementById('resultCount');
        
        if(!area || !listContainer) return;

        area.style.display = 'block';
        
        // 1. [New] 渲染 Reasoning (推薦理由)
        let html = '';
        if (data.reasoning) {
            html += `
            <div class="alert alert-info border-info shadow-sm mb-3">
                <div class="fw-bold text-info-emphasis mb-1">
                    <i class="bi bi-lightbulb-fill me-2"></i>AI 搜尋策略與推薦理由：
                </div>
                <div class="small text-dark" style="line-height: 1.6;">
                    ${this.escapeHtml(data.reasoning)}
                </div>
            </div>`;
        }

        // 2. [New] 渲染 Keywords
        if (data.keywords && data.keywords.length > 0) {
            html += `<div class="mb-3"><span class="small text-muted fw-bold me-2">建議關鍵字：</span>`;
            data.keywords.forEach(kw => {
                const query = encodeURIComponent(kw);
                const safeKw = this.escapeHtml(kw);
                html += `
                <a href="https://scholar.google.com/scholar?q=${query}" target="_blank" 
                   class="badge bg-light text-dark border text-decoration-none me-1 mb-1 hover-shadow">
                    <i class="bi bi-search me-1"></i>${safeKw}
                </a>`;
            });
            html += `</div>`;
        }

        // 3. [Enhanced] 渲染 Citations (APA)
        const citations = data.apa_citations || [];
        const paperList = Array.isArray(data.papers) ? data.papers : [];
        if(countBadge) countBadge.innerText = `${citations.length} papers`;

        if (citations.length > 0) {
            html += `<div class="list-group list-group-flush border rounded">`;
            citations.forEach((cite, idx) => {
                // 嘗試從 APA 字串中提取標題以優化搜尋連結
                let query = cite;
                const titleMatch = cite.match(/\)\.\s(.*?)\.\s/); 
                if(titleMatch) query = titleMatch[1];

                const searchUrl = `https://scholar.google.com/scholar?q=${encodeURIComponent(query)}`;
                const formatted = this.escapeHtml(this.formatCitationTitleFirst(cite));
                const paperReasonRaw = paperList[idx] && paperList[idx].reason ? String(paperList[idx].reason) : "";
                const paperReason = this.escapeHtml(paperReasonRaw);
                const num = String(idx + 1).padStart(2, '0');
                
                html += `
                <div class="list-group-item bg-white py-3">
                    <div class="d-flex gap-3">
                        <div class="text-secondary fw-bold">${num}</div>
                        <div class="flex-grow-1">
                            <a href="${searchUrl}" target="_blank" class="text-decoration-none text-dark hover-primary" style="display: block;">
                                <div class="mb-1" style="font-family: 'Times New Roman', serif; font-size: 1.05rem; cursor: pointer; transition: color 0.2s;" onmouseover="this.style.color='#0d6efd'" onmouseout="this.style.color='#212529'">
                                    ${formatted}
                                </div>
                            </a>
                            ${paperReason ? `<div class="small text-muted mt-1"><i class="bi bi-info-circle me-1"></i>${paperReason}</div>` : ""}
                        </div>
                    </div>
                </div>`;
            });
            html += `</div>`;
        } else {
            html += `<div class="text-muted text-center py-3">No specific citations found.</div>`;
        }
        
        listContainer.innerHTML = html;
        this.app.log("[Scholar] Results rendered with Reasoning.");
    }
}

// Auto-Attach
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        if (window.literatureApp) {
            window.literatureScholar = new LiteratureScholarHandler(window.literatureApp);
            // 覆寫 App 的 runSearch 方法以確保相容性
            window.literatureApp.runSearch = () => window.literatureScholar.runSearch();
            console.log("[System] LiteratureScholar v1.3 attached.");
        }
    }, 600);
});


//路徑(app/static/js/literature_app.js) #版本 v4.0-StatusResilience #更版時間 20260430-1915

/**
 * Roothinks Literature Application Logic (Merged v3.8)
 * 保有 v3.7 的所有功能 (Context, Search, Upload, Editor)
 * 並整合 v2.1 的 Pipeline Trigger 與 Status Polling 機制。
 */

window.literatureApp = {
    currentPid: "",
    refreshInterval: null,
    paperStatusMap: {},
    paperStageMap: {},
    runtimeProfile: null,
    pipelineLastGoodPapers: [],
    pipelineLastError: "",

    // [Add] Log helper
    log: function(msg) {
        console.log(msg);
    },

    init: function() {
        console.log("[literatureApp] Initializing v4.0 (Status Resilience)...");
        // 從 URL 獲取 PID
        const urlParams = new URLSearchParams(window.location.search);
        const pid = urlParams.get('pid');
        if (pid) this.currentPid = pid;

        this.bindEvents();
        this.loadBootstrap().finally(() => {
            this.loadSystemProfile();
            this.startStatusPolling();
            this.loadContextHistory();
            this.loadSavedSearchResults();
            this.editor.init();
            // [fix] 上傳歷史必須等 bootstrap 決定出 currentPid 之後再抓一次。
            // LiteratureOriginsHandler 在建構時就呼叫過 loadFileList()，但那時
            // pid 還沒解析出來（沒帶 ?pid 進頁面時是空字串），查到的是錯的專案，
            // 而初次載入原本沒有任何地方會回頭重抓——結果就是檔案已經在 2.3
            // 流程表出現了，上傳歷史卻永遠停在 No files uploaded。
            if (window.literatureOrigins) {
                window.literatureOrigins.loadFileList();
            }
        });
    },

    loadSystemProfile: function() {
        fetch('/api/literature/system_profile')
            .then(res => res.json())
            .then(data => {
                if (data && data.status === 'success') {
                    this.runtimeProfile = data.profile || {};
                    this.renderSystemProfile();
                }
            })
            .catch(err => console.error('System profile error:', err));
    },

    renderSystemProfile: function() {
        const box = document.getElementById('runtimeModeBadges');
        if (!box) return;
        const p = this.runtimeProfile || {};
        const strictBadge = p.demo_strict_mode
            ? '<span class="badge bg-danger-subtle text-danger border border-danger">DEMO STRICT</span>'
            : '<span class="badge bg-secondary-subtle text-secondary border border-secondary">DEMO NORMAL</span>';
        const nllb = String(p.nllb_mode || 'unknown');
        const judge = String(p.judge_mode || 'unknown');
        const workers = Number(p.page_workers || 1);
        const flowaSub = p.flowa_subprocess_enabled ? 'subproc' : 'inline';
        box.innerHTML = `
            ${strictBadge}
            <span class="badge bg-light text-dark border">NLLB: ${nllb}</span>
            <span class="badge bg-light text-dark border">Judge: ${judge}</span>
            <span class="badge bg-light text-dark border">Workers: ${workers}</span>
            <span class="badge bg-light text-dark border">FlowA: ${flowaSub}</span>
        `;
    },

    preloadTranslationModels: function() {
        const btn = document.getElementById('btnPreloadNllb');
        const old = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>預熱中...';
        }
        fetch('/api/literature/preload_translation_models', { method: 'POST' })
            .then(res => res.json())
            .then(data => {
                if (data.status !== 'success') {
                    throw new Error(data.message || 'preload failed');
                }
                alert('翻譯模組已就緒。');
            })
            .catch(err => {
                alert('預熱失敗: ' + err.message);
            })
            .finally(() => {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = old;
                }
            });
    },

    loadBootstrap: function() {
        return fetch(`/api/literature/bootstrap?pid=${encodeURIComponent(this.currentPid)}`)
            .then(res => res.json())
            .then(data => {
                if (data.status !== 'success') {
                    throw new Error(data.message || 'Bootstrap failed');
                }

                // [Security/UX Fix 20260722] bootstrap 現已依 membership 過濾（後端），
                // 這裡以它的權威回應對帳 localStorage 的 activePid 殘留：切帳號後，上一個
                // 帳號留下的 activePid 不屬於當前使用者時必須清掉，否則 scholar/origins
                // 會拿舊 pid 去打後端而吃 403，畫面看起來「沒清乾淨」。
                const ownedPids = new Set((data.formal_projects || []).map(p => p.pid));
                try {
                    if (data.active_project) {
                        localStorage.setItem('activePid', data.active_project);
                    } else {
                        const stored = (localStorage.getItem('activePid') || '').trim();
                        if (!stored || !ownedPids.has(stored)) {
                            localStorage.removeItem('activePid');
                        }
                    }
                } catch (e) { /* localStorage 不可用時略過 */ }

                if (data.active_project) {
                    this.currentPid = data.active_project;
                } else {
                    // 當前帳號沒有任何有權限的正式專案（例：全新註冊帳號）。
                    // 清空 currentPid 與殘留 context，避免顯示他人資料。
                    this.currentPid = '';
                    const topicClear = document.getElementById('manualTopic');
                    if (topicClear) topicClear.value = '';
                }

                const pidEl = document.getElementById('currentPid');
                if (pidEl) pidEl.innerText = this.currentPid || '--';

                const topicInput = document.getElementById('manualTopic');
                if (topicInput && data.manual_context && !topicInput.value.trim()) {
                    topicInput.value = data.manual_context;
                }

                this.renderFormalProjectList(data.formal_projects || []);

                const url = new URL(window.location);
                if (this.currentPid) {
                    url.searchParams.set('pid', this.currentPid);
                } else {
                    url.searchParams.delete('pid');
                }
                window.history.replaceState({}, '', url);

                // 完全沒有有權限的專案 → 顯示友善空狀態，別讓狀態輪詢吐一排紅字/403。
                if (!this.currentPid && ownedPids.size === 0) {
                    this.renderNoProjectState();
                }
            })
            .catch(err => {
                console.error('[literatureApp] bootstrap error:', err);
                return this.loadBootstrapFallback();
            });
    },

    loadBootstrapFallback: function() {
        return fetch('/api/project/list?status=formal')
            .then(res => res.json())
            .then(data => {
                const projects = (data && data.success && Array.isArray(data.projects)) ? data.projects : [];
                const mapped = projects.map(p => ({
                    pid: p.project_id,
                    research_title: p.research_title || p.name,
                    name: p.name,
                }));

                if (!mapped.length) {
                    this.renderFormalProjectList([]);
                    const pidEl = document.getElementById('currentPid');
                    if (pidEl) pidEl.innerText = '--';
                    return;
                }

                const active = mapped.find(p => p.pid === this.currentPid) || mapped[0];
                this.currentPid = active.pid;

                const pidEl = document.getElementById('currentPid');
                if (pidEl) pidEl.innerText = this.currentPid;

                this.renderFormalProjectList(mapped);

                const url = new URL(window.location);
                url.searchParams.set('pid', this.currentPid);
                window.history.replaceState({}, '', url);

                this.loadContextHistory();
            })
            .catch(err => {
                console.error('[literatureApp] fallback bootstrap error:', err);
            });
    },

    renderFormalProjectList: function(projects) {
        const ul = document.getElementById('formalProjectList');
        if (!ul) return;

        if (!projects.length) {
            ul.innerHTML = '<li><span class="dropdown-item-text text-muted small">尚無正式專案</span></li>';
            return;
        }

        ul.innerHTML = projects.map(p => {
            const title = this._escapeHtml(p.research_title || p.name || p.pid);
            const pid = this._escapeHtml(p.pid);
            return `
                <li>
                    <a class="dropdown-item ${p.pid === this.currentPid ? 'active' : ''}" href="#" onclick="event.preventDefault(); window.literatureApp.switchProject('${pid}')">
                        <div class="fw-bold text-truncate">${title}</div>
                        <div class="small text-muted">${pid}</div>
                    </a>
                </li>
            `;
        }).join('');
    },

    switchProject: function(pid) {
        if (!pid || pid === this.currentPid) return;
        this.currentPid = pid;

        if (window.literatureScholar) {
            window.literatureScholar.pid = pid;
        }
        if (window.literatureOrigins) {
            window.literatureOrigins.pid = pid;
        }

        const pidEl = document.getElementById('currentPid');
        if (pidEl) pidEl.innerText = this.currentPid;

        const url = new URL(window.location);
        url.searchParams.set('pid', this.currentPid);
        window.history.pushState({}, '', url);

        const topicEl = document.getElementById('manualTopic');
        if (topicEl) topicEl.value = '';
        this.loadBootstrap().finally(() => {
            this.loadContextHistory();
            this.loadSavedSearchResults();
            // 用 startStatusPolling（會清舊 interval 再重啟），確保若先前因 403
            // 停掉輪詢，切到有權限的專案後輪詢能恢復。
            this.startStatusPolling();
            if (window.literatureOrigins) {
                window.literatureOrigins.loadFileList();
                window.literatureOrigins.loadContextHistory();
            }
        });
    },

    unlockProject: function() {
        const newPid = prompt('請輸入新的專案ID:', this.currentPid);
        if (newPid && newPid.trim() !== '') {
            this.currentPid = newPid.trim();
            document.getElementById('currentPid').innerText = this.currentPid;
            // 更新 URL
            const url = new URL(window.location);
            url.searchParams.set('pid', this.currentPid);
            window.history.pushState({}, '', url);
            // 重新載入資料
            this.loadContextHistory();
            if (typeof this.refreshUploadHistory === 'function') {
                this.refreshUploadHistory();
            }
            if (typeof this.refreshPipelineStatus === 'function') {
                this.refreshPipelineStatus();
            }
            alert(`已切換至專案: ${this.currentPid}`);
        }
    },

    bindEvents: function() {
        // 1. Context Definition (ID: btnConfirmContext / manualTopic)
        const btnCtx = document.getElementById('btnConfirmContext');
        if(btnCtx) {
            btnCtx.addEventListener('click', () => this.saveContext());
        }

        // 2. Search (ID: btnSearch)
        const btnSearch = document.getElementById('btnSearch');
        if(btnSearch) {
            btnSearch.addEventListener('click', () => this.executeSearch());
        }

        // 3. Upload (ID: btnUpload / pdfUploadInput)
        const btnUpload = document.getElementById('btnUpload');
        const fileInput = document.getElementById('pdfUploadInput');
        if(btnUpload && fileInput) {
            btnUpload.addEventListener('click', () => {
                // [Fix] 清空文件输入，防止同一文件无法重新选择
                fileInput.value = '';
                fileInput.click();
            });
            fileInput.addEventListener('change', (e) => {
                if (e.target.files.length > 0) {
                    this.uploadFile(e.target.files[0]);
                }
            });
        }

        // [New v2.1] 4. Trigger Flow Actions (Dynamic Binding)
        document.addEventListener('click', (e) => {
            const aiBtn = e.target.closest('.btn-trigger-ai');
            if (aiBtn) {
                const paperId = aiBtn.getAttribute('data-id');
                this.triggerPipeline(paperId, aiBtn);
                return;
            }

            const transBtn = e.target.closest('.btn-trigger-translation');
            if (transBtn) {
                const paperId = transBtn.getAttribute('data-id');
                this.triggerTranslation(paperId, transBtn);
            }
        });

        // [ui] 批次執行與全選已移除——改為每列各自操作「文獻解析 / 文獻翻譯」。
        // runPipelineBatch / runTranslationBatch 兩支函式保留但不再有 UI 入口：
        // 解析要打雲端 LLM，批次會一次噴掉多篇費用且看不到中間結果。

        document.addEventListener('change', (e) => {
            if (e.target && e.target.classList && e.target.classList.contains('paper-checkbox')) {
                this.updateFlowBButtonState();
            }
        });
    },

    // --- Context Logic (Preserved v3.7) ---
    saveContext: function() {
        const text = document.getElementById('manualTopic').value;
        if(!text) { alert("Context cannot be empty"); return; }

        fetch('/api/literature/save_context', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid, context: text })
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') {
                alert("Context saved!");
                this.loadContextHistory();
            } else {
                alert("Error: " + data.message);
            }
        });
    },

    loadContextHistory: function() {
        const list = document.getElementById('contextHistoryList');
        if(!list) return;

        fetch(`/api/literature/get_context_history?pid=${this.currentPid}`)
        .then(res => res.json())
        .then(history => {
            if(!history || history.length === 0) {
                list.innerHTML = '<div class="text-center text-muted small py-3">No history.</div>';
                return;
            }
            let html = '';
            history.slice(0, 5).forEach(h => {
                const date = new Date(h.timestamp * 1000).toLocaleString();
                const rawText = String(h.text || '');
                const safePreview = this._escapeHtml(rawText.substring(0, 50));
                html += `
                    <div class="p-2 border-bottom small cursor-pointer hover-bg-light" onclick="document.getElementById('manualTopic').value='${this._escapeJs(rawText)}'">
                        <div class="fw-bold text-dark">${safePreview}...</div>
                        <div class="text-muted" style="font-size:0.75rem;">${this._escapeHtml(date)}</div>
                    </div>
                `;
            });
            list.innerHTML = html;
        });
    },

    // [New] Delete Paper Function
    deletePaper: function(paperId) {
        if (!confirm(`確定要刪除文件 ${paperId} 嗎？此操作無法復原。`)) return;
        
        fetch(`/api/literature/delete_paper`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid, paper_id: paperId })
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') {
                alert('文件已刪除');
                this.loadPipelineTable();
            } else {
                alert('刪除失敗: ' + (data.message || 'Unknown error'));
            }
        })
        .catch(err => {
            console.error('Delete error:', err);
            alert('刪除錯誤: ' + err.message);
        });
    },

    // --- Search Logic (Preserved v3.7) ---
    loadSavedSearchResults: function() {
        /**
         * 頁面初始化時加載已保存的搜尋結果
         * 如果存在則直接顯示，無需重新執行 Task 3
         */
        console.log("[literatureApp.loadSavedSearchResults] Starting...");
        console.log("[literatureApp.loadSavedSearchResults] currentPid:", this.currentPid);
        
        const url = `/api/literature/get_search_results?pid=${this.currentPid}`;
        console.log("[literatureApp.loadSavedSearchResults] Fetching from:", url);
        
        fetch(url)
            .then(res => {
                console.log("[literatureApp.loadSavedSearchResults] Response status:", res.status);
                return res.json();
            })
            .then(data => {
                console.log("[literatureApp.loadSavedSearchResults] Response data:", data);
                if(data.status === 'success' && data.results) {
                    console.log("[literatureApp] ✓ Loaded saved search results");
                    this._displaySearchResults(data.results);
                } else {
                    console.log("[literatureApp] No saved search results found (expected on first visit)");
                }
            })
            .catch(err => {
                console.error("[literatureApp] ❌ Could not load saved results:", err);
            });
    },

    executeSearch: function() {
        const context = document.getElementById('manualTopic').value;
        if(!context) { alert("Please define context first."); return; }

        const btn = document.getElementById('btnSearch');
        const spinner = document.getElementById('btnSearchSpinner');
        const resultArea = document.getElementById('searchResultArea');

        btn.disabled = true;
        spinner.style.display = 'inline-block';
        
        fetch('/api/literature/search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid, context: context })
        })
        .then(res => res.json())
        .then(data => {
            btn.disabled = false;
            spinner.style.display = 'none';
            resultArea.style.display = 'block';

            if(data.status === 'success' && data.results) {
                // 保存搜尋結果
                this._saveSearchResults(data.results);

                // 顯示結果
                this._displaySearchResults(data.results);

                // 後端已把本輪候選 merge 進持久文獻庫；刷新 Library 面板讓累積可見。
                if (window.literatureLibrary && window.literatureLibrary.loadLibrary) {
                    window.literatureLibrary.loadLibrary();
                }
            } else {
                document.getElementById('apaList').innerHTML = `<span class="text-danger">Error: ${this._escapeHtml(data.message || '無結果')}</span>`;
            }
        })
        .catch(err => {
            btn.disabled = false;
            spinner.style.display = 'none';
            alert("Search failed: " + err);
        });
    },

    _saveSearchResults: function(results) {
        /**
         * 將搜尋結果保存到後端
         */
        console.log("[literatureApp._saveSearchResults] Starting save...");
        console.log("[literatureApp._saveSearchResults] PID:", this.currentPid);
        console.log("[literatureApp._saveSearchResults] Results object:", results);
        console.log("[literatureApp._saveSearchResults] Results keys:", Object.keys(results));
        
        const payload = {
            pid: this.currentPid,
            results: results
        };
        
        console.log("[literatureApp._saveSearchResults] Full payload:", JSON.stringify(payload, null, 2));
        
        fetch('/api/literature/save_search_results', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        })
        .then(res => {
            console.log("[literatureApp._saveSearchResults] Response status:", res.status);
            return res.json();
        })
        .then(data => {
            console.log("[literatureApp._saveSearchResults] Response data:", data);
            if(data.status === 'success') {
                console.log("[literatureApp] ✓ Search results saved successfully");
            } else {
                console.warn("[literatureApp] ❌ Failed to save results:", data.message);
            }
        })
        .catch(err => {
            console.error("[literatureApp] ❌ Error saving results:", err);
        });
    },

    _escapeHtml: function(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _escapeJs: function(text) {
        return String(text || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    },

    _formatCitationTitleFirst: function(citation) {
        const raw = String(citation || '').trim();
        const m = raw.match(/^(.+?)\s*\((\d{4}[a-z]?)\)\.\s*(.+?)\.\s*(.+)$/i);
        if (!m) return raw;
        const authors = m[1].trim();
        const year = m[2].trim();
        const title = m[3].trim();
        const rest = m[4].trim();
        return `${title}. ${authors} (${year}). ${rest}`;
    },

    _buildScholarUrl: function(citation) {
        const raw = String(citation || '');
        const titleMatch = raw.match(/\)\.\s(.*?)\.\s/);
        const query = titleMatch ? titleMatch[1] : raw;
        return `https://scholar.google.com/scholar?q=${encodeURIComponent(query)}`;
    },

    _displaySearchResults: function(results) {
        /**
         * 渲染搜尋結果到 UI
         */
        console.log("[literatureApp._displaySearchResults] Starting render...");
        console.log("[literatureApp._displaySearchResults] Results object:", results);
        console.log("[literatureApp._displaySearchResults] Results type:", typeof results);
        console.log("[literatureApp._displaySearchResults] Results keys:", Object.keys(results || {}));
        
        // 顯示搜尋結果區域
        const resultArea = document.getElementById('searchResultArea');
        if(resultArea) {
            resultArea.style.display = 'block';
            console.log("[literatureApp._displaySearchResults] Showed searchResultArea");
        }
        
        const citations = results.apa_citations || [];
        const reasoning = results.reasoning || '';
        const paperList = Array.isArray(results.papers) ? results.papers : [];
        const list = document.getElementById('apaList');
        
        console.log("[literatureApp._displaySearchResults] Target element (apaList):", list);
        console.log("[literatureApp._displaySearchResults] Citations count:", citations.length);
        console.log("[literatureApp._displaySearchResults] Reasoning:", reasoning.substring(0, 100));

        document.getElementById('resultCount').innerText = `${citations.length} papers`;
        
        let html = '';
        // 顯示推薦理由
        if(reasoning) {
            html += `<div class="alert alert-info mb-3"><strong><i class="bi bi-lightbulb me-2"></i>AI 推薦理由:</strong><br>${this._escapeHtml(reasoning)}</div>`;
        }
        
        // 顯示清除結果按鈕
        html += `<div class="mb-3"><button class="btn btn-sm btn-outline-danger" onclick="window.literatureApp.clearSearchResults();">清除結果，重新搜尋</button></div>`;
        
        // 顯示文獻列表
        html += '<div class="list-group list-group-flush border rounded">';
        citations.forEach((citation, idx) => {
            const searchUrl = this._buildScholarUrl(citation);
            const formatted = this._escapeHtml(this._formatCitationTitleFirst(citation));
            const perPaperReason = this._escapeHtml(
                (paperList[idx] && paperList[idx].reason) ? String(paperList[idx].reason) : ''
            );
            const num = String(idx + 1).padStart(2, '0');
            
            html += `
            <div class="list-group-item bg-white py-3">
                <div class="d-flex gap-3">
                    <div class="text-secondary fw-bold">${num}</div>
                    <div class="flex-grow-1">
                        <a href="${searchUrl}" target="_blank" class="text-decoration-none text-dark" style="display: block;">
                            <div class="mb-1" style="font-family: 'Times New Roman', serif; font-size: 1.05rem; cursor: pointer; transition: color 0.2s;" onmouseover="this.style.color='#0d6efd'" onmouseout="this.style.color='#212529'">
                                ${formatted}
                            </div>
                        </a>
                        ${perPaperReason ? `<div class="small text-muted mt-1"><i class="bi bi-info-circle me-1"></i>${perPaperReason}</div>` : ''}
                    </div>
                </div>
            </div>`;
        });
        html += '</div>';
        list.innerHTML = html;
        console.log(`[literatureApp._displaySearchResults] ✓ Rendered ${citations.length} citations`);
    },

    clearSearchResults: function() {
        /**
         * 清除已保存的搜尋結果
         */
        if(!confirm("確定要清除搜尋結果嗎？")) return;
        
        fetch('/api/literature/clear_search_results', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid })
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') {
                document.getElementById('searchResultArea').style.display = 'none';
                document.getElementById('apaList').innerHTML = '';
                alert("搜尋結果已清除，您可以重新搜尋");
            }
        });
    },

    // --- Upload Logic (Preserved v3.7) ---
    uploadFile: function(file) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('pid', this.currentPid);

        const pBar = document.getElementById('uploadProgressBar');
        const pContainer = document.getElementById('uploadProgressContainer');
        if (pContainer && pBar) {
            pContainer.style.display = 'block';
            pBar.style.width = '0%';
        }

        fetch('/api/literature/upload', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (pBar) pBar.style.width = '100%';
            
            // 隱藏進度條
            if (pContainer) {
                setTimeout(() => { pContainer.style.display = 'none'; }, 500);
            }
            
            if (data.status === 'success') {
                alert(`上傳成功。\n文件 ID: ${data.paper_id}`);
                this.loadPipelineTable(); 
            } else {
                alert('上傳失敗: ' + data.message);
            }
        })
        .catch(err => {
            console.error(err);
            alert('上傳錯誤: ' + err.message);
        });
    },

    // --- Pipeline Status (Flow A / Flow B) ---
    startStatusPolling: function() {
        this.loadPipelineTable();
        if (this.refreshInterval) clearInterval(this.refreshInterval);
        this.refreshInterval = setInterval(() => {
            this.loadPipelineTable();
        }, 3000);
    },

    loadPipelineTable: function() {
        const pid = String(this.currentPid || '').trim();
        if (!pid) {
            // 尚未選定專案（例：全新帳號無任何有權限專案）→ 友善空狀態，不吐紅字。
            this.renderNoProjectState();
            return;
        }

        fetch(`/api/literature/status/${encodeURIComponent(pid)}`)
            .then(async (res) => {
                let data = null;
                try {
                    data = await res.json();
                } catch (e) {
                    throw new Error(`Status API JSON 解析失敗 (HTTP ${res.status})`);
                }
                if (!res.ok) {
                    // [UX Fix 20260722] 403 = 對此專案沒有權限（後端權限隔離正常運作）。
                    // 不再把技術字串 "Forbidden" 丟到畫面，改成友善提示 + 導回 Dashboard。
                    if (res.status === 403) {
                        const forbiddenErr = new Error('forbidden');
                        forbiddenErr.code = 'forbidden';
                        throw forbiddenErr;
                    }
                    const msg = String(data?.message || data?.msg || `HTTP ${res.status}`);
                    throw new Error(`Status API 失敗: ${msg}`);
                }
                if (!data || data.status === 'error') {
                    const msg = String(data?.message || data?.msg || 'unknown error');
                    throw new Error(`Status API 回傳錯誤: ${msg}`);
                }
                if (!Array.isArray(data.papers)) {
                    throw new Error('Status API 缺少 papers 陣列');
                }
                return data;
            })
            .then(data => {
                this.pipelineLastError = "";
                this.pipelineLastGoodPapers = data.papers;
                this.renderPipelineTable(data.papers);
            })
            .catch(err => {
                console.error('Status poll error:', err);
                if (err && err.code === 'forbidden') {
                    // 越權：停止輪詢並清畫面，避免持續紅字或殘留他人資料。
                    this.renderForbiddenState();
                    if (this.refreshInterval) { clearInterval(this.refreshInterval); this.refreshInterval = null; }
                    return;
                }
                this.pipelineLastError = String(err?.message || err || 'status poll failed');
                this.renderPipelineError(this.pipelineLastError);
            });
    },

    // 全新帳號 / 尚未選專案：友善空狀態，引導回 Dashboard。
    renderNoProjectState: function() {
        const tbody = document.getElementById('pipelineTableBody');
        if (!tbody) return;
        this.paperStatusMap = {};
        this.paperStageMap = {};
        this.pipelineLastGoodPapers = [];
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-muted">`
            + `尚未選定研究專案。<a href="/" class="fw-bold text-decoration-none ms-1">回 Dashboard 選擇你的專案 →</a>`
            + `</td></tr>`;
        this.updateFlowBButtonState();
    },

    // 對此專案沒有權限（後端 403）：友善提示，不顯示技術錯誤，引導回 Dashboard。
    renderForbiddenState: function() {
        const tbody = document.getElementById('pipelineTableBody');
        if (!tbody) return;
        this.paperStatusMap = {};
        this.paperStageMap = {};
        this.pipelineLastGoodPapers = [];
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-secondary">`
            + `<i class="bi bi-lock me-1"></i>你沒有此專案的存取權限。`
            + `<a href="/" class="fw-bold text-decoration-none ms-1">回 Dashboard 選擇你的專案 →</a>`
            + `</td></tr>`;
        this.updateFlowBButtonState();
    },

    renderPipelineError: function(message) {
        const tbody = document.getElementById('pipelineTableBody');
        if (!tbody) return;

        const msg = this._escapeHtml(String(message || 'Status poll failed'));
        if (Array.isArray(this.pipelineLastGoodPapers) && this.pipelineLastGoodPapers.length > 0) {
            // 保留上一次成功資料，避免暫時性 API 錯誤把畫面誤清空。
            this.renderPipelineTable(this.pipelineLastGoodPapers);
            tbody.insertAdjacentHTML(
                'afterbegin',
                `<tr class="table-warning"><td colspan="5" class="small text-dark py-2"><i class="bi bi-exclamation-triangle me-1"></i>狀態刷新暫時失敗，已保留上一版列表：${msg}</td></tr>`
            );
            return;
        }

        this.paperStatusMap = {};
        this.paperStageMap = {};
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-danger">Status API error: ${msg}</td></tr>`;
        this.updateFlowBButtonState();
    },

    canRunFlowBForPaper: function(paperId) {
        const status = this.paperStatusMap[paperId];
        if (status === 'ready_A' || status === 'ready_B') return true;
        const stages = this.paperStageMap[paperId] || {};
        return stages.s4 === 'done' || stages.s8 === 'done' || stages.s9 === 'done' || stages.s12 === 'done';
    },

    updateFlowBButtonState: function() {
        const btn = document.getElementById('btnRunFlowBBatch');
        if (!btn) return;

        const selectedIds = Array.from(document.querySelectorAll('.paper-checkbox:checked')).map(cb => cb.value);
        if (selectedIds.length === 0) {
            btn.disabled = true;
            btn.title = '請先勾選文件';
            return;
        }

        const blocked = selectedIds.filter(id => !this.canRunFlowBForPaper(id));
        if (blocked.length > 0) {
            btn.disabled = true;
            const preview = blocked.slice(0, 3).join(', ');
            btn.title = `尚未完成 Flow A: ${preview}${blocked.length > 3 ? ' ...' : ''}`;
            return;
        }

        btn.disabled = false;
        btn.title = '';
    },

    formatQueueDetails: function(details) {
        if (!details || typeof details !== 'object') return '';
        const lines = [];
        if (Array.isArray(details.queued) && details.queued.length > 0) {
            lines.push(`Queued: ${details.queued.join(', ')}`);
        }
        if (Array.isArray(details.busy) && details.busy.length > 0) {
            lines.push(`Busy: ${details.busy.join(', ')}`);
        }
        if (Array.isArray(details.missing_pdf) && details.missing_pdf.length > 0) {
            lines.push(`Missing PDF: ${details.missing_pdf.join(', ')}`);
        }
        if (Array.isArray(details.flow_a_not_ready) && details.flow_a_not_ready.length > 0) {
            lines.push(`Flow A not ready: ${details.flow_a_not_ready.join(', ')}`);
        }
        return lines.join('\n');
    },

    renderPipelineTable: function(papers) {
        const tbody = document.getElementById('pipelineTableBody');
        if (!tbody) return;
        this.paperStatusMap = {};
        this.paperStageMap = {};

        const checkedIds = new Set();
        document.querySelectorAll('.paper-checkbox:checked').forEach(cb => {
            checkedIds.add(cb.value);
        });

        if (!papers || papers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted">No papers found. Upload specific PDF to start.</td></tr>';
            this.updateFlowBButtonState();
            return;
        }

        let html = '';
        papers.forEach(p => {
            const rawStatus = p.flow_status || p.db_status || 'pending';
            let status = rawStatus;
            if (status === 'gold_ready' || status === 'rules_ready') status = 'ready_A';
            if (status === 'analyzing') status = 'processing_A';
            if (status === 'need_retry') status = 'failed';
            if (status === 'bilingual_ready') status = 'ready_B';
            this.paperStatusMap[p.paper_id] = status;
            this.paperStageMap[p.paper_id] = p.stages || {};

            const getColor = (state) => {
                if (state === 'done') return 'text-success';
                if (state === 'processing') return 'text-warning blink-text';
                if (state === 'error') return 'text-danger';
                return 'text-secondary opacity-25';
            };
            const stageIcon = (icon, state, label) => `
                <div class="d-flex flex-column align-items-center" title="${label}">
                    <i class="bi ${icon} fs-5 ${getColor(state)}"></i>
                    <span class="small" style="font-size:0.62rem;">${label}</span>
                </div>`;
            const arrowIcon = () => '<i class="bi bi-arrow-right text-muted small"></i>';
            const flowAReadyState = p.flow_a_ready ? 'done' : (status === 'processing_A' ? 'processing' : 'pending');
            const flowBReadyState = p.flow_b_ready ? 'done' : (status === 'processing_B' ? 'processing' : 'pending');

            let actionBtn = '';
            if (status === 'ready_B') {
                actionBtn = `<a href="/study/project/${this.currentPid}?paper_id=${p.paper_id}" class="btn btn-sm btn-success fw-bold"><i class="bi bi-book me-1"></i>Study</a>`;
            } else if (status === 'ready_A') {
                actionBtn = `<button class="btn btn-sm btn-outline-success btn-trigger-translation fw-bold" data-id="${p.paper_id}"><i class="bi bi-translate me-1"></i>文獻翻譯</button>`;
            } else if (status === 'processing_B') {
                actionBtn = '<button class="btn btn-sm btn-secondary" disabled><span class="spinner-border spinner-border-sm me-1"></span>翻譯中</button>';
            } else if (status === 'processing_A') {
                actionBtn = '<button class="btn btn-sm btn-secondary" disabled><span class="spinner-border spinner-border-sm me-1"></span>解析中</button>';
            } else if (status === 'failed') {
                actionBtn = `<button class="btn btn-sm btn-warning btn-trigger-ai fw-bold" data-id="${p.paper_id}" title="流程失敗，點擊重跑文獻解析"><i class="bi bi-arrow-clockwise me-1"></i>重跑解析</button>`;
            } else {
                actionBtn = `<button class="btn btn-sm btn-primary btn-trigger-ai fw-bold" data-id="${p.paper_id}"><i class="bi bi-play-circle me-1"></i>文獻解析</button>`;
            }

            html += `
            <tr>
                <td></td>
                <td>
                    <div class="fw-bold text-dark">${p.filename}</div>
                    <div class="small text-muted">ID: ${p.paper_id}</div>
                </td>
                <td>
                    <div class="d-flex align-items-center gap-2 flex-wrap">
                        ${stageIcon('bi-file-earmark-pdf', (p.stages || {}).s1 || 'pending', 'PDF')}
                        ${arrowIcon()}
                        ${stageIcon('bi-eye', (p.stages || {}).s2 || 'pending', 'OCR-A')}
                        ${stageIcon('bi-type', (p.stages || {}).s3 || 'pending', 'OCR-B')}
                        ${arrowIcon()}
                        ${stageIcon('bi-intersect', (p.stages || {}).s4 || 'pending', '規則仲裁')}
                        ${arrowIcon()}
                        ${stageIcon('bi-check-circle', flowAReadyState, 'Flow A')}
                        ${arrowIcon()}
                        ${stageIcon('bi-translate', (p.stages || {}).s10 || 'pending', '翻譯')}
                        ${arrowIcon()}
                        ${stageIcon('bi-clipboard-check', (p.stages || {}).s11 || 'pending', '校對')}
                        ${arrowIcon()}
                        ${stageIcon('bi-file-earmark-richtext', flowBReadyState, 'Flow B')}
                    </div>
                </td>
                <td>
                    <div class="d-flex gap-1 flex-wrap">
                        <span class="badge ${
                            status === 'ready_B' ? 'bg-success' :
                            status === 'ready_A' ? 'bg-primary' :
                            status === 'processing_A' || status === 'processing_B' ? 'bg-info' :
                            status === 'failed' ? 'bg-danger' :
                            'bg-secondary'
                        }">${status}</span>
                        ${p.error_type ? `<span class="badge bg-danger" title="${p.error_type}"><i class="bi bi-exclamation-triangle"></i></span>` : ''}
                    </div>
                </td>
                <td>
                    <div class="d-flex gap-2">
                        ${actionBtn}
                        <button class="btn btn-sm btn-outline-dark" onclick="literatureApp.editor.openSplitView('${p.paper_id}', 1, 'text_1')" title="人工修正"><i class="bi bi-pencil"></i></button>
                        <button class="btn btn-sm btn-outline-danger" onclick="literatureApp.deletePaper('${p.paper_id}')" title="刪除文件"><i class="bi bi-trash"></i></button>
                    </div>
                </td>
            </tr>
            `;
        });
        tbody.innerHTML = html;
        this.updateFlowBButtonState();
    },

    triggerPipeline: function(paperId, btnElement) {
        console.log(`[Literature] Triggering Flow A for ${paperId}`);

        if (!btnElement) {
            btnElement = document.querySelector(`.btn-trigger-ai[data-id="${paperId}"]`);
        }
        if (!btnElement) {
            alert('按鈕元素錯誤');
            return;
        }

        const originalText = btnElement.innerHTML;
        btnElement.disabled = true;
        btnElement.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> 啟動中...';

        fetch('/api/literature/run_batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid, paper_ids: [paperId] }),
        })
            .then(res => res.json())
            .then(data => {
                if (data.status !== 'success') {
                    alert('觸發失敗：' + (data.message || '未知錯誤'));
                    btnElement.disabled = false;
                    btnElement.innerHTML = originalText;
                } else {
                    btnElement.classList.remove('btn-warning', 'btn-primary');
                    btnElement.classList.add('btn-secondary');
                    btnElement.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Flow A處理中...';
                    setTimeout(() => this.loadPipelineTable(), 1000);
                }
            })
            .catch(err => {
                alert('觸發失敗：' + err.message);
                btnElement.disabled = false;
                btnElement.innerHTML = originalText;
            });
    },

    triggerTranslation: function(paperId, btnElement) {
        if (!this.canRunFlowBForPaper(paperId)) {
            alert('此文件尚未完成解析，請先執行「文獻解析」。');
            return;
        }

        if (!btnElement) {
            btnElement = document.querySelector(`.btn-trigger-translation[data-id="${paperId}"]`);
        }
        if (!btnElement) {
            alert('按鈕元素錯誤');
            return;
        }

        const originalText = btnElement.innerHTML;
        btnElement.disabled = true;
        btnElement.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> 啟動中...';

        fetch('/api/literature/run_translation', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid, paper_ids: [paperId] }),
        })
            .then(res => res.json())
            .then(data => {
                if (data.status !== 'success') {
                    alert('觸發失敗：' + (data.message || '未知錯誤'));
                    btnElement.disabled = false;
                    btnElement.innerHTML = originalText;
                } else {
                    btnElement.classList.remove('btn-outline-success');
                    btnElement.classList.add('btn-secondary');
                    btnElement.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Flow B處理中...';
                    setTimeout(() => this.loadPipelineTable(), 1000);
                }
            })
            .catch(err => {
                alert('觸發失敗：' + err.message);
                btnElement.disabled = false;
                btnElement.innerHTML = originalText;
            });
    },

    runPipelineBatch: function() {
        const checkboxes = document.querySelectorAll('.paper-checkbox:checked');
        const ids = Array.from(checkboxes).map(cb => cb.value);

        if (ids.length === 0) {
            alert('請先勾選至少一份文件！');
            return;
        }

        if (!confirm(`確定要對 ${ids.length} 份文件執行 Flow A (辨識成果)?`)) return;

        fetch('/api/literature/run_batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid, paper_ids: ids }),
        })
            .then(res => res.json())
            .then(data => {
                if (data.status !== 'success') {
                    const detailText = this.formatQueueDetails(data.details);
                    alert(`觸發失敗: ${data.message || '未知錯誤'}${detailText ? '\n' + detailText : ''}`);
                    return;
                }
                const detailText = this.formatQueueDetails(data.details);
                alert(`${data.message || '已啟動'}${detailText ? '\n' + detailText : ''}`);
                this.loadPipelineTable();
            });
    },

    runTranslationBatch: function() {
        const checkboxes = document.querySelectorAll('.paper-checkbox:checked');
        const ids = Array.from(checkboxes).map(cb => cb.value);

        if (ids.length === 0) {
            alert('請先勾選至少一份文件！');
            return;
        }

        const blocked = ids.filter(id => !this.canRunFlowBForPaper(id));
        if (blocked.length > 0) {
            alert(`以下文件尚未完成 Flow A：${blocked.join(', ')}`);
            this.updateFlowBButtonState();
            return;
        }

        if (!confirm(`確定要對 ${ids.length} 份文件執行 Flow B (雙語對照)?`)) return;

        fetch('/api/literature/run_translation', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: this.currentPid, paper_ids: ids }),
        })
            .then(res => res.json())
            .then(data => {
                if (data.status !== 'success') {
                    const detailText = this.formatQueueDetails(data.details);
                    alert(`觸發失敗: ${data.message || '未知錯誤'}${detailText ? '\n' + detailText : ''}`);
                    return;
                }
                const detailText = this.formatQueueDetails(data.details);
                alert(`${data.message || '已啟動'}${detailText ? '\n' + detailText : ''}`);
                this.loadPipelineTable();
            });
    },
    
    // --- Correction Editor (Preserved v3.7) ---
    editor: {
        modal: null,
        currentFile: null,
        rawJsonData: null,
        pageManifest: [],
        activeBlockIndex: null,

        init: function() {
            const el = document.getElementById('correctionModal');
            if(el) {
                this.modal = new bootstrap.Modal(el);
                el.addEventListener('shown.bs.modal', () => {
                    document.documentElement.classList.add('correction-modal-open');
                    document.body.classList.add('correction-modal-open');
                });
                el.addEventListener('hidden.bs.modal', () => {
                    document.documentElement.classList.remove('correction-modal-open');
                    document.body.classList.remove('correction-modal-open');
                });
                const btnSave = document.getElementById('btnSaveCorrection');
                const pageSelect = document.getElementById('correctionPageSelect');
                if (btnSave) {
                    btnSave.addEventListener('click', () => {
                        this.saveAndRerun();
                    });
                }
                if (pageSelect) {
                    pageSelect.addEventListener('change', (e) => {
                        const selectedPage = parseInt(e.target.value, 10);
                        const target = this.pageManifest.find(p => p.page === selectedPage);
                        if (target) {
                            this.currentFile.page = target.page;
                            this.currentFile.block = target.block;
                            this.loadCurrentPage();
                        }
                    });
                }

                const imgEl = document.getElementById('correctionImage');
                if (imgEl) {
                    imgEl.addEventListener('load', () => this.renderOverlayBoxes());
                }
            }
        },

        openSplitView: function(paperId, pageNum, blockName) {
            this.currentFile = { pid: literatureApp.currentPid, paperId: paperId, page: pageNum, block: blockName };
            this.rawJsonData = null;
            this.pageManifest = [];
            this.activeBlockIndex = null;

            const editorEl = document.getElementById('correctionEditor');
            if(editorEl) {
                editorEl.innerHTML = '<div class="text-center p-4"><div class="spinner-border text-primary" role="status"></div><p class="mt-2">載入中...</p></div>';
            }

            this.loadPageManifest()
                .then(() => {
                    if (!this.pageManifest.length) {
                        const imgEl = document.getElementById('correctionImage');
                        if (imgEl) imgEl.removeAttribute('src');
                        if (editorEl) {
                            editorEl.innerHTML = `
                                <div class="alert alert-warning">
                                    尚未產生 Task 4 可編輯資料。<br>
                                    請先執行該文件的 AI 解析（OCR/Fix），完成後再開啟修正視窗。
                                </div>
                            `;
                        }
                        this.syncPageSelect();
                        return;
                    }

                    // 若目前 page/block 不存在，回退到第一頁
                    const exists = this.pageManifest.find(p => p.page === this.currentFile.page && p.block === this.currentFile.block);
                    if (!exists && this.pageManifest.length > 0) {
                        this.currentFile.page = this.pageManifest[0].page;
                        this.currentFile.block = this.pageManifest[0].block;
                    }
                    this.syncPageSelect();
                    this.loadCurrentPage();
                })
                .catch(err => {
                    if (editorEl) editorEl.innerHTML = `<div class="alert alert-danger">載入失敗: ${this._escapeHtml(err.message)}</div>`;
                });

            if(this.modal) this.modal.show();
        },

        loadPageManifest: function() {
            return fetch(`/api/literature/get_block_manifest?pid=${literatureApp.currentPid}&paper_id=${this.currentFile.paperId}`)
                .then(res => res.json())
                .then(data => {
                    if (data.status !== 'success' || !Array.isArray(data.pages)) {
                        throw new Error(data.message || '無法取得頁面清單');
                    }
                    this.pageManifest = data.pages;
                });
        },

        syncPageSelect: function() {
            const select = document.getElementById('correctionPageSelect');
            if (!select) return;

            if (!this.pageManifest.length) {
                select.innerHTML = '<option value="">無頁面</option>';
                select.disabled = true;
                return;
            }

            select.disabled = false;
            select.innerHTML = this.pageManifest
                .map(p => `<option value="${p.page}">第 ${p.page} 頁 ${p.has_fixed ? '(Fixed)' : '(Raw)'}</option>`)
                .join('');

            select.value = String(this.currentFile.page);
        },

        loadCurrentPage: function() {
            const imgEl = document.getElementById('correctionImage');
            const editorEl = document.getElementById('correctionEditor');
            const overlay = document.getElementById('correctionOverlay');

            this.activeBlockIndex = null;
            if (overlay) overlay.innerHTML = '';

            if(imgEl) {
                imgEl.src = `/api/literature/get_region_image?pid=${literatureApp.currentPid}&paper_id=${this.currentFile.paperId}&page=${this.currentFile.page}&t=${new Date().getTime()}`;
            }

            if (editorEl) {
                editorEl.innerHTML = '<div class="text-center p-4"><div class="spinner-border text-primary" role="status"></div><p class="mt-2">載入頁面中...</p></div>';
            }

            fetch(`/api/literature/get_block_json?pid=${literatureApp.currentPid}&paper_id=${this.currentFile.paperId}&block=${this.currentFile.block}`)
                .then(res => res.json())
                .then(data => {
                    if(data.status === 'error') {
                        if (editorEl) editorEl.innerHTML = '<div class="alert alert-danger">無法載入數據</div>';
                        return;
                    }
                    this.rawJsonData = data.content;
                    this.renderTextBlocks(data.content);
                    this.renderOverlayBoxes();
                })
                .catch(err => {
                    if (editorEl) editorEl.innerHTML = `<div class="alert alert-danger">載入失敗: ${this._escapeHtml(err.message)}</div>`;
                });
        },

        renderTextBlocks: function(jsonArray) {
            const editorEl = document.getElementById('correctionEditor');
            if(!Array.isArray(jsonArray) || jsonArray.length === 0) {
                editorEl.innerHTML = '<div class="alert alert-warning">沒有可編輯的文字內容</div>';
                return;
            }

            let html = '';
            jsonArray.forEach((block, index) => {
                const rawScore = Number(block.score);
                const arbScore = Number(block.arbiter_score);
                const scoreVal = Number.isFinite(rawScore) && rawScore > 0
                    ? rawScore
                    : (Number.isFinite(arbScore) && arbScore > 0 ? arbScore : 0);
                const confidence = scoreVal > 0 ? (scoreVal * 100).toFixed(1) : 'N/A';
                const badgeClass = scoreVal >= 0.9 ? 'bg-success' : scoreVal >= 0.7 ? 'bg-warning' : 'bg-danger';
                const blockType = block.type || 'Text';
                const source = block.source || 'Unknown';
                
                html += `
                <div class="text-block-card" data-index="${index}">
                    <div class="text-block-header">
                        <span><i class="bi bi-file-text me-1"></i>區塊 ${index + 1} <span class="badge bg-secondary ms-1">${blockType}</span></span>
                        <span>
                            <span class="confidence-badge badge ${badgeClass}">信心度: ${confidence}%</span>
                            <span class="ms-1 text-muted">${source}</span>
                        </span>
                    </div>
                    <textarea class="text-block-content" data-index="${index}" rows="3">${block.content || ''}</textarea>
                </div>`;
            });

            editorEl.innerHTML = html;

            const cards = editorEl.querySelectorAll('.text-block-card');
            cards.forEach(card => {
                card.addEventListener('click', () => {
                    const index = parseInt(card.dataset.index, 10);
                    this.setActiveBlock(index);
                });
            });
        },

        setActiveBlock: function(index) {
            this.activeBlockIndex = index;

            const cards = document.querySelectorAll('.text-block-card');
            cards.forEach(el => el.classList.remove('active'));
            const activeCard = document.querySelector(`.text-block-card[data-index="${index}"]`);
            if (activeCard) {
                activeCard.classList.add('active');
            }

            const rects = document.querySelectorAll('.bbox-rect');
            rects.forEach(el => el.classList.remove('active'));
            const activeRect = document.querySelector(`.bbox-rect[data-index="${index}"]`);
            if (activeRect) {
                activeRect.classList.add('active');
            }
        },

        renderOverlayBoxes: function() {
            const overlay = document.getElementById('correctionOverlay');
            // 使用者需求：左側只顯示原始頁，不顯示切塊框
            if (overlay) {
                overlay.innerHTML = '';
            }
        },

        saveAndRerun: function() {
            if(!this.rawJsonData) {
                alert("沒有可儲存的數據");
                return;
            }

            // 收集所有編輯框的內容
            const textareas = document.querySelectorAll('.text-block-content');
            textareas.forEach(ta => {
                const index = parseInt(ta.dataset.index);
                if(this.rawJsonData[index]) {
                    this.rawJsonData[index].content = ta.value;
                }
            });

            const btn = document.getElementById('btnSaveCorrection');
            const originalText = btn.innerHTML;
            btn.innerHTML = '<i class="bi bi-hourglass-split"></i> 儲存中...';
            btn.disabled = true;

            fetch('/api/literature/save_correction', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    pid: this.currentFile.pid,
                    paper_id: this.currentFile.paperId,
                    block_name: this.currentFile.block,
                    content: this.rawJsonData
                })
            })
                .then(res => res.json())
                .then(result => {
                    if(result.status === 'success') {
                        return fetch('/api/literature/run_translation', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                pid: this.currentFile.pid,
                                paper_ids: [this.currentFile.paperId]
                            })
                        })
                            .then(res => res.json())
                            .then(runResp => {
                                if (runResp.status === 'success') {
                                    alert('✅ 修改已儲存，Flow B 已送出（排隊中）...');
                                } else {
                                    alert('⚠️ 修改已儲存，但 Flow B 啟動失敗：' + (runResp.message || '未知錯誤'));
                                }
                                this.modal.hide();
                                literatureApp.loadPipelineTable();
                                setTimeout(() => literatureApp.refreshView(), 1200);
                            })
                            .catch(err => {
                                alert('⚠️ 修改已儲存，但 Flow B 啟動失敗（網路錯誤）：' + err.message);
                                this.modal.hide();
                                literatureApp.loadPipelineTable();
                                setTimeout(() => literatureApp.refreshView(), 1200);
                            });
                    } else {
                        alert('❌ 儲存失敗: ' + (result.message || '未知錯誤'));
                    }
                })
                .catch(err => {
                    alert('❌ 網路錯誤: ' + err.message);
                })
                .finally(() => {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                });
        },

        openPreview: function(paperId, type) {
            const modalEl = document.getElementById('previewModal');
            if(modalEl) {
                const modal = new bootstrap.Modal(modalEl);
                document.getElementById('previewTitle').innerText = `${paperId} (${type})`;
                modal.show();
            }
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    window.literatureApp.init();
});


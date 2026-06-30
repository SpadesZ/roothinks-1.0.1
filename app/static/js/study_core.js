// #路徑(app/static/js/study_core.js) #版本 v2.2-Merged #更版時間 20260209-1400
/* [MVP+Prototype Handoff Header]
 * 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
 * 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
 * Study Matrix 生成/歷史載入/模式切換為階段性方案，後續請由人類團隊進行產品級重構與測試。
 */

/**
 * Study Core Controller (Merged v2.2)
 * 整合 v1.0 (Matrix, Sidebar) 與 v2.1 (Gatekeeper, FullText Render)
 * 實現完整的 Study 體驗：閉鎖 -> 列表 -> 全文 -> 矩陣 -> 筆記。
 */
class StudyCore {
    constructor() {
        this.pid = '';
        this.currentPaperId = null; // [New] Track current reading
        this.isLoading = false;
        this.saveTimer = null;
        this.matrixCacheKey = null;
        this.currentLoadedMatrixId = null;
        this.currentLoadedMatrixName = '';
        
        this.init();
    }

    init() {
        // 1. Get Params
        const urlParams = new URLSearchParams(window.location.search);
        const urlPid = urlParams.get('pid');
        const urlPaperId = urlParams.get('paper_id'); // [New]
        const pathParts = window.location.pathname.split('/').filter(Boolean);
        const pathPid = (pathParts.length >= 3 && pathParts[0] === 'study' && pathParts[1] === 'project')
            ? decodeURIComponent(pathParts[2])
            : null;
        
        if (urlPid) this.pid = urlPid;
        else if (pathPid) this.pid = pathPid;
        else this.pid = '';

        if (this.pid) {
            try { localStorage.setItem('activePid', this.pid); } catch (e) {}
        }
        if (urlPaperId) this.currentPaperId = urlPaperId;

        console.log(`[StudyCore] Init: PID=${this.pid}, Paper=${this.currentPaperId}`);
        this.resolvePidRuntime();
        this.matrixCacheKey = this.pid ? `study_matrix_cache_${this.pid}` : null;

        // 2. Load Sidebar List (From v1.0)
        this.loadPapers();

        // 3. Init Notes (From v1.0)
        this.initNotes();

        // 3.1 Init matrix history list
        this.initMatrixHistory();

        // 4. Gatekeeper & Content Load (From v2.1)
        if (this.currentPaperId) {
            this.checkGatekeeper().then(passed => {
                if (passed) {
                    this.loadPaperData(); // Load Full Text
                }
            });
        }
    }

    // --- Gatekeeper Logic (v2.1) ---
    checkGatekeeper() {
        return new Promise((resolve, reject) => {
            fetch(`/api/study/check_gate/${this.pid}/${this.currentPaperId}`)
                .then(r => {
                    if (r.ok) return r.json();
                    throw r;
                })
                .then(res => {
                    resolve(true); // Status 200: Gold Ready
                })
                .catch(err => {
                    // Status 403 (Locked) or 404/500
                    err.json().then(res => {
                         this.renderGateOverlay(res.message || "Access Denied", res.status === 'locked');
                    }).catch(() => {
                         this.renderGateOverlay("Connection Error", false);
                    });
                    resolve(false);
                });
        });
    }

    renderGateOverlay(msg, isLocked) {
        // Hide Main Content
        const container = document.getElementById('study-main-container');
        if(container) container.style.display = 'none';

        const iconHtml = isLocked 
            ? '<div class="spinner-border text-warning" style="width: 3rem; height: 3rem;"></div>' 
            : '<i class="bi bi-x-circle text-danger" style="font-size: 3rem;"></i>';
        
        const title = isLocked ? 'AI 智能解析進行中' : '資料未就緒';
        
        const overlayHtml = `
            <div id="gate-overlay" style="
                position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(0,0,0,0.85); z-index: 9999;
                display: flex; flex-direction: column; justify-content: center; align-items: center;
                color: white; text-align: center;">
                
                <div class="mb-4">
                    ${iconHtml}
                </div>
                
                <h2>${title}</h2>
                <p class="lead text-light">${this.escapeHtml(msg)}</p>
                
                <div class="mt-4">
                    <a href="/literature?pid=${encodeURIComponent(this.pid)}" class="btn btn-outline-light btn-lg">
                        <i class="bi bi-arrow-left"></i> 返回 Literature 進行解析
                    </a>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', overlayHtml);
    }

    // --- Content Loading (v2.1) ---
    loadPaperData() {
        // Load Full Text
        fetch(`/api/literature/get_full_json?pid=${this.pid}&paper_id=${this.currentPaperId}&type=fulltext`)
            .then(r => r.json())
            .then(data => {
                if (data.error) return alert("Error loading text: " + data.error);
                this.renderFullText(data.content || []);
            });

        // Load Summary Sidebar
        fetch(`/api/literature/get_full_json?pid=${this.pid}&paper_id=${this.currentPaperId}&type=summary`)
            .then(r => r.json())
            .then(data => {
                const sumEl = document.getElementById('summary-content');
                if(sumEl) sumEl.innerText = data.abstract_zh || "No summary available.";
                
                const findingsEl = document.getElementById('findings-list');
                if(findingsEl) {
                    findingsEl.innerHTML = '';
                    (data.key_findings || []).forEach(f => {
                        findingsEl.innerHTML += `<li>${this.escapeHtml(f)}</li>`;
                    });
                }
            });
    }

    renderFullText(blocks) {
        const container = document.getElementById('doc-viewer');
        if(!container) return;
        container.innerHTML = '';
        
        // [Enhanced] 改進的全文渲染邏輯，加入翻譯內容支持
        if (!blocks || blocks.length === 0) {
            container.innerHTML = '<div class="text-muted text-center py-5">No content available</div>';
            console.warn('[StudyCore] No blocks to render');
            return;
        }
        
        let blockCount = 0;
        
        blocks.forEach((block, idx) => {
            // block structure: { type: 'Body'/'Title', content: '...', bbox: ..., translation: '...' }
            // 或 { page: N, blocks: [{...}, ...] }
            
            // 處理嵌套結構 (page > blocks)
            let itemsToRender = [];
            if (block.blocks && Array.isArray(block.blocks)) {
                // 這是 {page, blocks} 結構
                console.log(`[StudyCore] Processing page ${block.page} with ${block.blocks.length} blocks`);
                itemsToRender = block.blocks;
            } else {
                // 直接的 block
                itemsToRender = [block];
            }
            
            itemsToRender.forEach((item) => {
                const blockType = (item.type || 'body').toLowerCase();
                const isTitleOrHeader = blockType === 'title' || blockType === 'header';
                
                let html = `<div class="doc-block type-${blockType} mb-3">`;
                
                if (isTitleOrHeader) {
                    html += `<div class="doc-original fw-bold fs-5 text-dark mb-2">${item.content || ''}</div>`;
                } else {
                    html += `<div class="doc-original text-dark mb-2" style="line-height: 1.8;">${item.content || ''}</div>`;
                    
                    // [New] 如果有翻譯，加入雙語對照
                    if (item.translation) {
                        html += `<div class="doc-translation text-secondary small mb-3" style="line-height: 1.8; border-left: 3px solid #ddd; padding-left: 12px;"><em>${item.translation}</em></div>`;
                    }
                }
                
                html += `</div>`;
                container.insertAdjacentHTML('beforeend', html);
                blockCount++;
            });
        });
        
        const mainContainer = document.getElementById('study-main-container');
        if(mainContainer) $(mainContainer).fadeIn();
        
        console.log("[StudyCore] Rendered " + blockCount + " blocks with translation support");
    }

    // --- Sidebar List (v1.0 Preserved) ---
    loadPapers() {
        const container = document.getElementById('paperListContainer');
        if (!container) return;
        const pid = this.resolvePidRuntime();
        if (!pid) {
            container.innerHTML = '<div class="text-warning small p-2">無有效 PID，請從 Dashboard 重新進入 Study。</div>';
            return;
        }

        container.innerHTML = '<div class="text-center py-3"><div class="spinner-border spinner-border-sm text-secondary"></div></div>';

        fetch(`/api/study/paper_list/${pid}`)
            .then(r => r.json())
            .then(res => {
                if (res.ok) {
                    this.renderPaperList(res.papers, container);
                } else {
                    container.innerHTML = `<div class="text-danger small p-2">Error: ${this.escapeHtml(res.msg)}</div>`;
                }
            })
            .catch(err => {
                container.innerHTML = `<div class="text-danger small p-2">Net Error: ${this.escapeHtml(err)}</div>`;
            });
    }

    renderPaperList(papers, container) {
        if (!papers || papers.length === 0) {
            container.innerHTML = '<div class="text-muted small p-2">No processed papers.</div>';
            return;
        }

        let html = `<div class="list-group list-group-flush bg-transparent">`;
        papers.forEach(p => {
            const safeTitle = this.escapeHtml(p.title || p.paper_id);
            const safePaperId = this.escapeHtml(p.paper_id);
            const safePaperIdJs = this.escapeJs(p.paper_id);
            const safePidJs = this.escapeJs(this.pid);
            const isActive = (p.paper_id === this.currentPaperId) ? 'active' : '';
            const statusBadge = p.is_ready 
                ? `<span class="badge bg-success rounded-pill" style="font-size:0.5em;">Ready</span>`
                : `<span class="badge bg-secondary rounded-pill" style="font-size:0.5em;">Wait</span>`;
            
            html += `
            <div class="list-group-item bg-transparent text-light border-secondary px-0 py-2 ${isActive}">
                <div class="d-flex w-100 justify-content-between align-items-start mb-2">
                    <h6 class="mb-1 text-truncate flex-grow-1" title="${safeTitle}" style="max-width: 70%;">
                        ${safeTitle}
                    </h6>
                    ${statusBadge}
                </div>
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <small style="font-size:0.7em;">${safePaperId}</small>
                    <input class="form-check-input paper-checkbox" type="checkbox" value="${safePaperId}" ${p.is_ready ? '' : 'disabled'}>
                </div>
                ${p.is_ready ? `
                <button class="btn btn-sm btn-outline-primary w-100" 
                        onclick="window.studyView.loadFulltext('${safePidJs}', '${safePaperIdJs}')">
                    <i class="bi bi-book-half me-1"></i> 閱讀全文
                </button>
                ` : `
                <button class="btn btn-sm btn-outline-secondary w-100" disabled>
                    <i class="bi bi-hourglass-split me-1"></i> 處理中...
                </button>
                `}
            </div>`;
        });
        html += `</div>`;
        container.innerHTML = html;
    }

    // --- Notes & Matrix (v1.0 Preserved) ---
    resolvePidRuntime() {
        if (this.pid && String(this.pid).trim()) return this.pid;
        const urlParams = new URLSearchParams(window.location.search);
        const urlPid = urlParams.get('pid');
        const pathParts = window.location.pathname.split('/').filter(Boolean);
        const pathPid = (pathParts.length >= 3 && pathParts[0] === 'study' && pathParts[1] === 'project')
            ? decodeURIComponent(pathParts[2])
            : null;
        this.pid = (urlPid || pathPid || '').trim();
        if (this.pid) {
            try { localStorage.setItem('activePid', this.pid); } catch (e) {}
        }
        return this.pid;
    }

    initNotes() {
        const noteArea = document.getElementById('study-notes');
        if (noteArea) {
            this.loadNotes();
            noteArea.addEventListener('input', () => {
                clearTimeout(this.saveTimer);
                this.saveTimer = setTimeout(() => this.saveNotes(), 2000);
            });
        }
    }

    saveNotes() {
        const noteArea = document.getElementById('study-notes');
        if (!noteArea) return;
        const pid = this.resolvePidRuntime();
        if (!pid) {
            this.showToast('無法儲存筆記：PID 不存在', 'error');
            return;
        }
        
        const content = noteArea.value;
        noteArea.style.borderColor = "#198754"; // Green border feedback
        
        fetch('/api/study/notes/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: pid, notes: content })
        }).then(r => r.json()).then(res => {
            if(res.ok) {
                console.log("[StudyCore] Notes saved.");
                setTimeout(() => { noteArea.style.borderColor = ""; }, 500);
            } else {
                noteArea.style.borderColor = "#dc3545";
                this.showToast(`筆記儲存失敗: ${res.msg || 'unknown error'}`, 'error');
            }
        }).catch(err => {
            noteArea.style.borderColor = "#dc3545";
            this.showToast(`筆記儲存失敗: ${err}`, 'error');
        });
    }

    loadNotes() {
        const noteArea = document.getElementById('study-notes');
        if (!noteArea) return;
        const pid = this.resolvePidRuntime();
        if (!pid) return;

        fetch(`/api/study/notes/load/${pid}`)
            .then(r => r.json())
            .then(res => {
                if(res.ok && res.notes) {
                    noteArea.value = res.notes;
                }
            });
    }

    runTask6() {
        if (this.isLoading) return;
        const pid = this.resolvePidRuntime();
        if (!pid) {
            this.showToast('無法生成矩陣：PID 不存在', 'error');
            return;
        }

        const checkboxes = document.querySelectorAll('.paper-checkbox:checked');
        const selectedIds = Array.from(checkboxes).map(cb => cb.value);

        if (selectedIds.length < 2) {
            alert("請至少勾選 2 篇文獻進行比較！");
            return;
        }

        this.setLoading(true, `AI 正在深度閱讀 ${selectedIds.length} 篇文獻...`);
        
        fetch('/api/study/compare', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: pid, paper_ids: selectedIds })
        })
        .then(r => r.json())
        .then(res => {
            this.setLoading(false);
            console.log('[StudyCore] Matrix API response:', res);
            if (res.ok) {
                console.log('[StudyCore] Matrix data received:', res.data);
                this.cacheMatrix(res.data, res.matrix_id || null);
                if (window.studyView) window.studyView.renderMatrix(res.data);
                this.currentLoadedMatrixId = res.matrix_id || null;
                this.currentLoadedMatrixName = (res.display_name || '').trim() || this.currentLoadedMatrixId || '';
                this.loadMatrixHistory();
                if (res.matrix_id) {
                    this.showToast(`矩陣已保存 (${res.matrix_id})`, 'success');
                }
                if (window.studyView && typeof window.studyView.setDisplayMode === 'function') {
                    window.studyView.setDisplayMode('matrix');
                }
            } else {
                console.error('[StudyCore] Matrix generation failed:', res.error);
                alert("生成失敗: " + (res.error || "Unknown Error"));
            }
        })
        .catch(err => {
            this.setLoading(false);
            console.error('[StudyCore] Network error:', err);
            alert("網絡錯誤: " + err);
        });
    }

    initMatrixHistory() {
        const historyTabBtn = document.getElementById('btn-tab-history');
        if (historyTabBtn) {
            historyTabBtn.addEventListener('shown.bs.tab', () => this.loadMatrixHistory());
        }
        this.loadMatrixHistory();
    }

    loadMatrixHistory() {
        const pid = this.resolvePidRuntime();
        if (!pid) return;
        const container = document.getElementById('historyListContainer');
        if (!container) return;

        container.innerHTML = '<div class="text-secondary small py-2"><div class="spinner-border spinner-border-sm me-2"></div>Loading...</div>';
        fetch(`/api/study/${pid}/matrix/history`)
            .then(r => r.json())
            .then(res => {
                if (!res.ok) {
                    container.innerHTML = `<div class="text-danger small">Error: ${this.escapeHtml(res.msg || 'load failed')}</div>`;
                    return;
                }

                const rows = Array.isArray(res.history) ? res.history : [];
                if (!rows.length) {
                    container.innerHTML = '<div class="text-secondary small"><i class="bi bi-info-circle me-1"></i> 暫無記錄</div>';
                    return;
                }

                let html = '<div class="list-group list-group-flush">';
                rows.forEach((item) => {
                    const matrixIdJs = this.escapeJs(item.matrix_id);
                    const papers = Array.isArray(item.paper_titles) ? item.paper_titles.slice(0, 2) : [];
                    const p1 = this.escapeHtml(papers[0] || 'Paper A');
                    const p2 = this.escapeHtml(papers[1] || 'Paper B');
                    const created = this.escapeHtml(item.created_at || '');
                    const criteria = this.escapeHtml(item.criteria || 'General');
                    const displayName = this.escapeHtml(item.display_name || item.matrix_id || criteria);
                    const isLegacy = !!item.is_legacy;
                    const activeClass = (item.matrix_id === this.currentLoadedMatrixId) ? 'active' : '';
                    html += `
                        <div class="list-group-item ${activeClass}">
                            <div class="d-flex justify-content-between align-items-start gap-2">
                                <button class="btn btn-link text-start text-decoration-none flex-grow-1 p-0"
                                        onclick="window.studyCore.loadMatrixRecord('${matrixIdJs}')">
                                    <div class="fw-bold small text-dark">${displayName}</div>
                                    <div class="small text-muted text-truncate">${criteria}</div>
                                    <div class="small text-muted text-truncate">A: ${p1}</div>
                                    <div class="small text-muted text-truncate">B: ${p2}</div>
                                    <div class="small text-secondary mt-1">${created}</div>
                                </button>
                                <div class="btn-group btn-group-sm ms-2" role="group">
                                    <button class="btn btn-outline-secondary"
                                            title="${isLegacy ? 'Legacy 記錄不支援改名' : '改名'}"
                                            ${isLegacy ? 'disabled' : ''}
                                            onclick="window.studyCore.renameMatrixRecord('${matrixIdJs}')">
                                        <i class="bi bi-pencil"></i>
                                    </button>
                                    <button class="btn btn-outline-danger"
                                            title="刪除"
                                            onclick="window.studyCore.deleteMatrixRecord('${matrixIdJs}')">
                                        <i class="bi bi-trash"></i>
                                    </button>
                                </div>
                            </div>
                        </div>`;
                });
                html += '</div>';
                container.innerHTML = html;
            })
            .catch(err => {
                container.innerHTML = `<div class="text-danger small">Net Error: ${this.escapeHtml(err)}</div>`;
            });
    }

    loadMatrixRecord(matrixId) {
        const pid = this.resolvePidRuntime();
        if (!pid || !matrixId) return;
        fetch(`/api/study/${pid}/matrix/${matrixId}`)
            .then(r => r.json())
            .then(res => {
                if (!res.ok || !res.record || !res.record.data) {
                    this.showToast('載入矩陣記錄失敗', 'error');
                    return;
                }
                const loadedId = res.record.matrix_id || matrixId;
                const loadedName = (res.record.display_name || '').trim() || loadedId;
                this.cacheMatrix(res.record.data, loadedId);
                this.currentLoadedMatrixId = loadedId;
                this.currentLoadedMatrixName = loadedName;
                if (window.studyView) window.studyView.renderMatrix(res.record.data);
                const titleEl = document.getElementById('matrix-title');
                if (titleEl) titleEl.innerText = `已載入記錄: ${loadedName}`;
                if (window.studyView && typeof window.studyView.setDisplayMode === 'function') {
                    window.studyView.setDisplayMode('matrix');
                }
                this.loadMatrixHistory();
                this.showToast('已載入歷史比較矩陣', 'info');
            })
            .catch(err => {
                console.error('[StudyCore] Load matrix record failed:', err);
                this.showToast('載入矩陣記錄失敗', 'error');
            });
    }

    loadLatestMatrix(attempt = 0, options = {}) {
        const pid = this.resolvePidRuntime();
        if (!pid) return;
        const switchMode = options.switchMode !== false;
        const silentWhenMissing = options.silentWhenMissing !== false;
        fetch(`/api/study/${pid}/matrix/latest`)
            .then(r => r.json())
            .then(res => {
                if (!res.ok || !res.record || !res.record.data) {
                    if (attempt < 3) {
                        setTimeout(() => this.loadLatestMatrix(attempt + 1, options), 300 * (attempt + 1));
                    } else if (!silentWhenMissing) {
                        this.showToast('目前沒有可載入的矩陣記錄', 'warning');
                    }
                    return;
                }
                const loadedId = res.record.matrix_id || null;
                const loadedName = (res.record.display_name || '').trim() || loadedId || '';
                this.cacheMatrix(res.record.data, loadedId);
                this.currentLoadedMatrixId = loadedId;
                this.currentLoadedMatrixName = loadedName;
                if (window.studyView && typeof window.studyView.renderMatrix === 'function') {
                    window.studyView.renderMatrix(res.record.data);
                } else {
                    // In rare race conditions, retry once after StudyView is ready.
                    setTimeout(() => {
                        if (window.studyView && typeof window.studyView.renderMatrix === 'function') {
                            window.studyView.renderMatrix(res.record.data);
                        }
                    }, 120);
                }
                const titleEl = document.getElementById('matrix-title');
                if (titleEl) titleEl.innerText = `已載入最近比較: ${loadedName || ''}`;
                if (switchMode && window.studyView && typeof window.studyView.setDisplayMode === 'function') {
                    window.studyView.setDisplayMode('matrix');
                }
                this.loadMatrixHistory();
            })
            .catch(err => {
                console.warn('[StudyCore] loadLatestMatrix failed:', err);
                if (attempt < 3) {
                    setTimeout(() => this.loadLatestMatrix(attempt + 1, options), 300 * (attempt + 1));
                } else if (!silentWhenMissing) {
                    this.showToast('載入最近矩陣失敗', 'error');
                }
            });
    }

    cacheMatrix(data, matrixId = null) {
        const pid = this.resolvePidRuntime();
        if (!pid) return;
        const key = `study_matrix_cache_${pid}`;
        try {
            localStorage.setItem(key, JSON.stringify({
                matrixId: matrixId,
                updatedAt: new Date().toISOString(),
                data: data,
            }));
            this.matrixCacheKey = key;
        } catch (err) {
            console.warn('[StudyCore] cacheMatrix failed:', err);
        }
    }

    restoreMatrixFromCache() {
        const pid = this.resolvePidRuntime();
        if (!pid) return;
        const key = `study_matrix_cache_${pid}`;
        this.matrixCacheKey = key;
        try {
            const raw = localStorage.getItem(key);
            if (!raw) return;
            const cached = JSON.parse(raw);
            if (!cached || !cached.data) return;
            this.currentLoadedMatrixId = cached.matrixId || null;
            this.currentLoadedMatrixName = cached.matrixId || '';
            if (window.studyView && typeof window.studyView.renderMatrix === 'function') {
                window.studyView.renderMatrix(cached.data);
            }
            const titleEl = document.getElementById('matrix-title');
            if (titleEl) titleEl.innerText = `已還原快取矩陣: ${cached.matrixId || ''}`;
        } catch (err) {
            console.warn('[StudyCore] restoreMatrixFromCache failed:', err);
        }
    }

    renameMatrixRecord(matrixId) {
        const pid = this.resolvePidRuntime();
        if (!pid || !matrixId) return;
        if (String(matrixId).startsWith('legacy::')) {
            this.showToast('Legacy 記錄不支援改名', 'warning');
            return;
        }
        const currentName = this.currentLoadedMatrixId === matrixId ? this.currentLoadedMatrixName : matrixId;
        const displayName = prompt('請輸入新的記錄名稱：', currentName || matrixId);
        if (displayName === null) return;
        const trimmed = String(displayName || '').trim();
        if (!trimmed) {
            this.showToast('名稱不可為空', 'warning');
            return;
        }
        fetch(`/api/study/${pid}/matrix/${encodeURIComponent(matrixId)}/rename`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({display_name: trimmed})
        })
            .then(r => r.json())
            .then(res => {
                if (!res.ok) {
                    this.showToast(`改名失敗: ${res.msg || 'unknown error'}`, 'error');
                    return;
                }
                if (this.currentLoadedMatrixId === matrixId) {
                    this.currentLoadedMatrixName = trimmed;
                    const titleEl = document.getElementById('matrix-title');
                    if (titleEl) titleEl.innerText = `已載入記錄: ${trimmed}`;
                }
                this.loadMatrixHistory();
                this.showToast('記錄名稱已更新', 'success');
            })
            .catch(err => {
                this.showToast(`改名失敗: ${err}`, 'error');
            });
    }

    deleteMatrixRecord(matrixId) {
        const pid = this.resolvePidRuntime();
        if (!pid || !matrixId) return;
        if (!confirm(`確定要刪除記錄 ${matrixId} 嗎？`)) return;
        fetch(`/api/study/${pid}/matrix/${encodeURIComponent(matrixId)}`, {
            method: 'DELETE'
        })
            .then(r => r.json())
            .then(res => {
                if (!res.ok) {
                    this.showToast(`刪除失敗: ${res.msg || 'unknown error'}`, 'error');
                    return;
                }
                const deletedCurrent = this.currentLoadedMatrixId === matrixId;
                if (deletedCurrent) {
                    this.currentLoadedMatrixId = null;
                    this.currentLoadedMatrixName = '';
                }
                this.loadMatrixHistory();
                this.showToast('矩陣記錄已刪除', 'success');
                if (deletedCurrent) {
                    this.loadLatestMatrix(0, {switchMode: true, silentWhenMissing: true});
                }
            })
            .catch(err => {
                this.showToast(`刪除失敗: ${err}`, 'error');
            });
    }

    setLoading(isLoading, msg) {
        this.isLoading = isLoading;
        const matrixContainer = document.getElementById('matrixContainer');
        const titleEl = document.getElementById('matrix-title');
        
        if (isLoading) {
            if (titleEl) titleEl.innerText = "Analyzing...";
            if (matrixContainer) {
                matrixContainer.innerHTML = `
                <div class="d-flex flex-column align-items-center justify-content-center h-100 text-primary">
                    <div class="spinner-border" style="width: 3rem; height: 3rem;" role="status"></div>
                    <div class="mt-3 fw-bold fs-5">LAVA Task 6 Running</div>
                    <div class="small text-muted mt-2">${this.escapeHtml(msg)}</div>
                </div>`;
            }
        } else {
            if (titleEl) titleEl.innerText = "Analysis Complete";
        }
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text ?? '');
        return div.innerHTML;
    }

    escapeJs(text) {
        return String(text ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    }

    // Toast 通知系統
    showToast(message, type = 'info') {
        // 確保容器存在
        let container = document.getElementById('toastContainer');
        if (!container) {
            const html = '<div class="position-fixed bottom-0 end-0 p-3" style="z-index: 11"><div id="toastContainer"></div></div>';
            document.body.insertAdjacentHTML('beforeend', html);
            container = document.getElementById('toastContainer');
        }
        
        const id = 'toast-' + Date.now();
        const bgClass = {
            'success': 'bg-success',
            'error': 'bg-danger',
            'warning': 'bg-warning',
            'info': 'bg-info'
        }[type] || 'bg-secondary';
        
        const html = `
            <div id="${id}" class="toast align-items-center text-white ${bgClass} border-0" role="alert">
                <div class="d-flex">
                    <div class="toast-body">${this.escapeHtml(message)}</div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" 
                            data-bs-dismiss="toast"></button>
                </div>
            </div>
        `;
        
        container.insertAdjacentHTML('beforeend', html);
        const toastEl = document.getElementById(id);
        const toast = new bootstrap.Toast(toastEl, {delay: 3000});
        toast.show();
        
        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    }
}

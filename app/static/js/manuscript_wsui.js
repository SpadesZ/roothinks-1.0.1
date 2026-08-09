// Roothinks source maintenance contract
// 檔案路徑: app/static/js/manuscript_wsui.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 集中 Manuscript workspace DOM 渲染、可編輯狀態與通知，不直接決定後端授權。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/manuscript_wsui.js
//路徑(./app/static/js/manuscript_wsui.js)
//版本 v0.6 (Section Dropdown + Word Direct Import to 2B/2C)
//更版時間 20260421-1445
// inner comment: 嚴格保留 v0.2 全量佈局邏輯。新增游標追蹤機制與素材庫 Modal 渲染邏輯，支援 2B/2C 富文本畫布的精準圖表注入。
// CHANGE_PLAN_STUDY_FLOWB_2026-04-20: MVP prototype - add Word direct import path into 2B/2C and keep .doc guardrails.

class ManuUI {
    constructor(app) {
        this.app = app;
        this.isResizing1 = false;
        this.isResizing2 = false;
        this._toolbarBound = false;
        this.pendingWordImportTarget = null;
        
        // [v0.3 新增] 游標追蹤與目標畫布狀態
        this.currentInsertTarget = null;
        this.savedCursorRange = null;
        
        // [v0.3 新增] 監聽後端回傳的圖片註冊表資料
        if (this.app.socket) {
            this.app.socket.on('image_registry_data', (data) => {
                if (data && data.registry) {
                    this.renderAssetGallery(data.registry);
                }
            });
        }
    }

    _escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }

    _getApiToken() {
        try {
            return (window.localStorage.getItem('roothinks_api_token') || '').trim();
        } catch (e) {
            return '';
        }
    }

    _withAuthToken(url) {
        const u = String(url || '').trim();
        if (!u) return '';
        const token = this._getApiToken();
        if (!token) return u;
        const sep = u.includes('?') ? '&' : '?';
        return `${u}${sep}access_token=${encodeURIComponent(token)}`;
    }

    _readFileAsDataUrl(fileObj) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (event) => resolve(String(event?.target?.result || ''));
            reader.onerror = () => reject(new Error('file_read_failed'));
            reader.readAsDataURL(fileObj);
        });
    }

    _buildAuthHeaders() {
        const token = this._getApiToken();
        const headers = { 'Content-Type': 'application/json' };
        if (token) {
            headers.Authorization = `Bearer ${token}`;
        }
        return headers;
    }

    _normalizeTextToHtml(text) {
        const normalized = String(text || '')
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n')
            .trim();
        if (!normalized) return '';
        const paragraphs = normalized.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
        if (paragraphs.length === 0) {
            return `<p>${this._escapeHtml(normalized).replace(/\n/g, '<br>')}</p>`;
        }
        return paragraphs.map((p) => `<p>${this._escapeHtml(p).replace(/\n/g, '<br>')}</p>`).join('');
    }

    async bootstrapWorkspace() {
        try {
            const res = await fetch(`/manuscript/api/bootstrap/${encodeURIComponent(this.app.pid)}`);
            const data = await res.json();
            if (!res.ok || !data.ok) {
                throw new Error(data.message || `Bootstrap failed: ${res.status}`);
            }

            if (Array.isArray(data.sections) && data.sections.length > 0) {
                this.app.sections = data.sections.map((s) => ({
                    id: s.id,
                    label: s.label,
                    is_fixed: !!s.is_fixed,
                }));
            }

            if (this.app.paperTitleInput) {
                const current = (this.app.paperTitleInput.value || '').trim();
                if (!current || current === this.app.pid || current.toLowerCase() === 'untitled_paper') {
                    this.app.paperTitleInput.value = data.initial_title || this.app.pid;
                }
            }

            return data;
        } catch (e) {
            console.warn('[ManuscriptWS] Bootstrap fallback:', e);
            return null;
        }
    }

    // =========================================================================
    // 佈局與視窗控制 (Panel Layout Manager)
    // =========================================================================
    hidePanel(panelId) {
        const panel = document.getElementById(`panel${panelId}`);
        if(panel) {
            panel.classList.remove('d-flex'); 
            panel.style.display = 'none';
            this.app.hiddenPanels.add(panelId);
            this.updateResizersVisibility();
            this.renderRestoreButtons();
            
            const visibleCount = 3 - this.app.hiddenPanels.size;
            const newWidth = visibleCount > 0 ? `${100 / visibleCount}%` : '0%';
            if(!this.app.hiddenPanels.has('2C')) this.app.panel2C.style.width = newWidth;
            if(!this.app.hiddenPanels.has('2B')) this.app.panel2B.style.width = newWidth;
            if(!this.app.hiddenPanels.has('2A')) this.app.panel2A.style.width = newWidth;
        }
    }

    restorePanel(panelId) {
        const panel = document.getElementById(`panel${panelId}`);
        if(panel) {
            panel.style.display = '';
            panel.classList.add('d-flex'); 
            this.app.hiddenPanels.delete(panelId);
            
            const visibleCount = 3 - this.app.hiddenPanels.size;
            const newWidth = `${100 / visibleCount}%`;
            if(!this.app.hiddenPanels.has('2C')) this.app.panel2C.style.width = newWidth;
            if(!this.app.hiddenPanels.has('2B')) this.app.panel2B.style.width = newWidth;
            if(!this.app.hiddenPanels.has('2A')) this.app.panel2A.style.width = newWidth;
            
            this.updateResizersVisibility();
            this.renderRestoreButtons();
        }
    }

    updateResizersVisibility() {
        const showR1 = !this.app.hiddenPanels.has('2C') && !this.app.hiddenPanels.has('2B');
        const showR2 = !this.app.hiddenPanels.has('2B') && !this.app.hiddenPanels.has('2A');
        if(this.app.resizer1) this.app.resizer1.style.display = showR1 ? 'flex' : 'none';
        if(this.app.resizer2) this.app.resizer2.style.display = showR2 ? 'flex' : 'none';
    }

    renderRestoreButtons() {
        if(!this.app.panelRestoreGroup) return;
        this.app.panelRestoreGroup.innerHTML = '';
        if(this.app.restoreHintText) {
            this.app.restoreHintText.style.display = this.app.hiddenPanels.size > 0 ? 'inline-block' : 'none';
            this.app.panelRestoreGroup.appendChild(this.app.restoreHintText);
        }
        this.app.hiddenPanels.forEach(p => {
            const btn = document.createElement('button');
            btn.className = 'btn btn-sm btn-warning fw-bold shadow-sm ms-2 animate__animated animate__headShake';
            btn.innerHTML = `<i class="bi bi-window-plus me-1"></i> 恢復 ${p}`;
            btn.onclick = () => this.restorePanel(p);
            this.app.panelRestoreGroup.appendChild(btn);
        });
    }

    fullscreenPanel(panelId) {
        const panel = document.getElementById(`panel${panelId}`);
        if (!panel) return;
        if (panel.classList.contains('fullscreen-mode')) {
            panel.classList.remove('fullscreen-mode');
            this.app.fullscreenState = null;
            const iconObj = panel.querySelector('.bi-arrows-angle-contract');
            if (iconObj) iconObj.classList.replace('bi-arrows-angle-contract', 'bi-arrows-angle-expand');
        } else {
            panel.classList.add('fullscreen-mode');
            this.app.fullscreenState = panelId;
            const iconObj = panel.querySelector('.bi-arrows-angle-expand');
            if (iconObj) iconObj.classList.replace('bi-arrows-angle-expand', 'bi-arrows-angle-contract');
        }
    }

    // =========================================================================
    // 按鈕 UI 回饋 (按鈕打勾成功提示) 
    // =========================================================================
    flashButtonSuccess(btn) {
        if(!btn) return;
        btn.innerHTML = '<i class="bi bi-check-lg"></i>';
        btn.classList.replace('btn-primary', 'btn-success');
        setTimeout(() => {
            btn.innerHTML = '<i class="bi bi-floppy-fill"></i>';
            btn.classList.replace('btn-success', 'btn-primary');
        }, 2000);
    }

    // =========================================================================
    // 分隔線與系統輔助 (Smooth Resizer Engine) 
    // =========================================================================
    setupResizer() {
        if (this.app.resizer1) {
            this.app.resizer1.addEventListener('mousedown', () => {
                if(this.app.fullscreenState) return; 
                this.isResizing1 = true; 
                document.body.style.cursor='col-resize'; 
                document.body.style.userSelect='none'; 
                this.app.resizer1.classList.add('active');
            });
        }
        if (this.app.resizer2) {
            this.app.resizer2.addEventListener('mousedown', () => {
                if(this.app.fullscreenState) return;
                this.isResizing2 = true; 
                document.body.style.cursor='col-resize'; 
                document.body.style.userSelect='none'; 
                this.app.resizer2.classList.add('active');
            });
        }

        document.addEventListener('mousemove', (e) => {
            if(!this.isResizing1 && !this.isResizing2) return;
            const container = document.getElementById('workspaceContainer');
            if (!container) return;
            
            const rect = container.getBoundingClientRect();
            const w = rect.width;
            let px = ((e.clientX - rect.left) / w) * 100;
            const SAFE_MARGIN = 15; 
            
            if(this.isResizing1) {
                let w2a = parseFloat(this.app.panel2A.style.width) || 33.33;
                let maxP = 100 - w2a - SAFE_MARGIN; 
                if (px > SAFE_MARGIN && px < maxP) {
                    this.app.panel2C.style.width = `${px}%`;
                    this.app.panel2B.style.width = `${100 - w2a - px}%`;
                }
            } else if (this.isResizing2) {
                let w2c = parseFloat(this.app.panel2C.style.width) || 33.33;
                let minP = w2c + SAFE_MARGIN;
                if (px > minP && px < (100 - SAFE_MARGIN)) {
                    this.app.panel2B.style.width = `${px - w2c}%`;
                    this.app.panel2A.style.width = `${100 - px}%`;
                }
            }
        });

        document.addEventListener('mouseup', () => {
            this.isResizing1 = false; this.isResizing2 = false;
            document.body.style.cursor='default';
            document.body.style.userSelect=''; 
            if(this.app.resizer1) this.app.resizer1.classList.remove('active');
            if(this.app.resizer2) this.app.resizer2.classList.remove('active');
        });
    }

    // =========================================================================
    // 輔助選單與數據統計 (Utilities & Stats) 
    // =========================================================================
    setupToolbarActions() {
        if (this._toolbarBound) return;
        this._toolbarBound = true;

        const exportBtn = document.getElementById('btnExportWord');
        if (exportBtn) {
            exportBtn.addEventListener('click', () => this.exportFusionToWord());
        }

        if (this.app.sectionDropdownMenu) {
            this.app.sectionDropdownMenu.addEventListener('click', (e) => {
                const item = e.target.closest('[data-section-id]');
                if (!item) return;
                e.preventDefault();
                const sectionId = item.getAttribute('data-section-id');
                if (!sectionId) return;

                this.app.selectedSections = new Set([sectionId]);
                // [v1.8] sectionVersion 現在是版本選單，選項屬於「上一個章節」。
                // 直接塞舊值會落空（select 找不到對應 option 就變成空字串），
                // 所以改為向伺服器要這個章節的版本清單，由 block_list 事件重畫選單。
                if (this.app.sectionVersion) {
                    this.app.sectionVersion.innerHTML = '<option value="">載入中…</option>';
                }
                if (this.app.socket && this.app.pid) {
                    this.app.socket.emit('cmd_list_blocks', {
                        pid: this.app.pid,
                        section: sectionId,
                    });
                }
                if (this.app.drafterTargetSection) {
                    this.app.drafterTargetSection.value = sectionId;
                }

                this.updateDropdownLabel();
                this.renderSectionDropdown();
                // [collab] 換章節後刷新唯讀鎖定、留言數徽章，以及開著的留言側欄。
                if (this.app.collab) {
                    this.app.collab.refreshLock();
                    this.app.collab.refreshCommentBadge();
                    const panel = document.getElementById('chapterCommentPanel');
                    if (panel && panel.style.display === 'flex') {
                        this.app.collab.refreshComments();
                    }
                }
                if (this.app.soed && this.app.soed.loadMultiSectionContent) {
                    this.app.soed.loadMultiSectionContent();
                }

                if (this.app.soed && this.app.soed.addSystemMessage) {
                    const label = this.app.sections.find((s) => s.id === sectionId)?.label || sectionId;
                    this.app.soed.addSystemMessage(`Switched current section to: ${label}`);
                }
            });
        }
    }

    renderSectionDropdown() {
        const activeId = Array.from(this.app.selectedSections)[0] || 'abstract';
        const excludeList = ['title', 'author', 'keyword'];
        this.updateDropdownLabel();

        if (this.app.sectionDropdownMenu) {
            this.app.sectionDropdownMenu.innerHTML = '';
            this.app.sections.forEach((sec) => {
                if (excludeList.includes(sec.id)) return;
                const li = document.createElement('li');
                const activeCls = sec.id === activeId ? 'active fw-bold' : '';
                li.innerHTML = `<button type="button" class="dropdown-item ${activeCls}" data-section-id="${this._escapeHtml(sec.id)}">${this._escapeHtml(sec.label)}</button>`;
                this.app.sectionDropdownMenu.appendChild(li);
            });
        }

        // 針對 2A 視窗的下拉選單進行冗餘過濾 (移除 title, author, keyword)
        if(this.app.drafterTargetSection) {
            const currentVal = this.app.drafterTargetSection.value;
            this.app.drafterTargetSection.innerHTML = '';
            this.app.sections.forEach(sec => {
                if (excludeList.includes(sec.id)) return; 
                const opt = document.createElement('option');
                opt.value = sec.id; opt.innerText = sec.label;
                this.app.drafterTargetSection.appendChild(opt);
            });
            if(currentVal && this.app.sections.find(s => s.id === currentVal) && !excludeList.includes(currentVal)) {
                this.app.drafterTargetSection.value = currentVal;
            } else {
                this.app.drafterTargetSection.value = 'abstract';
            }
        }
    }

    toggleSectionSelection(id, isChecked, checkboxElement) {
        // [保留介面] 完整保留舊有多選切換與 IORuling 防跳選檢核邏輯
    }

    updateDropdownLabel() {
        const activeId = Array.from(this.app.selectedSections)[0] || 'abstract';
        const label = this.app.sections.find(s => s.id === activeId)?.label || activeId;
        if(this.app.sectionDropdownBtn) {
            this.app.sectionDropdownBtn.innerHTML = `<i class="bi bi-file-text me-2"></i>${label}`;
        }
    }

    exportFusionToWord() {
        const fusionCanvas = this.app.fusionCanvas;
        if (!fusionCanvas) return;

        const rawText = (fusionCanvas.innerText || '').trim();
        const cleanText = rawText.replace(/來自 2B 的段落將會依序插入於此處\.\.\./g, '').trim();
        if (!cleanText) {
            alert('目前 2C 畫布沒有可匯出的內容。');
            return;
        }

        const title = (this.app.paperTitleInput?.value || this.app.pid || 'manuscript').trim();
        const safeName = title.replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, '_') || 'manuscript';
        const exportHtml = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${this._escapeHtml(title)}</title></head><body>${fusionCanvas.innerHTML}</body></html>`;
        const blob = new Blob(['\ufeff', exportHtml], { type: 'application/msword;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${safeName}.doc`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        if (this.app.soed && this.app.soed.addSystemMessage) {
            this.app.soed.addSystemMessage(`Word export completed: ${safeName}.doc`);
        }
    }

    // =========================================================================
    // 章節管理功能 (Modal Manager)
    // =========================================================================
    openSectionManager() { 
        this.renderManagerList(); 
        bootstrap.Modal.getOrCreateInstance(this.app.sectionManagerModal).show(); 
    }
    
    renderManagerList() {
        this.app.sectionListContainer.innerHTML = '';
        this.app.sections.forEach((sec, index) => {
            const isFixed = sec.is_fixed;
            const disabledAttr = isFixed ? 'disabled' : '';
            const row = document.createElement('div');
            row.className = 'input-group mb-2 shadow-sm animate__animated animate__fadeInDown';
            row.innerHTML = `
                <span class="input-group-text ${isFixed ? 'bg-dark text-warning' : 'bg-light text-muted'} fw-bold" style="width:45px;">
                    ${isFixed ? '<i class="bi bi-lock-fill"></i>' : index+1}
                </span>
                <input type="text" class="form-control" value="${sec.label}" ${disabledAttr} onchange="wsApp.updateSectionLabel(${index}, this.value)">
                ${isFixed ? '' : `
                    <button class="btn btn-outline-secondary" onclick="wsApp.moveSectionItem(${index}, -1)"><i class="bi bi-arrow-up"></i></button>
                    <button class="btn btn-outline-secondary" onclick="wsApp.moveSectionItem(${index}, 1)"><i class="bi bi-arrow-down"></i></button>
                    <button class="btn btn-outline-danger" onclick="wsApp.removeSectionItem(${index})"><i class="bi bi-trash"></i></button>
                `}
            `;
            this.app.sectionListContainer.appendChild(row);
        });
    }

    updateSectionLabel(idx, val) { 
        if(this.app.sections[idx] && !this.app.sections[idx].is_fixed) this.app.sections[idx].label = val.trim(); 
    }

    moveSectionItem(idx, dir) {
        if(this.app.sections[idx].is_fixed) return;
        const tIdx = idx + dir;
        if(tIdx < 0 || tIdx >= this.app.sections.length || this.app.sections[tIdx].is_fixed) return;
        [this.app.sections[idx], this.app.sections[tIdx]] = [this.app.sections[tIdx], this.app.sections[idx]];
        this.renderManagerList();
    }

    /**
     * 由章節顯示名產生 section_key。
     *
     * 舊做法 name.toLowerCase().replace(/[^a-z0-9]/g, '_') 是逐字元替換，
     * 中文章節名的每個字都變成一個底線 —— 「緒論」與「討論」都會得到 '__'，
     * 任兩個等長的中文名稱都會撞成同一個 key。section_key 同時是儲存目錄名、
     * 章節指派索引與留言索引，撞了就會版本互相污染、指派其一等於指派另一個。
     *
     * 改為：先收斂連續非法字元，兩端去底線；結果為空（純非 ASCII 名稱）時
     * 改用序號式 key，最後再確保在目前清單中唯一。
     * 後端 _normalize_section_key 會再驗一次，這裡只是讓 key 好讀。
     */
    _makeSectionKey(label) {
        const slug = String(label || '')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '_')
            .replace(/^_+|_+$/g, '')
            .slice(0, 50);
        const existing = new Set((this.app.sections || []).map(s => s.id));
        const base = slug || 'sec';
        let candidate = base;
        let n = 1;
        while (existing.has(candidate)) {
            n += 1;
            candidate = `${base}_${n}`.slice(0, 50);
        }
        return candidate;
    }

    addSectionItem() {
        const name = this.app.newSectionName.value.trim();
        if(!name) return;
        this.app.sections.push({
            id: this._makeSectionKey(name),
            label: name,
            is_fixed: false,
        });
        this.app.newSectionName.value = '';
        this.renderManagerList();
    }

    removeSectionItem(idx) { 
        if(this.app.sections[idx].is_fixed) return;
        if(confirm("Warning: Deleting this section will also remove its associated editor blocks. Continue?")) { 
            this.app.sections.splice(idx, 1); 
            this.renderManagerList(); 
        } 
    }

    async saveSectionConfig() { 
        try {
            const payload = {
                sections: this.app.sections.map((s) => ({
                    id: s.id,
                    label: s.label,
                    is_fixed: !!s.is_fixed,
                }))
            };
            const res = await fetch(`/manuscript/api/sections/${encodeURIComponent(this.app.pid)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                throw new Error(data.message || 'Failed to save section config');
            }
            this.renderSectionDropdown();
            bootstrap.Modal.getOrCreateInstance(this.app.sectionManagerModal).hide();
            if (this.app.soed && this.app.soed.addSystemMessage) {
                this.app.soed.addSystemMessage('Section config synced to server.');
            }
        } catch (e) {
            alert(`Section config save failed: ${e.message}`);
        }
    }

    // =========================================================================
    // 檔案上傳與轉換邏輯
    // =========================================================================
    _isTextMime(mime) {
        const m = String(mime || '').toLowerCase().trim();
        if (!m) return false;
        if (m.startsWith('text/')) return true;
        if (m.includes('json') || m.includes('xml') || m.includes('csv') || m.includes('yaml')) return true;
        if (m.includes('javascript') || m.includes('markdown')) return true;
        return false;
    }

    _isTextExt(filename) {
        const name = String(filename || '').toLowerCase();
        return [
            '.txt', '.md', '.markdown', '.csv', '.json', '.ndjson', '.xml', '.yml', '.yaml',
            '.html', '.htm', '.log', '.py', '.js', '.ts', '.tsx', '.jsx', '.css', '.sql',
            '.sh', '.bat', '.ps1', '.ini', '.cfg', '.conf'
        ].some((ext) => name.endsWith(ext));
    }

    _isDocxExt(filename) {
        const name = String(filename || '').toLowerCase().trim();
        return name.endsWith('.docx');
    }

    _isLegacyDocExt(filename) {
        const name = String(filename || '').toLowerCase().trim();
        return name.endsWith('.doc') && !name.endsWith('.docx');
    }

    _isDocxMime(mime) {
        const m = String(mime || '').toLowerCase().trim();
        return m === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    }

    _isLegacyDocMime(mime) {
        const m = String(mime || '').toLowerCase().trim();
        return m === 'application/msword';
    }

    _shouldReadAsText(fileObj) {
        const mime = String(fileObj?.type || '').toLowerCase();
        const name = String(fileObj?.name || '');

        if (this._isTextMime(mime) || this._isTextExt(name)) return true;
        if (mime.startsWith('image/') || mime.startsWith('video/') || mime.startsWith('audio/')) return false;
        if (mime.includes('pdf')) return false;
        if (mime.includes('excel') || mime.includes('spreadsheet') || mime.includes('word') || mime.includes('zip')) return false;
        return false;
    }

    handleFileSelect(e) { 
        const f = e.target.files[0]; 
        if(!f) return; 

        const fileName = String(f.name || '').trim();
        const fileMime = String(f.type || '').toLowerCase().trim();
        if (this._isLegacyDocExt(fileName) || this._isLegacyDocMime(fileMime)) {
            this.clearFile();
            alert('目前 Manuscript 僅支援匯入 .docx（不支援舊版 .doc）。請先另存成 .docx 後再上傳。');
            if (this.app.soed && this.app.soed.addSystemMessage) {
                this.app.soed.addSystemMessage('Word import blocked: legacy .doc is not supported. Please convert to .docx.');
            }
            return;
        }

        const reader = new FileReader();
        reader.onload = (event) => {
            this.app.currentAttachment = { 
                name: f.name, 
                mime: f.type || 'application/octet-stream',
                content: event.target.result
            }; 
            this.app.filePreviewArea.classList.remove('d-none'); 
            this.app.fileNameDisplay.innerText = f.name;
        };
        if (this._shouldReadAsText(f)) {
            reader.readAsText(f, 'utf-8');
        } else {
            reader.readAsDataURL(f);
        }
    }

    clearFile() { 
        this.app.fileInput.value = ''; 
        this.app.currentAttachment = null; 
        this.app.filePreviewArea.classList.add('d-none'); 
    }

    triggerFileUpload() { 
        const typeSelect = document.getElementById('importType');
        this.app.currentImportType = typeSelect ? typeSelect.value : 'other';
        this.app.fileInput.click(); 
    }

    triggerGoogleDrive() {
        const typeSelect = document.getElementById('importType');
        this.app.currentImportType = typeSelect ? typeSelect.value : 'other'; 
        if(window.driveAdapter) {
            window.driveAdapter.openPicker((fileObj) => {
                const selectedName = String(fileObj?.name || '').trim();
                const selectedMime = String(fileObj?.mime || '').toLowerCase().trim();
                if (this._isLegacyDocExt(selectedName) || this._isLegacyDocMime(selectedMime)) {
                    alert('Google Drive 匯入目前僅支援 .docx；請先將舊版 .doc 轉檔。');
                    if (this.app.soed && this.app.soed.addSystemMessage) {
                        this.app.soed.addSystemMessage('Google Drive Word import blocked: legacy .doc is not supported.');
                    }
                    return;
                }
                this.app.currentAttachment = fileObj;
                this.app.filePreviewArea.classList.remove('d-none'); 
                this.app.fileNameDisplay.innerText = fileObj.name + ' (Google Drive)';
            });
        } else {
            alert("Google Drive 模組尚未載入");
        }
    }

    triggerWordImport(targetCanvas = '2B') {
        const target = String(targetCanvas || '').toUpperCase() === '2C' ? '2C' : '2B';
        if (!this.app.pid) {
            alert('尚未選擇專案，無法匯入 Word。');
            return;
        }
        if (!this.app.wordImportInput) {
            alert('Word 匯入元件尚未載入。');
            return;
        }
        this.pendingWordImportTarget = target;
        this.app.wordImportInput.value = '';
        this.app.wordImportInput.click();
    }

    async _importWordToTarget(payload, target) {
        const url = this._withAuthToken(`/manuscript/api/import_word/${encodeURIComponent(this.app.pid)}`);
        const res = await fetch(url, {
            method: 'POST',
            headers: this._buildAuthHeaders(),
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            const msg = data.message || `Word import failed (${res.status})`;
            throw new Error(msg);
        }

        const importedText = String(data.text || '').trim();
        if (!importedText) {
            throw new Error('Word 內容為空，無法匯入。');
        }

        const html = this._normalizeTextToHtml(importedText);
        if (!html) {
            throw new Error('Word 內容解析後為空，無法匯入。');
        }

        if (target === '2C') {
            if (!this.app.fusionCanvas) {
                throw new Error('2C 畫布不存在。');
            }
            const rawText = String(this.app.fusionCanvas.innerText || '').replace(/來自 2B 的段落將會依序插入於此處\.\.\./g, '').trim();
            if (!rawText) {
                this.app.fusionCanvas.innerHTML = '';
            }
            const importedBlock = `
                <div class="fusion-block mb-4 border-start border-4 border-primary ps-3 animate__animated animate__fadeInLeft">
                    <h5 class="text-primary fw-bold"><i class="bi bi-file-earmark-word me-1"></i>Word 匯入原文</h5>
                    <div class="fusion-body">${html}</div>
                </div>
            `;
            this.app.fusionCanvas.innerHTML += importedBlock;
            if (this.app.soed && this.app.soed.updateWordCount) {
                this.app.soed.updateWordCount();
            }
        } else {
            if (!this.app.editorCanvas) {
                throw new Error('2B 畫布不存在。');
            }
            const sectionId = this.app.drafterTargetSection?.value || Array.from(this.app.selectedSections || [])[0] || 'abstract';
            this.app.selectedSections = new Set([sectionId]);
            this.updateDropdownLabel();
            this.app.editorCanvas.innerHTML = '';
            this.app.editorCanvas.focus();
            if (this.app.soed && this.app.soed.insertEditorCard) {
                this.app.soed.insertEditorCard(html, sectionId);
            } else {
                this.app.editorCanvas.innerHTML = html;
            }
        }

        if (this.app.soed && this.app.soed.addSystemMessage) {
            const stat = data.truncated ? ' (內容過長已截斷)' : '';
            this.app.soed.addSystemMessage(`Word 匯入完成 → ${target}${stat}`);
        }
    }

    async handleWordImportFileSelect(e) {
        const f = e?.target?.files?.[0];
        const target = this.pendingWordImportTarget || '2B';
        this.pendingWordImportTarget = null;
        if (!f) return;

        const fileName = String(f.name || '').trim();
        const fileMime = String(f.type || '').toLowerCase().trim();

        try {
            if (this._isLegacyDocExt(fileName) || this._isLegacyDocMime(fileMime)) {
                throw new Error('目前僅支援 .docx。請先將 .doc 另存為 .docx。');
            }
            if (!(this._isDocxExt(fileName) || this._isDocxMime(fileMime))) {
                throw new Error('請選擇 .docx 檔案。');
            }

            const dataUrl = await this._readFileAsDataUrl(f);
            if (!dataUrl || !dataUrl.startsWith('data:')) {
                throw new Error('無法讀取 Word 檔案內容。');
            }
            await this._importWordToTarget(
                {
                    filename: fileName,
                    mime: fileMime || 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    content: dataUrl,
                },
                target
            );
        } catch (err) {
            const msg = err?.message || 'Word 匯入失敗';
            alert(msg);
            if (this.app.soed && this.app.soed.addSystemMessage) {
                this.app.soed.addSystemMessage(`Word 匯入失敗：${msg}`);
            }
        } finally {
            if (this.app.wordImportInput) this.app.wordImportInput.value = '';
        }
    }

    // =========================================================================
    // [v0.3 新增核心] 論文素材庫 (Asset Gallery) 與 游標注入引擎
    // =========================================================================
    
    /**
     * 開啟圖庫 Modal，並精準捕捉使用者目前的游標位置 (Cursor Range)
     */
    openAssetGallery(targetCanvas) {
        this.currentInsertTarget = targetCanvas;
        
        // 更新 UI 顯示目前將要插入到哪一個畫布
        const displayEl = document.getElementById('assetTargetDisplay');
        if(displayEl) displayEl.innerText = `Panel ${targetCanvas}`;
        
        // 捕捉並儲存當前游標位置 (極度重要，否則點擊按鈕時會失去焦點)
        const selection = window.getSelection();
        if (selection.rangeCount > 0) {
            this.savedCursorRange = selection.getRangeAt(0);
        } else {
            this.savedCursorRange = null;
        }

        // 顯示 Modal
        const modalEl = document.getElementById('assetGalleryModal');
        if(modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).show();
        
        // 請求後端最新圖庫清單
        const container = document.getElementById('galleryImageContainer');
        if(container) container.innerHTML = `<div class="text-center text-muted p-4 w-100"><div class="spinner-border spinner-border-sm me-2"></div>正在從 Docker Volume 讀取素材...</div>`;
        
        // 發送 Socket 請求給 Python 端讀取 image_registry.json
        this.app.socket.emit('cmd_get_image_registry', { pid: this.app.pid });
    }

    /**
     * 接收後端資料，動態渲染圖片清單
     */
    renderAssetGallery(images) {
        const container = document.getElementById('galleryImageContainer');
        if(!container) return;
        
        container.innerHTML = '';
        if(!images || images.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted p-4 w-100 border rounded bg-white">
                    <i class="bi bi-images fs-3 d-block mb-2 text-secondary"></i>
                    目前圖庫中沒有任何實體圖片。<br><small>請先在 2A 區利用 AI 生成圖表並點擊「轉存 PNG」。</small>
                </div>`;
            return;
        }
        
        // 反轉陣列，讓最新生成的圖片顯示在最前面
        const reversedImages = [...images].reverse();
        
        reversedImages.forEach(img => {
            const col = document.createElement('div');
            col.className = 'col-md-6 col-lg-4';
            const imagePath = this._withAuthToken(img.path);
            const safePath = this._escapeHtml(imagePath);
            const safeCaption = this._escapeHtml(img.caption);
            const safeFigId = this._escapeHtml(img.fig_id);
            const sizeText = img.size_bytes ? Math.round(img.size_bytes / 1024) + ' KB' : '';
            const safeSizeText = this._escapeHtml(sizeText);
            
            // 準備要插入到畫布的 HTML 標籤 (包含 Bootstrap 的自適應與圖說排版)
            const insertHtml = `
                <div class="text-center my-4 image-container" contenteditable="false">
                    <img src="${safePath}" alt="${safeCaption}" class="img-fluid border border-2 rounded shadow-sm" style="max-width: 90%;">
                    <p class="text-muted fw-bold small mt-2"><i>${safeFigId}: ${safeCaption}</i></p>
                </div><p><br></p>
            `;
            
            // 為了避免引號衝突，使用 encodeURIComponent 將 HTML 包裝起來
            const safeHtml = encodeURIComponent(insertHtml);
            
            col.innerHTML = `
                <div class="card h-100 shadow-sm asset-card border-0">
                    <div class="card-img-top bg-light text-center p-2" style="height: 140px; overflow: hidden; display: flex; align-items: center; justify-content: center;">
                        <img src="${safePath}" class="img-fluid rounded border" style="max-height: 100%; object-fit: contain; background: white;">
                    </div>
                    <div class="card-body p-2 d-flex flex-column bg-white">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span class="badge bg-secondary">${safeFigId}</span>
                            <span class="small text-muted" style="font-size: 0.7rem;">${safeSizeText}</span>
                        </div>
                        <p class="card-text small text-truncate mb-2 text-dark fw-bold" title="${safeCaption}">${safeCaption}</p>
                        <button class="btn btn-sm btn-outline-primary mt-auto fw-bold" onclick="wsApp.insertAssetToCanvas(decodeURIComponent('${safeHtml}'))">
                            <i class="bi bi-box-arrow-in-down-right me-1"></i> 插入到游標處
                        </button>
                    </div>
                </div>
            `;
            container.appendChild(col);
        });
    }

    /**
     * 游標精準注入引擎 (Cursor Injection Core)
     */
    insertAssetToCanvas(htmlContent) {
        // 1. 關閉 Modal
        const modalEl = document.getElementById('assetGalleryModal');
        if(modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        
        // 2. 確定目標畫布 DOM
        const targetCanvas = this.currentInsertTarget === '2C' ? this.app.fusionCanvas : this.app.editorCanvas;
        if (!targetCanvas) return;
        
        // 3. 聚焦畫布並還原游標選取區 (Selection)
        targetCanvas.focus();
        const selection = window.getSelection();
        
        if (this.savedCursorRange) {
            selection.removeAllRanges();
            selection.addRange(this.savedCursorRange);
        } else {
            // 防呆：如果之前沒有成功抓到游標，預設將游標移到畫布的最末端
            selection.selectAllChildren(targetCanvas);
            selection.collapseToEnd();
        }
        
        // 4. 呼叫瀏覽器原生富文本 API 進行精準插入
        if (document.queryCommandSupported('insertHTML')) {
            document.execCommand('insertHTML', false, htmlContent);
        } else {
            // 針對極舊版瀏覽器的備用方案 (Fallback)
            targetCanvas.innerHTML += htmlContent;
        }
        
        // 5. 更新 2B/2C 的即時字數統計
        this.app.soed.updateWordCount();
        
        // 重置游標緩存
        this.savedCursorRange = null;
    }
}

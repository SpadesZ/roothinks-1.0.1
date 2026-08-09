// Roothinks source maintenance contract
// 檔案路徑: app/static/js/manuscript_soed.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 Manuscript 2A Drafter 的章節切換、對話歷史、換行送出、job ack/取消與 stale-response 防護。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/manuscript_soed.js
//路徑(./app/static/js/manuscript_soed.js)
//版本 v1.2 (Drafter multiline input + visible cancellation lifecycle)
//更版時間 20260806-0130
// inner comment: 保留 v1.0 全量代碼與防呆邏輯。本版變更：
//   [v1.1] 修掉「送出後永久轉圈」：新增 _armAckTimeout / _failPendingRequest。
//     - 舊 _armTypingTimeout 在 activeJobId 為 null 時直接 return，
//       而收不到 job_queued 正是 activeJobId 為 null 的情況 ——
//       等於唯一的逾時保護在最需要它的時候被關掉，實測可無聲轉三小時。
//     - 新增 20s ack 時限、sys_msg 終結、disconnect 終結三個出口，
//       對應伺服器 manuscript_routes.py v1.8 的 chat_message 事件契約。
//   [v1.0] 版本號改由伺服器指派（max + 0.1），前端不再自行遞增。
//     - cardActionSave 只送 from_ver（目前檢視版本），版號由 save_ack 回傳。
//     - S.Ver / G.Ver 由文字輸入框改為版本選單，選取即載入該版。
//     - 舊做法讓使用者能手打回既有版號，會產生相同檔名而靜默覆蓋該版內容。
//   [v1.0] 停筆 1.5 秒自動存檔：走 cmd_autosave_block 寫入單一草稿檔，不產生版本。
//     - 若自動存檔也跳版，一次編輯就會噴出數十個版本，版本選單失去意義。
//   [v1.0] 主論文版本為整數 V1/V2 的 manifest，可依 sections 對照表一鍵還原整組章節。
//   [v0.9] 保留 data-rev / save_conflict 機制：版本快照是 append-only 不會衝突，
//     但 rev 相關程式碼仍供 peer_update 提示與未來的就地編輯情境使用。
//   - peer_update 事件：若開啟段落被別人存，顯示非阻塞提示條「[by] 剛更新了此段落 [重新載入]」。
// CHANGE_PLAN_STUDY_FLOWB_2026-04-20: MVP prototype - wire direct .docx importer event to UI handler.

class ManuSoed {
    constructor(app) {
        this.app = app;
        this.activeJobId = null;
        this.requestPending = false;
        this.typingTimeoutHandle = null;
        this.typingWarnMs = 120000;
        // [v1.1] 送出後等待 job_queued 的硬性時限。
        // typingWarnMs(120s) 是「job 已在跑但很久」的提示，兩者不能共用一支計時器：
        // 沒收到 ack 代表伺服器根本沒接下這個請求，再等下去也不會有結果。
        this.ackTimeoutHandle = null;
        this.ackWaitMs = 20000;
        // [v0.9] 衝突防護：記錄目前開啟段落的版本號 { sectionId: rev }
        this._blockRevMap = {};
    }

    // =========================================================================
    // [v0.9] 衝突防護輔助方法
    // =========================================================================

    /**
     * 從 card DOM 讀取目前 rev（data-rev 屬性）；未設定回 null（無 base_rev 模式）。
     */
    _getCardRev(card) {
        const v = card.getAttribute('data-rev');
        if (v === null || v === '') return null;
        const n = parseInt(v, 10);
        return isNaN(n) ? null : n;
    }

    /**
     * 更新 card DOM 的 data-rev 屬性，並同步更新 _blockRevMap。
     */
    _setCardRev(card, rev) {
        if (rev === null || rev === undefined) return;
        card.setAttribute('data-rev', String(rev));
        const section = card.getAttribute('data-section') || 'general';
        this._blockRevMap[section] = rev;
    }

    /**
     * 顯示衝突提示條（紅色，non-blocking）。
     * 提供「重新載入」與「覆蓋儲存」兩個 CTA。
     */
    _showConflictBar(card, conflictData) {
        // 先清除舊衝突條
        const old = card.querySelector('.conflict-bar');
        if (old) old.remove();

        const section = card.getAttribute('data-section') || 'general';
        const byName = conflictData.updated_by || '其他人';
        const curRev = conflictData.current_rev;

        const bar = document.createElement('div');
        bar.className = 'conflict-bar';
        bar.style.cssText = [
            'background:#fee2e2',
            'border:1.5px solid #ef4444',
            'border-radius:6px',
            'padding:8px 12px',
            'margin-bottom:8px',
            'font-size:0.85em',
            'display:flex',
            'align-items:center',
            'gap:10px',
            'flex-wrap:wrap',
        ].join(';');

        const msg = document.createElement('span');
        msg.style.flex = '1';
        msg.innerHTML = `&#9888; 此段落已被 <strong>${this._esc(byName)}</strong> 更新 (v${curRev})，你的變更未儲存。`;

        const btnReload = document.createElement('button');
        btnReload.className = 'btn btn-sm btn-outline-danger fw-bold';
        btnReload.textContent = '重新載入';
        btnReload.onclick = () => {
            bar.remove();
            // 重新請求伺服器最新版本
            const files = this.app.socket;
            this.app.socket.emit('cmd_list_blocks', { pid: this.app.pid, section: section });
            this.addSystemMessage(`正在重新載入段落 [${section}]...`);
        };

        const btnForce = document.createElement('button');
        btnForce.className = 'btn btn-sm btn-danger fw-bold';
        btnForce.textContent = '覆蓋儲存';
        btnForce.onclick = () => {
            bar.remove();
            // 以 current_rev 為 base_rev 強制重送（明示覆蓋）
            this._setCardRev(card, curRev);
            this.cardActionSave(card.querySelector('button[title="Save Block"]'));
        };

        bar.appendChild(msg);
        bar.appendChild(btnReload);
        bar.appendChild(btnForce);

        // 插入到 card 頂部
        card.insertBefore(bar, card.firstChild);
    }

    /**
     * 顯示 peer_update 提示條（輕量，非阻塞，不打斷輸入）。
     */
    _showPeerUpdateBar(section, byName) {
        // 找到該 section 的 card
        const card = this.app.editorCanvas
            ? this.app.editorCanvas.querySelector(`.editor-card[data-section="${CSS.escape(section)}"]`)
            : null;

        const makeBar = () => {
            const bar = document.createElement('div');
            bar.className = 'peer-update-bar';
            bar.style.cssText = [
                'background:#fef9c3',
                'border:1px solid #eab308',
                'border-radius:6px',
                'padding:6px 12px',
                'margin-bottom:6px',
                'font-size:0.82em',
                'display:flex',
                'align-items:center',
                'gap:10px',
            ].join(';');
            bar.innerHTML = `<span style="flex:1"><i class="bi bi-people-fill me-1"></i><strong>${this._esc(byName)}</strong> 剛更新了此段落</span>`;

            const btnR = document.createElement('button');
            btnR.className = 'btn btn-sm btn-outline-warning fw-bold';
            btnR.textContent = '重新載入';
            btnR.onclick = () => {
                bar.remove();
                this.app.socket.emit('cmd_list_blocks', { pid: this.app.pid, section: section });
                this.addSystemMessage(`正在重新載入段落 [${section}]...`);
            };
            bar.appendChild(btnR);
            return bar;
        };

        if (card) {
            // 清除舊 peer bar
            const old = card.querySelector('.peer-update-bar');
            if (old) old.remove();
            card.insertBefore(makeBar(), card.firstChild);
        } else {
            // 段落未開啟，用 system message 代替
            this.addSystemMessage(`${byName} 剛更新了段落 [${section}]。`);
        }

        // 自動 8s 後消失
        setTimeout(() => {
            const bars = document.querySelectorAll('.peer-update-bar');
            bars.forEach(b => b.remove());
        }, 8000);
    }

    _esc(val) {
        return String(val || '').replace(/[&<>"']/g, (c) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    // =========================================================================
    // 畫布內容注入與複製邏輯 (Canvas Content Injection)
    // =========================================================================
    importToEditor(content) {
        if (!content || content.includes('bi-arrow-left') || content.includes('恢復 2A')) {
            console.error("[logic violation] prevented copying non-academic metadata.");
            alert("錯誤：系統攔截了非草稿內容。請點擊草稿桌布（黃底區塊）中的藍色按鈕。");
            return;
        }
        const currentSection = this.app.drafterTargetSection.value;
        this.app.selectedSections = new Set([currentSection]);
        this.app.ui.updateDropdownLabel();

        // 專注模式：如果傳入的是實體圖片標籤，保留並附加；若是文字則清空舊畫布
        if (content.includes('<img src=')) {
            this.insertEditorCard(content, currentSection);
        } else {
            this.app.editorCanvas.innerHTML = '';
            this.app.editorCanvas.focus();
            this.insertEditorCard(content, currentSection);
        }
    }

    /**
     * 確保插入點落在 2B 畫布內；不在就收合到畫布尾端（＝附加在既有內容之後）。
     *
     * 為什麼需要：execCommand('insertHTML') 插在「目前的插入點」，插入點不在
     * contenteditable 畫布內時它會安靜地什麼都不做，不丟例外也不回 false。
     * formatText() 早就有同一段前置動作，insertEditorCard() 當初漏了。
     */
    _placeCaretInEditor() {
        const canvas = this.app.editorCanvas;
        if (!canvas) return;
        const selection = window.getSelection();
        if (!selection) return;
        if (selection.rangeCount > 0
            && canvas.contains(selection.getRangeAt(0).commonAncestorContainer)) {
            return;   // 使用者正在畫布裡編輯，尊重他既有的插入點
        }
        canvas.focus();
        const range = document.createRange();
        range.selectNodeContents(canvas);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
    }

    insertEditorCard(content, sectionId = 'general') {
        const sectionLabel = this.app.sections.find(s => s.id === sectionId)?.label || sectionId;
        const cardHtml = `
            <div class="editor-card animate__animated animate__fadeInUp" data-section="${sectionId}" contenteditable="false">
                <div class="badge bg-secondary mb-2">${sectionLabel}</div>
                <div class="card-actions">
                    <button onclick="wsApp.cardActionPushToFusion(this)" title="Push to Fusor 2C"><i class="bi bi-arrow-left-circle-fill text-primary"></i></button>
                    <button onclick="wsApp.cardActionSave(this)" title="Save Block"><i class="bi bi-floppy"></i></button>
                    <button onclick="wsApp.cardActionContext(this)" title="Quote to Chat"><i class="bi bi-chat-quote"></i></button>
                </div>
                <div class="card-content" contenteditable="true">${content}</div>
            </div><p><br></p>
        `;
        // 載入舊版（block_loaded）與復原自動儲存草稿都是「先清空畫布再呼叫這裡」，
        // 此時插入點不在畫布內，execCommand 什麼都不做而系統訊息照報成功
        // ——症狀就是「回報載入成功，但編輯畫布是空的」。
        // importToEditor() 與 Word 匯入之所以一直正常，只是因為它們在呼叫前
        // 自己 focus 過。這個前提屬於本函式，補在這裡才不必每個呼叫端各自記得。
        this._placeCaretInEditor();

        if (document.queryCommandSupported('insertHTML')) {
            document.execCommand('insertHTML', false, cardHtml);
        } else {
            this.app.editorCanvas.innerHTML += cardHtml;
        }
        this.updateWordCount();
        // [collab] 卡片是每次重建的，插入後必須重新套用唯讀鎖定，
        // 否則沒有撰寫權的章節會以可編輯狀態出現。
        if (this.app.collab) this.app.collab.refreshLock();
    }

    formatText(command, val = null, target = 'editor') {
        const canvas = target === 'fusion' ? this.app.fusionCanvas : this.app.editorCanvas;
        if (!canvas) return;

        const selection = window.getSelection();
        let hasRange = selection && selection.rangeCount > 0;
        let inTargetCanvas = false;
        if (hasRange) {
            const range = selection.getRangeAt(0);
            inTargetCanvas = canvas.contains(range.commonAncestorContainer);
        }

        canvas.focus();
        if (!inTargetCanvas) {
            const range = document.createRange();
            range.selectNodeContents(canvas);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
        }

        let cmd = command;
        let cmdVal = val;
        if (command === 'h1') {
            cmd = 'formatBlock';
            cmdVal = '<h1>';
        } else if (command === 'h2') {
            cmd = 'formatBlock';
            cmdVal = '<h2>';
        } else if (command === 'list') {
            cmd = 'insertUnorderedList';
        }

        try {
            document.execCommand(cmd, false, cmdVal);
        } catch (err) {
            console.warn('[ManuscriptWS] formatText failed:', err);
            if (this.app.saveStatus) {
                this.app.saveStatus.innerText = `Formatting failed: ${command}`;
            }
        }

        if (target === 'editor') {
            this.updateWordCount();
        }
    }

    saveAllBlocks() {
        const cards = this.app.editorCanvas.querySelectorAll('.editor-card');
        if(cards.length === 0) return;

        cards.forEach(card => {
            const btn = card.querySelector('button[title="Save Block"]');
            if (btn) this.cardActionSave(btn);
        });
        // 版號由伺服器指派，前端事先不知道會是幾號，所以這裡不再預告版號；
        // 實際版號由 save_ack 回來後顯示在狀態列與版本選單。
    }

    /**
     * [v1.8] 建立章節版本快照。
     *
     * 版號完全交給伺服器指派（既有最大 + 0.1）。前端不再自行遞增——舊做法會在
     * 使用者手動改動版號輸入框時產生重複檔名而靜默覆蓋既有版本。
     * from_ver 帶上目前檢視中的版本，讓伺服器記錄「這一版改自哪一版」。
     */
    cardActionSave(btn) {
        const card = btn.closest('.editor-card');
        // [v0.6 修正] 改為 innerHTML，確保包含 <img> 標籤的內容能完整存檔
        const content = card.querySelector('.card-content').innerHTML;
        const section = card.getAttribute('data-section') || 'general';
        const icon = btn.querySelector('i');
        const originalClass = icon.className;
        icon.className = 'spinner-border spinner-border-sm text-primary';

        const title = this.app.paperTitleInput ? this.app.paperTitleInput.value.trim() : "Untitled_Paper";

        this.app.socket.emit('cmd_save_block', {
            pid: this.app.pid,
            title: title,
            section: section,
            content: content,
            from_ver: this._currentSectionVer(section),
        });

        if(this.app.saveStatus) this.app.saveStatus.innerText = `Saving [${section}] ...`;

        setTimeout(() => {
            icon.className = 'bi bi-check-lg text-success';
            setTimeout(() => icon.className = originalClass, 1500);
        }, 800);
    }

    // =========================================================================
    // [v1.8] 版本選單與自動存檔
    // =========================================================================

    /**
     * 從版本檔名取出版本號，同時支援新舊兩種格式。
     *
     * 新格式：V0.4.json / V2.json
     * 舊格式：<title>_<yymmdd>_V1.0.json
     * 原本只用 split('_V') 解析，新格式沒有底線前綴，會全部顯示成 vUnknown。
     */
    _verFromFilename(filename) {
        const m = String(filename || '').match(/(?:^|_)[Vv](\d+(?:\.\d+)?)\.json$/);
        return m ? m[1] : '?';
    }

    /** 目前章節編輯區正在檢視的版本號（版本選單的值）；沒有則回 null。 */
    _currentSectionVer(section) {
        const sel = this.app.sectionVersion;
        if (!sel || !sel.value) return null;
        return sel.value;
    }

    /**
     * 把伺服器回傳的結構化版本清單畫進選單。
     * 顯示 from_ver 是刻意的：一整排 V0.1~V0.9 若看不出誰改自誰，回退就無從判斷。
     */
    renderSectionVersions(versions, selectedVer) {
        const sel = this.app.sectionVersion;
        if (!sel) return;
        sel.innerHTML = '';

        if (!versions || versions.length === 0) {
            sel.innerHTML = '<option value="">尚無版本</option>';
            return;
        }

        versions.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.ver;
            const from = v.from_ver ? ` ← ${v.from_ver}` : '';
            const who = v.updated_by ? ` · ${v.updated_by}` : '';
            opt.textContent = `V${v.ver}${from}${who}`;
            if (v.filename) opt.dataset.filename = v.filename;
            sel.appendChild(opt);
        });

        if (selectedVer) sel.value = selectedVer;
    }

    /** 主論文版本選單（整數 V1/V2，代表投稿候選稿）。 */
    renderPaperVersions(versions, selectedVer) {
        const sel = this.app.globalVersion;
        if (!sel) return;
        sel.innerHTML = '';

        if (!versions || versions.length === 0) {
            sel.innerHTML = '<option value="">尚無版本</option>';
            return;
        }

        versions.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.g_ver;
            const from = v.from_ver ? ` ← ${v.from_ver}` : '';
            const who = v.updated_by ? ` · ${v.updated_by}` : '';
            opt.textContent = `${v.g_ver}${from}${who}`;
            sel.appendChild(opt);
        });

        if (selectedVer) sel.value = selectedVer;
    }

    /**
     * 停筆 1.5 秒後自動存檔。
     *
     * 走 cmd_autosave_block 而非 cmd_save_block：自動存檔只覆寫單一草稿檔、
     * 不產生版本。若每次自動存檔都跳版，打一段字就會噴出數十個版本。
     */
    scheduleAutosave() {
        if (!this.app.pid) return;
        clearTimeout(this._autosaveTimer);
        this._autosaveTimer = setTimeout(() => this._flushAutosave(), 1500);

        const statusEl = document.getElementById('autosaveStatus');
        if (statusEl) statusEl.textContent = '編輯中…';
    }

    /**
     * 詢問是否復原未存檔草稿。
     *
     * 只在草稿確實比目前編輯區內容新、且尚未提示過時詢問，
     * 避免每次切換章節都彈一次。
     */
    _offerDraftRestore(section, draft) {
        // 只處理目前正在檢視的章節，避免切換章節時把畫布換成別章的草稿。
        if (!this.app.selectedSections.has(section)) return;

        this._draftPrompted = this._draftPrompted || {};
        if (this._draftPrompted[section]) return;
        this._draftPrompted[section] = true;

        const restore = () => {
            this.app.selectedSections = new Set([section]);
            this.app.ui.updateDropdownLabel();
            this.app.editorCanvas.innerHTML = '';
            this.insertEditorCard(draft.content, section);
            this.addSystemMessage(`已復原「${section}」的自動儲存草稿。按存檔可將它建立為正式版本。`);
        };

        // 初次進頁時畫布是空的，自己的 autosave 應直接回來；只有畫布已有內容時
        // 才詢問，避免跨章切換時覆蓋使用者眼前的工作。
        const hasEditorContent = Array.from(this.app.editorCanvas.querySelectorAll('.card-content'))
            .some(el => String(el.innerText || '').trim().length > 0);
        if (!hasEditorContent) {
            restore();
            return;
        }

        const when = draft._updated_at
            ? new Date(draft._updated_at).toLocaleString()
            : '稍早';
        if (!confirm(`章節「${section}」有未存檔的自動儲存草稿（${when}）。要復原嗎？\n\n按取消則保留目前內容，草稿會在下次存檔時被覆蓋。`)) {
            return;
        }

        restore();
    }

    _flushAutosave() {
        const cards = this.app.editorCanvas
            ? this.app.editorCanvas.querySelectorAll('.editor-card')
            : [];
        if (!cards.length) return;

        const title = this.app.paperTitleInput
            ? this.app.paperTitleInput.value.trim()
            : 'Untitled_Paper';

        cards.forEach(card => {
            const body = card.querySelector('.card-content');
            if (!body) return;
            this.app.socket.emit('cmd_autosave_block', {
                pid: this.app.pid,
                title: title,
                section: card.getAttribute('data-section') || 'general',
                content: body.innerHTML,
            });
        });
    }

    cardActionPushToFusion(btn) {
        const card = btn.closest('.editor-card');
        // [v0.6 修正] 改為 innerHTML，確保推入 2C 時圖片不會消失
        const content = card.querySelector('.card-content').innerHTML;
        const section = card.getAttribute('data-section') || 'general';
        const sectionLabel = this.app.sections.find(s => s.id === section)?.label || section;
        
        if(this.app.fusionCanvas) {
            // [v0.5 修正] 當推入實質內容時，自動打掃掉預設的佔位文字，保持畫面乾淨
            if (this.app.fusionCanvas.innerHTML.includes('來自 2B 的段落將會依序插入於此處')) {
                this.app.fusionCanvas.innerHTML = this.app.fusionCanvas.innerHTML.replace(/<p[^>]*>來自 2B 的段落將會依序插入於此處\.\.\.<\/p>/g, '');
                this.app.fusionCanvas.innerHTML = this.app.fusionCanvas.innerHTML.replace('來自 2B 的段落將會依序插入於此處...', '');
            }

            const fusionHtml = `
                <div class="fusion-block mb-4 border-start border-4 border-success ps-3 animate__animated animate__fadeInLeft">
                    <h5 class="text-success fw-bold"><i class="bi bi-check2-circle me-1"></i>${sectionLabel}</h5>
                    <div class="fusion-body">${content.replace(/\n/g, '<br>')}</div>
                </div>
            `;
            this.app.fusionCanvas.innerHTML += fusionHtml;
        }

        const icon = btn.querySelector('i');
        icon.classList.replace('bi-arrow-left-circle-fill', 'bi-check-circle-fill');
        icon.classList.replace('text-primary', 'text-success');
        setTimeout(() => {
            icon.classList.replace('bi-check-circle-fill', 'bi-arrow-left-circle-fill');
            icon.classList.replace('text-success', 'text-primary');
        }, 1200);
    }

    cardActionContext(btn) {
        const card = btn.closest('.editor-card');
        const content = card.querySelector('.card-content').innerText;
        const quote = `> Citated content: "${content.substring(0, 150)}..."\n\n`;
        this.app.chatInput.value = quote + this.app.chatInput.value;
        this.app.chatInput.focus();
        
        const icon = btn.querySelector('i');
        const originalClass = icon.className;
        icon.className = 'bi bi-arrow-right text-primary';
        setTimeout(() => icon.className = originalClass, 1000);
    }

    // =========================================================================
    // 畫布內容與字數統計
    // =========================================================================
    loadMultiSectionContent() {
        if (this.app.editorCanvas.innerText.trim().length < 10) {
            const activeId = Array.from(this.app.selectedSections)[0] || 'abstract';
            const label = this.app.sections.find(s => s.id === activeId)?.label || activeId;
            this.app.editorCanvas.innerHTML = `
                <div class="section-block mb-4" data-section="${activeId}">
                    <h2 class="text-primary border-bottom pb-2 h4">${label}</h2>
                    <div class="section-content"><p class="text-muted fst-italic">Awaiting content draft for ${label} section...</p></div>
                </div>`;
        }
        this.updateWordCount();
    }

    updateWordCount() {
        const text = this.app.editorCanvas.innerText || "";
        const count = text.trim().length === 0 ? 0 : text.trim().split(/\s+/).length;
        if(this.app.wordCountDisplay) this.app.wordCountDisplay.innerText = `Current Draft Words: ${count}`;
    }

    switchChatSection(sectionId) {
        if(!sectionId) return;
        this.rememberChatSection(sectionId);
        this.app.chatContainer.innerHTML = ''; 
        this.addSystemMessage(`Synchronizing context for section: [${sectionId}]...`);
        this.app.socket.emit('cmd_load_chat', { pid: this.app.pid, section: sectionId });
        // 同一個切章入口一併要求版本與自己的 autosave 草稿；舊版只載聊天，
        // 因而後端雖有 _draft__<user>.json，重新進頁仍永遠看不到。
        this.app.socket.emit('cmd_list_blocks', { pid: this.app.pid, section: sectionId });
    }

    _chatSectionStorageKey() {
        const userId = document.getElementById('currentUserId')?.value || 'anonymous';
        return `roothinks:manuscript:2a:last-section:${userId}:${this.app.pid}`;
    }

    rememberChatSection(sectionId) {
        try {
            window.localStorage.setItem(this._chatSectionStorageKey(), sectionId);
        } catch (_) {
            // localStorage 不可用時仍可由伺服器載入目前章節，不中斷 Manuscript。
        }
    }

    restoreChatSection(fallbackSection) {
        try {
            const cached = window.localStorage.getItem(this._chatSectionStorageKey());
            const exists = cached && Array.from(this.app.drafterTargetSection.options)
                .some((option) => option.value === cached);
            if (exists) {
                this.app.drafterTargetSection.value = cached;
                return cached;
            }
        } catch (_) {
            // ponytail: cache 只是定位提示；聊天正文仍以伺服器 JSON 為準。
        }
        return fallbackSection;
    }

    // =========================================================================
    // Socket 與 通訊事件處理
    // =========================================================================
    /**
     * 更新右上角連線徽章。
     *
     * 為什麼要有這個：徽章原本是寫死的 "Online"，socket 完全沒連上時仍是綠的。
     * CORS 擋掉 websocket 那次（128 次握手全 400），畫面上看起來一切正常，
     * 診斷因此往錯的方向走了一輪。訊號寧可難看，也不能說謊。
     */
    _setConnectionBadge(state, detail = '') {
        const el = document.getElementById('socketStatusBadge');
        if (!el) return;
        const map = {
            online: ['bg-success', 'Online', '已連線'],
            offline: ['bg-danger', 'Offline', '連線中斷'],
            connecting: ['bg-secondary', 'Connecting…', '尚未建立連線'],
        };
        const [cls, text, defaultTitle] = map[state] || map.connecting;
        el.className = `badge ${cls} border border-light me-1`;
        el.textContent = text;
        el.title = detail ? `${defaultTitle}：${detail}` : defaultTitle;
    }

    /**
     * 重新連線後把聊天紀錄重抓一次。
     *
     * 為什麼需要：草稿結果是 `socketio.emit(..., to=sid)` 送的，綁在單一 sid 上。
     * 中途只要斷線重連（sid 就換人了），那個結果就永遠送不到畫面 ——
     * 而伺服器端其實**已經成功**：`save_chat_history()` 在 emit 之前就跑完，
     * 日誌也有 `job_done`。正式站實測過這個落差：伺服器 4 個 job 全部
     * job_done（6～10 秒），使用者畫面卻停在「processing 25%」。
     *
     * 結論：不能只靠一次性的 live 事件。重連後重抓一次，草稿就會出現。
     * 第一次 connect 不做（switchChatSection 已經會載），只處理「重」連。
     */
    _resyncAfterReconnect() {
        if (!this._hasConnectedOnce) {
            this._hasConnectedOnce = true;
            return;
        }
        const section = this.app.drafterTargetSection
            ? this.app.drafterTargetSection.value
            : Array.from(this.app.selectedSections || [])[0];
        if (!section || !this.app.pid) return;

        // 轉圈是綁在舊 sid 的那個請求留下的，重連後不會再有人來收，先收掉。
        if (document.getElementById('typingIndicator')) {
            this._clearAckTimeout();
            this._clearTypingTimeout();
            this.activeJobId = null;
            this.removeTypingIndicator();
            this.addSystemMessage('連線中斷過，正在重新同步這一章的紀錄；先前的草稿若已產生會出現在下方。');
        }
        this.app.socket.emit('cmd_load_chat', { pid: this.app.pid, section: section });
    }

    setupSocketEvents() {
        // socket 在 manuscript_ws.js 就建立了，往往在本函式註冊 handler **之前**
        // 就已經連上 —— 那個 'connect' 事件不會再補送一次，徽章於是永遠停在
        // Connecting…（瀏覽器實測：socket.connected 為 true、console 沒有任何
        // [Socket] Connection established，徽章仍是灰的）。
        // 事件只負責「之後的變化」，當下狀態必須自己讀。
        this._setConnectionBadge(this.app.socket.connected ? 'online' : 'connecting');
        // 同一個理由：socket 可能在這裡之前就連上了，那個 'connect' 不會再來。
        // 若不用當下狀態初始化，第一次真正的「重連」會被 _resyncAfterReconnect
        // 誤認成初次連線而跳過 —— 實測就是這樣，重連後完全沒有重抓紀錄。
        this._hasConnectedOnce = this.app.socket.connected;

        this.app.socket.on('connect', () => {
            console.log("[Socket] Connection established successfully.");
            this._setConnectionBadge('online');
            this.addSystemMessage("Drafter Server Connection: Active.");
            this._resyncAfterReconnect();
        });

        // 連不上（CORS 被拒、伺服器沒起來）時 socket.io 只會不斷重試，
        // 沒有這個 handler 的話畫面上不會有任何跡象。
        this.app.socket.on('connect_error', (err) => {
            const reason = (err && err.message) ? err.message : 'unknown error';
            this._setConnectionBadge('offline', reason);
            console.error('[Socket] connect_error:', reason);
        });

        this.app.socket.on('disconnect', (reason) => {
            this._setConnectionBadge('offline', reason || '');
            this.addSystemMessage("Drafter Server disconnected. Reconnecting...");
            // 連線斷掉後 sid 就換人了，伺服器 _sid_connected(sid) 會判定失聯而
            // 靜默結束該 job（manuscript_routes.py:426/467）——沒有任何事件會回來。
            // 這裡不收掉轉圈的話，重連後就是一個永遠不會結束的指示器。
            if (document.getElementById('typingIndicator')) {
                this._failPendingRequest('Connection lost while the request was running. Please retry.');
            }
        });

        // [presence] 在線協作者。伺服器只送「誰在線上」，不含誰在改哪一章。
        this.app.socket.on('presence_update', (data) => {
            if (this.app.collab) this.app.collab.renderPresence(data);
        });

        this.app.socket.on('sys_msg', (data) => {
            this.addSystemMessage(data.msg);
            // 伺服器在驗證失敗時只回 sys_msg、不會有 job_queued（例如訊息過長、
            // pid 解析失敗、無專案權限）。此時若還在等 ack，這就是最終結果，
            // 轉圈必須立刻收掉，否則使用者會以為還在生成。
            if (!this.activeJobId && document.getElementById('typingIndicator')) {
                this._failPendingRequest('Drafter rejected the request. Please revise and retry.', false);
            }
            // [collab] 權限是別人（owner）可以隨時改的，前端的 permissions 是快取。
            // 一旦伺服器以權限為由拒絕，立刻重抓權限並重新上鎖 ——
            // 否則畫布仍是可編輯狀態，使用者會一直打字到下次存檔才發現白打。
            if (data && typeof data.msg === 'string' && data.msg.includes('權限不足')) {
                if (this.app.collab) this.app.collab.loadPermissions();
            }
        });
        this.app.socket.on('ai_response', (data) => this.handleAIResponse(data));
        this.app.socket.on('job_queued', (data) => {
            this._clearAckTimeout();
            this.requestPending = false;
            this.activeJobId = data.job_id;
            this._setJobControls(true, true);
            this.updateTypingIndicator(`Job queued (${data.job_id}). Waiting worker...`);
            this._armTypingTimeout();
        });
        this.app.socket.on('job_progress', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            const msg = data.message || data.stage || 'processing';
            const pct = typeof data.progress === 'number' ? ` ${data.progress}%` : '';
            this.updateTypingIndicator(`AI ${msg}${pct}`);
        });
        this.app.socket.on('job_done', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            this._clearAckTimeout();
            this._clearTypingTimeout();
            this.requestPending = false;
            this.activeJobId = null;
            this._setJobControls(false);
            this.removeTypingIndicator();
            if (data && typeof data.latency_ms === 'number') {
                this.addSystemMessage(`Job completed in ${(data.latency_ms / 1000).toFixed(1)}s`);
            }
        });
        this.app.socket.on('job_error', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            this._clearAckTimeout();
            this._clearTypingTimeout();
            this.requestPending = false;
            this.activeJobId = null;
            this._setJobControls(false);
            this.removeTypingIndicator();
            this.addSystemMessage(`Job error: ${data.message || 'unknown error'}`);
        });
        this.app.socket.on('job_cancelled', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            this._clearAckTimeout();
            this._clearTypingTimeout();
            this.requestPending = false;
            this.activeJobId = null;
            this._setJobControls(false);
            this.removeTypingIndicator();
            this.addSystemMessage(`Job cancelled: ${data.job_id}`);
        });
        
        this.app.socket.on('save_ack', (data) => {
            if (data.target === 'paper') {
                this.app.ui.flashButtonSuccess(this.app.btnSaveGlobal);
                // [v1.8] 伺服器指派的主論文版號回來後才更新選單。
                this.renderPaperVersions(data.versions, data.g_ver);
                if (this.app.pendingOpenPaperModal) {
                    this.app.pendingOpenPaperModal = false;
                    const title = this.app.paperTitleInput.value.trim() || "Untitled_Paper";
                    this._wantPaperModal = true;   // 使用者主動要看舊版，允許彈窗
                    this.app.socket.emit('cmd_list_papers', { pid: this.app.pid, title: title });
                }
            } else if (data.target === 'block') {
                if(this.app.saveStatus) {
                    this.app.saveStatus.innerText = data.msg + " @ " + new Date().toLocaleTimeString();
                }
                // [v1.8] 版號由伺服器決定，存檔完成才知道實際號碼。
                this.renderSectionVersions(data.versions, data.ver);
                const statusEl = document.getElementById('autosaveStatus');
                if (statusEl) statusEl.textContent = `已存為 V${data.ver}`;

                if (this.app.pendingOpenBlockModal) {
                    this.app.pendingOpenBlockModal = false;
                    const sec = Array.from(this.app.selectedSections)[0] || 'abstract';
                    this.fetchOldBlocks(sec);
                    bootstrap.Modal.getOrCreateInstance(document.getElementById('oldBlockModal')).show();
                }
            }
        });

        // [v1.8] 自動存檔回應：只更新狀態文字，不動版本選單（草稿不是版本）。
        this.app.socket.on('autosave_ack', (data) => {
            const statusEl = document.getElementById('autosaveStatus');
            if (!statusEl) return;
            if (data && data.ok) {
                const t = data.saved_at ? new Date(data.saved_at) : new Date();
                statusEl.textContent = `已自動儲存 ${t.toLocaleTimeString()}`;
                statusEl.className = 'small text-muted ms-1';
            } else {
                statusEl.textContent = '自動儲存失敗';
                statusEl.className = 'small text-danger ms-1';
            }
        });

        // [v1.8] 刪除版本後刷新選單。
        this.app.socket.on('block_version_deleted', (data) => {
            if (!data) return;
            if (!data.ok) {
                this.addSystemMessage('刪除版本失敗：找不到該版本或權限不足。');
                return;
            }
            this.renderSectionVersions(data.versions, null);
            this.addSystemMessage(`已刪除版本 V${data.ver}。`);
        });

        // [v1.8] 主論文版本還原：一併把各章節編輯區回到當時的版本。
        this.app.socket.on('paper_restored', (data) => {
            if (!data || !data.ok) {
                this.addSystemMessage((data && data.msg) || '還原失敗。');
                return;
            }
            if (this.app.fusionCanvas) this.app.fusionCanvas.innerHTML = data.content || '';
            const count = Object.keys(data.sections || {}).length;
            let msg = `已還原主論文 ${data.g_ver}，含 ${count} 個章節版本。`;
            if (data.missing && data.missing.length) {
                msg += ` 有 ${data.missing.length} 個章節版本已被刪除而無法還原：${data.missing.join(', ')}`;
            }
            this.addSystemMessage(msg);
        });

        this.app.socket.on('chat_history', (data) => {
            // 快速切章時舊請求可能晚到；不得讓舊章回應清空目前章節的紀錄。
            if (this.app.drafterTargetSection
                && data.section !== this.app.drafterTargetSection.value) return;
            this.app.chatContainer.innerHTML = '';
            this.addSystemMessage(`Syncing history for section: ${data.section}`);
            if(data.history && data.history.length > 0) {
                data.history.forEach(msg => {
                    if(msg.type === 'draft') {
                        if(msg.chat_msg) this.addBubble('ai', msg.chat_msg);
                        this.addDraftCard(msg.content);
                    }
                    else this.addBubble(msg.role, msg.content);
                });
            } else {
                this.addSystemMessage("No previous chat threads found here.");
            }
            this.scrollToBottom();
        });

        this.app.socket.on('paper_list', (data) => {
            // [v1.8] 同步 G.Ver 版本選單。原本只有存檔成功（save_ack）才會填，
            // 導致重新進頁面時既有的主論文版本完全看不到，也就無法用選單還原。
            this.renderPaperVersions(data.versions, null);

            // 初次進頁自動還原最新 2C 主論文；後續 list 事件（例如剛存檔）不重載，
            // 避免把目前正在編輯的內容蓋回磁碟版本。
            if (!this._initialPaperHydrated) {
                this._initialPaperHydrated = true;
                const currentText = this.app.fusionCanvas
                    ? this.app.fusionCanvas.innerText.replace('來自 2B 的段落將會依序插入於此處...', '').trim()
                    : '';
                if (!currentText && data.files && data.files.length > 0) {
                    this.app.socket.emit('cmd_load_paper', {pid: this.app.pid, filename: data.files[0]});
                }
            }

            const container = document.getElementById('oldPaperListContainer');
            container.innerHTML = '';
            if (data.files && data.files.length > 0) {
                data.files.forEach(file => {
                    const a = document.createElement('a');
                    a.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
                    a.href = "#";
                    a.innerHTML = `<span class="fw-bold">${file}</span> `
                                + `<span class="badge bg-primary rounded-pill">V${this._verFromFilename(file)}</span>`;
                    a.onclick = (e) => {
                        e.preventDefault();
                        this.app.socket.emit('cmd_load_paper', { pid: this.app.pid, filename: file });
                        bootstrap.Modal.getOrCreateInstance(document.getElementById('oldPaperModal')).hide();
                    };
                    container.appendChild(a);
                });
            } else {
                container.innerHTML = '<div class="text-center text-muted p-3">查無舊檔案。</div>';
            }

            // [v1.8] 只有使用者主動按「開啟舊版」時才彈 modal。
            // 頁面載入時也會發一次 cmd_list_papers 來填 G.Ver 選單，
            // 若無條件 show()，每次進手稿頁都會被這個彈窗攔住。
            if (this._wantPaperModal) {
                this._wantPaperModal = false;
                bootstrap.Modal.getOrCreateInstance(document.getElementById('oldPaperModal')).show();
            }
        });

        this.app.socket.on('paper_loaded', (data) => {
            if(data.ok && this.app.fusionCanvas) {
                this.app.fusionCanvas.innerHTML = data.content.content || '';
                // [v1.8] g_ver 現在是 'V2' 這種整數版；選單是 select，直接設值即可。
                if (this.app.globalVersion && data.content.g_ver) {
                    this.app.globalVersion.value = data.content.g_ver;
                }
                this.app.lastSavedGVer = data.content.g_ver || '';
                this.addSystemMessage(`已成功還原 2C 全文版本: ${data.filename}`);
            }
        });

        this.app.socket.on('block_list', (data) => {
            // [collab] 伺服器判定這一章不可讀時會帶 forbidden；
            // 代表權限在本次工作階段中被改動過，重抓權限讓 UI 跟上。
            if (data && data.forbidden && this.app.collab) {
                this.app.collab.loadPermissions();
            }
            // [v1.8] 同步版本選單（結構化清單，含 from_ver / 作者）。
            this.renderSectionVersions(data.versions, null);

            // [v1.8] 有草稿代表上次離開時有未存檔內容，主動詢問是否復原。
            if (data.draft && data.draft.content) {
                this._offerDraftRestore(data.section, data.draft);
            }

            const container = document.getElementById('oldBlockListContainer');
            container.innerHTML = '';
            if (data.files && data.files.length > 0) {
                data.files.forEach(file => {
                    const vTag = this._verFromFilename(file);
                    const a = document.createElement('a');
                    a.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
                    a.href = "#";
                    a.innerHTML = `<span class="fw-bold">${file}</span> <span class="badge bg-info rounded-pill">v${vTag}</span>`;
                    a.onclick = (e) => {
                        e.preventDefault();
                        this.app.socket.emit('cmd_load_block', { pid: this.app.pid, section: data.section, filename: file });
                        bootstrap.Modal.getOrCreateInstance(document.getElementById('oldBlockModal')).hide();
                    };
                    container.appendChild(a);
                });
            } else {
                container.innerHTML = `
                    <div class="text-center text-muted p-4">
                        <i class="bi bi-folder-x fs-1 text-secondary mb-2"></i><br>
                        查無舊檔案。<br>
                        <button class="btn btn-sm btn-outline-primary mt-3" onclick="wsApp.createNewSection('${data.section}')">
                            <i class="bi bi-file-earmark-plus me-1"></i>建立空白新草稿
                        </button>
                    </div>`;
            }
        });

        this.app.socket.on('block_loaded', (data) => {
            if(data.ok && this.app.editorCanvas) {
                this.app.selectedSections = new Set([data.section]);
                this.app.ui.updateDropdownLabel();
                this.app.editorCanvas.innerHTML = '';

                this.insertEditorCard(data.content.content, data.section);
                // [v1.8] 版號優先讀 ver 欄位；舊檔沒有時退回 version（帶 V 前綴）。
                const vStr = data.content.ver
                    || String(data.content.version || '').replace(/^[Vv]/, '');
                if (this.app.sectionVersion) this.app.sectionVersion.value = vStr;
                this.app.lastSavedSVer[data.section] = vStr;
                this.addSystemMessage(`已載入 2B 段落版本 V${vStr}。存檔會建立新版，此版保留不動。`);
            }
        });
    }

    createNewSection(secId) {
        this.app.selectedSections = new Set([secId]);
        this.app.ui.updateDropdownLabel();
        this.app.editorCanvas.innerHTML = '';
        // 新草稿沒有來源版本；存檔時 from_ver 送 null，伺服器指派 max+0.1。
        if (this.app.sectionVersion) this.app.sectionVersion.value = '';
        this.app.lastSavedSVer[secId] = '';
        this.loadMultiSectionContent();
        
        const modalEl = document.getElementById('oldBlockModal');
        if (modalEl) {
            const instance = bootstrap.Modal.getOrCreateInstance(modalEl);
            if(instance) instance.hide();
        }
    }

    // [v0.5 修復] 開啟舊檔流程 (2C) - 升級判斷邏輯
    openOldPaperFlow() {
        const rawText = this.app.fusionCanvas.innerText.trim();
        // 剔除佔位文字後計算真實長度
        const cleanText = rawText.replace(/來自 2B 的段落將會依序插入於此處\.\.\./g, '').trim();
        
        if (cleanText.length < 10) {
            const title = this.app.paperTitleInput.value.trim() || "Untitled_Paper";
            this._wantPaperModal = true;   // 使用者按了「開啟舊版論文」
            this.app.socket.emit('cmd_list_papers', { pid: this.app.pid, title: title });
            return;
        }

        if(confirm("即將開啟舊版檔案！系統會先自動將目前 2C 的內容備份為新版本。確定繼續嗎？")) {
            this.app.pendingOpenPaperModal = true;
            // [v1.8] 不再需要重設 lastSavedGVer 來逼出版號遞增——
            // 版號由伺服器指派，每次存檔一定產生新版。
            this.app.btnSaveGlobal.click();
        }
    }

    openOldBlockFlow() {
        const cards = this.app.editorCanvas.querySelectorAll('.editor-card');
        const rawText = this.app.editorCanvas.innerText.trim();
        
        if (cards.length === 0 || rawText.includes("Awaiting content draft for") || rawText.includes("Select a section above")) {
            this.renderOldBlockSelect();
            this.fetchOldBlocks();
            bootstrap.Modal.getOrCreateInstance(document.getElementById('oldBlockModal')).show();
            return;
        }

        if(confirm("即將開啟舊版檔案(或切換章節)！系統會先自動將目前 2B 畫布上的內容強制備份 (S.Ver + 1.0)。確定繼續嗎？")) {
            this.app.pendingOpenBlockModal = true;
            this.saveAllBlocks();
            this.renderOldBlockSelect();
        }
    }

    renderOldBlockSelect() {
        const sel = document.getElementById('oldBlockSectionSelect');
        sel.innerHTML = '';
        this.app.sections.forEach(sec => {
            const opt = document.createElement('option');
            opt.value = sec.id;
            opt.innerText = sec.label;
            sel.appendChild(opt);
        });
        const currentActive = Array.from(this.app.selectedSections)[0] || 'abstract';
        sel.value = currentActive;
    }

    fetchOldBlocks(targetSec = null) {
        const sec = targetSec || document.getElementById('oldBlockSectionSelect').value;
        this.app.socket.emit('cmd_list_blocks', { pid: this.app.pid, section: sec });
    }

    // =========================================================================
    // UI 事件綁定 - 【v0.5 核心修復：防堵空畫布強制存檔跳號】
    /**
     * 把手稿 Title 寫回專案（name 與 research_title 由後端同步）。
     *
     * 走既有的 POST /api/project/update/<pid>，刻意不另開 socket 端點：
     * 那個端點已有 enforce_project_ownership（POST 需 editor 以上），
     * 自建一條等於繞過它。
     */
    async savePaperTitle() {
        const input = this.app.paperTitleInput;
        if (!input || !this.app.pid) return;
        const title = (input.value || '').trim();
        if (!title) {
            this.addSystemMessage('標題不可為空白，未寫入。');
            return;
        }
        if (title === this._lastSavedTitle) return;

        try {
            const res = await fetch(`/api/project/update/${encodeURIComponent(this.app.pid)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: title }),
            });
            const result = await res.json().catch(() => ({}));
            // 失敗一定要講原因。前端無聲失敗是這套 UI 的通病 —— 只把畫面重畫回
            // 舊值，使用者拿不到任何理由，然後以為是「系統沒存成功」。
            if (!res.ok || !result.success) {
                this.addSystemMessage(
                    `標題儲存失敗（${result.message || 'HTTP ' + res.status}）。重整後會回到舊標題。`
                );
                return;
            }
            this._lastSavedTitle = title;
            this.addSystemMessage(`論文標題已更新為「${title}」，專案題目一併更新。`);
        } catch (err) {
            this.addSystemMessage(`標題儲存失敗（${err.message}）。重整後會回到舊標題。`);
        }
    }

    // =========================================================================
    setupUIEvents() {
        this.app.btnSend.onclick = () => this.sendUserMessage();
        if (this.app.btnCancelJob) {
            this.app.btnCancelJob.onclick = () => this.cancelActiveJob();
        }
        this.app.chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                e.preventDefault();
                this.sendUserMessage();
            }
        });
        this.app.chatInput.addEventListener('input', () => this._resizeChatInput());
        this._setJobControls(false);
        this.app.fileInput.addEventListener('change', (e) => this.app.ui.handleFileSelect(e));
        if (this.app.wordImportInput) {
            this.app.wordImportInput.addEventListener('change', (e) => this.app.ui.handleWordImportFileSelect(e));
        }
        if (this.app.drafterTargetSection) {
            this.app.drafterTargetSection.addEventListener('change', (e) => {
                this.switchChatSection(e.target.value);
            });
        }
        
        // 手稿 Title 就是專案題目（擁有者決策：專案名稱即研究題目，兩欄永遠相等）。
        // 在這之前 Title 只會被寫進版本檔的 payload，沒有任何程式碼把它讀回輸入框
        // ——頁面一律以 research_title 重新填值，所以「改了、重整就變回舊的」。
        // 寫回專案是唯一能讓它在重整後留存的路徑，也讓儀表板卡片與所有 LLM task
        // 立刻跟上（它們讀的都是 research_title or name）。
        if (this.app.paperTitleInput) {
            this._lastSavedTitle = (this.app.paperTitleInput.value || '').trim();
            this.app.paperTitleInput.addEventListener('change', () => this.savePaperTitle());
        }

        if(this.app.btnSaveGlobal) {
            this.app.btnSaveGlobal.onclick = () => {
                const rawText = this.app.fusionCanvas ? this.app.fusionCanvas.innerText.trim() : "";
                
                // [v0.5 嚴格防呆] 剔除系統預設字串，確保只計算真正的文章內容
                const cleanText = rawText.replace(/來自 2B 的段落將會依序插入於此處\.\.\./g, '').trim();
                
                if (cleanText.length < 10) {
                    alert("畫布中沒有實質內容，無法執行存檔！");
                    return; // 強制中斷，不允許跳號存檔
                }

                const title = this.app.paperTitleInput.value.trim() || "Untitled_Paper";
                const fusionContent = this.app.fusionCanvas ? this.app.fusionCanvas.innerHTML : '';
                // [v1.8] 主論文版號改由伺服器指派為整數 V1/V2；前端只回報來源版本。
                // sections manifest 由伺服器蒐集各章節目前最新版，確保回退時拿得到組成。
                this.app.socket.emit('cmd_save_paper', {
                    pid: this.app.pid,
                    title: title,
                    content: fusionContent,
                    from_ver: this.app.globalVersion ? (this.app.globalVersion.value || null) : null,
                });
                this.app.btnSaveGlobal.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
            };
        }

        // [v1.8] 章節版本選單：選取即載入該版內容到編輯區。
        // 之後按存檔會建立 max+0.1 的新版，被選的舊版原檔不受影響。
        if (this.app.sectionVersion) {
            this.app.sectionVersion.addEventListener('change', (e) => {
                const opt = e.target.selectedOptions[0];
                if (!opt || !opt.value || !opt.dataset.filename) return;
                const section = Array.from(this.app.selectedSections)[0];
                if (!section) return;
                this.app.socket.emit('cmd_load_block', {
                    pid: this.app.pid,
                    section: section,
                    filename: opt.dataset.filename,
                });
            });
        }

        // [v1.8] 主論文版本選單：選取即依 manifest 還原全文與各章節組合。
        if (this.app.globalVersion) {
            this.app.globalVersion.addEventListener('change', (e) => {
                const ver = e.target.value;
                if (!ver) return;
                if (!confirm(`要還原主論文 ${ver} 嗎？\n\n目前 2C 畫布的內容會被取代（尚未存檔的變更會遺失）。`)) {
                    return;
                }
                this.app.socket.emit('cmd_restore_paper_version', {
                    pid: this.app.pid,
                    ver: ver,
                });
            });
        }
    }


    // =========================================================================
    // AI 聊天與意圖處理 
    // =========================================================================
    sendUserMessage() {
        const txt = this.app.chatInput.value.trim();
        if(!txt && !this.app.currentAttachment) return;
        if (this.activeJobId || this.requestPending) {
            this.addSystemMessage('A job is still running. Please wait or cancel it first.');
            return;
        }
        let targetLang = this.app.targetLangSelect ? this.app.targetLangSelect.value : "English";
        const targetSection = this.app.drafterTargetSection.value; 
        let displayPrompt = txt;
        if (this.app.currentAttachment) { displayPrompt += `<br><small class="text-warning"><i class="bi bi-paperclip"></i> 附加檔案: ${this.app.currentAttachment.name}</small>`; }
        this.addBubble('user', displayPrompt);
        const payload = { msg: txt, context: this.app.editorCanvas.innerText.substring(0,3000), target_lang: targetLang, attachment: this.app.currentAttachment, import_type: this.app.currentImportType, pid: this.app.pid, title: this.app.paperTitleInput.value.trim(), section: targetSection, s_ver: '0.1' };
        this.app.socket.emit('chat_message', payload);
        this.requestPending = true;
        this._setJobControls(true, false);
        this.app.chatInput.value = '';
        this._resizeChatInput();
        this.app.ui.clearFile(); 
        this.showTypingIndicator();
        this._armTypingTimeout();
        this._armAckTimeout();
    }

    triggerAutoDraft() {
        if (this.activeJobId || this.requestPending) {
            this.addSystemMessage('A job is still running. Please wait or cancel it first.');
            return;
        }
        const targetSection = this.app.drafterTargetSection.value;
        const targetLabel = this.app.sections.find(s => s.id === targetSection)?.label || targetSection;
        let targetLang = this.app.targetLangSelect ? this.app.targetLangSelect.value : "English";
        const currentTitle = this.app.paperTitleInput.value.trim() || 'Untitled';
        const refineMsg = `請撰寫草稿：一篇論文 ${targetLabel}，title是『${currentTitle}』，${targetLabel}字數300字`;
        this.addBubble('user', `[Auto Draft Command] <br><span class="text-info">${refineMsg}</span>`);
        this.app.socket.emit('chat_message', { msg: refineMsg, context: this.app.editorCanvas.innerText.substring(0,3000), target_lang: targetLang, pid: this.app.pid, title: currentTitle, section: targetSection, s_ver: '0.1' });
        this.requestPending = true;
        this._setJobControls(true, false);
        this.showTypingIndicator();
        this._armTypingTimeout();
        this._armAckTimeout();
    }
    
    handleAIResponse(d) {
        try {
            this.removeTypingIndicator();
            if(d.type === 'draft') {
                if(d.chat_msg) this.addBubble('ai', d.chat_msg);
                this.addDraftCard(d.content);
            } else if(d.type==='text') {
                this.addBubble('ai', d.content);
            } else if(d.type === 'image_mermaid') {
                // [v0.6 核心修復] 攔截 mermaid 語法，移交給獨立的 ManuImage 引擎處理前端渲染
                if (this.app.imgEngine) {
                    this.app.imgEngine.renderMermaidToChat(d.content);
                } else {
                    this.addCard(d.type, d.content, `Generated Graphic (Engine Missing)`);
                }
            } else if(d.type === 'sheet') {
                this.addCard(d.type, d.content, `Generated ${d.type}`);
            } else if(d.type==='error') {
                this.addSystemMessage(`Process Aborted: ${d.content}`);
            } else {
                this.addBubble('ai', typeof d.content === 'object' ? JSON.stringify(d.content) : d.content);
            }
        } catch (err) {
            console.error("處理 AI 回應時發生錯誤:", err);
            this.addSystemMessage("系統處理回應時發生異常錯誤。");
        } finally {
            this._clearAckTimeout();
            this._clearTypingTimeout();
            this.requestPending = false;
            this.activeJobId = null;
            this._setJobControls(false);
            this.removeTypingIndicator();
        }
    }

    cancelActiveJob() {
        if (!this.activeJobId) {
            this.addSystemMessage('No active job to cancel.');
            return;
        }
        this.app.socket.emit('cmd_cancel_job', { job_id: this.activeJobId });
        this._setJobControls(true, false);
        this.updateTypingIndicator('正在中止 Drafter 工作…');
    }
    
    addBubble(role, text) {
        const div = document.createElement('div'); 
        div.className = `message-bubble ${role}`;
        div.innerHTML = `<div class="bubble-content shadow-sm"><div class="chat-conversational-body">${text.replace(/\n/g, '<br>')}</div></div>`;
        this.app.chatContainer.appendChild(div);
        this.scrollToBottom();
    }

    addDraftCard(content) {
        const div = document.createElement('div'); 
        div.className = `message-bubble ai animate__animated animate__fadeIn`;
        const targetBodyId = 'academic_body_' + Math.random().toString(36).substr(2, 6);
        div.innerHTML = `<div class="bubble-content draft-card-wrapper shadow"><button class="btn btn-primary draft-card-btn" title="將此段落推入 2B 編輯區" onclick="wsApp.importToEditor(document.getElementById('${targetBodyId}').innerHTML)"><i class="bi bi-arrow-left-circle-fill fs-5"></i></button><div class="draft-card-text academic-content" id="${targetBodyId}">${content.replace(/\n/g, '<br>')}</div></div>`;
        this.app.chatContainer.appendChild(div);
        this.scrollToBottom();
    }
    
    addCard(type, data, label) { 
        // [保留行數防呆] 就算 image_mermaid 已經被上面的 imgEngine 接管，此處仍保留原結構確保程式碼行數不減
        let displayHtml = `<strong class="text-primary">${label}</strong><br>`;
        if (type === 'image_mermaid') { displayHtml += `<pre class="bg-dark text-light p-2 rounded mt-2" style="font-size: 0.8em;">${data}</pre>`; } 
        else if (type === 'sheet') { displayHtml += `<pre class="bg-light border p-2 rounded mt-2" style="font-size: 0.8em; overflow-x:auto;">${JSON.stringify(data, null, 2)}</pre>`; } 
        else { displayHtml += data; }
        this.addBubble('ai', displayHtml); 
    }

    scrollToBottom() { this.app.chatContainer.scrollTop = this.app.chatContainer.scrollHeight; }

    addSystemMessage(t) { 
        const d = document.createElement('div');
        d.className = "text-center text-muted small my-3 font-monospace animate__animated animate__fadeIn";
        d.innerHTML = `<i class="bi bi-cpu me-1"></i> ${t}`; 
        this.app.chatContainer.appendChild(d); 
        this.scrollToBottom(); 
    }

    showTypingIndicator() { 
        if(document.getElementById('typingIndicator')) return;
        const d = document.createElement('div');
        d.id = 'typingIndicator';
        d.className = 'message-bubble ai';
        d.innerHTML = `<div class="bubble-content text-muted"><span class="spinner-grow spinner-grow-sm me-1"></span> AI is analyzing and generating...</div>`;
        this.app.chatContainer.appendChild(d); 
        this.scrollToBottom(); 
    }

    updateTypingIndicator(msg) {
        const el = document.getElementById('typingIndicator');
        if (!el) {
            this.showTypingIndicator();
            return;
        }
        el.innerHTML = `<div class="bubble-content text-muted"><span class="spinner-grow spinner-grow-sm me-1"></span> ${msg}</div>`;
    }

    removeTypingIndicator() { 
        const el = document.getElementById('typingIndicator');
        if(el) el.remove(); 
    }

    _armTypingTimeout() {
        this._clearTypingTimeout();
        this.typingTimeoutHandle = setTimeout(() => {
            // [v1.1] 原本這裡是 `if (!this.activeJobId) return;`，
            // 意思是「沒有 job 在跑就不用提醒」——但那正是最該提醒的情況：
            // 沒有 activeJobId 代表 job_queued 從沒到過，也就沒有任何
            // job_done/job_error 會來收掉轉圈指示器。那個 return 把唯一的
            // 逃生口關掉了，實測可以無聲轉三小時。
            if (!this.activeJobId) {
                this._failPendingRequest('Server did not acknowledge the request. Please retry.');
                return;
            }
            this.addSystemMessage('Request is taking too long. You can wait or cancel and retry.');
        }, this.typingWarnMs);
    }

    _clearTypingTimeout() {
        if (this.typingTimeoutHandle) {
            clearTimeout(this.typingTimeoutHandle);
            this.typingTimeoutHandle = null;
        }
    }

    // [v1.1] 等待伺服器 ack 的硬性時限。
    // 伺服器契約（manuscript_routes.py v1.8）：chat_message 通過驗證必定回 job_queued，
    // 未通過必定回 sys_msg。超過 ackWaitMs 兩者都沒來，就是連線或 handler 出事了，
    // 此時不能繼續轉圈假裝在運算。
    _armAckTimeout() {
        this._clearAckTimeout();
        this.ackTimeoutHandle = setTimeout(() => {
            if (this.activeJobId) return;
            this._failPendingRequest('No response from Drafter server (timed out waiting for job ack). Please retry.');
        }, this.ackWaitMs);
    }

    _clearAckTimeout() {
        if (this.ackTimeoutHandle) {
            clearTimeout(this.ackTimeoutHandle);
            this.ackTimeoutHandle = null;
        }
    }

    // 收掉轉圈並回到可再送出的狀態。任何「請求已經死了」的路徑都要走這裡，
    // 否則 activeJobId 會卡住，sendUserMessage 會一直擋在 'A job is still running'。
    _failPendingRequest(msg, addMessage = true) {
        this._clearAckTimeout();
        this._clearTypingTimeout();
        this.requestPending = false;
        this.activeJobId = null;
        this._setJobControls(false);
        this.removeTypingIndicator();
        if (addMessage) this.addSystemMessage(msg);
    }

    _setJobControls(running, canCancel = false) {
        if (this.app.btnSend) {
            this.app.btnSend.disabled = running;
            this.app.btnSend.classList.toggle('d-none', running);
        }
        if (this.app.btnCancelJob) {
            this.app.btnCancelJob.classList.toggle('d-none', !running);
            this.app.btnCancelJob.disabled = !canCancel;
        }
    }

    _resizeChatInput() {
        const input = this.app.chatInput;
        if (!input) return;
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
    }
}

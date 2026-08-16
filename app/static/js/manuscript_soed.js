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
        // [v2.0] 切章請求序號與目前生效的請求 { token, section }。
        // 切章是「先要版本清單、再載入版本」的兩段式非同步流程，中途使用者
        // 可以再切好幾次；沒有序號就無法辨識遲到的回應屬於哪一次切換。
        this._sectionReqSeq = 0;
        this._activeSectionReq = null;
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
        // [v2.0] 使用者主動把 2A 草稿放進畫布：作廢在途切章回應，
        // 否則稍晚到達的 block_loaded 會把剛複製過來的草稿換成舊版本。
        this._cancelPendingSectionSwitch();
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

    /**
     * 插入點是否落在畫布內的 <li> 中（含巢狀清單）。
     * 用來把 indent/outdent 限制在清單情境，理由見 formatText。
     */
    _selectionInListItem(canvas) {
        const selection = window.getSelection();
        if (!canvas || !selection || selection.rangeCount === 0) return false;
        let node = selection.getRangeAt(0).commonAncestorContainer;
        if (node && node.nodeType === 3) node = node.parentNode;   // TEXT_NODE
        while (node && node !== canvas) {
            if (node.nodeName === 'LI') return true;
            node = node.parentNode;
        }
        return false;
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
        } else if (command === 'orderedList') {
            cmd = 'insertOrderedList';
        } else if (command === 'indent' || command === 'outdent') {
            // [v2.0] 清單層級：execCommand('indent') 在清單項目上會產生巢狀
            // <ol>/<ul>（正是「第 2、3 層」），但在一般段落上產生的是
            // <blockquote> —— 整段內縮加一條左邊界線，完全不是使用者要的東西，
            // 而且退不回去（outdent 對 blockquote 的行為各瀏覽器不一致）。
            // 所以插入點不在 <li> 內時直接拒絕並說明原因，不要靜靜做錯的事。
            if (!this._selectionInListItem(canvas)) {
                this.addSystemMessage('請先把游標放在清單項目內，再調整清單層級。');
                return;
            }
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
            // [v2.0] 空卡不存。空白新章也是一張正常的卡片，不濾掉的話
            // 「切章前強制備份」會替沒寫過半個字的章節生出一個空版本。
            const body = card.querySelector('.card-content');
            if (!body || String(body.innerText || '').trim().length === 0) return;
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

        // [v2.0] 禁止跨章存檔。存檔目標一律取自卡片本身的 data-section，
        // 所以只要畫布上殘留別章的卡片，按存檔就會替「那一章」建立新版本，
        // 而使用者看到的章節選單卻是另一章 —— 存進去的東西與他以為的不同。
        // 切章隔離修好後這種卡片不該存在，這裡是最後一道防線。
        const current = this._currentSectionId();
        if (current && section !== current) {
            this.addSystemMessage(
                `已阻止跨章存檔：卡片屬於「${this._sectionLabel(section)}」，目前章節是「${this._sectionLabel(current)}」。請重新切換章節後再試。`
            );
            return;
        }

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
        // NOTE(NOTE-023) 程式化渲染畫布不是「使用者在編輯」，不得寫草稿。
        if (this._programmaticRender) return;
        clearTimeout(this._autosaveTimer);
        this._autosaveTimer = setTimeout(() => this._flushAutosave(), 1500);

        const statusEl = document.getElementById('autosaveStatus');
        if (statusEl) statusEl.textContent = '編輯中…';
    }

    /**
     * 在「程式把內容畫進畫布」期間讓 autosave 靜音。
     *
     * NOTE(NOTE-023): `insertEditorCard()` 用 `document.execCommand('insertHTML')`，
     * 而 execCommand 在 contenteditable 上會派發 **isTrusted 的 input 事件**
     * （瀏覽器標準行為，不是本專案的 bug）。autosave 綁在 editorCanvas 的 input 上，
     * 於是每一次切章渲染都被當成使用者編輯，寫出一份 `_draft__1.json`。
     * 實機證據見 docs/HANDOFF.md §3.18。
     *
     * **只包切章的渲染路徑，不包 `insertEditorCard()` 本身**：插入素材、
     * Word 匯入、2A 複製草稿、使用者按「復原草稿」也走那個函式，
     * 那些是真的使用者動作，草稿該存。包錯層會讓真正的編輯存不進去。
     *
     * execCommand 的 input 事件是同步派發的（實機堆疊裡 insertEditorCard
     * 就在 listener 的呼叫堆疊上），所以 try/finally 這個視窗蓋得到它。
     */
    _renderWithoutAutosave(render) {
        this._programmaticRender = true;
        try {
            return render();
        } finally {
            this._programmaticRender = false;
        }
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

        // NOTE(NOTE-011) 這裡不能用原生 confirm()：它會凍結整個分頁。
        // 改用與 conflict-bar / peer-update-bar 同一套的非阻塞提示條。
        this._showDraftRestoreBar(section, draft, restore);
    }

    /**
     * 未存草稿的復原提示條（非阻塞）。
     *
     * NOTE(NOTE-011) 原本這裡是 `confirm()`。它有兩個問題：
     *   1. 阻塞：對話框開著時整個分頁停擺，socket 回應全部排隊等待，
     *      使用者連捲動看一眼「目前內容是什麼」都做不到，卻要當場決定要不要覆蓋它。
     *   2. 無法自動化驗證：原生對話框不在 DOM 裡，preview_eval 會直接逾時
     *      （實測連 `1+1` 都不回應），只能重啟瀏覽器才能繼續。
     * 語意完全不變：復原＝載入草稿；忽略／不理它＝保留目前內容，草稿在下次存檔時被覆蓋。
     * 不自動消失 —— 這是一個需要使用者決定的提示，不是通知；
     * 但「不理它」本身就是安全的那一邊（目前內容不動），所以不強迫互動。
     */
    _showDraftRestoreBar(section, draft, restore) {
        const canvas = this.app.editorCanvas;
        if (!canvas) return;
        const card = canvas.querySelector(`.editor-card[data-section="${CSS.escape(section)}"]`)
            || canvas.querySelector('.editor-card');
        if (!card) return;

        const old = card.querySelector('.draft-restore-bar');
        if (old) old.remove();

        const when = draft._updated_at
            ? new Date(draft._updated_at).toLocaleString()
            : '稍早';

        const bar = document.createElement('div');
        bar.className = 'draft-restore-bar';
        bar.setAttribute('data-section', section);
        bar.style.cssText = [
            'background:#e0f2fe',
            'border:1.5px solid #0284c7',
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
        // flex-basis 給一個可讀的下限，不用 `flex:1`：2B 欄位可以被拖到很窄，
        // `flex:1` 允許訊息一路縮，於是一行字被擠成七、八行、整條變成直條
        // （實測 1440px 視窗下訊息高 147px、整條 166px），按鈕反而不換行。
        // 空間不夠時該讓按鈕掉到下一行，而不是把文字壓成一條。
        msg.style.cssText = 'flex:1 1 14rem;min-width:0';
        msg.innerHTML = `<i class="bi bi-clock-history me-1"></i>`
            + `「${this._esc(this._sectionLabel(section))}」有未存檔的自動儲存草稿`
            + `（${this._esc(when)}）。`;

        const btnRestore = document.createElement('button');
        btnRestore.className = 'btn btn-sm btn-primary fw-bold';
        btnRestore.textContent = '復原草稿';
        btnRestore.onclick = () => {
            bar.remove();
            restore();
        };

        const btnKeep = document.createElement('button');
        btnKeep.className = 'btn btn-sm btn-outline-secondary fw-bold';
        btnKeep.textContent = '保留目前內容';
        btnKeep.title = '草稿會在下次存檔時被覆蓋';
        btnKeep.onclick = () => bar.remove();

        bar.appendChild(msg);
        bar.appendChild(btnRestore);
        bar.appendChild(btnKeep);
        card.insertBefore(bar, card.firstChild);
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

    /**
     * 把 2B 目前卡片的內容推進 2C 全文畫布。
     *
     * NOTE(NOTE-007) 這裡必須是「以章節為鍵的原位 upsert」，不是 append。
     * 舊版直接 `fusionCanvas.innerHTML += ...`：每修一次 Introduction 再推一次，
     * 2C 就多長出一段 Introduction，累積成多份重複章節；而且排列順序取決於推送
     * 先後，與論文真正的章節順序無關。
     *
     * 章節身分寫在 data-section。2C 存檔存的就是 fusionCanvas.innerHTML，載入時
     * 原樣回填（cmd_save_paper / paper_loaded），所以這個屬性會隨 G.Ver 一起往返
     * ——重整後再推同一章仍然是取代，冪等性不因重新整理而失效。
     *
     * 排列以 app.sections 的宣告順序為準，與 2B 章節選單同源。
     * 沒有 data-section 的既有內容（舊版存下來的裸段落、Word 匯入原文）一律原地
     * 保留：它們本來就沒有章節身分，靠標題文字猜歸屬只會把使用者的舊稿搬錯位置。
     */
    cardActionPushToFusion(btn) {
        const card = btn.closest('.editor-card');
        // [v0.6 修正] 改為 innerHTML，確保推入 2C 時圖片不會消失
        const content = card.querySelector('.card-content').innerHTML;
        const section = card.getAttribute('data-section') || 'general';

        if (this.app.fusionCanvas) {
            this._clearFusionPlaceholder();

            const block = this._ensureFusionBlock(section);
            const body = block.querySelector('.fusion-body');
            // NOTE(NOTE-030) 原樣搬運，**不得**再做 \n → <br>。
            // 那一行是 v0.5 的殘骸：當時 content 取的是 innerText（純文字，換行是
            // 語意的），v0.6 為了保住圖片改成 innerHTML 卻沒把補償一起拿掉，於是
            // HTML 原始碼的排版換行全被當成使用者的分行。素材插入樣板都是多行
            // template literal，所以只要該章有圖，推進 2C 就固定多出 4～5 個空行。
            if (body) body.innerHTML = content;

            // 標記這一章對應 2B 的哪一版，讓「2C 這段是從哪來的」可追。
            const srcVer = this._currentSectionVer(section) || '';
            block.setAttribute('data-src-ver', srcVer);
            const verTag = block.querySelector('.fusion-src-ver');
            if (verTag) verTag.textContent = srcVer ? `S.Ver ${srcVer}` : '未存檔';
        }

        const icon = btn.querySelector('i');
        icon.classList.replace('bi-arrow-left-circle-fill', 'bi-check-circle-fill');
        icon.classList.replace('text-primary', 'text-success');
        setTimeout(() => {
            icon.classList.replace('bi-check-circle-fill', 'bi-arrow-left-circle-fill');
            icon.classList.replace('text-success', 'text-primary');
        }, 1200);
    }

    /**
     * 移除 2C 的預設提示段落。
     *
     * 用節點比對而不是 innerHTML 字串取代：整片重寫 innerHTML 會把畫布上所有節點
     * 重建，使用者停在 2C 的插入點會跳掉，既有 fusion-block 的參照也會失效。
     */
    _clearFusionPlaceholder() {
        const canvas = this.app.fusionCanvas;
        if (!canvas) return;
        Array.from(canvas.children).forEach((el) => {
            if (el.classList.contains('fusion-block')) return;
            if (String(el.textContent || '').includes('來自 2B 的段落將會依序插入於此處')) {
                el.remove();
            }
        });
    }

    /** 2C 裡代表該章節的區塊；用逐一比對避免章節 id 直接進 CSS 選擇器。 */
    _findFusionBlock(sectionId) {
        const canvas = this.app.fusionCanvas;
        if (!canvas) return null;
        const byId = Array.from(canvas.querySelectorAll('.fusion-block[data-section]'))
            .find(b => b.getAttribute('data-section') === sectionId) || null;
        if (byId) return byId;
        // NOTE(NOTE-037) 沒有帶身分的區塊時，才去認領 NOTE-007 之前存下來的舊區塊。
        return this._adoptLegacyFusionBlock(sectionId);
    }

    /**
     * 認領一個「有標題但沒有 data-section」的舊區塊，並補寫身分。
     *
     * NOTE(NOTE-037) 為什麼需要這個：NOTE-007 之前的實作（commit 32e1949）是
     *   this.app.fusionCanvas.innerHTML += `<div class="fusion-block ...">
     *       <h5 ...><i class="bi bi-check2-circle me-1"></i>${sectionLabel}</h5>...`
     * —— 純 append，而且**沒有 data-section**。那些區塊隨 G.Ver 一路存到今天。
     * `_findFusionBlock` 只查 `[data-section]`，查不到就走 append 分支，於是每推
     * 一次就在最後面多一個同名章節。擁有者在 G.Ver V12 的專案上實機撞到兩個
     * Abstract，就是這條路徑（舊的那個標題旁沒有 S.Ver 徽章，新的有）。
     *
     * 認領的四道門檻是刻意收得極窄的 —— 這裡是整個 2C 最容易把使用者稿件搬錯
     * 位置的地方：
     *   1. 必須**完全沒有** data-section。絕不從別的章節手上搶。
     *   2. 標題文字必須與章節 label **全等**（剝掉 icon 與 S.Ver 徽章、正規化空白、
     *      忽略大小寫）。用 includes 會讓 `Abstract` 認領 `Abstract and Keywords`。
     *   3. 只認領第一個相符者。
     *   4. 認領**不搬動位置** —— 使用者的排版是他自己排的，只補一個屬性。
     */
    _adoptLegacyFusionBlock(sectionId) {
        const canvas = this.app.fusionCanvas;
        if (!canvas) return null;

        const wanted = this._normaliseHeading(this._sectionLabel(sectionId));
        if (!wanted) return null;

        const candidates = Array.from(canvas.querySelectorAll('.fusion-block'))
            .filter(b => !b.hasAttribute('data-section'))
            .filter(b => this._normaliseHeading(this._blockHeadingText(b)) === wanted);

        if (!candidates.length) return null;

        const block = candidates[0];
        block.setAttribute('data-section', sectionId);

        // 舊樣板沒有 fusion-src-ver 這個元素，補上去 S.Ver 才寫得進去，
        // 否則「這段來自 2B 第幾版」永遠是空的。
        const heading = block.querySelector('h1, h2, h3, h4, h5, h6');
        if (heading && !heading.querySelector('.fusion-src-ver')) {
            const badge = document.createElement('span');
            badge.className = 'badge bg-light text-secondary fw-normal ms-2 fusion-src-ver';
            heading.appendChild(badge);
        }
        // 極舊的存檔可能連 .fusion-body 都沒有；沒有就把現有內容包進一個。
        if (!block.querySelector('.fusion-body')) {
            const body = document.createElement('div');
            body.className = 'fusion-body';
            while (heading ? heading.nextSibling : block.firstChild) {
                const node = heading ? heading.nextSibling : block.firstChild;
                body.appendChild(node);
            }
            block.appendChild(body);
        }

        if (candidates.length > 1 && this.app.ui && this.app.ui.showNotice) {
            // **刻意不自動刪除**：那是使用者的稿件，靜默刪除比留下重複更糟。
            this.app.ui.showNotice(
                `2C 裡有 ${candidates.length} 個「${this._sectionLabel(sectionId)}」區塊，`
                + '已更新第一個。請確認是否要手動移除其餘的。', 'warning');
        }
        return block;
    }

    /** 區塊標題的純文字（不含 S.Ver 徽章）。 */
    _blockHeadingText(block) {
        const heading = block.querySelector('h1, h2, h3, h4, h5, h6');
        if (!heading) return '';
        // 在複本上動刀，不影響畫面上的節點。
        const clone = heading.cloneNode(true);
        clone.querySelectorAll('.fusion-src-ver, .badge, i, svg').forEach(el => el.remove());
        return clone.textContent || '';
    }

    /** 標題比對用的正規化：收斂空白、去頭尾、忽略大小寫。 */
    _normaliseHeading(text) {
        return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    }

    /**
     * 取得該章節在 2C 的區塊，沒有就依 canonical 順序插入一個新的。
     *
     * 插入位置取「第一個章節序在它之後的區塊」之前，因此無論使用者以什麼順序推送，
     * 2C 的章節排列永遠等同 app.sections 的宣告順序。找不到後繼者才附加到最後。
     */
    _ensureFusionBlock(sectionId) {
        const existing = this._findFusionBlock(sectionId);
        if (existing) return existing;

        const canvas = this.app.fusionCanvas;
        const block = document.createElement('div');
        block.className = 'fusion-block mb-4 border-start border-4 border-success ps-3';
        block.setAttribute('data-section', sectionId);
        block.innerHTML =
            `<h5 class="text-success fw-bold"><i class="bi bi-check2-circle me-1"></i>`
            + `${this._esc(this._sectionLabel(sectionId))}`
            + `<span class="badge bg-light text-secondary fw-normal ms-2 fusion-src-ver"></span></h5>`
            + `<div class="fusion-body"></div>`;

        const order = (this.app.sections || []).map(s => s.id);
        const idx = order.indexOf(sectionId);
        const successor = idx < 0 ? null : Array
            .from(canvas.querySelectorAll('.fusion-block[data-section]'))
            .find(b => order.indexOf(b.getAttribute('data-section')) > idx);

        if (successor) canvas.insertBefore(block, successor);
        else canvas.appendChild(block);
        return block;
    }

    // =========================================================================
    // Word 貼上殘留的硬換行清理（NOTE-038）
    // =========================================================================

    /**
     * 這個 <br> 是不是「硬換行殘骸」（出現在句子中間的換行）。
     *
     * NOTE(NOTE-038) 用 DOM 節點判斷，**不對 HTML 跑 regex** —— 用 regex 改寫
     * HTML 是已知的坑（屬性裡的 `&lt;br&gt;` 也會被打到，實測那份稿件的 style
     * 屬性裡就有 `mso-pagination` 旁邊夾著跳脫過的 br 字樣）。
     *
     * 判準是「這個換行落在一個句子的中間」：
     *   前面可見文字結尾是字母/數字，或**非句末**標點（, ; : ) ] -）
     *   後面可見文字（略過空白）開頭是字母/數字/中文/左括號
     * 句末標點（. ! ? 。！？）之後的換行**刻意保留** —— 那可能是作者要的分行。
     */
    _isSoftWrapBreak(br) {
        const prev = this._visibleTextBefore(br);
        const next = this._visibleTextAfter(br);
        if (!prev || !next) return false;
        // 前面：非句末的結尾字元
        if (!/[A-Za-z0-9,;:)\]\-一-鿿]$/.test(prev)) return false;
        // 後面：略過空白之後必須立刻是內容
        if (!/^[A-Za-z0-9(一-鿿]/.test(next.replace(/^\s+/, ''))) return false;
        return true;
    }

    /** <br> 之前最近的可見文字（往前走出祖先，遇到區塊邊界就停）。 */
    _visibleTextBefore(node) {
        let cur = node;
        let text = '';
        while (cur && text.length < 40) {
            let sib = cur.previousSibling;
            while (sib) {
                if (sib.nodeType === Node.TEXT_NODE) text = sib.textContent + text;
                else if (sib.nodeType === Node.ELEMENT_NODE) {
                    if (this._isBlockLevel(sib)) return text.replace(/\s+$/, '');
                    text = (sib.textContent || '') + text;
                }
                if (text.replace(/\s+$/, '')) return text.replace(/\s+$/, '');
                sib = sib.previousSibling;
            }
            cur = cur.parentNode;
            if (!cur || this._isBlockLevel(cur)) break;
        }
        return text.replace(/\s+$/, '');
    }

    /** <br> 之後最近的可見文字。 */
    _visibleTextAfter(node) {
        let cur = node;
        let text = '';
        while (cur && text.length < 40) {
            let sib = cur.nextSibling;
            while (sib) {
                if (sib.nodeType === Node.TEXT_NODE) text += sib.textContent;
                else if (sib.nodeType === Node.ELEMENT_NODE) {
                    if (this._isBlockLevel(sib)) return text;
                    text += (sib.textContent || '');
                }
                if (text.trim()) return text;
                sib = sib.nextSibling;
            }
            cur = cur.parentNode;
            if (!cur || this._isBlockLevel(cur)) break;
        }
        return text;
    }

    _isBlockLevel(el) {
        return el && el.nodeType === Node.ELEMENT_NODE && /^(P|DIV|LI|OL|UL|TABLE|TR|TD|TH|H1|H2|H3|H4|H5|H6|BLOCKQUOTE|SECTION|ARTICLE)$/
            .test(el.tagName);
    }

    /**
     * 清理 2C 畫布上 Word 貼上殘留的硬換行。**由使用者按按鈕觸發，不自動執行。**
     *
     * NOTE(NOTE-038) 刻意不自動跑、也不自動存檔：這是在改使用者的稿件，
     * 哪些換行是作者故意的只有作者知道。清理後畫面先呈現結果，
     * 使用者確認無誤才自己按 Save；不滿意就重新整理，等於什麼都沒發生。
     */
    cleanupHardWraps() {
        const canvas = this.app.fusionCanvas;
        if (!canvas) return 0;

        const notify = (msg, level) => {
            if (this.app.ui && this.app.ui.showNotice) this.app.ui.showNotice(msg, level);
        };

        const before = canvas.querySelectorAll('br').length;
        if (!before) { notify('2C 沒有找到任何換行，不需要清理。', 'info'); return 0; }

        let removed = 0;

        // 1) 連續 3 個以上的 <br>：Word 拿來當垂直間距的填充，整組移除。
        const all = Array.from(canvas.querySelectorAll('br'));
        let run = [];
        const flushRun = () => {
            if (run.length >= 3) { run.forEach(b => { b.remove(); removed++; }); }
            run = [];
        };
        all.forEach((br) => {
            const prev = br.previousSibling;
            const contiguous = run.length && prev === run[run.length - 1];
            const onlySpaceBetween = run.length && prev && prev.nodeType === Node.TEXT_NODE
                && !prev.textContent.trim() && prev.previousSibling === run[run.length - 1];
            if (contiguous || onlySpaceBetween) run.push(br);
            else { flushRun(); run = [br]; }
        });
        flushRun();

        // 2) 直接掛在 <ol>/<ul> 底下（夾在 </li> 與 <li> 之間）的 <br>。
        Array.from(canvas.querySelectorAll('ol > br, ul > br')).forEach((br) => {
            br.remove(); removed++;
        });

        // 3) 句子中間的硬換行 —— 換成一個空白，否則前後字會黏在一起。
        Array.from(canvas.querySelectorAll('br')).forEach((br) => {
            if (!br.isConnected) return;
            if (!this._isSoftWrapBreak(br)) return;
            br.replaceWith(document.createTextNode(' '));
            removed++;
        });

        const after = canvas.querySelectorAll('br').length;
        this.updateWordCount();

        if (!removed) {
            notify('沒有偵測到 Word 貼上殘留的硬換行（保留了所有換行）。', 'info');
        } else {
            notify(`已移除 ${removed} 個硬換行（換行數 ${before} → ${after}）。`
                 + '請確認內容無誤後按 Save 保存；不滿意就重新整理，不會有任何變更。',
                   'warning');
        }
        return removed;
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
    // [v2.0] 2B 切章協調器
    //
    // 為什麼要有這一層：切章是兩段式非同步流程（cmd_list_blocks → 版本清單 →
    // cmd_load_block → 內文），而 block_list / block_loaded 同時服務四種來源：
    // 章節下拉、2A 章節同步、「開啟舊版」modal、以及衝突提示條的重新載入。
    // 舊版沒有任何請求識別，於是：
    //   1. 切章後沒人去載入目標章內容 —— 選單與 S.Ver 換了，畫布還是上一章
    //      （loadMultiSectionContent 只在畫布近乎空白時才畫佔位，舊內容原封不動）。
    //   2. 快速 A→B→C 時，遲到的 A/B 回應會蓋掉 C。
    // 這裡用單調遞增的 token 綁定「一次切章」，所有回應都必須核對 token 與
    // section 才准動畫布。
    // =========================================================================

    /**
     * 切換 2B 目前章節的唯一入口。回傳本次請求的 token。
     *
     * 呼叫端不要自己 emit cmd_list_blocks，否則就繞過了 stale 防護。
     */
    requestSectionSwitch(sectionId) {
        if (!sectionId) return null;

        const token = `s${++this._sectionReqSeq}`;
        this._activeSectionReq = { token, section: sectionId };

        // NOTE(NOTE-004) 記住章節必須綁在「唯一入口」，不能留給各呼叫端自己做。
        // 章節下拉（manuscript_wsui.js 的 sectionDropdownMenu）當初直接呼叫本函式
        // 而沒有經過 switchChatSection，於是 localStorage 永遠停在上一次 2A 同步
        // 的章節。實測：切到 results → 重整 → 回到 abstract，使用者的所在位置遺失。
        // 放在這裡才能同時涵蓋下拉、2A 同步、重連後 resync 三條路徑。
        this.rememberChatSection(sectionId);

        this.app.selectedSections = new Set([sectionId]);
        if (this.app.ui && this.app.ui.updateDropdownLabel) {
            this.app.ui.updateDropdownLabel();
        }
        // NOTE(NOTE-036) 完成比例掛在**這個唯一入口**，理由與 NOTE-004
        // 記章節位置完全相同：下拉、2A 同步、重連後 resync 三條路徑都
        // 走這裡。掛在任何一個呼叫端都會漏掉另外兩條，症狀是「切了章
        // 但比例還是上一章的數字」——而那個數字看起來完全合理。
        if (this.app.progress) this.app.progress.onSectionChanged();

        // 先隔離再載入：舊章內容必須立刻離開畫布。若等回應到了才清，
        // 中間這段時間畫面上是「Reference 的標題 + Introduction 的正文」，
        // 使用者一旦此時按存檔就會把上一章的內容存進來。
        this._renderSectionLoading(sectionId);
        if (this.app.sectionVersion) {
            this.app.sectionVersion.innerHTML = '<option value="">載入中…</option>';
        }

        if (this.app.socket && this.app.pid) {
            this.app.socket.emit('cmd_list_blocks', {
                pid: this.app.pid,
                section: sectionId,
                intent: 'section_switch',
                req_token: token,
            });
        }
        return token;
    }

    /**
     * 作廢所有在途的切章回應。
     *
     * 給「使用者用別的方式改寫了畫布」的路徑用（建立空白新章、從 2A 複製草稿、
     * Word 匯入）。少了這一步，先前切章的 block_loaded 晚一步到達時，會把使用者
     * 剛放進畫布的內容換成該章的舊版本。
     */
    _cancelPendingSectionSwitch() {
        this._activeSectionReq = null;
    }

    /** 回應是否屬於「目前這一次」切章；不是就必須整份丟棄。 */
    _isStaleSectionReq(data) {
        const req = this._activeSectionReq;
        if (!req || !data) return true;
        return data.req_token !== req.token || data.section !== req.section;
    }

    /** 目前 2B 正在檢視的章節 id。 */
    _currentSectionId() {
        return Array.from(this.app.selectedSections || [])[0] || null;
    }

    _sectionLabel(sectionId) {
        return this.app.sections.find(s => s.id === sectionId)?.label || sectionId;
    }

    /** 切章進行中的過渡畫面：非 contenteditable，避免使用者對著暫時內容打字。 */
    _renderSectionLoading(sectionId) {
        if (!this.app.editorCanvas) return;
        const label = this._esc(this._sectionLabel(sectionId));
        this.app.editorCanvas.innerHTML = `
            <div class="section-loading text-center text-muted p-5" data-section="${this._esc(sectionId)}">
                <div class="spinner-border spinner-border-sm me-2"></div>正在載入 ${label} …
            </div>`;
        this.updateWordCount();
    }

    /**
     * 從未存檔的章節：給一張真正空白、可編輯、可存檔的卡片。
     *
     * 舊版在這裡畫的是 .section-block 佔位（「Awaiting content draft for …」），
     * 那不是 .editor-card —— cardActionSave 與 saveAllBlocks 都只認 .editor-card，
     * 所以全新章節打完字根本存不了，而且佔位文字會被當成正文一起算進字數。
     */
    _renderBlankSection(sectionId) {
        if (!this.app.editorCanvas) return;
        this.app.editorCanvas.innerHTML = '';
        if (this.app.sectionVersion) this.app.sectionVersion.value = '';
        this.app.lastSavedSVer[sectionId] = '';
        // NOTE(NOTE-023): 空白新章是程式畫的，不是使用者打的字。
        this._renderWithoutAutosave(() => this.insertEditorCard('<p><br></p>', sectionId));
    }

    /**
     * 收到屬於目前切章請求的版本清單後決定畫什麼。
     *   有正式版本 → 載入最新版（清單新版在前，見 ManuscriptIO.list_block_versions）
     *   沒有版本   → 空白新章畫布
     * 個人 autosave 草稿一律走既有的詢問復原機制，不直接覆蓋畫布。
     */
    _applySectionSwitch(data) {
        const section = data.section;
        const versions = Array.isArray(data.versions) ? data.versions : [];
        const latest = versions.find(v => v && v.filename);

        if (latest) {
            // 草稿的詢問必須延到 block_loaded 之後。此刻畫布還停在 loading，
            // _offerDraftRestore 會判定「畫布是空的」而直接復原草稿，接著
            // 稍晚到達的 block_loaded 又把它蓋成已存檔版本 —— 使用者的未存檔
            // 內容就這樣無聲消失。
            if (this._activeSectionReq) {
                this._activeSectionReq.pendingDraft = data.draft || null;
            }
            // 內容要等 block_loaded 才會到；畫布維持 loading，不先畫空白卡，
            // 否則會先閃一下「空白新章」再跳出正文，看起來像內容被清掉了。
            this.app.socket.emit('cmd_load_block', {
                pid: this.app.pid,
                section: section,
                filename: latest.filename,
                intent: 'section_switch',
                req_token: data.req_token,
            });
        } else {
            // 沒有正式版本：空白畫布是同步畫好的，此時詢問草稿才有正確的
            // 「畫布是否已有內容」判斷依據。
            this._renderBlankSection(section);
            if (data.draft && data.draft.content) {
                this._offerDraftRestore(section, data.draft);
            }
        }
    }

    /** 切章載入完成後，才處理該章的未存檔草稿（時序理由見 _applySectionSwitch）。 */
    _consumePendingDraft(section) {
        const req = this._activeSectionReq;
        if (!req || req.section !== section) return;
        const draft = req.pendingDraft;
        req.pendingDraft = null;
        if (draft && draft.content) {
            this._offerDraftRestore(section, draft);
        }
    }

    // =========================================================================
    // 畫布內容與字數統計
    // =========================================================================
    loadMultiSectionContent() {
        if (this.app.editorCanvas.innerText.trim().length < 10) {
            const activeId = this._currentSectionId() || 'abstract';
            const label = this._sectionLabel(activeId);
            this.app.editorCanvas.innerHTML = `
                <div class="section-block mb-4" data-section="${activeId}">
                    <h2 class="text-primary border-bottom pb-2 h4">${label}</h2>
                    <div class="section-content"><p class="text-muted fst-italic">Awaiting content draft for ${label} section...</p></div>
                </div>`;
        }
        this.updateWordCount();
    }

    /**
     * 統計 2B 目前草稿字數。
     *
     * NOTE(NOTE-006) 只能數 .card-content，不能數整個畫布的 innerText。
     * 每張 editor-card 都帶一個章節標籤 badge（見 insertEditorCard），整片數會把
     * 這些 UI 文字算進去——實測切到從未存檔的 Reference 時，空白畫布顯示
     * 「1 字」，那個 1 就是標籤本身。存檔取的也是 .card-content，統計口徑
     * 必須跟儲存口徑一致，否則使用者看到的字數永遠對不上實際存進去的內容。
     *
     * 已知限制：以空白切分，中日韓文整段會被算成 1 字。這是既有行為，
     * 本次不改動，以免所有章節顯示的數字一次全變。
     */
    /**
     * 送給 Drafter 的「目前未存草稿」文字。
     *
     * NOTE(NOTE-012) 只取 .card-content，且不再自行截斷、不再宣告版本。
     * 舊版送的是 `editorCanvas.innerText.substring(0, 3000)`，有三個問題：
     *   1. innerText 含每張卡片的章節標籤 badge —— UI 文字被當成論文正文送進 LLM
     *      （同一個取法讓空白畫布顯示「1 字」，見 NOTE-006）。
     *   2. 3000 字硬截斷，長章節後半段永遠到不了模型。
     *   3. 同時帶 `s_ver: '0.1'` 死值，讓伺服器讀錯版本。
     * 已存檔的內容改由伺服器依真實版本讀取（COC bundle），這裡只補「還沒存檔的」
     * 那一段；預算與截斷都交給伺服器統一處理。
     */
    _draftTextForPrompt() {
        const bodies = this.app.editorCanvas
            ? this.app.editorCanvas.querySelectorAll('.card-content')
            : [];
        return Array.from(bodies).map(el => (el.innerText || '').trim())
            .filter(Boolean).join('\n\n');
    }

    updateWordCount() {
        const bodies = this.app.editorCanvas
            ? this.app.editorCanvas.querySelectorAll('.card-content')
            : [];
        const text = Array.from(bodies).map(el => el.innerText || '').join('\n');
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
        // [v2.0] 改走 requestSectionSwitch：這條路徑同時是「重整／離頁返回」的
        // 進入點（init 會在啟動時呼叫一次），必須真的把該章最新版載回畫布，
        // 只送 cmd_list_blocks 只會填好版本選單而正文永遠空著。
        this.requestSectionSwitch(sectionId);
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
            data = data || {};
            // [collab] 伺服器判定這一章不可讀時會帶 forbidden；
            // 代表權限在本次工作階段中被改動過，重抓權限讓 UI 跟上。
            if (data.forbidden && this.app.collab) {
                this.app.collab.loadPermissions();
            }

            // [v2.0] 只有「切章」這個 intent 才准動畫布。開啟舊版 modal 與衝突列
            // 的重新載入也走同一個 block_list 事件，若不分辨，光是打開版本清單
            // 就會把使用者正在編輯的內容洗掉。
            const isSwitch = data.intent === 'section_switch';
            const stale = isSwitch && this._isStaleSectionReq(data);

            // [v1.8] 同步版本選單（結構化清單，含 from_ver / 作者）。
            // [v2.0] 只在回應屬於目前章節時重畫：modal 可以查別章的版本，
            // 無條件重畫會把 S.Ver 換成別章的版本號。
            if (!stale && data.section === this._currentSectionId()) {
                this.renderSectionVersions(data.versions, null);
            }

            if (isSwitch && !stale) {
                this._applySectionSwitch(data);
            } else if (!isSwitch && data.draft && data.draft.content) {
                // [v1.8] 非切章來源仍保留原本的草稿復原提示。
                this._offerDraftRestore(data.section, data.draft);
            }

            const container = document.getElementById('oldBlockListContainer');
            if (!container) return;
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
            data = data || {};
            // [v2.0] 帶 req_token 的是切章自動載入，必須核對是不是「這一次」切章；
            // 沒帶的是使用者自己點版本清單（含跨章開啟舊版），維持原本行為。
            const fromSwitch = data.req_token != null;
            if (fromSwitch && this._isStaleSectionReq(data)) return;

            if (!data.ok) {
                // 自動載入失敗（權限剛被撤、版本檔被刪）時不能停在 loading，
                // 否則畫布永遠轉圈且無法編輯。退回空白新章讓使用者還能作業。
                if (fromSwitch) {
                    this.addSystemMessage(`無法載入 ${this._sectionLabel(data.section)} 的最新版本，已開啟空白畫布。`);
                    this._renderBlankSection(data.section);
                    this._consumePendingDraft(data.section);
                }
                return;
            }

            if(data.ok && this.app.editorCanvas) {
                this.app.selectedSections = new Set([data.section]);
                this.app.ui.updateDropdownLabel();
                this.app.editorCanvas.innerHTML = '';

                // NOTE(NOTE-023): 載入既有版本同樣是程式化渲染，不得寫草稿。
                this._renderWithoutAutosave(
                    () => this.insertEditorCard(data.content.content, data.section)
                );
                // [v1.8] 版號優先讀 ver 欄位；舊檔沒有時退回 version（帶 V 前綴）。
                const vStr = data.content.ver
                    || String(data.content.version || '').replace(/^[Vv]/, '');
                if (this.app.sectionVersion) this.app.sectionVersion.value = vStr;
                this.app.lastSavedSVer[data.section] = vStr;
                this.addSystemMessage(`已載入 2B 段落版本 V${vStr}。存檔會建立新版，此版保留不動。`);

                // NOTE(NOTE-010) 版本換了，留言也要跟著換。留言綁 section + S.Ver，
                // 這裡是「目前檢視版本」真正改變的唯一時點（切章與手動選版本都會
                // 走到這）；不在這裡刷新，側欄就會繼續顯示上一版的意見。
                if (this.app.collab) {
                    this.app.collab.refreshCommentBadge();
                    const panel = document.getElementById('chapterCommentPanel');
                    if (panel && panel.style.display === 'flex') {
                        this.app.collab.refreshComments();
                    }
                }

                // [v2.0] 正文就位後才問草稿，讓「畫布已有內容」的判斷成立而走詢問路徑。
                if (fromSwitch) this._consumePendingDraft(data.section);
            }
        });
    }

    createNewSection(secId) {
        this._cancelPendingSectionSwitch();
        this.app.selectedSections = new Set([secId]);
        this.app.ui.updateDropdownLabel();
        // 新草稿沒有來源版本；存檔時 from_ver 送 null，伺服器指派 max+0.1。
        // [v2.0] 改用 _renderBlankSection：舊做法畫的是唯讀佔位區塊，
        // 不是 .editor-card，所以「建立空白新草稿」後打的字根本存不了。
        this._renderBlankSection(secId);

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

    /**
     * 畫布上是否有「使用者真的寫了東西」的卡片。
     *
     * 不能只看 .editor-card 的數量：空白新章現在也是一張正常的（空）卡片，
     * 只數張數會把它當成有內容，於是切章前先「強制備份」，替空章生出一個
     * 沒有任何內容的版本號。也不能只比對佔位字串 —— 那份清單每加一種佔位
     * 畫面就要跟著改，漏一個就退回舊行為。
     */
    _editorHasContent() {
        const canvas = this.app.editorCanvas;
        if (!canvas) return false;
        return Array.from(canvas.querySelectorAll('.editor-card .card-content'))
            .some(el => String(el.innerText || '').trim().length > 0);
    }

    openOldBlockFlow() {
        if (!this._editorHasContent()) {
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
        const payload = { msg: txt, context: this._draftTextForPrompt(), target_lang: targetLang, attachment: this.app.currentAttachment, import_type: this.app.currentImportType, pid: this.app.pid, title: this.app.paperTitleInput.value.trim(), section: targetSection };
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
        this.app.socket.emit('chat_message', { msg: refineMsg, context: this._draftTextForPrompt(), target_lang: targetLang, pid: this.app.pid, title: currentTitle, section: targetSection });
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

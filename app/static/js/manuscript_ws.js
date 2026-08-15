// Roothinks source maintenance contract
// 檔案路徑: app/static/js/manuscript_ws.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 Manuscript Socket 連線、認證、room lifecycle 與協作事件派送。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/manuscript_ws.js
//路徑(./app/static/js/manuscript_ws.js)
//版本 v4.6 (Main Controller - Toolbar Binding Bootstrap + Word direct import)
//更版時間 20260421-1445
// inner comment: 嚴格保留 v4.2 全量排版與代理邏輯。新增素材庫 (Asset Gallery) 的事件代理方法，支援 2B/2C 工具列的呼叫。
// CHANGE_PLAN_STUDY_FLOWB_2026-04-20: MVP prototype - add direct .docx import path into 2B/2C canvases.

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

document.addEventListener('DOMContentLoaded', () => {
    try {
        if (typeof ManuUI === 'undefined' || typeof ManuSoed === 'undefined' || typeof ManuImage === 'undefined') {
            throw new Error("【致命錯誤】找不到 ManuUI, ManuSoed 或 ManuImage 模組！<br>請確認 HTML 底部是否有依序引入這 4 支 JS 檔案，並且清除瀏覽器快取 (Ctrl+F5)。");
        }

        const bearerToken =
            window.localStorage.getItem('roothinks_api_token')
            || document.body?.dataset?.apiToken
            || '';

        const isLocalDevHost = /^(localhost|127\.0\.0\.1)$/i.test(window.location.hostname);
        // Local dev + gthread 時優先使用 polling，避免 websocket 握手不穩造成草稿任務卡住。
        const socketTransports = isLocalDevHost ? ['polling'] : ['websocket', 'polling'];

        // [presence] pid 必須在建立連線時就送出：伺服器的 connect handler 依此
        // 驗 workspace role 並 join_room("ws:{pid}")，沒帶的話收不到在線廣播。
        // 這裡重複一次 ManuscriptWorkspace 建構子裡的 pid 解析，因為 socket
        // 比 workspace 實例更早建立。
        const _params = new URLSearchParams(window.location.search);
        const _hidden = document.getElementById('initialPid');
        const connectPid = (_params.get('pid') || '').trim()
            || (_hidden ? (_hidden.value || '').trim() : '');

        const socket = io('/manu_ws', {
            forceNew: true,
            transports: socketTransports,
            reconnection: true,
            reconnectionAttempts: 20,
            reconnectionDelay: 1200,
            reconnectionDelayMax: 8000,
            timeout: 30000,
            auth: Object.assign(
                bearerToken ? { token: bearerToken } : {},
                connectPid ? { pid: connectPid } : {}
            ),
        });
        window.wsApp = new ManuscriptWorkspace(socket);
        console.log("[ManuscriptWS] Core Engine v4.3 Online. Modular Architecture (WS + UI + SOED + IMG) Active.");
    } catch (error) {
        console.error("[Fatal Init Error]", error);
        const chatCanvas = document.getElementById('chatCanvas');
        if (chatCanvas) {
            chatCanvas.innerHTML = `<div class="alert alert-danger m-3 shadow-sm border-danger"><i class="bi bi-exclamation-triangle-fill me-2"></i><b>系統初始化失敗</b><hr><span class="small">${escapeHtml(error.message)}</span></div>`;
        }
    }
});

class ManuscriptWorkspace {
    constructor(socket) {
        this.socket = socket;

        // 核心狀態
        this.selectedSections = new Set(['abstract']);
        this.lastSavedGVer = '0.0';
        this.lastSavedSVer = {};
        this.pendingOpenPaperModal = false;
        this.pendingOpenBlockModal = false;

        // --- 視窗元件與佈局元素 ---
        this.panel2C = document.getElementById('panel2C');
        this.panel2B = document.getElementById('panel2B');
        this.panel2A = document.getElementById('panel2A');

        this.toolbarPidDisplay = document.getElementById('toolbarPidDisplay');
        this.panelRestoreGroup = document.getElementById('panelRestoreGroup');
        this.restoreHintText = document.getElementById('restoreHintText');
        this.formalProjectList = document.getElementById('formalProjectList');

        this.paperTitleInput = document.getElementById('paperTitle');
        this.globalVersion = document.getElementById('globalVersion');
        this.btnSaveGlobal = document.getElementById('btnSaveGlobal');

        this.sectionDropdownMenu = document.getElementById('sectionDropdownMenu');
        this.sectionDropdownBtn = document.getElementById('sectionDropdownBtn');
        this.sectionVersion = document.getElementById('sectionVersion');
        this.editorCanvas = document.getElementById('editorCanvas');
        this.fusionCanvas = document.getElementById('fusionCanvas');
        this.wordCountDisplay = document.getElementById('wordCountDisplay');
        this.saveStatus = document.getElementById('saveStatus');

        this.drafterTargetSection = document.getElementById('drafterTargetSection');
        this.chatContainer = document.getElementById('chatCanvas');
        this.chatInput = document.getElementById('chatInput');
        this.btnSend = document.getElementById('btnSend');
        this.btnCancelJob = document.getElementById('btnCancelJob');
        this.targetLangSelect = document.getElementById('targetLang');
        this.importTypeSelect = document.getElementById('importType');
        this.fileInput = document.getElementById('fileInput');
        this.wordImportInput = document.getElementById('wordImportInput');
        this.filePreviewArea = document.getElementById('filePreviewArea');
        this.fileNameDisplay = document.getElementById('fileNameDisplay');
        this.importTypeBadge = document.getElementById('importTypeBadge');

        this.sectionManagerModal = document.getElementById('sectionManagerModal');
        this.sectionListContainer = document.getElementById('sectionListContainer');
        this.newSectionName = document.getElementById('newSectionName');

        this.resizer1 = document.getElementById('dragHandle1');
        this.resizer2 = document.getElementById('dragHandle2');
        this.btnExpand2A = document.getElementById('btnExpand2A');
        this.btnExpand2B = document.getElementById('btnExpand2B');

        this.fullscreenState = null;
        this.hiddenPanels = new Set();

        this.currentAttachment = null;
        this.currentImportType = '';
        this.formalProjects = [];

        const urlParams = new URLSearchParams(window.location.search);
        const hiddenPidEl = document.getElementById('initialPid');
        const hiddenPid = hiddenPidEl ? (hiddenPidEl.value || '').trim() : '';
        let rawPid = (urlParams.get('pid') || '').trim();
        if (!rawPid && hiddenPid) {
            rawPid = hiddenPid;
        }
        this.pid = rawPid;
        this.manuId = `man_${this.pid}`;

        this.sections = [
            { id: 'title', label: 'Title', is_fixed: true },
            { id: 'author', label: 'Author', is_fixed: true },
            { id: 'abstract', label: 'Abstract', is_fixed: true },
            { id: 'keyword', label: 'Keyword', is_fixed: true },
            { id: 'introduction', label: 'Introduction', is_fixed: false },
            { id: 'method', label: 'Method', is_fixed: false },
            { id: 'results', label: 'Results', is_fixed: false },
            { id: 'discussion', label: 'Discussion', is_fixed: false },
            { id: 'conclusion', label: 'Conclusion', is_fixed: false },
            { id: 'acknowledgements', label: 'Acknowledgements', is_fixed: false },
            { id: 'competing_interest', label: 'Declaration of Competing Interest', is_fixed: false },
            { id: 'reference', label: 'Reference', is_fixed: false },
            { id: 'appendix', label: 'Supplementary Materials / Appendices', is_fixed: false }
        ];

        // 實例化拆解後的子模組
        this.ui = new ManuUI(this);
        this.soed = new ManuSoed(this);
        this.imgEngine = new ManuImage(this);
        // [collab] 章節指派／留言／唯讀鎖定
        this.collab = new ManuCollab(this);
        // 章節完成比例（NOTE-036）。放在 collab 之後：render() 要問
        // collab.canWrite() 才知道欄位該不該鎖成唯讀。
        this.progress = new ManuProgress(this);

        this.init();
    }

    init() {
        const startup = () => {
            this.ui.renderSectionDropdown();
            this.ui.setupToolbarActions();
            this.soed.updateWordCount();
            if (this.editorCanvas) {
                this.editorCanvas.addEventListener('input', () => {
                    this.soed.updateWordCount();
                    // [v1.8] 停筆 1.5 秒後自動存檔（寫草稿，不產生版本）。
                    this.soed.scheduleAutosave();
                });
            }

            this.setupUnloadGuard();

            this.soed.setupSocketEvents();
            this.ui.setupResizer();
            this.soed.setupUIEvents();

            if (this.chatContainer) this.chatContainer.innerHTML = '';
            this.soed.addSystemMessage(`Fusor-Drafter Connected. Workspace ID: ${this.manuId}`);

            // [collab] 取得逐章可寫狀態，把沒有撰寫權的章節鎖成唯讀。
            this.collab.loadPermissions();

            // 章節完成比例：一次抓齊全部章節，之後切章只換顯示。
            this.progress.load();

            // [v1.8] 主動要一次主論文版本清單來填 G.Ver 選單。
            // 不做的話，選單要等到「這次工作階段有存過檔」才會有內容，
            // 使用者重新進頁面就看不到既有版本、也無從還原。
            if (this.pid && this.socket) {
                const title = this.paperTitleInput
                    ? (this.paperTitleInput.value.trim() || 'Untitled_Paper')
                    : 'Untitled_Paper';
                this.socket.emit('cmd_list_papers', { pid: this.pid, title: title });
            }

            // NOTE(NOTE-005) 這個延遲還原只負責「使用者還沒動作」時的初次定位。
            // 使用者在這 500ms 內自己切了章（甚至已開始打字）時再還原，會把畫布
            // 換成快取章節的已存版本，未存內容無聲消失。_sectionReqSeq > 0 代表
            // 已經有人正式切過章，此時還原沒有任何該做的事。
            setTimeout(() => {
                if (this.soed && this.soed._sectionReqSeq > 0) return;
                if (this.drafterTargetSection) {
                    const section = this.soed.restoreChatSection(this.drafterTargetSection.value);
                    this.soed.switchChatSection(section);
                }
            }, 500);
        };

        this.loadFormalProjects().finally(() => {
            if (!this.pid || this.pid === 'unknown') {
                this.pid = this.generateUUID();
                const newUrl = `${window.location.pathname}?pid=${encodeURIComponent(this.pid)}`;
                window.history.replaceState({ path: newUrl }, '', newUrl);
            }
            this.manuId = `man_${this.pid}`;
            if (this.toolbarPidDisplay) this.toolbarPidDisplay.innerText = this.pid;

            this.ui.bootstrapWorkspace().then((boot) => {
                if (boot && boot.pid) {
                    this.pid = boot.pid;
                    this.manuId = `man_${this.pid}`;
                    if (this.toolbarPidDisplay) this.toolbarPidDisplay.innerText = this.pid;
                    const bootUrl = new URL(window.location);
                    bootUrl.searchParams.set('pid', this.pid);
                    window.history.replaceState({}, '', bootUrl);
                }
                startup();
                if (boot && Array.isArray(boot.formal_projects) && boot.formal_projects.length > 0) {
                    this.formalProjects = boot.formal_projects;
                    this.renderFormalProjectList();
                }
                if (boot && boot.sync_status && this.soed && this.soed.addSystemMessage) {
                    const s = boot.sync_status;
                    this.soed.addSystemMessage(`Sync Status | PAQ:${s.has_paq ? 'Y' : 'N'} Literature:${s.has_literature ? 'Y' : 'N'} Study:${s.has_study ? 'Y' : 'N'}`);
                }
            }).catch(() => {
                startup();
            });
        });
    }

    generateUUID() {
        return 'proj_' + Math.random().toString(36).substr(2, 6);
    }

    loadFormalProjects() {
        const qPid = this.pid || '';
        return fetch(`/manuscript/api/formal_projects?pid=${encodeURIComponent(qPid)}`)
            .then((res) => res.json())
            .then((data) => {
                if (!data || !data.ok) return;
                this.formalProjects = Array.isArray(data.formal_projects) ? data.formal_projects : [];
                if (data.active_project) {
                    this.pid = data.active_project;
                }
                this.renderFormalProjectList();
            })
            .catch((err) => {
                console.warn('[ManuscriptWS] loadFormalProjects failed:', err);
            });
    }

    renderFormalProjectList() {
        if (!this.formalProjectList) return;
        if (!this.formalProjects || this.formalProjects.length === 0) {
            this.formalProjectList.innerHTML = '<li><span class="dropdown-item-text text-muted small">尚無正式專案</span></li>';
            return;
        }

        this.formalProjectList.innerHTML = this.formalProjects.map((p) => {
            const title = this.escapeHtml(p.research_title || p.name || p.pid || 'Untitled');
            const pid = this.escapeHtml(p.pid || '');
            const activeCls = p.pid === this.pid ? 'active' : '';
            return `
                <li>
                    <a class="dropdown-item ${activeCls}" href="#" onclick="event.preventDefault(); wsApp.switchProject('${pid}')">
                        <div class="fw-bold text-truncate">${title}</div>
                        <div class="small text-muted">${pid}</div>
                    </a>
                </li>
            `;
        }).join('');
    }

    switchProject(pid) {
        if (!pid || pid === this.pid) return;
        const url = new URL(window.location);
        url.searchParams.set('pid', pid);
        window.location.href = url.toString();
    }

    /**
     * 離開頁面前把未落地的編輯保住。
     *
     * NOTE(NOTE-029): 「偶發跳回 Dashboard」查了三輪查不到，因為**沒有錯誤可找**
     * —— 導覽列的品牌連結（`<a href="/">`，實測就浮在編輯區正上方 (12,8) 175×40）
     * 本來就是通往首頁的合法連結，誤點一次就是一次完全正常的導頁：
     * 沒有 console error、沒有失敗請求。
     *
     * 真正的缺陷是全 app 沒有任何 beforeunload 守衛，而 autosave 是 1500ms
     * debounce：實測打字後 6ms 內點下該連結，內容在磁碟上一個字都找不到。
     *
     * 為什麼用 sendBeacon 而不是補一次 `socket.emit`：unload 期間連線正在拆除，
     * emit 不保證送達；sendBeacon 由瀏覽器接手送出，不受頁面銷毀影響。
     *
     * 為什麼預設不攔截導頁：原生確認框會凍住整個 renderer（見 HANDOFF §3.8），
     * 而使用者真的想離開時只是多一次點擊。**內容不掉才是目的，攔截只是手段**，
     * 所以只有在 beacon 送不出去時才退回提示。
     */
    setupUnloadGuard() {
        if (this._unloadGuardInstalled) return;
        this._unloadGuardInstalled = true;

        window.addEventListener('beforeunload', (e) => {
            let preserved = false;
            try {
                preserved = this.flushDraftBeacon();
            } catch (err) {
                preserved = false;
            }

            // 保住了就安靜放行。保不住才擋，並且交給瀏覽器顯示它自己的提示。
            if (!preserved && this.hasUnsavedEdits()) {
                e.preventDefault();
                e.returnValue = '';
                return '';
            }
        });
    }

    /** 目前畫布是否有還沒寫進草稿的編輯（autosave 計時器還在跑就算）。 */
    hasUnsavedEdits() {
        return !!(this.soed && this.soed._autosaveTimer);
    }

    /**
     * 用 sendBeacon 把目前章節的草稿送出去。
     * @returns {boolean} 是否**已交給瀏覽器送出**（不代表伺服器已寫入）。
     */
    flushDraftBeacon() {
        if (!this.pid || !navigator.sendBeacon) return false;
        if (!this.hasUnsavedEdits()) return false;
        if (!this.editorCanvas) return false;

        const cards = this.editorCanvas.querySelectorAll('.editor-card');
        if (!cards.length) return false;

        const title = this.paperTitleInput
            ? this.paperTitleInput.value.trim()
            : 'Untitled_Paper';

        let queued = false;
        cards.forEach((card) => {
            const body = card.querySelector('.card-content');
            if (!body) return;
            const payload = JSON.stringify({
                pid: this.pid,
                title: title,
                section: card.getAttribute('data-section') || 'general',
                content: body.innerHTML,
            });
            // sendBeacon 不能設自訂標頭；端點因此用 force=True 自行解析 JSON，
            // 並自己做授權（帶不了 CSRF token，見 NOTE-029）。
            const blob = new Blob([payload], { type: 'application/json' });
            if (navigator.sendBeacon('/manuscript/api/draft/flush', blob)) {
                queued = true;
            }
        });
        // 已經交出去就取消計時器，避免 unload 途中又排一次沒有意義的 socket emit。
        if (queued && this.soed) clearTimeout(this.soed._autosaveTimer);
        return queued;
    }

    escapeHtml(val) {
        return String(val || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // =========================================================================
    // Proxy Methods (代理轉發)
    // =========================================================================
    hidePanel(id) { this.ui.hidePanel(id); }
    restorePanel(id) { this.ui.restorePanel(id); }
    fullscreenPanel(id) { this.ui.fullscreenPanel(id); }

    openAssetGallery(targetCanvas) { this.ui.openAssetGallery(targetCanvas); }
    insertAssetToCanvas(htmlContent) { this.ui.insertAssetToCanvas(htmlContent); }

    importToEditor(content) { this.soed.importToEditor(content); }
    cardActionPushToFusion(btn) { this.soed.cardActionPushToFusion(btn); }
    cardActionSave(btn) { this.soed.cardActionSave(btn); }
    cardActionContext(btn) { this.soed.cardActionContext(btn); }
    saveAllBlocks() { this.soed.saveAllBlocks(); }

    openOldPaperFlow() { this.soed.openOldPaperFlow(); }
    openOldBlockFlow() { this.soed.openOldBlockFlow(); }
    fetchOldBlocks() { this.soed.fetchOldBlocks(); }
    createNewSection(secId) { this.soed.createNewSection(secId); }

    openSectionManager() { this.ui.openSectionManager(); }
    updateSectionLabel(idx, val) { this.ui.updateSectionLabel(idx, val); }
    moveSectionItem(idx, dir) { this.ui.moveSectionItem(idx, dir); }
    addSectionItem() { this.ui.addSectionItem(); }
    removeSectionItem(idx) { this.ui.removeSectionItem(idx); }
    saveSectionConfig() { this.ui.saveSectionConfig(); }

    triggerAutoDraft() { this.soed.triggerAutoDraft(); }
    cancelActiveJob() { this.soed.cancelActiveJob(); }
    triggerFileUpload() { this.ui.triggerFileUpload(); }
    importWordToCanvas(targetCanvas) { this.ui.triggerWordImport(targetCanvas); }
    triggerGoogleDrive() { this.ui.triggerGoogleDrive(); }
    clearFile() { this.ui.clearFile(); }

    toggleSectionSelection(id, isChecked, el) { this.ui.toggleSectionSelection(id, isChecked, el); }
}

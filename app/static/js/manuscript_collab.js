// Roothinks source maintenance contract
// 檔案路徑: app/static/js/manuscript_collab.js
// 驗證: node --check app/static/js/manuscript_collab.js
//路徑(./app/static/js/manuscript_collab.js)
//版本 v1.3
//更版時間 20260727-1400
// v1.3 角色語意變更（依產品決策）：
//   - 限定編輯（coauthor）改為「只讀得到也只寫得到被指派的章節」，
//     讀取範圍比 viewer 還窄，因此不能用角色階梯線性推導讀取權。
//   - 新增 owner 專屬開關 coauthor_open_access：開啟後限定編輯可檢視
//     全部章節與 2C 全篇並對任何章節留言，但**寫入權不變**。
// v1.1 修正（皆由實機操作截圖發現）：
//   - openComments 原本用 display='' 還原，元素會退回 stylesheet 預設的 block，
//     side panel 的 flex 直向佈局失效，輸入框被擠到面板頂端。改為明確設 flex。
//   - 新增遮罩層與 Esc 關閉：側欄是覆蓋在三欄工作區之上的，沒有遮罩時
//     使用者分不出它是浮層還是版面的一部分。
//   - 新增工具列留言數徽章：先前必須逐章開側欄才知道哪裡有人留言。
// v1.2 修正：
//   - refreshLock 原本只鎖 .editor-card，但 #editorCanvas 本身即為 contenteditable，
//     且空章節載入的是 .section-block —— 沒有寫入權的人仍可直接在畫布打字，
//     要到存檔被拒才知道（自動存檔更是靜默略過）。改為連畫布一起鎖並顯示唯讀橫幅。
// 模組定位:
//   Manuscript 章節協作前端：章節指派、章節留言、無權章節唯讀鎖定。
// 主要責任:
//   1. 載入 /manuscript/api/chapter/<pid>/my-permissions，把無權章節鎖成唯讀。
//   2. 章節指派介面（editor 以上可見）。
//   3. 章節留言側欄：所有專案成員皆可留言，含未被指派該章節的 coauthor。
// 呼叫來源:
//   manuscript_workspace.html；由 ManuscriptWorkspace 於啟動時建立實例。
// 輸入輸出契約:
//   全部走 /manuscript/api/chapter/* JSON API，帶 session cookie。
// 安全邊界:
//   - 這裡的唯讀鎖定純為體驗優化，真正把關在伺服器端 can_write_section。
//     絕不可把前端判斷當成權限依據。
// 維護提醒:
//   - permissions.sections 是 { section_key: bool }；未列出的章節視為不可寫。
//   - 切換章節後必須重新套用鎖定（refreshLock），否則會沿用上一章的狀態。
// ---------------------------------------------------------------------------

class ManuCollab {
    constructor(app) {
        this.app = app;
        this.permissions = null;   // { role, can_assign, can_comment, sections: {} }
        this.comments = [];
    }

    // -- 權限 ---------------------------------------------------------------

    async loadPermissions() {
        if (!this.app.pid) return;
        try {
            const res = await fetch(
                `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/my-permissions`
            );
            if (!res.ok) {
                // 403 代表不是專案成員；此時不鎖定畫面，交由既有守衛處理導向。
                this.permissions = null;
                return;
            }
            this.permissions = await res.json();
            this._renderRoleBadge();
            this.refreshLock();
            this.refreshCommentBadge();
            // NOTE(NOTE-036) 完成比例欄位的唯讀狀態取決於 canWrite()，而權限是
            // 非同步來的。progress.load() 與這支是並行的，先回來的那一個看到的
            // permissions 還是 null（canWrite 一律回 true），於是 viewer 的欄位
            // 會停在「可編輯」的樣子。伺服器仍會擋（403），但畫面在騙人。
            if (this.app.progress) this.app.progress.render();
        } catch (err) {
            console.warn('[collab] 權限載入失敗', err);
            this.permissions = null;
        }
    }

    canWrite(sectionId) {
        // 權限尚未載入（或單機模式）時不阻擋，避免誤鎖住正常使用者。
        if (!this.permissions || !this.permissions.sections) return true;
        return this.permissions.sections[sectionId] === true;
    }

    _renderRoleBadge() {
        const el = document.getElementById('collabRoleBadge');
        if (!el || !this.permissions) return;
        const labels = {
            owner: '擁有者', editor: '總編輯', coauthor: '限定編輯', viewer: '檢視者',
        };
        const role = this.permissions.role;
        if (!role) { el.textContent = ''; return; }
        el.textContent = labels[role] || role;
        el.className = 'badge ' + (role === 'coauthor' ? 'bg-info text-dark' : 'bg-secondary');

        const assignBtn = document.getElementById('btnChapterAssign');
        if (assignBtn) {
            assignBtn.style.display = this.permissions.can_assign ? '' : 'none';
        }
    }

    // -- 在線協作者 ----------------------------------------------------------

    /**
     * 顯示目前還有誰在線上。
     *
     * 為什麼需要：同一章節可以有多人同時寫入——總編輯與 owner 依定義可寫所有
     * 章節，同一章節也能指派給多位限定編輯（實測同章可同時有 5 人有寫入權）。
     * 撞在一起不會壞（草稿每人一份、版本各自保留），但雙方互不知情，最後會
     * 各自存出不同版本再人工合併。看到「還有人在」就會先去問一聲。
     *
     * 刻意只顯示在線，不顯示誰在改哪一章：章節層級的狀態會把「誰被指派了哪章」
     * 洩漏給看不到該章的限定編輯，產品上也不需要那麼細。
     */
    renderPresence(data) {
        const chip = document.getElementById('presenceChip');
        if (!chip) return;

        const others = ((data && data.users) || [])
            .filter(u => String(u.user_id) !== String(this.myUserId()));

        if (!others.length) {
            chip.style.display = 'none';
            chip.textContent = '';
            chip.title = '';   // 一併清掉，否則會殘留上一次的名單
            return;
        }
        const names = others.map(u => this._esc(u.name)).join('、');
        chip.style.display = '';
        chip.innerHTML = `<i class="bi bi-people-fill me-1"></i>${others.length} 人在線`;
        chip.title = `目前也在這個專案裡：${names}`;
    }

    /** 目前登入者 id。優先讀模板寫入的隱藏欄位——presence_update 可能早於
     *  my-permissions 回來，那時 this.permissions 還是 null，會把自己也算進在線名單。 */
    myUserId() {
        const el = document.getElementById('currentUserId');
        const fromDom = el ? (el.value || '').trim() : '';
        if (fromDom) return fromDom;
        return (this.permissions && this.permissions.user_id) || null;
    }

    _esc(v) {
        return String(v ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    /**
     * 依權限把編輯區鎖成唯讀。
     * 無權章節仍看得到內容，只是不能改——這正是「其他章節唯讀但可留言」的呈現。
     *
     * 必須同時鎖「畫布本體」與「卡片」兩層：
     * #editorCanvas 自己就是 contenteditable=true，而空章節載入的是 .section-block
     * 而非 .editor-card。只鎖卡片的話，沒有寫入權的人仍能直接在畫布上打字，
     * 一直要到存檔被伺服器拒絕才知道——自動存檔更是連錯誤都不會跳。
     */
    refreshLock() {
        if (!this.app.editorCanvas) return;

        this._lockCanvas();

        this.app.editorCanvas.querySelectorAll('.editor-card').forEach(card => {
            const sectionId = card.getAttribute('data-section') || 'general';
            const writable = this.canWrite(sectionId);
            const body = card.querySelector('.card-content');
            if (body) body.setAttribute('contenteditable', writable ? 'true' : 'false');

            const saveBtn = card.querySelector('button[title="Save Block"]');
            if (saveBtn) {
                saveBtn.disabled = !writable;
                saveBtn.title = writable ? 'Save Block' : '你沒有此章節的撰寫權限';
            }

            let hint = card.querySelector('.collab-readonly-hint');
            if (!writable && !hint) {
                hint = document.createElement('div');
                hint.className = 'collab-readonly-hint badge bg-warning text-dark mb-2 ms-1';
                hint.textContent = '唯讀 · 可留言';
                card.prepend(hint);
            } else if (writable && hint) {
                hint.remove();
            }
        });
    }

    /**
     * 依「目前檢視的章節」鎖定整個編輯畫布，並在上方顯示醒目的唯讀橫幅。
     *
     * 橫幅是必要的：單純把 contenteditable 關掉，使用者只會覺得「打字沒反應」，
     * 不會知道原因，也不知道還能用留言表達意見。
     */
    _lockCanvas() {
        const canvas = this.app.editorCanvas;
        const section = this.currentSection();
        const writable = this.canWrite(section);

        canvas.setAttribute('contenteditable', writable ? 'true' : 'false');
        canvas.style.background = writable ? '' : '#f8f9fa';

        const bannerId = 'collabCanvasReadonlyBanner';
        let banner = document.getElementById(bannerId);

        if (writable) {
            if (banner) banner.remove();
            return;
        }
        if (!banner) {
            banner = document.createElement('div');
            banner.id = bannerId;
            banner.className = 'alert alert-warning py-2 px-3 mb-0 small d-flex '
                             + 'justify-content-between align-items-center';
            canvas.parentNode.insertBefore(banner, canvas);
        }
        const role = (this.permissions && this.permissions.role) || '';
        const openAccess = !!(this.permissions && this.permissions.open_access);
        let why;
        if (role === 'viewer') {
            why = '你在本專案是檢視者，可檢視所有章節並留言，但不能編輯。';
        } else if (role === 'coauthor' && openAccess) {
            // 開放後限定編輯看得到別章但仍不能改——要講清楚，否則會以為壞掉。
            why = `「${section}」不是指派給你的章節，可檢視與留言但無法編輯。`;
        } else {
            why = `「${section}」這個章節沒有指派給你，因此無法編輯。`;
        }
        banner.innerHTML =
            `<span><i class="bi bi-lock-fill me-1"></i>唯讀模式 · ${why}</span>`;

        const btn = document.createElement('button');
        btn.className = 'btn btn-sm btn-outline-dark ms-2';
        btn.innerHTML = '<i class="bi bi-chat-left-text me-1"></i>改用留言提意見';
        btn.onclick = () => this.openComments();
        banner.appendChild(btn);
    }

    // -- 章節指派 ------------------------------------------------------------

    async openAssignModal() {
        const modalEl = document.getElementById('chapterAssignModal');
        if (!modalEl) return;
        await this.refreshAssignments();
        this._fillAssignSectionOptions();
        await this.refreshOpenAccess();
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }

    /** 讀取並顯示 owner 的「開放限定編輯檢視全文」開關。非 owner 不顯示。 */
    async refreshOpenAccess() {
        const box = document.getElementById('collabOpenAccessBox');
        const toggle = document.getElementById('collabOpenAccessToggle');
        const meta = document.getElementById('collabOpenAccessMeta');
        if (!box || !toggle) return;
        try {
            const res = await fetch(
                `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/settings`
            );
            if (!res.ok) { box.style.display = 'none'; return; }
            const data = await res.json();
            box.style.display = data.can_toggle ? '' : 'none';
            toggle.checked = !!data.coauthor_open_access;
            if (meta) {
                meta.textContent = data.updated_by
                    ? `最後變更：${data.updated_by} · ${new Date(data.updated_at).toLocaleString()}`
                    : '';
            }
        } catch (err) {
            box.style.display = 'none';
        }
    }

    /**
     * 切換開放開關（owner 專屬）。
     * 切換後必須重載權限與章節清單 —— 限定編輯看得到的章節數量會即時改變。
     */
    async toggleOpenAccess(enabled) {
        try {
            const res = await fetch(
                `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/settings`,
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ coauthor_open_access: !!enabled }),
                }
            );
            const data = await res.json();
            if (!data.success) {
                alert(data.message || '沒有權限變更此設定（僅擁有者可調整）。');
                await this.refreshOpenAccess();
                return;
            }
            await this.refreshOpenAccess();
            // 章節清單由伺服器依讀取範圍過濾，開關一改可見章節數就變，
            // 必須重抓 bootstrap 並重畫下拉，否則畫面停在舊的章節集合。
            if (this.app.ui && this.app.ui.bootstrapWorkspace) {
                await this.app.ui.bootstrapWorkspace();
                this.app.ui.renderSectionDropdown();
            }
            await this.loadPermissions();
        } catch (err) {
            alert('設定變更失敗。');
            await this.refreshOpenAccess();
        }
    }

    _fillAssignSectionOptions() {
        const sel = document.getElementById('assignSectionSelect');
        if (!sel) return;
        sel.innerHTML = '';
        (this.app.sections || []).forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.label || s.id;
            sel.appendChild(opt);
        });
    }

    async refreshAssignments() {
        const list = document.getElementById('chapterAssignList');
        if (!list) return;
        list.innerHTML = '<div class="text-muted small p-2">載入中…</div>';
        try {
            const res = await fetch(
                `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/assignments`
            );
            const data = await res.json();
            if (!data.success) {
                list.innerHTML = `<div class="text-danger small p-2">${data.message || '載入失敗'}</div>`;
                return;
            }
            if (!data.assignments.length) {
                list.innerHTML = '<div class="text-muted small p-2">尚無章節指派。</div>';
                return;
            }
            const canAssign = this.permissions && this.permissions.can_assign;
            list.innerHTML = '';
            data.assignments.forEach(a => {
                const row = document.createElement('div');
                row.className = 'list-group-item d-flex justify-content-between align-items-center py-2';
                row.innerHTML = `
                    <span><span class="badge bg-primary me-2">${a.section_key}</span>
                    ${a.username || ''} <span class="text-muted small">${a.email || ''}</span></span>`;
                if (canAssign) {
                    const btn = document.createElement('button');
                    btn.className = 'btn btn-sm btn-outline-danger';
                    btn.innerHTML = '<i class="bi bi-x-lg"></i>';
                    btn.onclick = () => this.removeAssignment(a.id);
                    row.appendChild(btn);
                }
                list.appendChild(row);
            });
        } catch (err) {
            list.innerHTML = '<div class="text-danger small p-2">載入失敗</div>';
        }
    }

    async createAssignment() {
        const emailEl = document.getElementById('assignEmailInput');
        const sectionEl = document.getElementById('assignSectionSelect');
        const errEl = document.getElementById('chapterAssignError');
        if (!emailEl || !sectionEl) return;

        if (errEl) errEl.style.display = 'none';
        try {
            const res = await fetch(
                `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/assignments`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        section_key: sectionEl.value,
                        email: emailEl.value.trim(),
                    }),
                }
            );
            const data = await res.json();
            if (!data.success) {
                if (errEl) {
                    errEl.textContent = data.message || '指派失敗';
                    errEl.style.display = '';
                }
                return;
            }
            emailEl.value = '';
            await this.refreshAssignments();
            await this.loadPermissions();
        } catch (err) {
            if (errEl) { errEl.textContent = '指派失敗'; errEl.style.display = ''; }
        }
    }

    async removeAssignment(assignmentId) {
        if (!confirm('確定要取消這筆章節指派嗎？')) return;
        await fetch(
            `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/assignments/${assignmentId}`,
            { method: 'DELETE' }
        );
        await this.refreshAssignments();
        await this.loadPermissions();
    }

    // -- 章節留言 ------------------------------------------------------------

    currentSection() {
        return Array.from(this.app.selectedSections || [])[0] || 'general';
    }

    /**
     * 2B 目前檢視的段落版本（S.Ver），如 "0.5"；沒有正式版本時回 null。
     *
     * 讀 S.Ver 選單而不是自己記狀態：選單本身就是「目前正在看哪一版」的
     * 唯一事實來源，切章與載入舊版都會更新它（見 manuscript_soed.js 的
     * block_loaded 處理）。
     */
    currentSVer() {
        const sel = this.app.sectionVersion;
        const value = sel && sel.value ? String(sel.value).trim() : '';
        return value || null;
    }

    async openComments() {
        const panel = document.getElementById('chapterCommentPanel');
        if (!panel) return;
        // 必須明確設成 flex。先前用 display='' 只是清掉 inline 樣式，
        // 元素會退回 stylesheet 預設的 block，側欄的 flex 直向佈局隨即失效
        // ——列表不再撐開，輸入框被擠到面板頂端。
        panel.style.display = 'flex';
        const backdrop = document.getElementById('chapterCommentBackdrop');
        if (backdrop) backdrop.style.display = 'block';
        this._bindEscToClose();
        await this.refreshComments();
        const input = document.getElementById('chapterCommentInput');
        if (input) input.focus();
    }

    closeComments() {
        const panel = document.getElementById('chapterCommentPanel');
        if (panel) panel.style.display = 'none';
        const backdrop = document.getElementById('chapterCommentBackdrop');
        if (backdrop) backdrop.style.display = 'none';
    }

    /** 側欄是覆蓋在工作區之上的，給 Esc 一條退路才不會逼使用者去找關閉鈕。 */
    _bindEscToClose() {
        if (this._escBound) return;
        this._escBound = true;
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            const panel = document.getElementById('chapterCommentPanel');
            if (panel && panel.style.display === 'flex') this.closeComments();
        });
    }

    /**
     * 在工具列的留言按鈕上顯示該章節的留言數。
     *
     * 沒有這個提示的話，使用者必須逐章打開側欄才知道哪裡有人留了話 ——
     * 這是協作情境最常被忽略的一環。
     */
    async refreshCommentBadge() {
        const btn = document.getElementById('btnChapterComments');
        if (!btn || !this.app.pid) return;
        let badge = btn.querySelector('.collab-comment-count');
        try {
            const res = await fetch(
                `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/comments`
                + `?section=${encodeURIComponent(this.currentSection())}`
            );
            if (!res.ok) return;
            const data = await res.json();
            const open = (data.comments || []).filter(c => !c.resolved).length;

            if (!open) {
                if (badge) badge.remove();
                btn.classList.remove('btn-outline-primary');
                btn.classList.add('btn-outline-secondary');
                return;
            }
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'collab-comment-count badge bg-danger ms-1';
                btn.appendChild(badge);
            }
            badge.textContent = open;
            btn.classList.remove('btn-outline-secondary');
            btn.classList.add('btn-outline-primary');
        } catch (err) {
            /* 留言數只是提示，取不到就維持原樣 */
        }
    }

    async refreshComments() {
        const list = document.getElementById('chapterCommentList');
        const title = document.getElementById('chapterCommentSection');
        if (!list) return;
        const section = this.currentSection();
        if (title) title.textContent = section;

        list.innerHTML = '<div class="text-muted small">載入中…</div>';
        try {
            // NOTE(NOTE-010) 依「章節 + 目前檢視版本」取留言，切版本就換一組。
            // include_legacy 讓本次改動之前、沒有版本定位的舊留言仍看得到；
            // 它們在清單上會標成「未標版本」，不會被誤讀成針對現版的意見。
            const sVer = this.currentSVer();
            const res = await fetch(
                `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/comments`
                + `?section=${encodeURIComponent(section)}`
                + (sVer ? `&s_ver=${encodeURIComponent(sVer)}` : '')
                + `&include_legacy=1`
            );
            const data = await res.json();
            if (!data.success) {
                list.innerHTML = `<div class="text-danger small">${data.message || '載入失敗'}</div>`;
                return;
            }
            this.comments = data.comments || [];
            if (!this.comments.length) {
                list.innerHTML = '<div class="text-muted small">這個章節還沒有留言。</div>';
                return;
            }
            list.innerHTML = '';
            this.comments.forEach(c => list.appendChild(this._commentNode(c)));
        } catch (err) {
            list.innerHTML = '<div class="text-danger small">載入失敗</div>';
        }
    }

    _commentNode(c) {
        const wrap = document.createElement('div');
        wrap.className = 'border rounded p-2 mb-2' + (c.resolved ? ' bg-light opacity-75' : '');
        const when = c.created_at ? new Date(c.created_at).toLocaleString() : '';
        // 版本標籤必須顯示：同一章不同版的意見會並存，看不到版本就無從判斷
        // 這句話在講哪一版。舊留言沒有版本定位，明講「未標版本」而不是留白，
        // 留白會被讀成「針對現在這一版」。
        const verLabel = c.legacy_unversioned
            ? '未標版本'
            : (c.scope === 'paper' ? `G.Ver ${c.g_ver}` : `S.Ver ${c.s_ver}`);
        const verClass = c.legacy_unversioned ? 'bg-warning-subtle text-warning-emphasis' : 'bg-light text-secondary';
        wrap.innerHTML = `
            <div class="d-flex justify-content-between align-items-start">
                <div class="small fw-bold">${c.author || '（已移除的帳號）'}</div>
                <div class="text-muted" style="font-size:0.72rem;">${when}</div>
            </div>
            <div><span class="badge ${verClass} fw-normal" style="font-size:0.66rem;">${this._escape(verLabel)}</span></div>
            <div class="small mt-1" style="white-space:pre-wrap;">${this._escape(c.body)}</div>`;

        const actions = document.createElement('div');
        actions.className = 'mt-1 d-flex gap-2';

        const toggle = document.createElement('button');
        toggle.className = 'btn btn-sm btn-link p-0 small';
        toggle.textContent = c.resolved ? '重新開啟' : '標記已解決';
        toggle.onclick = () => this.setResolved(c.id, !c.resolved);
        actions.appendChild(toggle);

        const del = document.createElement('button');
        del.className = 'btn btn-sm btn-link p-0 small text-danger';
        del.textContent = '刪除';
        del.onclick = () => this.deleteComment(c.id);
        actions.appendChild(del);

        wrap.appendChild(actions);
        return wrap;
    }

    /** 留言內容以純文字呈現，避免他人留言中的 HTML 被當標籤執行。 */
    _escape(text) {
        const div = document.createElement('div');
        div.textContent = String(text == null ? '' : text);
        return div.innerHTML;
    }

    async postComment() {
        const input = document.getElementById('chapterCommentInput');
        if (!input) return;
        const body = input.value.trim();
        if (!body) return;

        const res = await fetch(
            `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/comments`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // NOTE(NOTE-010) 送出目前檢視的 S.Ver：留言在建立當下就綁死版本，
                // 之後 2B 存成新版也不會把這句意見帶過去。
                body: JSON.stringify({
                    section_key: this.currentSection(),
                    s_ver: this.currentSVer(),
                    body: body,
                }),
            }
        );
        const data = await res.json();
        if (!data.success) {
            alert(data.message || '留言失敗');
            return;
        }
        input.value = '';
        await this.refreshComments();
        this.refreshCommentBadge();
    }

    async setResolved(commentId, resolved) {
        const res = await fetch(
            `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/comments/${commentId}`,
            {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ resolved: resolved }),
            }
        );
        if (!res.ok) { alert('沒有權限變更這則留言。'); return; }
        await this.refreshComments();
        this.refreshCommentBadge();
    }

    async deleteComment(commentId) {
        if (!confirm('確定要刪除這則留言嗎？')) return;
        const res = await fetch(
            `/manuscript/api/chapter/${encodeURIComponent(this.app.pid)}/comments/${commentId}`,
            { method: 'DELETE' }
        );
        if (!res.ok) { alert('沒有權限刪除這則留言。'); return; }
        await this.refreshComments();
        this.refreshCommentBadge();
    }
}

window.ManuCollab = ManuCollab;

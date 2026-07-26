//路徑(./app/static/js/mentor.js)
//版本 v1.0
//更版時間 20260726-0425
// 模組定位:
//   Mentor 儀表板前端：mentee 清單、活動統計、專案／手稿／筆記檢視、派工與評論。
// 主要責任:
//   1. 載入 /api/mentor/mentees 與單一 mentee 詳情。
//   2. 分頁切換與各分頁的渲染。
//   3. 建立／更新／刪除交付工作，新增評論。
// 呼叫來源:
//   app/templates/mentor/dashboard.html
// 輸入輸出契約:
//   全部走 /api/mentor/* JSON API，帶 session cookie。
// 安全邊界:
//   - 所有 mentee 資料為唯讀呈現；越權由後端 _require_mentee 擋，
//     前端不做也不能做權限判斷。
//   - 所有來自後端的文字一律以 escape() 輸出，避免 mentee 的筆記或留言
//     內容中的 HTML 被當標籤執行。
// 維護提醒:
//   - Study 筆記按專案歸戶（檔案沒有作者欄位），UI 上必須保留該提示文字。
// ---------------------------------------------------------------------------

class MentorDashboard {
    constructor() {
        this.mentees = [];
        this.current = null;      // 目前選取的 mentee 詳情
        this.currentId = null;
        this.tab = 'overview';
    }

    async init() {
        this._bindTabs();
        await this.loadMentees();
    }

    // -- 工具 ---------------------------------------------------------------

    escape(text) {
        const div = document.createElement('div');
        div.textContent = String(text == null ? '' : text);
        return div.innerHTML;
    }

    showError(msg) {
        const el = document.getElementById('mentorError');
        if (!el) return;
        el.textContent = msg;
        el.style.display = msg ? '' : 'none';
    }

    fmtMinutes(minutes) {
        const m = Number(minutes || 0);
        if (m < 60) return m.toFixed(1) + ' 分鐘';
        return (m / 60).toFixed(1) + ' 小時';
    }

    fmtDate(iso) {
        if (!iso) return '—';
        try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
    }

    _bindTabs() {
        const tabs = document.getElementById('mentorTabs');
        if (!tabs) return;
        tabs.addEventListener('click', (e) => {
            const link = e.target.closest('[data-tab]');
            if (!link) return;
            e.preventDefault();
            tabs.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));
            link.classList.add('active');
            this.tab = link.getAttribute('data-tab');
            this.renderTab();
        });
    }

    // -- mentee 清單 --------------------------------------------------------

    async loadMentees() {
        const list = document.getElementById('menteeList');
        try {
            const res = await fetch('/api/mentor/mentees');
            const data = await res.json();
            if (!data.success) { this.showError(data.message || '載入失敗'); return; }
            this.mentees = data.mentees || [];
            document.getElementById('menteeCount').textContent = this.mentees.length;

            if (!this.mentees.length) {
                list.innerHTML = '<div class="text-muted small p-3">'
                    + '尚未加入任何指導對象。用右上角的欄位輸入對方的 Email 加入。</div>';
                return;
            }
            list.innerHTML = '';
            this.mentees.forEach(m => list.appendChild(this._menteeRow(m)));
        } catch (err) {
            list.innerHTML = '<div class="text-danger small p-3">載入失敗</div>';
        }
    }

    _menteeRow(m) {
        const a = document.createElement('a');
        a.href = '#';
        a.className = 'list-group-item list-group-item-action';
        const online = m.stats && m.stats.online_now
            ? '<span class="badge bg-success ms-1">在線</span>' : '';
        a.innerHTML = `
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <div class="fw-bold small">${this.escape(m.mentee_username)}${online}</div>
                    <div class="text-muted" style="font-size:.72rem;">${this.escape(m.mentee_email)}</div>
                </div>
                ${m.open_tasks ? `<span class="badge bg-warning text-dark">${m.open_tasks}</span>` : ''}
            </div>
            <div class="text-muted mt-1" style="font-size:.72rem;">
                上線 ${m.stats ? m.stats.login_count : 0} 次 ·
                ${this.fmtMinutes(m.stats ? m.stats.total_minutes : 0)} ·
                ${m.project_count} 個專案
            </div>`;
        a.onclick = (e) => { e.preventDefault(); this.selectMentee(m.mentee_id); };
        return a;
    }

    async addMentee() {
        const input = document.getElementById('menteeEmailInput');
        const email = (input.value || '').trim();
        if (!email) return;
        this.showError('');

        const res = await fetch('/api/mentor/mentees', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email }),
        });
        const data = await res.json();
        if (!data.success) { this.showError(data.message || '加入失敗'); return; }
        input.value = '';
        await this.loadMentees();
    }

    async removeMentee(menteeId) {
        if (!confirm('確定要解除這位指導對象的歸屬嗎？（不會刪除對方的資料）')) return;
        await fetch(`/api/mentor/mentees/${menteeId}`, { method: 'DELETE' });
        this.current = null;
        this.currentId = null;
        document.getElementById('mentorTabs').style.display = 'none';
        document.getElementById('menteeTitle').textContent = '請從左側選擇一位指導對象';
        document.getElementById('mentorTabBody').innerHTML =
            '<div class="text-muted small">尚未選擇指導對象。</div>';
        await this.loadMentees();
    }

    // -- 詳情 ---------------------------------------------------------------

    async selectMentee(menteeId) {
        this.currentId = menteeId;
        const body = document.getElementById('mentorTabBody');
        body.innerHTML = '<div class="text-muted small">載入中…</div>';

        const res = await fetch(`/api/mentor/mentees/${menteeId}`);
        if (!res.ok) {
            body.innerHTML = '<div class="text-danger small">沒有權限檢視這位使用者。</div>';
            return;
        }
        this.current = await res.json();

        document.getElementById('mentorTabs').style.display = '';
        const m = this.current.mentee;
        document.getElementById('menteeTitle').innerHTML =
            `<span class="fw-bold">${this.escape(m.username)}</span>
             <span class="text-muted small ms-2">${this.escape(m.email)}</span>
             <button class="btn btn-sm btn-outline-danger float-end"
                     onclick="mentorApp.removeMentee(${m.id})">解除歸屬</button>`;
        this.renderTab();
    }

    renderTab() {
        if (!this.current) return;
        const body = document.getElementById('mentorTabBody');
        const render = {
            overview: () => this._renderOverview(),
            projects: () => this._renderProjects(),
            notes: () => this._renderNotes(),
            tasks: () => this._renderTasks(body),
            comments: () => this._renderComments(body),
        }[this.tab];
        const html = render ? render() : '';
        if (typeof html === 'string') body.innerHTML = html;
    }

    _renderOverview() {
        const s = this.current.stats || {};
        const sessions = this.current.sessions || [];
        const rows = sessions.map(x => `
            <tr>
                <td class="small">${this.fmtDate(x.login_at)}</td>
                <td class="small">${this.fmtDate(x.last_seen_at)}</td>
                <td class="small">${(x.duration_sec / 60).toFixed(1)} 分</td>
                <td class="small">${x.active ? '<span class="badge bg-success">在線</span>' : '已結束'}</td>
            </tr>`).join('');

        return `
            <div class="row g-3 mb-3">
                ${this._statCard('上線次數', s.login_count || 0, 'bi-box-arrow-in-right')}
                ${this._statCard('累計停留', this.fmtMinutes(s.total_minutes), 'bi-clock-history')}
                ${this._statCard('平均每次', this.fmtMinutes(s.avg_minutes), 'bi-hourglass-split')}
                ${this._statCard('最近上線', this.fmtDate(s.last_login), 'bi-calendar-check')}
            </div>
            <h6 class="fw-bold small mt-4">最近的登入紀錄</h6>
            <table class="table table-sm table-hover">
                <thead><tr>
                    <th class="small">登入時間</th><th class="small">最後活動</th>
                    <th class="small">停留</th><th class="small">狀態</th>
                </tr></thead>
                <tbody>${rows || '<tr><td colspan="4" class="text-muted small">尚無紀錄。</td></tr>'}</tbody>
            </table>`;
    }

    _statCard(label, value, icon) {
        return `
            <div class="col-6 col-md-3">
                <div class="border rounded p-3 h-100">
                    <div class="text-muted small"><i class="bi ${icon} me-1"></i>${label}</div>
                    <div class="fw-bold fs-5 mt-1">${this.escape(value)}</div>
                </div>
            </div>`;
    }

    _renderProjects() {
        const projects = this.current.projects || [];
        const revisions = this.current.revisions || [];

        const projectRows = projects.map(p => `
            <tr>
                <td class="small"><code>${this.escape(p.pid)}</code></td>
                <td class="small">${this.escape(p.name || '—')}</td>
                <td class="small"><span class="badge bg-secondary">${this.escape(p.role)}</span></td>
                <td class="small">${this.escape(p.status || '—')}</td>
            </tr>`).join('');

        const revRows = revisions.map(r => `
            <tr>
                <td class="small">${this.fmtDate(r.created_at)}</td>
                <td class="small"><code>${this.escape(r.pid)}</code></td>
                <td class="small">${this.escape(r.entity_ref)}</td>
                <td class="small">${this.escape(r.summary || '')}</td>
            </tr>`).join('');

        return `
            <h6 class="fw-bold small">參與的專案</h6>
            <table class="table table-sm">
                <thead><tr><th class="small">PID</th><th class="small">名稱</th>
                <th class="small">角色</th><th class="small">狀態</th></tr></thead>
                <tbody>${projectRows || '<tr><td colspan="4" class="text-muted small">尚未參與任何專案。</td></tr>'}</tbody>
            </table>

            <h6 class="fw-bold small mt-4">手稿異動紀錄（最近 50 筆）</h6>
            <table class="table table-sm table-hover">
                <thead><tr><th class="small">時間</th><th class="small">專案</th>
                <th class="small">對象</th><th class="small">摘要</th></tr></thead>
                <tbody>${revRows || '<tr><td colspan="4" class="text-muted small">尚無手稿異動。</td></tr>'}</tbody>
            </table>`;
    }

    _renderNotes() {
        const notes = this.current.study_notes || [];
        if (!notes.length) {
            return '<div class="text-muted small">沒有可檢視的 Study 筆記。</div>';
        }
        const blocks = notes.map(n => {
            const noteText = n.note && n.note.text
                ? `<pre class="small bg-light p-2 rounded" style="white-space:pre-wrap;">${this.escape(n.note.text)}</pre>`
                : '<div class="text-muted small">（此專案沒有筆記內容）</div>';
            const convs = (n.conversations || []).map(c => `
                <details class="mb-2">
                    <summary class="small">
                        ${this.escape(c.title || c.conv_id)}
                        <span class="text-muted">· ${c.message_count} 則 · ${this.fmtDate(c.updated_at)}</span>
                    </summary>
                    <div class="ps-3 pt-2">
                        ${(c.messages || []).map(msg => `
                            <div class="mb-2">
                                <span class="badge ${msg.role === 'user' ? 'bg-primary' : 'bg-secondary'}">${this.escape(msg.role)}</span>
                                <div class="small mt-1" style="white-space:pre-wrap;">${this.escape(msg.content)}</div>
                            </div>`).join('')}
                    </div>
                </details>`).join('');

            return `
                <div class="border rounded p-3 mb-3">
                    <div class="fw-bold small mb-2"><code>${this.escape(n.pid)}</code></div>
                    ${noteText}
                    <div class="fw-bold small mt-3 mb-1">學習對話</div>
                    ${convs || '<div class="text-muted small">（沒有對話紀錄）</div>'}
                </div>`;
        }).join('');

        return `
            <div class="alert alert-info py-2 small">
                <i class="bi bi-info-circle me-1"></i>
                Study 筆記與對話是<strong>按專案</strong>儲存的，檔案本身沒有作者欄位，
                因此同一專案若有多位成員，內容無法分辨是誰所寫。
            </div>
            ${blocks}`;
    }

    // -- 交付工作 -----------------------------------------------------------

    async _renderTasks(body) {
        body.innerHTML = '<div class="text-muted small">載入中…</div>';
        const res = await fetch(`/api/mentor/mentees/${this.currentId}/tasks`);
        const data = await res.json();
        const tasks = data.tasks || [];

        const rows = tasks.map(t => `
            <tr>
                <td class="small">${this.escape(t.title)}
                    ${t.body ? `<div class="text-muted" style="font-size:.72rem;">${this.escape(t.body)}</div>` : ''}</td>
                <td class="small">${this.escape(t.due_date || '—')}</td>
                <td class="small">
                    <select class="form-select form-select-sm" onchange="mentorApp.setTaskStatus(${t.id}, this.value)">
                        <option value="open" ${t.status === 'open' ? 'selected' : ''}>未開始</option>
                        <option value="in_progress" ${t.status === 'in_progress' ? 'selected' : ''}>進行中</option>
                        <option value="done" ${t.status === 'done' ? 'selected' : ''}>已完成</option>
                    </select>
                </td>
                <td><button class="btn btn-sm btn-outline-danger"
                            onclick="mentorApp.deleteTask(${t.id})"><i class="bi bi-trash"></i></button></td>
            </tr>`).join('');

        body.innerHTML = `
            <div class="row g-2 align-items-end mb-3">
                <div class="col-md-4">
                    <label class="form-label small fw-bold text-muted">工作標題</label>
                    <input type="text" class="form-control form-control-sm" id="taskTitle">
                </div>
                <div class="col-md-4">
                    <label class="form-label small fw-bold text-muted">說明（選填）</label>
                    <input type="text" class="form-control form-control-sm" id="taskBody">
                </div>
                <div class="col-md-2">
                    <label class="form-label small fw-bold text-muted">期限（選填）</label>
                    <input type="date" class="form-control form-control-sm" id="taskDue">
                </div>
                <div class="col-md-2">
                    <button class="btn btn-sm btn-primary w-100" onclick="mentorApp.createTask()">指派工作</button>
                </div>
            </div>
            <table class="table table-sm table-hover">
                <thead><tr><th class="small">工作</th><th class="small">期限</th>
                <th class="small" style="width:140px;">狀態</th><th style="width:50px;"></th></tr></thead>
                <tbody>${rows || '<tr><td colspan="4" class="text-muted small">尚未指派任何工作。</td></tr>'}</tbody>
            </table>`;
    }

    async createTask() {
        const title = (document.getElementById('taskTitle').value || '').trim();
        if (!title) { alert('請輸入工作標題'); return; }
        const res = await fetch(`/api/mentor/mentees/${this.currentId}/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: title,
                body: document.getElementById('taskBody').value,
                due_date: document.getElementById('taskDue').value,
            }),
        });
        const data = await res.json();
        if (!data.success) { alert(data.message || '指派失敗'); return; }
        this.renderTab();
        this.loadMentees();
    }

    async setTaskStatus(taskId, status) {
        await fetch(`/api/mentor/tasks/${taskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: status }),
        });
        this.loadMentees();
    }

    async deleteTask(taskId) {
        if (!confirm('確定要刪除這項工作嗎？')) return;
        await fetch(`/api/mentor/tasks/${taskId}`, { method: 'DELETE' });
        this.renderTab();
        this.loadMentees();
    }

    // -- 評論 ---------------------------------------------------------------

    async _renderComments(body) {
        body.innerHTML = '<div class="text-muted small">載入中…</div>';
        const res = await fetch(`/api/mentor/mentees/${this.currentId}/comments`);
        const data = await res.json();
        const comments = data.comments || [];

        const items = comments.map(c => `
            <div class="border rounded p-2 mb-2">
                <div class="d-flex justify-content-between">
                    <span class="fw-bold small">${this.escape(c.mentor || '—')}</span>
                    <span class="text-muted" style="font-size:.72rem;">${this.fmtDate(c.created_at)}</span>
                </div>
                <div class="small mt-1" style="white-space:pre-wrap;">${this.escape(c.body)}</div>
            </div>`).join('');

        body.innerHTML = `
            <div class="mb-3">
                <textarea class="form-control form-control-sm mb-2" id="mentorCommentInput" rows="3"
                          placeholder="對這位指導對象的整體回饋…"></textarea>
                <button class="btn btn-sm btn-primary" onclick="mentorApp.postComment()">送出評論</button>
            </div>
            ${items || '<div class="text-muted small">尚無評論。</div>'}`;
    }

    async postComment() {
        const input = document.getElementById('mentorCommentInput');
        const body = (input.value || '').trim();
        if (!body) return;
        const res = await fetch(`/api/mentor/mentees/${this.currentId}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ body: body }),
        });
        const data = await res.json();
        if (!data.success) { alert(data.message || '送出失敗'); return; }
        this.renderTab();
    }
}

window.MentorDashboard = MentorDashboard;

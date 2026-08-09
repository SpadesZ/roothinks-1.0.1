// 路徑: app/static/js/mentor.js
// 版本: v2.0
// 模組定位: Mentor / Reviewer 的 2C 主論文審閱工作台。
// 安全邊界: 前端只負責呈現；mentee、PID、資源下載都由後端重新授權。

class MentorDashboard {
    constructor() {
        this.mentees = [];
        this.current = null;
        this.currentId = null;
        this.currentPid = null;
        this.currentReview = null;
    }

    async init() {
        await this.loadMentees();
    }

    escape(text) {
        const div = document.createElement('div');
        div.textContent = String(text == null ? '' : text);
        return div.innerHTML;
    }

    showError(message) {
        const el = document.getElementById('mentorError');
        if (!el) return;
        el.textContent = message || '';
        el.style.display = message ? '' : 'none';
    }

    fmtDate(iso) {
        if (!iso) return '—';
        try { return new Date(iso).toLocaleString(); } catch (_err) { return iso; }
    }

    fmtSize(bytes) {
        const value = Number(bytes || 0);
        if (value < 1024) return `${value} B`;
        if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
        return `${(value / 1024 / 1024).toFixed(1)} MB`;
    }

    async _fetchJson(url, options) {
        const response = await fetch(url, options);
        let data = {};
        try { data = await response.json(); } catch (_err) { /* non-JSON error */ }
        if (!response.ok || !data.success) {
            throw new Error(data.message || data.error || `Request failed (${response.status})`);
        }
        return data;
    }

    async loadMentees() {
        const list = document.getElementById('menteeList');
        try {
            const data = await this._fetchJson('/api/mentor/mentees');
            this.mentees = data.mentees || [];
            document.getElementById('menteeCount').textContent = this.mentees.length;
            if (!this.mentees.length) {
                list.innerHTML = '<div class="text-muted small p-3">尚未加入指導對象。</div>';
                return;
            }
            list.innerHTML = '';
            this.mentees.forEach(item => list.appendChild(this._menteeRow(item)));
            if (!this.currentId) await this.selectMentee(this.mentees[0].mentee_id);
        } catch (err) {
            list.innerHTML = '<div class="text-danger small p-3">載入失敗</div>';
            this.showError(err.message);
        }
    }

    _menteeRow(item) {
        const row = document.createElement('a');
        row.href = '#';
        row.dataset.menteeId = String(item.mentee_id);
        row.className = 'list-group-item list-group-item-action';
        const online = item.stats && item.stats.online_now
            ? '<span class="badge bg-success ms-1">在線</span>' : '';
        row.innerHTML = `
            <div class="fw-bold small">${this.escape(item.mentee_username)}${online}</div>
            <div class="text-muted" style="font-size:.72rem;">${this.escape(item.mentee_email)}</div>
            <div class="text-muted mt-1" style="font-size:.72rem;">${item.project_count} 個專案</div>`;
        row.onclick = event => {
            event.preventDefault();
            this.selectMentee(item.mentee_id);
        };
        return row;
    }

    async addMentee() {
        const input = document.getElementById('menteeEmailInput');
        const email = (input.value || '').trim();
        if (!email) return;
        this.showError('');
        try {
            await this._fetchJson('/api/mentor/mentees', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email}),
            });
            input.value = '';
            await this.loadMentees();
        } catch (err) {
            this.showError(err.message);
        }
    }

    async removeMentee(menteeId) {
        if (!confirm('確定解除這位指導對象嗎？不會刪除對方資料。')) return;
        try {
            await this._fetchJson(`/api/mentor/mentees/${menteeId}`, {method: 'DELETE'});
            this.current = null;
            this.currentId = null;
            this.currentPid = null;
            document.getElementById('menteeTitle').textContent = '請從左側選擇一位指導對象';
            document.getElementById('mentorWorkbenchBody').innerHTML =
                '<div class="text-muted small">尚未選擇指導對象。</div>';
            await this.loadMentees();
        } catch (err) {
            this.showError(err.message);
        }
    }

    async selectMentee(menteeId) {
        const body = document.getElementById('mentorWorkbenchBody');
        body.innerHTML = '<div class="text-muted small">載入 2C 工作台…</div>';
        this.showError('');
        try {
            const detail = await this._fetchJson(`/api/mentor/mentees/${menteeId}?notes=0`);
            this.current = detail;
            this.currentId = menteeId;
            document.querySelectorAll('[data-mentee-id]').forEach(row => {
                row.classList.toggle('active', row.dataset.menteeId === String(menteeId));
            });

            const mentee = detail.mentee;
            document.getElementById('menteeTitle').innerHTML = `
                <span class="fw-bold">${this.escape(mentee.username)}</span>
                <span class="text-muted small ms-2">${this.escape(mentee.email)}</span>
                <button class="btn btn-sm btn-outline-danger float-end"
                        onclick="mentorApp.removeMentee(${mentee.id})">解除歸屬</button>`;

            const projects = (detail.projects || []).filter(item =>
                item.reviewable_2c !== false && (item.status === 'formal' || String(item.pid).endsWith('-p'))
            );
            this._renderWorkbenchShell(projects);
            if (projects.length) await this.loadReview(projects[0].pid);
        } catch (err) {
            body.innerHTML = '<div class="text-danger small">沒有權限或載入失敗。</div>';
            this.showError(err.message);
        }
    }

    _renderWorkbenchShell(projects) {
        const body = document.getElementById('mentorWorkbenchBody');
        if (!projects.length) {
            body.innerHTML = '<div class="alert alert-info small mb-0">這位指導對象目前沒有可檢視 2C 全篇的專案。</div>';
            return;
        }
        const options = projects.map(project => `
            <option value="${this.escape(project.pid)}">
                ${this.escape(project.name || project.pid)} · ${this.escape(project.pid)}
            </option>`).join('');

        body.innerHTML = `
            <div class="d-flex flex-wrap align-items-center gap-2 mb-3">
                <label class="small fw-bold text-muted" for="reviewProjectSelect">2C 專案</label>
                <select class="form-select form-select-sm" id="reviewProjectSelect" style="max-width:420px;">
                    ${options}
                </select>
                <span class="text-muted small" id="reviewVersionMeta"></span>
            </div>
            <div class="row g-3">
                <div class="col-xl-8">
                    <div class="border rounded bg-light overflow-hidden">
                        <div class="bg-white border-bottom px-3 py-2 fw-bold" id="reviewPaperTitle">2C 主論文</div>
                        <iframe id="reviewManuscriptFrame" title="2C 主論文唯讀內容" sandbox=""
                                referrerpolicy="no-referrer" style="width:100%;height:68vh;border:0;background:#fff;"></iframe>
                        <div class="p-4 text-muted small" id="reviewManuscriptEmpty" style="display:none;">
                            此專案尚未儲存任何 2C 主論文版本。
                        </div>
                    </div>
                </div>
                <div class="col-xl-4">
                    <div class="border rounded p-3 mb-3">
                        <h6 class="fw-bold">提供意見／建議</h6>
                        <select class="form-select form-select-sm mb-2" id="reviewFeedbackKind">
                            <option value="comment">意見</option>
                            <option value="suggestion">建議</option>
                        </select>
                        <textarea class="form-control form-control-sm mb-2" id="reviewFeedbackBody" rows="4"
                                  placeholder="針對目前 2C 版本留下具體回饋…"></textarea>
                        <button class="btn btn-primary btn-sm w-100" onclick="mentorApp.postFeedback()">送出</button>
                    </div>
                    <div class="border rounded p-3 mb-3">
                        <h6 class="fw-bold">提供資源</h6>
                        <input class="form-control form-control-sm mb-2" id="reviewResourceTitle" placeholder="資源名稱（選填）">
                        <div class="input-group input-group-sm mb-2">
                            <input type="url" class="form-control" id="reviewResourceUrl" placeholder="https://…">
                            <button class="btn btn-outline-primary" onclick="mentorApp.addUrlResource()">加入網址</button>
                        </div>
                        <div class="input-group input-group-sm">
                            <input type="file" class="form-control" id="reviewResourcePdf" accept=".pdf,application/pdf">
                            <button class="btn btn-outline-danger" onclick="mentorApp.uploadPdfResource()">上傳 PDF</button>
                        </div>
                        <div class="form-text">只接受 PDF，單檔上限 20 MB。</div>
                    </div>
                    <div id="reviewItems"><div class="text-muted small">載入回饋與資源…</div></div>
                </div>
            </div>`;
        document.getElementById('reviewProjectSelect').addEventListener('change', event => {
            this.loadReview(event.target.value);
        });
    }

    async loadReview(pid) {
        if (!pid || !this.currentId) return;
        this.currentPid = pid;
        this.showError('');
        try {
            const data = await this._fetchJson(
                `/api/mentor/mentees/${this.currentId}/reviews/${encodeURIComponent(pid)}`
            );
            this.currentReview = data;
            this._renderManuscript(data.manuscript);
            this._renderItems(data.items || [], data.legacy_comments || []);
        } catch (err) {
            this.showError(err.message);
        }
    }

    _renderManuscript(manuscript) {
        const frame = document.getElementById('reviewManuscriptFrame');
        const empty = document.getElementById('reviewManuscriptEmpty');
        const meta = document.getElementById('reviewVersionMeta');
        const title = document.getElementById('reviewPaperTitle');
        if (!frame || !empty) return;
        if (!manuscript) {
            frame.style.display = 'none';
            empty.style.display = '';
            title.textContent = '2C 主論文';
            meta.textContent = '尚無版本';
            return;
        }

        const parsed = new DOMParser().parseFromString(manuscript.content || '', 'text/html');
        parsed.querySelectorAll('script,iframe,object,embed,form,input,button,textarea,select,link,meta,base')
            .forEach(node => node.remove());
        parsed.querySelectorAll('*').forEach(node => {
            Array.from(node.attributes).forEach(attr => {
                const name = attr.name.toLowerCase();
                if (name.startsWith('on') || ['href', 'srcset', 'action', 'formaction', 'srcdoc', 'xlink:href'].includes(name)) {
                    node.removeAttribute(attr.name);
                }
                if (name === 'src' && !(node.tagName === 'IMG' && /^data:image\//i.test(attr.value))) {
                    node.removeAttribute(attr.name);
                }
            });
        });
        const safeBody = parsed.body.innerHTML;
        const shell = `<!doctype html><html><head>
            <meta charset="utf-8">
            <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
            <style>body{font-family:system-ui,-apple-system,sans-serif;line-height:1.75;color:#212529;padding:28px;max-width:900px;margin:auto}img{max-width:100%;height:auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px}</style>
            </head><body>${safeBody}</body></html>`;
        frame.srcdoc = shell;
        frame.style.display = '';
        empty.style.display = 'none';
        title.textContent = manuscript.title || '2C 主論文';
        meta.textContent = `${manuscript.version || '—'} · ${this.fmtDate(manuscript.updated_at)}${manuscript.updated_by ? ` · ${manuscript.updated_by}` : ''}`;
    }

    _renderItems(items, legacyComments) {
        const target = document.getElementById('reviewItems');
        if (!target) return;
        const feedback = items.filter(item => ['comment', 'suggestion'].includes(item.kind));
        const resources = items.filter(item => ['resource_url', 'resource_pdf'].includes(item.kind));
        const feedbackHtml = feedback.map(item => `
            <div class="border rounded p-2 mb-2">
                <div class="d-flex justify-content-between gap-2">
                    <span class="badge ${item.kind === 'suggestion' ? 'bg-success' : 'bg-primary'}">
                        ${item.kind === 'suggestion' ? '建議' : '意見'}${item.paper_version ? ` · ${this.escape(item.paper_version)}` : ''}
                    </span>
                    <span class="text-muted" style="font-size:.72rem;">${this.fmtDate(item.created_at)}</span>
                </div>
                <div class="small mt-2" style="white-space:pre-wrap;">${this.escape(item.body)}</div>
            </div>`).join('');
        const legacyHtml = legacyComments.map(item => `
            <div class="border rounded p-2 mb-2 bg-light">
                <div class="text-muted" style="font-size:.72rem;">既有整體評論 · ${this.fmtDate(item.created_at)}</div>
                <div class="small mt-1" style="white-space:pre-wrap;">${this.escape(item.body)}</div>
            </div>`).join('');
        const resourceHtml = resources.map(item => {
            const label = this.escape(item.title || item.file_name || item.url || '資源');
            const href = item.kind === 'resource_pdf' ? item.download_url : item.url;
            const detail = item.kind === 'resource_pdf'
                ? `${this.escape(item.file_name || 'PDF')} · ${this.fmtSize(item.size_bytes)}`
                : this.escape(item.url || '');
            return `<a class="list-group-item list-group-item-action" href="${this.escape(href)}"
                       ${item.kind === 'resource_url' ? 'target="_blank" rel="noopener noreferrer"' : ''}>
                    <div class="fw-bold small"><i class="bi ${item.kind === 'resource_pdf' ? 'bi-file-earmark-pdf text-danger' : 'bi-link-45deg text-primary'} me-1"></i>${label}</div>
                    <div class="text-muted text-truncate" style="font-size:.72rem;">${detail}</div>
                </a>`;
        }).join('');
        target.innerHTML = `
            <h6 class="fw-bold small mt-4">本稿回饋</h6>
            ${feedbackHtml || '<div class="text-muted small mb-3">尚無意見或建議。</div>'}
            ${legacyHtml}
            <h6 class="fw-bold small mt-4">提供的資源</h6>
            <div class="list-group">${resourceHtml || '<div class="text-muted small">尚無資源。</div>'}</div>`;
    }

    _itemUrl() {
        return `/api/mentor/mentees/${this.currentId}/reviews/${encodeURIComponent(this.currentPid)}/items`;
    }

    _paperVersion() {
        return this.currentReview && this.currentReview.manuscript
            ? this.currentReview.manuscript.version : '';
    }

    async postFeedback() {
        const bodyEl = document.getElementById('reviewFeedbackBody');
        const body = (bodyEl.value || '').trim();
        if (!body) return;
        try {
            await this._fetchJson(this._itemUrl(), {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    kind: document.getElementById('reviewFeedbackKind').value,
                    body,
                    paper_version: this._paperVersion(),
                }),
            });
            bodyEl.value = '';
            await this.loadReview(this.currentPid);
        } catch (err) {
            this.showError(err.message);
        }
    }

    async addUrlResource() {
        const urlEl = document.getElementById('reviewResourceUrl');
        const url = (urlEl.value || '').trim();
        if (!url) return;
        try {
            await this._fetchJson(this._itemUrl(), {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    kind: 'resource_url',
                    title: document.getElementById('reviewResourceTitle').value,
                    url,
                    paper_version: this._paperVersion(),
                }),
            });
            urlEl.value = '';
            document.getElementById('reviewResourceTitle').value = '';
            await this.loadReview(this.currentPid);
        } catch (err) {
            this.showError(err.message);
        }
    }

    async uploadPdfResource() {
        const input = document.getElementById('reviewResourcePdf');
        if (!input.files || !input.files[0]) return;
        const form = new FormData();
        form.append('kind', 'resource_pdf');
        form.append('title', document.getElementById('reviewResourceTitle').value);
        form.append('paper_version', this._paperVersion());
        form.append('file', input.files[0]);
        try {
            await this._fetchJson(this._itemUrl(), {method: 'POST', body: form});
            input.value = '';
            document.getElementById('reviewResourceTitle').value = '';
            await this.loadReview(this.currentPid);
        } catch (err) {
            this.showError(err.message);
        }
    }
}

window.MentorDashboard = MentorDashboard;

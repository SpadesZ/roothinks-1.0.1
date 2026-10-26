// Roothinks source maintenance contract
// 檔案路徑: app/static/js/study_evidence.js
// 模組定位: Study 前端；證據筆記（roothinks.study.evidence_notes/1）的草稿、接受、清單、留言與審閱請求。
// 主要責任: AI 回答只會變成「草稿」；研究者編輯並按「接受」才送出。原文事實由伺服器核對、畫面上鎖定不可改。
// 上下游: study_chat.js 的結構化回答卡呼叫 openDraft；study_view.js 的 revealAnchor 負責點證據回原文。
// 維護邊界: 模型與使用者文字一律用 textContent；本機 localStorage 只存未送出的草稿（個人便利），伺服器才是紀錄。
// 驗證: test/integration_e2e/study_coresearch_browser.cjs（STUDY_PHASE=after）
(() => {
    'use strict';

    const KIND_LABELS = { source_fact: '原文事實', ai_interpretation: 'AI 解讀', human_hypothesis: '我的假說' };
    const KIND_CLASS = { source_fact: 'study-kind-source', ai_interpretation: 'study-kind-ai', human_hypothesis: 'study-kind-human' };
    // 狀態只描述「引文在原文中找不找得到、在哪裡」，不描述是否支持任何論點。
    const STATUS_LABELS = { exact: '原文中找到', relocated: '原文中找到（已重新定位）', ambiguous_in_block: '同段多處，只確定段落',
        ambiguous: '多段都有，無法定位', not_found: '原文找不到', unavailable: '全文無法讀取' };
    const SUPPORT_LABELS = { not_assessed: '未判定是否支持', supports: '我判斷：支持', partial: '我判斷：部分支持', does_not_support: '我判斷：不支持' };
    const SUPPORT_SHORT = { not_assessed: '未判定是否支持', supports: '研究者判斷：支持', partial: '研究者判斷：部分支持', does_not_support: '研究者判斷：不支持' };

    function currentUserId() {
        // study.html 的自由筆記欄位帶目前登入者 id；未登入時是 'local'。
        const owner = document.getElementById('study-notes')?.dataset.owner;
        return owner && owner !== 'local' ? Number(owner) : null;
    }

    function supportSelect(st, onChange) {
        const sel = el('select', 'form-select form-select-sm w-auto');
        sel.setAttribute('aria-label', '引文是否支持這則陳述（你的判斷）');
        sel.setAttribute('data-claim-support', st.claim_support || 'not_assessed');
        Object.entries(SUPPORT_LABELS).forEach(([value, label]) => {
            const opt = el('option', '', label);
            opt.value = value;
            opt.selected = (st.claim_support || 'not_assessed') === value;
            sel.appendChild(opt);
        });
        sel.addEventListener('change', () => { st.claim_support = sel.value; sel.setAttribute('data-claim-support', sel.value); onChange(); });
        return sel;
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function button(label, className, onClick, attrs = {}) {
        const btn = el('button', className, label);
        btn.type = 'button';
        Object.entries(attrs).forEach(([k, v]) => btn.setAttribute(k, v));
        btn.addEventListener('click', onClick);
        return btn;
    }

    function asSelection(anchor) {
        // 只送段落提示與文字；狀態、位置由伺服器重算。
        return {
            quote: anchor.quote, side: anchor.side || 'original', ref: anchor.ref || '', seq_id: anchor.seq_id || '',
            prefix: anchor.prefix || '', suffix: anchor.suffix || '', source: anchor.source || '',
            source_sha256: anchor.source_sha256 || '',
        };
    }

    function shortTime(value) {
        const d = new Date(value || '');
        return isNaN(d.getTime()) ? '' : d.toLocaleString('zh-TW', { hour12: false });
    }

    class StudyEvidence {
        constructor(pid) {
            this.pid = pid;
            this.rev = 0;
            this.notes = [];
            this.draft = null;
            this.pane = document.getElementById('evidence-notes-pane');
            this.freePane = document.getElementById('free-notes-pane');
            this.draftEl = document.getElementById('evidence-note-draft');
            this.listEl = document.getElementById('evidence-notes-list');
            this.statusEl = document.getElementById('evidence-notes-status');
            this.countEl = document.getElementById('evidence-notes-count');
            document.querySelectorAll('[data-notes-tab]').forEach(btn => {
                btn.addEventListener('click', () => this.showPane(btn.dataset.notesTab));
            });
            this.reviewScope = !!window.STUDY_REVIEW_SCOPE;
            if (this.reviewScope) this.showPane('evidence');
            else this._restoreDraft();
            this.load();
        }

        _draftKey() { return `study_evidence_draft_${this.pid}`; }

        _saveDraftLocal() {
            try {
                if (this.draft) localStorage.setItem(this._draftKey(), JSON.stringify(this.draft));
                else localStorage.removeItem(this._draftKey());
            } catch (e) { /* 私密視窗寫不進去；草稿只留在本頁 */ }
        }

        _restoreDraft() {
            try {
                const raw = localStorage.getItem(this._draftKey());
                if (raw) {
                    this.draft = JSON.parse(raw);
                    this.draft.restored = true;
                }
            } catch (e) {
                this.draft = null;
            }
            this.renderDraft();
        }

        setStatus(text, tone = 'muted') {
            if (!this.statusEl) return;
            this.statusEl.textContent = text;
            this.statusEl.className = `small text-${tone}`;
        }

        showPane(which) {
            const evidence = which === 'evidence';
            if (this.pane) this.pane.hidden = !evidence;
            if (this.freePane) this.freePane.hidden = evidence;
            document.querySelectorAll('[data-notes-tab]').forEach(btn => {
                const active = btn.dataset.notesTab === (evidence ? 'evidence' : 'free');
                btn.classList.toggle('active', active);
                btn.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
        }

        async load() {
            if (!this.pid) return;
            try {
                const res = await fetch(`/api/study/evidence_notes/${encodeURIComponent(this.pid)}?verify=1`, { cache: 'no-store' });
                const data = await res.json();
                if (!res.ok || !data.ok) throw new Error(data.msg || `HTTP ${res.status}`);
                this.rev = data.rev;
                this.notes = data.notes || [];
                this.renderList();
                this.setStatus(`伺服器版本 ${this.rev}`);
            } catch (err) {
                this.setStatus(`證據筆記讀取失敗：${err.message}`, 'danger');
            }
        }

        openDraft(structured, origin = {}) {
            const selection = structured.selection || {};
            const anchors = [];
            const views = [];
            const keyOf = a => `${a.ref}|${a.side}|${a.start}|${a.end}`;
            const index = new Map();
            const add = (anchor) => {
                if (!anchor || !anchor.quote) return -1;
                const key = keyOf(anchor);
                if (index.has(key)) return index.get(key);
                anchors.push(asSelection(anchor));
                views.push(anchor);
                index.set(key, anchors.length - 1);
                return anchors.length - 1;
            };
            const statements = [];
            const selIdx = add(selection);
            if (selection.side === 'original' && selIdx >= 0) {
                statements.push({ kind: 'source_fact', text: selection.quote, evidence: [selIdx], locked: true });
            }
            if (structured.selection_source) {
                const srcIdx = add(structured.selection_source);
                statements.push({ kind: 'source_fact', text: structured.selection_source.quote, evidence: [srcIdx], locked: true });
            }
            (structured.points || []).forEach(point => (point.evidence || []).forEach(ev => {
                const known = index.has(keyOf(ev));
                const idx = add(ev);
                if (!known && idx >= 0) statements.push({ kind: 'source_fact', text: ev.quote, evidence: [idx], locked: true });
            }));
            if (structured.interpretation) {
                statements.push({ kind: 'ai_interpretation', text: structured.interpretation, evidence: [] });
            }
            let skipped = 0;
            (structured.points || []).forEach(point => {
                const refs = (point.evidence || []).map(ev => index.get(keyOf(ev))).filter(i => i !== undefined);
                // 模型為這個論點給的引文全部對不上原文：預設不放進草稿，避免把假出處的說法存成筆記。
                if (!refs.length && point.rejected_evidence) {
                    skipped += 1;
                    return;
                }
                statements.push({ kind: 'ai_interpretation', text: point.text, evidence: refs });
            });
            statements.push({ kind: 'human_hypothesis', text: '', evidence: [] });
            this.draft = {
                paper_id: selection.paper_id || '',
                title: '',
                skipped,
                anchors,
                views,
                statements,
                origin: {
                    type: 'tutor', mode: structured.mode || 'explain', conv_id: origin.conv_id || '',
                    uncertainty: structured.uncertainty, system_check: structured.system_check,
                },
            };
            this._saveDraftLocal();
            this.showPane('evidence');
            this.renderDraft();
            this.draftEl?.scrollIntoView({ block: 'nearest' });
        }

        discardDraft() {
            this.draft = null;
            this._saveDraftLocal();
            this.renderDraft();
            this.setStatus('已放棄草稿（沒有任何內容寫入伺服器）');
        }

        _anchorChip(anchor, label) {
            const chip = button('', 'btn btn-link btn-sm p-0 text-start study-evidence-chip', () => window.studyView?.revealAnchor(anchor),
                { 'data-evidence-anchor': anchor.ref || '', title: '在左側原文中定位這段' });
            chip.appendChild(el('span', 'badge bg-success-subtle text-success-emphasis me-1', label));
            const quote = String(anchor.quote || '');
            chip.appendChild(el('span', 'study-quote', `“${quote.length > 220 ? quote.slice(0, 220) + '…' : quote}”`));
            return chip;
        }

        renderDraft() {
            if (!this.draftEl) return;
            this.draftEl.replaceChildren();
            const draft = this.draft;
            this.draftEl.hidden = !draft;
            if (!draft) return;
            const box = el('div', 'study-draft border border-success rounded p-2 mb-2 bg-white');
            box.setAttribute('data-evidence-draft', '1');
            box.appendChild(el('div', 'fw-bold text-success small', draft.restored ? '研究筆記草稿（從本機復原，尚未存檔）' : '研究筆記草稿（尚未存檔）'));
            box.appendChild(el('div', 'small text-muted mb-1', 'AI 內容只有在你按「接受」後才會存檔。原文事實由伺服器逐字核對，不能改寫；AI 解讀可以改寫或改標成你的假說。'));
            const title = el('input', 'form-control form-control-sm mb-2');
            title.placeholder = '標題（可留空）';
            title.value = draft.title || '';
            title.setAttribute('aria-label', '筆記標題');
            title.addEventListener('input', () => { draft.title = title.value; this._saveDraftLocal(); });
            box.appendChild(title);
            if (draft.skipped) {
                const skippedNote = el('div', 'alert alert-warning py-1 px-2 small mb-2',
                    `已略過 ${draft.skipped} 個 AI 論點：它們附的引文在原文中找不到。若你仍認為值得記錄，請自己改寫成「我的假說」。`);
                skippedNote.setAttribute('data-draft-skipped', String(draft.skipped));
                box.appendChild(skippedNote);
            }

            draft.statements.forEach((st, i) => {
                const row = el('div', `study-statement ${KIND_CLASS[st.kind]} mb-2`);
                row.setAttribute('data-statement-kind', st.kind);
                const top = el('div', 'd-flex align-items-center gap-1 mb-1');
                if (st.locked) {
                    const view = draft.views[st.evidence[0]] || {};
                    top.appendChild(el('span', 'badge bg-success', view.match_kind === 'paragraph_from_translation'
                        ? '原文事實・由譯文推定的整段原文' : '原文事實・原文中逐字找到'));
                } else {
                    const kind = el('select', 'form-select form-select-sm w-auto');
                    kind.setAttribute('aria-label', '陳述類別');
                    ['ai_interpretation', 'human_hypothesis'].forEach(k => {
                        const opt = el('option', '', KIND_LABELS[k]);
                        opt.value = k;
                        opt.selected = st.kind === k;
                        kind.appendChild(opt);
                    });
                    kind.addEventListener('change', () => { st.kind = kind.value; this._saveDraftLocal(); this.renderDraft(); });
                    top.appendChild(kind);
                }
                (st.evidence || []).forEach(idx => top.appendChild(el('span', 'badge bg-light text-dark border', `證據 ${idx + 1}`)));
                if (!st.locked && !(st.evidence || []).length) top.appendChild(el('span', 'badge bg-secondary', '無證據'));
                if (!st.locked && (st.evidence || []).length) top.appendChild(supportSelect(st, () => this._saveDraftLocal()));
                top.appendChild(button('移除', 'btn btn-sm btn-link text-danger ms-auto p-0', () => {
                    draft.statements.splice(i, 1);
                    this._saveDraftLocal();
                    this.renderDraft();
                }));
                row.appendChild(top);
                if (st.locked) {
                    const anchor = draft.views[st.evidence[0]];
                    row.appendChild(anchor ? this._anchorChip(anchor, anchor.page ? `原文 p.${anchor.page}` : '原文') : el('div', '', st.text));
                } else {
                    const text = el('textarea', 'form-control form-control-sm');
                    text.rows = 2;
                    text.value = st.text;
                    text.placeholder = st.kind === 'human_hypothesis' ? '你的假說或判斷（留空會略過）' : 'AI 解讀（可改寫）';
                    text.setAttribute('aria-label', KIND_LABELS[st.kind]);
                    text.addEventListener('input', () => { st.text = text.value; this._saveDraftLocal(); });
                    row.appendChild(text);
                }
                box.appendChild(row);
            });

            const unc = draft.origin?.uncertainty;
            if (unc) {
                const levels = { low: '低', medium: '中', high: '高', unknown: '未知' };
                box.appendChild(el('div', 'small text-muted mb-1',
                    `AI 自評不確定性：${levels[unc.level] || '未知'}${(unc.reasons || []).length ? '：' + unc.reasons.join('；') : ''}`));
            }
            const actions = el('div', 'd-flex flex-wrap gap-1');
            actions.appendChild(button('加入我的假說', 'btn btn-sm btn-outline-primary', () => {
                draft.statements.push({ kind: 'human_hypothesis', text: '', evidence: [] });
                this._saveDraftLocal();
                this.renderDraft();
            }));
            actions.appendChild(button('接受並存成證據筆記', 'btn btn-sm btn-success', () => this.acceptDraft(), { 'data-evidence-note-accept': '1' }));
            actions.appendChild(button('放棄草稿', 'btn btn-sm btn-outline-secondary', () => this.discardDraft()));
            box.appendChild(actions);
            const msg = el('div', 'small mt-1');
            msg.id = 'evidence-draft-message';
            msg.setAttribute('role', 'status');
            msg.setAttribute('aria-live', 'polite');
            box.appendChild(msg);
            this.draftEl.appendChild(box);
        }

        _draftMessage(text, tone) {
            const msg = document.getElementById('evidence-draft-message');
            if (msg) {
                msg.textContent = text;
                msg.className = `small mt-1 text-${tone}`;
            }
        }

        async acceptDraft() {
            const draft = this.draft;
            if (!draft) return;
            const statements = draft.statements
                .filter(st => st.locked || String(st.text || '').trim())
                .map(st => st.locked ? { kind: 'source_fact', evidence: st.evidence }
                    : { kind: st.kind, text: st.text.trim(), evidence: st.evidence || [],
                        claim_support: (st.evidence || []).length ? (st.claim_support || 'not_assessed') : 'not_assessed' });
            if (!statements.length) {
                this._draftMessage('至少要有一則陳述。', 'danger');
                return;
            }
            const body = {
                pid: this.pid, base_rev: this.rev, accepted: true,
                note: { paper_id: draft.paper_id, title: draft.title || '', anchors: draft.anchors, statements, origin: draft.origin },
            };
            try {
                const res = await fetch('/api/study/evidence_notes/accept', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => ({}));
                if (res.status === 409 || res.status === 428) {
                    await this.load();
                    this._draftMessage('證據筆記在別處更新過，已重新載入；草稿仍保留，請再按一次接受。', 'warning');
                    return;
                }
                if (!res.ok || !data.ok) {
                    this._draftMessage(`沒有存檔：${data.msg || `HTTP ${res.status}`}（草稿仍保留）`, 'danger');
                    return;
                }
                this.draft = null;
                this._saveDraftLocal();
                this.renderDraft();
                await this.load();
                this.setStatus(`已存成證據筆記（版本 ${data.rev}）`, 'success');
                window.studyCore?.showToast?.('已存成證據筆記', 'success');
            } catch (err) {
                this._draftMessage('連線失敗；草稿仍保留在本機，可稍後再試。', 'danger');
            }
        }

        async _post(url, body) {
            const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) throw new Error(data.msg || (res.status === 403 ? '沒有權限（需要專案編輯權）' : `HTTP ${res.status}`));
            return data;
        }

        async comment(noteId, input, msg) {
            const body = String(input.value || '').trim();
            if (!body) return;
            try {
                await this._post('/api/study/evidence_notes/comment', { pid: this.pid, note_id: noteId, body });
                input.value = '';
                await this.load();
            } catch (err) {
                msg.textContent = `留言失敗：${err.message}`;
            }
        }

        async review(noteId, action, msg) {
            try {
                await this._post('/api/study/evidence_notes/review', { pid: this.pid, note_id: noteId, action });
                await this.load();
                this.setStatus(action === 'request' ? '已請求審閱；指導教授會在導師工作台看到這則筆記' : '已撤回審閱請求', 'success');
            } catch (err) {
                msg.textContent = `沒有更新：${err.message}`;
            }
        }

        renderList() {
            if (!this.listEl) return;
            this.listEl.replaceChildren();
            if (this.countEl) this.countEl.textContent = String(this.notes.length);
            const tools = el('div', 'd-flex justify-content-between align-items-center mb-1');
            tools.appendChild(el('span', 'small text-muted', this.reviewScope
                ? `審閱模式：研究者請求審閱的 ${this.notes.length} 則筆記（留言請到導師工作台）`
                : `${this.notes.length} 則證據筆記`));
            const exportLink = el('a', 'btn btn-sm btn-link p-0', '匯出 Markdown');
            exportLink.href = `/api/study/evidence_notes/${encodeURIComponent(this.pid)}/export`;
            exportLink.setAttribute('data-evidence-export', '1');
            tools.appendChild(exportLink);
            this.listEl.appendChild(tools);
            if (!this.notes.length) {
                this.listEl.appendChild(el('div', 'small text-muted', '還沒有證據筆記。在全文選一段文字，按「解釋」或「批判」，再從回答建立草稿。'));
                return;
            }
            [...this.notes].reverse().forEach(note => this.listEl.appendChild(this._noteCard(note)));
        }

        _noteCard(note) {
            const card = el('div', 'study-note-card border rounded p-2 mb-2 bg-white');
            card.setAttribute('data-evidence-note', note.note_id);
            const head = el('div', 'd-flex flex-wrap align-items-center gap-1 mb-1');
            head.appendChild(el('span', 'fw-bold small', note.title || '證據筆記'));
            const found = (note.quote_match || (note.evidence_status === 'verified' ? 'found' : 'none')) === 'found';
            const matchBadge = el('span', `badge ${found ? 'bg-light text-success border border-success' : 'bg-secondary'}`,
                found ? '引文在原文中找到（不代表支持陳述）' : '沒有在原文中找到的引文');
            matchBadge.setAttribute('data-quote-match', found ? 'found' : 'none');
            head.appendChild(matchBadge);
            head.appendChild(el('span', 'small text-muted', `作者：${note.accepted_by?.username || '未知'}`));
            if (note.review?.state === 'requested') {
                const badge = el('span', 'badge bg-warning text-dark', '已請求審閱');
                badge.setAttribute('data-review-state', 'requested');
                head.appendChild(badge);
            }
            head.appendChild(el('span', 'small text-muted ms-auto', shortTime(note.accepted_at)));
            card.appendChild(head);

            (note.anchors || []).forEach((anchor, i) => {
                const line = el('div', 'small mb-1');
                line.appendChild(el('span', 'badge bg-light text-dark border me-1', `證據 ${i + 1}`));
                line.appendChild(this._anchorChip(anchor, anchor.page ? `${anchor.side === 'translation' ? '譯文' : '原文'} p.${anchor.page}` : '位置不明'));
                const current = anchor.current;
                if (current && (current.version_changed || current.status !== 'exact')) {
                    const warn = el('span', 'badge bg-warning text-dark ms-1',
                        current.status === 'relocated' && current.version_changed ? '原文版本已變更（已重新定位）' : (STATUS_LABELS[current.status] || current.status));
                    warn.setAttribute('data-anchor-current', current.status);
                    line.appendChild(warn);
                } else if (!current && anchor.status && anchor.status !== 'exact') {
                    line.appendChild(el('span', 'badge bg-secondary ms-1', STATUS_LABELS[anchor.status] || anchor.status));
                }
                card.appendChild(line);
            });

            (note.statements || []).forEach(st => {
                const row = el('div', `study-statement ${KIND_CLASS[st.kind]} small mb-1`);
                row.setAttribute('data-statement-kind', st.kind);
                row.appendChild(el('span', 'study-kind-label me-1', KIND_LABELS[st.kind]));
                row.appendChild(el('span', '', st.text));
                if ((st.evidence || []).length) row.appendChild(el('span', 'text-muted ms-1', `（${st.evidence.map(i => `證據 ${i + 1}`).join('、')}）`));
                else if (st.kind !== 'source_fact') row.appendChild(el('span', 'badge bg-secondary ms-1', '無證據'));
                if (st.kind === 'source_fact' && st.quote_scope === 'paragraph_from_translation') {
                    row.appendChild(el('span', 'badge bg-light text-dark border ms-1', '由譯文推定的整段原文'));
                }
                if (st.kind !== 'source_fact' && (st.evidence || []).length) {
                    const support = el('span', `badge ms-1 ${st.claim_support && st.claim_support !== 'not_assessed' ? 'bg-info text-dark' : 'bg-light text-dark border'}`,
                        SUPPORT_SHORT[st.claim_support || 'not_assessed'] + (st.claim_support_by?.username ? `（${st.claim_support_by.username}）` : ''));
                    support.setAttribute('data-claim-support', st.claim_support || 'not_assessed');
                    row.appendChild(support);
                }
                card.appendChild(row);
            });
            card.appendChild(this._historyBlock(note));

            const comments = el('div', 'study-comments mt-1');
            (note.comments || []).forEach(c => {
                const line = el('div', 'small border-start ps-2 mb-1');
                line.setAttribute('data-comment-role', c.author_role);
                line.appendChild(el('span', 'fw-bold me-1', c.author?.username || '未知'));
                line.appendChild(el('span', `badge me-1 ${c.author_role === 'mentor' ? 'bg-info text-dark' : 'bg-light text-dark border'}`,
                    c.author_role === 'mentor' ? '指導教授' : '成員'));
                line.appendChild(el('span', '', c.body));
                comments.appendChild(line);
            });
            card.appendChild(comments);

            if (this.reviewScope) return card;
            const msg = el('div', 'small text-danger');
            msg.setAttribute('role', 'status');
            const actions = el('div', 'd-flex gap-1 mt-1');
            const input = el('input', 'form-control form-control-sm');
            input.placeholder = '留言（成員可見）';
            input.setAttribute('aria-label', '留言內容');
            actions.appendChild(input);
            actions.appendChild(button('留言', 'btn btn-sm btn-outline-secondary text-nowrap', () => this.comment(note.note_id, input, msg),
                { 'data-evidence-comment': note.note_id }));
            const me = currentUserId();
            const isAuthor = !note.accepted_by || me === null || note.accepted_by.user_id === me;
            if (isAuthor) {
                // 只有作者能直接改寫與請求審閱。
                const requested = note.review?.state === 'requested';
                actions.appendChild(button(requested ? '撤回審閱請求' : '請求審閱', `btn btn-sm text-nowrap ${requested ? 'btn-outline-warning' : 'btn-outline-primary'}`,
                    () => this.review(note.note_id, requested ? 'withdraw' : 'request', msg), { 'data-evidence-review': requested ? 'withdraw' : 'request' }));
                actions.appendChild(button('改寫', 'btn btn-sm btn-outline-secondary text-nowrap',
                    () => this._openEditor(card, note, 'edit', msg), { 'data-evidence-edit': note.note_id }));
            } else {
                actions.appendChild(button('提出修改建議', 'btn btn-sm btn-outline-secondary text-nowrap',
                    () => this._openEditor(card, note, 'suggest', msg), { 'data-evidence-suggest': note.note_id }));
            }
            card.appendChild(actions);
            card.appendChild(msg);
            if (isAuthor) this._openSuggestions(card, note, msg);
            return card;
        }

        _historyBlock(note) {
            const box = el('div', 'small text-muted');
            const revisions = note.revisions || [];
            const suggestions = note.suggestions || [];
            if (!revisions.length && !suggestions.length) return box;
            const details = el('details', 'mt-1');
            details.setAttribute('data-evidence-history', String(revisions.length));
            details.appendChild(el('summary', '', `協作紀錄：修訂 ${revisions.length}、建議 ${suggestions.length}`));
            revisions.forEach(rev => details.appendChild(el('div', '', rev.action === 'accepted_suggestion'
                ? `${shortTime(rev.at)} ${rev.by?.username || '未知'} 接受了 ${rev.suggested_by?.username || '未知'} 的建議`
                : `${shortTime(rev.at)} ${rev.by?.username || '未知'} 改寫`)));
            suggestions.forEach(sg => details.appendChild(el('div', '',
                `${shortTime(sg.at)} ${sg.by?.username || '未知'} 建議（${{ open: '待處理', accepted: '已接受', declined: '未採用' }[sg.status] || sg.status}）${sg.reason ? '：' + sg.reason : ''}`)));
            box.appendChild(details);
            return box;
        }

        _openSuggestions(card, note, msg) {
            (note.suggestions || []).filter(sg => sg.status === 'open').forEach(sg => {
                const box = el('div', 'border border-warning rounded p-2 mt-1 small');
                box.setAttribute('data-evidence-open-suggestion', sg.suggestion_id);
                box.appendChild(el('div', 'fw-bold', `${sg.by?.username || '未知'} 建議修改${sg.reason ? '：' + sg.reason : ''}`));
                (sg.statements || []).forEach(st => box.appendChild(el('div', '', `【${KIND_LABELS[st.kind]}】${st.text}`)));
                const row = el('div', 'd-flex gap-1 mt-1');
                row.appendChild(button('接受建議', 'btn btn-sm btn-success', () => this._resolveSuggestion(note, sg, 'accept', msg),
                    { 'data-suggestion-accept': sg.suggestion_id }));
                row.appendChild(button('不採用', 'btn btn-sm btn-outline-secondary', () => this._resolveSuggestion(note, sg, 'decline', msg),
                    { 'data-suggestion-decline': sg.suggestion_id }));
                box.appendChild(row);
                card.appendChild(box);
            });
        }

        async _resolveSuggestion(note, sg, decision, msg, allowStale = false) {
            const body = { pid: this.pid, base_rev: this.rev, note_id: note.note_id, suggestion_id: sg.suggestion_id, decision,
                allow_stale: allowStale };
            try {
                const res = await fetch('/api/study/evidence_notes/suggestion', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                const data = await res.json().catch(() => ({}));
                if (res.status === 409 && data.stale_suggestion && !allowStale) {
                    // 建議是根據較舊的版本提出：讓作者明確決定，不默默覆蓋之後的改寫。
                    if (window.confirm(`${data.msg}\n\n仍要用這則建議取代目前的內容嗎？目前內容會保留在協作紀錄。`)) {
                        return this._resolveSuggestion(note, sg, decision, msg, true);
                    }
                    msg.textContent = '沒有接受：建議比目前版本舊。';
                    return;
                }
                if (res.status === 409) {
                    await this.load();
                    msg.textContent = '筆記在別處更新過，已重新載入；請再處理一次。';
                    return;
                }
                if (!res.ok || !data.ok) throw new Error(data.msg || `HTTP ${res.status}`);
                await this.load();
                this.setStatus(decision === 'accept' ? '已接受建議；原內容保留在協作紀錄' : '已不採用建議', 'success');
            } catch (err) {
                await this.load();
                msg.textContent = `沒有更新：${err.message}`;
            }
        }

        _openEditor(card, note, mode, msg) {
            card.querySelector('[data-evidence-editor]')?.remove();
            const work = (note.statements || []).map(st => ({ ...st }));
            const box = el('div', 'border rounded p-2 mt-1 bg-light');
            box.setAttribute('data-evidence-editor', mode);
            box.appendChild(el('div', 'small fw-bold', mode === 'edit' ? '改寫（舊內容會保留在協作紀錄）' : '提出修改建議（作者決定是否接受）'));
            work.forEach(st => {
                if (st.kind === 'source_fact') {
                    box.appendChild(el('div', 'small text-muted', `【原文事實，不能改】${st.text}`));
                    return;
                }
                const text = el('textarea', 'form-control form-control-sm mb-1');
                text.rows = 2;
                text.value = st.text;
                text.setAttribute('aria-label', `修改：${KIND_LABELS[st.kind]}`);
                text.addEventListener('input', () => { st.text = text.value; });
                box.appendChild(text);
                if ((st.evidence || []).length) box.appendChild(supportSelect(st, () => {}));
            });
            const reason = el('input', 'form-control form-control-sm mb-1');
            reason.placeholder = mode === 'edit' ? '標題（可留空）' : '建議理由';
            reason.setAttribute('aria-label', mode === 'edit' ? '筆記標題' : '建議理由');
            if (mode === 'edit') reason.value = note.title || '';
            box.appendChild(reason);
            box.appendChild(button(mode === 'edit' ? '儲存改寫' : '送出建議', 'btn btn-sm btn-primary', async () => {
                const statements = work.filter(st => st.kind === 'source_fact' || String(st.text || '').trim()).map(st => st.kind === 'source_fact'
                    ? { kind: 'source_fact', evidence: st.evidence }
                    : { kind: st.kind, text: st.text.trim(), evidence: st.evidence || [], claim_support: st.claim_support || 'not_assessed' });
                try {
                    if (mode === 'edit') {
                        await this._post('/api/study/evidence_notes/update', { pid: this.pid, base_rev: this.rev, note_id: note.note_id,
                            statements, title: reason.value });
                    } else {
                        await this._post('/api/study/evidence_notes/suggest', { pid: this.pid, note_id: note.note_id, statements, reason: reason.value });
                    }
                    await this.load();
                    this.setStatus(mode === 'edit' ? '已改寫' : '已送出建議；作者接受前筆記不變', 'success');
                } catch (err) {
                    if (/409/.test(err.message)) {
                        // 舊分頁：保留編輯框內容，重新載入伺服器版本後讓研究者再送一次。
                        const kept = work.map(st => st.text);
                        await this.load();
                        this.setStatus('筆記在別處更新過，已重新載入最新版本；你的修改還在編輯框，請確認後再送出。', 'warning');
                        const card2 = this.listEl.querySelector(`[data-evidence-note="${note.note_id}"]`);
                        const fresh = this.notes.find(n => n.note_id === note.note_id);
                        if (card2 && fresh) {
                            this._openEditor(card2, fresh, mode, msg);
                            card2.querySelectorAll('[data-evidence-editor] textarea').forEach((ta, i) => {
                                const pending = kept.filter((_, j) => work[j].kind !== 'source_fact')[i];
                                if (pending !== undefined) { ta.value = pending; ta.dispatchEvent(new Event('input')); }
                            });
                        }
                        return;
                    }
                    msg.textContent = `沒有送出：${err.message}`;
                }
            }, { 'data-evidence-editor-submit': mode }));
            card.appendChild(box);
        }
    }

    window.StudyEvidence = StudyEvidence;
})();

// Roothinks source maintenance contract
// 檔案路徑: app/static/js/paragraph_provenance.js
// 模組定位: Manuscript「段落出處」面板（R4）。把已保存的一段寫作連到 Study 證據筆記，或標成自由寫作。
// 主要責任: 顯示每段的出處與「需要重新核對」提示（段落、筆記、原文改變）；撤回只追加紀錄；匯出 Markdown 研究紀錄。
// 邊界: 出處狀態只是提示，不擋寫作、保存、匯出。引文找到不代表支持；支持與否只顯示研究者的判斷。
// 位置（R6）: 同一段文字可能出現多次。「選這段」送段落編號、編輯器選取送前後文，讓伺服器確定是哪一處；
//       無法確定時伺服器拒絕連結（不猜），覆蓋清單把位置不明確的連結另外列出，不算有出處。
// 引文（R6）: 只有用目前原文重新核對仍找得到的引文可以插入；譯文一律標「機器翻譯，非原文」，頁碼寫成 PDF 第 N 頁。
//       所有使用者與模型文字一律 textContent。伺服器重新驗證章節權限、版本與段落文字。
// 驗證: node --check app/static/js/paragraph_provenance.js；test/integration_e2e/study_coresearch_after.cjs
(function () {
    'use strict';
    const byId = id => document.getElementById(id);
    const KIND = { source_fact: '原文事實', ai_interpretation: 'AI 解讀', human_hypothesis: '研究者假說' };
    const SUPPORT = { not_assessed: '未判定是否支持', supports: '研究者判斷：支持', partial: '研究者判斷：部分支持',
        does_not_support: '研究者判斷：不支持' };
    let pid = '', section = '', rev = 0, notes = [], latestSVer = null;
    let hint = null;  // 帶入文字時的位置提示：{text, paragraph_index, s_ver} 或 {text, context_before, context_after}

    function el(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function status(text) { byId('ppStatus').textContent = text; }

    async function api(url, body) {
        const resp = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
        let data = {};
        try { data = await resp.json(); } catch (e) { data = {}; }
        if (!resp.ok || data.success === false) {
            const err = new Error(data.message || (resp.status === 403 ? '你沒有這個章節的寫入權限。' : '無法完成。'));
            err.status = resp.status;
            throw err;
        }
        return data;
    }

    function ensurePanel() {
        if (byId('paragraphProvenancePanel')) return;
        const panel = el('div', 'offcanvas offcanvas-end');
        panel.id = 'paragraphProvenancePanel';
        panel.tabIndex = -1;
        panel.style.width = 'min(640px,100vw)';
        panel.setAttribute('aria-labelledby', 'ppHeading');
        panel.innerHTML = `<div class="offcanvas-header"><h5 id="ppHeading">段落出處</h5><button class="btn-close" data-bs-dismiss="offcanvas" aria-label="關閉"></button></div>
        <div class="offcanvas-body">
          <p id="ppTarget" class="small text-muted mb-1"></p>
          <p class="small text-muted">把已保存的一段連到 Study 證據筆記，或標成自由寫作。出處狀態只是提示，不影響寫作、保存與匯出。
          「引文在原文中找到」只代表引文存在，不代表支持這一段。</p>
          <label class="form-label w-100 small">已保存的這一段（可先在 2B 選取文字再開啟）
            <textarea id="ppParagraph" class="form-control form-control-sm" rows="3" maxlength="8000"></textarea></label>
          <label class="form-label w-100 small">證據筆記<select id="ppNote" class="form-select form-select-sm"></select></label>
          <div id="ppNotePreview" class="small border rounded p-2 mb-2 bg-light"></div>
          <div class="d-flex flex-wrap gap-1 mb-2">
            <button type="button" id="ppLink" class="btn btn-sm btn-primary" data-provenance-link="1">連結證據筆記</button>
            <input id="ppReason" class="form-control form-control-sm w-auto flex-grow-1" maxlength="1000" placeholder="自由寫作的說明（可留空）" aria-label="自由寫作的說明">
            <button type="button" id="ppFree" class="btn btn-sm btn-outline-secondary" data-provenance-free="1">標為自由寫作</button>
          </div>
          <div id="ppStatus" role="status" aria-live="polite" class="small text-primary mb-2"></div>
          <details id="ppCoverageBox" class="mb-2" open><summary class="fw-bold small">本章每一段的出處（最新保存版本）</summary>
            <div id="ppCoverageSummary" class="small text-muted my-1"></div><div id="ppCoverage"></div></details>
          <div class="d-flex justify-content-between align-items-center"><h6 class="mb-1">這一章的出處</h6>
            <a id="ppExport" class="small">匯出出處紀錄（Markdown）</a></div>
          <div id="ppList"></div>
        </div>`;
        document.body.appendChild(panel);
        byId('ppNote').onchange = preview;
        byId('ppLink').onclick = () => save('evidence_note');
        byId('ppFree').onclick = () => save('free_writing');
    }

    function preview() {
        const box = byId('ppNotePreview');
        box.textContent = '';
        const note = notes.find(n => n.note_id === byId('ppNote').value);
        if (!note) {
            box.textContent = notes.length ? '請選一則證據筆記。' : '沒有可用的證據筆記（只有 owner／editor 能從 Study 連結）。仍可標為自由寫作。';
            return;
        }
        box.appendChild(el('div', 'fw-bold', `${note.title || '證據筆記'} · ${note.paper_id || ''} · 作者 ${note.accepted_by || '未知'}`));
        (note.summary || []).forEach(st => box.appendChild(el('div', '',
            `【${KIND[st.kind] || st.kind}】${st.text}${st.kind !== 'source_fact' ? '（' + (SUPPORT[st.claim_support || 'not_assessed']) + '）' : ''}`)));
        (note.quotes || []).forEach((q, i) => {
            const row = el('div', 'border-top pt-1 mt-1');
            row.setAttribute('data-provenance-quote', String(q.anchor_index));
            row.setAttribute('data-quote-side', q.machine_translation ? 'machine_translation' : 'original');
            const side = q.machine_translation ? '機器翻譯（非原文）' : (q.match_kind === 'paragraph_from_translation' ? '整段原文（由譯文選取推得）' : '原文');
            row.appendChild(el('span', `badge ${q.machine_translation ? 'bg-warning text-dark' : 'bg-success'} me-1`, side));
            row.appendChild(el('span', 'text-muted', `PDF 第 ${q.page || '?'} 頁 · 目前原文核對：${q.status_label}`));
            row.appendChild(el('div', '', q.quote.length > 160 ? q.quote.slice(0, 160) + '…' : q.quote));
            if (q.insertable) {
                const btn = el('button', 'btn btn-sm btn-outline-primary mt-1 me-1',
                    q.machine_translation ? `插入機器翻譯 ${i + 1} 到 2B（標示非原文；保存後自動連結）` : `插入原文引文 ${i + 1} 到 2B（保存後自動連結）`);
                btn.type = 'button';
                btn.setAttribute('data-provenance-insert-quote', String(i));
                btn.onclick = () => insertQuote(note, q);
                row.appendChild(btn);
            } else {
                const blocked = el('div', 'text-danger', `不能插入：${q.blocked_reason || q.status_label}`);
                blocked.setAttribute('data-provenance-quote-blocked', q.current_status);
                row.appendChild(blocked);
            }
            box.appendChild(row);
        });
    }

    // R5：把伺服器核對過的原文引文（連同頁碼）附加到 2B 目前章節的最後一段；不覆蓋任何既有文字。
    // 保存成功（save_ack）後自動以這段文字建立「證據筆記」出處；若保存前改寫了引文，伺服器會拒絕並提示手動連結。
    let pending = null;

    function quoteText(note, q) {
        const page = q.page ? `PDF 第 ${q.page} 頁` : 'PDF 頁碼未知';
        const paper = q.paper_id || note.paper_id || '';
        // 機器翻譯不加引號、明確標示非原文；原文才用「」引用。
        if (q.machine_translation) return `［機器翻譯，非原文引文］${q.quote}（${paper}，${page}，譯文）`;
        const kind = q.match_kind === 'paragraph_from_translation' ? '整段原文' : '原文';
        return `「${q.quote}」（${paper}，${page}，${kind}）`;
    }

    function insertQuote(note, q) {
        const content = document.querySelector(`.editor-card[data-section="${CSS.escape(section)}"] .card-content`);
        if (!content) return status('找不到 2B 目前章節的編輯區；請先在 2B 開啟這一章。');
        const before = (content.textContent || '').slice(-80);
        const p = document.createElement('p');
        p.textContent = quoteText(note, q);
        content.appendChild(p);
        content.dispatchEvent(new Event('input', { bubbles: true }));
        pending = { section, note_id: note.note_id, text: p.textContent, anchor_index: q.anchor_index, context_before: before };
        status(q.machine_translation ? '已在 2B 末尾插入機器翻譯（已標示非原文）。按 Save 保存後，會自動把這段連到證據筆記。'
            : '已在 2B 末尾插入原文引文。按 Save 保存後，會自動把這段連到證據筆記。');
    }

    async function completePending(ack) {
        if (!pending || ack.target !== 'block' || ack.section !== pending.section) return;
        const job = pending;
        pending = null;
        try {
            const data = await api(`/manuscript/api/provenance?pid=${encodeURIComponent(pid)}&section=${encodeURIComponent(job.section)}`);
            await api('/manuscript/api/provenance/link', { pid, section: job.section, base_rev: data.rev, kind: 'evidence_note',
                note_id: job.note_id, paragraph_text: job.text, s_ver: ack.ver, quote_anchor_index: job.anchor_index,
                context_before: job.context_before, context_after: '' });
            status('已保存，並自動把插入的引文連到證據筆記。');
        } catch (error) {
            status(`已保存，但沒有自動連結：${error.message}（引文若被改寫，請選取該段後手動連結）`);
        }
        if (byId('paragraphProvenancePanel')?.classList.contains('show')) await load().catch(() => {});
    }

    // 與伺服器 paragraph_provenance.anchor_label 相同：分清原文與機器翻譯；依建立筆記時的核對結果用字。
    function anchorLabel(a) {
        const where = a.side === 'translation' ? '機器翻譯（非原文）'
            : (a.match_kind === 'paragraph_from_translation' ? '整段原文（由譯文選取推得）' : '原文');
        let found = '核對狀態未知';
        if (['exact', 'relocated', 'ambiguous_in_block'].includes(a.status)) found = `建立筆記時在${a.side === 'translation' ? '機器翻譯' : '原文'}中逐字找到，不代表支持`;
        else if (a.status === 'unavailable') found = '建立筆記時讀不到原文，沒有核對';
        else if (a.status) found = '建立筆記時沒有在原文中唯一找到這段文字';
        return [where, found];
    }

    function card(item) {
        const box = el('div', 'border rounded p-2 mb-2 small bg-white');
        box.setAttribute('data-provenance-item', item.link_id);
        box.setAttribute('data-provenance-kind', item.kind);
        const head = el('div', 'd-flex flex-wrap gap-1 align-items-center mb-1');
        head.appendChild(el('span', `badge ${item.kind === 'free_writing' ? 'bg-secondary' : 'bg-primary'}`,
            item.kind === 'free_writing' ? '自由寫作（未連結已核對證據）' : '證據筆記'));
        head.appendChild(el('span', 'text-muted', `S.Ver ${item.s_ver}`));
        if (item.retracted) head.appendChild(el('span', 'badge bg-light text-dark border', '已撤回'));
        if ((item.issues || []).length) {
            const warn = el('span', 'badge bg-warning text-dark', '需要重新核對');
            warn.setAttribute('data-provenance-issues', item.issues.join(','));
            head.appendChild(warn);
        }
        box.appendChild(head);
        box.appendChild(el('blockquote', 'border-start ps-2 mb-1 text-dark', item.paragraph_text));
        const loc = item.location || {};
        if (!item.retracted && loc.status === 'matched') {
            const where = el('div', 'text-muted', `位置：最新版本${(loc.paragraphs || []).map(i => `第 ${i + 1} 段`).join('、')}`);
            where.setAttribute('data-provenance-location', 'matched');
            box.appendChild(where);
        }
        const quote = item.quote || {};
        if (quote.side && !quote.hidden) {
            const q = el('div', quote.machine_translation ? 'text-warning-emphasis' : 'text-success',
                `插入的引文：${quote.machine_translation ? '機器翻譯，非原文' : '原文'} · PDF 第 ${quote.page || '?'} 頁 · 連結當下核對：${quote.status_at_link || '未核對'}`);
            q.setAttribute('data-provenance-quote-record', quote.machine_translation ? 'machine_translation' : 'original');
            box.appendChild(q);
        }
        (item.issue_labels || []).forEach(t => box.appendChild(el('div', 'text-warning-emphasis', '・' + t)));
        if (item.kind === 'free_writing' && item.reason) box.appendChild(el('div', 'text-muted', '說明：' + item.reason));
        const note = item.note || {};
        if (note.hidden) box.appendChild(el('div', 'text-muted', '連結到未分享的證據筆記（審閱模式不顯示內容）。'));
        else if (item.kind === 'evidence_note') {
            box.appendChild(el('div', 'fw-bold', `${note.title || note.note_id} · ${note.paper_id || ''}（連結時修訂 ${note.revision_count} 次）`));
            if (note.revised_since_link) box.appendChild(el('div', 'text-warning-emphasis', '筆記在連結後已修改；這裡顯示目前分享給審閱者的版本。'));
            (note.anchors || []).forEach((a, i) => {
                const [where, found] = anchorLabel(a);
                const row = el('div', a.side === 'translation' ? 'text-warning-emphasis' : 'text-muted',
                    `證據 ${i + 1}：${where} PDF 第 ${a.page || '?'} 頁「${a.quote}」（${found}）`);
                row.setAttribute('data-provenance-anchor-side', a.side === 'translation' ? 'machine_translation' : 'original');
                box.appendChild(row);
            });
            (note.statements || []).forEach(st => {
                const row = el('div', '', `【${KIND[st.kind] || st.kind}】${st.text}`);
                if (st.kind !== 'source_fact') row.appendChild(el('span', 'badge bg-light text-dark border ms-1', SUPPORT[st.claim_support || 'not_assessed']));
                box.appendChild(row);
            });
            if (note.ai_uncertainty) box.appendChild(el('div', 'text-muted', `AI 自評不確定性：${({ low: '低', medium: '中', high: '高' })[note.ai_uncertainty.level] || '未知'}`));
        }
        box.appendChild(el('div', 'text-muted', `連結者：${(item.created_by || {}).username || '未知'}`));
        if (!item.retracted) {
            const btn = el('button', 'btn btn-sm btn-link text-danger p-0', '撤回這筆出處');
            btn.type = 'button';
            btn.setAttribute('data-provenance-retract', item.link_id);
            btn.onclick = () => retract(item.link_id);
            box.appendChild(btn);
        }
        return box;
    }

    const STATE = {
        evidence_note: ['bg-primary', '連到證據筆記'], needs_recheck: ['bg-warning text-dark', '需要重新核對'],
        free_writing: ['bg-secondary', '自由寫作'], unmarked: ['bg-light text-dark border', '尚未標記'],
    };

    function renderCoverage(cov) {
        const box = byId('ppCoverage');
        box.textContent = '';
        if (!cov) { byId('ppCoverageSummary').textContent = '這一章還沒有保存的版本。'; return; }
        const c = cov.counts;
        byId('ppCoverageSummary').textContent = `共 ${cov.total} 段：連到證據筆記 ${c.evidence_note}、需要重新核對 ${c.needs_recheck}、`
            + `自由寫作 ${c.free_writing}、尚未標記 ${c.unmarked}`
            + (cov.orphaned_links ? `；另有 ${cov.orphaned_links} 筆出處的文字已不在最新版本` : '')
            + (cov.ambiguous_links ? `；${cov.ambiguous_links} 筆出處位置不明確（文字出現多處），不算有出處` : '')
            + (cov.partial_paragraphs ? `；${cov.partial_paragraphs} 段只有部分文字有出處` : '')
            + '。只是提示，不影響保存與匯出。';
        cov.paragraphs.forEach(p => {
            const row = el('div', 'd-flex align-items-start gap-2 border-bottom py-1 small');
            row.setAttribute('data-coverage-state', p.state);
            const [cls, label] = STATE[p.state] || STATE.unmarked;
            const badge = el('span', `badge ${cls} flex-shrink-0`, p.partial ? `${label}（部分，約 ${Math.round(p.covered_ratio * 100)}%）` : label);
            if (p.partial) row.setAttribute('data-coverage-partial', '1');
            row.appendChild(badge);
            const body = el('span', 'flex-grow-1', p.text.length > 140 ? p.text.slice(0, 140) + '…' : p.text);
            if ((p.ambiguous_link_ids || []).length) {
                row.setAttribute('data-coverage-ambiguous', String(p.ambiguous_link_ids.length));
                body.appendChild(el('div', 'text-warning-emphasis', `有 ${p.ambiguous_link_ids.length} 筆出處可能指到這裡，但文字出現多處、位置不明確；請按「選這段」重新連結。`));
            }
            row.appendChild(body);
            const pick = el('button', 'btn btn-sm btn-link p-0 flex-shrink-0', '選這段');
            pick.type = 'button';
            pick.setAttribute('data-coverage-pick', String(p.index));
            pick.onclick = () => {
                byId('ppParagraph').value = p.text;
                hint = { text: p.text, paragraph_index: p.index, s_ver: latestSVer };
                status('已帶入這一段；選一則證據筆記或標為自由寫作。');
            };
            row.appendChild(pick);
            box.appendChild(row);
        });
    }

    async function load() {
        const data = await api(`/manuscript/api/provenance?pid=${encodeURIComponent(pid)}&section=${encodeURIComponent(section)}`);
        rev = data.rev;
        latestSVer = data.latest_s_ver;
        notes = data.notes || [];
        renderCoverage(data.coverage);
        const select = byId('ppNote');
        select.textContent = '';
        notes.forEach(n => {
            const opt = el('option', '', `${n.title || '證據筆記'} · ${n.paper_id || ''} · ${n.note_id}`);
            opt.value = n.note_id;
            select.appendChild(opt);
        });
        byId('ppLink').disabled = !notes.length;
        preview();
        const list = byId('ppList');
        list.textContent = '';
        if (!data.links.length) list.appendChild(el('div', 'small text-muted', '這一章還沒有段落出處。沒有出處也可以照常寫作與匯出。'));
        data.links.slice().reverse().forEach(item => list.appendChild(card(item)));
    }

    async function save(kind) {
        const body = { pid, section, base_rev: rev, kind, paragraph_text: byId('ppParagraph').value.trim() };
        if (kind === 'evidence_note') body.note_id = byId('ppNote').value;
        else body.reason = byId('ppReason').value.trim();
        if (!body.paragraph_text) return status('請先選取或貼上已保存的這一段。');
        if (hint && hint.paragraph_index !== undefined && hint.text.includes(body.paragraph_text)) {
            body.paragraph_index = hint.paragraph_index;
            if (hint.s_ver) body.s_ver = hint.s_ver;
        } else if (hint && hint.text === body.paragraph_text) {
            body.context_before = hint.context_before;
            body.context_after = hint.context_after;
        }
        try {
            await api('/manuscript/api/provenance/link', body);
            status(kind === 'free_writing' ? '已標為自由寫作。' : '已連結證據筆記。之後段落或筆記改變時，這裡會提示重新核對。');
            byId('ppReason').value = '';
            await load();
        } catch (error) {
            status(error.status === 409 ? error.message + '（已重新載入）' : error.message);
            if (error.status === 409) await load().catch(() => {});
        }
    }

    async function retract(linkId) {
        const reason = window.prompt('撤回原因（會保留在紀錄中）', '');
        if (reason === null) return;
        try {
            await api('/manuscript/api/provenance/retract', { pid, link_id: linkId, base_rev: rev, reason });
            status('已撤回；原紀錄仍保留。');
            await load();
        } catch (error) { status(error.message); }
    }

    // 編輯器選取的前後文（各 80 字）：同一段文字出現多次時，讓伺服器確定是哪一處。
    function selectionHint(text) {
        try {
            const sel = window.getSelection();
            if (!sel || !sel.rangeCount) return null;
            const range = sel.getRangeAt(0);
            const start = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
            const root = start && start.closest('.card-content');
            if (!root) return null;
            const before = document.createRange();
            before.selectNodeContents(root);
            before.setEnd(range.startContainer, range.startOffset);
            const after = document.createRange();
            after.selectNodeContents(root);
            after.setStart(range.endContainer, range.endOffset);
            return { text, context_before: before.toString().slice(-80), context_after: after.toString().slice(0, 80) };
        } catch (e) { return null; }
    }

    window.paragraphProvenance = {
        open: async function () {
            ensurePanel();
            const app = window.wsApp;
            pid = app.pid;
            section = app.collab.currentSection();
            byId('ppTarget').textContent = `${pid} · ${section} · 目前 S.Ver ${app.collab.currentSVer() || '尚未保存'}`;
            byId('ppExport').href = `/manuscript/api/provenance/export?pid=${encodeURIComponent(pid)}`;
            const selection = String(window.getSelection() || '').trim();
            hint = null;
            if (selection) {
                byId('ppParagraph').value = selection;
                hint = selectionHint(selection);
            }
            if (!app._provenanceAckBound && app.socket) {
                app._provenanceAckBound = true;
                app.socket.on('save_ack', ack => { completePending(ack || {}); });
            }
            status('載入中…');
            bootstrap.Offcanvas.getOrCreateInstance(byId('paragraphProvenancePanel')).show();
            try { await load(); status(''); } catch (error) { status(error.message); }
        },
    };
})();

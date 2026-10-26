// Roothinks source maintenance contract
// 檔案路徑: app/static/js/paragraph_provenance.js
// 模組定位: Manuscript「段落出處」面板（R4）。把已保存的一段寫作連到 Study 證據筆記，或標成自由寫作。
// 主要責任: 顯示每段的出處與「需要重新核對」提示（段落、筆記、原文改變）；撤回只追加紀錄；匯出 Markdown 研究紀錄。
// 邊界: 出處狀態只是提示，不擋寫作、保存、匯出。引文找到不代表支持；支持與否只顯示研究者的判斷。
//       所有使用者與模型文字一律 textContent。伺服器重新驗證章節權限、版本與段落文字。
// 驗證: node --check app/static/js/paragraph_provenance.js；test/integration_e2e/study_coresearch_after.cjs
(function () {
    'use strict';
    const byId = id => document.getElementById(id);
    const KIND = { source_fact: '原文事實', ai_interpretation: 'AI 解讀', human_hypothesis: '研究者假說' };
    const SUPPORT = { not_assessed: '未判定是否支持', supports: '研究者判斷：支持', partial: '研究者判斷：部分支持',
        does_not_support: '研究者判斷：不支持' };
    let pid = '', section = '', rev = 0, notes = [];

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
        (item.issue_labels || []).forEach(t => box.appendChild(el('div', 'text-warning-emphasis', '・' + t)));
        if (item.kind === 'free_writing' && item.reason) box.appendChild(el('div', 'text-muted', '說明：' + item.reason));
        const note = item.note || {};
        if (note.hidden) box.appendChild(el('div', 'text-muted', '連結到未分享的證據筆記（審閱模式不顯示內容）。'));
        else if (item.kind === 'evidence_note') {
            box.appendChild(el('div', 'fw-bold', `${note.title || note.note_id} · ${note.paper_id || ''}（連結時修訂 ${note.revision_count} 次）`));
            (note.anchors || []).forEach((a, i) => box.appendChild(el('div', 'text-muted',
                `證據 ${i + 1}：${a.side === 'translation' ? '譯文' : '原文'} p.${a.page || '?'}「${a.quote}」（引文找到，不代表支持）`)));
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

    async function load() {
        const data = await api(`/manuscript/api/provenance?pid=${encodeURIComponent(pid)}&section=${encodeURIComponent(section)}`);
        rev = data.rev;
        notes = data.notes || [];
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

    window.paragraphProvenance = {
        open: async function () {
            ensurePanel();
            const app = window.wsApp;
            pid = app.pid;
            section = app.collab.currentSection();
            byId('ppTarget').textContent = `${pid} · ${section} · 目前 S.Ver ${app.collab.currentSVer() || '尚未保存'}`;
            byId('ppExport').href = `/manuscript/api/provenance/export?pid=${encodeURIComponent(pid)}`;
            const selection = String(window.getSelection() || '').trim();
            if (selection) byId('ppParagraph').value = selection;
            status('載入中…');
            bootstrap.Offcanvas.getOrCreateInstance(byId('paragraphProvenancePanel')).show();
            try { await load(); status(''); } catch (error) { status(error.message); }
        },
    };
})();

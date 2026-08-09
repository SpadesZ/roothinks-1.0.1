// Roothinks source maintenance contract
// 檔案路徑: app/static/js/manuscript_citation.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 封裝 citation suggestion/ledger API 與前端插入流程，保留 paper/evidence identity。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/manuscript_citation.js
//路徑(./app/static/js/manuscript_citation.js) #版本 v0.1 #更版時間 20260719
// Manuscript 引用建議面板：選段落 → 取得 evidence-based 建議 → 人確認採用/拒絕。
// 設計原則：系統只建議與記錄，永不自動把 citation 寫入正文。
// 依賴 window.wsApp（pid / drafterTargetSection / selectedSections / editorCanvas）。
(function () {
    function api(path) { return path; }

    function bearer() {
        try { return window.localStorage.getItem('roothinks_api_token') || (document.body && document.body.dataset ? document.body.dataset.apiToken : '') || ''; }
        catch (e) { return ''; }
    }

    function postJSON(path, body) {
        const headers = { 'Content-Type': 'application/json' };
        const tok = bearer();
        if (tok) headers['Authorization'] = 'Bearer ' + tok;
        return fetch(api(path), { method: 'POST', headers: headers, body: JSON.stringify(body) }).then(r => r.json());
    }

    function esc(s) {
        const d = document.createElement('div');
        d.innerText = String(s == null ? '' : s);
        return d.innerHTML;
    }

    function pid() { return (window.wsApp && window.wsApp.pid) || ''; }

    function currentSection() {
        if (window.wsApp) {
            const dd = window.wsApp.drafterTargetSection;
            if (dd && dd.value) return dd.value;
            if (window.wsApp.selectedSections && window.wsApp.selectedSections.size) {
                return [...window.wsApp.selectedSections][0];
            }
        }
        return 'abstract';
    }

    // 選取文字優先；沒有選取則用游標所在段落；再退化到整個編輯區前 600 字。
    function currentClaimText() {
        const sel = window.getSelection ? window.getSelection() : null;
        const canvas = document.getElementById('editorCanvas');
        if (sel && String(sel).trim()) {
            let node = sel.anchorNode;
            while (node && node !== document.body) {
                if (node === canvas) return String(sel).trim();
                node = node.parentNode;
            }
        }
        if (sel && sel.anchorNode && canvas) {
            let node = sel.anchorNode;
            while (node && node !== canvas && node.parentNode) {
                if (node.nodeType === 1 && /^(P|LI|DIV|H1|H2|H3)$/.test(node.tagName) && node.parentNode === canvas) {
                    const t = (node.innerText || '').trim();
                    if (t) return t;
                }
                node = node.parentNode;
            }
        }
        if (canvas) return (canvas.innerText || '').trim().slice(0, 600);
        return '';
    }

    function ensurePanel() {
        if (document.getElementById('citationOffcanvas')) return;
        const html = `
        <div class="offcanvas offcanvas-end" tabindex="-1" id="citationOffcanvas" style="width: 420px;" aria-labelledby="citationOffcanvasLabel">
          <div class="offcanvas-header bg-success-subtle border-bottom">
            <h6 class="offcanvas-title fw-bold mb-0" id="citationOffcanvasLabel"><i class="bi bi-quote me-2"></i>引用建議 (Citation)</h6>
            <button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label="Close"></button>
          </div>
          <div class="offcanvas-body p-3">
            <div class="small text-muted mb-2">
              目前段落：<span class="fw-bold" id="citSectionLabel">-</span>
              <span id="citNeedBadge"></span>
            </div>
            <div class="border rounded p-2 bg-light mb-2 small" id="citClaimPreview" style="max-height:80px; overflow:auto;">-</div>
            <div class="d-flex gap-2 mb-3">
              <button class="btn btn-sm btn-success flex-grow-1" id="citRefreshBtn"><i class="bi bi-search me-1"></i>取得建議</button>
              <button class="btn btn-sm btn-outline-secondary" id="citHistoryBtn" title="已確認的引用決策"><i class="bi bi-clock-history"></i></button>
            </div>
            <div id="citSuggestions"><div class="text-muted small text-center py-4">選取正文段落後按「取得建議」。</div></div>
            <div id="citHistory" class="mt-3" style="display:none;"></div>
          </div>
        </div>`;
        const wrap = document.createElement('div');
        wrap.innerHTML = html;
        document.body.appendChild(wrap.firstElementChild);

        document.getElementById('citRefreshBtn').addEventListener('click', loadSuggestions);
        document.getElementById('citHistoryBtn').addEventListener('click', toggleHistory);
    }

    let _lastClaim = '';

    function renderSuggestions(data) {
        const host = document.getElementById('citSuggestions');
        const needBadge = document.getElementById('citNeedBadge');
        needBadge.innerHTML = data.citation_needed
            ? '<span class="badge bg-warning text-dark ms-1">建議補引用</span>'
            : '<span class="badge bg-secondary ms-1">未偵測到明確引用需求</span>';

        const items = (data && data.suggestions) || [];
        if (!items.length) {
            host.innerHTML = '<div class="text-muted small text-center py-4">沒有可用的 evidence 建議。<br>請先在 Literature 完成處理並建立索引。</div>';
            return;
        }
        host.innerHTML = items.map(function (s) {
            const snips = (s.relevant_snippets || []).slice(0, 2).map(x => `<div class="text-muted small border-start ps-2 my-1">${esc(x)}</div>`).join('');
            const segs = (s.segment_ids || []).join(',');
            return `<div class="card mb-2 shadow-sm border-0" data-paper="${esc(s.paper_id)}" data-segs="${esc(segs)}">
                <div class="card-body p-2">
                    <div class="fw-bold small">${esc(s.title || s.paper_id)}</div>
                    <div class="text-muted" style="font-size:0.72rem;">score ${Number(s.score || 0).toFixed(3)} · ${esc(s.paper_id)}</div>
                    ${snips}
                    <div class="d-flex gap-2 mt-2">
                        <button class="btn btn-xs btn-success py-0 px-2 cit-accept"><i class="bi bi-check-lg"></i> 採用</button>
                        <button class="btn btn-xs btn-outline-danger py-0 px-2 cit-reject"><i class="bi bi-x-lg"></i> 拒絕</button>
                    </div>
                </div></div>`;
        }).join('');

        host.querySelectorAll('.cit-accept').forEach(b => b.addEventListener('click', ev => decide(ev, 'accepted')));
        host.querySelectorAll('.cit-reject').forEach(b => b.addEventListener('click', ev => decide(ev, 'rejected')));
    }

    function loadSuggestions() {
        if (!pid()) { alert('請先選擇正式專案'); return; }
        const section = currentSection();
        _lastClaim = currentClaimText();
        document.getElementById('citSectionLabel').innerText = section;
        document.getElementById('citClaimPreview').innerText = _lastClaim || '(未取得段落文字)';
        const host = document.getElementById('citSuggestions');
        host.innerHTML = '<div class="text-center py-4"><span class="spinner-border spinner-border-sm"></span></div>';
        if (!_lastClaim) {
            host.innerHTML = '<div class="text-muted small text-center py-4">請先在正文選取或點擊一個段落。</div>';
            return;
        }
        postJSON('/manuscript/api/citation/suggest', { pid: pid(), text: _lastClaim, section: section })
            .then(function (data) {
                if (!data || data.success === false) {
                    host.innerHTML = `<div class="text-danger small py-3">建議失敗：${esc((data && data.message) || 'unknown')}</div>`;
                    return;
                }
                renderSuggestions(data);
            })
            .catch(err => { host.innerHTML = `<div class="text-danger small py-3">建議失敗：${esc(err)}</div>`; });
    }

    function decide(ev, status) {
        const card = ev.target.closest('[data-paper]');
        if (!card) return;
        const paperId = card.getAttribute('data-paper');
        const segs = (card.getAttribute('data-segs') || '').split(',').filter(Boolean);
        postJSON('/manuscript/api/citation/decide', {
            pid: pid(),
            section: currentSection(),
            status: status,
            paper_id: paperId,
            claim_text: _lastClaim,
            segment_ids: segs,
        }).then(function (data) {
            if (data && data.success) {
                card.classList.add(status === 'accepted' ? 'border-success' : 'border-danger', 'border');
                card.querySelector('.card-body').insertAdjacentHTML('beforeend',
                    `<div class="small mt-1 ${status === 'accepted' ? 'text-success' : 'text-danger'}">已記錄：${status === 'accepted' ? '採用' : '拒絕'}（未寫入正文）</div>`);
                card.querySelectorAll('button').forEach(b => b.disabled = true);
            } else {
                alert('記錄失敗：' + ((data && data.message) || 'unknown'));
            }
        }).catch(err => alert('記錄失敗：' + err));
    }

    function toggleHistory() {
        const box = document.getElementById('citHistory');
        if (box.style.display !== 'none') { box.style.display = 'none'; return; }
        box.style.display = 'block';
        box.innerHTML = '<div class="text-center py-3"><span class="spinner-border spinner-border-sm"></span></div>';
        fetch(api(`/manuscript/api/citation/decisions?pid=${encodeURIComponent(pid())}`))
            .then(r => r.json())
            .then(function (data) {
                const rows = (data && data.decisions) || [];
                if (!rows.length) { box.innerHTML = '<div class="text-muted small py-2">尚無已確認的引用決策。</div>'; return; }
                box.innerHTML = '<div class="fw-bold small mb-2 border-top pt-2">引用決策紀錄</div>' + rows.slice().reverse().map(function (d) {
                    const cls = d.status === 'accepted' ? 'text-success' : 'text-danger';
                    return `<div class="small border-bottom py-1"><span class="${cls} fw-bold">${d.status === 'accepted' ? '採用' : '拒絕'}</span> · ${esc(d.section_id)} · ${esc(d.paper_id || d.entry_id)}<div class="text-muted" style="font-size:0.7rem;">${esc((d.claim_text || '').slice(0, 80))}</div></div>`;
                }).join('');
            })
            .catch(err => { box.innerHTML = `<div class="text-danger small py-2">${esc(err)}</div>`; });
    }

    function open() {
        ensurePanel();
        const el = document.getElementById('citationOffcanvas');
        const oc = bootstrap.Offcanvas.getOrCreateInstance(el);
        oc.show();
        loadSuggestions();
    }

    window.manuscriptCitation = { open: open };
})();

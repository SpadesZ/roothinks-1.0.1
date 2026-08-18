// Roothinks source maintenance contract
// 檔案路徑: app/static/js/literature_chat.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 Literature 找文獻對話視窗、渲染已驗證論文與三種接手動作。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api/literature/chat*、
//         並與 literatureApp（save_context）與 literatureScholar（runSearch）接力。
// 維護邊界:
//   - 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID 後才能套用。
//   - 論文物件一律存在 JS Map 裡，用 data-card-id 取回；**絕不可把 LLM 產出的字串
//     插進 onclick 或 href 屬性**。
//   - meta.stage_errors 與被剔除筆數必須顯示；只呈現通過的部分等於謊報搜尋完整。
// 驗證: node --check app/static/js/literature_chat.js
//路徑(./app/static/js/literature_chat.js) #版本 v0.1 #更版時間 20260817-1200
class LiteratureChatHandler {
    constructor() {
        this.pid = this.resolvePid();
        this.historyLoaded = false;
        this.sending = false;
        // cardId -> paper 物件。渲染時只把 id 放進 DOM，資料留在這裡。
        this.papers = new Map();
        this.cardSeq = 0;
        this.init();
    }

    resolvePid() {
        const fromApp = (window.literatureApp && window.literatureApp.currentPid)
            ? String(window.literatureApp.currentPid).trim() : '';
        if (fromApp) return fromApp;

        const urlParams = new URLSearchParams(window.location.search);
        const fromUrl = (urlParams.get('pid') || '').trim();
        if (fromUrl) return fromUrl;

        return (localStorage.getItem('activePid') || '').trim();
    }

    escapeHtml(text) {
        return String(text === null || text === undefined ? '' : text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // 只接受 https 的絕對網址。LLM 產出的字串直接進 href 是 javascript: 注入的入口。
    safeUrl(url) {
        const raw = String(url || '').trim();
        if (!/^https:\/\/[^\s"'<>]+$/i.test(raw)) return '';
        return raw;
    }

    init() {
        const panel = document.getElementById('litChatPanel');
        if (!panel) {
            console.warn('[LitChat] #litChatPanel not found, chat disabled.');
            return;
        }

        panel.addEventListener('shown.bs.offcanvas', () => {
            this.pid = this.resolvePid();
            if (!this.historyLoaded) this.loadHistory();
            const input = document.getElementById('litChatInput');
            if (input) input.focus();
        });

        const sendBtn = document.getElementById('btnLitChatSend');
        if (sendBtn) sendBtn.addEventListener('click', () => this.send());

        const input = document.getElementById('litChatInput');
        if (input) {
            input.addEventListener('keydown', (e) => {
                // Enter 送出、Shift+Enter 換行：與一般聊天視窗一致。
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.send();
                }
            });
        }

        const clearBtn = document.getElementById('btnLitChatClear');
        if (clearBtn) clearBtn.addEventListener('click', () => this.clearHistory());

        const list = document.getElementById('litChatMessages');
        if (list) list.addEventListener('click', (e) => this.onListClick(e));

        console.log('[LitChat] v0.1 attached.');
    }

    setStatus(text, busy) {
        const el = document.getElementById('litChatStatus');
        if (el) {
            el.innerHTML = busy
                ? `<span class="spinner-border spinner-border-sm me-2"></span>${this.escapeHtml(text)}`
                : this.escapeHtml(text);
        }
        const btn = document.getElementById('btnLitChatSend');
        if (btn) btn.disabled = !!busy;
    }

    scrollToBottom() {
        const list = document.getElementById('litChatMessages');
        if (list) list.scrollTop = list.scrollHeight;
    }

    // ---- 資料載入 ----------------------------------------------------
    loadHistory() {
        this.pid = this.resolvePid();
        if (!this.pid) {
            this.renderSystemNote('找不到專案 ID，請先到 PAQ 選定專案。');
            return;
        }
        const requestedPid = this.pid;
        this.setStatus('載入對話紀錄…', true);

        fetch(`/api/literature/chat/history?pid=${encodeURIComponent(requestedPid)}`)
            .then(r => r.json())
            .then(d => {
                // 載入期間使用者可能已切換專案，套用前先核對。
                if (requestedPid !== this.resolvePid()) return;
                const list = document.getElementById('litChatMessages');
                if (list) list.innerHTML = '';
                this.papers.clear();

                const records = (d && d.records) || [];
                if (!records.length) {
                    this.renderSystemNote('問我要找什麼文獻，例如：「幫我找聽覺腹側路徑與語音辨識的關鍵論文」。');
                } else {
                    records.forEach(rec => {
                        this.renderUser(rec.user || '');
                        this.renderAssistant(rec.ai || '', rec.papers || [], rec.meta || {});
                    });
                }
                this.historyLoaded = true;
                this.setStatus('', false);
                this.scrollToBottom();
            })
            .catch(err => {
                console.error('[LitChat] history failed', err);
                this.setStatus('對話紀錄載入失敗', false);
            });
    }

    send() {
        if (this.sending) return;
        const input = document.getElementById('litChatInput');
        const message = input ? String(input.value || '').trim() : '';
        if (!message) return;

        this.pid = this.resolvePid();
        if (!this.pid) {
            this.renderSystemNote('找不到專案 ID，請先到 PAQ 選定專案。');
            return;
        }

        const requestedPid = this.pid;
        this.sending = true;
        if (input) input.value = '';
        this.renderUser(message);
        this.setStatus('思考中：判斷是否需要搜尋 → B/C 上網檢索 → 交叉查核 → 回查驗證…', true);

        fetch('/api/literature/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pid: requestedPid, message: message })
        })
            .then(r => r.json())
            .then(d => {
                if (requestedPid !== this.resolvePid()) return;
                if (!d || d.status !== 'success') {
                    this.renderSystemNote(`錯誤：${(d && d.message) || '未知錯誤'}`);
                    return;
                }
                this.renderAssistant(d.reply || '', d.papers || [], d.meta || {});
                if (d.persisted === false) {
                    this.renderSystemNote('注意：這一輪對話沒有存檔成功，重新整理後會消失。');
                }
            })
            .catch(err => {
                console.error('[LitChat] send failed', err);
                this.renderSystemNote(`連線失敗：${err}`);
            })
            .finally(() => {
                this.sending = false;
                this.setStatus('', false);
                this.scrollToBottom();
            });
    }

    clearHistory() {
        this.pid = this.resolvePid();
        if (!this.pid) return;
        if (!confirm('確定清除這個專案的找文獻對話紀錄？（不影響文獻庫）')) return;

        fetch('/api/literature/chat/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pid: this.pid })
        })
            .then(r => r.json())
            .then(() => {
                const list = document.getElementById('litChatMessages');
                if (list) list.innerHTML = '';
                this.papers.clear();
                this.renderSystemNote('對話紀錄已清除。');
            })
            .catch(err => console.error('[LitChat] clear failed', err));
    }

    // ---- 渲染 --------------------------------------------------------
    appendHtml(html) {
        const list = document.getElementById('litChatMessages');
        if (!list) return;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = html;
        while (wrapper.firstChild) list.appendChild(wrapper.firstChild);
        this.scrollToBottom();
    }

    renderSystemNote(text) {
        this.appendHtml(`
            <div class="text-center my-2">
                <span class="badge bg-light text-secondary border">${this.escapeHtml(text)}</span>
            </div>`);
    }

    renderUser(text) {
        if (!String(text || '').trim()) return;
        this.appendHtml(`
            <div class="d-flex justify-content-end mb-2">
                <div class="bg-primary text-white rounded-3 px-3 py-2" style="max-width: 85%; white-space: pre-wrap;">
                    ${this.escapeHtml(text)}
                </div>
            </div>`);
    }

    renderAssistant(reply, papers, meta) {
        const safeMeta = meta || {};
        let html = `
            <div class="d-flex justify-content-start mb-2">
                <div class="bg-white border rounded-3 px-3 py-2 shadow-sm" style="max-width: 92%; white-space: pre-wrap;">
                    ${this.escapeHtml(reply)}
                </div>
            </div>`;

        html += this.renderMetaBar(safeMeta, (papers || []).length);

        if (papers && papers.length) {
            const groupId = `litchat-group-${++this.cardSeq}`;
            html += `<div class="mb-3" id="${groupId}">`;
            html += `
                <div class="d-flex justify-content-between align-items-center small text-muted mb-1 px-1">
                    <span><i class="bi bi-check2-circle text-success me-1"></i>已驗證文獻 ${papers.length} 篇</span>
                    <button type="button" class="btn btn-sm btn-outline-primary py-0"
                            data-lit-action="adopt-all" data-group-id="${groupId}">
                        <i class="bi bi-collection me-1"></i>全部匯入文獻庫
                    </button>
                </div>`;
            papers.forEach((paper, idx) => {
                html += this.renderPaperCard(paper, idx + 1, groupId);
            });
            html += `</div>`;
        }

        this.appendHtml(html);
    }

    renderMetaBar(meta, paperCount) {
        const bits = [];
        if (meta.cache_hit) bits.push('<span class="badge bg-secondary-subtle text-secondary-emphasis border">快取結果</span>');
        if (Array.isArray(meta.searched_by) && meta.searched_by.length) {
            bits.push(`<span class="badge bg-light text-dark border">搜尋腳 ${this.escapeHtml(meta.searched_by.join('/'))}</span>`);
        }
        if (meta.intent_source === 'keyword') {
            bits.push('<span class="badge bg-warning-subtle text-warning-emphasis border">意圖判斷退化為關鍵字</span>');
        }

        // stage_errors 一定要顯示：跑一半的搜尋不能長得像完整結果。
        const stageErrors = meta.stage_errors || {};
        const errKeys = Object.keys(stageErrors);
        let html = '';
        if (bits.length) {
            html += `<div class="d-flex flex-wrap gap-1 mb-2 px-1">${bits.join('')}</div>`;
        }

        // 被剔除的必須看得到、看得出原因：那是這個功能唯一能證明自己有在擋幻覺的地方。
        const dropped = Array.isArray(meta.dropped) ? meta.dropped : [];
        if (dropped.length) {
            const rows = dropped.slice(0, 20)
                .map(d => `<li>${this.escapeHtml(d.title || '(無標題)')} — <span class="text-muted">${this.escapeHtml(d.reason || '')}</span></li>`)
                .join('');
            html += `
                <details class="small text-muted px-1 mb-2">
                    <summary class="text-warning-emphasis">已剔除 ${dropped.length} 筆未通過驗證的結果</summary>
                    <ul class="mt-1 mb-0 ps-3">${rows}</ul>
                </details>`;
        }

        if (errKeys.length) {
            const lines = errKeys
                .map(k => `${this.escapeHtml(k)}：${this.escapeHtml(String(stageErrors[k]))}`)
                .join('<br>');
            html += `
                <div class="alert alert-warning py-2 px-3 small mb-2">
                    <i class="bi bi-exclamation-triangle-fill me-1"></i>
                    <strong>部分階段未完成，以下結果不完整：</strong><br>${lines}
                </div>`;
        }
        if (!paperCount && !errKeys.length && meta.should_search) {
            html += `<div class="small text-muted px-1 mb-2">本輪沒有任何文獻通過來源驗證。</div>`;
        }
        return html;
    }

    renderPaperCard(paper, num, groupId) {
        const cardId = `litchat-paper-${++this.cardSeq}`;
        this.papers.set(cardId, paper);

        const url = this.safeUrl(paper.url);
        const scholarUrl = this.safeUrl(paper.scholar_url);
        const title = this.escapeHtml(paper.title || '(無標題)');
        const titleHtml = url
            ? `<a href="${url}" target="_blank" rel="noopener noreferrer" class="text-decoration-none fw-semibold">${title}</a>`
            : `<span class="fw-semibold">${title}</span>`;

        const authors = Array.isArray(paper.authors) ? paper.authors.slice(0, 3).join(', ') : '';
        const metaLine = [
            authors,
            paper.year || '',
            paper.venue || ''
        ].filter(Boolean).map(v => this.escapeHtml(v)).join(' · ');

        const badges = [];
        if (paper.source) {
            badges.push(`<span class="badge bg-info-subtle text-info-emphasis border">${this.escapeHtml(paper.source)}</span>`);
        }
        if (paper.link_kind) {
            badges.push(`<span class="badge bg-light text-dark border">${this.escapeHtml(paper.link_kind)}</span>`);
        }
        if (paper.consensus === 'both') {
            badges.push('<span class="badge bg-success-subtle text-success-emphasis border">B/C 共識</span>');
        } else if (paper.consensus) {
            badges.push(`<span class="badge bg-light text-secondary border">${this.escapeHtml(paper.consensus === 'b_only' ? '僅 B 找到' : '僅 C 找到')}</span>`);
        }
        if (paper.doi) {
            badges.push('<span class="badge bg-success-subtle text-success-emphasis border">DOI 已驗證</span>');
        }

        return `
        <div class="border rounded bg-white p-2 mb-2" id="${cardId}" data-lit-card="1">
            <div class="d-flex gap-2">
                <div class="text-secondary small fw-bold">${String(num).padStart(2, '0')}</div>
                <div class="flex-grow-1">
                    <div class="mb-1" style="line-height:1.4;">${titleHtml}</div>
                    ${metaLine ? `<div class="small text-muted">${metaLine}</div>` : ''}
                    ${paper.why ? `<div class="small text-muted mt-1"><i class="bi bi-info-circle me-1"></i>${this.escapeHtml(paper.why)}</div>` : ''}
                    <div class="d-flex flex-wrap gap-1 mt-1">${badges.join('')}</div>
                    <div class="d-flex flex-wrap gap-1 mt-2">
                        <button type="button" class="btn btn-sm btn-outline-secondary py-0"
                                data-lit-action="fill" data-card-id="${cardId}">
                            <i class="bi bi-box-arrow-in-down me-1"></i>帶入主題欄
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-primary py-0"
                                data-lit-action="adopt" data-card-id="${cardId}" data-group-id="${groupId}">
                            <i class="bi bi-collection me-1"></i>匯入文獻庫
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-dark py-0"
                                data-lit-action="fill-search" data-card-id="${cardId}">
                            <i class="bi bi-search me-1"></i>帶入並搜尋
                        </button>
                        ${scholarUrl ? `<a class="btn btn-sm btn-link py-0 text-decoration-none" href="${scholarUrl}" target="_blank" rel="noopener noreferrer">Scholar</a>` : ''}
                    </div>
                </div>
            </div>
        </div>`;
    }

    // ---- 接手動作 ----------------------------------------------------
    onListClick(e) {
        const btn = e.target.closest('[data-lit-action]');
        if (!btn) return;
        e.preventDefault();

        const action = btn.getAttribute('data-lit-action');
        if (action === 'adopt-all') {
            const groupId = btn.getAttribute('data-group-id');
            this.adoptGroup(groupId, btn);
            return;
        }

        const paper = this.papers.get(btn.getAttribute('data-card-id'));
        if (!paper) return;

        if (action === 'fill') {
            this.fillTopic(paper, false);
        } else if (action === 'fill-search') {
            this.fillTopic(paper, true);
        } else if (action === 'adopt') {
            this.adopt([paper], btn);
        }
    }

    // 帶入 2.1 研究主題欄。用標題而不是 APA 全文：2.2A 吃的是檢索用主題字串。
    fillTopic(paper, alsoSearch) {
        const topicInput = document.getElementById('manualTopic');
        if (!topicInput) return;
        topicInput.value = String(paper.title || '').trim();
        topicInput.dispatchEvent(new Event('input', { bubbles: true }));

        if (!alsoSearch) {
            const panel = document.getElementById('litChatPanel');
            const oc = panel && window.bootstrap ? window.bootstrap.Offcanvas.getInstance(panel) : null;
            if (oc) oc.hide();
            topicInput.focus();
            topicInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return;
        }

        this.setStatus('儲存主題並執行 2.2A 搜尋…', true);
        fetch('/api/literature/save_context', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pid: this.resolvePid(), context: topicInput.value })
        })
            .then(r => r.json())
            .then(d => {
                if (!d || d.status !== 'success') throw new Error((d && d.message) || 'save_context failed');
                if (window.literatureApp && typeof window.literatureApp.loadContextHistory === 'function') {
                    window.literatureApp.loadContextHistory();
                }
                if (window.literatureScholar && typeof window.literatureScholar.runSearch === 'function') {
                    window.literatureScholar.runSearch();
                    this.renderSystemNote('已帶入主題並啟動 2.2A 搜尋，結果顯示在頁面上。');
                } else {
                    this.renderSystemNote('已帶入並儲存主題，請自行按「執行雲端分析」。');
                }
            })
            .catch(err => this.renderSystemNote(`帶入失敗：${err}`))
            .finally(() => this.setStatus('', false));
    }

    adoptGroup(groupId, btn) {
        const group = groupId ? document.getElementById(groupId) : null;
        if (!group) return;
        const papers = [];
        group.querySelectorAll('[data-lit-card]').forEach(card => {
            const paper = this.papers.get(card.id);
            if (paper) papers.push(paper);
        });
        if (papers.length) this.adopt(papers, btn);
    }

    adopt(papers, btn) {
        const pid = this.resolvePid();
        if (!pid) return;
        const topicInput = document.getElementById('manualTopic');

        if (btn) btn.disabled = true;
        fetch('/api/literature/chat/adopt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pid: pid,
                papers: papers,
                topic: topicInput ? topicInput.value : ''
            })
        })
            .then(r => r.json())
            .then(d => {
                if (!d || d.status !== 'success') throw new Error((d && d.message) || 'adopt failed');
                this.renderSystemNote(
                    `已匯入文獻庫：新增 ${d.added || 0} 筆、更新 ${d.updated || 0} 筆（狀態為 candidate，請到 2.2B 做納入判斷）。`
                );
                if (window.literatureLibrary && typeof window.literatureLibrary.loadLibrary === 'function') {
                    window.literatureLibrary.loadLibrary();
                }
            })
            .catch(err => this.renderSystemNote(`匯入失敗：${err}`))
            .finally(() => { if (btn) btn.disabled = false; });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // 等 literatureApp bootstrap 決定出 currentPid 之後再建立，避免一開始就抓不到 PID。
    setTimeout(() => {
        window.literatureChat = new LiteratureChatHandler();
    }, 700);
});

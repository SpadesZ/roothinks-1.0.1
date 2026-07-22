(function () {
    const ORDER = ['paq', 'literature', 'study', 'manuscript', 'submit'];
    const TONE = {
        complete: 'success',
        ready: 'primary',
        working: 'warning',
        blocked: 'secondary',
        empty: 'secondary'
    };

    function ensureStyles() {
        if (document.getElementById('rt-workflow-status-style')) return;
        const style = document.createElement('style');
        style.id = 'rt-workflow-status-style';
        style.textContent = `
            .rt-workflow-strip { border: 1px solid #dde4f2; background: #fff; border-radius: 7px; box-shadow: 0 1px 3px rgba(13, 40, 90, .05); }
            .rt-workflow-compact { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 4px 8px; }
            .rt-workflow-head { display: flex; align-items: center; gap: 6px; min-width: 0; flex: 0 1 300px; }
            .rt-workflow-head-label { flex: 0 0 auto; white-space: nowrap; font-weight: 700; font-size: .76rem; color: #4f67ff; }
            .rt-workflow-head-title { min-width: 0; color: #667085; font-size: .72rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
            .rt-workflow-items { display: flex; flex: 1 1 auto; min-width: 0; gap: 6px; align-items: center; overflow-x: auto; scrollbar-width: thin; }
            .rt-workflow-item { min-width: 116px; max-width: 190px; flex: 1 0 116px; border: 1px solid #e6ebf5; border-radius: 6px; padding: 3px 6px; background: #fdfefe; color: inherit; text-decoration: none; }
            .rt-workflow-item:hover { border-color: #b9c7ff; background: #f8faff; color: inherit; }
            .rt-workflow-item.is-active { border-color: #5c7cfa; background: #f5f7ff; }
            .rt-workflow-title { font-weight: 700; font-size: .74rem; display: flex; justify-content: space-between; align-items: center; gap: 6px; }
            .rt-workflow-title .badge { font-size: .62rem; padding: .12rem .32rem; }
            .rt-workflow-detail { color: #667085; font-size: .67rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
            .rt-workflow-next { display: none; }
            .rt-workflow-full { padding: 10px; }
            .rt-workflow-full .rt-workflow-head { flex-basis: auto; margin-bottom: 8px; }
            .rt-workflow-full .rt-workflow-items { display: flex; flex-direction: column; align-items: stretch; overflow: visible; }
            .rt-workflow-full .rt-workflow-item { max-width: none; min-width: 0; width: 100%; flex-basis: auto; padding: 8px 10px; }
            .rt-workflow-full .rt-workflow-title { font-size: .82rem; }
            .rt-workflow-full .rt-workflow-detail { font-size: .75rem; margin-top: 3px; }
            .rt-workflow-full .rt-workflow-next { display: block; color: #344054; font-size: .74rem; margin-top: 4px; }
        `;
        document.head.appendChild(style);
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[ch]));
    }

    function resolvePid(el) {
        const explicit = String(el.dataset.pid || '').trim();
        if (explicit) return explicit;
        const qs = new URLSearchParams(window.location.search);
        const queryPid = String(qs.get('pid') || '').trim();
        if (queryPid) return queryPid;
        const parts = window.location.pathname.split('/').filter(Boolean);
        if (parts[0] === 'study' && parts[1] === 'project' && parts[2]) {
            return decodeURIComponent(parts[2]);
        }
        return '';
    }

    function renderModule(module, focus, variant) {
        const key = module.key || '';
        const tone = TONE[module.status] || 'secondary';
        const active = key === focus ? ' is-active' : '';
        const href = escapeHtml(module.href || '#');
        const label = escapeHtml(module.label || key);
        const detail = escapeHtml(module.detail || '');
        const next = escapeHtml(module.next_action || '');
        const status = escapeHtml(module.status || 'unknown');
        const title = [label, detail, next].filter(Boolean).join(' - ');
        const compact = variant !== 'full';
        const fullNext = variant === 'full' && next
            ? `<a class="btn btn-sm btn-outline-${tone} mt-2" href="${href}">${next}</a>`
            : `<div class="rt-workflow-next">${next}</div>`;
        if (compact) {
            return `
                <a class="rt-workflow-item${active}" href="${href}" title="${title}">
                    <div class="rt-workflow-title">
                        <span>${label}</span>
                        <span class="badge bg-${tone}-subtle text-${tone} border border-${tone}">${status}</span>
                    </div>
                    <div class="rt-workflow-detail">${detail}</div>
                </a>
            `;
        }
        return `
            <div class="rt-workflow-item${active}">
                <div class="rt-workflow-title">
                    <span>${label}</span>
                    <span class="badge bg-${tone}-subtle text-${tone} border border-${tone}">${status}</span>
                </div>
                <div class="rt-workflow-detail" title="${detail}">${detail}</div>
                ${fullNext}
            </div>
        `;
    }

    function render(el, data) {
        const focus = String(el.dataset.focus || '').trim();
        const variant = String(el.dataset.variant || 'strip').trim();
        const project = data.project || {};
        const title = project.research_title || project.name || data.formal_pid || data.pid || '';
        const modules = data.modules || {};
        const items = ORDER.map((key) => modules[key]).filter(Boolean).map((m) => renderModule(m, focus, variant)).join('');
        const fullClass = variant === 'full' ? ' rt-workflow-full' : ' rt-workflow-compact';
        el.innerHTML = `
            <div class="rt-workflow-strip${fullClass}">
                <div class="rt-workflow-head">
                    <div class="rt-workflow-head-label"><i class="bi bi-diagram-3 me-1"></i>五大模組</div>
                    <div class="rt-workflow-head-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
                </div>
                <div class="rt-workflow-items">${items}</div>
            </div>
        `;
    }

    async function hydrate(el) {
        ensureStyles();
        const pid = resolvePid(el);
        if (!pid) {
            el.innerHTML = '<div class="alert alert-light border small mb-0">尚未選擇專案，請先從 Dashboard 或研究列表進入。</div>';
            return;
        }
        el.innerHTML = '<div class="rt-workflow-strip small text-muted">讀取五大模組狀態...</div>';
        try {
            const res = await fetch(`/api/project/workflow/${encodeURIComponent(pid)}`);
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.message || `HTTP ${res.status}`);
            }
            render(el, data);
        } catch (err) {
            el.innerHTML = `<div class="alert alert-warning small mb-0">五大模組狀態讀取失敗：${escapeHtml(err.message || err)}</div>`;
        }
    }

    function init() {
        document.querySelectorAll('[data-workflow-status]').forEach((el) => hydrate(el));
    }

    window.roothinksWorkflowStatus = { init, hydrate };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

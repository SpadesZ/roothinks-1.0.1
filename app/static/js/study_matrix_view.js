// Roothinks source maintenance contract
// 檔案路徑: app/static/js/study_matrix_view.js
// 模組定位: Study 研究矩陣（R7）。由 study_view.js 的 renderMatrix() 轉交；本檔只負責矩陣，不碰閱讀器。
// 主要責任:
//   1. 把後端記錄（roothinks.study.matrix/2 與舊版單一比較記錄）轉成同一個表格：列＝維度、欄＝基準＋每篇對照論文（照記錄順序）。
//   2. 每格顯示狀態（有報告／未報告／不確定／不可比較／失敗）、文字（過長可「展開」）、原文中找到的引文（點了開閱讀器到該段）、
//      被排除的 AI 引文，以及每篇的「比較依據」涵蓋說明。
//   3. 比較設定：研究者勾選或自填維度、決定論文順序與基準；側欄「生成比較矩陣」改送勾選順序、基準與維度。
// 上下游: POST /api/study/compare；StudyCore（setLoading／cacheMatrix／loadMatrixHistory／showToast）；
//         StudyView（revealAnchor 開閱讀器、handleCellClick 帶入家教、_updateDimensionFilters 維度篩選）。
// 維護邊界: 引文只表示「原文中找得到」，不表示支持描述，畫面不得用「驗證」描述；缺資料不得排名，未報告格不顯示差異描述；
//           AI 協助比較、研究者判斷；不確定不阻擋任何寫作或匯出動作。localStorage 只存個人偏好（維度勾選）。
// 驗證: node --check app/static/js/study_matrix_view.js；test/integration_e2e/study_matrix_r7_browser.cjs
(function () {
    'use strict';

    const V2 = 'roothinks.study.matrix/2';
    const DEFAULT_DIMENSIONS = ['研究問題與目標', '方法與系統設計', '資料與實驗設定', '結果與證據強度', '限制與風險'];
    const STATUS = {
        reported: { label: '有報告', hint: 'AI 在提供的段落中找到描述，並附原文中找得到的引文' },
        not_reported: { label: '未報告', hint: '在提供給 AI 的段落中沒有找到；不比較、不排名' },
        uncertain: { label: '不確定', hint: '描述不完整、模糊，或 AI 沒有給出原文中找得到的引文' },
        not_comparable: { label: '不可比較', hint: '條件、單位、指標或比較基線不同，不能直接比較' },
        failed: { label: '失敗', hint: '這一格沒有得到比較結果' },
        legacy: { label: '舊版・未核對', hint: '舊版記錄：沒有原文引文與狀態核對' },
    };
    const CLAMP_CHARS = 140;
    const PREF_KEY = 'roothinks.study.matrix.dimensions';

    function esc(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function isNA(text) {
        const t = String(text ?? '').trim();
        return !t || /^(n\/?a|na|none)$/i.test(t);
    }

    function pageRangeLabel(coverage) {
        const span = coverage && Array.isArray(coverage.page_span) ? coverage.page_span : null;
        if (!span) return '';
        return span[0] === span[1] ? `p.${span[0]}` : `p.${span[0]}–p.${span[1]}`;
    }

    // ---- 資料正規化：新舊記錄都轉成 {columns, dimensions, cell(col, dim)} ----
    function normalizeV2(data) {
        const papers = (Array.isArray(data.papers) ? data.papers.slice() : []).sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
        const comps = new Map((data.comparisons || []).map(c => [String(c.target_paper_id || ''), c]));
        const columns = papers.map((p, i) => {
            const comp = i === 0 ? null : comps.get(String(p.paper_id));
            return {
                paperId: String(p.paper_id), title: p.title || p.paper_id, role: i === 0 ? 'baseline' : 'compared', position: i,
                coverage: p.coverage || null, fulltext: p.fulltext_available !== false,
                failed: !!(comp && (comp.error || comp.status === 'failed')),
                failReason: comp ? (comp.reason || comp.analysis || '') : '',
            };
        });
        const dims = (data.dimensions || []).map(d => ({ id: String(d.id), label: String(d.label) }));
        const baseCells = data.baseline_cells || {};
        const titleOf = id => (columns.find(c => c.paperId === id) || {}).title || id;
        return {
            schema: V2, legacy: false, criteria: data.criteria || '', columns, dimensions: dims, systemCheck: data.system_check || null,
            cell(col, dim) {
                if (col.role === 'baseline') {
                    const cell = baseCells[dim.id] || { status: 'failed', reason: '沒有資料' };
                    return { ...cell, sourceTitle: cell.source_pair ? titleOf(cell.source_pair) : '' };
                }
                const comp = comps.get(col.paperId);
                return (comp && comp.cells && comp.cells[dim.id]) || { status: 'failed', reason: (comp && comp.reason) || '沒有資料' };
            },
        };
    }

    function normalizeLegacy(data) {
        const baseline = data.baseline || {};
        const comps = Array.isArray(data.comparisons) ? data.comparisons : [];
        const baseId = String(baseline.id || baseline.paper_id || 'paper_a');
        const columns = [{ paperId: baseId, title: baseline.title || baseId, role: 'baseline', position: 0, coverage: null, fulltext: true }];
        comps.forEach((c, i) => {
            const id = String(c.target_paper_id || c.paper_id || c.id || c.target || `paper_${i + 1}`);
            columns.push({
                paperId: id, title: c.target_paper_title || c.target_title || c.target || id, role: 'compared', position: i + 1,
                coverage: null, fulltext: true, failed: !!(c.error || c.status === 'failed'), failReason: c.analysis || c.error || '',
                comp: c,
            });
        });
        const labels = [];
        comps.forEach(c => (Array.isArray(c.comparison_points) ? c.comparison_points : []).forEach(p => {
            if (p && p.dimension && !labels.includes(String(p.dimension))) labels.push(String(p.dimension));
        }));
        if (!labels.length) labels.push('核心分析');
        const dims = labels.map((label, i) => ({ id: `legacy-${i}`, label }));
        const pointOf = (c, label) => (Array.isArray(c.comparison_points) ? c.comparison_points : []).find(p => p && String(p.dimension) === label) || null;
        const side = text => (isNA(text) ? { status: 'not_reported', text: '', reason: '舊版記錄標為 N/A（可能只是當時沒讀到）' }
            : { status: 'legacy', text: String(text) });
        return {
            schema: data.schema || 'roothinks.study.matrix/1', legacy: true, criteria: data.criteria || 'General', columns, dimensions: dims,
            systemCheck: null,
            cell(col, dim) {
                if (col.role === 'baseline') {
                    const found = comps.map(c => pointOf(c, dim.label)).find(p => p && !isNA(p.baseline_view));
                    if (found) return side(found.baseline_view);
                    if (comps.some(c => pointOf(c, dim.label))) return side('N/A');
                    return { status: 'legacy', text: '', reason: '舊版記錄只有整體比較文字' };
                }
                const c = col.comp || {};
                if (col.failed) return { status: 'failed', text: '', reason: col.failReason || '比較失敗' };
                const p = pointOf(c, dim.label);
                if (!p) {
                    const whole = c.analysis || c.content || '';
                    return whole ? { status: 'legacy', text: String(whole) } : { status: 'failed', text: '', reason: '舊版記錄沒有這個維度' };
                }
                const cell = side(p.target_view);
                const baseMissing = isNA(p.baseline_view);
                if (cell.status === 'not_reported' || baseMissing) {
                    // 舊記錄也套用同一規則：任一方缺資料，就不顯示當時的差異評析（舊版會照樣排名）。
                    if (!isNA(p.synthesis)) cell.relation_withheld = 'missing';
                } else if (!isNA(p.synthesis)) {
                    cell.relation = String(p.synthesis);
                    cell.relation_legacy = true;
                }
                return cell;
            },
        };
    }

    class StudyMatrixView {
        constructor() {
            this.selectionOrder = [];
            this.model = null;
            this.lastData = null;
            this.dims = this._loadDims();
            this.extraDims = this.dims.filter(d => !DEFAULT_DIMENSIONS.includes(d));
            document.addEventListener('change', (e) => this._onPaperCheckbox(e));
            this._installSidebarPanel();
        }

        // ---------- 偏好（個人維度勾選；讀寫失敗不影響功能） ----------
        _loadDims() {
            try {
                const raw = JSON.parse(localStorage.getItem(PREF_KEY) || 'null');
                if (Array.isArray(raw) && raw.length) return raw.map(String).filter(Boolean).slice(0, 8);
            } catch (e) { /* 無法讀取就用預設 */ }
            return DEFAULT_DIMENSIONS.slice();
        }

        _saveDims() {
            try { localStorage.setItem(PREF_KEY, JSON.stringify(this.dims)); } catch (e) { /* 忽略 */ }
        }

        normalize(data) {
            if (!data || typeof data !== 'object') return null;
            return data.schema === V2 && Array.isArray(data.papers) ? normalizeV2(data) : normalizeLegacy(data);
        }

        // ---------- 渲染 ----------
        render(data, view) {
            const sv = view || window.studyView;
            const host = sv && sv.matrixContainer ? sv.matrixContainer : document.getElementById('matrixContainer');
            if (!host) return;
            if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) {
                host.innerHTML = `<div class="rt-matrix-empty" role="status"><p class="fw-bold">矩陣數據為空</p>
                    <p class="small text-muted">請至少選擇 2 篇文獻或檢查 API 回應</p></div>`;
                return;
            }
            if (data.error) {
                host.innerHTML = `<div class="rt-matrix-empty text-danger" role="alert"><p class="fw-bold">${esc(data.error)}</p></div>`;
                return;
            }
            const model = this.normalize(data);
            this.model = model;
            this.lastData = data;
            const n = model.columns.length;
            const check = model.systemCheck;
            const caption = `研究矩陣：列是比較維度，欄是論文；第 1 欄是基準，其餘依研究者選的順序。`;
            let html = `<section class="rt-matrix" aria-labelledby="rt-matrix-heading" data-matrix-schema="${esc(model.schema)}">
                <header class="rt-matrix-head">
                    <div class="rt-matrix-title-row">
                        <h2 id="rt-matrix-heading" class="rt-matrix-title">研究矩陣：${esc(model.criteria || '預設維度')}</h2>
                        <span class="rt-matrix-count" data-matrix-paper-count="${n}">${n} 篇論文</span>
                    </div>
                    <p class="rt-matrix-notice" role="note" data-matrix-notice>AI 協助整理與比較，判斷由你決定。
                        每格只根據 AI 讀到的段落；「原文中找到」只代表引文逐字存在於原文，不代表它支持該格的描述。缺資料的格子不比較、不排名。</p>`;
            if (model.legacy) {
                html += `<p class="rt-matrix-legacy" role="note" data-matrix-legacy>這是舊版記錄：沒有原文引文、沒有狀態核對，也沒有記錄 AI 讀了哪些段落，只能當參考。
                    可在下方「比較設定」用相同論文重新比較。</p>`;
            }
            if (check) {
                html += `<p class="rt-matrix-check" data-system-check>系統檢查（只檢查引文是否逐字存在）：原文中找到的引文 ${Number(check.quotes_found || 0)} 段、
                    已排除 ${Number(check.quotes_rejected || 0)} 段（找不到、太短或無法歸屬）；模型呼叫 ${Number(check.model_calls || 0)} 次。</p>`;
            }
            html += this._settingsHtml(model);
            html += `<div class="rt-matrix-scroll" role="region" aria-labelledby="rt-matrix-heading" tabindex="0">
                <table class="roothinks-matrix rt-matrix-table" data-matrix-columns="${n}">
                <caption class="rt-matrix-caption">${caption}</caption>
                <thead><tr><th scope="col" class="rt-dim-col">比較維度</th>`;
            model.columns.forEach((col, i) => { html += this._headerHtml(col, i); });
            html += '</tr></thead><tbody>';
            model.dimensions.forEach(dim => {
                html += `<tr class="matrix-row" data-dim="${esc(encodeURIComponent(dim.label))}" data-dim-id="${esc(dim.id)}">
                    <th scope="row" class="rt-dim-col">${esc(dim.label)}</th>`;
                model.columns.forEach(col => { html += this._cellHtml(col, dim, model.cell(col, dim)); });
                html += '</tr>';
            });
            html += '</tbody></table></div></section>';
            host.innerHTML = html;
            this._bind(host);
            if (sv) {
                if (sv.matrixTitle) sv.matrixTitle.innerText = `已比較 ${n} 篇文獻`;
                if (typeof sv._updateDimensionFilters === 'function') sv._updateDimensionFilters(model.dimensions.map(d => d.label));
                if (typeof sv.setDisplayMode === 'function') sv.setDisplayMode('matrix');
            }
        }

        _headerHtml(col, i) {
            const role = col.role === 'baseline' ? '基準' : `對照 ${i}`;
            let coverage = '';
            const cov = col.coverage;
            if (cov) {
                const range = pageRangeLabel(cov);
                const where = range ? `${range} 共 ${cov.blocks_given} 段` : `共 ${cov.blocks_given} 段`;
                const whole = cov.complete ? '（全部正文）' : `（全文 ${cov.text_blocks_total} 段中的 ${cov.blocks_given} 段）`;
                const missing = (cov.sections_not_given || []).length ? `；未提供的章節：${esc(cov.sections_not_given.join('、'))}` : '';
                coverage = `<div class="rt-coverage" data-coverage="${esc(col.paperId)}">比較依據：${esc(where)}${whole}${missing}。
                    <span class="rt-coverage-warn">${cov.complete ? '' : '未讀到的部分不代表論文沒有。'}</span></div>`;
            } else if (!col.fulltext) {
                coverage = '<div class="rt-coverage rt-coverage--none">沒有可用全文，未進行比較。</div>';
            } else {
                coverage = '<div class="rt-coverage rt-coverage--none">舊版記錄：沒有記錄 AI 讀了哪些段落。</div>';
            }
            const failed = col.failed ? `<div class="rt-col-failed" role="note">本篇比較失敗：${esc(String(col.failReason || '').slice(0, 160))}</div>` : '';
            return `<th scope="col" class="rt-paper-col${col.role === 'baseline' ? ' rt-paper-col--baseline' : ''}"
                    data-paper-id="${esc(col.paperId)}" data-col-index="${i}">
                <span class="rt-role">${esc(role)}</span>
                <span class="rt-paper-title" title="${esc(col.title)}">${esc(col.title)}</span>
                <span class="rt-paper-id">${esc(col.paperId)}</span>
                ${coverage}${failed}
            </th>`;
        }

        _clampHtml(text, cls, label) {
            const t = String(text || '');
            if (!t) return '';
            const long = t.length > CLAMP_CHARS;
            return `<div class="${cls}${long ? ' rt-clamped' : ''}" data-clamp-text>${label ? `<span class="rt-label">${esc(label)}</span>` : ''}${esc(t)}</div>`
                + (long ? '<button type="button" class="rt-expand" aria-expanded="false" data-expand>展開</button>' : '');
        }

        _cellHtml(col, dim, cell) {
            const status = STATUS[cell.status] ? cell.status : 'uncertain';
            const meta = STATUS[status];
            let html = `<td class="matrix-cell rt-cell rt-cell--${status}" data-status="${status}" data-paper-id="${esc(col.paperId)}"
                data-dim-id="${esc(dim.id)}"><div class="rt-cell-top"><span class="rt-badge rt-badge--${status}" title="${esc(meta.hint)}"
                data-status-badge="${status}">${esc(meta.label)}</span>`;
            html += `<button type="button" class="rt-ask" data-ask aria-label="把「${esc(col.title)}／${esc(dim.label)}」帶入 AI 家教">帶入家教</button></div>`;
            if (cell.reason && status !== 'reported') {
                let reason = String(cell.reason);
                if (status === 'not_reported' && col.coverage && !col.coverage.complete) reason += '（未讀到的段落可能有）';
                html += `<div class="rt-reason" data-reason>${esc(reason)}</div>`;
            }
            html += this._clampHtml(cell.text, 'rt-text', '');
            if (cell.truncated) {
                html += `<div class="rt-trunc" data-truncated>AI 文字超過保存上限，已截在前 ${esc(String((cell.text || '').length))} 字（原長 ${esc(String(cell.full_chars || ''))} 字）。</div>`;
            }
            const evidence = Array.isArray(cell.evidence) ? cell.evidence : [];
            if (evidence.length) {
                html += '<ul class="rt-evidence" aria-label="原文中找到的引文（是否支持描述由你判斷）">';
                evidence.forEach((ev, i) => {
                    const where = ev.page ? `p.${ev.page}` : '無頁碼';
                    const notes = [];
                    if (ev.ref_corrected_from) notes.push(`AI 標的位置是 ${ev.ref_corrected_from}，已改到實際所在段落`);
                    if (ev.outside_given_blocks) notes.push('這段不在送給 AI 的段落中');
                    if (ev.status === 'ambiguous_in_block') notes.push('同一段出現多次，只能定位到段落');
                    const quote = `<span class="rt-quote-where">${esc(where)}</span>「${esc(ev.quote)}」`;
                    html += ev.ref
                        ? `<li><button type="button" class="rt-quote" data-evidence-anchor="${esc(ev.ref)}" data-evidence-index="${i}"
                            aria-label="在閱讀器開啟 ${esc(where)} 的原文">${quote}</button>`
                        : `<li><span class="rt-quote rt-quote--static">${quote}</span>`;
                    html += `${notes.length ? `<span class="rt-quote-note">${esc(notes.join('；'))}</span>` : ''}</li>`;
                });
                html += '</ul><div class="rt-quote-rule">原文中找到；是否支持這格的描述由你判斷。</div>';
            }
            const rejected = Array.isArray(cell.rejected_quotes) ? cell.rejected_quotes : [];
            if (rejected.length) {
                html += `<details class="rt-rejected" data-rejected-count="${rejected.length}"><summary>已排除 ${rejected.length} 段 AI 引文（不是證據）</summary><ul>`;
                rejected.forEach(r => {
                    html += `<li data-rejected-quote><s>${esc(String(r.quote || '').slice(0, 200))}</s> — ${esc(r.reason || '原文中找不到')}</li>`;
                });
                html += '</ul></details>';
            }
            if (col.role !== 'baseline') {
                if (cell.relation) {
                    html += this._clampHtml(cell.relation, 'rt-relation', cell.relation_legacy ? 'AI 評析（舊版）' : 'AI 描述的差異');
                    if (cell.relation_has_judgement) html += '<div class="rt-judgement" role="note">這句含優劣字眼；請以原文自行判斷。</div>';
                } else if (cell.relation_withheld === 'missing') {
                    html += '<div class="rt-withheld" data-relation-withheld="missing">有一方缺資料：不比較、不排名。</div>';
                } else if (cell.relation_withheld === 'not_comparable') {
                    html += '<div class="rt-withheld" data-relation-withheld="not_comparable">條件不同：不比較大小。</div>';
                }
            } else {
                if (cell.pair_disagreement) {
                    const parts = Object.entries(cell.pair_statuses || {}).map(([id, s]) => `${this._titleOf(id)}：${(STATUS[s] || STATUS.uncertain).label}`);
                    html += `<div class="rt-disagree" role="note">各配對對基準的判讀不一致（${esc(parts.join('；'))}）；顯示的是第一個有原文引文的判讀。</div>`;
                }
                if (cell.sourceTitle && this.model && this.model.columns.length > 2) {
                    html += `<div class="rt-source-pair">取自與〈${esc(cell.sourceTitle)}〉的比較</div>`;
                }
            }
            return html + '</td>';
        }

        _titleOf(paperId) {
            const col = this.model ? this.model.columns.find(c => c.paperId === paperId) : null;
            return col ? col.title : paperId;
        }

        _cellData(td) {
            const model = this.model;
            if (!model || !td) return null;
            const col = model.columns.find(c => c.paperId === td.dataset.paperId);
            const dim = model.dimensions.find(d => d.id === td.dataset.dimId);
            return col && dim ? { col, dim, cell: model.cell(col, dim) } : null;
        }

        _bind(host) {
            host.querySelectorAll('[data-expand]').forEach(btn => btn.addEventListener('click', () => {
                const box = btn.previousElementSibling;
                const open = btn.getAttribute('aria-expanded') === 'true';
                box.classList.toggle('rt-clamped', open);
                btn.setAttribute('aria-expanded', open ? 'false' : 'true');
                btn.textContent = open ? '展開' : '收合';
            }));
            host.querySelectorAll('[data-evidence-anchor]').forEach(btn => btn.addEventListener('click', () => {
                const found = this._cellData(btn.closest('td'));
                const ev = found && (found.cell.evidence || [])[Number(btn.dataset.evidenceIndex)];
                if (ev) this.openEvidence(ev, found.col.paperId);
            }));
            host.querySelectorAll('[data-ask]').forEach(btn => btn.addEventListener('click', () => this.askTutor(btn.closest('td'))));
            const settings = host.querySelector('[data-matrix-settings]');
            if (settings) this._bindSettings(settings, () => (this.model ? this.model.columns.map(c => c.paperId) : []));
        }

        openEvidence(ev, paperId) {
            const sv = window.studyView;
            if (!sv || typeof sv.revealAnchor !== 'function') return false;
            // 只送伺服器核對過的錨點欄位；閱讀器依 paper_id／ref／quote 定位，找不到會照實提示。
            return sv.revealAnchor({ ...ev, paper_id: ev.paper_id || paperId, side: 'original' });
        }

        askTutor(td) {
            const found = this._cellData(td);
            const sv = window.studyView;
            if (!found || !sv || typeof sv.handleCellClick !== 'function') return;
            const { col, dim, cell } = found;
            const lines = [`狀態：${(STATUS[cell.status] || STATUS.uncertain).label}${cell.reason ? `（${cell.reason}）` : ''}`];
            if (cell.text) lines.push(`AI 整理（未判定是否被原文支持）：${cell.text}`);
            (cell.evidence || []).forEach(ev => lines.push(`原文中找到的引文 ${ev.page ? 'p.' + ev.page : ''}：「${ev.quote}」`));
            if (cell.relation) lines.push(`AI 描述的差異：${cell.relation}`);
            const base = this.model.columns[0].paperId;
            const focus = col.role === 'baseline' ? [base] : [base, col.paperId];
            sv.handleCellClick(td, col.paperId, dim.label, lines.join('\n'), focus);
        }

        // ---------- 比較設定（維度、順序、基準） ----------
        _dimOptions(model) {
            const fromRecord = model ? model.dimensions.map(d => d.label) : [];
            const all = [];
            [...DEFAULT_DIMENSIONS, ...fromRecord, ...this.extraDims, ...this.dims].forEach(d => { if (d && !all.includes(d)) all.push(d); });
            return all;
        }

        _dimsHtml(model, idPrefix) {
            const options = this._dimOptions(model);
            let html = `<fieldset class="rt-dims"><legend>比較維度（列）</legend><div class="rt-dim-options">`;
            options.forEach((label, i) => {
                const id = `${idPrefix}-dim-${i}`;
                html += `<label class="rt-dim-option" for="${id}"><input type="checkbox" id="${id}" value="${esc(label)}" data-dim-option
                    ${this.dims.includes(label) ? 'checked' : ''}> ${esc(label)}</label>`;
            });
            html += `</div><div class="rt-dim-add"><label class="visually-hidden" for="${idPrefix}-custom">自訂比較維度</label>
                <input type="text" id="${idPrefix}-custom" maxlength="40" placeholder="自訂維度，例如：樣本溫度" data-dim-custom>
                <button type="button" class="btn btn-sm btn-outline-secondary" data-dim-add>加入</button></div>
                <p class="rt-hint">最多 8 個；沒勾任何維度時用預設五維。</p></fieldset>`;
            return html;
        }

        _settingsHtml(model) {
            const order = model.columns.map((c, i) => `<li>${i === 0 ? '<span class="rt-role">基準</span>' : ''}${esc(c.title)}</li>`).join('');
            return `<details class="rt-matrix-settings" data-matrix-settings>
                <summary>比較設定：調整維度後用相同論文重新比較</summary>
                ${this._dimsHtml(model, 'rt-m')}
                <div class="rt-order-current"><span>論文順序（沿用本記錄）：</span><ol>${order}</ol></div>
                <button type="button" class="btn btn-sm btn-primary" data-matrix-rerun>以這些維度重新比較</button>
                <p class="rt-hint">重新比較會產生新的記錄；原記錄保留在「記錄」分頁。</p>
            </details>`;
        }

        _bindSettings(root, idsFn) {
            root.querySelectorAll('[data-dim-option]').forEach(cb => cb.addEventListener('change', () => {
                const picked = [...root.querySelectorAll('[data-dim-option]:checked')].map(x => x.value);
                if (picked.length > 8) { cb.checked = false; this._toast('最多 8 個維度', 'warning'); return; }
                this.dims = picked;
                this._saveDims();
            }));
            const add = () => {
                const input = root.querySelector('[data-dim-custom]');
                const label = String(input.value || '').replace(/\s+/g, ' ').trim().slice(0, 40);
                if (!label) return;
                if (!this.dims.includes(label)) {
                    if (this.dims.length >= 8) { this._toast('最多 8 個維度', 'warning'); return; }
                    this.dims.push(label);
                    if (!DEFAULT_DIMENSIONS.includes(label) && !this.extraDims.includes(label)) this.extraDims.push(label);
                    this._saveDims();
                }
                input.value = '';
                this._refreshSettings(root);
            };
            const addBtn = root.querySelector('[data-dim-add]');
            if (addBtn) addBtn.addEventListener('click', add);
            const input = root.querySelector('[data-dim-custom]');
            if (input) input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
            const rerun = root.querySelector('[data-matrix-rerun]');
            if (rerun) rerun.addEventListener('click', () => {
                const ids = idsFn();
                if (ids.length >= 2) this.runCompare(window.studyCore, ids, ids[0]);
            });
        }

        _refreshSettings(root) {
            const fieldset = root.querySelector('fieldset.rt-dims');
            if (!fieldset) return;
            const prefix = root.hasAttribute('data-matrix-settings') ? 'rt-m' : 'rt-s';
            const holder = document.createElement('div');
            holder.innerHTML = this._dimsHtml(this.model, prefix);
            fieldset.replaceWith(holder.firstElementChild);
            this._bindSettings(root, root.hasAttribute('data-matrix-settings')
                ? () => (this.model ? this.model.columns.map(c => c.paperId) : [])
                : () => this.orderedSelection());
            const custom = root.querySelector('[data-dim-custom]');
            if (custom) custom.focus();
        }

        // 側欄：勾選順序就是欄位順序；第一篇是基準，可上移／下移／設為基準。
        _installSidebarPanel() {
            const tab = document.getElementById('tab-papers');
            const list = document.getElementById('paperListContainer');
            if (!tab || !list || document.getElementById('rt-matrix-setup')) return;
            const panel = document.createElement('section');
            panel.id = 'rt-matrix-setup';
            panel.className = 'rt-matrix-setup';
            panel.setAttribute('aria-label', '比較矩陣設定');
            list.insertAdjacentElement('afterend', panel);
            this.sidebarPanel = panel;
            this._renderSidebarPanel();
        }

        _paperTitle(id) {
            const cb = document.querySelector(`.paper-checkbox[value="${CSS.escape(id)}"]`);
            const h = cb ? cb.closest('.list-group-item')?.querySelector('h6') : null;
            return (h && (h.getAttribute('title') || h.textContent.trim())) || id;
        }

        orderedSelection() {
            const checked = [...document.querySelectorAll('.paper-checkbox:checked')].map(cb => cb.value);
            this.selectionOrder = this.selectionOrder.filter(id => checked.includes(id));
            checked.forEach(id => { if (!this.selectionOrder.includes(id)) this.selectionOrder.push(id); });
            return this.selectionOrder.slice();
        }

        _onPaperCheckbox(e) {
            const cb = e.target;
            if (!cb || !cb.classList || !cb.classList.contains('paper-checkbox')) return;
            if (cb.checked) {
                if (!this.selectionOrder.includes(cb.value)) this.selectionOrder.push(cb.value);
            } else {
                this.selectionOrder = this.selectionOrder.filter(id => id !== cb.value);
            }
            this._renderSidebarPanel();
        }

        _renderSidebarPanel() {
            const panel = this.sidebarPanel;
            if (!panel) return;
            const ids = this.orderedSelection();
            let order = '<p class="rt-hint">依勾選順序比較；第一篇是基準。</p>';
            if (ids.length) {
                order = '<ol class="rt-order-list" data-order-list>' + ids.map((id, i) => `<li data-order-id="${esc(id)}">
                    ${i === 0 ? '<span class="rt-role">基準</span>' : `<span class="rt-role rt-role--compared">對照 ${i}</span>`}
                    <span class="rt-order-title">${esc(this._paperTitle(id))}</span>
                    <span class="rt-order-actions">
                        <button type="button" data-order-up aria-label="上移 ${esc(this._paperTitle(id))}" ${i === 0 ? 'disabled' : ''}>↑</button>
                        <button type="button" data-order-down aria-label="下移 ${esc(this._paperTitle(id))}" ${i === ids.length - 1 ? 'disabled' : ''}>↓</button>
                        ${i === 0 ? '' : `<button type="button" data-order-baseline aria-label="把 ${esc(this._paperTitle(id))} 設為基準">設為基準</button>`}
                    </span></li>`).join('') + '</ol>';
            }
            panel.innerHTML = `<h3 class="rt-setup-title">比較矩陣設定</h3>
                <div class="rt-order" aria-live="polite"><div class="rt-order-head">論文順序（欄）</div>${order}</div>
                ${this._dimsHtml(this.model, 'rt-s')}`;
            panel.querySelectorAll('[data-order-list] li').forEach(li => {
                const id = li.dataset.orderId;
                const move = (to) => {
                    const from = this.selectionOrder.indexOf(id);
                    if (from < 0 || to < 0 || to >= this.selectionOrder.length) return;
                    this.selectionOrder.splice(from, 1);
                    this.selectionOrder.splice(to, 0, id);
                    this._renderSidebarPanel();
                    const again = panel.querySelector(`[data-order-id="${CSS.escape(id)}"] button:not([disabled])`);
                    if (again) again.focus();
                };
                li.querySelector('[data-order-up]')?.addEventListener('click', () => move(this.selectionOrder.indexOf(id) - 1));
                li.querySelector('[data-order-down]')?.addEventListener('click', () => move(this.selectionOrder.indexOf(id) + 1));
                li.querySelector('[data-order-baseline]')?.addEventListener('click', () => move(0));
            });
            this._bindSettings(panel, () => this.orderedSelection());
        }

        // ---------- 送出比較 ----------
        _toast(msg, type) {
            if (window.studyCore && typeof window.studyCore.showToast === 'function') window.studyCore.showToast(msg, type);
        }

        _criteriaLabel() {
            const same = this.dims.length === DEFAULT_DIMENSIONS.length && this.dims.every((d, i) => d === DEFAULT_DIMENSIONS[i]);
            return same ? '預設五維' : this.dims.join('、').slice(0, 80);
        }

        runFromSidebar(core) {
            const ids = this.orderedSelection();
            if (ids.length < 2) {
                alert('請至少勾選 2 篇文獻進行比較！');
                return;
            }
            this.runCompare(core, ids, ids[0]);
        }

        runCompare(core, ids, baselineId) {
            core = core || window.studyCore;
            if (!core || core.isLoading) return;
            const pid = core.resolvePidRuntime();
            if (!pid) {
                core.showToast('無法生成矩陣：PID 不存在', 'error');
                return;
            }
            const dims = this.dims.length ? this.dims.slice() : DEFAULT_DIMENSIONS.slice();
            core.setLoading(true, `AI 正在比較 ${ids.length} 篇文獻（${dims.length} 個維度）...`);
            fetch('/api/study/compare', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pid, paper_ids: ids, baseline_id: baselineId, dimensions: dims, criteria: this._criteriaLabel() }),
            })
                .then(r => r.json())
                .then(res => {
                    core.setLoading(false);
                    if (res.data && Array.isArray(res.data.comparisons)) {
                        if (!res.ok) core.showToast(res.error || '部分比較未完成；請核對標示「失敗」的欄位。', 'error');
                        if (Array.isArray(res.skipped_papers) && res.skipped_papers.length) {
                            core.showToast(`有 ${res.skipped_papers.length} 篇沒有納入（編號不合法或重複）`, 'warning');
                        }
                        core.cacheMatrix(res.data, res.matrix_id || null);
                        this.render(res.data, window.studyView);
                        core.currentLoadedMatrixId = res.matrix_id || null;
                        core.currentLoadedMatrixName = (res.display_name || '').trim() || core.currentLoadedMatrixId || '';
                        core.loadMatrixHistory();
                        if (res.matrix_id && res.saved) core.showToast(`矩陣已保存 (${res.matrix_id})`, 'success');
                    } else {
                        alert('生成失敗: ' + (res.error || 'Unknown Error'));
                    }
                })
                .catch(err => {
                    core.setLoading(false);
                    alert('網絡錯誤: ' + err);
                });
        }
    }

    window.StudyMatrixView = StudyMatrixView;
    window.studyMatrixView = new StudyMatrixView();
    // 側欄「生成比較矩陣」（study.html 呼叫 studyCore.runTask6）改由本檔送出：勾選順序、明示基準與維度。
    if (typeof StudyCore !== 'undefined' && StudyCore.prototype) {
        StudyCore.prototype.runTask6 = function () { return window.studyMatrixView.runFromSidebar(this); };
    }
})();

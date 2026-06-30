//路徑(./app/static/js/study_view.js) #版本 v3.2-MediaMode #更版時間 20260430-1835
/* [MVP+Prototype Handoff Header]
 * 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
 * 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
 * 2026-04-20~2026-04-21 Study Fulltext/Matrix 切換與 Flow B 顯示邏輯已集中於此，供人類團隊接手。
 */
/**
 * Study View Controller v3.2
 * 職責：
 * 1. 控制 Sidebar 收合與 Tab 切換 (Restored from v3.0)
 * 2. 接收 JSON 並渲染為 Sticky Table (Enhanced for Task 6)
 * 3. 處理 Cell 點擊事件並通知 Chat 模組
 * 4. 支援 SOTA Highlight 與 Dimension Filters
 */
class StudyView {
    constructor() {
        this.matrixContainer = document.getElementById('matrixContainer');
        this.sidebar = document.getElementById('sidebar-pane');
        // [Fix] 初始化時檢查实际的 collapsed 状态，而不是假设默认展开
        this.isSidebarCollapsed = this.sidebar ? this.sidebar.classList.contains('collapsed') : false;
        this.hasLoadedPapers = false;   // Lazy load flag

        // DOM Cache
        this.contextBadge = document.getElementById('chat-context-badge');
        this.filterMenu = document.getElementById('dimensionFilterMenu');
        this.matrixTitle = document.getElementById('matrix-title');
        this.currentFulltextPaperId = '';
        this.currentMode = 'idle';
        this.inlineFulltextPanel = document.getElementById('inline-fulltext-panel');
        this.matrixModeBtn = document.getElementById('btn-view-matrix');
        this.fulltextModeBtn = document.getElementById('btn-view-fulltext');
        this.modeBadge = document.getElementById('matrix-mode-badge');
        this.contentModeSelect = document.getElementById('study-content-mode-select');
        this._selectionHandler = null;
        this._selectionTarget = null;
        this.currentFulltextPayload = null;
        this.fulltextHtmlCache = {};
        this.mediaCache = {};

        // Init
        this.initEventListeners();
        this.setDisplayMode('idle', {force: true});
        if (this.contentModeSelect) this.contentModeSelect.value = 'fulltext_block';
        this.updateMediaRefreshButtonState();
        console.log("[StudyView] Initialized v3.2 (isSidebarCollapsed: " + this.isSidebarCollapsed + ")");
    }

    _escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    _sanitizeLatex(value) {
        let s = String(value ?? '').replace(/[\u0000-\u001F\u007F]/g, ' ').trim();
        if (!s) return '';
        s = s.replace(/\s+/g, ' ');
        if (s.length > 4000) s = s.slice(0, 4000);
        return s;
    }

    _isFailedEquationText(value) {
        const s = String(value ?? '').trim();
        if (!s) return false;
        if (s === '[Equation]' || s === '<Failed to OCR Equation>') return true;
        return /failed\s+to\s+ocr\s+equation/i.test(s);
    }

    _renderPageMarker(pageNum) {
        const pageLabel = Number.isFinite(Number(pageNum)) && Number(pageNum) > 0 ? Number(pageNum) : '?';
        return `
            <div class="page-marker my-4 d-flex align-items-center">
                <div class="flex-grow-1" style="height: 1px; background: linear-gradient(to right, transparent, #cbd5e0, transparent);"></div>
                <span class="px-3 text-muted" style="font-size: 0.8rem; font-weight: 500;">Page ${this._escapeHtml(pageLabel)}</span>
                <div class="flex-grow-1" style="height: 1px; background: linear-gradient(to right, transparent, #cbd5e0, transparent);"></div>
            </div>
        `;
    }

    _renderBlockCard(block, opts = {}) {
        const safeBlock = (block && typeof block === 'object') ? block : {};
        const translationReady = !!opts.translationReady;
        const compact = !!opts.compact;
        const blockType = String(safeBlock.type || 'body').toLowerCase();
        const isTitle = blockType === 'title' || blockType === 'header' || blockType === 'maintitle';
        const isSubtitle = blockType === 'subtitle';
        const isEquation = blockType === 'equation';
        const isFigure = blockType === 'figure';
        const isTable = blockType === 'table';
        const text = String(safeBlock.content || '').trim();
        const cardMaxWidth = compact ? 1000 : 1200;

        if (isSubtitle) {
            if (!text) return '';
            return `<h5 class="mt-4 mb-2 selectable-text" style="font-weight: 600; font-size: 1.1rem; color: #4a5568; border-left: 3px solid #667eea; padding-left: 0.75rem;">${this._escapeHtml(text)}</h5>`;
        }

        if (isEquation) {
            const latexRaw = this._sanitizeLatex(String(safeBlock.latex || ''));
            const contentRaw = String(safeBlock.content || '').trim();
            const failureLabel = String(safeBlock.equation_marker || contentRaw || '<Failed to OCR Equation>').trim();
            const equationFailed = !!safeBlock.equation_failed
                || this._isFailedEquationText(contentRaw)
                || this._isFailedEquationText(latexRaw)
                || !latexRaw;

            if (equationFailed) {
                const pidEq = String(safeBlock.pid || '');
                const paperIdEq = String(safeBlock.paper_id || '');
                const seqIdEq = String(safeBlock.seq_id || '').trim();
                const eqImgUrl = (pidEq && paperIdEq && seqIdEq)
                    ? `/api/study/media_asset/${encodeURIComponent(pidEq)}/${encodeURIComponent(paperIdEq)}/${encodeURIComponent(seqIdEq)}.png?kind=equation`
                    : '';
                let html = `<div class="equation-block mb-3 text-center" style="background:#fff7ed;border-radius:8px;border:1px solid #fed7aa;padding:${compact ? '0.8rem 1rem' : '1rem 1.5rem'};max-width:${compact ? 980 : 900}px;margin:0 auto;">`;
                if (eqImgUrl) {
                    html += `<div class="mb-2"><img src="${this._escapeHtml(eqImgUrl)}" alt="equation region" loading="lazy" style="max-width:100%;border-radius:6px;border:1px solid #fdba74;background:#fff;"></div>`;
                }
                html += `<div class="small" style="color:#9a3412;font-weight:600;">${this._escapeHtml(failureLabel || '<Failed to OCR Equation>')}</div>`;
                const failReason = String(safeBlock.equation_failure_reason || '').trim();
                if (failReason) {
                    html += `<div class="small mt-1 text-muted">reason: ${this._escapeHtml(failReason)}</div>`;
                }
                html += `</div>`;
                return html;
            }

            const latexEsc = this._escapeHtml(latexRaw);
            return `<div class="equation-block mb-3 text-center" style="background:#f8f9fa;border-radius:8px;border:1px solid #e2e8f0;padding:${compact ? '0.8rem 1rem' : '1rem 1.5rem'};overflow-x:auto;max-width:${compact ? 980 : 900}px;margin:0 auto;"><span class="katex-eq" data-latex="${latexEsc}"></span></div>`;
        }

        if (isFigure) {
            const caption = text;
            const pid = String(safeBlock.pid || '');
            const paperId = String(safeBlock.paper_id || '');
            const seqId = String(safeBlock.seq_id || '').trim();
            const imgUrl = (pid && paperId && seqId)
                ? `/api/study/media_asset/${encodeURIComponent(pid)}/${encodeURIComponent(paperId)}/${encodeURIComponent(seqId)}.png?kind=figure`
                : '';
            let html = `<div class="figure-block mb-4 text-center" style="background:#fff;border-radius:12px;border:1px solid #e2e8f0;padding:${compact ? '1rem' : '1.5rem'};max-width:${cardMaxWidth}px;margin:0 auto;">`;
            if (imgUrl) {
                html += `<img src="${this._escapeHtml(imgUrl)}" style="max-width:100%;border-radius:6px;" alt="${this._escapeHtml(caption)}" loading="lazy">`;
            }
            if (caption && caption !== '[Figure]') {
                html += `<p class="mt-2 text-muted small selectable-text">${this._escapeHtml(caption)}</p>`;
            }
            html += `</div>`;
            return html;
        }

        if (isTable) {
            const caption = text;
            const pid = String(safeBlock.pid || '');
            const paperId = String(safeBlock.paper_id || '');
            const seqId = String(safeBlock.seq_id || '').trim();
            const imgUrl = (pid && paperId && seqId)
                ? `/api/study/media_asset/${encodeURIComponent(pid)}/${encodeURIComponent(paperId)}/${encodeURIComponent(seqId)}.png?kind=table`
                : '';
            let html = `<div class="table-block mb-4 text-center" style="background:#fff;border-radius:12px;border:1px solid #e2e8f0;padding:${compact ? '1rem' : '1.5rem'};max-width:${cardMaxWidth}px;margin:0 auto;">`;
            if (imgUrl) {
                html += `<img src="${this._escapeHtml(imgUrl)}" style="max-width:100%;border-radius:6px;" alt="${this._escapeHtml(caption)}" loading="lazy">`;
            }
            if (caption && caption !== '[Table]') {
                html += `<p class="mt-2 text-muted small selectable-text">${this._escapeHtml(caption)}</p>`;
            }
            html += `</div>`;
            return html;
        }

        if (!text) return '';

        if (isTitle) {
            return `<h4 class="mt-4 mb-3 text-primary selectable-text" style="font-weight: 600; font-size: 1.3rem;">${this._escapeHtml(text)}</h4>`;
        }

        const translation = String(safeBlock.translation || safeBlock.content_zh || '').trim();
        const paragraphs = text.split(/(?:\n\n+|\. {2,})/g).map(s => s.trim()).filter(Boolean);
        let html = `
            <div class="bilingual-card mb-4" style="
                background: white;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
                border: 1px solid #e2e8f0;
                max-width: ${cardMaxWidth}px;
                margin: 0 auto;
            ">
                <div class="row g-0" style="min-height: ${compact ? 140 : 160}px;">
                    <div class="col-md-6" style="border-right: 2px solid #e2e8f0; padding: ${compact ? '1rem' : '1.5rem'}; background: #fafbfc;">
                        <div class="section-header mb-3 small fw-bold text-uppercase text-secondary">Original Text</div>
                        <div class="original-content selectable-text">
        `;
        paragraphs.forEach((para, idx) => {
            html += `<p class="${idx > 0 ? 'mt-3' : ''}" style="font-size: ${compact ? '0.9rem' : '0.95rem'}; line-height: 1.75; color: #2d3748; text-align: justify; margin-bottom: 0;">${this._escapeHtml(para)}</p>`;
        });
        html += `
                        </div>
                    </div>
                    <div class="col-md-6" style="padding: ${compact ? '1rem' : '1.5rem'}; background: #ffffff;">
                        <div class="section-header mb-3 small fw-bold text-uppercase" style="color:#667eea;">中文翻譯</div>
        `;
        if (translation) {
            html += `<div class="translation-content selectable-text" style="font-size: ${compact ? '0.95rem' : '1.02rem'}; line-height: 1.8; color: #1a202c; text-align: justify;">${this._escapeHtml(translation)}</div>`;
        } else {
            const tipDesc = translationReady ? '此段落暫無可用翻譯' : '尚未產生翻譯，請回 Literature 重新執行 Flow B';
            html += `<div class="text-muted small">${this._escapeHtml(tipDesc)}</div>`;
        }
        html += `
                    </div>
                </div>
            </div>
        `;
        return html;
    }

    _renderBlocksFromRefs(refs, blockRefMap, opts = {}) {
        const normalizedRefs = Array.isArray(refs)
            ? refs.map(r => String(r || '').trim()).filter(Boolean)
            : [];
        const missingRefs = [];
        let html = '';
        let lastPage = 0;

        normalizedRefs.forEach((ref) => {
            const block = (blockRefMap && typeof blockRefMap === 'object') ? blockRefMap[ref] : null;
            if (!block) {
                missingRefs.push(ref);
                return;
            }

            const pageNo = Number(block.__page || 0);
            if (pageNo > 0 && pageNo !== lastPage) {
                html += `<div class="small text-muted mt-2 mb-1"><i class="bi bi-journal-text me-1"></i>Page ${pageNo}</div>`;
                lastPage = pageNo;
            }

            const card = this._renderBlockCard(block, {
                translationReady: !!opts.translationReady,
                compact: true,
            });
            if (card) {
                html += `<div class="reflow-source-block mb-2" data-ref="${this._escapeHtml(ref)}">${card}</div>`;
            }
        });

        return {
            html,
            missingRefs,
        };
    }

    initEventListeners() {
        // [Restored] Global Shortcuts (e.g. Ctrl+B to toggle sidebar)
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'b') {
                e.preventDefault();
                this.toggleSidebar();
            }
        });

        if (this.contentModeSelect) {
            this.contentModeSelect.addEventListener('change', () => {
                this.onContentModeChanged();
            });
        }
    }

    setDisplayMode(mode, opts = {}) {
        const nextMode = String(mode || 'idle').toLowerCase();
        if (!['idle', 'single', 'matrix'].includes(nextMode)) return;
        if (!opts.force && this.currentMode === nextMode) return;
        this.currentMode = nextMode;

        const showSingle = nextMode === 'single';
        if (this.matrixContainer) {
            this.matrixContainer.style.display = showSingle ? 'none' : 'block';
        }
        if (this.inlineFulltextPanel) {
            // Bootstrap utility classes use !important (e.g. d-flex), so toggle class + style together.
            if (showSingle) {
                this.inlineFulltextPanel.classList.remove('d-none');
                this.inlineFulltextPanel.classList.add('d-flex');
                this.inlineFulltextPanel.style.display = 'flex';
            } else {
                this.inlineFulltextPanel.classList.remove('d-flex');
                this.inlineFulltextPanel.classList.add('d-none');
                this.inlineFulltextPanel.style.display = 'none';
            }
        }

        if (this.matrixModeBtn) {
            this.matrixModeBtn.classList.toggle('btn-primary', nextMode === 'matrix');
            this.matrixModeBtn.classList.toggle('btn-outline-primary', nextMode !== 'matrix');
        }
        if (this.fulltextModeBtn) {
            const hasFulltext = !!this.currentFulltextPaperId;
            this.fulltextModeBtn.disabled = !hasFulltext;
            this.fulltextModeBtn.classList.toggle('btn-primary', nextMode === 'single');
            this.fulltextModeBtn.classList.toggle('btn-outline-primary', nextMode !== 'single');
        }
        if (this.modeBadge) {
            if (nextMode === 'single') {
                this.modeBadge.className = 'badge bg-info text-dark';
                this.modeBadge.textContent = 'Single View';
            } else if (nextMode === 'matrix') {
                this.modeBadge.className = 'badge bg-primary';
                this.modeBadge.textContent = 'Matrix View';
            } else {
                this.modeBadge.className = 'badge bg-secondary';
                this.modeBadge.textContent = 'Idle';
            }
        }
    }

    switchToMatrixView() {
        const hasRenderedMatrix = this.matrixContainer && this.matrixContainer.querySelector('.roothinks-matrix');
        if (!hasRenderedMatrix && window.studyCore && typeof window.studyCore.loadLatestMatrix === 'function') {
            window.studyCore.loadLatestMatrix(0, {switchMode: true, silentWhenMissing: false});
            return;
        }
        this.setDisplayMode('matrix');
    }

    switchToFulltextView() {
        if (!this.currentFulltextPaperId) {
            window.studyCore?.showToast('請先從左側文獻列表開啟全文', 'warning');
            return;
        }
        if (this.contentModeSelect) this.contentModeSelect.value = 'fulltext_block';
        this.onContentModeChanged();
    }

    getSelectedContentMode() {
        const mode = String(this.contentModeSelect?.value || 'fulltext_block').trim().toLowerCase();
        if (mode === 'figure' || mode === 'table') return mode;
        if (mode === 'fulltext_reflow') return 'fulltext_reflow';
        if (mode === 'fulltext' || mode === 'fulltext_block') return 'fulltext_block';
        return 'fulltext_block';
    }

    isMediaContentMode(mode) {
        return mode === 'figure' || mode === 'table';
    }

    updateMediaRefreshButtonState() {
        const btn = document.getElementById('btn-media-refresh');
        if (!btn) return;
        const mode = this.getSelectedContentMode();
        const canRefresh = !!this.currentFulltextPaperId && this.isMediaContentMode(mode);
        btn.disabled = !canRefresh;
        btn.classList.toggle('btn-outline-warning', canRefresh);
        btn.classList.toggle('btn-outline-secondary', !canRefresh);
    }

    _getCachedFulltextHtml(paperId, mode) {
        const key = String(paperId || '').trim();
        if (!key) return '';
        const slot = this.fulltextHtmlCache[key];
        if (!slot || typeof slot !== 'object') return '';
        const normalizedMode = mode === 'fulltext_reflow' ? 'fulltext_reflow' : 'fulltext_block';
        return String(slot[normalizedMode] || '');
    }

    onContentModeChanged() {
        if (!this.currentFulltextPaperId) {
            window.studyCore?.showToast('請先載入全文', 'warning');
            if (this.contentModeSelect) this.contentModeSelect.value = 'fulltext_block';
            this.updateMediaRefreshButtonState();
            return;
        }
        const mode = this.getSelectedContentMode();
        this.updateMediaRefreshButtonState();

        if (!this.isMediaContentMode(mode)) {
            const pid = String(window.studyCore?.pid || '').trim();
            const paperId = String(this.currentFulltextPaperId || '').trim();
            const cached = this._getCachedFulltextHtml(this.currentFulltextPaperId, mode);
            const content = document.getElementById('fulltext-content');
            const placeholder = document.getElementById('fulltext-content-placeholder');
            if (content && cached) {
                content.innerHTML = cached;
                content.style.display = 'block';
                if (placeholder) placeholder.style.display = 'none';
                this.initTextSelection();
            }
            this.setDisplayMode('single');
            // 使用快取先秒開，但同步背景 no-store 重抓，避免顯示舊資料。
            if (pid && paperId) {
                this.loadFulltext(pid, paperId, { forceRefresh: true, silent: true, viewMode: mode });
            }
            return;
        }
        this.renderMediaGalleryMode(mode);
    }

    refreshCurrentMediaAnalysis() {
        if (!this.currentFulltextPaperId) {
            window.studyCore?.showToast('請先載入全文，再切換到 Figures/Tables', 'warning');
            return;
        }
        const mode = this.getSelectedContentMode();
        if (!this.isMediaContentMode(mode)) {
            window.studyCore?.showToast('請先切換到 Figures 或 Tables 模式', 'warning');
            return;
        }
        const paperId = String(this.currentFulltextPaperId || '').trim();
        const kind = mode === 'table' ? 'table' : 'figure';
        delete this.mediaCache[`${paperId}::${kind}`];
        window.studyCore?.showToast(`已觸發 ${kind === 'figure' ? 'Figure' : 'Table'} 解說重跑`, 'info');
        this.renderMediaGalleryMode(mode, true);
    }

    renderMediaGalleryMode(mode, refresh = false) {
        const kind = mode === 'table' ? 'table' : 'figure';
        const pid = String(window.studyCore?.pid || '').trim();
        const paperId = String(this.currentFulltextPaperId || '').trim();
        if (!pid || !paperId) {
            window.studyCore?.showToast('缺少 pid/paper_id，無法載入圖表清單', 'error');
            return;
        }

        const content = document.getElementById('fulltext-content');
        const placeholder = document.getElementById('fulltext-content-placeholder');
        if (!content || !placeholder) return;

        content.innerHTML = `
            <div class="py-5 text-center text-muted">
                <div class="spinner-border text-secondary mb-3"></div>
                <div>載入 ${kind === 'figure' ? '圖' : '表'} 與 LLM 解說中...</div>
            </div>
        `;
        content.style.display = 'block';
        placeholder.style.display = 'none';
        this.setDisplayMode('single');

        const cacheKey = `${paperId}::${kind}`;
        const cachedMedia = this.mediaCache[cacheKey];
        const cachedCount = Number(cachedMedia?.count ?? 0);
        // 不沿用空結果快取，避免後端已修復後前端仍卡在 0 筆。
        if (!refresh && cachedMedia && cachedCount > 0) {
            this._renderMediaCards(cachedMedia, kind, paperId);
            return;
        }

        const url = `/api/study/media_gallery/${encodeURIComponent(pid)}/${encodeURIComponent(paperId)}?kind=${encodeURIComponent(kind)}${refresh ? '&refresh=1' : ''}`;
        fetch(url)
            .then(r => r.json())
            .then(data => {
                if (!data || !data.ok) {
                    const msg = String(data?.msg || '未知錯誤');
                    window.studyCore?.showToast(`載入${kind === 'figure' ? '圖' : '表'}失敗: ${msg}`, 'error');
                    return;
                }
                const nextCount = Number(data?.count || 0);
                if (nextCount > 0) {
                    this.mediaCache[cacheKey] = data;
                } else {
                    delete this.mediaCache[cacheKey];
                }
                this._renderMediaCards(data, kind, paperId);
            })
            .catch(err => {
                console.error('[StudyView] media gallery load failed:', err);
                window.studyCore?.showToast(`載入${kind === 'figure' ? '圖' : '表'}失敗`, 'error');
            });
    }

    _renderMediaCards(payload, kind, paperId) {
        const content = document.getElementById('fulltext-content');
        const placeholder = document.getElementById('fulltext-content-placeholder');
        if (!content || !placeholder) return;

        const items = Array.isArray(payload?.items) ? payload.items : [];
        const titleText = kind === 'figure' ? 'Figures' : 'Tables';
        const subtitle = kind === 'figure'
            ? '左側為圖塊，右側為 LLM 解說與重點（可按「重跑解說」刷新）。'
            : '左側為表格切塊，右側為 LLM 解說（不只 caption，含 OCR 內容整合；可重跑）。';

        let html = `
            <div class="fulltext-reader" style="background: #ffffff; padding: 1.2rem 0.75rem;">
                <div class="reader-header mb-3" style="max-width: 1200px; margin: 0 auto; padding-bottom: 0.9rem; border-bottom: 2px solid #e2e8f0;">
                    <h3 style="color: #2d3748; font-weight: 700; font-size: 1.45rem; margin-bottom: 0.45rem;">${this._escapeHtml(titleText)} · ${this._escapeHtml(paperId)}</h3>
                    <div class="text-muted small">${this._escapeHtml(subtitle)} 共 ${Number(payload?.count || items.length)} 項。</div>
                </div>
        `;

        if (!items.length) {
            html += `
                <div class="alert alert-warning" style="max-width: 1000px; margin: 0 auto;">
                    目前找不到 ${kind === 'figure' ? 'Figure' : 'Table'} 區塊。請先確認該篇已完成最新 VNS 切割。
                </div>
            `;
        } else {
            items.forEach((item, idx) => {
                const page = Number(item?.page || 0);
                const seqId = String(item?.seq_id || '');
                const pairId = String(item?.pair_id || '');
                const analysis = String(item?.analysis_zh || '').trim();
                const notes = String(item?.analysis_notes || '').trim();
                const caption = String(item?.caption_text || '').trim();
                const ocrExcerpt = String(item?.ocr_excerpt || '').trim();
                const points = Array.isArray(item?.analysis_points) ? item.analysis_points : [];
                const pointsHtml = points.length
                    ? `<ul class="mb-2 ps-3">${points.map(p => `<li class="mb-1">${this._escapeHtml(String(p || ''))}</li>`).join('')}</ul>`
                    : '<div class="text-muted small mb-2">尚無重點條列</div>';
                const imageUrl = String(item?.image_url || '').trim();
                const sourceTag = String(item?.analysis_source || '').trim() || 'unknown';
                const updatedAt = String(item?.analysis_updated_at || '').trim();
                const analysisMeta = updatedAt
                    ? `analysis: ${sourceTag} | cache: ${updatedAt}`
                    : `analysis: ${sourceTag}`;

                html += `
                    <div class="media-card border rounded-3 bg-white shadow-sm mb-3" style="max-width: 1200px; margin: 0 auto;">
                        <div class="px-3 py-2 border-bottom d-flex align-items-center justify-content-between">
                            <div>
                                <span class="badge bg-secondary me-2">#${idx + 1}</span>
                                <span class="fw-bold">${this._escapeHtml(seqId || `${kind}_${idx + 1}`)}</span>
                                <span class="text-muted small ms-2">Page ${Number.isFinite(page) && page > 0 ? page : '?'}</span>
                                ${pairId ? `<span class="badge bg-light text-dark ms-2">${this._escapeHtml(pairId)}</span>` : ''}
                            </div>
                            <div class="small text-muted">${this._escapeHtml(analysisMeta)}</div>
                        </div>
                        <div class="row g-0">
                            <div class="col-lg-6 border-end p-3 bg-light">
                                ${imageUrl ? `<img src="${this._escapeHtml(imageUrl)}" alt="${this._escapeHtml(seqId)}" class="img-fluid rounded border" style="width:100%; max-height:560px; object-fit:contain; background:#fff;" loading="lazy">` : '<div class="text-muted small">找不到切塊圖片</div>'}
                            </div>
                            <div class="col-lg-6 p-3">
                                <div class="small text-muted mb-1">LLM 解說</div>
                                <div class="mb-2" style="line-height:1.8;">${this._escapeHtml(analysis || '尚無解說')}</div>
                                ${pointsHtml}
                                ${notes ? `<div class="alert alert-light border small py-2">${this._escapeHtml(notes)}</div>` : ''}
                                ${caption ? `<div class="small mt-2"><b>Caption:</b> ${this._escapeHtml(caption)}</div>` : ''}
                                ${ocrExcerpt ? `<details class="small mt-2"><summary class="text-muted">OCR 摘錄</summary><div class="mt-2 text-muted" style="line-height:1.6;">${this._escapeHtml(ocrExcerpt)}</div></details>` : ''}
                            </div>
                        </div>
                    </div>
                `;
            });
        }

        html += '</div>';
        content.innerHTML = html;
        content.style.display = 'block';
        placeholder.style.display = 'none';

        if (window.studyChat && typeof window.studyChat.setContext === 'function') {
            window.studyChat.setContext({
                pid: paperId,
                focus_pids: paperId ? [paperId] : [],
                source: kind,
                dimension: kind === 'figure' ? 'Figures' : 'Tables',
                content: `${titleText} gallery loaded`,
            });
        }
        if (this.contextBadge) {
            this.contextBadge.textContent = `Context: ${paperId} / ${kind === 'figure' ? 'Figures' : 'Tables'}`;
            this.contextBadge.className = 'badge bg-success';
        }
        this.setDisplayMode('single');
    }

    /**
     * [Restored] 切換側邊欄收合狀態
     */
    toggleSidebar(targetTabId = null) {
        // 1. 若目前是收合的 -> 展開
        if (this.isSidebarCollapsed) {
            if(this.sidebar) this.sidebar.classList.remove('collapsed');
            this.isSidebarCollapsed = false;
            
            // 觸發資料載入 (Lazy Load)
            if (!this.hasLoadedPapers) {
                if(window.studyCore && typeof window.studyCore.loadPapers === 'function') {
                    window.studyCore.loadPapers();
                } else {
                    console.warn("[StudyView] studyCore not ready.");
                }
                this.hasLoadedPapers = true;
            }
        } 
        // 2. 若目前是展開的，且沒有指定 Tab -> 收合
        else if (!targetTabId) {
            if(this.sidebar) this.sidebar.classList.add('collapsed');
            this.isSidebarCollapsed = true;
        }

        // 3. 切換 Tab (如果指定)
        if (targetTabId) {
            const btnSelector = `#btn-${targetTabId}`; 
            const tabBtn = document.querySelector(btnSelector);
            if (tabBtn && window.bootstrap) {
                // 使用 Bootstrap API 切換 Tab
                const tab = new bootstrap.Tab(tabBtn);
                tab.show();
            }
        }
    }

    /**
     * [Enhanced] 核心渲染函數：將 Task 6 JSON 轉為 HTML Table
     * 兼容新版後端結構 { baseline: {}, comparisons: [] }
     */
    renderMatrix(data) {
        if (!this.matrixContainer) return;

        // [Fix] 檢查數據完整性
        if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) {
            this.matrixContainer.innerHTML = `
                <div class="d-flex flex-column align-items-center justify-content-center h-100 text-warning">
                    <i class="bi bi-exclamation-circle display-1"></i>
                    <p class="mt-3 fw-bold">矩陣數據為空</p>
                    <p class="small text-muted">請至少選擇 2 篇文獻或檢查 API 響應</p>
                </div>`;
            return;
        }

        // 1. Error Handling
        if (data.error) {
            this.matrixContainer.innerHTML = `
                <div class="d-flex flex-column align-items-center justify-content-center h-100 text-danger">
                    <i class="bi bi-exclamation-triangle display-1"></i>
                    <p class="mt-3 fw-bold">${data.error}</p>
                    <p class="small text-muted">${data.raw ? 'Raw output available in console' : ''}</p>
                </div>`;
            if(data.raw) console.warn("[Matrix] Raw Error Data:", data.raw);
            return;
        }

        // 2. Data Parsing (Adapt v1.6 backend to v3.0 frontend logic)
        // 後端 v1.6 結構: { baseline: {id, title}, comparisons: [{target_title, content/analysis, ...}] }
        // 我們需要將其轉換為 Matrix Row 結構以支援過濾
        
        const baseline = data.baseline || { title: "Baseline" };
        const comparisons = data.comparisons || [];
        const criteria = data.criteria || "General";
        const safeCriteria = this._escapeHtml(criteria);

        // 3. Build HTML - [Enhanced] 改進寬度計算和完整性顯示
        console.log(`[StudyView] renderMatrix: baseline=${baseline.title}, comparisons=${comparisons.length}`);
        
        const totalWidth = 100;
        const dimWidth = 14;
        const paperAWidth = 28;
        const paperBWidth = 28;
        const synthesisWidth = totalWidth - dimWidth - paperAWidth - paperBWidth;
        const activeComp = comparisons[0] || null;
        const paperBTitle = activeComp
            ? (activeComp.target_paper_title || activeComp.target_title || activeComp.target || 'Paper B')
            : 'Paper B';
        const paperBPid = activeComp
            ? (activeComp.id || activeComp.paper_id || activeComp.target_paper_id || 'paper_b')
            : 'paper_b';
        const paperAPid = String(baseline.id || baseline.paper_id || 'paper_a');
        const pidEncA = encodeURIComponent(paperAPid);
        const pidEncB = encodeURIComponent(String(paperBPid || 'paper_b'));
        const pidEncS = encodeURIComponent('synthesis');
        const focusAEnc = encodeURIComponent(JSON.stringify([paperAPid]));
        const focusBEnc = encodeURIComponent(JSON.stringify([String(paperBPid || 'paper_b')]));
        const focusSEnc = encodeURIComponent(JSON.stringify([paperAPid, String(paperBPid || 'paper_b')]));
        
        let html = `<div class="card shadow-sm border-0" style="max-width: 100%;">
            <div class="card-header bg-white py-3 border-bottom d-flex justify-content-between align-items-center">
                <h5 class="mb-0 fw-bold text-primary">
                    <i class="bi bi-grid-3x3-gap-fill me-2"></i>Analysis Matrix: ${safeCriteria}
                </h5>
                <span class="badge bg-secondary">${comparisons.length + 1} Papers</span>
            </div>
            <div class="w-100">
                <table class="table table-bordered table-hover mb-0 align-middle roothinks-matrix" style="width: 100%;">`;

        // A. Table Header (Baseline + Targets)
        html += `<thead class="table-light text-center sticky-top" style="z-index: 10;"><tr>
                    <th style="width: ${dimWidth}%; background-color: #f8f9fa; min-width: 80px;">維度</th>
                    <th style="width: ${paperAWidth}%; background-color: #e9ecef; border-bottom: 2px solid #0d6efd; min-width: 150px;">
                        <div class="badge bg-primary mb-1">Paper A</div><br>
                        <div class="fw-bold text-truncate" title="${this._escapeHtml(baseline.title)}">${this._escapeHtml(baseline.title)}</div>
                    </th>
                    <th style="width: ${paperBWidth}%; min-width: 150px;">
                        <div class="badge bg-secondary mb-1">Paper B</div><br>
                        <div class="fw-bold text-truncate" title="${this._escapeHtml(paperBTitle)}">${this._escapeHtml(paperBTitle)}</div>
                    </th>
                    <th style="width: ${synthesisWidth}%; min-width: 160px; background-color: #f8f9ff; border-bottom: 2px solid #6f42c1;">
                        <div class="badge bg-info text-dark mb-1">Synthesis</div><br>
                        <div class="fw-bold text-truncate" title="Paper A + Paper B">Cross-paper Integration</div>
                    </th>`;
        html += `</tr></thead>`;

        // B. Table Body (dimension-oriented rows)
        html += `<tbody>`;
        const dimSet = new Set();
        comparisons.forEach(comp => {
            const points = Array.isArray(comp.comparison_points) ? comp.comparison_points : [];
            points.forEach(p => {
                if (p && p.dimension) dimSet.add(String(p.dimension));
            });
        });
        const dims = Array.from(dimSet);
        if (!dims.length) dims.push('核心分析');

        const findPoint = (comp, dim) => {
            const points = Array.isArray(comp.comparison_points) ? comp.comparison_points : [];
            return points.find(p => p && String(p.dimension) === String(dim)) || null;
        };

        dims.forEach((dim) => {
            const dimSafe = this._escapeHtml(dim);
            const dimKey = encodeURIComponent(dim);
            const p = activeComp ? findPoint(activeComp, dim) : null;
            let baselineText = 'N/A';
            if (p && p.baseline_view) baselineText = String(p.baseline_view);
            let targetText = 'N/A';
            let synthesisText = 'N/A';
            if (p) {
                targetText = String(p.target_view || 'N/A');
                synthesisText = String(p.synthesis || 'N/A');
            } else if (activeComp && activeComp.analysis) {
                synthesisText = String(activeComp.analysis);
            }

            const baselineShort = baselineText.length > 260 ? baselineText.slice(0, 260) + '...' : baselineText;
            const targetShort = targetText.length > 260 ? targetText.slice(0, 260) + '...' : targetText;
            const synthShort = synthesisText.length > 300 ? synthesisText.slice(0, 300) + '...' : synthesisText;

            const safeDim = encodeURIComponent(dim);
            const safeAContent = encodeURIComponent(String(baselineText));
            const safeBContent = encodeURIComponent(String(targetText));
            const safeSContent = encodeURIComponent(String(synthesisText));

            html += `<tr class="matrix-row" data-dim="${dimKey}">
                        <th class="bg-light fw-bold text-center" style="width: ${dimWidth}%">${dimSafe}</th>
                        <td class="p-3 bg-light matrix-cell" style="width: ${paperAWidth}%; word-break: break-word; white-space: normal;"
                            data-pid-enc="${pidEncA}" data-dim-enc="${safeDim}" data-content-enc="${safeAContent}" data-focus-enc="${focusAEnc}">
                            <div class="small text-muted mb-1">Baseline View</div>
                            <div>${this._escapeHtml(baselineShort)}</div>
                        </td>
                        <td class="p-3 matrix-cell" style="width: ${paperBWidth}%; word-break: break-word; white-space: normal;"
                            data-pid-enc="${pidEncB}" data-dim-enc="${safeDim}" data-content-enc="${safeBContent}" data-focus-enc="${focusBEnc}">
                            <div class="small text-muted mb-1">Target View</div>
                            <div>${this._escapeHtml(targetShort)}</div>
                        </td>
                        <td class="p-3 matrix-cell" style="width: ${synthesisWidth}%; word-break: break-word; white-space: normal; background-color: #fcfcff;"
                            data-pid-enc="${pidEncS}" data-dim-enc="${safeDim}" data-content-enc="${safeSContent}" data-focus-enc="${focusSEnc}">
                            <div class="small text-muted mb-1">Cross-paper Synthesis</div>
                            <div>${this._escapeHtml(synthShort)}</div>
                        </td>`;
            html += `</tr>`;
        });
        
        html += `</tbody></table></div></div>`;

        // 4. Inject
        this.matrixContainer.innerHTML = html;

        // 4.1 Bind cell click handlers after DOM injection (safer than inline onclick)
        this.matrixContainer.querySelectorAll('.matrix-cell').forEach(cell => {
            cell.addEventListener('click', () => {
                const pid = decodeURIComponent(cell.getAttribute('data-pid-enc') || '');
                const dim = decodeURIComponent(cell.getAttribute('data-dim-enc') || '');
                const content = decodeURIComponent(cell.getAttribute('data-content-enc') || '');
                let focusPids = [];
                try {
                    const raw = decodeURIComponent(cell.getAttribute('data-focus-enc') || '[]');
                    const arr = JSON.parse(raw);
                    if (Array.isArray(arr)) focusPids = arr;
                } catch (e) {
                    focusPids = [];
                }
                this.handleCellClick(cell, pid, dim, content, focusPids);
            });
        });
        
        // 5. [Restored] Update UI Status & Filters
        if(this.matrixTitle) this.matrixTitle.innerText = `已比較 ${Math.min(comparisons.length + 1, 2)} 篇文獻`;
        
        this._updateDimensionFilters(dims);
        this.setDisplayMode('matrix');
        
        console.log(`[StudyView] Rendered matrix with ${comparisons.length} targets.`);
    }

    /**
     * [Restored] 處理單元格點擊 -> 觸發 Chat Context
     */
    handleCellClick(el, pid, dimension, content, focusPids = []) {
        // 1. UI Highlight
        document.querySelectorAll('.matrix-cell').forEach(c => c.classList.remove('active-context'));
        el.classList.add('active-context');

        const shorten = (value, maxLen = 56) => {
            const text = String(value || '');
            return text.length > maxLen ? text.slice(0, maxLen) + '...' : text;
        };

        // 2. Update Badge
        if(this.contextBadge) {
            const shortPid = shorten(pid, 42);
            const shortDim = shorten(dimension, 18);
            this.contextBadge.innerText = `Context: ${shortPid} / ${shortDim}`;
            this.contextBadge.title = `${pid} / ${dimension}`;
            this.contextBadge.className = "badge bg-success"; 
        }

        // 3. Notify Chat Module
        if (window.studyChat && typeof window.studyChat.setContext === 'function') {
            const normalizedFocus = Array.isArray(focusPids)
                ? Array.from(new Set(focusPids.map(v => String(v || '').trim()).filter(Boolean)))
                : [];
            window.studyChat.setContext({
                pid: pid,
                dimension: dimension,
                content: content,
                focus_pids: normalizedFocus
            });
        }
    }

    /**
     * [Restored] 檢查內容是否包含 SOTA 關鍵字
     */
    _checkSota(pid, text) {
        if (!text) return false;
        const s = text.toLowerCase();
        // 簡單關鍵字檢查
        const keywords = ["best performance", "state-of-the-art", "sota", "outperform", "superior", "highest accuracy"];
        return keywords.some(k => s.includes(k));
    }

    /**
     * [Restored] 更新維度過濾選單
     */
    _updateDimensionFilters(dimensions) {
        if(!this.filterMenu) return;
        
        let html = `<li><h6 class="dropdown-header">顯示維度 (Toggle)</h6></li>
                    <li><hr class="dropdown-divider"></li>`;
        
        dimensions.forEach((dim, idx) => {
            const dimKey = encodeURIComponent(dim);
            const dimLabel = this._escapeHtml(dim);
            const dimKeyJs = dimKey.replace(/'/g, "\\'");
            html += `
            <li><a class="dropdown-item" href="#" onclick="window.studyView.toggleRow(event, '${dimKeyJs}')">
                <i class="bi bi-check-lg me-2 text-success" id="icon-dim-${idx}"></i> ${dimLabel}
            </a></li>`;
        });
        
        this.filterMenu.innerHTML = html;
    }

    toggleRow(e, dimKey) {
        e.preventDefault(); 
        e.stopPropagation();
        const trigger = e.currentTarget || e.target.closest('a.dropdown-item');
        const icon = trigger ? trigger.querySelector('i') : null;
        
        const rows = document.querySelectorAll(`.matrix-row[data-dim="${dimKey}"]`);
        rows.forEach(row => {
            if (row.style.display === 'none') {
                row.style.display = '';
                if (icon) icon.classList.remove('invisible');
            } else {
                row.style.display = 'none';
                if (icon) icon.classList.add('invisible');
            }
        });
    }
    
    resetZoom() {
        // 全螢幕切換功能
        const matrixPane = document.getElementById('matrix-pane');
        if (!matrixPane) return;
        
        if (!document.fullscreenElement) {
            // 進入全螢幕
            matrixPane.requestFullscreen().then(() => {
                console.log('[StudyView] 已進入全螢幕模式');
            }).catch(err => {
                console.error('[StudyView] 無法進入全螢幕:', err);
                // 降級方案：滾動到頂部
                if(this.matrixContainer) this.matrixContainer.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
            });
        } else {
            // 退出全螢幕
            document.exitFullscreen().then(() => {
                console.log('[StudyView] 已退出全螢幕模式');
            });
        }
    }

    _renderReflowSections(reflowPayload, reflowMeta = null, blockRefMap = null, renderOptions = {}) {
        const payload = (reflowPayload && typeof reflowPayload === 'object') ? reflowPayload : {};
        const sectionsRaw = Array.isArray(payload.sections) ? payload.sections : [];
        const meta = (payload && typeof payload.meta === 'object') ? payload.meta : {};
        const metaGuard = (reflowMeta && typeof reflowMeta === 'object') ? reflowMeta : {};
        const generationMode = String(meta.generation_mode || '').trim();
        const reflowMode = String(meta.reflow_mode || '').trim().toLowerCase();
        const llmError = String(meta.llm_error || '').trim();
        const coverage = (metaGuard && typeof metaGuard.coverage === 'object') ? metaGuard.coverage : {};
        const thresholds = (metaGuard && typeof metaGuard.thresholds === 'object') ? metaGuard.thresholds : {};
        const warnings = Array.isArray(metaGuard.warnings) ? metaGuard.warnings : [];
        let modeLabel = '章節重組';
        if (generationMode === 'task_5b_reflow') modeLabel = 'LLM 重組';
        else if (generationMode === 'task_5interpret') modeLabel = 'LLM 重組 (task_5interpret)';
        else if (generationMode === 'heuristic_fallback') modeLabel = 'Fallback 重組';

        const orderMap = {
            'abstract': 1,
            'introduction': 2,
            'related work': 3,
            'methods': 4,
            'results': 5,
            'discussion': 6,
            'conclusion': 7,
            '未分類段落': 99
        };
        const sections = [...sectionsRaw].sort((a, b) => {
            const la = String(a.section_label || '').trim().toLowerCase();
            const lb = String(b.section_label || '').trim().toLowerCase();
            const oa = Object.prototype.hasOwnProperty.call(orderMap, la) ? orderMap[la] : 90;
            const ob = Object.prototype.hasOwnProperty.call(orderMap, lb) ? orderMap[lb] : 90;
            if (oa !== ob) return oa - ob;
            return la.localeCompare(lb);
        });

        const noisePattern = /impact factor|quartile|co-?author|corresponding author|publish institute|reporter|agenda|影響因子|四分位|通訊作者|合著者|記者|版權|copyright/i;
        const compactText = (value) => String(value || '').replace(/\r/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
        const cleanSectionLines = (value, lang) => {
            const src = compactText(value);
            if (!src) return [];
            const strictExtract = reflowMode === 'strict_extract';

            // strict_extract 要求高保真，前端僅做輕量分段，不主動清洗掉內容。
            if (strictExtract) {
                const strictLines = src
                    .split(/\n+/)
                    .map(x => x.replace(/\s+/g, ' ').trim())
                    .filter(Boolean);
                return strictLines.slice(0, 160);
            }

            const chunks = src
                .split(/\n+/)
                .map(x => x.replace(/\s+/g, ' ').trim())
                .filter(Boolean);
            const lines = [];
            chunks.forEach((line) => {
                if (!line || line.length < 20) return;
                if (noisePattern.test(line)) return;

                const textChars = (line.match(/[A-Za-z\u4e00-\u9fff]/g) || []).length;
                if (textChars < 10) return;
                const symbolChars = (line.match(/[0-9%#\-\+\*=\[\]\(\)\/\\]/g) || []).length;
                if (symbolChars > Math.max(12, Math.floor(line.length * 0.28))) return;

                if (lang === 'en' && /^[A-Z][a-z]+(?:,\s*[A-Z][a-z]+){2,}/.test(line)) return;
                if (lang === 'zh' && /[，、]\s*[\u4e00-\u9fff]{2,4}\s*[，、]\s*[\u4e00-\u9fff]{2,4}/.test(line)) return;
                lines.push(line);
            });
            return lines.slice(0, 48);
        };

        const renderColumn = (title, lines, placeholder) => {
            const body = lines.length
                ? lines.map(line => `<p class="mb-2">${this._escapeHtml(line)}</p>`).join('')
                : `<p class="text-muted small mb-0">${this._escapeHtml(placeholder)}</p>`;
            return `
                <div class="col-lg-6">
                    <div class="rounded-3 h-100 p-3 border bg-light bg-opacity-25">
                        <div class="small fw-bold text-secondary mb-2">${this._escapeHtml(title)}</div>
                        <div class="small selectable-text" style="line-height: 1.85;">${body}</div>
                    </div>
                </div>
            `;
        };

        let html = `
            <div class="mb-4" style="max-width: 980px; margin: 0 auto;">
                <div class="d-flex align-items-center justify-content-between mb-2">
                    <h5 class="mb-0 text-primary fw-bold"><i class="bi bi-diagram-3 me-2"></i>Flow B 語意重組章節</h5>
                    <small class="text-muted">含章節標示與來源追溯 · ${this._escapeHtml(modeLabel)}</small>
                </div>
        `;

        if (coverage && Object.keys(coverage).length) {
            const pct = (v) => `${Math.round(Number(v || 0) * 100)}%`;
            const warningMap = {
                low_ref_coverage: '引用區塊覆蓋偏低',
                low_char_coverage: '字元覆蓋偏低',
                low_sections: '章節數偏少'
            };
            const warningText = warnings.map(w => warningMap[w] || String(w || '').trim()).filter(Boolean).join('、');
            const thresholdText = `門檻 ref≥${pct(thresholds.min_ref_ratio)} / char≥${pct(thresholds.min_char_ratio)} / sections≥${Number(thresholds.min_sections || 0)}`;
            html += `
                <div class="alert ${warnings.length ? 'alert-danger' : 'alert-success'} small py-2 px-3 mb-3">
                    <div class="fw-bold mb-1">覆蓋率檢查</div>
                    <div>ref 覆蓋 ${pct(coverage.ref_coverage)}、char 覆蓋 ${pct(coverage.char_coverage)}、章節 ${Number(coverage.sections || 0)}（canonical ${Number(coverage.canonical_section_hits || 0)}）</div>
                    <div class="text-muted">${this._escapeHtml(thresholdText)}</div>
                    ${warningText ? `<div class="mt-1"><b>警示：</b>${this._escapeHtml(warningText)}</div>` : ''}
                </div>
            `;
        }

        if (generationMode === 'heuristic_fallback') {
            const errLower = llmError.toLowerCase();
            const isBindingError = /未綁定|未绑定/.test(llmError) || (errLower.includes('not bound') || errLower.includes('not bind'));
            const isTimeoutError = /timeout|timed out|逾時|超時/.test(errLower) || /逾時|超時/.test(llmError);
            let fallbackMsg = '目前使用 fallback 重組（LLM 重組未完成），內容可能仍含 OCR 雜訊；建議在 LAVA 綁定 task_5b_reflow 後重新執行 Flow B。';
            if (isBindingError) {
                fallbackMsg = '目前使用 fallback 重組：task_5b_reflow 尚未綁定。請先到 LAVA Setup 完成綁定，再重新執行 Flow B。';
            } else if (isTimeoutError) {
                fallbackMsg = '目前使用 fallback 重組：task_5b_reflow 已綁定但請求逾時。可稍後重試，或由管理端提高 timeout/retry 後再跑 Flow B。';
            }
            html += `
                <div class="alert alert-warning small py-2 px-3 mb-3">
                    ${this._escapeHtml(fallbackMsg)}
                </div>
            `;
            if (llmError) {
                html += `
                    <div class="small text-muted mb-2" style="word-break: break-word;">
                        <b>系統註記：</b> ${this._escapeHtml(llmError.slice(0, 280))}${llmError.length > 280 ? '...' : ''}
                    </div>
                `;
            }
        }

        const hasBlockRefMap = !!(blockRefMap && typeof blockRefMap === 'object' && Object.keys(blockRefMap).length > 0);
        const sectionRenderOpts = (renderOptions && typeof renderOptions === 'object') ? renderOptions : {};
        let rendered = 0;
        sections.slice(0, 24).forEach((sec) => {
            const label = this._escapeHtml(sec.section_label || '未分類段落');
            const refs = Array.isArray(sec.source_block_refs) ? sec.source_block_refs : [];
            const droppedRefs = Array.isArray(sec.dropped_refs) ? sec.dropped_refs : [];
            const refsText = this._escapeHtml(refs.slice(0, 24).join(', '));
            const droppedText = this._escapeHtml(droppedRefs.slice(0, 24).join(', '));
            const confidenceRaw = Number(sec.confidence);
            const confidenceText = Number.isFinite(confidenceRaw) ? `${Math.round(confidenceRaw * 100)}%` : 'N/A';
            const inferred = !!sec.inferred_label;
            const contentZh = String(sec.content_zh || '').trim();
            const contentEn = String(sec.content_en || '').trim();
            const zhLines = cleanSectionLines(contentZh, 'zh');
            const enLines = cleanSectionLines(contentEn, 'en');
            const sourceCards = hasBlockRefMap
                ? this._renderBlocksFromRefs(refs, blockRefMap, { translationReady: !!sectionRenderOpts.translationReady })
                : { html: '', missingRefs: [] };
            if (!zhLines.length && !enLines.length && !refs.length) return;
            rendered += 1;

            html += `
                <div class="border rounded-3 bg-white p-3 mb-2 shadow-sm">
                    <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
                        <span class="badge bg-primary">章節</span>
                        <span class="fw-bold">${label}</span>
                        <span class="badge bg-light text-dark">信心 ${confidenceText}</span>
                        ${inferred ? '<span class="badge bg-warning text-dark">AI 推定章節</span>' : ''}
                    </div>
                    <div class="row g-3 mb-2">
                        ${renderColumn('English (Reflow)', enLines, '無可用英文重組內容')}
                        ${renderColumn('中文重組', zhLines, '無可用中文重組內容')}
                    </div>
                    <div class="small text-muted">${refs.length ? `來源：${refsText}` : '來源：未提供'}</div>
                    ${droppedRefs.length ? `<div class="small text-muted mt-1">剔除 refs：${droppedText}</div>` : ''}
                    ${sourceCards.html ? `
                        <div class="mt-3 pt-2 border-top">
                            <div class="small fw-bold text-secondary mb-2"><i class="bi bi-link-45deg me-1"></i>來源 Block 卡片 (${refs.length})</div>
                            ${sourceCards.html}
                        </div>
                    ` : ''}
                    ${sourceCards.missingRefs && sourceCards.missingRefs.length ? `
                        <div class="small text-danger mt-2">缺失 refs：${this._escapeHtml(sourceCards.missingRefs.join(', '))}</div>
                    ` : ''}
                </div>
            `;
        });

        if (rendered === 0) {
            html += `
                <div class="alert alert-secondary small">
                    目前沒有可讀的章節化雙語內容，請回 Literature 重新執行 Flow B。
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    // --- 全文閱讀器功能 ---
    _invalidatePaperCaches(paperId) {
        const target = String(paperId || '').trim();
        if (!target) return;
        delete this.fulltextHtmlCache[target];
        delete this.mediaCache[`${target}::figure`];
        delete this.mediaCache[`${target}::table`];
    }

    loadFulltext(pid, paperId, opts = {}) {
        const targetPaperId = String(paperId || '').trim();
        if (!targetPaperId) {
            window.studyCore?.showToast('缺少 paper_id，無法載入全文', 'warning');
            return;
        }
        const forceRefresh = !!opts.forceRefresh;
        const silent = !!opts.silent;
        if (forceRefresh) {
            this._invalidatePaperCaches(targetPaperId);
        }

        // Default to auto mode so Flow B translations are used when available.
        const preferModeRaw = String(opts.prefer || 'auto').trim().toLowerCase();
        const preferMode = ['auto', 'flowa', 'flowb', 'a', 'b', 'fusion', 'trans', 'translation'].includes(preferModeRaw)
            ? preferModeRaw
            : 'auto';
        const noStoreUrl = `/api/study/get_fulltext/${encodeURIComponent(pid)}/${encodeURIComponent(targetPaperId)}?prefer=${encodeURIComponent(preferMode)}&_ts=${Date.now()}`;
        fetch(noStoreUrl, {
            cache: 'no-store',
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache'
            }
        })
            .then(r => r.json())
            .then(data => {
                if (!data.ok) {
                    if (!silent) {
                        window.studyCore?.showToast('無法載入全文: ' + (data.msg || 'Unknown error'), 'error');
                    }
                    return;
                }

                const content = document.getElementById('fulltext-content');
                const placeholder = document.getElementById('fulltext-content-placeholder');
                if (!content || !placeholder) {
                    window.studyCore?.showToast('全文容器不存在，請重新整理頁面', 'error');
                    return;
                }

                let contentHtml = '';
                const blockRefMap = {};
                if (Array.isArray(data.blocks) && data.blocks.length > 0) {
                    data.blocks.forEach((pageData, pageIdx) => {
                        const rawPage = Number((pageData && pageData.page) || (pageIdx + 1));
                        const pageNum = Number.isFinite(rawPage) && rawPage > 0 ? rawPage : (pageIdx + 1);
                        contentHtml += this._renderPageMarker(pageNum);

                        const pageBlocks = Array.isArray(pageData.blocks) ? pageData.blocks : [];
                        pageBlocks.forEach((rawBlock, blockIdx) => {
                            const safeBlock = (rawBlock && typeof rawBlock === 'object')
                                ? { ...rawBlock }
                                : { type: 'body', content: String(rawBlock || '') };
                            const ref = `p${pageNum}-b${blockIdx + 1}`;
                            safeBlock.__page = pageNum;
                            safeBlock.__block_index = blockIdx + 1;
                            safeBlock.__ref = ref;
                            blockRefMap[ref] = safeBlock;

                            const blockHtml = this._renderBlockCard(safeBlock, {
                                translationReady: !!data.translation_ready,
                                compact: false,
                            });
                            if (blockHtml) contentHtml += blockHtml;
                        });
                    });
                } else {
                    const fulltext = String(data.fulltext || '');
                    const paragraphs = fulltext.split(/\n\n+/).map(s => s.trim()).filter(Boolean);
                    contentHtml = `<div class="content-card selectable-text" style="background: #f8f9fa; border-radius: 10px; padding: 2rem; max-width: 900px; margin: 0 auto;">`;
                    paragraphs.forEach((para, idx) => {
                        contentHtml += `<p class="${idx > 0 ? 'mt-4' : ''}" style="font-size: 1.05rem; line-height: 1.85; color: #2d3748; text-align: justify;">${this._escapeHtml(para)}</p>`;
                    });
                    contentHtml += `</div>`;
                }

                let summaryText = '';
                const summaryObj = data.summary;
                if (summaryObj) {
                    if (typeof summaryObj === 'string') {
                        summaryText = summaryObj.trim();
                    } else if (typeof summaryObj === 'object') {
                        summaryText = (summaryObj.abstract_zh || summaryObj.abstract_en || '').trim();
                        if (!summaryText && Array.isArray(summaryObj.key_findings) && summaryObj.key_findings.length) {
                            summaryText = summaryObj.key_findings.map(x => String(x || '').trim()).filter(Boolean).slice(0, 3).join('\n');
                        }
                    }
                }
                if (!summaryText) {
                    summaryText = '此文獻暫無可顯示摘要，請回 Literature 重新執行 Task 5。';
                }

                const summaryHtml = `
                    <div class="ai-summary-card mb-4" style="background: linear-gradient(135deg, #667eea08 0%, #764ba208 100%); border-radius: 12px; padding: 1.5rem 2rem; border: 1px solid #e0e7ff; box-shadow: 0 2px 8px rgba(102, 126, 234, 0.08); max-width: 900px; margin: 0 auto 2rem auto;">
                        <div class="d-flex align-items-center mb-3">
                            <i class="bi bi-lightbulb-fill me-2" style="font-size: 1.3rem; color: #667eea;"></i>
                            <strong style="font-size: 1.1rem; color: #4c51bf;">AI 智能摘要</strong>
                        </div>
                        <div style="font-size: 1rem; line-height: 1.8; color: #4a5568; white-space: pre-wrap;">${this._escapeHtml(summaryText)}</div>
                    </div>
                `;

                const reflowPayload = (data.reflow && typeof data.reflow === 'object') ? data.reflow : null;
                const reflowMeta = (data.reflow_meta && typeof data.reflow_meta === 'object') ? data.reflow_meta : null;
                const hasReflowSections = !!(reflowPayload && Array.isArray(reflowPayload.sections) && reflowPayload.sections.length > 0);
                const hasReflowMeta = !!reflowMeta;
                const reflowHtml = this._renderReflowSections(reflowPayload || null, reflowMeta, blockRefMap, {
                    translationReady: !!data.translation_ready,
                });
                const rawBlocksHtml = `
                    <div class="reading-content selectable-text" style="margin-top: 1.5rem;">
                        ${contentHtml}
                    </div>
                `;
                const buildReaderFrame = (modeLabel, bodyHtml) => `
                    <div class="fulltext-reader" style="background: #ffffff; padding: 1.5rem 0.75rem;">
                        <div class="reader-header mb-4" style="max-width: 900px; margin: 0 auto; padding-bottom: 1rem; border-bottom: 2px solid #e2e8f0;">
                            <h3 style="color: #2d3748; font-weight: 700; font-size: 1.65rem; margin-bottom: 0.6rem; line-height: 1.4;">${this._escapeHtml(data.title || targetPaperId)}</h3>
                            <div style="font-size: 0.9rem; color: #718096;" class="d-flex align-items-center gap-2 flex-wrap">
                                <span><i class="bi bi-file-earmark-text me-1"></i>論文代碼: ${this._escapeHtml(data.paper_id || targetPaperId)}</span>
                                <span class="badge bg-light text-dark">${this._escapeHtml(modeLabel)}</span>
                            </div>
                        </div>
                        ${summaryHtml}
                        ${bodyHtml}
                        <div class="text-center text-muted small mt-4">
                            <p>選取文字可用於右側 AI 家教對話</p>
                        </div>
                    </div>
                `;
                const blockModeHtml = buildReaderFrame('Block 對照版', rawBlocksHtml);
                const reflowPrimaryHtml = (hasReflowSections || hasReflowMeta)
                    ? `
                        <div class="reading-content selectable-text" style="margin-top: 1.2rem;">
                            ${reflowHtml}
                        </div>
                    `
                    : `
                        <div class="alert alert-warning" style="max-width: 980px; margin: 1rem auto 0 auto;">
                            目前尚無可顯示的 Reflow 章節，請先確認該篇已完成 Flow B 語意重組。
                        </div>
                    `;
                const reflowModeHtml = buildReaderFrame(
                    'Reflow 章節版',
                    `
                        ${reflowPrimaryHtml}
                        <details class="mt-3" open style="max-width: 1200px; margin: 0 auto;">
                            <summary class="fw-bold text-secondary" style="cursor:pointer;">原始 Block 對照（Flow A/Flow B，預設展開）</summary>
                            <div style="margin-top: 0.8rem;">
                                ${rawBlocksHtml}
                            </div>
                        </details>
                    `,
                );

                this.currentFulltextPaperId = targetPaperId;
                this.currentFulltextPayload = data;
                this.fulltextHtmlCache[targetPaperId] = {
                    fulltext_block: blockModeHtml,
                    fulltext_reflow: reflowModeHtml,
                };
                // 重新載入全文代表資料可能已更新，清掉同篇圖表快取避免沿用舊切塊。
                delete this.mediaCache[`${targetPaperId}::figure`];
                delete this.mediaCache[`${targetPaperId}::table`];
                if (this.contentModeSelect) {
                    this.contentModeSelect.disabled = false;
                    if (!this.contentModeSelect.value) this.contentModeSelect.value = 'fulltext_block';
                }
                const selectedMode = this.getSelectedContentMode();
                if (!this.isMediaContentMode(selectedMode)) {
                    const normalizedMode = selectedMode === 'fulltext_reflow' ? 'fulltext_reflow' : 'fulltext_block';
                    const html = this._getCachedFulltextHtml(targetPaperId, normalizedMode) || blockModeHtml;
                    content.innerHTML = html;
                    placeholder.style.display = 'none';
                    content.style.display = 'block';
                    this.initTextSelection();
                    // 渲染 KaTeX 公式
                    content.querySelectorAll('.katex-eq[data-latex]').forEach(el => {
                        try {
                            const raw = this._sanitizeLatex(el.getAttribute('data-latex') || '');
                            if (!raw || this._isFailedEquationText(raw)) {
                                el.textContent = '<Failed to OCR Equation>';
                                return;
                            }
                            if (window.katex) {
                                window.katex.render(raw, el, { displayMode: true, throwOnError: false });
                            }
                        } catch (e) {
                            el.textContent = '<Equation Render Error>';
                        }
                    });
                    if (!silent) {
                        const modeLabel = normalizedMode === 'fulltext_reflow' ? 'Reflow 章節版' : 'Block 對照版';
                        window.studyCore?.showToast(`全文已載入（${modeLabel}）`, 'success');
                    }
                } else {
                    this.renderMediaGalleryMode(selectedMode);
                }
                this.setDisplayMode('single');
                this.updateMediaRefreshButtonState();

                if (!this.isMediaContentMode(selectedMode)) {
                    const contextDimension = selectedMode === 'fulltext_reflow' ? 'Fulltext Reflow' : 'Fulltext Block';
                    if (window.studyChat && typeof window.studyChat.setContext === 'function') {
                        window.studyChat.setContext({
                            pid: this.currentFulltextPaperId || '',
                            focus_pids: this.currentFulltextPaperId ? [this.currentFulltextPaperId] : [],
                            source: 'fulltext',
                            dimension: contextDimension,
                            content: ''
                        });
                    }
                    if (this.contextBadge) {
                        this.contextBadge.textContent = `Context: ${this.currentFulltextPaperId || '-'} / ${contextDimension}`;
                        this.contextBadge.className = 'badge bg-success';
                    }
                }

            })
            .catch(err => {
                console.error('Fulltext load error:', err);
                if (!silent) {
                    window.studyCore?.showToast('載入失敗，請檢查文獻是否已完成 AI 解析', 'error');
                }
            });
    }

    toggleFullscreenPane() {
        const pane = this.inlineFulltextPanel || document.getElementById('inline-fulltext-panel');
        if (!pane) {
            window.studyCore?.showToast('找不到全文面板容器', 'warning');
            return;
        }
        if (!document.fullscreenElement) {
            pane.requestFullscreen?.().catch((err) => {
                console.warn('[Fulltext] requestFullscreen failed:', err);
                window.studyCore?.showToast('無法切換全螢幕', 'warning');
            });
            return;
        }
        document.exitFullscreen?.().catch((err) => {
            console.warn('[Fulltext] exitFullscreen failed:', err);
        });
    }

    closeFulltext() {
        const hasRenderedMatrix = this.matrixContainer && this.matrixContainer.querySelector('.roothinks-matrix');
        this.setDisplayMode(hasRenderedMatrix ? 'matrix' : 'idle');
    }

    initTextSelection() {
        const content = document.getElementById('fulltext-content');
        if (!content) return;

        if (this._selectionHandler && this._selectionTarget) {
            this._selectionTarget.removeEventListener('mouseup', this._selectionHandler);
            this._selectionTarget.removeEventListener('keyup', this._selectionHandler);
        }

        this._selectionHandler = () => {
            const selection = window.getSelection();
            if (!selection || selection.rangeCount < 1) return;
            const range = selection.getRangeAt(0);
            if (!content.contains(range.commonAncestorContainer)) return;
            const text = selection.toString().trim();
            
            if (text.length > 3) {
                // 將選取內容設為 Chat Context
                if (window.studyChat) {
                    window.studyChat.setContext({
                        type: 'text_selection',
                        pid: this.currentFulltextPaperId || '',
                        focus_pids: this.currentFulltextPaperId ? [this.currentFulltextPaperId] : [],
                        content: text.substring(0, 200), // 限制長度
                        source: 'fulltext',
                        dimension: 'Selected Text'
                    });
                    
                    // 更新 Context Badge
                    if (this.contextBadge) {
                        this.contextBadge.textContent = `Context: ${this.currentFulltextPaperId || '-'} / Selected Text (${text.length})`;
                        this.contextBadge.className = 'badge bg-success';
                    }
                }
            }
        };

        this._selectionTarget = content;
        content.addEventListener('mouseup', this._selectionHandler);
        content.addEventListener('keyup', this._selectionHandler);
    }

    toggleHighlight() {
        const content = document.getElementById('fulltext-content');
        if (!content) {
            window.studyCore?.showToast('請先載入全文', 'warning');
            return;
        }
        
        const selection = window.getSelection();
        if (!selection || selection.rangeCount < 1) {
            window.studyCore?.showToast('請先選取要標記的文字', 'warning');
            return;
        }
        const range = selection.getRangeAt(0);
        if (!content.contains(range.commonAncestorContainer)) {
            window.studyCore?.showToast('請在全文區塊內選字', 'warning');
            return;
        }
        const text = selection.toString().trim();
        
        if (text) {
            // 簡單高亮實現（包裹在 mark 標籤中）
            const mark = document.createElement('mark');
            mark.className = 'bg-warning bg-opacity-50';
            mark.style.padding = '2px 0';
            
            try {
                range.surroundContents(mark);
                selection.removeAllRanges();
                
                // 保存高亮記錄
                this.saveHighlight(text);
                window.studyCore?.showToast('已標記文字', 'success');
            } catch (e) {
                console.warn('Highlight failed (complex selection):', e);
                window.studyCore?.showToast('無法標記跨段落選取，請改選單一段落', 'warning');
            }
        } else {
            window.studyCore?.showToast('請先選取要標記的文字', 'warning');
        }
    }

    saveHighlight(text) {
        // 儲存到 localStorage
        const highlights = JSON.parse(localStorage.getItem('highlights') || '[]');
        highlights.push({
            text: text.substring(0, 100),
            timestamp: Date.now(),
            pid: window.studyCore?.pid || 'unknown'
        });
        localStorage.setItem('highlights', JSON.stringify(highlights));
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// 不在這裡全局初始化，在 HTML 的 DOMContentLoaded 中初始化

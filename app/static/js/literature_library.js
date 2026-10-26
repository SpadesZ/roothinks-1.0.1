// Roothinks source maintenance contract
// 檔案路徑: app/static/js/literature_library.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 Literature library 篩選、screening 狀態、資源清單與安全 DOM 更新。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/literature_library.js
//路徑(./app/static/js/literature_library.js) #版本 v0.1 #更版時間 20260719
// 持久文獻庫最小面板：列表/篩選/狀態更新/匯入/匯出/索引重建。
// 依賴 window.literatureApp.currentPid；所有寫入皆走後端 API，前端不持狀態。
(function () {
    const SCREENING = ["candidate", "included", "excluded"];
    // [ui] READING 已不在文獻庫呈現：閱讀進度歸 Study 模組管，
// 放在這裡會與 Screening（納入與否）混淆。後端欄位保留不動。

    function pid() {
        return (window.literatureApp && window.literatureApp.currentPid) || "";
    }

    function esc(s) {
        const div = document.createElement("div");
        div.innerText = String(s == null ? "" : s);
        return div.innerHTML;
    }

    let loadedEntries = [];

    window.recoverLiteratureUpload = async function (failure, projectId) {
        if (!failure.original_saved || !confirm(`原件 ${failure.paper_id} 已保留。現在只補登記？原件不會重寫；未完成的文獻連結請到清單核對。`)) return;
        const response = await fetch('/api/literature/recover_upload', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({pid:projectId,paper_id:failure.paper_id})
        });
        const result = await response.json();
        alert(response.ok ? `補登記完成：${result.paper_id}。請核對文獻連結與納入理由。` : (result.message || '補登記失敗；原件仍保留。'));
        if (response.ok && projectId === pid()) await loadLibrary();
    };

    function draftKey(entryId) {
        const owner = document.getElementById('libraryPanel')?.dataset.owner || 'local';
        return `literature-decision:${owner}:${pid()}:${entryId}`;
    }

    function persistDecision(form) {
        const patch = {};
        form.querySelectorAll('[data-field]').forEach(control => {patch[control.dataset.field] = control.value;});
        try { sessionStorage.setItem(draftKey(form.dataset.entry), JSON.stringify({patch, base_rev: Number(form.dataset.rev)})); }
        catch (error) { form.querySelector('.lib-save-status').textContent = '無法保留本機副本，請先複製文字。'; }
    }

    async function loadLibrary() {
        const host = document.getElementById("libraryTableBody");
        if (!host || !pid()) return;
        const screening = document.getElementById("libFilterScreening").value;
        const q = document.getElementById("libFilterQuery").value.trim();
        const params = new URLSearchParams({ pid: pid() });
        if (screening) params.set("screening", screening);
        if (q) params.set("q", q);
        host.innerHTML = '<tr><td colspan="4" class="text-center text-muted small py-3">Loading...</td></tr>';
        try {
            const res = await fetch(`/api/literature/library?${params.toString()}`);
            const data = await res.json();
            if (!res.ok || data.status !== 'success') throw new Error(data.message || '文獻讀取失敗');
            const entries = (data && data.entries) || [];
            loadedEntries = entries;
            const target = document.getElementById('libUploadTarget');
            if (target) {
                target.innerHTML = '<option value="">建立獨立候選文獻</option>' + entries.filter(e => !e.linked_to).map(e =>
                    `<option value="${esc(e.entry_id)}">${esc(e.title)}${e.paper_id ? '（已有 PDF，新版需重核）' : ''}</option>`).join('');
            }
            const papers = [...new Set(entries.flatMap(e => [e.paper_id, ...(e.paper_ids || [])]).filter(Boolean))];
            const mainEntries = entries.filter(e => !e.linked_to);
            const badge = document.getElementById("libCountBadge");
            if (badge) badge.innerText = `${mainEntries.length} 文獻 · ${entries.length - mainEntries.length} 來源紀錄`;
            if (!entries.length) {
                host.innerHTML = '<tr><td colspan="4" class="text-center text-muted small py-3">Library is empty. 執行搜尋或匯入後會自動累積。</td></tr>';
                return;
            }
            host.innerHTML = mainEntries.map(function (e) {
                const link = e.url || (e.doi ? `https://doi.org/${e.doi}` : "");
                const titleHtml = link
                    ? `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(e.title || "(untitled)")}</a>`
                    : esc(e.title || "(untitled)");
                const authors = (e.authors || []).slice(0, 3).join(", ") + ((e.authors || []).length > 3 ? " et al." : "");
                // [ui] 移除逐列的「上傳PDF」按鈕：上傳統一由 2.2C 上傳文件／
                // 2.3 的 Upload Papers 進入，同一件事不要有兩個入口。
                // 已連結的仍顯示 paper_id——那是資訊（這篇已進 pipeline），不是操作。
                const paperTag = e.paper_id
                    ? `<span class="badge bg-success-subtle text-success border border-success" title="已連結 PDF pipeline">PDF: ${esc(e.paper_id)}</span>`
                    : '<span class="text-muted small">—</span>';
                const revision = Number(e._rev || 0);
                const details = `
                    <details class="mt-2 lib-decision" data-entry="${esc(e.entry_id)}" data-rev="${revision}" data-server-rev="${revision}">
                        <summary>理由、閱讀筆記與來源版本</summary>
                        <label class="d-block mt-2">採用／排除理由<textarea class="form-control form-control-sm" data-field="screening_note" rows="2">${esc(e.screening_note || '')}</textarea></label>
                        <label class="d-block mt-2">閱讀筆記與適用限制${e.reading_note_hidden ? '<div class="small text-muted border rounded p-1" data-reading-note-hidden="1">研究者有閱讀筆記；審閱模式不顯示。</div>' : `<textarea class="form-control form-control-sm" data-field="reading_note" rows="2">${esc(e.reading_note || '')}</textarea>`}</label>
                        <label class="d-block mt-2">對應 PDF（核對同篇後選擇）<select class="form-select form-select-sm" data-field="paper_id">
                            <option value="">尚未連結</option>${papers.map(p => `<option value="${esc(p)}" ${p === e.paper_id ? 'selected' : ''}>${esc(p)}</option>`).join('')}
                        </select></label>
                        <label class="d-block mt-2">作者決策<select class="form-select form-select-sm" data-field="screening_status">
                            ${SCREENING.map(status => `<option value="${status}" ${status === e.screening_status ? 'selected' : ''}>${status}</option>`).join('')}
                        </select></label>
                        <div class="small text-muted mt-2">新 PDF 先存為 candidate，檢視後再確認 included。只有已確認的目前版本會供寫作使用。</div>
                        <button class="btn btn-sm btn-primary mt-2 lib-save">儲存並確認決策</button><span class="lib-save-status ms-2" role="status"></span>
                        <details class="lib-server-review mt-2" hidden><summary>伺服器目前已存決策（請比對）</summary><div>${esc(e.screening_status)} · ${esc(e.paper_id)} · 修訂 ${revision}</div><p>${esc(e.screening_note)}</p><p>${esc(e.reading_note)}</p>
                            <button class="btn btn-sm btn-outline-primary lib-resolve">採用目前編輯內容，另存新版決策</button>
                        </details>
                        <div class="small mt-2">決策者：${esc(e.screening_decided_by || '尚未確認')} · ${esc(e.screening_decided_at || '')} · 修訂 ${revision}</div>
                        <details class="mt-2"><summary>變更紀錄（${(e.history || []).length}）</summary>${(e.history || []).slice().reverse().map(h => { const prev = h.previous || {}; return `<p class="small">修訂 ${Number(h.revision)} · ${esc(h.actor || '舊版／內部流程')} · ${esc(h.at)}${h.previous ? `<br>${esc(prev.screening_status)} · ${esc(prev.paper_id)}<br>${esc(prev.screening_note)}<br>${esc(prev.reading_note)}` : ''}</p>`; }).join('')}</details>
                        <div class="small">來源：${esc((e.sources || []).join(', '))} · 原 ID：${esc((e.source_entry_ids || [e.entry_id]).join(', '))}</div>
                        ${(e.uploaded_sources || []).map(src => `<div class="small text-break">${esc(src.filename)} · ${esc(src.paper_id)} · SHA256 ${esc(src.sha256)}</div>`).join('')}
                    </details>`;
                return `<tr>
                    <td class="small">${titleHtml}<div class="text-muted">${esc(authors)}${e.year ? ' · ' + esc(e.year) : ''}${e.venue ? ' · ' + esc(e.venue) : ''}</div>${details}</td>
                    <td class="small text-muted">${esc(e.doi || '')}</td>
                    <td>${esc(e.screening_status)}</td><td>${paperTag}</td>
                </tr>`;
            }).join('');
            host.querySelectorAll('.lib-decision').forEach(form => {
                const button = form.querySelector('.lib-save');
                button.addEventListener('click', () => saveDecision(form, button));
                form.querySelectorAll('[data-field]').forEach(control => {
                    control.addEventListener('input', () => persistDecision(form));
                    control.addEventListener('change', () => persistDecision(form));
                });
                form.querySelector('.lib-resolve').addEventListener('click', () => {
                    form.dataset.rev = form.dataset.serverRev;
                    persistDecision(form);
                    saveDecision(form, button);
                });
                try {
                    const draft = JSON.parse(sessionStorage.getItem(draftKey(form.dataset.entry)) || 'null');
                    if (draft && draft.patch) {
                        form.querySelectorAll('[data-field]').forEach(control => {
                            if (Object.hasOwn(draft.patch, control.dataset.field)) control.value = draft.patch[control.dataset.field];
                        });
                        form.dataset.rev = draft.base_rev;
                        form.open = true;
                        const stale = Number(draft.base_rev) !== Number(form.dataset.serverRev);
                        form.querySelector('.lib-server-review').hidden = !stale;
                        form.querySelector('.lib-save-status').textContent = stale
                            ? '未儲存草稿已保留；伺服器有新版，請先比對再明確另存。' : '未儲存草稿已復原。';
                    }
                } catch (error) {}
            });
        } catch (err) {
            host.innerHTML = `<tr><td colspan="4" class="text-danger small py-3">Load failed: ${esc(err)}</td></tr>`;
        }
    }

    function appendUploadTarget(formData) {
        const id = document.getElementById('libUploadTarget')?.value;
        const entry = loadedEntries.find(e => e.entry_id === id);
        if (entry) {
            formData.append('entry_id', id);
            formData.append('base_rev', Number(entry._rev || 0));
        }
    }

    async function saveDecision(form, button) {
        const patch = {};
        form.querySelectorAll('[data-field]').forEach(control => {patch[control.dataset.field] = control.value;});
        const status = form.querySelector('.lib-save-status');
        persistDecision(form);
        button.disabled = true;
        status.textContent = '儲存中…';
        try {
            const res = await fetch('/api/literature/library/update', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pid: pid(), entry_id: form.dataset.entry, patch, base_rev: Number(form.dataset.rev)}),
            });
            const data = await res.json();
            if (!res.ok || data.status !== 'success') throw new Error(data.message || '儲存失敗');
            const newer = {};
            form.querySelectorAll('[data-field]').forEach(control => {newer[control.dataset.field] = control.value;});
            form.dataset.rev = data.entry._rev;
            if (JSON.stringify(newer) !== JSON.stringify(patch)) {
                persistDecision(form);
                status.textContent = '有較新文字尚未儲存；本機副本已保留。';
                return;
            }
            status.textContent = '已儲存';
            try { sessionStorage.removeItem(draftKey(form.dataset.entry)); } catch (error) {}
            await loadLibrary();
        } catch (err) {
            status.textContent = `未儲存；輸入已保留。${err.message} 請按 Refresh 比對新版。`;
        } finally { button.disabled = false; }
    }

    async function importItems() {
        const box = document.getElementById("libImportText");
        if (!box || !box.value.trim()) return;
        let items;
        try {
            items = JSON.parse(box.value);
            if (!Array.isArray(items)) throw new Error("需要 JSON 陣列");
        } catch (err) {
            alert(`JSON 解析失敗: ${err}`);
            return;
        }
        try {
            const res = await fetch("/api/literature/library/import", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pid: pid(), items: items }),
            });
            const data = await res.json();
            if (data.status === "success") {
                alert(`匯入完成: +${data.added} 新增, ${data.updated} 更新, ${data.skipped} 略過`);
                box.value = "";
                loadLibrary();
            } else {
                alert(`匯入失敗: ${data.message || "unknown"}`);
            }
        } catch (err) {
            alert(`匯入失敗: ${err}`);
        }
    }

    async function rebuildIndex() {
        if (!confirm("重建本專案 evidence index？（冪等，可重複執行）")) return;
        try {
            const res = await fetch("/api/literature/rebuild_evidence_index", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pid: pid() }),
            });
            const data = await res.json();
            if (data.status === "success") {
                alert(`索引完成: ${data.papers_indexed} 篇入索引, ${data.papers_skipped} 篇無 artifacts`);
            } else {
                alert(`重建失敗: ${data.message || "unknown"}`);
            }
        } catch (err) {
            alert(`重建失敗: ${err}`);
        }
    }

    function exportRefs(fmt) {
        if (!pid()) return;
        const scope = document.getElementById("libExportScope").value;
        window.open(`/api/literature/library/export?pid=${encodeURIComponent(pid())}&format=${fmt}&scope=${scope}`, "_blank");
    }

    window.literatureLibrary = { loadLibrary: loadLibrary, appendUploadTarget, importItems: importItems, rebuildIndex: rebuildIndex, exportRefs: exportRefs };

    document.addEventListener("DOMContentLoaded", function () {
        if (!document.getElementById("libraryPanel")) return;
        ["libFilterScreening"].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.addEventListener("change", loadLibrary);
        });
        const qEl = document.getElementById("libFilterQuery");
        if (qEl) qEl.addEventListener("keydown", function (ev) { if (ev.key === "Enter") loadLibrary(); });
        // literatureApp bootstrap 先跑完（拿到 currentPid）再載入
        setTimeout(loadLibrary, 800);
    });
})();

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
            const entries = (data && data.entries) || [];
            const badge = document.getElementById("libCountBadge");
            if (badge) badge.innerText = `${entries.length} entries`;
            if (!entries.length) {
                host.innerHTML = '<tr><td colspan="4" class="text-center text-muted small py-3">Library is empty. 執行搜尋或匯入後會自動累積。</td></tr>';
                return;
            }
            host.innerHTML = entries.map(function (e) {
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
                const selectHtml = function (field, options, current) {
                    return `<select class="form-select form-select-sm lib-status" data-entry="${esc(e.entry_id)}" data-field="${field}">`
                        + options.map(o => `<option value="${o}" ${o === current ? "selected" : ""}>${o}</option>`).join("")
                        + "</select>";
                };
                return `<tr>
                    <td class="small">${titleHtml}<div class="text-muted">${esc(authors)}${e.year ? " · " + esc(e.year) : ""}${e.venue ? " · " + esc(e.venue) : ""}</div></td>
                    <td class="small text-muted">${esc(e.doi || "")}</td>
                    <td>${selectHtml("screening_status", SCREENING, e.screening_status)}</td>
                    <td>${paperTag}</td>
                </tr>`;
            }).join("");
            host.querySelectorAll("select.lib-status").forEach(function (sel) {
                sel.addEventListener("change", function () {
                    updateEntry(sel.dataset.entry, sel.dataset.field, sel.value);
                });
            });
        } catch (err) {
            host.innerHTML = `<tr><td colspan="4" class="text-danger small py-3">Load failed: ${esc(err)}</td></tr>`;
        }
    }

    async function updateEntry(entryId, field, value) {
        const patch = {};
        patch[field] = value;
        try {
            const res = await fetch("/api/literature/library/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pid: pid(), entry_id: entryId, patch: patch }),
            });
            const data = await res.json();
            if (data.status !== "success") alert(`更新失敗: ${data.message || "unknown"}`);
        } catch (err) {
            alert(`更新失敗: ${err}`);
        }
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

    window.literatureLibrary = { loadLibrary: loadLibrary, importItems: importItems, rebuildIndex: rebuildIndex, exportRefs: exportRefs };

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

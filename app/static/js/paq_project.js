// Roothinks source maintenance contract
// 檔案路徑: app/static/js/paq_project.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 載入 PAQ project 狀態並控制 PI 可用動作，協調 promotion 與 Dashboard 導航。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/paq_project.js
/* 路徑(./app/static/js/paq_project.js) #版本 v0.5 #更版時間 20260221-1730 */
/**
 * ==================================================================================
 * Roothinks-PAQ Project Manager (v0.5)從paq_interact.js v3.4 拆分 (1045 行 → 3 檔案)
 * ==================================================================================
 * [v0.5 Critical Fix #6 — 破解三層嵌套姓名物件]
 * 根據 Log 證實，資料庫的 name 欄位為三層嵌套結構：
 * name: { original: { surname: '林', given: '銘杰' }, en: { ... } }
 * 新增專屬解析邏輯，完美組裝 original.surname + original.given。
 * * [v0.4 Critical Fix #5 — PI 終極萃取與底層資料透視]
 * 1. 強化 PI 解析：若無指定 role，無差別抓取第一個出現的姓名；若無法解析為 JSON，直接視為純文字姓名。
 * 2. 埋入 Console 偵察點：協助開發者直接於 F12 查看底層 API 吐出的原始 members 資料。
 * * [v0.3 Critical Fix #4 — PI 極致萃取與歷史資料清洗]
 * 1. PI 解析增強：後端若傳來 Unknown，前端啟動深度遞迴解析 `proj.members`。
 * 2. Taxonomy \n 清洗：自動將資料庫撈出的舊版 "中文 \n English" 轉換為 "中文 (English)"。
 * * [v0.2 Critical Fix #3 — PI 顯示 [object Object] 修復]
 * 問題：前端原有的 members 解析邏輯，抓到的是 JSON 物件 {"zh": "...", "en": "..."}，
 * 修正：全面廢除前端多餘的 members 陣列解析，直接使用後端已解析字串，加入型別防呆。
 *
 * [v0.1 Critical Fix #1 — API 回傳結構不匹配]
 * paq_routes.py 的 /api/paq/status/<pid> 回傳 FLAT (扁平) 結構。
 * 修正: 改為 `const proj = data;` 直接使用頂層屬性。
 * * [v0.1 Critical Fix #2 — CubeRenderer 函式簽名修正 (初始載入)]
 * 修正: 改為 renderVoxels('cube-container', { voxels: ..., axis_labels: ... })
 *
 * 本程式碼負責：
 * 1. 專案狀態載入 (loadPaqStatus) — Section 3
 * 2. 介面鎖定 (lockInterfaceForFormal) — Section 3.5
 * 3. 專案轉正 (Promote) — Section 8
 *
 * 依賴關係 (Dependencies):
 * - 全域變數: currentPid, localTaxonomy (宣告於 paq_initial.js)
 * - 函式: toggleLoading(), setDirty() (定義於 paq_initial.js)
 * - 函式: renderTaxonomyUI() (定義於 paq_interact.js)
 * - 外部: CubeRenderer (cube_renderer.js v1.4), bootstrap (Bootstrap 5)
 * ==================================================================================
 */

// ==================================================================================
// 3. 資料載入與 API 串接 (Data Loading & API)
// ==================================================================================

function isPaqPrincipalInvestigator(role) {
    const r = String(role || '').trim();
    if (!r || r.startsWith('共同主持人') || /\bco[\s-]*pi\b/i.test(r)) return false;
    return r.startsWith('主持人')
        || /^principal investigator\b/i.test(r)
        || /\bpi\b/i.test(r);
}

/**
 * 載入專案完整狀態 (Status & Survey Data)
 * 呼叫 API: GET /api/paq/status/<pid> 
 */
async function loadPaqStatus() {
    // 顯示全域 Loading (如果有的話) 或 Panel Specific Loading
    toggleLoading('taxonomy', true);

    // NOTE(NOTE-026): 提到 try 之外，好讓 voxel 那一段能在**主 try 結束之後**
    // 獨立執行。留在 try 內的話，voxel 就只能待在同一個 catch 底下，
    // 那正是「一個子資源失敗抹掉全部」的成因。
    //
    // 初值刻意是 null 而非 {}：null 代表「**根本沒拿到 survey**」，
    // 與「拿到了、但矩陣是空的」是兩件不同的事。用 {} 當初值的話，
    // 專案整個載入失敗時 voxel 區會顯示「尚未建立分類矩陣」——
    // 那是我們**無從得知**的事，等於用另一種形式再犯一次錯誤歸因。
    let surveyForVoxels = null;

    try {
        console.log(`[API] Fetching status for ${currentPid}...`);
        const res = await fetch(`/api/paq/status/${currentPid}`);
        
        if (!res.ok) {
            throw new Error(`HTTP Error: ${res.status}`);
        }

        const data = await res.json();

        if (!data.success) {
            throw new Error(data.message || 'Unknown API Error');
        }

        // 3.1 渲染 Header 資訊
        const proj = data;  

        document.getElementById('header-pid').textContent = proj.project_id || currentPid;
        document.getElementById('header-pname').textContent = proj.name || 'Untitled Project';
        document.getElementById('header-pname').title = proj.name || 'Untitled Project'; 
        
        // ====================================================================
        // [v0.5 Critical Fix] PI 終極無差別萃取器 (支援三層嵌套 JSON)
        // ====================================================================
        console.log("---------------------------------------------------");
        console.log("[PI Debug] Backend raw pi_name:", proj.pi_name);
        console.log("[PI Debug] Backend raw members:", proj.members);
        console.log("---------------------------------------------------");

        let piName = proj.pi_name;
        
        // 若後端解析失敗給了空值或 Unknown，啟動前端暴力萃取
        if (!piName || piName === 'Unknown') {
            let members = proj.members;
            
            if (members) {
                // 若為字串型態，嘗試轉為 JSON
                if (typeof members === 'string') {
                    try { 
                        members = JSON.parse(members); 
                    } catch (e) {
                        piName = members.trim();
                    }
                }

                // 嘗試從陣列中尋找 PI
                if (Array.isArray(members) && members.length > 0) {
                    // 精準排除 Co-PI，同時保留舊資料的 PI / Lead PI 英文角色。
                    let piObj = members.find(
                        m => m && isPaqPrincipalInvestigator(m.role)
                    );
                    
                    if (!piObj) {
                        // 無差別攻擊：如果沒有標註 role，直接抓出第一個有名字的人！
                        piObj = members.find(m => m && (m.name || m.zh || m.en));
                    }
                    
                    if (piObj && piObj.name) {
                        let n = piObj.name;
                        // [v0.5 核心邏輯] 處理三層嵌套物件： { original: {surname, given}, en: {surname, given} }
                        if (typeof n === 'object' && n !== null) {
                            let orig = n.original || n.zh || n.native;
                            let en = n.en || n.english;
                            
                            if (orig && typeof orig === 'object') {
                                // 中文習慣：姓 + 名 (不加空白)
                                piName = `${orig.surname || ''}${orig.given || ''}`.trim();
                            } else if (en && typeof en === 'object') {
                                // 英文習慣：名 + 姓 (加空白)
                                piName = `${en.given || ''} ${en.surname || ''}`.trim();
                            } else if (typeof orig === 'string') {
                                piName = orig;
                            } else if (typeof en === 'string') {
                                piName = en;
                            } else {
                                piName = n.full || n.display || 'Unknown';
                            }
                        } else if (typeof n === 'string') {
                            piName = n; // 單純字串
                        }
                    } else if (piObj) {
                        // 兼容舊格式
                        piName = piObj.zh || piObj.en || piName;
                    }
                } 
                // 嘗試從單一物件中尋找
                else if (members && typeof members === 'object' && !Array.isArray(members)) {
                    piName = members.name || members.zh || members.en || members.PI || members.pi || piName;
                }
            }
        }

        // 最終型別防呆：確保物件被轉化為乾淨的字串
        if (typeof piName === 'object' && piName !== null) {
            piName = piName.zh || piName.en || piName.full || piName.display || 'Unknown';
        }
        
        if (!piName || piName === '[object Object]' || piName.trim() === '') {
            piName = 'Unknown';
        }

        document.getElementById('header-pi').textContent = piName;
        // ====================================================================

        // 填入轉正 Modal 的預設值（temp/formal 都預填，避免空白）
        const finalTopicEl = document.getElementById('modal-final-topic');
        if (finalTopicEl) {
            finalTopicEl.value = (proj.research_title || proj.name || '').trim();
        }

        // 3.2 依專案狀態與**伺服器回報的存取權**設定介面。
        // NOTE(NOTE-027): formal 只鎖 taxonomy 編輯面，對話維持可用；
        // 是否唯讀由 access.can_edit（伺服器算）決定，前端不自行推導角色。
        if (proj.status === 'formal') {
            lockInterfaceForFormal();
        }
        if (proj.access && proj.access.can_edit === false) {
            lockInterfaceForViewer();
        }

        // 3.3 載入 Survey 資料 (Taxonomy & Cube)
        const survey = data.survey || {};
        surveyForVoxels = survey;

        // [v3.3 Fix] 資料結構對接: 優先使用 axis_labels, 兼容 axis_definitions
        const loadedLabels = survey.axis_labels || survey.axis_definitions || {};
        const loadedTags = survey.axis_tags || {};

        // ====================================================================
        // [v0.3 New] 歷史資料清洗器 (Legacy Data Cleaner)
        // 自動將資料庫裡舊版的 "\n" 轉換為 "( )"，無須使用者重新生成
        // ====================================================================
        const cleanBilingual = (str) => {
            if (typeof str !== 'string') return str;
            // 攔截並轉換 \n 或 \\n
            if (str.includes('\\n')) {
                let parts = str.split('\\n');
                return `${parts[0].trim()} (${parts[1].trim()})`;
            }
            if (str.includes('\n')) {
                let parts = str.split('\n');
                return `${parts[0].trim()} (${parts[1].trim()})`;
            }
            return str;
        };

        // 清洗 Labels
        ['x', 'y', 'z'].forEach(axis => {
            if (loadedLabels[axis]) loadedLabels[axis] = cleanBilingual(loadedLabels[axis]);
        });

        // 清洗 Tags
        ['x', 'y', 'z'].forEach(axis => {
            if (Array.isArray(loadedTags[axis])) {
                loadedTags[axis] = loadedTags[axis].map(cleanBilingual);
            }
        });
        // ====================================================================

        // 更新本地暫存 (Deep Copy to avoid ref issues)
        localTaxonomy.axis_labels = JSON.parse(JSON.stringify(loadedLabels));
        localTaxonomy.axis_tags = JSON.parse(JSON.stringify(loadedTags));

        // 更新 UI 輸入框 (X/Y/Z)
        ['x', 'y', 'z'].forEach(axis => {
            const el = document.getElementById(`label-${axis}`);
            if (el) el.value = localTaxonomy.axis_labels[axis] || '';
        });

        // 更新 Tag 列表 UI
        // NOTE(NOTE-026): taxonomy 渲染失敗寫進自己的錯誤槽，不得冒到外層
        // 而把專案名稱改寫成載入失敗。
        try {
            const taxErr = document.getElementById('taxonomy-error');
            if (taxErr) taxErr.classList.add('d-none');
            renderTaxonomyUI();
        } catch (taxError) {
            console.error('[Taxonomy] render failed', taxError);
            showTaxonomyError(taxError);
        }

        // 重置 Dirty 狀態 (剛載入視為乾淨)
        setDirty(false);

    } catch (e) {
        // NOTE(NOTE-026): 這個 catch 現在**只**負責「專案身分載不到」。
        // 3D voxel 已經移到下面自己的 try（見 renderVoxelsFromSurvey）——
        // 舊碼把它包在這裡，於是 cube_renderer 對壞 voxel 抛的 TypeError 會
        // 一路冒上來，把**已經正確寫進畫面**的專案名稱覆蓋成「⚠ Load Failed」。
        // 壞的是 3D 渲染器，畫面卻說專案名稱載入失敗 —— 錯誤歸因指向錯的元件，
        // 會讓下一個查的人往完全錯的方向走。
        console.error('[Load Error]', e);
        showProjectIdentityError(e);
    } finally {
        toggleLoading('taxonomy', false);
    }

    // 3.4 3D Voxel：獨立責任、獨立 try。
    // 刻意排在主 try 之外 —— 這一段失敗時，上面已經成功的專案名稱、PI、
    // taxonomy 與聊天都必須留在畫面上。
    try {
        renderVoxelsFromSurvey(surveyForVoxels);
    } catch (e) {
        console.error('[Voxel] render failed', e);
        showVoxelError(e);
    }
}

/**
 * 3D voxel 渲染與其失敗分類。
 *
 * NOTE(NOTE-026): 「沒有矩陣」與「矩陣壞掉」是兩種不同的使用者行動
 * （去建一個 vs 回報壞資料），所以在這裡就分流，不留給呼叫端猜。
 * 空值判斷刻意做在呼叫 renderVoxels **之前**：renderer 自己對空陣列會畫
 * 「Waiting for Data…」，那句話對「還沒建立矩陣」的人沒有任何指示作用。
 */
function renderVoxelsFromSurvey(survey) {
    // survey === null 代表「專案資料根本沒載到」。這時候**不能說**「尚未建立
    // 分類矩陣」—— 我們沒有任何依據宣稱矩陣不存在。呼叫端已經在
    // #header-pname 報過真正的失敗，這裡保持沉默即可。
    if (survey === null || survey === undefined) {
        showVoxelNotice('分類矩陣狀態未知',
            '專案資料未能載入，無法判斷矩陣是否存在。請重新整理或稍後再試。');
        return;
    }

    const voxels = survey.cube_data;

    if (voxels === null || voxels === undefined
        || (Array.isArray(voxels) && voxels.length === 0)) {
        showVoxelNotice('尚未建立分類矩陣',
            '完成 Task 1 的軸向與標籤後，按「Generate Cube Analysis」即可建立。');
        return;
    }

    if (!Array.isArray(voxels)) {
        // 型別就不對，連交給 renderer 都不必。
        throw new TypeError(`cube_data 應為陣列，實際為 ${typeof voxels}`);
    }

    if (typeof CubeRenderer === 'undefined') {
        throw new Error('CubeRenderer library not loaded');
    }

    console.log(`[Init] Rendering Cube with ${voxels.length} voxels.`);
    CubeRenderer.renderVoxels('cube-container', {
        voxels: voxels,
        axis_labels: localTaxonomy.axis_labels
    });
}

// --- 各區域專屬的錯誤呈現：每個只寫自己的 DOM（NOTE-026） ---------------------

/**
 * 只有「專案本身載不到」才寫 #header-pname。
 * 不 alert：子資源失敗時彈窗會擋住使用者操作，而且看不出是哪一塊壞了。
 */
function showProjectIdentityError(err) {
    const nameEl = document.getElementById('header-pname');
    if (!nameEl) return;
    nameEl.textContent = '專案載入失敗';
    nameEl.title = `無法取得專案資料：${(err && err.message) || err}`;
    nameEl.classList.add('text-danger');
}

/** 尚未建立矩陣：這不是錯誤，是還沒做，因此用中性語氣並指出下一步。 */
function showVoxelNotice(title, hint) {
    const el = document.getElementById('cube-container');
    if (!el) return;
    el.classList.add('d-flex', 'align-items-center', 'justify-content-center');
    el.innerHTML = `
        <div class="text-center text-white-50 px-3">
            <i class="bi bi-box" style="font-size: 2rem;"></i>
            <div class="fw-bold mt-2">${escapeHtml(title)}</div>
            <div class="small mt-1">${escapeHtml(hint || '')}</div>
        </div>`;
}

/** Voxel 資料格式錯誤：明說是 voxel，並保留原因供回報。 */
function showVoxelError(err) {
    const el = document.getElementById('cube-container');
    if (!el) return;
    el.classList.add('d-flex', 'align-items-center', 'justify-content-center');
    el.innerHTML = `
        <div class="text-center text-warning px-3">
            <i class="bi bi-exclamation-triangle" style="font-size: 2rem;"></i>
            <div class="fw-bold mt-2">Voxel 資料格式錯誤</div>
            <div class="small mt-1 text-white-50">${escapeHtml((err && err.message) || String(err))}</div>
            <div class="small mt-1 text-white-50">請重新執行 Task 2 產生矩陣。</div>
        </div>`;
}

/** taxonomy 清單顯示失敗：寫進 taxonomy 面板自己的錯誤槽。 */
function showTaxonomyError(err) {
    const el = document.getElementById('taxonomy-error');
    if (!el) return;
    el.textContent = `分類清單顯示失敗：${(err && err.message) || err}`;
    el.classList.remove('d-none');
}

/**
 * 鎖定介面 (唯讀模式)
 * 當專案狀態為 'formal' 時呼叫，防止修改
 */
function lockInterfaceForFormal() {
    console.log('[Mode] Locking interface for Formal Project.');
    
    // 1. 只鎖 taxonomy 編輯面。
    //
    // NOTE(NOTE-027): 舊碼是 `document.querySelectorAll('input')` —— 一個**全頁面**
    // 選擇器，於是 `#chat-input` 一起被停用：專案一轉正，PAQ Co-Pilot 對話就完全
    // 不能用，連 PI 自己都不行。轉正是專案的正常生命週期，不是降級。
    // 這個函式的意圖一直都只是「鎖住 taxonomy 編輯」，是範圍寫錯而不是意圖如此。
    const taxPanel = document.getElementById('panel-tax');
    if (taxPanel) {
        taxPanel.querySelectorAll('input, textarea').forEach(el => el.disabled = true);
    }

    // 2. 隱藏操作按鈕
    const btnFresh = document.getElementById('btn-tax-fresh');
    const btnRefine = document.getElementById('btn-tax-refine');
    const btnCube = document.getElementById('btn-run-cube');
    const promoteBtn = document.querySelector('button[onclick="openPromoteModal()"]');

    if (btnFresh) btnFresh.classList.add('d-none');
    if (btnRefine) btnRefine.classList.add('d-none');
    if (btnCube) btnCube.classList.add('d-none');
    if (promoteBtn) promoteBtn.classList.add('d-none');
    
    // 3. 移除 Tag 刪除按鈕與新增按鈕
    document.querySelectorAll('.remove-tag-icon').forEach(icon => icon.style.display = 'none');
    
    // 4. 對話維持可用。
    // NOTE(NOTE-027): 明確把 #chat-input 解鎖，不是「剛好沒被選到」而已 ——
    // 這一行同時是文件：轉正之後 PI/Co-PI 仍必須能延續 PAQ 對話。
    const chatInput = document.getElementById('chat-input');
    if (chatInput) chatInput.disabled = false;

    // 5. 顯示 Formal 標籤
    const headerName = document.getElementById('header-pname');
    if (headerName && !headerName.querySelector('.badge')) {
        const badge = document.createElement('span');
        badge.className = 'badge bg-success ms-2';
        badge.innerText = 'Formal (Locked)';
        headerName.appendChild(badge);
    }
}

/**
 * viewer（唯讀成員）：連送出都不給，因為後端 POST 需要 editor。
 *
 * NOTE(NOTE-027): 這是**呈現**，不是防線。使用者在 console 把 disabled 拿掉
 * 仍然只會拿到 403 —— 真正的門檻是 enforce_project_ownership。
 * 之所以還是要鎖，是因為讓人打完一整段話才被 403 是更差的體驗。
 */
function lockInterfaceForViewer() {
    ['chat-input', 'btn-tax-fresh', 'btn-tax-refine', 'btn-run-cube'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = true;
    });
    const chatInput = document.getElementById('chat-input');
    if (chatInput) chatInput.placeholder = '唯讀權限：無法送出訊息';
}

// ==================================================================================
// 8. 專案管理 (Promotion & Modals)
// ==================================================================================

/**
 * 開啟轉正 Modal
 */
function openPromoteModal() {
    const modalEl = document.getElementById('promoteModal');
    if(modalEl) {
        // Bootstrap 5 Modal
        new bootstrap.Modal(modalEl).show();
    }
}

/**
 * 提交轉正請求
 */
async function submitPromote() {
    const finalTopic = document.getElementById('modal-final-topic').value;
    
    if (!confirm('轉正後專案將被鎖定，確定繼續嗎？')) return;

    try {
        const res = await fetch('/api/paq/promote', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pid: currentPid, final_topic: finalTopic })
        });
        const result = await res.json();
        
        if (result.success) {
            alert('專案已成功轉正！');
            window.location.reload();
        } else {
            alert('轉正失敗: ' + result.message);
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { isPaqPrincipalInvestigator };
}

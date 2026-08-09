// Roothinks source maintenance contract
// 檔案路徑: app/static/js/paq_initial.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 PAQ 2A 初始對話、taxonomy 草稿與 session history，保存目前 PID 的未完成狀態。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/paq_initial.js
/* 路徑(./app/static/js/paq_initial.js) #版本 v0.3 #更版時間 20260226-0610 */
/**
 * ==================================================================================
 * Roothinks-PAQ Initial Setup & Layout Controller (v0.3)
 * ==================================================================================
 * [v0.3 Update] 新增 enforceReadOnlyMode()，支援專案轉正後的唯讀快照狀態鎖定。
 * [v0.2 Update] 新增 requestCubeFullscreen() 實體全螢幕功能與 restoreDefaultLayout() 恢復三視窗預設比例功能。
 * [v0.1] 從 paq_interact.js v3.4 拆分而來 (1045 行 → 3 檔案)
 * 本檔負責：
 * 1. 全域狀態管理 (Global State Management) — Section 0
 * 2. 初始化流程 (Initialization Flow) — Section 1
 * - DOMContentLoaded 事件
 * - PID 解析 (Query String / Path Param)
 * - 子系統啟動順序 (Layout → Status → Listeners → Plotly → Dock)
 * 3. 輸入監聽與狀態同步 (Input Listeners & Sync) — Section 2
 * - setupInputListeners: Enter 鍵新增 Tag、輸入同步 Dirty
 * - setDirty: 髒值標記與視覺回饋
 * - updateButtonStates: Fresh/Refine 按鈕狀態
 * 4. 佈局引擎與視窗控制 (Layout Engine & Resizers) — Section 7
 * - showPanel / hidePanel: Bootstrap d-flex 相容切換
 * - initLayoutSystem / initResizers: 拖曳調整器
 * - recalculateLayout: 動態寬度計算
 * - togglePanel / restorePanel / toggleMaximize: 最小化/最大化
 * - updateDock: 右下角還原工具列
 * - requestCubeFullscreen: [New] 呼叫 HTML5 實體全螢幕
 * - restoreDefaultLayout: [New] 一鍵恢復 30:40:auto 比例
 * 5. 全域工具函式 (Utilities)
 * - toggleLoading: Loading Spinner 控制
 * - initPlotlyResizeHandler: 3D Cube RWD
 * - initDockSystem: Dock 元素建立
 * 依賴關係 (Dependencies):檔必須在 paq_interact.js 和 paq_project.js 之前載入、宣告的全域變數 (currentPid, localTaxonomy 等) 供其他檔案使用
 * - 呼叫的外部函式: loadPaqStatus() [paq_project.js], createTag() [paq_interact.js]
 * 載入順序 (paq.html script tags):
 * 1. paq_initial.js (本檔 — 全域狀態 & 初始化)、2. paq_interact.js (三大任務互動)
 * 3. paq_project.js (專案載入 & Promote)、4. cube_renderer.js (3D 渲染引擎)
 */
// ==================================================================================
// 0. 全域狀態管理 (Global State Management)
// ==================================================================================

/** 當前專案 ID (Project ID) */
let currentPid = null;

/** 聊天歷史紀錄 (Chat Session History) - 用於多輪對話 Context */
let chatSessionHistory = []; 

/** * 本地暫存 Taxonomy 資料 (Local Data Store)
 * 用於在 UI 操作與 API 請求之間同步資料，[v3.3 Fix] 統一結構名稱以匹配後端: axis_labels, axis_tags
 */
let localTaxonomy = {
    axis_labels: {x: '', y: '', z: ''},
    axis_tags: {x: [], y: [], z: []}
};

/** * 髒值標記 (Dirty Flag)
 * true 表示使用者已手動修改過介面內容，但尚未提交給 AI 進行 Refine 或存檔
 */
let taxonomyDirty = false;

/**
 * 面板狀態設定 (Panel Configuration)，定義三個主要工作區的屬性，用於 Layout Engine 計算
 */
const panels = {
    'panel-tax':  { 
        visible: true, 
        label: 'Task 1: Taxonomy', 
        icon: 'bi-list-ul', 
        color: 'btn-outline-secondary',
        defaultWidth: 25 
    }, 
    'panel-cube': { 
        visible: true, 
        label: 'Task 2: Cube',     
        icon: 'bi-box',     
        color: 'btn-outline-primary',
        defaultWidth: 50
    },
    'panel-chat': { 
        visible: true, 
        label: 'Task 2A: Chat',   
        icon: 'bi-chat',    
        color: 'btn-outline-success',
        defaultWidth: 25
    }
};

/** 當前最大化的面板 ID (null 表示無最大化) */
let maximizedPanelId = null; 

function escapeHtml(value) {
    const str = String(value ?? '');
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function sanitizeIconClass(value) {
    const raw = String(value ?? '').trim();
    if (/^[a-zA-Z0-9-]+$/.test(raw)) return raw;
    return 'bi-square';
}

// ==================================================================================
// 1. 初始化流程 (Initialization Flow)
// ==================================================================================

document.addEventListener('DOMContentLoaded', () => {
    // 1.1 解析 URL 參數取得 PID
    // [v3.4 Enhancement] Robust PID Parsing
    // 優先順序: 1. Query String (?pid=...) 2. Path Param (/paq/status/...) 3. Path Param (/paq/...)
    const params = new URLSearchParams(window.location.search);
    currentPid = params.get('pid');
    
    if (!currentPid) {
        const path = window.location.pathname;
        // Regex to match typical PID patterns (alphanumeric, length 6-20) at end of path
        // Matches /paq/123456 or /paq/status/123456
        const match = path.match(/\/([A-Za-z0-9]{6,20})\/?$/);
        if (match && match[1]) {
            currentPid = match[1];
        }
    }

    if (!currentPid) {
        console.error("[Init] Missing PID in URL or Path");
        alert('無效的專案 ID (Missing PID). Redirecting to Dashboard...');
        window.location.href = '/';
        return;
    }

    console.log(`[Init] PAQ Loaded for Project: ${currentPid}`);
    try {
        localStorage.setItem('activePid', currentPid);
    } catch (e) {
        console.warn('[Init] Failed to persist activePid:', e);
    }

    // 1.2 初始化各子系統 (依序執行)
    initLayoutSystem();      // 佈局與拖曳系統
    loadPaqStatus();         // 載入專案資料 (非同步，但內部會處理 UI) [paq_project.js]
    setupInputListeners();   // 綁定輸入監聽 (Dirty Check)
    initPlotlyResizeHandler(); // 綁定 3D 圖表 RWD
    initDockSystem();        // 初始化最小化工作列

    // [v0.3 New] 執行唯讀模式檢查與介面鎖定
    enforceReadOnlyMode();

    // 1.3 初始按鈕狀態檢查
    updateButtonStates();
});

/**
 * [v0.3 New] 檢查專案狀態，若為 readonly 則強制鎖定所有輸入與操作
 */
async function enforceReadOnlyMode() {
    if (!currentPid) return;
    try {
        const res = await fetch(`/api/paq/status/${currentPid}`);
        const data = await res.json();
        
        if (data.success && data.status === 'readonly') {
            console.log("[Security] Project is Read-Only. Locking UI.");
            
            // 1. 禁用所有文字輸入框 (包含 x,y,z 軸設定)
            document.querySelectorAll('input, textarea').forEach(el => {
                el.disabled = true;
            });
            
            // 2. 禁用主要操作按鈕
            const actionButtons = [
                'btn-tax-fresh', 'btn-tax-refine', 
                'btn-run-cube'
            ];
            actionButtons.forEach(id => {
                const btn = document.getElementById(id);
                if (btn) btn.disabled = true;
            });
            
            // 封鎖 Chat 區塊
            const chatInput = document.getElementById('chat-input');
            if(chatInput) {
                chatInput.disabled = true;
                chatInput.placeholder = "唯讀模式 (Read-Only Mode)";
            }
            const chatBtn = document.querySelector('button[onclick="sendChat()"]');
            if (chatBtn) chatBtn.disabled = true;
            
            // 3. 移除 Promote 轉正按鈕 (避免重複轉正)
            const promoteBtn = document.querySelector('button[onclick="openPromoteModal()"]');
            if (promoteBtn) promoteBtn.remove();
            
            // 4. 顯示唯讀提示 Banner 於工作區頂部
            const workspace = document.getElementById('workspace-container');
            if (workspace) {
                const banner = document.createElement('div');
                banner.className = 'alert alert-secondary border-secondary text-center m-0 py-2 fw-bold d-flex justify-content-center align-items-center flex-shrink-0';
                banner.style.borderBottomLeftRadius = '0';
                banner.style.borderBottomRightRadius = '0';
                banner.innerHTML = '<i class="bi bi-lock-fill text-danger"></i> <span>此專案已轉為正式研究，當前為「唯讀快照模式」，無法編輯或執行 AI 任務。</span>';
                workspace.parentNode.insertBefore(banner, workspace);
            }
        }
    } catch (e) {
        console.error("[Security] Failed to check read-only status", e);
    }
}

/**
 * 初始化 Plotly 的 Resize 監聽器，確保在視窗縮放或面板調整時，3D Cube 能自適應重繪，防止圖表變形
 */
function initPlotlyResizeHandler() {
    window.addEventListener('resize', () => {
        if (typeof Plotly !== 'undefined') {
            const container = document.getElementById('cube-container');
            // 只有當容器內有 Plotly 實例 (data 屬性存在) 時才呼叫 resize
            if (container && container.data) {
                Plotly.Plots.resize(container);
            }
        }
    });
}

/**
 * 初始化右下角的 Dock 工作列 (用於還原最小化的視窗)
 * 動態建立 DOM 元素，不依賴 HTML 預先存在
 */
function initDockSystem() {
    if (!document.getElementById('panel-dock')) {
        const dock = document.createElement('div');
        dock.id = 'panel-dock';
        dock.className = 'position-fixed bottom-0 end-0 p-3 d-flex gap-2';
        dock.style.zIndex = '1050'; // Bootstrap Modal is 1055, keep below
        document.body.appendChild(dock);
    }
}

// ==================================================================================
// 2. 輸入監聽與狀態同步 (Input Listeners & Sync)
// ==================================================================================
/**
 * 設定所有輸入欄位的事件監聽，包含：Enter 鍵新增 Tag、輸入文字時標記 Dirty、輸入同步至 localTaxonomy
 * [v3.3 Fix] 確保 ID selector 與 paq.html v0.7 一致 (#label-x)
 */
function setupInputListeners() {
    const axes = ['x', 'y', 'z'];
    
    axes.forEach(axis => {
        const inputId = `label-${axis}`;
        const el = document.getElementById(inputId);
        
        if (el) {
            // 監聽 Enter 鍵 -> 觸發新增 Tag
            el.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault(); // 防止表單提交
                    createTag(axis); // [Cross-File] 定義在 paq_interact.js
                }
            });

            // 監聽輸入事件 -> 同步資料並標記 Dirty
            el.addEventListener('input', (e) => {
                // 更新本地暫存
                localTaxonomy.axis_labels[axis] = e.target.value;
                
                // 標記為已修改
                setDirty(true);
            });
        } else {
            console.warn(`[Setup] Input element #${inputId} not found!`);
        }
    });
}

/**
 * 設定髒值狀態 (Dirty Flag)
 * @param {boolean} isDirty 
 */
function setDirty(isDirty) {
    taxonomyDirty = isDirty;
    updateButtonStates();
    
    // 可選：在標題旁顯示未儲存提示 (Unsaved Changes Badge)
    // 這裡實作一個簡單的視覺反饋
    const headerTitle = document.querySelector('#panel-tax .panel-header span');
    if (headerTitle) {
        if (isDirty) {
            if (!headerTitle.innerHTML.includes('*')) {
                headerTitle.innerHTML += ' <span class="text-warning">*</span>';
            }
        } else {
            headerTitle.innerHTML = headerTitle.innerHTML.replace(' <span class="text-warning">*</span>', '');
        }
    }
}

/**
 * 根據狀態更新按鈕可用性 (Button A vs Button B) Fresh 生成與 Refine 優化的按鈕邏輯
 */
function updateButtonStates() {
    const btnFresh = document.getElementById('btn-tax-fresh');
    const btnRefine = document.getElementById('btn-tax-refine');
    
    if (!btnFresh || !btnRefine) return;

    // 邏輯：
    // Button A (Fresh): 隨時可用，但若 dirty 建議跳 Confirm
    // Button B (Refine): 隨時可用 (只要有基礎資料即可 Refine)
    
    if (taxonomyDirty) {
        btnRefine.classList.remove('btn-outline-success');
        btnRefine.classList.add('btn-success', 'text-white'); // Highlight Refine
        btnRefine.title = "您有未提交的修改，點此讓 AI 依據您的修改進行優化";
    } else {
        btnRefine.classList.remove('btn-success', 'text-white');
        btnRefine.classList.add('btn-outline-success');
        btnRefine.title = "基於當前內容進行優化";
    }
}

// ==================================================================================
// 7. 佈局引擎與視窗控制 (Layout Engine & Resizers)
// ==================================================================================
/**
 * Bootstrap 的 d-flex 屬性是 !important 的，直接用 style.display = 'none' 
 * 有時會失效或造成佈局錯亂。使用 helper function 切換 class。
 */
function showPanel(el) {
    el.classList.remove('d-none');
    el.classList.add('d-flex');
}
function hidePanel(el) {
    el.classList.remove('d-flex');
    el.classList.add('d-none');
}

/**
 * 初始化佈局與拖曳器
 */
function initLayoutSystem() {
    initResizers();
}

/**
 * 初始化 Resizer 拖曳邏輯
 */
function initResizers() {
    const resizer1 = document.getElementById('resizer-1');
    const resizer2 = document.getElementById('resizer-2');
    
    // 初始計算
    recalculateLayout(); 

    // 綁定 Resizer 1 (Taxonomy <-> Cube)
    if(resizer1) {
        setupResizer(resizer1, (deltaX) => {
            if (panels['panel-tax'].visible && panels['panel-cube'].visible) {
                resizePair('panel-tax', 'panel-cube', deltaX);
            }
        });
    }

    // 綁定 Resizer 2 (Cube <-> Chat)
    if(resizer2) {
        setupResizer(resizer2, (deltaX) => {
             if (panels['panel-cube'].visible && panels['panel-chat'].visible) {
                resizePair('panel-cube', 'panel-chat', deltaX);
             }
        });
    }

    /**
     * 通用 Resizer 事件綁定
     */
    function setupResizer(resizer, callback) {
        resizer.addEventListener('mousedown', (e) => {
            e.preventDefault();
            document.body.style.cursor = 'col-resize';
            const startX = e.clientX;
            
            const onMouseMove = (e) => {
                const deltaX = e.clientX - startX;
                callback(deltaX);
            };

            const onMouseUp = () => {
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
                document.body.style.cursor = '';
                // 拖曳結束後建議觸發一次 Resize 以確保圖表正確
                window.dispatchEvent(new Event('resize'));
            };

            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
        });
    }

    /**
     * 調整左右兩個面板的寬度
     */
    function resizePair(leftId, rightId, deltaX) {
        const leftEl = document.getElementById(leftId);
        const rightEl = document.getElementById(rightId);
        
        if(!leftEl || !rightEl) return;
        
        const leftW = leftEl.getBoundingClientRect().width;
        const rightW = rightEl.getBoundingClientRect().width;
        
        const newLeftW = leftW + deltaX;
        const newRightW = rightW - deltaX;

        // 設定最小寬度限制 (防止崩潰)
        if (newLeftW < 250 || newRightW < 250) return;

        leftEl.style.width = `${newLeftW}px`;
        rightEl.style.width = `${newRightW}px`;
        
        // 若其中包含 Cube，觸發 Plotly Resize
        if (leftId === 'panel-cube' || rightId === 'panel-cube') {
             // 使用 debounce 或 requestAnimationFrame 優化效能
             requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
        }
    }
}

/**
 * 重新計算佈局 (Layout Recalculation)+當面板顯示/隱藏/最大化/還原時呼叫
 */
function recalculateLayout() {
    if (maximizedPanelId) return; // 若有最大化面板，不進行自動分配

    const visiblePanels = Object.keys(panels).filter(k => panels[k].visible);
    const count = visiblePanels.length;
    
    if (count === 0) return;

    // 簡單平均分配策略 (進階版可依 defaultWidth 比例分配)
    const widthPct = 100 / count;
    
    Object.keys(panels).forEach(pid => {
        const el = document.getElementById(pid);
        if(!el) return;
        
        if (panels[pid].visible) {
            showPanel(el);
            // 扣除一些 gap buffer
            el.style.width = `calc(${widthPct}% - 10px)`;
        } else {
            hidePanel(el);
        }
    });

    // 控制 Resizers 的顯示
    const r1 = document.getElementById('resizer-1');
    const r2 = document.getElementById('resizer-2');
    
    if (r1) {
        r1.style.display = (panels['panel-tax'].visible && panels['panel-cube'].visible) ? 'block' : 'none';
    }
    if (r2) {
        r2.style.display = (panels['panel-cube'].visible && panels['panel-chat'].visible) ? 'block' : 'none';
    }

    // 延遲觸發 Resize 以確保 DOM 渲染完成
    setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
}

/**
 * 最小化面板 (Minimize) @param {string} panelId 
 */
function togglePanel(panelId) {
    if (maximizedPanelId) toggleMaximize(maximizedPanelId); // 先退出最大化
    
    panels[panelId].visible = false;
    recalculateLayout();
    updateDock(); // 更新右下角 Dock
}

/**
 * 還原面板 (Restore)
 * @param {string} panelId 
 */
function restorePanel(panelId) {
    panels[panelId].visible = true;
    recalculateLayout();
    updateDock();
}

/**
 * 更新右下角還原工具列
 */
function updateDock() {
    const dock = document.getElementById('panel-dock');
    if(!dock) return;
    
    dock.innerHTML = ''; // Clear
    
    Object.keys(panels).forEach(pid => {
        if (!panels[pid].visible) {
            const btn = document.createElement('button');
            btn.className = `btn btn-sm shadow ${panels[pid].color} bg-white border`;
            const icon = document.createElement('i');
            icon.className = `bi ${sanitizeIconClass(panels[pid].icon)}`;
            btn.appendChild(icon);
            btn.appendChild(document.createTextNode(` ${String(panels[pid].label ?? '')}`));
            btn.onclick = () => restorePanel(pid);
            dock.appendChild(btn);
        }
    });
}

/**
 * 最大化/還原 面板 (Maximize/Restore)
 * @param {string} panelId 
 */
function toggleMaximize(panelId) {
    const panel = document.getElementById(panelId);
    const icon = document.getElementById(`icon-max-${panelId}`);
    
    if (maximizedPanelId === panelId) {
        // --- Restore Mode ---
        maximizedPanelId = null;
        
        // 恢復之前的佈局狀態
        recalculateLayout(); 
        
        // 切換圖示
        if(icon) {
            icon.classList.remove('bi-fullscreen-exit');
            icon.classList.add('bi-arrows-fullscreen');
        }
        
    } else {
        // --- Maximize Mode ---
        maximizedPanelId = panelId;
        
        Object.keys(panels).forEach(pid => {
            const el = document.getElementById(pid);
            if (pid === panelId) {
                showPanel(el);
                el.style.width = '100%';
            } else {
                hidePanel(el);
            }
        });

        // 隱藏所有 Resizers
        document.querySelectorAll('.resizer').forEach(r => r.style.display = 'none');

        // 切換圖示
        if(icon) {
            icon.classList.remove('bi-arrows-fullscreen');
            icon.classList.add('bi-fullscreen-exit');
        }
    }
    
    // 觸發 Resize
    setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
}

/**
 * [v0.2 New] 呼叫瀏覽器原生 HTML5 實體全螢幕 (True Fullscreen)
 */
function requestCubeFullscreen() {
    const cubeContainer = document.getElementById('panel-cube');
    if (!cubeContainer) return;

    if (cubeContainer.requestFullscreen) {
        cubeContainer.requestFullscreen();
    } else if (cubeContainer.webkitRequestFullscreen) { /* Safari */
        cubeContainer.webkitRequestFullscreen();
    } else if (cubeContainer.msRequestFullscreen) { /* IE11 */
        cubeContainer.msRequestFullscreen();
    }
}

/**
 * [v0.2 New] 一鍵恢復三視窗預設比例 (Taxonomy 30%, Cube 40%, Chat 佔滿剩餘)
 */
function restoreDefaultLayout() {
    // 1. 若當前有最大化的面板，先強制退出最大化狀態
    if (maximizedPanelId) {
        toggleMaximize(maximizedPanelId);
    }
    
    // 2. 獲取三個面板元素
    const taxPanel = document.getElementById('panel-tax');
    const cubePanel = document.getElementById('panel-cube');
    const chatPanel = document.getElementById('panel-chat');
    
    // 3. 強制覆寫寬度樣式至預設完美比例
    if (taxPanel) taxPanel.style.width = '30%';
    if (cubePanel) cubePanel.style.width = '40%';
    if (chatPanel) chatPanel.style.width = ''; // 置空交由 d-flex flex-grow:1 自動填滿剩餘
    
    // 4. 強制更新面板狀態為全部顯示
    panels['panel-tax'].visible = true;
    panels['panel-cube'].visible = true;
    panels['panel-chat'].visible = true;
    
    if (taxPanel) showPanel(taxPanel);
    if (cubePanel) showPanel(cubePanel);
    if (chatPanel) showPanel(chatPanel);
    
    // 5. 確保分隔線 (Resizers) 恢復顯示
    const r1 = document.getElementById('resizer-1');
    const r2 = document.getElementById('resizer-2');
    if (r1) r1.style.display = 'block';
    if (r2) r2.style.display = 'block';
    
    // 6. 清除右下角的 Dock (因為全視窗皆已展開)
    updateDock();
    
    // 7. 觸發視窗 Resize 事件，通知 Plotly 圖表引擎重繪 3D Cube，避免破圖
    setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
}

// ==================================================================================
// 9. 全域工具函式 (Global Utilities)
// ==================================================================================
/**
 * [v3.4 New] 全域工具函式: 切換 Loading 顯示
 * @param {string} section - Loading 元素的 ID 後綴 (e.g. 'taxonomy' → #loading-taxonomy)
 * @param {boolean} show - true=顯示, false=隱藏
 */
function toggleLoading(section, show) {
    const el = document.getElementById(`loading-${section}`);
    if (el) {
        if (show) el.classList.remove('d-none');
        else el.classList.add('d-none');
    }
}


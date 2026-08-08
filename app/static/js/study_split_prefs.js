// 檔案路徑: roothinks/app/static/js/study_split_prefs.js
// 產生時間: 2026-08-07 22:10 +08:00
// 版本: v1.0
// 模組定位:
//   Study 頁分割位置（Split.js sizes）的本機持久化。
// 主要責任:
//   1. loadSplitSizes(): 讀回上次拉好的比例，讀不到或值不合法就回預設。
//   2. saveSplitSizes(): 寫入失敗不得讓拖曳這個動作失敗。
// 呼叫來源:
//   app/templates/study.html 的 Split.js 初始化（sizes 與 onDragEnd）。
// 輸入輸出契約:
//   sizes 是百分比陣列，長度需與 fallback 相同、每項為有限非負數、
//   總和 100（容差 1，Split.js 自身會有浮點誤差）。
//   任一條不符一律回 fallback。
// 安全邊界:
//   - localStorage 是使用者可任意竄改的儲存區，讀回的值一律當外部輸入驗證。
//     壞值會讓 Split 算出爛版面，而且每次重整都復發 —— 驗不過就回預設，
//     使用者至少能看到一個正常畫面再重拉。
//   - storage 由呼叫端傳入而不是直接抓 localStorage，才能在測試環境替換。
// 維護提醒:
//   - 這是「這台瀏覽器上的顯示偏好」，不是專案資料，刻意不進後端，
//     不跨裝置同步，也不值得為它開 API。
// ------------------------------------------------------------------------------
(function (global) {
    'use strict';

    function loadSplitSizes(storage, key, fallback) {
        try {
            var raw = JSON.parse(storage.getItem(key));
            if (!Array.isArray(raw) || raw.length !== fallback.length) return fallback;
            for (var i = 0; i < raw.length; i++) {
                var n = raw[i];
                if (typeof n !== 'number' || !isFinite(n) || n < 0) return fallback;
            }
            var total = raw.reduce(function (a, b) { return a + b; }, 0);
            if (Math.abs(total - 100) > 1) return fallback;
            return raw;
        } catch (e) {
            return fallback;
        }
    }

    function saveSplitSizes(storage, key, sizes) {
        try {
            storage.setItem(key, JSON.stringify(sizes));
            return true;
        } catch (e) {
            // 無痕模式／配額用盡：存不了就算了，不該讓拖曳失敗。
            return false;
        }
    }

    global.studySplitPrefs = {
        loadSplitSizes: loadSplitSizes,
        saveSplitSizes: saveSplitSizes
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { loadSplitSizes: loadSplitSizes, saveSplitSizes: saveSplitSizes };
    }
})(typeof window !== 'undefined' ? window : globalThis);

//路徑(./app/static/js/manuscript_ioruling.js)
//版本 v0.1
//更版時間 20260319-1530
// inner comment: 負責 2B 視窗多重章節勾選的連續性(防跳選)檢核

class ManuscriptIORuling {
    /**
     * 檢核章節選擇是否連續
     * @param {Array} allSections - 系統定義的所有章節陣列
     * @param {Set} currentSelectedSet - 目前已經勾選的章節 ID 集合
     * @param {String} newlyAddedId - 這次嘗試新勾選的章節 ID
     * @returns {Boolean} true 表示連續允許勾選，false 表示發生跳選應予阻擋
     */
    static validateContinuity(allSections, currentSelectedSet, newlyAddedId) {
        // 1. 取得所有「可自由編輯排序」的章節 ID 清單
        const editableIds = allSections.filter(s => !s.is_fixed).map(s => s.id);
        
        // 如果這個新 ID 不在可編輯清單中（例如 title），系統強制放行（因為固定章節本就無法點擊，這只是防呆）
        if (!editableIds.includes(newlyAddedId)) return true;

        // 2. 建立一個包含這次新勾選的「假設集合」
        const hypothetical = new Set([...currentSelectedSet, newlyAddedId]);
        
        // 3. 找出這些被選取的 ID 在原始陣列中的 index 位置
        const selectedIndices = [];
        editableIds.forEach((id, index) => {
            if (hypothetical.has(id)) {
                selectedIndices.push(index);
            }
        });

        // 如果選擇的章節總數少於 2 個，必定是連續的
        if (selectedIndices.length < 2) return true;

        // 4. 排序 index 並檢查是否為連續整數 (差異必須皆為 1)
        selectedIndices.sort((a, b) => a - b);
        for (let i = 1; i < selectedIndices.length; i++) {
            if (selectedIndices[i] - selectedIndices[i-1] !== 1) {
                return false; // 發現跳選！
            }
        }
        
        return true;
    }
}
// 掛載到全域
window.ioruling = ManuscriptIORuling;
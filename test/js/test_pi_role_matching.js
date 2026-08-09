// Roothinks source maintenance contract
// 檔案路徑: test/js/test_pi_role_matching.js
// 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
// 主要責任: 重現並驗收 pi role matching 的成功、失敗與回歸邊界。
// 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
// 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
// 驗證: node test/js/test_pi_role_matching.js
const assert = require('node:assert/strict');
const {
    isPaqPrincipalInvestigator,
} = require('../../app/static/js/paq_project.js');

for (const role of [
    '主持人 (Principal Investigator)',
    'Principal Investigator',
    'PI',
    'Lead PI',
]) {
    assert.equal(isPaqPrincipalInvestigator(role), true, role);
}

for (const role of [
    '共同主持人 (Co-PI)',
    'Co-PI',
    'Co PI',
    'Co-Principal Investigator',
    '研究助理',
    '',
]) {
    assert.equal(isPaqPrincipalInvestigator(role), false, role);
}

console.log('PAQ PI role behavior: passed');

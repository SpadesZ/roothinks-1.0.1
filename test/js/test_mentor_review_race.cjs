// 檔案路徑: test/js/test_mentor_review_race.cjs
// 建立時間: 2026-08-10 +08:00；版本: v1.0
// 模組定位: mentor.js stale-response race 的無瀏覽器可執行回歸測試。
// 驗證契約: B request 先完成、A 後完成時，只能 render/保存 B。
// 安全邊界: VM 中 stub fetch/render，不讀取登入 session 或正式資料。
// 執行: node test/js/test_mentor_review_race.cjs

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

global.window = {};
vm.runInThisContext(
    fs.readFileSync('app/static/js/mentor.js', 'utf8'),
    {filename: 'app/static/js/mentor.js'},
);

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return {promise, resolve};
}

(async () => {
    const app = new window.MentorDashboard();
    const requests = {A: deferred(), B: deferred()};
    const rendered = [];
    app.currentId = 42;
    app.showError = () => {};
    app._fetchJson = url => requests[url.endsWith('/A') ? 'A' : 'B'].promise;
    app._renderManuscript = manuscript => rendered.push(manuscript.pid);
    app._renderItems = () => {};

    const loadA = app.loadReview('A');
    const loadB = app.loadReview('B');
    requests.B.resolve({success: true, pid: 'B', manuscript: {pid: 'B'}, items: []});
    await loadB;
    requests.A.resolve({success: true, pid: 'A', manuscript: {pid: 'A'}, items: []});
    await loadA;

    assert.equal(app.currentPid, 'B');
    assert.equal(app.currentReview.pid, 'B');
    assert.deepEqual(rendered, ['B']);
    console.log('mentor review race: ok');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});

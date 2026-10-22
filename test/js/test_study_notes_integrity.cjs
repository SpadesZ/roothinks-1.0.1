// Roothinks source maintenance contract
// 合成 DOM/fetch 行為驗收：未 ack 不成功、衝突文字保留、舊讀取不蓋新輸入。
// 執行：node test/js/test_study_notes_integrity.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    const area = {value: 'human thought', dataset: {owner: '17'}, style: {}};
    const status = {textContent: ''};
    const stored = new Map();
    const context = vm.createContext({
        console, setTimeout, clearTimeout, URLSearchParams,
        window: {location: {search: '?pid=P-p', pathname: '/study/project/P-p'}},
        document: {getElementById: id => id === 'study-notes' ? area : status},
        localStorage: {setItem: (k, v) => stored.set(k, v), getItem: k => stored.get(k) || null, removeItem: k => stored.delete(k)},
    });
    vm.runInContext(fs.readFileSync('app/static/js/study_core.js', 'utf8'), context);
    const Core = vm.runInContext('StudyCore', context);
    const app = Object.create(Core.prototype);
    Object.assign(app, {pid: 'P-p', notesRev: 0, notesDirty: true, notesConflict: false, showToast() {}});
    return {context, app, area, status, stored};
}
function reply(status, data) { return {ok: status < 400, status, json: async () => data}; }

(async () => {
    {
        const {context, app, area, status, stored} = setup();
        let resolve;
        context.fetch = () => new Promise(done => {resolve = done;});
        const saving = app.saveNotes();
        assert.notEqual(area.style.borderColor, '#198754');
        assert.notEqual(status.textContent, '已儲存');
        resolve(reply(409, {ok: false, conflict: true, current_rev: 9}));
        await saving;
        assert.equal(area.value, 'human thought');
        assert.equal(app.notesRev, 0);
        assert.equal(app.notesConflict, true);
        assert.equal(app.notesDirty, true);
        assert.ok(stored.get('study-notes:17:P-p').includes('human thought'));
        context.fetch = () => {throw Error('衝突時不可自動重送');};
        await app.saveNotes();
    }
    {
        const {context, app, area, stored} = setup();
        context.fetch = async () => reply(500, {ok: true, rev: 1});
        await app.saveNotes();
        assert.equal(app.notesDirty, true);
        assert.notEqual(area.style.borderColor, '#198754');
        assert.equal(stored.size, 1);
        context.fetch = async () => reply(200, {ok: true, rev: 1});
        await app.saveNotes();
        assert.equal(app.notesDirty, false);
        assert.equal(stored.size, 0);
    }
    {
        const {context, app, area} = setup();
        let resolve;
        context.fetch = () => new Promise(done => {resolve = done;});
        const loading = app.loadNotes();
        area.value = 'typed while loading';
        resolve(reply(200, {ok: true, rev: 3, notes: 'old server'}));
        await loading;
        assert.equal(area.value, 'typed while loading');
        assert.equal(app.notesConflict, true);
    }
    {
        const {context, app, area, stored} = setup();
        stored.set('study-notes:17:P-p', JSON.stringify({notes: 'private local', base_rev: 0}));
        area.dataset.owner = '18';
        context.fetch = async () => reply(200, {ok: true, rev: 2, notes: 'user 18 server'});
        await app.loadNotes();
        assert.equal(area.value, 'user 18 server');
        assert.ok(stored.has('study-notes:17:P-p'));
    }
    console.log('study notes integrity: 4 scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

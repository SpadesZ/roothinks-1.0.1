// Roothinks source maintenance contract
// 驗證：node test/js/test_manuscript_draft_integrity.cjs；只用合成 DOM/socket。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup(rev = 0) {
    const stored = new Map();
    const status = {textContent: ''};
    const body = {innerHTML: 'my human paragraph'};
    const card = {getAttribute: () => 'intro', querySelector: () => body};
    const events = [];
    const context = vm.createContext({console, window: {localStorage: {
        setItem: (k, v) => stored.set(k, v), getItem: k => stored.get(k), removeItem: k => stored.delete(k),
    }}, document: {getElementById: id => id === 'currentUserId' ? {value: '17'} : status}});
    vm.runInContext(fs.readFileSync('app/static/js/manuscript_soed.js', 'utf8'), context);
    const Soed = vm.runInContext('ManuSoed', context);
    const app = Object.create(Soed.prototype);
    Object.assign(app, {
        app: {pid: 'P-p', paperTitleInput: {value: 'T'}, editorCanvas: {querySelectorAll: () => [card]},
              socket: {emit: (event, payload) => events.push({event, payload})}},
        _draftRevMap: rev == null ? {} : {intro: rev}, _draftDirtySections: {},
        _draftPending: {}, _draftConflicts: {}, _draftSeq: 0, _draftDirty: true,
        addSystemMessage() {}, _showConflictBar() {}, scheduleAutosave() {},
    });
    return {app, events, body, stored, status};
}
{
    const {app, events, stored} = setup();
    app._flushAutosave(); app._flushAutosave();
    assert.equal(events.length, 1, '同章節不能同時送兩個基礎版號相同的寫入');
    assert.equal(events[0].payload.base_rev, 0);
    app._handleDraftAck({section: 'intro', req_id: 'old response', ok: true, rev: 99});
    assert.equal(app._draftRevMap.intro, 0);
    app._handleDraftAck({section: 'intro', req_id: events[0].payload.req_id, ok: false, conflict: true, current_rev: 9});
    assert.equal(app._draftRevMap.intro, 0);
    assert.equal(app._draftDirty, true);
    assert.ok(stored.get('roothinks:draft:17:P-p:intro').includes('my human paragraph'));
    app._flushAutosave(); assert.equal(events.length, 1);
}
{
    const {app, events, stored, status} = setup();
    app._flushAutosave();
    app._handleDraftAck({section: 'intro', req_id: events[0].payload.req_id, ok: true, rev: 1});
    assert.equal(app._draftRevMap.intro, 1);
    assert.equal(app._draftDirty, false);
    assert.equal(stored.size, 0);
    assert.equal(status.textContent, '已自動儲存');
}
{
    const {app, events, body, stored, status} = setup();
    app._flushAutosave();
    body.innerHTML = 'new words during save';
    app._rememberDraft('intro', body.innerHTML);
    app._handleDraftAck({section: 'intro', req_id: events[0].payload.req_id, ok: true, rev: 1});
    assert.equal(app._draftDirty, true);
    assert.ok(stored.get('roothinks:draft:17:P-p:intro').includes('new words during save'));
    assert.notEqual(status.textContent, '已自動儲存');
}
{
    const {app, events, stored} = setup(null);
    app._flushAutosave();
    assert.equal(events[0].event, 'cmd_load_draft');
    assert.equal(stored.size, 1);
}
console.log('manuscript draft integrity: 4 scenarios passed');

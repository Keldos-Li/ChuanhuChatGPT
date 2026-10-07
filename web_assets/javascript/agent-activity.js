/* Display elapsed observation time without polling or mutating task state. */
(() => {
    if (globalThis.__chuanhuActivityVersion === 9) return;
    globalThis.__chuanhuActivityCleanup?.();
    globalThis.__chuanhuActivityVersion = 9;
    const clocks = new WeakMap();
    const expanded = new Map();
    function owned(detail, conversation) {
        return detail && !detail.closest('.history-message') && conversation !== undefined
            && detail.dataset.conversationId === conversation;
    }
    function detailKey(detail) {
        return JSON.stringify([detail.dataset.conversationId, detail.dataset.scopeId || '', detail.dataset.turnId || '', detail.dataset.layer || 'tool', detail.dataset.activityKey]);
    }
    const nodeStates = new WeakMap();
    const currentNodes = new Map();
    let restoreFrame;
    function clearRestoreMarkers() {
        for (const detail of currentNodes.values()) delete detail.dataset.restoringOpen;
    }
    function finishRestoration() {
        // One pending frame, consulting only the latest bindings. Streaming
        // replacements cannot queue callbacks retaining detached predecessors.
        if (restoreFrame === undefined) restoreFrame = requestAnimationFrame(() => {
            restoreFrame = undefined;
            clearRestoreMarkers();
        });
    }
    const explicitIntent = new WeakSet();
    function persistent(detail) {
        return detail.dataset.activityKey && detail.dataset.persistOpen !== 'false';
    }
    function save(key, open) {
        expanded.set(key, open);
        if (expanded.size > 1000) expanded.delete(expanded.keys().next().value);
    }
    function rememberToggle(event) {
        const detail = event.target;
        const app = typeof gradioApp === 'function' ? gradioApp() : document;
        if (!detail?.matches?.('#chuanhu-chatbot .agent-history-detail')
            || !owned(detail, globalThis.chuanhuInputConversation?.()) || !persistent(detail)
            || detail.isConnected === false
            || !Array.from(app.querySelectorAll('#chuanhu-chatbot .agent-history-detail')).includes(detail)) return;
        // A late event from a removed predecessor cannot overwrite the new
        // node. Native open is sampled synchronously, before queued toggle.
        explicitIntent.add(detail);
        tick();
    }
    document.addEventListener('toggle', rememberToggle, true);
    let timer;
    function tick(records = []) {
        const app = typeof gradioApp === 'function' ? gradioApp() : document;
        const conversation = globalThis.chuanhuInputConversation?.();
        let running = false;
        // Drain attributes even when a timer runs before the observer callback.
        // This captures a new node's user choice before first cache restoration.
        for (const record of [...(records || []), ...(observer.takeRecords?.() || [])]) {
            if (record.type === 'attributes' && record.attributeName === 'open'
                && !nodeStates.has(record.target)) explicitIntent.add(record.target);
        }
        // Sample predecessors before replacing them, including detached nodes:
        // their last DOM open may contain a click whose toggle is still queued.
        for (const [key, node] of currentNodes) {
            const state = nodeStates.get(node);
            if (state && detailKey(node) === key && node.open !== state.open) {
                save(key, node.open); state.open = node.open;
            }
        }
        const nextNodes = new Map();
        for (const detail of app.querySelectorAll('#chuanhu-chatbot .agent-history-detail')) {
            if (!owned(detail, conversation) || !persistent(detail)) continue;
            const key = detailKey(detail);
            let state = nodeStates.get(detail);
            if (!state || state.key !== key) {
                // Restore only a newly encountered DOM binding. Any open
                // attribute change or current toggle on that node wins.
                if (explicitIntent.has(detail)) save(key, detail.open);
                else if (expanded.has(key) && detail.open !== expanded.get(key)) {
                    // Restoring a replacement is not a user disclosure action.
                    // Resolve its final layout with transitions disabled before
                    // the next paint, without a separate animation state.
                    detail.dataset.restoringOpen = 'true';
                    detail.open = expanded.get(key);
                    globalThis.getComputedStyle?.(detail, '::details-content').height;
                    if (typeof requestAnimationFrame === 'function') {
                        finishRestoration();
                    } else delete detail.dataset.restoringOpen;
                }
                else save(key, detail.open);
                state = {key, open: detail.open}; nodeStates.set(detail, state);
            } else if (detail.open !== state.open || explicitIntent.has(detail)) {
                save(key, detail.open); state.open = detail.open;
            }
            explicitIntent.delete(detail);
            nextNodes.set(key, detail);
        }
        for (const [key, detail] of currentNodes) {
            if (nextNodes.get(key) !== detail) delete detail.dataset.restoringOpen;
        }
        currentNodes.clear();
        for (const [key, detail] of nextNodes) currentNodes.set(key, detail);
        for (const node of app.querySelectorAll('#chuanhu-chatbot .agent-activity-elapsed')) {
            const detail = node.closest('.agent-history-detail');
            if (!owned(detail, conversation) || detail.dataset.layer !== 'group') continue;
            if (node.dataset.running !== 'true') continue;
            const base = Number(node.dataset.elapsedMs);
            if (!Number.isFinite(base) || base < 0 || base > 31536000000) continue;
            let clock = clocks.get(node);
            if (!clock || clock.base !== base) {
                clock = {base, started: performance.now()}; clocks.set(node, clock);
            }
            const elapsed = base + Math.max(0, performance.now() - clock.started);
            const seconds = Math.floor(elapsed / 1000);
            const displayed = elapsed < 1000 ? (elapsed === 0 ? '0.0' : elapsed < 100 ? '<0.1' : (Math.floor(elapsed / 100) / 10).toFixed(1)) : String(seconds < 60 ? seconds : seconds % 60);
            const format = seconds < 60 ? (node.dataset.secondsFormat || '{seconds}s') : (node.dataset.minutesFormat || '{minutes}m {seconds}s');
            const text = format.replaceAll('{minutes}', String(Math.floor(seconds / 60))).replaceAll('{seconds}', displayed);
            if (node.textContent !== text) node.textContent = text;
            running = true;
        }
        if (running && timer === undefined) timer = setInterval(tick, 250);
        if (!running && timer !== undefined) { clearInterval(timer); timer = undefined; }
    }
    const observer = new MutationObserver(tick);
    function start() { observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['open'], attributeOldValue: true}); tick(); }
    globalThis.__chuanhuActivityCleanup = () => {
        observer.disconnect();
        document.removeEventListener?.('toggle', rememberToggle, true);
        expanded.clear();
        if (restoreFrame !== undefined) globalThis.cancelAnimationFrame?.(restoreFrame);
        restoreFrame = undefined;
        clearRestoreMarkers();
        currentNodes.clear();
        if (timer !== undefined) clearInterval(timer);
        timer = undefined;
        document.removeEventListener?.('DOMContentLoaded', start);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();
})();

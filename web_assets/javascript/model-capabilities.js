// One capability payload drives custom controls as well as native Gradio updates.
(function () {
    let snapshot = {};
    let queued = false;
    const root = () => typeof gradioApp === 'function' ? gradioApp() : document;
    window.chuanhuSupports = capability => snapshot[capability] !== false;
    window.chuanhuInputTarget = () => snapshot.sandbox_attachments === true ? '#agent-upload-files' : '#upload-index-file';
    window.chuanhuInputBusy = () => snapshot.busy === true;
    window.chuanhuInputConversation = () => snapshot.input_target || '';
    function apply() {
        queued = false;
        const app = root();
        const marker = app.querySelector('#model-capability-state [data-model-capabilities]');
        if (!marker) return;
        try { snapshot = JSON.parse(marker.dataset.modelCapabilities); } catch (_) { return; }
        const mapping = {
            regenerate: '.regenerate-btn', history_delete: '.delete-latest-btn',
            history_edit: '.edit-message-btn', history_rollback: '.rollback-btn',
            message_copy: '.copy-bot-btn', message_markdown: '.toggle-md-btn',
            input_attachments: '#upload-files-btn', knowledge: '#uploaded-files-btn',
            single_turn: 'input[name="single-session-cb"]',
            external_websearch: 'input[name="online-search-cb"]'
        };
        Object.entries(mapping).forEach(([capability, selector]) => {
            app.querySelectorAll(selector).forEach(element => {
                const visible = window.chuanhuSupports(capability);
                const target = element.matches('input[type="checkbox"]') ? element.closest('.switch-checkbox') : element;
                if (target && target.hidden === visible) target.hidden = !visible;
                if ('disabled' in element && element.disabled === visible) element.disabled = !visible;
            });
        });
        const more = app.querySelector('#chatbot-input-more-btn-div');
        const hasMore = ['input_attachments','knowledge','single_turn','external_websearch'].some(window.chuanhuSupports);
        if (more && more.hidden === hasMore) more.hidden = !hasMore;
        window.chuanhuRefreshSendButton?.();
        window.chuanhuRefreshArtifactCards?.();
        window.chuanhuClearSubmittedDraft?.(snapshot.submitted_draft);
        window.chuanhuApplyToolPatch?.(snapshot.tool_patch);
    }
    function schedule() {
        if (!queued) { queued = true; queueMicrotask(apply); }
    }
    const observer = new MutationObserver(schedule);
    function start() { observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['data-model-capabilities']}); apply(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

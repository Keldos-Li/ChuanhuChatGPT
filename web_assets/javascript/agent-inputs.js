// Capture the intended conversation before the browser's upload starts. A late
// upload completion must not attach files to a different model or new chat.
(function () {
    const root = () => typeof gradioApp === 'function' ? gradioApp() : document;
    let previousError = null, sawProgress = false;
    document.addEventListener('change', event => {
        const input = (event.composedPath ? event.composedPath() : [event.target])
            .find(node => node?.matches?.('#agent-upload-files input[type=file]'));
        if (!input || !input.files?.length) return;
        // Gradio can recreate this input when a model changes while a previous
        // upload is still in flight. Keep that batch's conversation immutable
        // until its upload callback has captured it.
        if (window.chuanhuAgentUploading) {
            event.preventDefault();
            event.stopImmediatePropagation();
            input.value = '';
            return;
        }
        window.chuanhuAgentUploadTarget = window.chuanhuInputConversation?.() || '';
        window.chuanhuAgentUploading = true;
        previousError = root().querySelector('#agent-upload-files .error');
        sawProgress = false;
    }, true);
    function removeSelected(event) {
        if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
        const button = (event.composedPath ? event.composedPath() : [event.target])
            .find(node => node?.matches?.('button'));
        const preview = button?.closest?.('#agent-pending-files');
        if (!preview) return;
        const row = button.closest('tr.file');
        if (row && !button.matches('.label-clear-button')) return;
        // These are the native per-file remove and clear-all buttons. Prevent
        // File.change from removing by a stale positional subset; submit only
        // the stable IDs that the user actually saw and selected.
        event.preventDefault();
        event.stopImmediatePropagation();
        if (window.chuanhuInputBusy?.() || window.chuanhuAgentUploading) return;
        let metadata;
        try { metadata = JSON.parse(preview.querySelector('[data-testid="block-label"]').textContent.trim()); }
        catch (_) { return; }
        const rows = Array.from(preview.querySelectorAll('tr.file'));
        if (!Array.isArray(metadata.ids) || metadata.ids.length !== rows.length) return;
        const ids = row ? [metadata.ids[rows.indexOf(row)]] : metadata.ids;
        if (!ids.length || ids.some(id => !id)) return;
        const app = root();
        const input = app.querySelector('#agent-input-remove-payload textarea, #agent-input-remove-payload input');
        const trigger = app.querySelector('#agent-input-remove');
        if (!input || !trigger) return;
        input.value = JSON.stringify({target: metadata.target, ids});
        input.dispatchEvent(new Event('input', {bubbles: true}));
        requestAnimationFrame(() => trigger.click());
    }
    document.addEventListener('click', removeSelected, true);
    document.addEventListener('keydown', removeSelected, true);
    const observer = new MutationObserver(() => {
        const app = root(), progress = app.querySelector('#agent-upload-files .uploading');
        if (progress) sawProgress = true;
        const error = app.querySelector('#agent-upload-files .error');
        if (!window.chuanhuAgentUploadStaging && !progress && error && (sawProgress || error !== previousError))
            window.chuanhuAgentUploading = false;
    });
    function start() { observer.observe(document.documentElement, {childList:true, subtree:true}); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

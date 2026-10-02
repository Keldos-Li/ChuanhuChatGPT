// Capture the intended conversation before the browser's upload starts. A late
// upload completion must not attach files to a different model or new chat.
(function () {
    document.addEventListener('change', event => {
        const input = (event.composedPath ? event.composedPath() : [event.target])
            .find(node => node?.matches?.('#agent-upload-files input[type=file]'));
        if (!input || !input.files?.length) return;
        window.chuanhuAgentUploadTarget = window.chuanhuInputConversation?.() || '';
        window.chuanhuAgentUploading = true;
    }, true);
    const observer = new MutationObserver(() => {
        const root = typeof gradioApp === 'function' ? gradioApp() : document;
        if (root.querySelector('#agent-upload-files .error')) window.chuanhuAgentUploading = false;
    });
    function start() { observer.observe(document.documentElement, {childList:true, subtree:true}); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

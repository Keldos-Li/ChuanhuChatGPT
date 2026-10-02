// Delegate from rerendered cards to the mounted native Gradio controls.
(function () {
    const root = () => typeof gradioApp === 'function' ? gradioApp() : document;
    document.addEventListener('click', event => {
        const card = (event.composedPath ? event.composedPath() : [event.target])
            .find(node => node?.matches?.('.model-file-card'));
        if (!card || card.disabled) return;
        const app = root();
        const identifier = card.dataset.artifactId;
        const feedback = text => { card.querySelector('.model-file-feedback').textContent = text; };
        if (card.dataset.fileAction === 'download') {
            const native = app.querySelector('#model-output-native-files');
            let ids;
            try { ids = JSON.parse(native.querySelector('[data-testid="block-label"]').textContent.trim()); }
            catch (_) { feedback('文件链接正在准备，请稍后重试'); return; }
            const links = native.querySelectorAll('td.download a[href]');
            const index = ids.indexOf(identifier);
            if (index < 0 || links.length !== ids.length || !links[index]) {
                feedback('文件链接正在准备，请稍后重试'); return;
            }
            links[index].click();
        } else if (card.dataset.fileAction === 'retry') {
            const input = app.querySelector('#model-output-retry-id textarea, #model-output-retry-id input');
            const trigger = app.querySelector('#model-output-retry');
            if (!input || !trigger) { feedback('暂时无法重试，请重新连接'); return; }
            input.value = identifier;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            card.disabled = true;
            feedback('正在重新获取');
            requestAnimationFrame(() => trigger.click());
        }
    });
})();

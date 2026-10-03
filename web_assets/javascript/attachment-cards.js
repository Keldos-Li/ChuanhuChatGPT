// 原生 File 保留上传状态；卡片只投影名称、大小和稳定附件 ID。
(function () {
    let scheduled = false;
    const root = () => typeof gradioApp === 'function' ? gradioApp() : document;
    function mount() {
        scheduled = false;
        const app = root(), composer = app.querySelector('#chatbot-input-tb-row');
        const native = app.querySelector('#agent-pending-files');
        if (!composer || !native) return;
        let metadata;
        try { metadata = JSON.parse(native.querySelector('[data-testid="block-label"]').textContent); }
        catch (_) { metadata = {}; }
        const files = metadata.target === window.chuanhuInputConversation?.() ? metadata.files || [] : [];
        let holder = composer.querySelector('.agent-pending-cards');
        if (!holder && files.length) {
            holder = document.createElement('div'); holder.className = 'agent-pending-cards';
            holder.setAttribute('role', 'group'); holder.setAttribute('aria-label', '待发送附件');
            composer.prepend(holder);
        }
        if (holder) {
            const signature = JSON.stringify([metadata.target, files]);
            if (holder._signature !== signature) {
                holder.replaceChildren(); holder._signature = signature;
                for (const file of files) {
                    const card = document.createElement('div'); card.className = 'agent-input-card';
                    const extension = file.extension || 'FILE';
                    // Same escaped server renderer as sent user and bot cards.
                    const icon = document.createElement('template'); icon.innerHTML = file.icon;
                    const text = document.createElement('span'); text.className = 'agent-input-card-text';
                    const name = document.createElement('span'); name.className = 'agent-input-name';
                    name.textContent = file.name; name.title = file.name;
                    const meta = document.createElement('span'); meta.className = 'agent-input-meta';
                    meta.textContent = extension + ' · ' + file.size_label;
                    text.append(name, meta);
                    const remove = document.createElement('button'); remove.type = 'button';
                    remove.className = 'agent-input-remove-card'; remove.textContent = '×';
                    remove.setAttribute('aria-label', '移除 ' + file.name);
                    remove.dataset.inputId = file.id; remove.dataset.inputTarget = metadata.target;
                    card.append(icon.content, text, remove); holder.append(card);
                }
            }
            const busy = window.chuanhuInputBusy?.() || window.chuanhuAgentUploading;
            for (const button of holder.querySelectorAll('button')) button.disabled = !!busy;
            holder.hidden = files.length === 0;
            composer.classList.toggle('agent-composer-has-files', files.length > 0);
        }
        const chat = app.querySelector('#chuanhu-chatbot');
        if (!chat) return;
        const used = new Set();
        for (const source of chat.querySelectorAll('.agent-user-file-source')) {
            const row = source.closest('.message-row.user-row');
            if (!row) continue;
            let cards = row.previousElementSibling;
            if (!cards?.classList.contains('agent-user-files')) {
                cards = document.createElement('div'); cards.className = 'agent-user-files';
                cards.setAttribute('role', 'group'); cards.setAttribute('aria-label', '此消息上传的附件');
                row.before(cards);
            }
            if (cards._markup !== source.innerHTML) { cards.innerHTML = source.innerHTML; cards._markup = source.innerHTML; }
            used.add(cards);
        }
        for (const cards of chat.querySelectorAll('.agent-user-files')) if (!used.has(cards)) cards.remove();
    }
    function schedule() { if (!scheduled) { scheduled = true; queueMicrotask(mount); } }
    window.chuanhuRefreshInputCards = schedule;
    const observer = new MutationObserver(schedule);
    function start() { observer.observe(document.documentElement, {childList: true, subtree: true, characterData: true}); mount(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();


function openSettingBox() {
    chuanhuPopup.classList.add('showBox');
    popupWrapper.classList.add('showBox');
    settingBox.classList.remove('hideBox');
    trainingBox.classList.add('hideBox');
    showMask("box");

}

function openTrainingBox() {
    chuanhuPopup.classList.add('showBox');
    popupWrapper.classList.add('showBox');
    trainingBox.classList.remove('hideBox');
    settingBox.classList.add('hideBox');
    showMask("box");
}

function openChatMore() {
    chatbotArea.classList.add('show-chat-more');
    showMask("chat-more");
}

function closeChatMore() {
    chatbotArea.classList.remove('show-chat-more');
    chatbotArea.querySelector('.chuanhu-mask')?.remove();
}


function showMask(obj) {
    const mask = document.createElement('div');
    mask.classList.add('chuanhu-mask');
    if (obj == "box") {
        mask.classList.add('mask-blur');
        document.body.classList.add('popup-open');
        popupWrapper.appendChild(mask);
    } else if (obj == "chat-more") {
        mask.classList.add('transparent-mask');
        chatbotArea.querySelector('#chatbot-input-more-area').parentNode.appendChild(mask);
    } else if (obj == "update-toast") {
        mask.classList.add('chuanhu-top-mask');
        if (document.querySelector('.chuanhu-top-mask')) {
            for (var i = 0; i < document.querySelectorAll('.chuanhu-top-mask').length; i++) {
                document.querySelectorAll('.chuanhu-top-mask')[i].remove();
            }
        }
        document.body.appendChild(mask);
        // mask.classList.add('transparent-mask');
    }
    

    mask.addEventListener('click', () => {
        if (obj == "box") {
            closeBox();
        } else if (obj == "chat-more") {
            closeChatMore();
        } else if (obj == "update-toast") {
            closeUpdateToast();
        }
    });
}

function chatMoreBtnClick() {
    if (chatbotArea.classList.contains('show-chat-more')) {
        closeChatMore();
    } else {
        openChatMore();
    }
}

function closeBtnClick(obj) {
    if (obj == "box") {
        closeBox();
    } else if (obj == "toolbox") {
        closeSide(toolbox);
        wantOpenToolbox = false;
    }
}

function closeBox() {
    chuanhuPopup.classList.remove('showBox');
    popupWrapper.classList.remove('showBox');
    trainingBox.classList.add('hideBox');
    settingBox.classList.add('hideBox');
    document.querySelector('.chuanhu-mask')?.remove();
    document.body.classList.remove('popup-open');
}

function closeSide(sideArea) {
    document.body.classList.remove('popup-open');
    sideArea.classList.remove('showSide');
    if (sideArea == toolbox) {
        chuanhuHeader.classList.remove('under-box');
        chatbotArea.classList.remove('toolbox-open')
        toolboxOpening = false;
    } else if (sideArea == menu) {
        chatbotArea.classList.remove('menu-open')
        menuOpening = false;
    }
    adjustMask();
}

function openSide(sideArea) {
    sideArea.classList.add('showSide');
    if (sideArea == toolbox) {
        chuanhuHeader.classList.add('under-box');
        chatbotArea.classList.add('toolbox-open')
        toolboxOpening = true;
    } else if (sideArea == menu) {
        chatbotArea.classList.add('menu-open')
        menuOpening = true;
    }
    // document.body.classList.add('popup-open');
}

function menuClick() {
    shouldAutoClose = false;
    if (menuOpening) {
        closeSide(menu);
        wantOpenMenu = false;
    } else {
        if (windowWidth < 1024 && toolboxOpening) {
            closeSide(toolbox);
            wantOpenToolbox = false;
        }
        openSide(menu);
        wantOpenMenu = true;
    }
    adjustSide();
}

function toolboxClick() {
    shouldAutoClose = false;
    if (toolboxOpening) {
        closeSide(toolbox);
        wantOpenToolbox = false;
    } else {
        if (windowWidth < 1024 && menuOpening) {
            closeSide(menu);
            wantOpenMenu = false;
        }
        openSide(toolbox);
        wantOpenToolbox = true;
    }
    adjustSide();
}

var menuOpening = false;
var toolboxOpening = false;
var shouldAutoClose = true;
var wantOpenMenu = windowWidth > 768;
var wantOpenToolbox = windowWidth >= 1024;

function adjustSide() {
    if (windowWidth >= 1024) {
        shouldAutoClose = true;
        if (wantOpenMenu) {
            openSide(menu);
            if (wantOpenToolbox) openSide(toolbox);
        } else if (wantOpenToolbox) {
            openSide(toolbox);
        } else {
            closeSide(menu);
            closeSide(toolbox);
        }
    } else if (windowWidth > 768 && windowWidth < 1024 ) {
        shouldAutoClose = true;
        if (wantOpenToolbox) {
            if (wantOpenMenu) {
                closeSide(toolbox);
                openSide(menu);
            } else {
                closeSide(menu);
                openSide(toolbox);
            }
        } else if (wantOpenMenu) {
            if (wantOpenToolbox) {
                closeSide(menu);
                openSide(toolbox);
            } else {
                closeSide(toolbox);
                openSide(menu);
            }
        } else if (!wantOpenMenu && !wantOpenToolbox){
            closeSide(menu);
            closeSide(toolbox);
        }
    } else { // windowWidth <= 768
        if (shouldAutoClose) {
            closeSide(menu);
            // closeSide(toolbox);
        }
    }
    checkChatbotWidth();
    adjustMask();
}

function adjustMask() {
    var sideMask = null;
    if (!gradioApp().querySelector('.chuanhu-side-mask')) {
        sideMask = document.createElement('div');
        sideMask.classList.add('chuanhu-side-mask');
        gradioApp().appendChild(sideMask);
        sideMask.addEventListener('click', () => {
            closeSide(menu);
            closeSide(toolbox);
        });
    }
    sideMask = gradioApp().querySelector('.chuanhu-side-mask');

    if (windowWidth > 768) {
        sideMask.style.backgroundColor = 'rgba(0, 0, 0, 0)';
        setTimeout(() => {sideMask.style.display = 'none'; }, 100);
        return;
    }
    // if (windowWidth <= 768)
    if (menuOpening || toolboxOpening) {
        document.body.classList.add('popup-open');
        sideMask.style.display = 'block';
        setTimeout(() => {sideMask.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';}, 200);
        sideMask.classList.add('mask-blur');
    } else if (!menuOpening && !toolboxOpening) {
        sideMask.style.backgroundColor = 'rgba(0, 0, 0, 0)';
        setTimeout(() => {sideMask.style.display = 'none'; }, 100);
    }
}

function checkChatbotWidth() {
    // let chatbotWidth = chatbotArea.clientWidth;
    // if (chatbotWidth > 488) {
    if (windowWidth > 768) {
        chatbotArea.classList.add('chatbot-full-width');
    } else {
        chatbotArea.classList.remove('chatbot-full-width');
    }

    if (windowWidth > 768) {
        chatbotArea.classList.remove('no-toolbox');
        chatbotArea.classList.remove('no-menu');

        if (!chatbotArea.classList.contains('toolbox-open') && chatbotArea.classList.contains('menu-open')) {
            chatbotArea.classList.add('no-toolbox');
        } else if (!chatbotArea.classList.contains('menu-open') && chatbotArea.classList.contains('toolbox-open')) {
            chatbotArea.classList.add('no-menu');
        } else if (!chatbotArea.classList.contains('menu-open') && !chatbotArea.classList.contains('toolbox-open')) {
            chatbotArea.classList.add('no-toolbox');
            chatbotArea.classList.add('no-menu');
        }
    }

    checkChatMoreMask();
}

function checkChatMoreMask() {
    if (!chatbotArea.classList.contains('chatbot-full-width')) {
        chatbotArea.querySelector('.chuanhu-mask')?.remove();
        chatbotArea.classList.remove('show-chat-more');
    }
}

var intro = null;
function showIntro() {
    // 检测是否已经加载了introjs
    while (!window.introJs) {
        setTimeout(() => { showIntro(); }, 500);
        return;
    }
    intro = introJs();
    intro.setOptions({
        disableInteraction: true,
        exitOnOverlayClick: false,
        // showBullets: false,
        // showProgress: true,
        hidePrev: true,
        steps: [
            { //0
                title: '我是教程',
                intro: 'Hello World! 👋 I will teach you how to use Chuanhu Chat<br><a href="javascript:void(0)" onclick="introJs().exit()">教程去死吧</a>'
            },
            { //1
                element: document.querySelector('#status-display #status-display'),
                intro: 'Here shows the status of the chatbot'
            },
            { //2
                element: document.querySelector('#chuanhu-menu-btn'),
                intro: '点这个按钮打开菜单，如果菜单未打开，点继续我会帮你自动打开菜单'
            },
            { //3
                element: document.querySelector('#chuanhu-setting-btn'),
                intro: '点这个按钮打开设置，点继续我会帮你自动打开设置'
            },
            { //4
                element: document.querySelector('.gradio-tabs > .gradio-tabitem > div:nth-child(1) > div.form:nth-child(1)'),
                intro: '记得在这里输入api key'
            },
            { //5
                element: document.querySelector('#model-select-dropdown'),
                intro: '在这里切换模型'
            },
            { //6
                element: document.querySelector('#new-chat-btn'),
                intro: '这个是新建对话'
            },
            { //7
                element: document.querySelector('#open-toolbox-btn'),
                intro: '点这个按钮打开工具箱'
            },
            { //8
                element: document.querySelector('#chuanhu-toolbox .tab-nav'),
                intro: '这里切换工具的tab',
                position: 'left'
            },
            { //9
                element: document.querySelector('#upload-index-file'),
                intro: '这里管理你的知识库文件'
            },
            { //10
                element: document.querySelector('#chatbot-input-row .chatbot-input-more-btn'),
                intro: '也可以在这上传文件',
                position: 'right'
            },
            { //11
                title: '对话',
                element: document.querySelector('#chatbot-input-tb-row'),
                intro: '开始对话吧这里和模型对话'
            },]
    })

    var introOpenMenu = false;
    var introOpenToolbox = false;
    intro.onafterchange(function () {
        setTimeout(() => {
            intro.refresh();
        }, 320);
    });
    intro.onbeforechange(function () {
        if (this._currentStep === 3) {
            tryClosePopup();
            if (!menuOpening) {
                menuClick();
                introOpenMenu = true;
            }
        }
        if (this._currentStep === 4) {
            openSettingBox();
            if (introOpenMenu) {
                menuClick();
                introOpenMenu = false;
            }
        }
        if (this._currentStep === 0 || this._currentStep === 1 || this._currentStep === 2 || this._currentStep === 5 || this._currentStep === 6 || this._currentStep === 7 || this._currentStep ===  11) {
            tryClosePopup();
        }
        if (this._currentStep === 8 || this._currentStep === 9) {
            if (!toolboxOpening) {
                toolboxClick();
                introOpenToolbox = true;
            }
        }
        if (this._currentStep === 10) {
            tryClosePopup();
            if (!chatbotArea.classList.contains('show-chat-more')) {
                chatMoreBtnClick();
            }
        }
    });
    intro.onexit(function () {
        console.log('exit');
        byebyeIntro();
    });
    intro.oncomplete(function () {
        console.log('end');
        byebyeIntro();
    });

    setTimeout(() => { 
        intro.start();
    }, 500);

    function tryClosePopup() {
        let mask = gradioApp().querySelector('.mask-blur');
        if (mask?.style.backgroundColor === 'rgba(0, 0, 0, 0.5)' || mask?.style.display !== 'none') {
            closeBtnClick("box");
        }
        if (introOpenToolbox) {
            toolboxClick();
            introOpenToolbox = false;
        }
        if (introOpenMenu) {
            menuClick();
            introOpenMenu = false;
        }
    }
    function byebyeIntro() {
        localStorage.setItem('needIntro', 'false');
    }
}

// function adjustIntroTourForMenu() {
//     console.log('adjustIntroTourForMenu');
//     var sideOpening = gradioApp().querySelector('.chuanhu-side-mask');
//     if (sideOpening) {
//         document.querySelector('.introjs-helperLayer.introjs-fixedTooltip').style.left = '12px';
//         document.querySelector('.introjs-tooltipReferenceLayer.introjs-fixedTooltip').style.left = '0';
//     }
// }
// function adjustIntroTourForToolbox() {
//     console.log('adjustIntroTourForToolbox');
    
// }

/*
function setHistroyPanel() {
    const historySelectorInput = gradioApp().querySelector('#history-select-dropdown input');
    const historyPanel = document.createElement('div');
    historyPanel.classList.add('chuanhu-history-panel');
    historySelector.parentNode.insertBefore(historyPanel, historySelector);
    var historyList=null;

    historySelectorInput.addEventListener('click', (e) => {
        e.stopPropagation();
        historyList = gradioApp().querySelector('#history-select-dropdown ul.options');

        if (historyList) {
            // gradioApp().querySelector('.chuanhu-history-panel')?.remove();
            historyPanel.innerHTML = '';
            let historyListClone = historyList.cloneNode(true);
            historyListClone.removeAttribute('style');
            // historyList.classList.add('hidden');
            historyList.classList.add('hideK');
            historyPanel.appendChild(historyListClone);
            addHistoryPanelListener(historyPanel);
            // historySelector.parentNode.insertBefore(historyPanel, historySelector);
        }
    });
}
*/

// function addHistoryPanelListener(historyPanel){
//     historyPanel.querySelectorAll('ul.options > li').forEach((historyItem) => {
//         historyItem.addEventListener('click', (e) => {
//             const historySelectorInput = gradioApp().querySelector('#history-select-dropdown input');
//             const historySelectBtn = gradioApp().querySelector('#history-select-btn');
//             historySelectorInput.value = historyItem.innerText;
//             historySelectBtn.click();
//         });
//     });
// }


// function testTrain() {
    
//     trainBody.classList.toggle('hide-body');
//     trainingBox.classList.remove('hideBox');

//     var chuanhuBody = document.querySelector('#chuanhu-body');
//     chuanhuBody.classList.toggle('hide-body');
// }
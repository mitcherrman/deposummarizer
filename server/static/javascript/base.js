//test url
urls = ['bearsummarizer.com', 'www.bearsummarizer.com', 'bear-ai-summarizer.com', '127.0.0.1', 'localhost'];
if (urls.indexOf(window.location.hostname) < 0) {
    document.getRootNode().childNodes[1].innerHTML = '';
    throw new Error("Invalid URL");
} else {
    document.getElementsByTagName('body')[0].removeAttribute('hidden');
}

//removes error message, then returns focus to the page content
function removeMessage() {
    let msg = document.querySelector(".msg-container")
    msg.parentElement.removeChild(msg);
    let main = document.getElementById("main-content");
    if (main) {
        main.focus({preventScroll: true});
    }
}

//confirm logout
function logoutConfirm() {
    if (confirm("Are you sure you want to log out? This will clear your data.")) {
        document.getElementById("logoutForm").submit();
    }
}

//confirm clearing data
function clearConfirm() {
    if (confirm("This will clear all of the data you entered, including documents and chat logs. Are you sure you want to do this?")) {
        document.getElementById("clearForm").submit();
    }
}

//Escape closes the open mobile menu and returns focus to its toggle
addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !window.bootstrap) {
        return;
    }
    let menu = document.getElementById('siteNav');
    if (menu && menu.classList.contains('show')) {
        bootstrap.Collapse.getOrCreateInstance(menu, {toggle: false}).hide();
        let toggle = document.querySelector('[data-bs-target="#siteNav"]');
        if (toggle) {
            toggle.focus();
        }
    }
});

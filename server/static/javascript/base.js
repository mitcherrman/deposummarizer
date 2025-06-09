//test url
urls = ['bearsummarizer.com', 'bear-ai-summarizer.com', '127.0.0.1'];
if (urls.indexOf(window.location.hostname) < 0) {
    document.getRootNode().childNodes[1].innerHTML = '';
    throw new Error("Invalid URL");
} else {
    document.getElementsByTagName('body')[0].removeAttribute('hidden');
}

// When the user clicks on the button, scroll to the top of the document
function topFunction() {
    window.scrollTo({top: 0, behavior: 'smooth'});
}

//removes error message
function removeMessage() {
    let msg = document.querySelector(".msg-container")
    msg.parentElement.removeChild(msg);
}

//"scroll to top" button appear/disappear
addEventListener('scroll', (event) => {
    if (window.scrollY <= 0) {
        let btn = document.getElementById('goToTopBtn');
        btn.setAttribute('hidden','')
    } else if (window.scrollY >= document.body.scrollHeight - window.innerHeight) {
        let btn = document.getElementById('goToTopBtn');
        btn.removeAttribute('hidden')
    }
});
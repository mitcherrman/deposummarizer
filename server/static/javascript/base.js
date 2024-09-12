// When the user clicks on the button, scroll to the top of the document
function topFunction() {
    window.scrollTo({top: 0, behavior: 'smooth'});
}

addEventListener('scroll', (event) => {
    if (window.scrollY <= 0) {
        btn = document.getElementById('goToTopBtn');
        btn.setAttribute('hidden','')
    } else if (window.scrollY >= document.body.scrollHeight - window.innerHeight) {
        btn = document.getElementById('goToTopBtn');
        btn.removeAttribute('hidden')
    }
});
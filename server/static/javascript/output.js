checkSummaryIntervalId = 0

function startCheckThread() {
    checkSummaryIntervalId = setInterval(checkSummary, 1000);
}

function checkSummary() {
    currUrl = window.location.href;
    fetch(currUrl + "/verify").then((response) => {
        if (response.status != 200) {
            clearInterval(checkSummaryIntervalId);
            load = document.getElementById("loading");
            parent = load.parentElement;
            frame = document.createElement("iframe");
            frame.setAttribute("src", "/out");
            frame.style.setProperty('width', "75%");
            frame.style.setProperty("aspect-ratio", "3 / 2");
            parent.replaceChild(frame, load);
            document.querySelector(".form-group").removeAttribute("hidden");
        }
    });
}

document.getElementById("question").addEventListener("keydown", (event) => {
    if (event.key == "Enter") {
        form = document.getElementById("chat-question");
        form.submit();
        form.reset();
        document.getElementById("question").blur();
    }
});
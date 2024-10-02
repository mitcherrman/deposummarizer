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
            document.querySelector(".chat-container").removeAttribute("hidden");
        }
    });
}

function createMessageBubble(text, out, error=false) {
    bubble = document.createElement("p");
    bubble.classList.add("chat-messages-bubble");
    if (out) {
        bubble.classList.add("chat-messages-out");
    } else {
        bubble.classList.add("chat-messages-in");
    }
    if (error) {
        bubble.classList.add("chat-messages-error");
    }
    bubble.innerText = text;
    document.querySelector(".chat-messages").appendChild(bubble);
}

function submitQuestion() {
    data = new FormData(document.querySelector(".chat-question"));
    createMessageBubble(data.get("question"), true);
    box = document.querySelector(".chat-messages");
    box.scrollTo(0, box.scrollHeight-box.offsetHeight);
    currUrl = window.location.href;
    let ok = true;
    textbox = document.getElementById("question");
    textbox.setAttribute("disabled","");
    fetch(currUrl.substring(0,currUrl.length-6) + "ask", {
        method: "POST",
        body: data
    }).then((response) => {
        ok = response.ok;
        return response.text();
    }).then((answer) => {
        let jumpBottom = box.scrollTop >= box.scrollHeight-box.offsetHeight-1;
        createMessageBubble(answer, false, !ok);
        if (jumpBottom) {
            box.scrollTo(0, box.scrollHeight-box.offsetHeight);
        }
    }).catch((exc) => {
        createMessageBubble("It appears the server did not respond properly, check console log for more details.", false, true);
        throw exc;
    }).finally(() => {
        textbox.removeAttribute("disabled");
    });
}

document.getElementById("question").addEventListener("keydown", (event) => {
    if (event.key == "Enter") {
        form = document.getElementById("chat-question");
        submitQuestion();
        form.reset();
        document.getElementById("question").blur();
    }
});
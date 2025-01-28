checkSummaryIntervalId = 0

//called in rendering
function init() {
    startCheckThread();
    includeChat();
}

//restores chat history
function includeChat() {
    let msgBox = document.querySelector(".chat-messages");
    fetch("chat").then((response) => {
        return response.text();
    }).then((text) => {
        msgBox.innerHTML = text;
    });
}

function startCheckThread() {
    checkSummaryIntervalId = setInterval(checkSummary, 1000);
}

//checks for complete summary, called periodically
function checkSummary() {
    fetch("out/verify").then((response) => {
        if (response.status != 200) {
            clearInterval(checkSummaryIntervalId);
            let parent = document.querySelector(".summary-container");
            let load = document.getElementById("loading");
            let frame = document.createElement("iframe");
            frame.setAttribute("src", "/out");
            frame.style.setProperty('width', "99%");
            frame.style.setProperty("aspect-ratio", "3 / 2");
            parent.prepend(frame);
            document.querySelector(".body-container").removeAttribute("hidden");
            load.parentNode.removeChild(load);
        }
    });
}

//adds message bubble to chatbox
function createMessageBubble(text, out, error=false) {
    let bubble = document.createElement("p");
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

//called when question asked to chatbot
function submitQuestion() {
    //adjust visuals
    let data = new FormData(document.querySelector(".chat-question"));
    createMessageBubble(data.get("question"), true);
    let box = document.querySelector(".chat-messages");
    box.scrollTo(0, box.scrollHeight-box.offsetHeight);
    let ok = true;
    let textbox = document.getElementById("question");
    textbox.setAttribute("disabled","");
    //api call
    fetch("ask", {
        method: "POST",
        body: data
    }).then((response) => {
        ok = response.ok;
        return response.text();
    }).then((answer) => {
        //adjust visuals/include message
        let jumpBottom = box.scrollTop >= box.scrollHeight-box.offsetHeight-1;
        createMessageBubble(answer, false, !ok);
        if (jumpBottom) {
            box.scrollTo(0, box.scrollHeight-box.offsetHeight);
        }
        if (ok) {
            document.querySelector(".chat-download-button").removeAttribute("hidden");
        }
    }).catch((exc) => {
        //error chat message
        createMessageBubble("It appears the server did not respond properly, please try again later.", false, true);
        throw exc;
    }).finally(() => {
        textbox.removeAttribute("disabled");
    });
}

//submit question via keypad
document.getElementById("question").addEventListener("keydown", (event) => {
    if (event.key == "Enter") {
        let form = document.getElementById("chat-question");
        submitQuestion();
        form.reset();
        document.getElementById("question").blur();
    }
});

//change download format depending on dropbox value
function changeDownloadFormat() {
    let data = document.getElementById("summary-download-option").value;
    let download_elem = document.querySelector(".summary-download-button");
    download_elem.setAttribute("href", "out/" + data);
}
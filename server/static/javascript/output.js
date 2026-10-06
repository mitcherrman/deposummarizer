// Completed-summary workspace on /output (BEAR-V2-B3).
//
// processing.js owns job polling; when the page is (or becomes) ready it
// calls insertIframe(), which reveals the workspace once and then loads the
// PDF preview and the chat history. Everything else here is event wiring:
// the Summary / Ask Bear switch on narrow screens, download buttons with
// busy and error states, and the chat form.
//
// Server contracts used (unchanged): GET out/pdf (and HEAD as an existence
// probe), GET out/docx, GET chat (server-rendered, autoescaped history),
// POST ask (FormData: csrfmiddlewaretoken + question; answer is plain text),
// GET transcript. Answers and history are only ever inserted as text or as
// the server's escaped fragment; server error text is never shown.

//wording for a failed POST ask, by status code (0 = no response)
function chatErrorFor(status) {
  if (status === 409) {
    return {unavailable: true, text: "Chat isn't available for this summary. Upload the PDF again to use chat."};
  }
  if (status === 400) {
    return {text: "Type a question first."};
  }
  if (status === 403) {
    return {text: "Your session has changed. Reload the page and try again."};
  }
  if (status === 0) {
    return {text: "Couldn't reach BearSummarizer. Check your connection and try again."};
  }
  return {text: "Bear couldn't answer that right now. Please try again in a moment."};
}

//the question as sent; blank questions are not sent
function normalizeQuestion(text) {
  return String(text == null ? "" : text).trim();
}

//filename from a Content-Disposition header, or null
function filenameFrom(disposition) {
  var m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(String(disposition || ""));
  if (!m) return null;
  try {
    return decodeURIComponent(m[1].trim());
  } catch (e) {
    return m[1].trim();
  }
}

var DOWNLOADS = {
  pdf:  {name: "deposition_summary.pdf",  busy: "Preparing the PDF…",           done: "PDF download started.",
         failed: "The PDF couldn't be downloaded. Please try again."},
  docx: {name: "deposition_summary.docx", busy: "Preparing the Word document…", done: "Word document download started.",
         failed: "The Word document couldn't be prepared. Please try again, or download the PDF."}
};

if (typeof module === "object" && module.exports) {
  module.exports = {chatErrorFor: chatErrorFor, normalizeQuestion: normalizeQuestion,
                    filenameFrom: filenameFrom, DOWNLOADS: DOWNLOADS};
}

//reveals the workspace (once) and loads its content; processing.js calls
//this when the job is ready. #loading is removed as in B2.
function insertIframe() {
  var load = document.getElementById("loading");
  if (load) load.parentNode.removeChild(load);
  var ws = document.getElementById("workspace");
  if (!ws || !ws.hidden) return;
  ws.hidden = false;
  outputWorkspace.loadPreview();
  outputWorkspace.loadChat();
}

var outputWorkspace = (typeof document === "undefined") ? null : (function () {
  var ws = document.getElementById("workspace");
  if (!ws) return null;

  function $(id) { return document.getElementById(id); }
  function coarsePointer() {
    return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  }

  // ───── Summary / Ask Bear switch (below 1024px) ─────
  var switches = ws.querySelectorAll("[data-view-target]");
  var chatSeen = false;
  function setView(view) {
    ws.dataset.view = view;
    switches.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.viewTarget === view));
    });
    //bring the chosen panel to the top of the screen: on taller phones the
    //switch sticks under the site header, so park it there; on short
    //(landscape) screens it doesn't stick, so park the panel itself
    var bar = ws.querySelector(".view-switch");
    var header = document.querySelector(".site-header");
    var stop = (header ? header.getBoundingClientRect().bottom : 0) + 8;
    var anchor = getComputedStyle(bar).position === "sticky" ? bar : ws.querySelector(".workspace-grid");
    window.scrollBy({top: anchor.getBoundingClientRect().top - stop, behavior: "instant"});
    //history loaded while the chat was hidden could not scroll to the end
    if (view === "chat" && !chatSeen) {
      chatSeen = true;
      scrollToBottom();
    }
  }
  switches.forEach(function (b) {
    b.addEventListener("click", function () { setView(b.dataset.viewTarget); });
  });

  // ───── PDF preview ─────
  var frame = $("docFrame");
  var frameTimer = null;
  function setFrameState(state) {
    frame.dataset.state = state;
    frame.querySelectorAll("[data-frame-message]").forEach(function (m) {
      m.hidden = m.dataset.frameMessage !== state;
    });
  }
  function loadPreview() {
    var src = frame.dataset.previewSrc;
    var old = frame.querySelector("iframe");
    if (old) old.parentNode.removeChild(old);
    clearTimeout(frameTimer);
    setFrameState("loading");
    //browsers without an inline PDF viewer (e.g. Android Chrome) would
    //download the file instead of showing it
    if (navigator.pdfViewerEnabled === false) {
      setFrameState("unsupported");
      return;
    }
    fetch(src, {method: "HEAD", cache: "no-store", credentials: "same-origin"}).then(function (response) {
      if (!response.ok) throw new Error("preview unavailable");
      var iframe = document.createElement("iframe");
      iframe.className = "doc-frame__iframe";
      iframe.title = "Summary PDF preview";
      iframe.addEventListener("load", function () {
        clearTimeout(frameTimer);
        setFrameState("ready");
      });
      //some viewers never report load; show whatever rendered after a while
      frameTimer = setTimeout(function () { setFrameState("ready"); }, 10000);
      iframe.src = src + "#navpanes=0&view=FitH"; //viewer hints only; ignored where unsupported
      frame.appendChild(iframe);
    }).catch(function () {
      setFrameState("error");
    });
  }
  $("retryPreview").addEventListener("click", loadPreview);

  // ───── downloads (PDF / Word) ─────
  var downloadStatus = $("downloadStatus");
  var busyKinds = {};
  function setDownloadStatus(text, isError) {
    downloadStatus.textContent = text;
    downloadStatus.classList.toggle("is-error", !!isError);
  }
  function setDownloadBusy(kind, busy) {
    busyKinds[kind] = busy;
    document.querySelectorAll('a[data-download="' + kind + '"]').forEach(function (link) {
      link.classList.toggle("is-busy", busy);
      if (busy) link.setAttribute("aria-disabled", "true");
      else link.removeAttribute("aria-disabled");
    });
  }
  function saveBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.hidden = true;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(url);
      a.parentNode.removeChild(a);
    }, 30000);
  }
  function download(link) {
    var kind = link.dataset.download;
    var copy = DOWNLOADS[kind];
    if (busyKinds[kind]) return; //one conversion at a time
    setDownloadBusy(kind, true);
    setDownloadStatus(copy.busy, false);
    fetch(link.getAttribute("href"), {cache: "no-store", credentials: "same-origin"}).then(function (response) {
      if (!response.ok) throw new Error("download failed");
      var name = filenameFrom(response.headers.get("Content-Disposition")) || copy.name;
      return response.blob().then(function (blob) { saveBlob(blob, name); });
    }).then(function () {
      setDownloadStatus(copy.done, false);
    }).catch(function () {
      setDownloadStatus(copy.failed, true);
    }).then(function () {
      setDownloadBusy(kind, false);
    });
  }
  if (window.fetch && window.URL && URL.createObjectURL) {
    //without these the links still work as plain downloads
    document.querySelectorAll("a[data-download]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        download(link);
      });
    });
  }

  // ───── chat ─────
  var form = $("chat-question");
  var input = $("question");
  var sendButton = $("askButton");
  var log = $("chatMessages");
  var scroller = $("chatScroll");
  var empty = ws.querySelector(".chat-empty");
  var fieldError = $("questionError");
  var chatStatus = $("chatStatus");
  var transcript = ws.querySelector(".chat-download-button");
  var busy = false;

  function chatUnavailable() {
    return ws.dataset.chatState === "unavailable";
  }
  function updateEmpty() {
    empty.hidden = log.children.length > 0 || chatUnavailable();
  }
  function applyChatState() {
    var off = chatUnavailable();
    form.hidden = off;
    $("chatUnavailable").hidden = !off;
    scroller.hidden = off && log.children.length === 0;
    updateEmpty();
  }
  function scrollToBottom() {
    scroller.scrollTop = scroller.scrollHeight;
  }
  //long answers are read from their first line
  function revealMessage(message) {
    scroller.scrollTop = Math.max(0, message.offsetTop - 12);
  }

  function appendMessage(kind, text) {
    var message = document.createElement("div");
    message.className = "chat-msg " + (kind === "user" ? "chat-msg--user" : "chat-msg--bear")
      + (kind === "error" ? " chat-msg--error" : "");
    var who = document.createElement("p");
    who.className = "chat-msg__who";
    who.textContent = kind === "user" ? "You" : "Bear";
    var body = document.createElement("p");
    body.className = "chat-msg__text";
    body.textContent = text;
    message.appendChild(who);
    message.appendChild(body);
    log.appendChild(message);
    updateEmpty();
    return message;
  }
  function appendPending() {
    var message = appendMessage("bear", "Reading the transcript");
    message.classList.add("chat-msg--pending");
    message.setAttribute("aria-hidden", "true");
    var dots = document.createElement("span");
    dots.className = "chat-dots";
    dots.innerHTML = "<span></span><span></span><span></span>";
    message.lastChild.appendChild(dots);
    return message;
  }

  function loadChat() {
    applyChatState();
    log.setAttribute("aria-busy", "true");
    fetch("chat", {cache: "no-store", credentials: "same-origin"}).then(function (response) {
      if (!response.ok) throw new Error("history unavailable");
      return response.text();
    }).then(function (html) {
      log.innerHTML = html; //escaped by the chat_message.html template
      if (log.children.length) transcript.hidden = false;
      applyChatState();
      scrollToBottom();
    }).catch(function () {
      //no history to restore; the conversation simply starts empty
    }).then(function () {
      log.removeAttribute("aria-busy");
    });
  }

  function showFieldError(text) {
    fieldError.textContent = text;
    fieldError.hidden = false;
    input.setAttribute("aria-invalid", "true");
  }
  function clearFieldError() {
    fieldError.textContent = "";
    fieldError.hidden = true;
    input.removeAttribute("aria-invalid");
  }
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight + 2, 160) + "px";
  }
  function setBusy(value) {
    busy = value;
    form.setAttribute("aria-busy", String(value));
    sendButton.classList.toggle("is-busy", value);
    if (value) sendButton.setAttribute("aria-disabled", "true");
    else sendButton.removeAttribute("aria-disabled");
  }

  function fail(error, question) {
    if (error.unavailable) {
      ws.dataset.chatState = "unavailable";
      applyChatState();
      chatStatus.textContent = error.text;
      $("chatUnavailableTitle").focus();
      return;
    }
    appendMessage("error", error.text);
    if (!input.value) {
      input.value = question; //one Enter to retry
      autosize();
    }
    if (!coarsePointer()) input.focus();
  }

  function ask(question) {
    var data = new FormData(form); //csrfmiddlewaretoken + question
    data.set("question", question);
    clearFieldError();
    revealMessage(appendMessage("user", question));
    input.value = "";
    autosize();
    setBusy(true);
    chatStatus.textContent = "Bear is answering…";
    var pending = appendPending();
    scrollToBottom();
    if (coarsePointer()) input.blur(); //let the phone keyboard close so the answer is visible

    fetch("ask", {method: "POST", body: data, credentials: "same-origin"}).then(function (response) {
      return response.text().then(function (text) {
        return {ok: response.ok, status: response.status, text: text};
      });
    }).then(function (result) {
      pending.parentNode.removeChild(pending);
      chatStatus.textContent = "";
      if (result.ok) {
        revealMessage(appendMessage("bear", result.text));
        transcript.hidden = false;
      } else {
        fail(chatErrorFor(result.status), question);
      }
    }).catch(function () {
      if (pending.parentNode) pending.parentNode.removeChild(pending);
      chatStatus.textContent = "";
      fail(chatErrorFor(0), question);
    }).then(function () {
      setBusy(false);
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy) {
      chatStatus.textContent = "Bear is still answering your last question.";
      return;
    }
    var question = normalizeQuestion(input.value);
    if (!question) {
      showFieldError("Type a question first.");
      input.focus();
      return;
    }
    ask(question);
  });
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", {cancelable: true}));
    }
  });
  input.addEventListener("input", function () {
    if (!fieldError.hidden && normalizeQuestion(input.value)) clearFieldError();
    autosize();
  });

  //Back from another page can restore a stale workspace from the bfcache
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) window.location.reload();
  });

  return {loadPreview: loadPreview, loadChat: loadChat, setView: setView};
})();

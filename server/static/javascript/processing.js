// Processing view on /output (BEAR-V2-B2).
//
// Polls out/verify once a second (200 + status text = still running; any
// other status = stop) and reads the additive X-Job-State / X-Job-Reason
// headers to tell ready, failed, stalled and "nothing to show" apart.
// The worker's status messages are mapped onto five presentation stages.
// Only numbers the server reports are shown; nothing is estimated, and the
// server's text itself is never written into the page.

var STAGES = ["received", "extracting", "indexing", "summarizing", "building", "finished"];

//interprets one status message from summarizer.py; unknown text maps to
//{stage: null} so the page keeps showing what it had
function mapStatus(text) {
  var m;
  text = String(text || "").trim();
  if (text === "" || /^Working/.test(text)) {
    return {stage: "received"};
  }
  m = text.match(/^Extracting text\D*?(\d+)\s*%\s*\((\d+)\/(\d+)\)/);
  if (m) {
    return {stage: "extracting", percent: +m[1], current: +m[2], total: +m[3]};
  }
  if (/^Extracting text/.test(text)) {
    return {stage: "extracting"};
  }
  if (/^Configuring chatbot/.test(text)) {
    return {stage: "indexing"};
  }
  m = text.match(/^(\d+)\/(\d+) pages processed/);
  if (m) {
    return {stage: "summarizing", current: +m[1], total: +m[2]};
  }
  m = text.match(/^(?:EN|ES) page \d+: retry (\d+)\/(\d+)/);
  if (m) {
    return {stage: "summarizing", retry: +m[1], attempts: +m[2]};
  }
  if (/^Building PDF summary/.test(text)) {
    return {stage: "building"};
  }
  if (/^Finished/.test(text)) {
    return {stage: "finished"};
  }
  return {stage: null};
}

//folds a mapped status into the current view: stages never move backwards,
//and a retry notice keeps the page counts it interrupts
function mergeStatus(view, status) {
  if (!status.stage) return view;
  var from = STAGES.indexOf(view.stage);
  var to = STAGES.indexOf(status.stage);
  if (to < from) return view;
  if (status.retry && to === from) {
    return Object.assign({}, view, {retry: status.retry, attempts: status.attempts});
  }
  return Object.assign({}, status);
}

//wording and bar value for a view; percent null = indeterminate bar
function describe(view) {
  var hasCount = view.total > 0 && view.current > 0;
  switch (view.stage) {
    case "extracting":
      return hasCount ? {
        line: "Extracting text · page " + view.current + " of " + view.total,
        detail: "Reading the text on each page of the PDF.",
        percent: Math.min(100, view.percent),
        valueText: view.percent + "% of pages read"
      } : {
        line: "Extracting text",
        detail: "Reading the text on each page of the PDF.",
        percent: null
      };
    case "indexing":
      return {
        line: "Preparing document search",
        detail: "Indexing the text so the chat can answer questions about it.",
        percent: null
      };
    case "summarizing":
      var detail = view.retry
        ? "The AI service is responding slowly. Retrying (attempt " + view.retry + " of " + view.attempts + ")…"
        : "Only pages with enough readable text are counted.";
      return hasCount ? {
        line: "Summarizing page " + view.current + " of " + view.total,
        detail: detail,
        percent: Math.round((view.current - 1) / view.total * 100),
        valueText: (view.current - 1) + " of " + view.total + " pages summarized"
      } : {
        line: "Summarizing testimony",
        detail: detail,
        percent: null
      };
    case "building":
      return {
        line: "Building the summary PDF",
        detail: "Putting the page summaries together.",
        percent: null
      };
    case "finished":
      return {line: "Summary ready", detail: "Opening your summary…", percent: 100, valueText: "Complete"};
    default:
      return {line: "Upload received", detail: "Waiting for processing to start…", percent: null};
  }
}

if (typeof module === "object" && module.exports) {
  module.exports = {STAGES: STAGES, mapStatus: mapStatus, mergeStatus: mergeStatus, describe: describe};
}

if (typeof document !== "undefined") (function () {
  var POLL_MS = 1000;
  var MAX_BACKOFF_MS = 15000;
  var root = document.getElementById("loading");
  if (!root) return;

  var view = {stage: "received"};
  var announcedStage = null;
  var failures = 0;
  var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $(id) { return document.getElementById(id); }

  function announce(text) {
    $("processingAnnouncer").textContent = text;
  }

  function renderStages() {
    var current = STAGES.indexOf(view.stage);
    root.querySelectorAll(".stage").forEach(function (item, index) {
      var status = index < current || view.stage === "finished" ? "done"
        : index === current ? "current" : "pending";
      item.dataset.status = status;
      item.querySelector(".stage__state").textContent =
        status === "done" ? "completed" : status === "current" ? "in progress" : "not started";
      if (status === "current") item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
  }

  function render() {
    var text = describe(view);
    var bar = $("progressBar");
    $("status_msg").textContent = text.line;
    $("progressDetail").textContent = text.detail;
    if (text.percent === null) {
      bar.dataset.mode = "indeterminate";
      bar.removeAttribute("aria-valuenow");
      bar.removeAttribute("aria-valuemin");
      bar.removeAttribute("aria-valuemax");
      bar.removeAttribute("aria-valuetext");
      bar.style.removeProperty("--progress");
    } else {
      bar.dataset.mode = "determinate";
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", "100");
      bar.setAttribute("aria-valuenow", String(text.percent));
      bar.setAttribute("aria-valuetext", text.valueText);
      bar.style.setProperty("--progress", text.percent + "%");
    }
    renderStages();
    root.dataset.stage = view.stage;

    //announce stage changes only, not every page tick
    if (view.stage !== announcedStage) {
      announcedStage = view.stage;
      var step = Math.min(STAGES.indexOf(view.stage), 4) + 1;
      announce(view.stage === "finished" ? "Your summary is ready."
        : "Step " + step + " of 5: " + root.querySelectorAll(".stage__label")[step - 1].textContent + ".");
    }
  }

  function setStalled(stalled) {
    var notice = $("stalledNotice");
    if (stalled && notice.hidden) {
      announce("This summary stopped responding. You can cancel it and start over.");
    }
    notice.hidden = !stalled;
    root.dataset.state = stalled ? "stalled" : "running";
  }

  function showPanel(name, reason) {
    root.dataset.state = name;
    root.querySelectorAll("[data-panel]").forEach(function (panel) {
      panel.hidden = panel.dataset.panel !== name;
    });
    if (name === "failed") {
      root.querySelectorAll("[data-reason]").forEach(function (p) {
        p.hidden = p.dataset.reason !== (reason === "no-text" ? "no-text" : "error");
      });
    }
    var heading = root.querySelector('[data-panel="' + name + '"] h1');
    if (heading) heading.focus();
  }

  //a job that finishes while this page is open: reload so the server renders
  //the completed workspace (chat availability, page count, chat history),
  //which then opens through insertIframe() like any ready page (B3)
  function openCompletedWorkspace() {
    window.location.replace(window.location.pathname);
  }

  //hands over to the summary workspace (output.js)
  function showWorkspace() {
    var hadFocus = root.contains(document.activeElement);
    insertIframe();
    if (hadFocus) $("main-content").focus({preventScroll: true});
  }

  function finish(state, reason) {
    if (state === "ready") {
      view = {stage: "finished"};
      render();
      setTimeout(openCompletedWorkspace, reducedMotion ? 0 : 700);
    } else if (state === "failed") {
      showPanel("failed", reason);
    } else if (state === "none") {
      showPanel("none");
    } else {
      showWorkspace(); //no X-Job-State: previous behavior
    }
  }

  function schedule(delay) {
    setTimeout(poll, delay);
  }

  function poll() {
    fetch("out/verify", {cache: "no-store", credentials: "same-origin"}).then(function (response) {
      failures = 0;
      $("connectionNote").hidden = true;
      var state = response.headers.get("X-Job-State");
      if (response.status === 200) {
        return response.text().then(function (text) {
          setStalled(state === "stalled");
          view = mergeStatus(view, mapStatus(text));
          render();
          schedule(POLL_MS);
        });
      }
      finish(state, response.headers.get("X-Job-Reason"));
    }).catch(function () {
      failures += 1;
      $("connectionNote").hidden = false;
      schedule(Math.min(POLL_MS * Math.pow(2, failures), MAX_BACKOFF_MS));
    });
  }

  var initial = root.dataset.state;
  if (initial === "ready") {
    insertIframe();
  } else if (initial === "running" || initial === "stalled") {
    render();
    poll();
  }
  //failed and none are rendered by the server; nothing to poll
})();

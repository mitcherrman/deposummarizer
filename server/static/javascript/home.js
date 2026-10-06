// Upload form on /home (BEAR-V2-B2): PDF picker / drop zone, summary focus
// topics, client-side checks and the submit busy state. The form still posts
// file, lang, filterType and repeated filterText fields to /summarize; the
// server stays authoritative and nothing is uploaded before submission.
// Loaded once, at the end of the page, so the DOM below already exists.

var form = document.getElementById("form");
var fileInput = document.getElementById("fileInput");
var dropzone = document.getElementById("dropzone");
var topicEntry = document.getElementById("topicEntry");
var topicList = document.getElementById("topicList");
var submitting = false;

function formatBytes(bytes) {
  if (!(bytes >= 0)) return "";
  if (bytes < 1024) return bytes + " bytes";
  if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function isPdf(file) {
  return file.type === "application/pdf" || /\.pdf$/i.test(file.name);
}

function setError(id, input, text) {
  document.getElementById(id).textContent = text || "";
  if (input) {
    if (text) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }
}

/* ---------- PDF picker ---------- */

//returns an error message for the selected file, or "" if it can be submitted
function fileProblem(file) {
  if (!file) return "Choose a PDF to summarize.";
  if (!isPdf(file)) return "“" + file.name + "” isn't a PDF. Choose a PDF file.";
  if (file.size === 0) return "“" + file.name + "” is empty. Choose another PDF.";
  return "";
}

function showSelectedFile() {
  var file = fileInput.files && fileInput.files[0];
  var empty = dropzone.querySelector(".dropzone__empty");
  var chosen = dropzone.querySelector(".dropzone__file");
  var status = document.getElementById("fileStatus");

  if (file && fileProblem(file)) {
    setError("fileError", fileInput, fileProblem(file));
    fileInput.value = "";
    file = null;
  } else {
    setError("fileError", fileInput, "");
  }

  if (file) {
    document.getElementById("fileName").textContent = file.name;
    document.getElementById("fileSize").textContent = formatBytes(file.size);
    status.textContent = "Selected " + file.name + ", " + formatBytes(file.size) + ".";
    dropzone.dataset.state = "selected";
  } else {
    status.textContent = "";
    dropzone.dataset.state = "empty";
  }
  empty.hidden = !!file;
  chosen.hidden = !file;
}

function hasFiles(event) {
  var types = event.dataTransfer && event.dataTransfer.types;
  return !!types && Array.prototype.indexOf.call(types, "Files") >= 0;
}

/* ---------- summary focus topics ---------- */

function topicMode() {
  var checked = form.querySelector(".filter-type:checked");
  return checked ? checked.value : "none";
}

function topicValues() {
  return Array.prototype.map.call(topicList.querySelectorAll(".filter-text"), function (input) {
    return input.value;
  });
}

function announceTopics(text) {
  document.getElementById("topicStatus").textContent = text;
}

//shows the topic entry for include/exclude; topics are kept but not submitted for "none"
function updateTopicPanel() {
  var mode = topicMode();
  var panel = document.getElementById("topicPanel");
  panel.hidden = mode === "none";
  document.getElementById("topicLabel").textContent =
    mode === "exclude" ? "Topics to exclude" : "Topics to include";
  topicList.querySelectorAll(".filter-text").forEach(function (input) {
    input.disabled = mode === "none";
  });
  if (mode === "none") setError("topicError", topicEntry, "");
}

function removeTopic(chip) {
  var chips = Array.prototype.slice.call(topicList.children);
  var index = chips.indexOf(chip);
  var text = chip.querySelector(".filter-text").value;
  chip.remove();
  topicList.hidden = topicList.children.length === 0;
  //keep focus in the list: next chip, previous chip, then the entry box
  var next = topicList.children[index] || topicList.children[index - 1];
  (next ? next.querySelector("button") : topicEntry).focus();
  announceTopics("Removed " + text + ".");
}

function createChip(text) {
  var chip = document.createElement("li");
  chip.className = "chip";

  var label = document.createElement("span");
  label.className = "chip__text";
  label.textContent = text;

  //the submitted value: one filterText field per topic, in order
  var value = document.createElement("input");
  value.type = "hidden";
  value.className = "filter-text";
  value.name = "filterText";
  value.value = text;
  value.disabled = topicMode() === "none";

  var remove = document.createElement("button");
  remove.type = "button";
  remove.className = "chip__remove";
  remove.setAttribute("aria-label", "Remove topic: " + text);
  remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M7 7l10 10M17 7L7 17"/></svg>';
  remove.addEventListener("click", function () {
    removeTopic(chip);
  });

  chip.append(label, value, remove);
  return chip;
}

//adds the text in the topic box as a chip; returns false if it was rejected
function addFilterKeyword() {
  var text = topicEntry.value.replace(/\s+/g, " ").trim();
  if (!text) {
    setError("topicError", topicEntry, "Type a topic first.");
    topicEntry.focus();
    return false;
  }
  //mirrors the server, which keeps only these characters
  if (!/[a-zA-Z0-9]/.test(text.replace(/[^a-zA-Z0-9- ]/g, ""))) {
    setError("topicError", topicEntry, "Use letters A–Z or numbers in the topic.");
    topicEntry.focus();
    return false;
  }
  var duplicate = topicValues().some(function (value) {
    return value.toLowerCase() === text.toLowerCase();
  });
  if (!duplicate) {
    topicList.append(createChip(text));
    topicList.hidden = false;
    var count = topicList.children.length;
    announceTopics("Added " + text + ". " + count + (count === 1 ? " topic." : " topics."));
  }
  setError("topicError", topicEntry, "");
  topicEntry.value = "";
  topicEntry.focus();
  return true;
}

/* ---------- submit ---------- */

function setBusy(busy) {
  var button = document.getElementById("btnClicked");
  button.disabled = busy;
  button.classList.toggle("is-busy", busy);
  button.querySelector(".upload-submit__label").textContent =
    busy ? "Uploading…" : "Summarize deposition";
  form.setAttribute("aria-busy", busy ? "true" : "false");
  document.getElementById("submitStatus").textContent = busy
    ? "Uploading your PDF. Keep this page open; it will move on when the upload finishes."
    : "Long transcripts can take several minutes. You can follow the progress on the next page.";
}

//client-side checks only; returns true if the form can be submitted
function validateForm() {
  var firstInvalid = null;

  var problem = fileProblem(fileInput.files && fileInput.files[0]);
  setError("fileError", fileInput, problem);
  if (problem) firstInvalid = fileInput;

  if (topicMode() !== "none") {
    //a topic typed but not yet added still counts
    if (topicEntry.value.trim() && !addFilterKeyword()) {
      firstInvalid = firstInvalid || topicEntry;
    } else if (topicValues().length === 0) {
      setError("topicError", topicEntry, "Add at least one topic, or choose Full deposition.");
      firstInvalid = firstInvalid || topicEntry;
    }
  }

  if (firstInvalid) {
    firstInvalid.focus();
    return false;
  }
  return true;
}

form.addEventListener("submit", function (event) {
  //one upload per page load: a double-click must not post twice
  if (submitting || !validateForm()) {
    event.preventDefault();
    return;
  }
  submitting = true;
  setBusy(true);
});

/* ---------- wiring ---------- */

fileInput.addEventListener("change", showSelectedFile);

dropzone.addEventListener("dragenter", function (event) {
  if (hasFiles(event)) dropzone.classList.add("is-dragover");
});
dropzone.addEventListener("dragover", function (event) {
  if (hasFiles(event)) dropzone.classList.add("is-dragover");
});
dropzone.addEventListener("dragleave", function (event) {
  if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("is-dragover");
});
dropzone.addEventListener("drop", function () {
  dropzone.classList.remove("is-dragover"); //the file input receives the file natively
});

//a file dropped next to the drop zone would otherwise open in this tab
["dragover", "drop"].forEach(function (type) {
  window.addEventListener(type, function (event) {
    if (event.target !== fileInput && hasFiles(event)) {
      event.preventDefault();
      if (type === "drop") dropzone.classList.remove("is-dragover");
    }
  });
});

form.querySelectorAll(".filter-type").forEach(function (radio) {
  radio.addEventListener("change", updateTopicPanel);
});

topicEntry.addEventListener("keydown", function (event) {
  if (event.key === "Enter") {
    event.preventDefault(); //Enter adds a topic instead of submitting the form
    addFilterKeyword();
  }
});

//returning with Back can restore this page with the button still busy and a
//stale job card; reload to show the session's current state
addEventListener("pageshow", function (event) {
  if (event.persisted) {
    window.location.reload();
  }
});

//restore UI for values the browser kept (e.g. a reload with a chosen file)
showSelectedFile();
updateTopicPanel();

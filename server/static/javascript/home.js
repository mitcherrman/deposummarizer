//called when summarize button is pressed
function validateForm() {
  var fileInput = document.getElementById('fileInput');
  if (!fileInput.value) {
    return false;
  }

  // Show the loading message and disable the submit button
  document.getElementById('btnClicked').setAttribute('disabled', 'disabled');
  document.getElementById('loading').style.display = 'block';

  // Submit the form
  return true;
}

//checks if summary already exists, called when page loaded
function checkForSummary() {
  let currUrl = window.location.href;
  fetch(currUrl.substring(0,currUrl.length-4) + "out", {
    method: "HEAD"
  }).then((response) => {
    if (response.status == 200) {
      document.querySelector(".output-url").removeAttribute("hidden");
    }
  });
}

function addFilterKeyword() {
  // Create a new filter text input
  let existingFilterText = document.querySelector(".filter-text");
  let newFilterText = document.createElement("input");
  newFilterText.type = "text";
  newFilterText.className = "filter-text";
  newFilterText.name = "filterText";
  newFilterText.placeholder = "Enter keyword, e.g. \"accident details\"";
  if (existingFilterText.hasAttribute("disabled")) {
    newFilterText.setAttribute("disabled", "disabled");
  }
  // Create a line break
  let br = document.createElement("br");
  // Insert the new filter text box after the existing one
  existingFilterText.parentNode.insertBefore(br, existingFilterText.nextSibling);
  existingFilterText.parentNode.insertBefore(newFilterText, br.nextSibling);
  document.querySelector("#removeFilter").removeAttribute("disabled");
}

function removeFilterKeyword() {
  let filters = document.querySelectorAll(".filter-text");
  let filter = filters[filters.length - 1];
  if (filter) {
    let br = filter.previousSibling;
    if (br) {
      br.remove();
    }
    filter.remove();
  }
  if (document.querySelectorAll(".filter-text").length == 1) {
    document.querySelector("#removeFilter").setAttribute("disabled", "disabled");
  }
}

addEventListener("load", checkForSummary());

//filter box enable/disable
document.querySelectorAll(".filter-type").forEach(filter => {
  filter.addEventListener("change", () => {
    if (filter.classList.contains("filter-disable")) {
      document.querySelectorAll(".filter-text").forEach(element => {
        element.setAttribute("disabled", "disabled");
        element.value = "";
      });
    } else {
      document.querySelectorAll(".filter-text").forEach(element => {
        element.removeAttribute("disabled");
        element.value = "";
      });
    }
  });
});
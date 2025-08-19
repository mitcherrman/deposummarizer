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

addEventListener("load", checkForSummary());

//filter box enable/disable
document.querySelectorAll(".filter-type").forEach(filter => {
  console.log("add");
  filter.addEventListener("change", () => {
    console.log("change");
    if (filter.classList.contains("filter-disable")) {
      document.querySelector(".filter-text").setAttribute("disabled", "disabled");
      document.querySelector(".filter-text").value = "";
    } else {
      document.querySelector(".filter-text").removeAttribute("disabled");
      document.querySelector(".filter-text").value = "";
    }
  });
});
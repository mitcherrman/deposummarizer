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
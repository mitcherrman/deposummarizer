checkSummaryIntervalId = 0

function validateForm() {
    var fileInput = document.getElementById('fileInput');
    if (!fileInput.value) {
      return false;
    }

    // Show the loading message and disable the submit button
    document.getElementById('btnClicked').setAttribute('disabled', 'disabled');
    document.getElementById('loading').style.display = 'block';

    checkSummaryIntervalId = setInterval(checkSummary, 1000);

    // Submit the form
    return true;
}

function checkSummary() {
  currUrl = window.location.href;
  response = fetch(currUrl.substring(0,currUrl.length - 4) + "out/verify");
  response.then((response) => {
    if (response.status != 200) {
      clearInterval(checkSummaryIntervalId);
      window.location.href = currUrl.substring(0,currUrl.length - 4) + "output";
    }
  });
}
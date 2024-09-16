checkSummaryIntervalId = 0

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
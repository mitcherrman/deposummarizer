function validateForm() {
    var fileInput = document.getElementById('fileInput');
    if (!fileInput.value) {
      alert("Please select a file to upload.");
      return false;
    }

    // Show the loading message and disable the submit button
    document.getElementById('btnClicked').setAttribute('disabled', 'disabled');
    document.getElementById('loading').style.display = 'block';
    
    // Submit the form
    return true;
}
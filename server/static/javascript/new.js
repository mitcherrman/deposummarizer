function updateCreateButton() {
    let createButton = document.getElementById("create-btn");
    let inputs = document.querySelectorAll(".form-control");
    let warningBox = document.getElementById("warning-box");
    let username = inputs[0].value;
    let password1 = inputs[1].value;
    let password2 = inputs[2].value;
    if (username == "" || password1 == "") {
        createButton.setAttribute("disabled", "");
        warningBox.innerText = "All fields must be filled.";
        warningBox.removeAttribute("hidden");
    } else if (password1 != password2) {
        createButton.setAttribute("disabled", "");
        warningBox.innerText = "Passwords do not match.";
        warningBox.removeAttribute("hidden");
    } else {
        createButton.removeAttribute("disabled");
        warningBox.setAttribute("hidden","");
    }
}

addEventListener("input", (event) => {
    updateCreateButton();
})
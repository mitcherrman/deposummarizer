checkSummaryIntervalId = 0

function startCheckThread() {
    checkSummaryIntervalId = setInterval(checkSummary, 1000);
}

function checkSummary() {
    currUrl = window.location.href;
    response = fetch(currUrl.substring(0,currUrl.length - 3) + "/verify");
    response.then((response) => {
      if (response.status != 200) {
        clearInterval(checkSummaryIntervalId);
        load = document.getElementById("loading");
        parent = load.parentElement;
        frame = document.createElement("iframe");
        frame.setAttribute("src", "/out");
        frame.style.setProperty('width', "100%");
        frame.style.setProperty("aspect-ratio", "3 / 2");
        parent.replaceChild(frame, load);
      }
    });
  }
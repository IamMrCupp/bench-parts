// bench-parts — the only script in the app, and it only does one thing.
//
// Forms marked with data-confirm ask before submitting. Kept out of the
// templates so the Content-Security-Policy can say `script-src 'self'` with
// no inline script at all; that one line is what makes an injected <script>
// in, say, a part description inert.
document.addEventListener("submit", function (event) {
  var form = event.target;
  var message = form.getAttribute("data-confirm");
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

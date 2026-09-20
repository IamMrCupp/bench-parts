// bench-parts — the only script in the app, and it does two small things.
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

// Buttons marked with data-print open the print dialog (the label page).
// Same reason it lives here and not in an onclick: `script-src 'self'` bans
// inline handlers, and that ban is worth more than the convenience.
document.addEventListener("click", function (event) {
  var el = event.target.closest ? event.target.closest("[data-print]") : null;
  if (el) {
    window.print();
  }
});

// Stops a form being sent by the browser itself before the page's own code is ready.
//
// A form with no script behind it is sent by the browser as an ordinary web address, which would put
// what the person typed (an email and a password) into the address bar, the browser history and the
// server's logs. The page's code takes over a moment after the page appears, and on a slow connection
// that moment can be long. This tiny classic script loads first, before the form is even drawn, and
// refuses to let a guarded form go anywhere until the page's code has taken it over (ui.js bindForm
// marks it ready). If someone presses the button early, nothing is sent: they are told to wait a
// moment, and the form is sent for them the instant the page is ready.
//
// A guarded form is any <form data-guard>. It needs no change to the page's own code.
(function () {
  "use strict";
  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      if (!form || !form.hasAttribute || !form.hasAttribute("data-guard") || form.dataset.ready) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      form.dataset.pending = "1";
      var box = document.getElementById("message");
      if (box) {
        box.className = "message";
        box.textContent = "One moment, the page is still loading. Nothing has been sent yet; it will go as soon as the page is ready.";
        box.hidden = false;
      }
    },
    true
  );
})();

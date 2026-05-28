// Rewrite the server-rendered IST article dateline to the viewer's local
// timezone. The <time datetime="..."> attribute carries the UTC instant; the
// visible text is the IST fallback for no-JS / Intl-less clients, which this
// leaves untouched if anything fails.
(function () {
  function localize(el) {
    var iso = el.getAttribute("datetime");
    if (!iso) return;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return;
    try {
      var parts = new Intl.DateTimeFormat(undefined, {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
        timeZoneName: "short"
      }).formatToParts(d);
      var p = {};
      for (var i = 0; i < parts.length; i++) p[parts[i].type] = parts[i].value;
      if (!p.day || !p.hour) return;
      var out =
        p.day + " " + p.month + " " + p.year + ", " +
        p.hour + ":" + p.minute + " " + (p.dayPeriod || "") + " " +
        (p.timeZoneName || "");
      el.textContent = out.replace(/\s+/g, " ").trim();
    } catch (e) {
      // Intl unavailable — keep the server-rendered IST text.
    }
  }
  var els = document.querySelectorAll("time.article__kicker-time[datetime]");
  for (var i = 0; i < els.length; i++) localize(els[i]);
})();

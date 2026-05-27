(function () {
  "use strict";

  function fmtAgo(iso) {
    if (!iso) return "";
    const then = new Date(iso);
    if (isNaN(then.getTime())) return "";
    const diffSec = Math.max(0, (Date.now() - then.getTime()) / 1000);
    if (diffSec < 60) return Math.floor(diffSec) + "s ago";
    if (diffSec < 3600) return Math.floor(diffSec / 60) + "m ago";
    if (diffSec < 86400) return Math.floor(diffSec / 3600) + "h ago";
    return Math.floor(diffSec / 86400) + "d ago";
  }

  function direction(curr, prev) {
    if (prev == null) return { sym: "•", word: "first sample" };
    const delta = curr - prev;
    const pct = Math.abs(prev) > 1e-9 ? Math.abs(delta) / Math.abs(prev) : Math.abs(delta);
    if (pct < 0.01) return { sym: "▬", word: "steady" };
    return delta > 0 ? { sym: "▲", word: "rising" } : { sym: "▼", word: "falling" };
  }

  let tip = null;

  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement("div");
    tip.className = "sparkline-tooltip";
    tip.style.position = "fixed";
    tip.style.pointerEvents = "none";
    tip.style.display = "none";
    tip.style.zIndex = "1000";
    document.body.appendChild(tip);
    return tip;
  }

  function showTip(circle, ev) {
    const svg = circle.ownerSVGElement;
    if (!svg) return;
    const pts = Array.from(svg.querySelectorAll(".sparkline__pt"));
    const idx = pts.indexOf(circle);
    const score = parseFloat(circle.getAttribute("data-score"));
    const ts = circle.getAttribute("data-ts");
    const prevScore = idx > 0 ? parseFloat(pts[idx - 1].getAttribute("data-score")) : null;
    const dir = direction(score, prevScore);
    const ago = fmtAgo(ts);

    const t = ensureTip();
    t.innerHTML =
      '<div class="sparkline-tooltip__row"><span class="sparkline-tooltip__k">Score</span>' +
      '<span class="sparkline-tooltip__v">' + score.toFixed(3) + "</span></div>" +
      (ago ? '<div class="sparkline-tooltip__row"><span class="sparkline-tooltip__k">When</span>' +
        '<span class="sparkline-tooltip__v">' + ago + "</span></div>" : "") +
      '<div class="sparkline-tooltip__row sparkline-tooltip__dir"><span>' + dir.sym + "</span>" +
      "<span>" + dir.word + "</span></div>";

    t.style.display = "block";
    const rect = circle.getBoundingClientRect();
    const tipRect = t.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    let top = rect.top - tipRect.height - 8;
    if (top < 4) top = rect.bottom + 8;
    if (left < 4) left = 4;
    const maxLeft = window.innerWidth - tipRect.width - 4;
    if (left > maxLeft) left = maxLeft;
    t.style.left = left + "px";
    t.style.top = top + "px";
  }

  function hideTip() {
    if (tip) tip.style.display = "none";
  }

  function attach() {
    const circles = document.querySelectorAll(".sparkline__pt");
    circles.forEach(function (c) {
      c.addEventListener("mouseenter", function (ev) { showTip(c, ev); });
      c.addEventListener("mouseleave", hideTip);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();

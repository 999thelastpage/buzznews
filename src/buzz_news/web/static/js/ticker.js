document.addEventListener("DOMContentLoaded", () => {
  const items = document.querySelectorAll(".headline-bar__item");
  if (items.length <= 1) return;
  let cur = 0;
  setInterval(() => {
    items[cur].classList.remove("is-active");
    cur = (cur + 1) % items.length;
    items[cur].classList.add("is-active");
  }, 5000);
});

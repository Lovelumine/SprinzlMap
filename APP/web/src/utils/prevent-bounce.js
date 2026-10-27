let startY = 0;

document.addEventListener(
  "touchstart",
  (e) => {
    startY = e.touches[0].pageY;
  },
  { passive: true }
);

document.addEventListener(
  "touchmove",
  (e) => {
    const currentY = e.touches[0].pageY;
    const atTop = window.scrollY === 0 && currentY > startY;
    const atBottom =
      window.scrollY + window.innerHeight >= document.body.scrollHeight &&
      currentY < startY;

    if (atTop || atBottom) {
      e.preventDefault(); // 阻止弹性拉动
    }
  },
  { passive: false }
);

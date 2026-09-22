// Full-screen zoomable image viewer, used by the DXF overlay gallery (and
// any other result image marked with data-lightbox-src). Event delegation
// on document means renderDxfOutputs (render-helpers.js) doesn't need to
// wire up click handlers itself -- it just stamps the data attributes.
const imageLightboxOverlay = document.getElementById("image-lightbox-overlay");
const imageLightboxImg = document.getElementById("image-lightbox-img");
const imageLightboxClose = document.getElementById("image-lightbox-close");
const imageLightboxCaption = document.getElementById("image-lightbox-caption");

let lbScale = 1, lbX = 0, lbY = 0, lbDragging = false, lbStartX = 0, lbStartY = 0;

function lbApplyTransform() {
  imageLightboxImg.style.transform = `translate(${lbX}px, ${lbY}px) scale(${lbScale})`;
}

function lbReset() {
  lbScale = 1;
  lbX = 0;
  lbY = 0;
  lbApplyTransform();
  imageLightboxImg.classList.remove("zoomed");
}

function openImageLightbox(url, label) {
  imageLightboxImg.src = url;
  imageLightboxCaption.textContent = label || "";
  lbReset();
  imageLightboxOverlay.classList.remove("hidden");
}

function closeImageLightbox() {
  imageLightboxOverlay.classList.add("hidden");
  imageLightboxImg.src = "";
}

document.addEventListener("click", (e) => {
  const trigger = e.target.closest("[data-lightbox-src]");
  if (trigger) openImageLightbox(trigger.dataset.lightboxSrc, trigger.dataset.lightboxLabel || "");
});

imageLightboxClose.addEventListener("click", closeImageLightbox);
imageLightboxOverlay.addEventListener("click", (e) => {
  if (e.target === imageLightboxOverlay) closeImageLightbox();
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !imageLightboxOverlay.classList.contains("hidden")) closeImageLightbox();
});

imageLightboxImg.addEventListener("wheel", (e) => {
  e.preventDefault();
  const delta = e.deltaY < 0 ? 0.2 : -0.2;
  lbScale = Math.min(6, Math.max(1, lbScale + delta));
  if (lbScale === 1) {
    lbX = 0;
    lbY = 0;
  }
  imageLightboxImg.classList.toggle("zoomed", lbScale > 1);
  lbApplyTransform();
}, { passive: false });

imageLightboxImg.addEventListener("dblclick", () => {
  if (lbScale > 1) {
    lbReset();
  } else {
    lbScale = 2.5;
    imageLightboxImg.classList.add("zoomed");
    lbApplyTransform();
  }
});

imageLightboxImg.addEventListener("mousedown", (e) => {
  if (lbScale <= 1) return;
  lbDragging = true;
  lbStartX = e.clientX - lbX;
  lbStartY = e.clientY - lbY;
  imageLightboxImg.classList.add("dragging");
});
window.addEventListener("mousemove", (e) => {
  if (!lbDragging) return;
  lbX = e.clientX - lbStartX;
  lbY = e.clientY - lbStartY;
  lbApplyTransform();
});
window.addEventListener("mouseup", () => {
  lbDragging = false;
  imageLightboxImg.classList.remove("dragging");
});

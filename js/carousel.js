/* ============================================
  TESTIMONIALS CAROUSEL — NorthStar
   ============================================ */

(function () {
  const track = document.getElementById('testimonials-track');
  if (!track) return;

  const slides = track.querySelectorAll('.testimonial-slide');
  const dots   = document.querySelectorAll('.carousel-dot');
  let current  = 0;
  let autoPlay;
  const total  = slides.length;

  function goTo(index) {
    current = (index + total) % total;
    track.style.transform = `translateX(-${current * 100}%)`;
    dots.forEach((dot, i) => {
      dot.classList.toggle('active', i === current);
      dot.setAttribute('aria-current', i === current ? 'true' : 'false');
    });
    document.getElementById('testimonials-carousel')?.setAttribute('aria-label',
      `Client testimonials carousel, slide ${current + 1} of ${total}`);
  }

  function next() { goTo(current + 1); }
  function prev() { goTo(current - 1); }

  function startAutoPlay() {
    stopAutoPlay();
    autoPlay = setInterval(next, 5000);
  }

  function stopAutoPlay() {
    clearInterval(autoPlay);
  }

  // Dot click
  dots.forEach((dot, i) => {
    dot.addEventListener('click', () => { goTo(i); startAutoPlay(); });
    dot.setAttribute('tabindex', '0');
    dot.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goTo(i); startAutoPlay(); }
    });
  });

  // Keyboard navigation
  const carousel = document.getElementById('testimonials-carousel');
  carousel?.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { prev(); startAutoPlay(); }
    if (e.key === 'ArrowRight') { next(); startAutoPlay(); }
  });

  // Pause on hover / focus
  carousel?.addEventListener('mouseenter', stopAutoPlay);
  carousel?.addEventListener('mouseleave', startAutoPlay);
  carousel?.addEventListener('focusin',  stopAutoPlay);
  carousel?.addEventListener('focusout', startAutoPlay);

  // Touch / swipe support
  let touchStartX = 0;
  carousel?.addEventListener('touchstart', (e) => {
    touchStartX = e.changedTouches[0].screenX;
  }, { passive: true });
  carousel?.addEventListener('touchend', (e) => {
    const diff = touchStartX - e.changedTouches[0].screenX;
    if (Math.abs(diff) > 50) { diff > 0 ? next() : prev(); startAutoPlay(); }
  }, { passive: true });

  // Init
  goTo(0);
  startAutoPlay();

  // Expose for HTML onclick handlers
  window.carouselNext = () => { next(); startAutoPlay(); };
  window.carouselPrev = () => { prev(); startAutoPlay(); };
})();

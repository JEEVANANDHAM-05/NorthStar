/* ============================================
  TESTIMONIALS CAROUSEL — NorthStar
  Refactored to support dynamic DOM rendering
   ============================================ */

window.initTestimonialsCarousel = function () {
  const track = document.getElementById('testimonials-track');
  const carousel = document.getElementById('testimonials-carousel');
  if (!track || !carousel) return;

  const slides = track.querySelectorAll('.testimonial-slide');
  const dots = document.querySelectorAll('.carousel-dot');
  const total = slides.length;
  if (total === 0) return;

  let current = 0;
  
  if (window.carouselAutoPlayInterval) {
    clearInterval(window.carouselAutoPlayInterval);
  }

  function next() { goTo(current + 1); }
  function prev() { goTo(current - 1); }

  function startAutoPlay() {
    stopAutoPlay();
    window.carouselAutoPlayInterval = setInterval(next, 5000);
  }

  function stopAutoPlay() {
    if (window.carouselAutoPlayInterval) {
      clearInterval(window.carouselAutoPlayInterval);
    }
  }

  // Clone and replace dots to clean up old event listeners
  dots.forEach((dot, i) => {
    const newDot = dot.cloneNode(true);
    dot.parentNode.replaceChild(newDot, dot);
    
    newDot.addEventListener('click', () => { goTo(i); startAutoPlay(); });
    newDot.setAttribute('tabindex', '0');
    newDot.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goTo(i); startAutoPlay(); }
    });
  });

  // Re-query the dots list after replacement to keep states correct
  const updatedDots = document.querySelectorAll('.carousel-dot');
  
  function updateDotStates(index) {
    updatedDots.forEach((dot, i) => {
      dot.classList.toggle('active', i === index);
      dot.setAttribute('aria-current', i === index ? 'true' : 'false');
    });
  }

  function goTo(index) {
    current = (index + total) % total;
    track.style.transform = `translateX(-${current * 100}%)`;
    updateDotStates(current);
    carousel.setAttribute('aria-label', `Client testimonials carousel, slide ${current + 1} of ${total}`);
  }

  // Clone and replace next/prev buttons to clean up old event listeners
  const prevBtn = document.getElementById('prev-btn');
  const nextBtn = document.getElementById('next-btn');
  if (prevBtn) {
    const newPrevBtn = prevBtn.cloneNode(true);
    prevBtn.parentNode.replaceChild(newPrevBtn, prevBtn);
    newPrevBtn.addEventListener('click', () => { prev(); startAutoPlay(); });
  }
  if (nextBtn) {
    const newNextBtn = nextBtn.cloneNode(true);
    nextBtn.parentNode.replaceChild(newNextBtn, nextBtn);
    newNextBtn.addEventListener('click', () => { next(); startAutoPlay(); });
  }

  // Setup carousel event listeners once
  if (!carousel.dataset.listenersAttached) {
    carousel.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') {
        window.carouselPrev();
      }
      if (e.key === 'ArrowRight') {
        window.carouselNext();
      }
    });

    carousel.addEventListener('mouseenter', () => window.carouselStopAutoPlay());
    carousel.addEventListener('mouseleave', () => window.carouselStartAutoPlay());
    carousel.addEventListener('focusin', () => window.carouselStopAutoPlay());
    carousel.addEventListener('focusout', () => window.carouselStartAutoPlay());

    let touchStartX = 0;
    carousel.addEventListener('touchstart', (e) => {
      touchStartX = e.changedTouches[0].screenX;
    }, { passive: true });
    carousel.addEventListener('touchend', (e) => {
      const diff = touchStartX - e.changedTouches[0].screenX;
      if (Math.abs(diff) > 50) { 
        diff > 0 ? window.carouselNext() : window.carouselPrev(); 
      }
    }, { passive: true });

    carousel.dataset.listenersAttached = 'true';
  }

  // Expose navigation functions globally
  window.carouselNext = () => { next(); startAutoPlay(); };
  window.carouselPrev = () => { prev(); startAutoPlay(); };
  window.carouselStartAutoPlay = startAutoPlay;
  window.carouselStopAutoPlay = stopAutoPlay;

  // Initial call
  goTo(0);
  startAutoPlay();
};

// Auto-run if elements are statically present (fallback / standard page load)
document.addEventListener('DOMContentLoaded', () => {
  if (document.querySelectorAll('.testimonial-slide').length > 0) {
    window.initTestimonialsCarousel();
  }
});

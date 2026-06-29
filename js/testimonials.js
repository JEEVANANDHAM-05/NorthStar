/* ============================================
  TESTIMONIALS & FEEDBACK FLOW — NorthStar
   ============================================ */

document.addEventListener('DOMContentLoaded', () => {
  const track = document.getElementById('testimonials-track');
  const dotsContainer = document.getElementById('carousel-dots');

  // API Endpoints
  const GET_FEEDBACK_API = '/api/feedback';
  const POST_FEEDBACK_API = '/api/feedback';

  // ─────────────────────────────────────────────
  // 1. Fetch and Render Testimonials
  // ─────────────────────────────────────────────
  async function loadTestimonials() {
    try {
      const response = await fetch(GET_FEEDBACK_API);
      if (!response.ok) {
        throw new Error(`Failed to fetch testimonials: ${response.status}`);
      }
      
      const testimonials = await response.json();
      renderTestimonials(testimonials);
    } catch (error) {
      console.error('Error loading testimonials:', error);
      // Fallback to error message or try to use default mockup
      track.innerHTML = `
        <div style="text-align: center; padding: var(--sp-10) 0; color: var(--error); width: 100%;">
          <p>⚠️ Failed to load testimonials. Please refresh the page.</p>
        </div>
      `;
    }
  }

  function renderTestimonials(testimonials) {
    const carouselNav = document.querySelector('.carousel-nav');
    if (!track || !testimonials || testimonials.length === 0) {
      if (track) {
        track.innerHTML = '<div style="text-align: center; padding: var(--sp-10) 0; width: 100%; color: var(--text-secondary);">No reviews available yet.</div>';
      }
      if (carouselNav) {
        carouselNav.style.display = 'none';
      }
      return;
    }

    if (carouselNav) {
      carouselNav.style.display = 'flex';
    }

    track.innerHTML = '';
    if (dotsContainer) dotsContainer.innerHTML = '';

    // Group testimonials in chunks of 3 for the slide layout
    const chunkSize = 3;
    const slidesCount = Math.ceil(testimonials.length / chunkSize);

    for (let i = 0; i < slidesCount; i++) {
      const start = i * chunkSize;
      const chunk = testimonials.slice(start, start + chunkSize);

      // Create slide element
      const slide = document.createElement('div');
      slide.className = 'testimonial-slide';

      const grid = document.createElement('div');
      grid.className = 'testimonials-grid';

      chunk.forEach(item => {
        const ratingStars = '★'.repeat(item.rating) + '☆'.repeat(5 - item.rating);
        const avatarLetter = item.name ? item.name.charAt(0).toUpperCase() : 'U';

        // Select a color gradient for the avatar based on name character code
        const gradients = [
          'linear-gradient(135deg, var(--primary), var(--primary-dark))',
          'linear-gradient(135deg, var(--secondary), var(--secondary-dark))',
          'linear-gradient(135deg, var(--info), var(--primary-dark))',
          'linear-gradient(135deg, var(--accent), var(--accent-dark))',
          'linear-gradient(135deg, var(--error), var(--primary-dark))'
        ];
        const gradientIndex = item.name ? item.name.charCodeAt(0) % gradients.length : 0;
        const avatarGradient = gradients[gradientIndex];

        const card = document.createElement('article');
        card.className = 'testimonial-card';
        card.innerHTML = `
          <div class="testimonial-stars" aria-label="${item.rating} out of 5 stars" style="color: #fbbf24; margin-bottom: var(--sp-3); font-size: 1.1rem;">${'★'.repeat(item.rating)}</div>
          <p class="testimonial-text" style="color: var(--text-secondary); font-size: var(--text-sm); line-height: var(--lh-relaxed); margin-bottom: var(--sp-4); flex-grow: 1;">${item.message}</p>
          <div class="testimonial-author" style="display: flex; align-items: center; gap: var(--sp-3); margin-top: auto;">
            <div class="testimonial-avatar" aria-hidden="true" style="width: 40px; height: 40px; border-radius: 50%; background: ${avatarGradient}; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: var(--fw-bold); font-size: var(--text-md);">${avatarLetter}</div>
            <div>
              <div class="testimonial-name" style="font-weight: var(--fw-semi); font-size: var(--text-sm); color: var(--text);">${item.name}</div>
              <div class="testimonial-role" style="font-size: var(--text-xs); color: var(--text-secondary);">${item.role || 'Verified Customer'}</div>
            </div>
          </div>
        `;
        grid.appendChild(card);
      });

      slide.appendChild(grid);
      track.appendChild(slide);

      // Create carousel indicator dots
      if (dotsContainer) {
        const dot = document.createElement('div');
        dot.className = `carousel-dot ${i === 0 ? 'active' : ''}`;
        dot.setAttribute('role', 'listitem');
        dot.setAttribute('aria-label', `Slide ${i + 1}`);
        if (i === 0) dot.setAttribute('aria-current', 'true');
        dotsContainer.appendChild(dot);
      }
    }

    // Initialize/Bind the carousel events after rendering HTML
    if (window.initTestimonialsCarousel) {
      window.initTestimonialsCarousel();
    }
  }

  // Load testimonials initially
  loadTestimonials();

  // ─────────────────────────────────────────────
  // 2. Feedback Modal Control
  // ─────────────────────────────────────────────
  const modal = document.getElementById('feedback-modal');
  const writeReviewBtn = document.getElementById('write-review-btn');
  const closeBtn = document.getElementById('modal-close-btn');
  const form = document.getElementById('feedback-form');

  if (writeReviewBtn && modal) {
    writeReviewBtn.addEventListener('click', () => {
      resetFeedbackForm();
      modal.classList.add('active');
      document.body.style.overflow = 'hidden'; // Lock background scrolling
    });
  }

  function closeModal() {
    if (modal) {
      modal.classList.remove('active');
      document.body.style.overflow = ''; // Restore background scrolling
    }
  }

  if (closeBtn) {
    closeBtn.addEventListener('click', closeModal);
  }

  if (modal) {
    modal.addEventListener('click', (e) => {
      // Close modal when clicking on the overlay background
      if (e.target === modal) {
        closeModal();
      }
    });
  }

  // Esc key closes modal
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && modal.classList.contains('active')) {
      closeModal();
    }
  });

  // ─────────────────────────────────────────────
  // 3. Interactive Star Rating Selector
  // ─────────────────────────────────────────────
  const starButtons = document.querySelectorAll('.star-rating-selector .star-btn');
  const ratingInput = document.getElementById('feedback-rating');
  const ratingError = document.getElementById('rating-error');

  starButtons.forEach(btn => {
    // Click handler to lock-in the rating
    btn.addEventListener('click', () => {
      const val = parseInt(btn.getAttribute('data-value'), 10);
      ratingInput.value = val;
      ratingError.textContent = ''; // Clear error if set
      updateStarsState(val);
    });

    // Hover handler
    btn.addEventListener('mouseenter', () => {
      const val = parseInt(btn.getAttribute('data-value'), 10);
      highlightStarsOnHover(val);
    });
  });

  const starSelectorContainer = document.querySelector('.star-rating-selector');
  if (starSelectorContainer) {
    starSelectorContainer.addEventListener('mouseleave', () => {
      const currentRating = parseInt(ratingInput.value, 10) || 0;
      updateStarsState(currentRating);
    });
  }

  function updateStarsState(rating) {
    starButtons.forEach(btn => {
      const val = parseInt(btn.getAttribute('data-value'), 10);
      if (val <= rating) {
        btn.classList.add('active');
        btn.setAttribute('aria-checked', 'true');
      } else {
        btn.classList.remove('active');
        btn.setAttribute('aria-checked', 'false');
      }
      btn.classList.remove('hover');
    });
  }

  function highlightStarsOnHover(hoverRating) {
    starButtons.forEach(btn => {
      const val = parseInt(btn.getAttribute('data-value'), 10);
      if (val <= hoverRating) {
        btn.classList.add('hover');
      } else {
        btn.classList.remove('hover');
      }
    });
  }

  // ─────────────────────────────────────────────
  // 4. Form Validation & Submission
  // ─────────────────────────────────────────────
  function resetFeedbackForm() {
    if (form) form.reset();
    if (ratingInput) ratingInput.value = '';
    updateStarsState(0);
    
    // Hide notifications
    const successAlert = document.getElementById('fb-success');
    const errorAlert = document.getElementById('fb-error');
    if (successAlert) successAlert.style.display = 'none';
    if (errorAlert) errorAlert.style.display = 'none';

    // Clear validation error text fields
    document.querySelectorAll('#feedback-form .form-error').forEach(div => {
      div.textContent = '';
      div.style.display = 'none';
    });
    
    const spinner = document.getElementById('fb-spinner');
    if (spinner) spinner.style.display = 'none';
  }

  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      
      // Clear alerts
      const successAlert = document.getElementById('fb-success');
      const errorAlert = document.getElementById('fb-error');
      if (successAlert) successAlert.style.display = 'none';
      if (errorAlert) errorAlert.style.display = 'none';

      // ── Client-side Validation ──
      let isValid = true;

      // 1. Validate Star Rating
      const ratingVal = parseInt(ratingInput.value, 10);
      if (isNaN(ratingVal) || ratingVal < 1 || ratingVal > 5) {
        ratingError.textContent = 'Please choose a rating of 1 to 5 stars.';
        ratingError.style.display = 'block';
        isValid = false;
      } else {
        ratingError.textContent = '';
        ratingError.style.display = 'none';
      }

      // 2. Validate Name
      const nameInput = document.getElementById('fb-name');
      const nameError = document.getElementById('fb-name-error');
      const nameVal = nameInput.value.trim();
      if (!nameVal || nameVal.length < 2) {
        nameError.textContent = 'Name must be at least 2 characters.';
        nameError.style.display = 'block';
        isValid = false;
      } else if (nameVal.length > 100) {
        nameError.textContent = 'Name must not exceed 100 characters.';
        nameError.style.display = 'block';
        isValid = false;
      } else if (!/^[A-Za-z\s\.\-']+$/.test(nameVal)) {
        nameError.textContent = 'Name contains invalid characters (letters only).';
        nameError.style.display = 'block';
        isValid = false;
      } else {
        nameError.textContent = '';
        nameError.style.display = 'none';
      }

      // 3. Validate Role
      const roleInput = document.getElementById('fb-role');
      const roleError = document.getElementById('fb-role-error');
      const roleVal = roleInput.value.trim();
      if (roleVal) {
        if (roleVal.length > 100) {
          roleError.textContent = 'Role/location must not exceed 100 characters.';
          roleError.style.display = 'block';
          isValid = false;
        } else if (!/^[A-Za-z0-9\s\.\,\-\'\/]+$/.test(roleVal)) {
          roleError.textContent = 'Role/location contains invalid characters.';
          roleError.style.display = 'block';
          isValid = false;
        } else {
          roleError.textContent = '';
          roleError.style.display = 'none';
        }
      } else {
        roleError.textContent = '';
        roleError.style.display = 'none';
      }

      // 4. Validate Message
      const messageInput = document.getElementById('fb-message');
      const messageError = document.getElementById('fb-message-error');
      const messageVal = messageInput.value.trim();
      if (!messageVal || messageVal.length < 10) {
        messageError.textContent = 'Feedback must be at least 10 characters long.';
        messageError.style.display = 'block';
        isValid = false;
      } else if (messageVal.length > 1000) {
        messageError.textContent = 'Feedback must not exceed 1000 characters.';
        messageError.style.display = 'block';
        isValid = false;
      } else {
        messageError.textContent = '';
        messageError.style.display = 'none';
      }

      if (!isValid) return;

      // ── Submission via fetch ──
      const spinner = document.getElementById('fb-spinner');
      const submitBtn = form.querySelector('button[type="submit"]');
      
      if (spinner) spinner.style.display = 'inline-block';
      if (submitBtn) submitBtn.disabled = true;

      try {
        const payload = {
          name: nameVal,
          role: roleVal || null,
          rating: ratingVal,
          message: messageVal
        };

        const response = await fetch(POST_FEEDBACK_API, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (response.ok && result.success) {
          // Show Success
          if (successAlert) {
            const successText = document.getElementById('fb-success-text');
            if (successText) successText.textContent = result.message;
            successAlert.style.display = 'flex';
          }
          // Reset form, but keep stars/alerts
          form.reset();
          ratingInput.value = '';
          updateStarsState(0);
          
          // Close modal after 2.5 seconds
          setTimeout(() => {
            closeModal();
          }, 2500);
        } else {
          // Show Error from API
          if (errorAlert) {
            const errorText = document.getElementById('fb-error-text');
            if (errorText) errorText.textContent = result.message || 'Failed to submit feedback. Please try again.';
            errorAlert.style.display = 'flex';
          }
        }
      } catch (error) {
        console.error('Feedback submission error:', error);
        if (errorAlert) {
          const errorText = document.getElementById('fb-error-text');
          if (errorText) errorText.textContent = 'A network error occurred. Please check your internet connection and try again.';
          errorAlert.style.display = 'flex';
        }
      } finally {
        if (spinner) spinner.style.display = 'none';
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }
});

/* ============================================
   FORM VALIDATION & SUBMISSION — NorthStar
   Flow: User → Validate → POST /api/enquiry → Backend (rate-limit + CAPTCHA) → SMTP → Admin Inbox
   ============================================ */

(function () {
  const form = document.getElementById('contact-form');
  if (!form) return;

  const fields = {
    name:    { el: document.getElementById('full-name'), err: document.getElementById('name-error') },
    phone:   { el: document.getElementById('phone'),     err: document.getElementById('phone-error') },
    email:   { el: document.getElementById('email'),     err: document.getElementById('email-error') },
    service: { el: document.getElementById('service'),   err: document.getElementById('service-error') },
  };

  const successMsg = document.getElementById('form-success');
  const errorMsg   = document.getElementById('form-error');
  const errorText  = document.getElementById('form-error-text');
  const submitBtn  = document.getElementById('submit-btn');

  let hcaptchaWidgetId = null;
  let hcaptchaEnabled  = false;

  // Detect CAPTCHA config from health endpoint
  async function initCaptcha() {
    try {
      const res = await fetch('/api/health');
      if (res.ok) {
        const data = await res.json();
        if (data.captcha_enabled && data.captcha_sitekey) {
          hcaptchaEnabled = true;

          // Create a container element inside the form before the submit button
          const captchaContainer = document.createElement('div');
          captchaContainer.id = 'hcaptcha-container';
          captchaContainer.className = 'form-group';
          captchaContainer.style.display = 'flex';
          captchaContainer.style.justifyContent = 'center';
          
          // Insert above the submit button
          submitBtn.parentNode.insertBefore(captchaContainer, submitBtn);

          // Define the global callback function that hCaptcha calls once loaded
          window.onHCaptchaLoad = function () {
            if (window.hcaptcha) {
              hcaptchaWidgetId = window.hcaptcha.render('hcaptcha-container', {
                sitekey: data.captcha_sitekey,
                theme: 'light',
              });
            }
          };

          // Dynamically inject the hCaptcha script tag
          const script = document.createElement('script');
          script.src = 'https://js.hcaptcha.com/1/api.js?onload=onHCaptchaLoad&render=explicit';
          script.async = true;
          script.defer = true;
          document.head.appendChild(script);
        }
      }
    } catch (err) {
      console.error('Failed to load health status / captcha configuration:', err);
    }
  }

  initCaptcha();

  function resetCaptcha() {
    if (hcaptchaEnabled && window.hcaptcha && hcaptchaWidgetId !== null) {
      window.hcaptcha.reset(hcaptchaWidgetId);
    }
  }

  // ── Validation rules ─────────────────────────────────────
  const rules = {
    name(val) {
      val = val.trim();
      if (val.length < 2)  return 'Please enter your full name (at least 2 characters).';
      if (val.length > 100) return 'Name must not exceed 100 characters.';
      if (!/^[A-Za-z\s.\-']+$/.test(val)) return 'Name should contain only letters.';
      return null;
    },
    phone(val) {
      val = val.replace(/[\s\-()]/g, '').trim();
      return /^[6-9]\d{9}$/.test(val) ? null : 'Please enter a valid 10-digit Indian mobile number.';
    },
    email(val) {
      if (!val.trim()) return 'Please enter your email address.';
      return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val.trim())
        ? null
        : 'Please enter a valid email address.';
    },
    service(val) {
      return val ? null : 'Please select a service of interest.';
    },
  };

  function validateField(key) {
    const { el, err } = fields[key];
    const msg = rules[key](el.value);
    if (msg) {
      el.classList.add('error');
      err.textContent = msg;
      err.classList.add('show');
      el.setAttribute('aria-invalid', 'true');
      el.setAttribute('aria-describedby', err.id);
    } else {
      el.classList.remove('error');
      err.textContent = '';
      err.classList.remove('show');
      el.removeAttribute('aria-invalid');
    }
    return !msg;
  }

  // Validate on blur & clear error on fix
  Object.keys(fields).forEach(key => {
    fields[key].el?.addEventListener('blur',  () => validateField(key));
    fields[key].el?.addEventListener('input', () => {
      if (fields[key].el.classList.contains('error')) validateField(key);
    });
  });

  // ── Spinner helper ────────────────────────────────────────
  const SEND_ICON = `<svg width="20" height="20" fill="none" stroke="currentColor"
    stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
    <line x1="22" y1="2" x2="11" y2="13"/>
    <polygon points="22,2 15,22 11,13 2,9"/>
  </svg>`;

  const SPIN_ICON = `<svg width="20" height="20" class="spin" fill="none"
    stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83
             M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
  </svg>`;

  if (!document.getElementById('spin-style')) {
    const s = document.createElement('style');
    s.id = 'spin-style';
    s.textContent = '.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}';
    document.head.appendChild(s);
  }

  function setLoading(loading) {
    submitBtn.disabled = loading;
    submitBtn.innerHTML = loading
      ? `${SPIN_ICON} Sending...`
      : `${SEND_ICON} Send Enquiry`;
  }

  function showError(message) {
    if (errorText) errorText.textContent = message;
    errorMsg?.classList.add('show');
    successMsg?.classList.remove('show');
  }

  function showSuccess() {
    successMsg?.classList.add('show');
    errorMsg?.classList.remove('show');
    successMsg?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    form.reset();
  }

  // ── Submit handler ────────────────────────────────────────
  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // Client-side validation
    const valid = Object.keys(fields).map(k => validateField(k)).every(Boolean);
    if (!valid) {
      const firstError = Object.values(fields).find(f => f.el.classList.contains('error'));
      firstError?.el.focus();
      return;
    }

    // CAPTCHA validation
    let captchaToken = null;
    if (hcaptchaEnabled) {
      if (window.hcaptcha && hcaptchaWidgetId !== null) {
        captchaToken = window.hcaptcha.getResponse(hcaptchaWidgetId);
      }
      if (!captchaToken) {
        showError('Please complete the CAPTCHA verification.');
        return;
      }
    }

    setLoading(true);
    errorMsg?.classList.remove('show');
    successMsg?.classList.remove('show');

    const payload = {
      name:          fields.name.el.value.trim(),
      phone:         fields.phone.el.value.replace(/[\s\-()]/g, '').trim(),
      email:         fields.email.el.value.trim() || null,
      service:       fields.service.el.value,
      message:       (document.getElementById('message')?.value.trim()) || null,
      captcha_token: captchaToken,
    };

    try {
      const res = await fetch('/api/enquiry', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });

      const body = await res.json().catch(() => ({}));

      if (res.ok && body.success) {
        // ✅ Success
        showSuccess();
        resetCaptcha();
        if (typeof showToast === 'function') {
          showToast('success', 'Enquiry Sent!', 'We\'ll get back to you within 24 hours.');
        }
      } else if (res.status === 429) {
        // ⚠ Rate limited
        showError(body.message || body.detail || 'You have submitted too many requests. Please wait an hour and try again.');
        resetCaptcha();
        if (typeof showToast === 'function') {
          showToast('warning', 'Slow Down!', 'Too many requests. Please wait a while.');
        }
      } else if (res.status === 400) {
        // ⚠ Validation / CAPTCHA error
        const msg = body.message || body.detail || 'Submission failed. Please check your details.';
        showError(typeof msg === 'string' ? msg : 'Please check your inputs and try again.');
        resetCaptcha();
      } else {
        throw new Error(`Server returned ${res.status}`);
      }

    } catch (err) {
      console.error('Form submission error:', err);
      showError('Something went wrong. Please try again or reach us on WhatsApp.');
      resetCaptcha();
      if (typeof showToast === 'function') {
        showToast('error', 'Submission Failed', 'Please try again or contact us on WhatsApp.');
      }
    } finally {
      setLoading(false);
    }
  });
})();

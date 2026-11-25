// =============================================================================
// OneJournal — client-side app module
// Modern Fintech build (Tailwind + shadcn-style components, vanilla JS)
// =============================================================================

// ── Modal API (used everywhere; paired with components/modal.html) ──────────
window.openModal = function (id) {
  const m = document.getElementById(id);
  if (!m) return;
  m.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  const first = m.querySelector('input, select, textarea, button, [tabindex]');
  if (first) setTimeout(() => first.focus(), 40);
};

window.closeModal = function (id) {
  const m = document.getElementById(id);
  if (!m) return;
  m.classList.add('hidden');
  if (!document.querySelector('.modal:not(.hidden)')) {
    document.body.style.overflow = '';
  }
};

// ESC closes the topmost open modal
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  const open = document.querySelectorAll('.modal:not(.hidden)');
  if (open.length) window.closeModal(open[open.length - 1].id);
});

// ── Theme (class strategy on <html>) ────────────────────────────────────────
window.getCurrentTheme = () =>
  document.documentElement.classList.contains('dark') ? 'dark' : 'light';

window.setTheme = function (theme) {
  if (theme === 'dark') document.documentElement.classList.add('dark');
  else document.documentElement.classList.remove('dark');
  updateThemeToggleIcon();
  window.dispatchEvent(new CustomEvent('themeChanged', { detail: { theme } }));
};

function updateThemeToggleIcon() {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  const isDark = window.getCurrentTheme() === 'dark';
  btn.innerHTML = isDark
    ? '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>'
    : '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
}

document.addEventListener('DOMContentLoaded', function () {
  updateThemeToggleIcon();

  fetch('/api/settings/theme')
    .then((r) => r.json())
    .then((data) => {
      if (data && data.theme) {
        localStorage.setItem('theme', data.theme);
        window.setTheme(data.theme);
      }
    })
    .catch(() => {});

  const toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const next = window.getCurrentTheme() === 'dark' ? 'light' : 'dark';
      localStorage.setItem('theme', next);
      window.setTheme(next);
      fetch('/api/settings/theme', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: next }),
      }).catch(() => {});
    });
  }
});

// ── Plotly theming — reads CSS variables so charts always match the palette ──
function _cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

window.getThemedLayout = function (baseLayout) {
  const bgTrip      = _cssVar('--bg');
  const surfaceTrip = _cssVar('--surface');
  const inkTrip     = _cssVar('--ink');
  const mutedTrip   = _cssVar('--muted');
  const borderTrip  = _cssVar('--border');
  const accentTrip  = _cssVar('--accent');
  const gainTrip    = _cssVar('--gain');
  const lossTrip    = _cssVar('--loss');

  const bg       = `rgb(${bgTrip})`;
  const surface  = `rgb(${surfaceTrip})`;
  const ink      = `rgb(${inkTrip})`;
  const muted    = `rgb(${mutedTrip})`;
  const hairline = `rgb(${borderTrip})`;
  const accent   = `rgb(${accentTrip})`;
  const gain     = `rgb(${gainTrip})`;
  const loss     = `rgb(${lossTrip})`;

  const MONO = '"Geist Mono Variable", ui-monospace, SFMono-Regular, monospace';
  const SANS = '"Geist Variable", ui-sans-serif, system-ui, sans-serif';

  const base = baseLayout || {};
  const axisTheme = {
    gridcolor: hairline,
    linecolor: hairline,
    tickcolor: hairline,
    zerolinecolor: hairline,
    color: muted,
    tickfont: { color: muted, family: MONO, size: 11 },
  };

  return {
    ...base,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor:  'rgba(0,0,0,0)',
    font: { color: ink, family: SANS, size: 12 },
    xaxis: { ...axisTheme, ...(base.xaxis || {}) },
    yaxis: { ...axisTheme, ...(base.yaxis || {}) },
    colorway: [accent, gain, loss, muted, ink],
    hoverlabel: {
      bgcolor: surface,
      bordercolor: hairline,
      font: { color: ink, family: MONO, size: 11 },
    },
    dragmode: 'pan',
    margin: base.margin || { l: 52, r: 24, t: 28, b: 44 },
  };
};

// ── Screenshot lightbox ─────────────────────────────────────────────────────
let _lightboxScale = 1;

window.openLightbox = function (src, label) {
  _lightboxScale = 1;
  const img = document.getElementById('lightbox-img');
  if (!img) return;
  img.src = src;
  img.style.transform = 'scale(1)';
  const labelEl = document.getElementById('lightbox-label');
  if (labelEl) labelEl.textContent = label || '';
  window.openModal('screenshotLightbox');
};

window.lightboxZoom = function (delta) {
  if (delta === 0) _lightboxScale = 1;
  else _lightboxScale = Math.min(5, Math.max(0.25, _lightboxScale + delta));
  const img = document.getElementById('lightbox-img');
  if (img) img.style.transform = `scale(${_lightboxScale})`;
};

document.addEventListener('DOMContentLoaded', function () {
  const lightboxBody = document.getElementById('lightbox-body');
  if (lightboxBody) {
    lightboxBody.addEventListener(
      'wheel',
      function (e) {
        e.preventDefault();
        window.lightboxZoom(e.deltaY < 0 ? 0.15 : -0.15);
      },
      { passive: false }
    );
  }
});

// ── Flatpickr / form logic — unchanged behavior, rebound after HTMX swaps ────
function initializeDatePickers() {
  if (typeof flatpickr !== 'function') return;
  flatpickr('.datepicker', {
    dateFormat: 'd/m/Y',
    allowInput: true,
    altInput: false,
    locale: { firstDayOfWeek: 1 },
  });
}

function initializeMarketDropdown() {
  const marketSelect     = document.getElementById('market-select');
  const optionTypeRow    = document.getElementById('option-type-row');
  const optionTypeSelect = document.getElementById('option-type-select');
  if (marketSelect && optionTypeRow) {
    marketSelect.addEventListener('change', function () {
      if (this.value.includes('Options')) {
        optionTypeRow.style.display = 'flex';
        optionTypeSelect.setAttribute('required', 'required');
      } else {
        optionTypeRow.style.display = 'none';
        optionTypeSelect.removeAttribute('required');
        optionTypeSelect.value = '';
      }
    });
  }

  const editMarketSelect     = document.getElementById('edit-market-select');
  const editOptionTypeField  = document.getElementById('edit-option-type-field');
  const editOptionTypeSelect = document.getElementById('edit-option-type-select');
  if (editMarketSelect && editOptionTypeField) {
    editMarketSelect.addEventListener('change', function () {
      if (this.value.includes('Options')) {
        editOptionTypeField.style.display = 'block';
        editOptionTypeSelect.setAttribute('required', 'required');
      } else {
        editOptionTypeField.style.display = 'none';
        editOptionTypeSelect.removeAttribute('required');
        editOptionTypeSelect.value = '';
      }
    });
  }
}

function initializeTradeStyleValidation() {
  const tradeStyleSelect    = document.getElementById('trade-style-select');
  const exitDate            = document.getElementById('exit-date');
  const exitPrice           = document.getElementById('exit-price');
  const exitRequiredLabels  = document.querySelectorAll('.exit-required');
  if (!tradeStyleSelect || !exitDate || !exitPrice) return;

  tradeStyleSelect.addEventListener('change', function () {
    const isIntraday = this.value === 'Intraday';
    if (isIntraday) {
      exitDate.setAttribute('required', 'required');
      exitPrice.setAttribute('required', 'required');
      exitRequiredLabels.forEach((el) => (el.style.display = 'inline'));
    } else {
      exitDate.removeAttribute('required');
      exitPrice.removeAttribute('required');
      exitRequiredLabels.forEach((el) => (el.style.display = 'none'));
    }
  });
}

function initializeExitQuantityTracking() {
  const entryQuantity = document.querySelector('input[name="quantity"]');
  const exitQuantity  = document.getElementById('exit-quantity');
  const maxExitQty    = document.getElementById('max-exit-qty');
  if (!entryQuantity || !exitQuantity || !maxExitQty) return;

  entryQuantity.addEventListener('input', function () {
    const qty = this.value || '-';
    maxExitQty.textContent = qty;
    if (this.value) exitQuantity.setAttribute('max', this.value);
    else exitQuantity.removeAttribute('max');
  });
}

// Manage-position modal form (loaded via HTMX; logic lives here so no inline
// script is needed under strict CSP). The current quantity is read from a
// data attribute on the action select.
function initializeManagePositionForm() {
  const actionSelect = document.getElementById('action-select');
  if (!actionSelect) return;

  if (typeof flatpickr === 'function') {
    flatpickr('.datepicker-manage', { dateFormat: 'd/m/Y', maxDate: 'today' });
  }

  const currentQty = actionSelect.dataset.currentQuantity || '';
  actionSelect.addEventListener('change', function () {
    const action = this.value;
    const leverageRow   = document.getElementById('leverage-row');
    const chargesRow    = document.getElementById('charges-row');
    const quantityInput = document.getElementById('quantity-input');
    const quantityHint  = document.getElementById('quantity-hint');

    leverageRow.style.display = 'none';
    chargesRow.style.display  = 'none';
    quantityInput.value = '';
    quantityInput.readOnly = false;

    if (action === 'scale_in') {
      leverageRow.style.display = 'grid';
      quantityHint.textContent = 'Any amount';
    } else if (action === 'scale_out') {
      chargesRow.style.display = 'grid';
      quantityHint.textContent = 'Max: ' + currentQty;
    } else if (action === 'exit_all') {
      chargesRow.style.display = 'grid';
      quantityInput.value = currentQty;
      quantityInput.readOnly = true;
      quantityHint.textContent = 'Full position exit';
    }
  });
}

function runAllInitializers() {
  initializeDatePickers();
  initializeMarketDropdown();
  initializeTradeStyleValidation();
  initializeExitQuantityTracking();
  initializeManagePositionForm();
}

document.addEventListener('DOMContentLoaded', runAllInitializers);
document.body.addEventListener('htmx:afterSwap', runAllInitializers);

// ── CSP-safe event delegation ────────────────────────────────────────────────
// Strict CSP forbids inline on* handlers and hx-on. Elements now carry
// data-action (+ data-* params); these delegated listeners dispatch them to the
// same functions the inline handlers used to call.
document.addEventListener('click', function (e) {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const d = el.dataset;
  switch (d.action) {
    case 'open-modal':                window.openModal(d.modal); break;
    case 'close-modal':               window.closeModal(d.modal); break;
    case 'clear-modal-container':
      { const c = document.getElementById('modal-container'); if (c) c.innerHTML = ''; }
      break;
    case 'modal-backdrop':
      if (e.target === el) window.closeModal(d.modal);
      break;
    case 'modal-container-backdrop':
      if (e.target === el) {
        const c = document.getElementById('modal-container'); if (c) c.innerHTML = '';
      }
      break;
    case 'open-lightbox':             window.openLightbox(d.src, d.label); break;
    case 'lightbox-zoom':             window.lightboxZoom(parseFloat(d.delta)); break;
    case 'view-position':             window.viewPosition(d.positionId); break;
    case 'manage-position':           window.managePosition(d.positionId); break;
    case 'show-position-type-editor': window.showPositionTypeEditor(d.positionId); break;
    case 'update-position-type':      window.updatePositionType(d.positionId); break;
    case 'cancel-position-type-edit': window.cancelPositionTypeEdit(d.positionId); break;
    case 'set-date-range':            window.setDateRange(d.range); break;
    case 'clear-date-range':          window.clearDateRange(); break;
    case 'clear-export-filters':      window.clearExportFilters(); break;
    case 'export-with-filters':       window.exportWithFilters(d.format); break;
    case 'backup-database':           window.backupDatabase(); break;
    case 'show-load-template-modal':  window.showLoadTemplateModal(); break;
    case 'share-comparison':          window.shareComparison(); break;
    case 'save-template':             window.saveTemplate(); break;
    case 'load-template':             window.loadTemplate(parseInt(d.index, 10)); break;
    case 'delete-template':           window.deleteTemplate(parseInt(d.index, 10)); break;
  }
});

// Forms inside HTMX-loaded modals clear the modal container on a successful submit
// (replaces the old hx-on::after-request inline handlers).
document.body.addEventListener('htmx:afterRequest', function (e) {
  const t = e.target;
  if (t && t.matches && t.matches('[data-clear-modal-on-success]') &&
      e.detail && e.detail.successful) {
    const c = document.getElementById('modal-container');
    if (c) c.innerHTML = '';
  }
});

// Manage-position form: after a successful scale-in / scale-out / exit, close the
// window and reload so the positions list shows the new quantity and P&L.
document.body.addEventListener('htmx:afterSwap', function (e) {
  const t = e.detail && e.detail.target;
  if (t && t.id === 'manage-result' && t.querySelector('.alert-success')) {
    setTimeout(function () {
      window.closeModal('managePositionModal');
      location.reload();
    }, 1200);
  }
});

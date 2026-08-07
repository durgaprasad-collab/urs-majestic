(function () {
  const body = document.body;
  const quickMenu = document.querySelector('[data-quick-menu]');
  const quickToggles = document.querySelectorAll('[data-quick-toggle]');
  const palette = document.querySelector('[data-command-palette]');
  const input = document.querySelector('[data-command-input]');
  const items = Array.from(document.querySelectorAll('[data-command-item]'));
  const empty = document.querySelector('[data-command-empty]');

  function setQuick(open) {
    if (!quickMenu) return;
    quickMenu.hidden = !open;
    quickToggles.forEach((button) => button.setAttribute('aria-expanded', String(open)));
  }

  quickToggles.forEach((button) => button.addEventListener('click', (event) => {
    event.stopPropagation();
    if (button.closest('.mobile-actions')) {
      body.classList.remove('nav-open');
      setQuick(true);
      return;
    }
    setQuick(quickMenu ? quickMenu.hidden : false);
  }));

  function filterCommands() {
    const query = (input ? input.value : '').trim().toLowerCase();
    let visible = 0;
    items.forEach((item) => {
      const haystack = `${item.textContent} ${item.dataset.keywords || ''}`.toLowerCase();
      item.hidden = query !== '' && !haystack.includes(query);
      if (!item.hidden) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0;
  }

  function setPalette(open) {
    if (!palette) return;
    palette.hidden = !open;
    body.classList.toggle('palette-open', open);
    if (open && input) {
      input.value = '';
      filterCommands();
      window.setTimeout(() => input.focus(), 10);
    }
  }

  document.querySelectorAll('[data-command-open]').forEach((button) => button.addEventListener('click', () => setPalette(true)));
  document.querySelectorAll('[data-command-close]').forEach((button) => button.addEventListener('click', () => setPalette(false)));
  document.querySelectorAll('[data-nav-toggle]').forEach((button) => button.addEventListener('click', () => body.classList.toggle('nav-open')));
  if (input) input.addEventListener('input', filterCommands);

  document.addEventListener('click', (event) => {
    if (quickMenu && !quickMenu.hidden && !event.target.closest('.quick-wrap')) setQuick(false);
    if (body.classList.contains('nav-open') && !event.target.closest('.app-nav') && !event.target.closest('[data-nav-toggle]')) body.classList.remove('nav-open');
  });

  // Generic tabs / segmented control: [data-tabs] wraps buttons with
  // [data-tab-btn="key"] and panels with [data-tab-panel="key"].
  document.querySelectorAll('[data-tabs]').forEach((wrap) => {
    const btns = Array.from(wrap.querySelectorAll('[data-tab-btn]'));
    const panels = Array.from(wrap.querySelectorAll('[data-tab-panel]'));
    function activate(key) {
      btns.forEach((b) => b.classList.toggle('active', b.dataset.tabBtn === key));
      panels.forEach((p) => { p.hidden = p.dataset.tabPanel !== key; });
    }
    btns.forEach((b) => b.addEventListener('click', () => activate(b.dataset.tabBtn)));
    const initial = wrap.dataset.tabsInitial || (btns[0] && btns[0].dataset.tabBtn);
    if (initial) activate(initial);
  });

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      setPalette(true);
    }
    if (event.key === 'Escape') {
      setPalette(false);
      setQuick(false);
      body.classList.remove('nav-open');
    }
    if (event.key === 'Enter' && palette && !palette.hidden && document.activeElement === input) {
      const first = items.find((item) => !item.hidden);
      if (first) first.click();
    }
  });
})();

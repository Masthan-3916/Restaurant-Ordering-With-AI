(() => {
  let credentials = '';
  let currentSessionId = null;
  let isAdmin = false;
  const cart = new Map();
  const $ = (selector) => document.querySelector(selector);
  const byId = (id) => document.getElementById(id);

  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (credentials) headers.set('Authorization', `Basic ${credentials}`);
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(url, { ...options, headers });
    const contentType = response.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) throw new Error(body?.message || body?.detail || `Request failed (${response.status}).`);
    return body;
  }

  function showError(element, error) {
    element.textContent = error.message || 'Something went wrong.';
  }

  function showApp(username, adminRole) {
    isAdmin = adminRole;
    byId('login-panel').hidden = true;
    byId('app-panel').hidden = false;
    byId('identity').textContent = username;
    byId('logout').hidden = false;
    byId('admin-tab').hidden = !isAdmin;
    loadMenu();
    loadOrders();
    loadSessions();
  }

  async function signIn(username, password) {
    credentials = btoa(`${username}:${password}`);
    const identity = await request('/api/auth/me');
    showApp(identity.username, identity.role === 'admin');
  }

  function renderMenu(items) {
    const grid = byId('menu-grid');
    grid.replaceChildren();
    byId('menu-count').textContent = `${items.length} dishes`;
    const categorySelect = byId('category');
    const categories = [...new Set(items.map((item) => item.category))].sort();
    categories.forEach((category) => {
      if (![...categorySelect.options].some((option) => option.value === category)) {
        categorySelect.add(new Option(category, category));
      }
    });
    items.forEach((item) => {
      const card = document.createElement('article');
      card.className = 'menu-card';
      const top = document.createElement('div');
      top.className = 'menu-card-top';
      const name = document.createElement('div');
      const title = document.createElement('h3');
      title.textContent = item.title;
      const category = document.createElement('span');
      category.className = 'category';
      category.textContent = item.category;
      name.append(title, category);
      const mark = document.createElement('span');
      mark.className = `veg-dot${item.vegetarian ? '' : ' nonveg'}`;
      mark.setAttribute('aria-label', item.vegetarian ? 'Vegetarian' : 'Non-vegetarian');
      top.append(name, mark);
      const bottom = document.createElement('div');
      bottom.className = 'menu-card-bottom';
      const price = document.createElement('div');
      const amount = document.createElement('span');
      amount.className = 'price';
      amount.textContent = `₹${Number(item.price).toFixed(2)}`;
      const stock = document.createElement('span');
      stock.className = 'stock';
      stock.textContent = `${item.capacity} portions available`;
      price.append(amount, stock);
      const controls = document.createElement('div');
      controls.className = 'quantity-control';
      const quantity = document.createElement('input');
      quantity.type = 'number';
      quantity.min = '1';
      quantity.max = String(Math.min(10, item.capacity));
      quantity.value = '1';
      quantity.setAttribute('aria-label', `Quantity of ${item.title}`);
      quantity.disabled = item.capacity < 1;
      const add = document.createElement('button');
      add.type = 'button';
      add.textContent = 'Add';
      add.disabled = item.capacity < 1;
      add.addEventListener('click', () => {
        const amount = Math.max(1, Math.min(10, Number(quantity.value) || 1));
        cart.set(item.id, { item, quantity: Math.min(item.capacity, amount) });
        updateCart();
      });
      controls.append(quantity, add);
      bottom.append(price, controls);
      if (isAdmin) {
        const adminActions = document.createElement('div');
        adminActions.className = 'menu-admin-actions';
        const edit = document.createElement('button');
        edit.className = 'button button-outline';
        edit.type = 'button';
        edit.textContent = 'Edit';
        edit.addEventListener('click', () => {
          const form = byId('item-form');
          form.elements.item_id.value = item.id;
          form.elements.title.value = item.title;
          form.elements.category.value = item.category;
          form.elements.price.value = item.price;
          form.elements.capacity.value = item.capacity;
          form.elements.vegetarian.checked = item.vegetarian;
          form.querySelector('button[type="submit"]').textContent = 'Save changes';
          byId('cancel-edit').hidden = false;
          document.querySelector('.tab[data-panel="admin-panel"]').click();
        });
        const disable = document.createElement('button');
        disable.className = 'button button-quiet';
        disable.type = 'button';
        disable.textContent = 'Disable';
        disable.addEventListener('click', async () => {
          try {
            await request(`/api/menu_items/${item.id}`, { method: 'DELETE' });
            await loadMenu();
          } catch (error) { showError(byId('global-error'), error); }
        });
        adminActions.append(edit, disable);
      }
      card.append(top, bottom);
      if (isAdmin) card.append(adminActions);
      grid.append(card);
    });
  }

  async function loadMenu() {
    const params = new URLSearchParams();
    if (byId('keyword').value.trim()) params.set('keyword', byId('keyword').value.trim());
    if (byId('category').value) params.set('category', byId('category').value);
    if (byId('vegetarian').checked) params.set('vegetarian', 'true');
    if (byId('max-price').value) params.set('max_price', byId('max-price').value);
    try {
      renderMenu(await request(`/api/menu_items?${params}`));
      byId('global-error').textContent = '';
    } catch (error) {
      showError(byId('global-error'), error);
    }
  }

  function updateCart() {
    const values = [...cart.values()];
    const count = values.reduce((total, entry) => total + entry.quantity, 0);
    const total = values.reduce((sum, entry) => sum + Number(entry.item.price) * entry.quantity, 0);
    byId('cart-count').textContent = String(count);
    byId('cart-total').textContent = `₹${total.toFixed(2)}`;
    byId('checkout').disabled = count === 0;
  }

  async function checkout() {
    try {
      const order = await request('/api/orders', {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ lines: [...cart.values()].map(({ item, quantity }) => ({ item_id: item.id, quantity })) }),
      });
      cart.clear();
      updateCart();
      await loadMenu();
      await loadOrders();
      alert(`Your order #${order.id} is confirmed. Total ₹${Number(order.total).toFixed(2)}.`);
    } catch (error) {
      showError(byId('global-error'), error);
    }
  }

  async function loadOrders() {
    try {
      const orders = await request(isAdmin ? '/api/admin/orders' : '/api/orders');
      const list = byId('orders-list');
      list.replaceChildren();
      if (!orders.length) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = 'No orders yet. Your next favourite is on the menu.';
        list.append(empty);
        return;
      }
      orders.forEach((order) => {
        const card = document.createElement('article');
        card.className = 'order-card';
        const top = document.createElement('div');
        top.className = 'order-top';
        const number = document.createElement('span');
        number.className = 'order-number';
        number.textContent = `Order #${order.id}`;
        const status = document.createElement('span');
        status.className = 'status';
        status.textContent = order.status;
        top.append(number, status);
        const lines = document.createElement('p');
        lines.className = 'order-lines';
        lines.textContent = order.lines.map((line) => `${line.quantity} × ${line.title}`).join(' · ');
        const bottom = document.createElement('div');
        bottom.className = 'order-bottom';
        const date = document.createElement('span');
        date.textContent = new Date(order.created_at).toLocaleString();
        const total = document.createElement('strong');
        total.textContent = `₹${Number(order.total).toFixed(2)}`;
        bottom.append(date, total);
        if (order.status === 'CONFIRMED') {
          if (isAdmin) {
            addAdminStatusControl(order, bottom);
          } else {
            const cancel = document.createElement('button');
            cancel.className = 'button button-outline';
            cancel.type = 'button';
            cancel.textContent = 'Cancel order';
            cancel.addEventListener('click', async () => {
              try {
                await request(`/api/orders/${order.id}/cancel`, { method: 'POST' });
                await loadOrders();
                await loadMenu();
              } catch (error) { showError(byId('global-error'), error); }
            });
            bottom.append(cancel);
          }
        } else if (isAdmin && (order.status === 'PREPARING' || order.status === 'READY')) {
          addAdminStatusControl(order, bottom);
        }
        card.append(top, lines, bottom);
        list.append(card);
      });
    } catch (error) {
      showError(byId('global-error'), error);
    }

    function addAdminStatusControl(order, container) {
      const select = document.createElement('select');
      const nextStatuses = order.status === 'CONFIRMED'
        ? ['PREPARING', 'CANCELLED']
        : order.status === 'PREPARING' ? ['READY'] : ['COMPLETED'];
      nextStatuses.forEach((status) => select.add(new Option(status, status)));
      const update = document.createElement('button');
      update.className = 'button button-outline';
      update.type = 'button';
      update.textContent = 'Update status';
      update.addEventListener('click', async () => {
        try {
          await request(`/api/admin/orders/${order.id}/status`, { method: 'PATCH', body: JSON.stringify({ status: select.value }) });
          await loadOrders();
          await loadMenu();
        } catch (error) { showError(byId('global-error'), error); }
      });
      container.append(select, update);
    }
  }

  async function loadSessions() {
    try {
      const sessions = await request('/api/chat/sessions');
      const list = byId('session-list');
      list.replaceChildren();
      sessions.forEach((session) => {
        const button = document.createElement('button');
        button.className = `session-button${session.id === currentSessionId ? ' active' : ''}`;
        button.type = 'button';
        button.textContent = session.title;
        button.addEventListener('click', () => openSession(session.id));
        list.append(button);
      });
      if (!currentSessionId && sessions.length) await openSession(sessions[0].id);
    } catch (error) {
      showError(byId('chat-error'), error);
    }
  }

  function addMessage(message, metadata = {}) {
    const container = byId('chat-messages');
    const bubble = document.createElement('div');
    bubble.className = `message ${message.role}`;
    bubble.textContent = message.content;
    if (message.role === 'assistant' && metadata.citations?.length) {
      const sources = document.createElement('div');
      sources.className = 'message-meta';
      metadata.citations.forEach((citation) => {
        const chip = document.createElement('span');
        chip.className = 'citation-chip';
        chip.textContent = `${citation.filename}${citation.page ? ` · p. ${citation.page}` : ''}`;
        sources.append(chip);
      });
      bubble.append(sources);
    }
    if (message.role === 'assistant' && metadata.pending_action) {
      const action = metadata.pending_action;
      const card = document.createElement('div');
      card.className = 'action-card';
      const text = document.createElement('p');
      text.textContent = action.action === 'CREATE_ORDER' ? `Confirm order total: INR ${action.payload.total}` : `Confirm cancellation of order #${action.payload.order_id}`;
      const confirm = document.createElement('button');
      confirm.className = 'button button-primary';
      confirm.type = 'button';
      confirm.textContent = 'Confirm';
      const reject = document.createElement('button');
      reject.className = 'button button-outline';
      reject.type = 'button';
      reject.textContent = 'Reject';
      [confirm, reject].forEach((button, index) => {
        button.addEventListener('click', () => confirmAction(action.id, index === 0));
      });
      card.append(text, confirm, reject);
      bubble.append(card);
    }
    container.append(bubble);
    container.scrollTop = container.scrollHeight;
  }

  async function openSession(sessionId) {
    currentSessionId = sessionId;
    const messages = await request(`/api/chat/sessions/${sessionId}/messages`);
    byId('chat-messages').replaceChildren();
    messages.forEach((message) => addMessage(message, message.metadata || {}));
    await loadSessions();
  }

  async function createSession() {
    const session = await request('/api/chat/sessions', { method: 'POST', body: JSON.stringify({ title: 'New conversation' }) });
    currentSessionId = session.id;
    byId('chat-messages').replaceChildren();
    await loadSessions();
  }

  async function sendMessage(text) {
    if (!currentSessionId) await createSession();
    const result = await request(`/api/chat/sessions/${currentSessionId}/messages`, { method: 'POST', body: JSON.stringify({ message: text }) });
    addMessage(result.user_message);
    addMessage(result.assistant_message, { citations: result.citations, pending_action: result.pending_action });
    await loadSessions();
  }

  async function confirmAction(actionId, confirm) {
    try {
      const result = await request(`/api/chat/sessions/${currentSessionId}/actions/${actionId}/confirm`, {
        method: 'POST', body: JSON.stringify({ confirm }),
      });
      if (result.assistant_message) addMessage(result.assistant_message);
      else addMessage({ role: 'assistant', content: confirm ? 'Action already confirmed.' : 'Action rejected.' });
      await loadOrders();
      await loadMenu();
    } catch (error) {
      showError(byId('chat-error'), error);
      if (currentSessionId) {
        try { await openSession(currentSessionId); } catch (reloadError) { showError(byId('chat-error'), reloadError); }
      }
    }
  }

  byId('login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    byId('login-error').textContent = '';
    const form = new FormData(event.currentTarget);
    try { await signIn(form.get('username'), form.get('password')); }
    catch (error) { credentials = ''; showError(byId('login-error'), error); }
  });
  byId('logout').addEventListener('click', () => {
    credentials = '';
    currentSessionId = null;
    isAdmin = false;
    byId('login-panel').hidden = false;
    byId('app-panel').hidden = true;
    byId('identity').textContent = 'Not signed in';
    byId('logout').hidden = true;
  });
  byId('filters').addEventListener('submit', (event) => { event.preventDefault(); loadMenu(); });
  byId('keyword').addEventListener('input', () => { clearTimeout(window.menuTimer); window.menuTimer = setTimeout(loadMenu, 250); });
  byId('checkout').addEventListener('click', checkout);
  byId('refresh-orders').addEventListener('click', loadOrders);
  byId('new-chat').addEventListener('click', () => createSession().catch((error) => showError(byId('chat-error'), error)));
  byId('chat-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = byId('chat-input');
    const text = input.value.trim();
    if (!text) return;
    byId('chat-error').textContent = '';
    input.value = '';
    try { await sendMessage(text); }
    catch (error) {
      showError(byId('chat-error'), error);
      if (currentSessionId) {
        try { await openSession(currentSessionId); } catch (reloadError) { showError(byId('chat-error'), reloadError); }
      }
    }
  });
  document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === tab));
    document.querySelectorAll('.view-panel').forEach((panel) => { panel.hidden = panel.id !== tab.dataset.panel; });
  }));
  byId('item-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const itemId = form.get('item_id');
      await request(itemId ? `/api/menu_items/${itemId}` : '/api/menu_items', { method: itemId ? 'PUT' : 'POST', body: JSON.stringify({
        title: form.get('title'), category: form.get('category'), price: form.get('price'),
        capacity: Number(form.get('capacity')), vegetarian: form.has('vegetarian'),
      }) });
      event.currentTarget.reset();
      event.currentTarget.querySelector('button[type="submit"]').textContent = 'Add dish';
      byId('cancel-edit').hidden = true;
      byId('admin-message').textContent = itemId ? 'Menu item updated.' : 'Menu item added.';
      await loadMenu();
    } catch (error) { showError(byId('admin-message'), error); }
  });
  byId('cancel-edit').addEventListener('click', () => {
    byId('item-form').reset();
    byId('item-form').querySelector('button[type="submit"]').textContent = 'Add dish';
    byId('cancel-edit').hidden = true;
  });
  byId('document-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData();
    form.append('file', event.currentTarget.elements.document.files[0]);
    try {
      const result = await request('/api/documents', { method: 'POST', body: form });
      byId('admin-message').textContent = `${result.filename} indexed (${result.chunks} chunks).`;
      event.currentTarget.reset();
    } catch (error) { showError(byId('admin-message'), error); }
  });
})();

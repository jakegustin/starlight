let zoneOrder = [];
let statusById = {};
let saveTimer = null;
let saveInFlight = false;
let lastSavedSignature = null;

function $(id) {
  return document.getElementById(id);
}

function setMessage(text, kind = '') {
  const message = $('message');
  message.className = `message ${kind}`.trim();
  message.textContent = text;
}

function setSyncIndicator(text, active = false) {
  const indicator = $('sync-indicator');
  indicator.textContent = text;
  indicator.style.opacity = active ? '1' : '0.85';
}

function updateSummary() {
  const ids = zoneOrder.length ? zoneOrder : Object.keys(statusById);
  const onlineCount = Object.values(statusById).filter((receiver) => receiver.online).length;
  $('receiver-count').textContent = String(ids.length);
  $('online-count').textContent = String(onlineCount);
  $('save-state').textContent = lastSavedSignature === JSON.stringify(zoneOrder) ? 'Synced' : 'Pending';
}

function move(index, delta) {
  const nextIndex = index + delta;
  if (nextIndex < 0 || nextIndex >= zoneOrder.length) return;

  [zoneOrder[index], zoneOrder[nextIndex]] = [zoneOrder[nextIndex], zoneOrder[index]];
  render();
  scheduleSave();
}

function scheduleSave() {
  if (saveTimer) {
    clearTimeout(saveTimer);
  }

  saveTimer = setTimeout(() => {
    save().catch(() => {});
  }, 150);
}

function render() {
  const list = $('receiver-list');
  list.innerHTML = '';

  zoneOrder.forEach((id, index) => {
    const status = statusById[id] || { online: false, port: 'unknown' };
    const row = document.createElement('li');
    row.className = 'receiver-row';

    row.innerHTML = `
      <span class="dot ${status.online ? 'on' : 'off'}" aria-hidden="true"></span>
      <div class="receiver-main">
        <div class="receiver-name">${id}</div>
        <div class="receiver-meta">${status.online ? 'online' : 'offline'} · port: ${status.port ?? 'unknown'}</div>
      </div>
      <button class="button-primary" ${!status.online ? 'disabled' : ''} data-action="blink">Blink</button>
      <button class="button-small" ${index === 0 ? 'disabled' : ''} data-action="up">↑</button>
      <button class="button-small" ${index === zoneOrder.length - 1 ? 'disabled' : ''} data-action="down">↓</button>
      <div class="rank">${index + 1}</div>
    `;

    const buttons = row.querySelectorAll('button');
    buttons[0].onclick = () => blink(id);
    buttons[1].onclick = () => move(index, -1);
    buttons[2].onclick = () => move(index, 1);

    list.appendChild(row);
  });

  updateSummary();
}

async function blink(receiverId) {
  try {
    const response = await fetch('/api/blink', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ receiverId }),
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `Blink request failed for ${receiverId}`);
    }

    setMessage(`Blink request sent to ${receiverId}`, 'ok');
  } catch (error) {
    setMessage(error instanceof Error ? error.message : String(error), 'err');
  }
}

async function refresh() {
  setSyncIndicator('Refreshing', true);

  const response = await fetch('/api/status');
  if (!response.ok) {
    throw new Error('Failed to fetch controller status');
  }

  const data = await response.json();
  statusById = {};
  (data.receivers || []).forEach((receiver) => {
    statusById[receiver.id] = receiver;
  });

  const merged = [...(data.zoneOrder || [])];
  Object.keys(statusById).forEach((id) => {
    if (!merged.includes(id)) merged.push(id);
  });

  zoneOrder = merged;
  lastSavedSignature = JSON.stringify(data.zoneOrder || []);
  render();
  setSyncIndicator('Live', false);
}

async function save() {
  if (saveInFlight) return;
  saveInFlight = true;
  setSyncIndicator('Saving', true);

  try {
    const response = await fetch('/api/zone-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ zoneOrder }),
    });

    if (!response.ok) {
      throw new Error('Failed to save order');
    }

    const payload = await response.json();
    zoneOrder = payload.zoneOrder || zoneOrder;
    lastSavedSignature = JSON.stringify(zoneOrder);
    setMessage('Order saved automatically', 'ok');
    render();
    setSyncIndicator('Live', false);
  } catch (error) {
    setMessage('Failed to save order (will retry on next change)', 'err');
    setSyncIndicator('Retrying', true);
    throw error;
  } finally {
    saveInFlight = false;
  }
}

async function boot() {
  try {
    await refresh();
  } catch (error) {
    setMessage(error instanceof Error ? error.message : String(error), 'err');
    setSyncIndicator('Offline', false);
  }

  setInterval(() => refresh().catch((error) => {
    setMessage(error instanceof Error ? error.message : String(error), 'err');
    setSyncIndicator('Offline', false);
  }), 2000);
}

document.addEventListener('DOMContentLoaded', boot);

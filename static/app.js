const USER_ID = 'chat-' + (localStorage.getItem('userId') || (() => {
  const id = crypto.randomUUID().slice(0, 8);
  localStorage.setItem('userId', id);
  return id;
})());

const chatContainer = document.getElementById('chatContainer');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatText(text) {
  let html = escapeHtml(text);
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<div class="code-block">$2</div>');

  // Convert markdown tables to HTML tables
  if (html.includes('|') && html.includes('---')) {
    const lines = html.split('\n');
    let inTable = false;
    let tableLines = [];
    const result = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.trim().startsWith('|') && line.includes('---')) continue; // skip separator
      if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
        if (!inTable) { inTable = true; tableLines = []; }
        tableLines.push(line);
      } else {
        if (inTable) {
          result.push(buildHtmlTable(tableLines));
          inTable = false;
        }
        result.push(line);
      }
    }
    if (inTable) result.push(buildHtmlTable(tableLines));
    html = result.join('\n');
  }

  html = html.replace(/\n/g, '<br>');
  return html;
}

function buildHtmlTable(mdLines) {
  const rows = mdLines.map(line => {
    const cells = line.split('|').filter(c => c.trim() !== '');
    return cells.map(c => c.trim());
  });
  if (rows.length === 0) return '';
  const thead = rows[0];
  const tbody = rows.slice(1);
  let table = '<table><thead><tr>';
  thead.forEach(h => { table += '<th>' + h + '</th>'; });
  table += '</tr></thead><tbody>';
  tbody.forEach(row => {
    table += '<tr>';
    row.forEach(c => { table += '<td>' + c + '</td>'; });
    table += '</tr>';
  });
  table += '</tbody></table>';
  return table;
}

function addMessage(text, role) {
  const el = document.createElement('div');
  el.className = `message ${role}`;
  const avatar = role === 'user' ? '👤' : '🤖';
  el.innerHTML = `<span class="avatar">${avatar}</span><span class="message-content">${formatText(text)}</span>`;
  chatContainer.appendChild(el);
  chatContainer.scrollTop = chatContainer.scrollHeight;
  return el;
}

function addApprovalCard(text, actionId) {
  const card = document.createElement('div');
  card.className = 'approval-card';
  card.dataset.actionId = actionId;
  card.innerHTML = `
    <div class="text">${escapeHtml(text)}</div>
    <div class="actions">
      <button class="approve" data-action="approve">✅ Approve</button>
      <button class="reject" data-action="reject">❌ Reject</button>
    </div>
  `;
  chatContainer.appendChild(card);
  chatContainer.scrollTop = chatContainer.scrollHeight;

  card.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      card.querySelectorAll('button').forEach(b => b.disabled = true);
      await sendAction(actionId, btn.dataset.action, card);
    });
  });
}

function showThinking() {
  const el = document.createElement('div');
  el.className = 'message bot thinking';
  el.innerHTML = '<span class="avatar">🤖</span><div class="typing-dots"><span></span><span></span><span></span></div>';
  el.id = 'thinkingIndicator';
  chatContainer.appendChild(el);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function hideThinking() {
  const el = document.getElementById('thinkingIndicator');
  if (el) el.remove();
}

async function sendAction(actionId, action, cardEl) {
  showThinking();
  try {
    const res = await fetch('/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        value: { action, action_id: actionId },
        from: { id: USER_ID }
      })
    });
    const data = await res.json();
    hideThinking();
    addMessage(data.text, 'bot');
  } catch (err) {
    hideThinking();
    addMessage('Error: ' + err.message, 'bot error');
  }
}

async function sendMessage() {
  const text = userInput.value.trim();
  if (!text) return;

  addMessage(text, 'user');
  userInput.value = '';
  showThinking();
  sendBtn.disabled = true;
  userInput.disabled = true;

  try {
    const res = await fetch('/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: text,
        from: { id: USER_ID }
      })
    });
    const data = await res.json();
    hideThinking();

    if (data.attachments) {
      data.attachments.forEach(att => {
        const content = att.content;
        const bodyText = content.body.map(b => b.text).join('\n');
        const actionId = content.actions?.[0]?.data?.action_id;
        if (actionId) {
          addApprovalCard(bodyText, actionId);
        } else {
          addMessage(bodyText, 'bot');
        }
      });
    } else if (data.text) {
      addMessage(data.text, 'bot');
    }
  } catch (err) {
    hideThinking();
    addMessage('Error: ' + err.message, 'bot error');
  } finally {
    sendBtn.disabled = false;
    userInput.disabled = false;
    userInput.focus();
  }

  // Refresh cluster badge after every message
  refreshClusterBadge();
}

sendBtn.addEventListener('click', sendMessage);
userInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

addMessage('👋 Hello! I\'m your Kubernetes AI agent. Ask me about pods, deployments, or cluster status.', 'bot');

// ---- CLUSTER BADGE ---- //

async function refreshClusterBadge() {
  try {
    const res = await fetch('/api/cluster/active');
    const data = await res.json();
    const dot = document.getElementById('cluster-dot');
    const name = document.getElementById('cluster-name');
    const zone = document.getElementById('cluster-zone');
    const badge = document.getElementById('cluster-badge');
    if (!dot || !name || !zone || !badge) return;

    if (!data.name) {
      dot.className = 'status-dot offline';
      name.textContent = 'No cluster';
      zone.textContent = '';
      badge.title = 'Click to select a cluster';
      return;
    }
    dot.className = 'status-dot ' + (data.online ? '' : 'offline');
    name.textContent = data.name;
    zone.textContent = data.zone || '';
    badge.title = 'Project: ' + (data.project || 'default') + ' • Last verified: ' + (data.last_verified || 'unknown');
  } catch (e) {
    console.error('Failed to refresh cluster badge', e);
  }
}

async function toggleClusterDropdown(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById('cluster-dropdown');
  if (!dropdown) return;
  if (dropdown.style.display === 'block') {
    dropdown.style.display = 'none';
    return;
  }
  try {
    const [activeRes, listRes] = await Promise.all([
      fetch('/api/cluster/active').then(r => r.json()),
      fetch('/api/cluster/list').then(r => r.json()),
    ]);
    const clusters = listRes.clusters || [];
    dropdown.innerHTML = clusters.map(c =>
      `<div class="dropdown-item ${c.name === activeRes.name ? 'active' : ''}"
           onclick="event.stopPropagation(); switchCluster('${c.name}')">
        <span>${c.name}</span>
        <span class="dd-zone">${c.zone || ''}</span>
      </div>`
    ).join('') || '<div class="dropdown-item">No saved clusters</div>';
    dropdown.style.display = 'block';
  } catch (err) {
    console.error('Failed to load cluster list', err);
  }
}

function switchCluster(name) {
  const dropdown = document.getElementById('cluster-dropdown');
  if (dropdown) dropdown.style.display = 'none';
  if (typeof sendMessage === 'function') {
    userInput.value = 'switch to cluster ' + name;
    sendMessage();
  }
}

document.addEventListener('click', (e) => {
  const badge = document.getElementById('cluster-badge');
  const dropdown = document.getElementById('cluster-dropdown');
  if (badge && dropdown && !badge.contains(e.target)) {
    dropdown.style.display = 'none';
  }
});

document.addEventListener('DOMContentLoaded', () => {
  refreshClusterBadge();
  setInterval(refreshClusterBadge, 30000);
});

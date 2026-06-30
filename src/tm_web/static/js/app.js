
// ─── Theme ──────────────────────────────────────
(function initTheme() {
  const stored = localStorage.getItem('ttm-theme');
  let theme;
  if (stored === 'light' || stored === 'dark') {
    theme = stored;
  } else if (window.__TTM_SERVER_THEME === 'light' || window.__TTM_SERVER_THEME === 'dark') {
    theme = window.__TTM_SERVER_THEME;
  } else {
    theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  applyTheme(theme);
})();

function applyTheme(theme) {
  if (theme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  const icon = document.getElementById('theme-icon');
  if (icon) icon.innerHTML = theme === 'light' ? '&#9728;' : '&#9790;';
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  const next = current === 'light' ? 'dark' : 'light';
  applyTheme(next);
  localStorage.setItem('ttm-theme', next);
  // Sync config selector if visible
  const sel = document.getElementById('cfg-web-theme');
  if (sel) sel.value = next;
}

// Listen for system theme changes
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', e => {
  if (!localStorage.getItem('ttm-theme')) {
    applyTheme(e.matches ? 'light' : 'dark');
  }
});

// ─── State ──────────────────────────────────────
let STATES = [];
let PRIORITIES = [];
let currentView = 'tasks';
let currentTaskView = 'pending';
let taskCache = {};  // id -> task object
let subtaskCache = {};  // subtask_id -> subtask object
let allTasks = [];   // flat list for filtering/sorting
let activeFilters = { state: null, priority: null, tag: null, text: '' };
let kanbanFilters = { priority: null, tag: null, text: '' };
let kanbanTagsExpanded = false;
let allKanbanTasks = [];  // flat list for kanban filtering
let currentSort = { field: null, asc: true };
let currentJiraFilter = 'active';

// ─── API helpers ────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  return res.json();
}

function cacheTasks(tasks) {
  tasks.forEach(t => {
    taskCache[t.id] = t;
    (t.subtasks || []).forEach(st => subtaskCache[st.id] = st);
  });
}

// ─── Navigation ─────────────────────────────────
document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => {
    const view = el.dataset.view;
    if (view === 'jira' && el.dataset.jiraFilter) {
      currentJiraFilter = el.dataset.jiraFilter;
    }
    switchView(view);
  });
});

function setActiveNavItem(view) {
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  let selector = `.nav-item[data-view="${view}"]`;
  if (view === 'jira') {
    selector += `[data-jira-filter="${currentJiraFilter}"]`;
  }
  const el = document.querySelector(selector);
  if (el) el.classList.add('active');
}

function switchView(view) {
  currentView = view;
  setActiveNavItem(view);
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(`view-${view}`).classList.add('active');
  loadView(view);
}

function loadView(view) {
  const loaders = {
    tasks: () => loadTasks(currentTaskView),
    kanban: loadKanban,
    agenda: loadAgenda,
    calendar: loadCalendar,
    stats: loadStats,
    weekly: loadWeekly,
    burndown: loadBurndown,
    gantt: loadGantt,
    tags: loadTags,
    time: loadTime,
    pomodoro: loadPomodoro,
    blockers: loadBlockers,
    jira: loadJira,
    notes: loadNotes,
    sync: loadSync,
    config: loadConfig,
    log: loadLog,
  };
  if (loaders[view]) loaders[view]();
}

// ─── Tasks View ─────────────────────────────────
async function loadTasks(view = 'pending') {
  currentTaskView = view;
  document.getElementById('btn-view-pending').classList.toggle('btn-primary', view === 'pending');
  document.getElementById('btn-view-all').classList.toggle('btn-primary', view === 'all');

  const data = await api('GET', `/api/tasks?view=${view}`);
  if (data.states) STATES = data.states;
  if (data.priorities) PRIORITIES = data.priorities;

  if (!data.tasks || data.tasks.length === 0) {
    document.getElementById('task-list').innerHTML = '<li class="empty">No tasks</li>';
    document.getElementById('filter-bar').innerHTML = '';
    return;
  }

  cacheTasks(data.tasks);
  allTasks = data.tasks;
  buildFilterBar(data.tasks);
  renderFilteredTasks();
}

function buildFilterBar(tasks) {
  const states = [...new Set(tasks.map(t => t.state))].sort();
  const priorities = [...new Set(tasks.map(t => t.priority).filter(Boolean))];
  const tags = [...new Set(tasks.flatMap(t => t.tags || []))].sort();

  let html = '<span style="font-size:10px;color:var(--text-dim);margin-right:4px">Filter:</span>';
  // Text filter
  html += `<span class="kbd-hint" style="margin-right:4px">f</span><input type="text" id="filter-text-input" placeholder="text..." value="${h(activeFilters.text)}" 
    style="width:120px;padding:3px 8px;font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-family:inherit"
    oninput="onFilterTextChange(this.value)">`;
  html += '<span style="width:8px"></span>';
  states.forEach(s => {
    html += `<span class="filter-chip${activeFilters.state===s?' active':''}" onclick="toggleFilter('state','${s}')">${s}</span>`;
  });
  html += '<span style="width:8px"></span>';
  priorities.forEach(p => {
    html += `<span class="filter-chip${activeFilters.priority===p?' active':''}" onclick="toggleFilter('priority','${p}')">${p}</span>`;
  });
  if (tags.length) {
    html += '<span style="width:8px"></span>';
    tags.forEach(tag => {
      html += `<span class="filter-chip${activeFilters.tag===tag?' active':''}" onclick="toggleFilter('tag','${tag}')">#${tag}</span>`;
    });
  }
  if (activeFilters.state || activeFilters.priority || activeFilters.tag || activeFilters.text) {
    html += `<span class="filter-clear" onclick="clearFilters()">Clear</span>`;
  }
  document.getElementById('filter-bar').innerHTML = html;
}

function onFilterTextChange(value) {
  activeFilters.text = value.trim().toLowerCase();
  renderFilteredTasks();
}

function toggleFilter(type, value) {
  activeFilters[type] = activeFilters[type] === value ? null : value;
  buildFilterBar(allTasks);
  renderFilteredTasks();
}

// ─── Archive ─────────────────────────────────────
async function archiveFinished() {
  if (!confirm('Archive all finished tasks?')) return;
  const data = await api('POST', '/api/tasks/archive', {});
  if (data.ok) alert(`Archived ${data.archived} task(s)`);
  loadView(currentView);
}

// ─── Batch Operations ────────────────────────────
function toggleSelect(id) {
  if (SELECTED_TASKS.has(id)) SELECTED_TASKS.delete(id);
  else SELECTED_TASKS.add(id);
  updateBatchBar();
}

function clearSelection() {
  SELECTED_TASKS.clear();
  updateBatchBar();
  renderFilteredTasks();
}

function updateBatchBar() {
  const bar = document.getElementById('batch-bar');
  const count = SELECTED_TASKS.size;
  if (count === 0) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  document.getElementById('batch-count').textContent = count + ' selected';
  const stateSel = document.getElementById('batch-state');
  if (!stateSel.dataset.populated) {
    stateSel.innerHTML = '<option value="">State…</option>' + STATES.map(s => `<option value="${s}">${s}</option>`).join('');
    stateSel.dataset.populated = '1';
  }
  const prioSel = document.getElementById('batch-priority');
  if (!prioSel.dataset.populated) {
    prioSel.innerHTML = '<option value="">Priority…</option>' + PRIORITIES.map(p => `<option value="${p}">${p}</option>`).join('');
    prioSel.dataset.populated = '1';
  }
}

async function batchSetState(state) {
  if (!state) return;
  const ids = Array.from(SELECTED_TASKS);
  await api('POST', '/api/tasks/batch/state', { task_ids: ids, state });
  document.getElementById('batch-state').value = '';
  clearSelection();
  loadView(currentView);
}

async function batchSetPriority(priority) {
  if (!priority) return;
  const ids = Array.from(SELECTED_TASKS);
  await api('POST', '/api/tasks/batch/priority', { task_ids: ids, priority });
  document.getElementById('batch-priority').value = '';
  clearSelection();
  loadView(currentView);
}

async function batchAddTag(tag) {
  const t = (tag || '').trim();
  if (!t) return;
  const ids = Array.from(SELECTED_TASKS);
  await api('POST', '/api/tasks/batch/tags', { task_ids: ids, add_tags: [t] });
  document.getElementById('batch-add-tag').value = '';
  loadView(currentView);
}

async function batchRemoveTag(tag) {
  const t = (tag || '').trim();
  if (!t) return;
  const ids = Array.from(SELECTED_TASKS);
  await api('POST', '/api/tasks/batch/tags', { task_ids: ids, remove_tags: [t] });
  document.getElementById('batch-remove-tag').value = '';
  loadView(currentView);
}

async function batchDelete() {
  const ids = Array.from(SELECTED_TASKS);
  if (!confirm(`Delete ${ids.length} task(s)?`)) return;
  await api('POST', '/api/tasks/batch/delete', { task_ids: ids });
  clearSelection();
  loadView(currentView);
}

// ─── Import ──────────────────────────────────────
async function importTasks() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json';
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const text = await file.text();
    try { JSON.parse(text); } catch(e) { alert('Invalid JSON file'); return; }
    const data = await api('POST', '/api/tasks/import', { json: text });
    if (data.ok) alert(`Imported ${data.imported} task(s)`);
    loadView(currentView);
  };
  input.click();
}

function clearFilters() {
  activeFilters = { state: null, priority: null, tag: null, text: '' };
  buildFilterBar(allTasks);
  renderFilteredTasks();
}

function renderFilteredTasks() {
  let tasks = allTasks.filter(t => {
    if (activeFilters.state && t.state !== activeFilters.state) return false;
    if (activeFilters.priority && t.priority !== activeFilters.priority) return false;
    if (activeFilters.tag && !(t.tags || []).includes(activeFilters.tag)) return false;
    if (activeFilters.text) {
      const q = activeFilters.text;
      const inTitle = t.title.toLowerCase().includes(q);
      const inTags = (t.tags || []).some(tag => tag.toLowerCase().includes(q));
      const inNotes = (t.notes || []).some(n => n.toLowerCase().includes(q));
      const inSubtasks = (t.subtasks || []).some(st => st.title.toLowerCase().includes(q));
      if (!inTitle && !inTags && !inNotes && !inSubtasks) return false;
    }
    return true;
  });

  if (currentSort.field) {
    tasks = [...tasks].sort((a, b) => {
      let va = a[currentSort.field] || '';
      let vb = b[currentSort.field] || '';
      if (currentSort.field === 'id') { va = parseInt(va); vb = parseInt(vb); }
      if (currentSort.field === 'priority') {
        const order = { URGENT: 0, HIGH: 1, MEDIUM: 2, LOW: 3, '': 4 };
        va = order[va] ?? 4; vb = order[vb] ?? 4;
      }
      if (currentSort.field === 'due_date') {
        va = va ? va.split('/').reverse().join('') : '9999';
        vb = vb ? vb.split('/').reverse().join('') : '9999';
      }
      if (va < vb) return currentSort.asc ? -1 : 1;
      if (va > vb) return currentSort.asc ? 1 : -1;
      return 0;
    });
  }

  const list = document.getElementById('task-list');
  if (tasks.length === 0) {
    list.innerHTML = '<li class="empty">No tasks match filters</li>';
  } else {
    list.innerHTML = tasks.map(t => renderTaskItem(t)).join('');
  }
}

// Sort header click
document.getElementById('sort-header').addEventListener('click', e => {
  const span = e.target.closest('[data-sort]');
  if (!span) return;
  const field = span.dataset.sort;
  if (currentSort.field === field) {
    currentSort.asc = !currentSort.asc;
  } else {
    currentSort = { field, asc: true };
  }
  // Update visual
  document.querySelectorAll('.sort-header span').forEach(s => s.className = '');
  span.className = 'sorted' + (currentSort.asc ? ' asc' : '');
  renderFilteredTasks();
});

function renderTagsWithLimit(tags, limit = 3, containerId) {
  if (!tags || !tags.length) return '';
  const visible = tags.slice(0, limit);
  const hidden = tags.slice(limit);
  let html = visible.map(tag => `<span class="task-tag">#${h(tag)}</span>`).join('');
  if (hidden.length > 0) {
    html += `<span class="tags-hidden" id="${containerId}">${hidden.map(tag => `<span class="task-tag">#${h(tag)}</span>`).join('')}</span>`;
    html += `<span class="tags-more" onclick="event.stopPropagation();toggleTagsMore(this, '${containerId}')">+${hidden.length}</span>`;
  }
  return html;
}

function toggleTagsMore(btn, containerId) {
  const container = document.getElementById(containerId);
  if (container) {
    container.classList.toggle('show');
    btn.style.display = container.classList.contains('show') ? 'none' : 'inline';
  }
}

const SELECTED_TASKS = new Set();

function renderTaskItem(t, showStateDropdown = true) {
  const hasTags = t.tags && t.tags.length;
  const hasSubtasks = t.subtasks && t.subtasks.length;
  const hasNotes = t.notes && t.notes.length;
  const hasExtra = hasTags || hasSubtasks || hasNotes;
  const checked = SELECTED_TASKS.has(t.id) ? 'checked' : '';
  return `
    <li class="task-item" data-id="${t.id}">
      <div class="task-row" onclick="openEditModal('${t.id}')">
        <input type="checkbox" class="task-checkbox" ${checked} onclick="event.stopPropagation();toggleSelect('${t.id}')">
        <span class="task-id">#${t.id}</span>
        <span class="state-badge state-${t.state.replace(/ /g, '_')}"${showStateDropdown ? ` onclick="event.stopPropagation();toggleStateDropdown(this, '${t.id}', '${t.state}')"` : ''}>${t.state}</span>
        <div class="task-title-col">
          <span class="task-title">${h(stripTags(t.title))}</span>
          ${hasSubtasks ? `
            <div class="task-subtasks">
              ${t.subtasks.map((st, idx) => `
                <div class="subtask-block">
                  <div class="task-subtask" onclick="event.stopPropagation();openSubtaskModal('${st.id}')">
                    <span class="sub-state state-badge state-${st.state.replace(/ /g, '_')}">${st.state}</span>
                    <span class="sub-title">${h(stripTags(st.title))}</span>
                    <span class="sub-tags">${renderTagsWithLimit(st.tags, 3, `st-tags-${t.id}-${idx}`)}</span>
                    <span class="sub-priority priority-badge ${st.priority ? 'priority-' + st.priority : ''}">${st.priority || ''}</span>
                    <span class="sub-due ${st.due_date && isOverdue(st.due_date) ? 'overdue' : ''}">${st.due_date || ''}</span>
                  </div>
                  ${st.notes && st.notes.length ? `<div class="subtask-notes">${st.notes.map(n => `<div class="subtask-note"><span class="sn-text">${linkifyNote(n)}</span></div>`).join('')}</div>` : ''}
                  ${st.linked_notes && st.linked_notes.length ? `<div style="display:flex;gap:2px;flex-wrap:wrap;padding:2px 0 0 20px">${st.linked_notes.map(n => `<span class="linked-note-badge" onclick="event.stopPropagation();openNoteView('${h(n)}')" style="display:inline-block;padding:0 4px;font-size:9px;background:var(--bg);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--accent, #4fc3f7);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px" title="${h(n)}">${h(n.split('/').pop().replace('.md',''))}</span>`).join('')}</div>` : ''}
                </div>
              `).join('')}
            </div>
          ` : ''}
          ${hasNotes ? `
            <ul class="task-notes">
              ${t.notes.map((n, i) => `
                <li>
                  <span class="note-content">${linkifyNote(n)}</span>
                  <span class="note-actions">
                    <span class="na-edit" onclick="event.stopPropagation();inlineEditNote('${t.id}',${i},'${h(n).replace(/'/g,"\\'")}')">edit</span>
                    <span class="na-del" onclick="event.stopPropagation();inlineDeleteNote('${t.id}',${i})">x</span>
                  </span>
                </li>
              `).join('')}
            </ul>
          ` : ''}
          ${t.linked_notes && t.linked_notes.length ? `<div style="display:flex;gap:2px;flex-wrap:wrap;margin-top:2px">${t.linked_notes.map(n => `<span class="linked-note-badge" onclick="event.stopPropagation();openNoteView('${h(n)}')" style="display:inline-block;padding:0 4px;font-size:9px;background:var(--bg);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--accent, #4fc3f7);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px" title="${h(n)}">${h(n.split('/').pop().replace('.md',''))}</span>`).join('')}</div>` : ''}
        </div>
        <span class="task-tags-col">${renderTagsWithLimit(t.tags, 3, `task-tags-${t.id}`)}</span>
        <span class="priority-badge ${t.priority ? 'priority-' + t.priority : ''}">${t.priority || ''}</span>
        <span class="due-badge ${t.due_date && isOverdue(t.due_date) ? 'overdue' : ''}">${t.due_date || ''}</span>
      </div>
    </li>`;
}

// ─── Inline Note Actions (Task List) ────────────
async function inlineDeleteNote(taskId, index) {
  if (!confirm('Delete this note?')) return;
  const data = await api('POST', `/api/tasks/${taskId}/notes/delete`, { index });
  if (data.id) { taskCache[data.id] = data; (data.subtasks || []).forEach(st => subtaskCache[st.id] = st); }
  loadView(currentView);
}

async function inlineEditNote(taskId, index, oldNote) {
  document.getElementById('editnote-task-id').value = taskId;
  document.getElementById('editnote-index').value = index;
  document.getElementById('editnote-text').value = oldNote;
  document.getElementById('modal-editnote').classList.add('open');
  document.getElementById('editnote-text').focus();
}

async function submitEditNote() {
  // If a callback override is set (from edit-task-modal or subtask-modal), use it
  if (window._editNoteCallback) { await window._editNoteCallback(); return; }
  const taskId = document.getElementById('editnote-task-id').value;
  const index = parseInt(document.getElementById('editnote-index').value);
  const newNote = document.getElementById('editnote-text').value.trim();
  if (!newNote) return;
  const data = await api('POST', `/api/tasks/${taskId}/notes/edit`, { index, note: newNote });
  if (data.id) { taskCache[data.id] = data; (data.subtasks || []).forEach(st => subtaskCache[st.id] = st); }
  closeModal('modal-editnote');
  loadView(currentView);
}

// ─── Kanban View ────────────────────────────────
async function loadKanban() {
  // Always fetch all tasks (no server-side filtering)
  const data = await api('GET', '/api/kanban');
  const board = document.getElementById('kanban-board');

  // Flatten all tasks for filtering
  allKanbanTasks = [];
  data.columns.forEach(col => {
    (data.tasks[col] || []).forEach(t => {
      t._column = col;  // Track original column
      allKanbanTasks.push(t);
    });
  });
  
  // Pre-cache all kanban tasks
  cacheTasks(allKanbanTasks);
  
  // Build filter bar and render
  buildKanbanFilterBar(allKanbanTasks, data.columns);
  renderFilteredKanban(data.columns);
}

function buildKanbanFilterBar(tasks, columns) {
  const priorities = [...new Set(tasks.map(t => t.priority).filter(Boolean))];
  const allTags = [...new Set(tasks.flatMap(t => t.tags || []))].sort();
  const MAX_VISIBLE_TAGS = 8;

  let html = '<span style="font-size:10px;color:var(--text-dim);margin-right:4px">Filter:</span>';
  
  // Text filter
  html += `<span class="kbd-hint" style="margin-right:4px">f</span><input type="text" id="kanban-filter-text" placeholder="text..." value="${h(kanbanFilters.text || '')}" 
    style="width:120px;padding:3px 8px;font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-family:inherit"
    oninput="onKanbanFilterTextChange(this.value)">`;
  html += '<span style="width:8px"></span>';
  
  // Priorities as chips
  priorities.forEach(p => {
    html += `<span class="filter-chip${kanbanFilters.priority===p?' active':''}" onclick="toggleKanbanFilter('priority','${p}')">${p}</span>`;
  });
  
  // Tags as chips (with limit + show all)
  if (allTags.length) {
    html += '<span style="width:8px"></span>';
    const visibleTags = kanbanTagsExpanded ? allTags : allTags.slice(0, MAX_VISIBLE_TAGS);
    const hiddenCount = allTags.length - MAX_VISIBLE_TAGS;
    
    visibleTags.forEach(tag => {
      html += `<span class="filter-chip${kanbanFilters.tag===tag?' active':''}" onclick="toggleKanbanFilter('tag','${tag}')">#${tag}</span>`;
    });
    
    if (hiddenCount > 0 && !kanbanTagsExpanded) {
      html += `<span class="filter-chip" style="background:var(--bg-tertiary);font-style:italic" onclick="expandKanbanTags()">+${hiddenCount} more</span>`;
    } else if (kanbanTagsExpanded && allTags.length > MAX_VISIBLE_TAGS) {
      html += `<span class="filter-chip" style="background:var(--bg-tertiary);font-style:italic" onclick="collapseKanbanTags()">Show less</span>`;
    }
  }
  
  // Clear button
  if (kanbanFilters.priority || kanbanFilters.tag || kanbanFilters.text) {
    html += `<span class="filter-clear" onclick="clearKanbanFilters()">Clear</span>`;
  }
  
  document.getElementById('kanban-filter-bar').innerHTML = html;
}

function onKanbanFilterTextChange(value) {
  kanbanFilters.text = value.trim().toLowerCase();
  // Re-render without rebuilding filter bar (to keep focus)
  const data = { columns: [...new Set(allKanbanTasks.map(t => t._column))] };
  renderFilteredKanban(data.columns.length ? data.columns : ['BACKLOG', 'IN PROGRESS', 'TESTING', 'DONE']);
}

function toggleKanbanFilter(type, value) {
  kanbanFilters[type] = kanbanFilters[type] === value ? null : value;
  buildKanbanFilterBar(allKanbanTasks, [...new Set(allKanbanTasks.map(t => t._column))]);
  renderFilteredKanban([...new Set(allKanbanTasks.map(t => t._column))]);
}

function clearKanbanFilters() {
  kanbanFilters = { priority: null, tag: null, text: '' };
  kanbanTagsExpanded = false;
  buildKanbanFilterBar(allKanbanTasks, [...new Set(allKanbanTasks.map(t => t._column))]);
  renderFilteredKanban([...new Set(allKanbanTasks.map(t => t._column))]);
}

function expandKanbanTags() {
  kanbanTagsExpanded = true;
  buildKanbanFilterBar(allKanbanTasks, [...new Set(allKanbanTasks.map(t => t._column))]);
}

function collapseKanbanTags() {
  kanbanTagsExpanded = false;
  buildKanbanFilterBar(allKanbanTasks, [...new Set(allKanbanTasks.map(t => t._column))]);
}

function renderFilteredKanban(columns) {
  // Apply filters
  let tasks = allKanbanTasks.filter(t => {
    if (kanbanFilters.priority && t.priority !== kanbanFilters.priority) return false;
    if (kanbanFilters.tag && !(t.tags || []).includes(kanbanFilters.tag)) return false;
    if (kanbanFilters.text) {
      const q = kanbanFilters.text;
      const inTitle = t.title.toLowerCase().includes(q);
      const inTags = (t.tags || []).some(tag => tag.toLowerCase().includes(q));
      const inNotes = (t.notes || []).some(n => n.toLowerCase().includes(q));
      const inSubtasks = (t.subtasks || []).some(st => st.title.toLowerCase().includes(q));
      if (!inTitle && !inTags && !inNotes && !inSubtasks) return false;
    }
    return true;
  });
  
  // Group by column
  const tasksByColumn = {};
  columns.forEach(col => tasksByColumn[col] = []);
  tasks.forEach(t => {
    if (tasksByColumn[t._column]) {
      tasksByColumn[t._column].push(t);
    }
  });
  
  const board = document.getElementById('kanban-board');
  board.innerHTML = columns.map(col => `
    <div class="kanban-col" data-column="${col}">
      <div class="kanban-col-header">
        <span>${col}</span>
        <span class="kanban-col-count">${(tasksByColumn[col] || []).length}</span>
      </div>
      <div class="kanban-col-body" data-column="${col}"
           ondragover="kanbanDragOver(event)" ondragleave="kanbanDragLeave(event)" ondrop="kanbanDrop(event)">
        ${(tasksByColumn[col] || []).map(t => `
          <div class="kanban-card" draggable="true" data-id="${t.id}"
               ondragstart="kanbanDragStart(event)" ondragend="kanbanDragEnd(event)"
               onclick="openEditModal('${t.id}')">
            <div class="card-title">${h(stripTags(t.title))}</div>
            <div class="card-meta">
              ${t.priority ? `<span class="priority-badge priority-${t.priority}" style="font-size:9px">${t.priority}</span>` : ''}
              ${t.due_date ? `<span class="due-badge ${isOverdue(t.due_date) ? 'overdue' : ''}" style="font-size:10px">${t.due_date}</span>` : ''}
              ${(t.tags || []).map(tag => `<span class="card-tag">#${tag}</span>`).join('')}
            </div>
          </div>`).join('')}
      </div>
    </div>
  `).join('');
}

// Drag & Drop
function kanbanDragStart(e) {
  e.target.classList.add('dragging');
  e.dataTransfer.setData('text/plain', e.target.dataset.id);
  e.dataTransfer.effectAllowed = 'move';
}

function kanbanDragEnd(e) {
  e.target.classList.remove('dragging');
  document.querySelectorAll('.kanban-col-body').forEach(el => el.classList.remove('drag-over'));
}

function kanbanDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  e.currentTarget.classList.add('drag-over');
}

function kanbanDragLeave(e) {
  e.currentTarget.classList.remove('drag-over');
}

async function kanbanDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  const taskId = e.dataTransfer.getData('text/plain');
  const newState = e.currentTarget.dataset.column;

  await api('POST', `/api/tasks/${taskId}/state`, { state: newState });
  loadKanban();
}

// ─── Agenda View ────────────────────────────────
async function loadAgenda() {
  const data = await api('GET', '/api/agenda');
  const el = document.getElementById('agenda-content');

  let html = '';
  if (data.overdue.length > 0) {
    html += `<div class="agenda-section"><h3 class="overdue">Overdue (${data.overdue.length})</h3>`;
    html += taskListHtml(data.overdue);
    html += '</div>';
  }
  if (data.due_today.length > 0) {
    html += `<div class="agenda-section"><h3 class="today">Due Today (${data.due_today.length})</h3>`;
    html += taskListHtml(data.due_today);
    html += '</div>';
  }
  if (data.due_soon.length > 0) {
    html += `<div class="agenda-section"><h3 class="soon">Due Soon (${data.due_soon.length})</h3>`;
    html += taskListHtml(data.due_soon);
    html += '</div>';
  }
  if (!html) html = '<div class="empty">No upcoming deadlines</div>';
  el.innerHTML = html;
}

// ─── Calendar View ──────────────────────────────
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
let calendarState = {
  view: 'month',
  year: new Date().getFullYear(),
  month: new Date().getMonth() + 1,
  day: new Date().getDate(),
};

async function loadCalendar() {
  const { view, year, month, day } = calendarState;
  const data = await api('GET', `/api/calendar?view=${view}&year=${year}&month=${month}&day=${day}`);
  
  // Update title
  const titleEl = document.getElementById('calendar-title');
  if (view === 'week') {
    titleEl.textContent = `Week of ${data.start_date}`;
  } else {
    titleEl.textContent = `${MONTHS[month - 1]} ${year}`;
  }
  
  // Update view buttons
  document.getElementById('cal-btn-week').classList.toggle('active', view === 'week');
  document.getElementById('cal-btn-month').classList.toggle('active', view === 'month');
  
  const el = document.getElementById('calendar-content');
  const today = new Date();
  const todayStr = `${String(today.getDate()).padStart(2, '0')}/${String(today.getMonth() + 1).padStart(2, '0')}/${today.getFullYear()}`;
  
  // Build grid
  let html = `<div class="calendar-grid ${view === 'week' ? 'week-view' : ''}">`;
  
  // Weekday headers
  html += WEEKDAYS.map(d => `<div class="calendar-weekday">${d}</div>`).join('');
  
  // Get all dates we need to display
  const dates = Object.keys(data.days).sort((a, b) => {
    const [da, ma, ya] = a.split('/').map(Number);
    const [db, mb, yb] = b.split('/').map(Number);
    return new Date(ya, ma - 1, da) - new Date(yb, mb - 1, db);
  });
  
  if (view === 'month') {
    // For month view, we need to pad with days from prev/next month
    const firstDate = dates[0];
    const [fd, fm, fy] = firstDate.split('/').map(Number);
    const firstDayOfMonth = new Date(fy, fm - 1, fd);
    const startPad = (firstDayOfMonth.getDay() + 6) % 7; // Monday = 0
    
    // Add padding days before
    for (let i = startPad - 1; i >= 0; i--) {
      const padDate = new Date(fy, fm - 1, fd - (i + 1));
      const padStr = formatDateStr(padDate);
      html += renderCalendarDay(padStr, [], todayStr, true);
    }
  }
  
  // Render actual days
  for (const dateStr of dates) {
    const tasks = data.days[dateStr] || [];
    const [d, m, y] = dateStr.split('/').map(Number);
    const isOtherMonth = view === 'month' && m !== month;
    html += renderCalendarDay(dateStr, tasks, todayStr, isOtherMonth);
  }
  
  if (view === 'month') {
    // Add padding days after to complete the grid (6 rows * 7 = 42 cells)
    const totalCells = dates.length + ((new Date(year, month - 1, 1).getDay() + 6) % 7);
    const remaining = (7 - (totalCells % 7)) % 7;
    const lastDate = dates[dates.length - 1];
    const [ld, lm, ly] = lastDate.split('/').map(Number);
    for (let i = 1; i <= remaining; i++) {
      const padDate = new Date(ly, lm - 1, ld + i);
      const padStr = formatDateStr(padDate);
      html += renderCalendarDay(padStr, [], todayStr, true);
    }
  }
  
  html += '</div>';
  el.innerHTML = html;
  
  // Setup drag and drop
  setupCalendarDragDrop();
}

function formatDateStr(date) {
  return `${String(date.getDate()).padStart(2, '0')}/${String(date.getMonth() + 1).padStart(2, '0')}/${date.getFullYear()}`;
}

function renderCalendarDay(dateStr, tasks, todayStr, isOtherMonth = false) {
  const [d, m, y] = dateStr.split('/').map(Number);
  const date = new Date(y, m - 1, d);
  const dayOfWeek = date.getDay();
  const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
  const isToday = dateStr === todayStr;
  const maxVisible = calendarState.view === 'week' ? 8 : 3;
  
  let classes = 'calendar-day calendar-day-clickable';
  if (isOtherMonth) classes += ' other-month';
  if (isToday) classes += ' today';
  if (isWeekend) classes += ' weekend';
  
  const visibleTasks = tasks.slice(0, maxVisible);
  const hiddenCount = tasks.length - maxVisible;
  
  let html = `<div class="${classes}" data-date="${dateStr}" ondragover="calendarDragOver(event)" ondrop="calendarDrop(event, '${dateStr}')" ondragleave="calendarDragLeave(event)" onclick="calendarDayClick(event, '${dateStr}')">`;
  html += `<div class="day-number">${d}</div>`;
  html += '<div class="calendar-tasks">';
  
  for (const task of visibleTasks) {
    const prioClass = task.priority ? `priority-${task.priority}` : '';
    const stateClass = task.state ? `state-${task.state.replace(/ /g, '_')}` : '';
    html += `<div class="calendar-task ${prioClass} ${stateClass}" draggable="true" data-id="${task.id}" onclick="event.stopPropagation();openEditModal('${task.id}')" ondragstart="calendarDragStart(event, '${task.id}')" ondragend="calendarDragEnd(event)" title="#${task.id} ${h(task.title)}">#${task.id} ${h(task.title).substring(0, 20)}</div>`;
  }
  
  if (hiddenCount > 0) {
    html += `<div class="calendar-more" onclick="event.stopPropagation();showCalendarPopover(this, '${dateStr}')">+${hiddenCount} more</div>`;
  }
  
  html += '</div></div>';
  return html;
}

function setCalendarView(view) {
  calendarState.view = view;
  loadCalendar();
}

function calendarPrev() {
  if (calendarState.view === 'week') {
    const d = new Date(calendarState.year, calendarState.month - 1, calendarState.day - 7);
    calendarState.year = d.getFullYear();
    calendarState.month = d.getMonth() + 1;
    calendarState.day = d.getDate();
  } else {
    calendarState.month--;
    if (calendarState.month < 1) {
      calendarState.month = 12;
      calendarState.year--;
    }
  }
  loadCalendar();
}

function calendarNext() {
  if (calendarState.view === 'week') {
    const d = new Date(calendarState.year, calendarState.month - 1, calendarState.day + 7);
    calendarState.year = d.getFullYear();
    calendarState.month = d.getMonth() + 1;
    calendarState.day = d.getDate();
  } else {
    calendarState.month++;
    if (calendarState.month > 12) {
      calendarState.month = 1;
      calendarState.year++;
    }
  }
  loadCalendar();
}

// Calendar popover for +N more
let activeCalendarPopover = null;
function showCalendarPopover(el, dateStr) {
  closeCalendarPopover();
  
  // Parse the date to get correct API params
  const [d, m, y] = dateStr.split('/').map(Number);
  
  // Get all tasks for this date - use the date's own month/year for the API call
  api('GET', `/api/calendar?view=month&year=${y}&month=${m}&day=${d}`).then(data => {
    const tasks = data.days[dateStr] || [];
    if (tasks.length === 0) return;
    
    const popover = document.createElement('div');
    popover.className = 'calendar-popover';
    popover.innerHTML = tasks.map(task => {
      const prioClass = task.priority ? `priority-${task.priority}` : '';
      const stateClass = task.state ? `state-${task.state.replace(/ /g, '_')}` : '';
      return `<div class="calendar-task ${prioClass} ${stateClass}" draggable="true" data-id="${task.id}" onclick="event.stopPropagation();openEditModal('${task.id}')" ondragstart="calendarDragStart(event, '${task.id}')" ondragend="calendarDragEnd(event)">#${task.id} ${h(task.title)}</div>`;
    }).join('');
    
    // Append to the calendar-day element (el's grandparent: more -> calendar-tasks -> calendar-day)
    const dayEl = el.closest('.calendar-day');
    if (dayEl) {
      dayEl.appendChild(popover);
      activeCalendarPopover = popover;
      
      // Close on outside click
      setTimeout(() => {
        document.addEventListener('click', closeCalendarPopover, { once: true });
      }, 0);
    }
  });
}

function closeCalendarPopover() {
  if (activeCalendarPopover) {
    activeCalendarPopover.remove();
    activeCalendarPopover = null;
  }
}

// Drag and drop
let draggedTaskId = null;

function setupCalendarDragDrop() {
  // Already set up via inline handlers
}

function calendarDragStart(event, taskId) {
  draggedTaskId = taskId;
  event.target.classList.add('dragging');
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', taskId);
}

function calendarDragEnd(event) {
  event.target.classList.remove('dragging');
  draggedTaskId = null;
  document.querySelectorAll('.calendar-day.drag-over').forEach(el => el.classList.remove('drag-over'));
}

function calendarDragOver(event) {
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  event.currentTarget.classList.add('drag-over');
}

function calendarDragLeave(event) {
  event.currentTarget.classList.remove('drag-over');
}

async function calendarDrop(event, dateStr) {
  event.preventDefault();
  event.currentTarget.classList.remove('drag-over');
  
  const taskId = event.dataTransfer.getData('text/plain') || draggedTaskId;
  if (!taskId) return;
  
  // Update task due date
  const result = await api('POST', '/api/tasks/edit', { task_id: taskId, due_date: dateStr });
  if (result && !result.error) {
    loadCalendar();
  }
}

// Click on empty day to create task
function calendarDayClick(event, dateStr) {
  // Only if clicking on the day itself, not on a task
  if (event.target.classList.contains('calendar-day') || event.target.classList.contains('day-number') || event.target.classList.contains('calendar-tasks')) {
    openNewTaskModalWithDate(dateStr);
  }
}

function openNewTaskModalWithDate(dateStr) {
  // Open the new task modal and pre-fill the due date
  openNewTaskModal();
  if (dateStr) {
    document.getElementById('new-due').value = dateStr;
  }
}

// ─── Stats View ─────────────────────────────────
async function loadStats() {
  const data = await api('GET', '/api/stats');
  const el = document.getElementById('stats-content');

  const maxState = Math.max(...Object.values(data.by_state), 1);
  const maxPri = Math.max(...Object.values(data.by_priority || {}), 1);

  el.innerHTML = `
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-value">${data.total}</div><div class="stat-label">Total Tasks</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--red)">${data.overdue}</div><div class="stat-label">Overdue</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--green)">${data.due_today}</div><div class="stat-label">Due Today</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--yellow)">${data.due_this_week}</div><div class="stat-label">Due This Week</div></div>
    </div>
    <h3 style="margin:16px 0 8px;font-size:13px">By State</h3>
    <div class="stat-bars">
      ${Object.entries(data.by_state).map(([s, c]) => `
        <div class="stat-bar-row">
          <span class="stat-bar-label">${s}</span>
          <div class="stat-bar"><div class="stat-bar-fill state-${s.replace(/ /g, '_')}" style="width:${(c/maxState)*100}%"></div></div>
          <span class="stat-bar-count">${c}</span>
        </div>
      `).join('')}
    </div>
    ${Object.keys(data.by_priority || {}).length > 0 ? `
      <h3 style="margin:16px 0 8px;font-size:13px">By Priority</h3>
      <div class="stat-bars">
        ${Object.entries(data.by_priority).map(([p, c]) => `
          <div class="stat-bar-row">
            <span class="stat-bar-label">${p}</span>
            <div class="stat-bar"><div class="stat-bar-fill priority-${p}" style="width:${(c/maxPri)*100}%"></div></div>
            <span class="stat-bar-count">${c}</span>
          </div>
        `).join('')}
      </div>
    ` : ''}
  `;
}

// ─── Weekly Report ──────────────────────────────
async function loadWeekly() {
  const data = await api('GET', '/api/weekly');
  const el = document.getElementById('weekly-content');

  el.innerHTML = `
    <div class="report-summary">
      <div class="item"><div class="value">${data.total}</div><div class="label">Total</div></div>
      <div class="item"><div class="value" style="color:var(--green)">${data.total_done}</div><div class="label">Done</div></div>
      <div class="item"><div class="value" style="color:var(--accent)">${data.total_pending}</div><div class="label">Pending</div></div>
      <div class="item"><div class="value" style="color:var(--green)">${data.completed.length}</div><div class="label">Completed (${data.days}d)</div></div>
    </div>
    <p style="font-size:11px;color:var(--text-dim);margin-bottom:16px">Period: ${data.period_start} - ${data.period_end}</p>
    <div class="report-section">
      <h3 style="color:var(--green)">Completed (${data.completed.length})</h3>
      ${data.completed.length ? taskListHtml(data.completed) : '<div class="empty">None</div>'}
    </div>
    <div class="report-section">
      <h3 style="color:var(--accent)">In Progress (${data.in_progress.length})</h3>
      ${data.in_progress.length ? taskListHtml(data.in_progress) : '<div class="empty">None</div>'}
    </div>
    <div class="report-section">
      <h3 style="color:var(--yellow)">Upcoming (${data.upcoming.length})</h3>
      ${data.upcoming.length ? taskListHtml(data.upcoming) : '<div class="empty">None</div>'}
    </div>
  `;
}

// ─── Burndown Chart ─────────────────────────────
async function loadBurndown() {
  const data = await api('GET', '/api/burndown');
  const el = document.getElementById('burndown-content');

  if (!data.points || data.points.length === 0) {
    el.innerHTML = '<div class="empty">No data to chart</div>';
    return;
  }

  const maxVal = data.total_tasks;
  const w = 700, ht = 300, pad = 40;
  const chartW = w - pad * 2, chartH = ht - pad * 2;
  const stepX = chartW / Math.max(data.points.length - 1, 1);

  let path = '';
  data.points.forEach((p, i) => {
    const x = pad + i * stepX;
    const y = pad + chartH - (p.remaining / maxVal) * chartH;
    path += (i === 0 ? 'M' : 'L') + `${x},${y}`;
  });

  el.innerHTML = `
    <div class="chart-container">
      <div style="display:flex;gap:16px;margin-bottom:12px;font-size:11px;color:var(--text-dim)">
        <span>Total: ${data.total_tasks}</span>
        <span>Remaining: ${data.current_remaining}</span>
        <span>Velocity: ${data.velocity} done</span>
        <span>Ideal: -${data.ideal_per_day.toFixed(1)}/day</span>
      </div>
      <svg width="${w}" height="${ht}" style="width:100%;height:auto">
        ${Array.from({length: 5}, (_, i) => {
          const y = pad + (i / 4) * chartH;
          const val = Math.round(maxVal - (i / 4) * maxVal);
          return `<line x1="${pad}" y1="${y}" x2="${pad+chartW}" y2="${y}" stroke="var(--border)" stroke-dasharray="4"/>
                  <text x="${pad-5}" y="${y+4}" text-anchor="end" font-size="10" fill="var(--text-dim)">${val}</text>`;
        }).join('')}
        <line x1="${pad}" y1="${pad}" x2="${pad+chartW}" y2="${pad+chartH}" stroke="var(--text-dim)" stroke-dasharray="6" stroke-width="1.5"/>
        <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>
        ${data.points.map((p, i) => {
          const x = pad + i * stepX;
          const y = pad + chartH - (p.remaining / maxVal) * chartH;
          return `<circle cx="${x}" cy="${y}" r="3" fill="var(--accent)"/>`;
        }).join('')}
        ${data.points.filter((_, i) => i % Math.ceil(data.points.length / 7) === 0 || i === data.points.length - 1).map((p) => {
          const i = data.points.indexOf(p);
          const x = pad + i * stepX;
          return `<text x="${x}" y="${ht-8}" text-anchor="middle" font-size="9" fill="var(--text-dim)">${p.date}</text>`;
        }).join('')}
      </svg>
    </div>
  `;
}

// ─── Gantt Chart ──────────────────────────────────
async function loadGantt() {
  const data = await api('GET', '/api/gantt');
  const el = document.getElementById('gantt-content');
  const tasks = data.tasks || [];
  if (!tasks.length) {
    el.innerHTML = '<div class="empty">No tasks with dates to display</div>';
    return;
  }

  const DAY_W = 28;
  const ROW_H = 32;
  const LABEL_W = 220;
  const HEADER_H = 28;
  const PAD = 12;
  const ARROW_W = 60;

  function parseDmy(s) {
    if (!s) return null;
    const p = s.split('/');
    return new Date(+p[2], +p[1] - 1, +p[0]);
  }

  function toDays(d) {
    return Math.floor(d.getTime() / 86400000);
  }

  const start = parseDmy(data.range_start);
  const end = parseDmy(data.range_end);
  if (!start || !end) { el.innerHTML = '<div class="empty">Invalid date range</div>'; return; }

  const totalDays = Math.max(toDays(end) - toDays(start) + 1, 1);
  const svgW = LABEL_W + totalDays * DAY_W + PAD * 2 + ARROW_W;
  const svgH = HEADER_H + tasks.length * ROW_H + PAD * 2;

  const stateColors = {
    'DONE': '#9ece6a', 'CANCELLED': '#f7768e',
    'IN PROGRESS': '#7aa2f7', 'WAITING': '#e0af68',
    'BACKLOG': '#565f89',
  };
  function barColor(s) { return stateColors[s] || '#7aa2f7'; }

  // Build task lookup by title
  const taskMap = {};
  tasks.forEach(t => { taskMap[t.title.toLowerCase()] = t; });

  // Pre-compute bar positions
  const bars = tasks.map((t, i) => {
    const tStart = parseDmy(t.start_date) || start;
    const tEnd = parseDmy(t.end_date) || end;
    const x1 = Math.max(0, toDays(tStart) - toDays(start));
    const x2 = Math.max(x1 + 1, toDays(tEnd) - toDays(start));
    const y = HEADER_H + PAD + i * ROW_H;
    return { ...t, x1, x2, y };
  });

  // Build SVG
  let svg = `<svg viewBox="0 0 ${svgW} ${svgH}" style="width:100%;height:auto;min-height:${svgH}px;font-family:system-ui,sans-serif;overflow:visible">`;

  // Grid lines
  for (let d = 0; d < totalDays; d++) {
    const x = LABEL_W + PAD + d * DAY_W;
    const isWeekend = (start.getDay() + d) % 7 === 0 || (start.getDay() + d) % 7 === 6;
    svg += `<line x1="${x}" y1="0" x2="${x}" y2="${svgH}" stroke="${isWeekend ? 'rgba(255,255,255,0.04)' : 'rgba(255,255,255,0.02)'}" stroke-width="1"/>`;
    // Day header
    const date = new Date(start);
    date.setDate(date.getDate() + d);
    svg += `<text x="${x + DAY_W/2}" y="${HEADER_H - 8}" text-anchor="middle" font-size="9" fill="var(--text-dim)">${String(date.getDate()).padStart(2,'0')}/${String(date.getMonth()+1).padStart(2,'0')}</text>`;
  }

  // Row backgrounds
  bars.forEach((b, i) => {
    const y = b.y;
    if (i % 2 === 0) {
      svg += `<rect x="0" y="${y}" width="${svgW}" height="${ROW_H}" fill="rgba(255,255,255,0.02)"/>`;
    }
  });

  // Dependency arrows (draw first so they're behind bars)
  bars.forEach(b => {
    b.blocked_by.forEach(blockerTitle => {
      const blocker = taskMap[blockerTitle.toLowerCase().trim()];
      if (!blocker) return;
      const bi = tasks.findIndex(t => t.id === blocker.id);
      if (bi < 0) return;
      const bBar = bars[bi];
      const x1 = LABEL_W + PAD + bBar.x2 * DAY_W;
      const y1 = bBar.y + ROW_H / 2;
      const x2 = LABEL_W + PAD + b.x1 * DAY_W;
      const y2 = b.y + ROW_H / 2;
      if (x2 - x1 < 10) return;
      const cx = (x1 + x2) / 2;
      svg += `<path d="M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}" fill="none" stroke="var(--red)" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.5"/>`;
      svg += `<polygon points="${x2},${y2} ${x2-6},${y2-4} ${x2-6},${y2+4}" fill="var(--red)" opacity="0.5"/>`;
    });
  });

  // Task bars
  bars.forEach(b => {
    const x = LABEL_W + PAD + b.x1 * DAY_W;
    const w = Math.max(4, (b.x2 - b.x1) * DAY_W);
    const y = b.y + 6;
    const h = ROW_H - 12;
    const color = barColor(b.state);
    svg += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4" fill="${color}" opacity="0.85">`;
    svg += `<title>${h(b.title)}\n${b.state}${b.priority ? ' | ' + b.priority : ''}\n${b.start_date || '?'} → ${b.end_date || '?'}${b.blocked_by.length ? '\nBlocked by: ' + b.blocked_by.join(', ') : ''}${b.blocks.length ? '\nBlocks: ' + b.blocks.join(', ') : ''}</title>`;
    svg += `</rect>`;
    // Task label
    svg += `<text x="${LABEL_W - 8}" y="${b.y + ROW_H/2 + 1}" text-anchor="end" font-size="11" fill="var(--text)" dominant-baseline="middle">${h(b.title)}</text>`;
  });

  svg += '</svg>';

  el.innerHTML = `
    <div style="overflow-x:auto;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg-surface);padding:4px">
      ${svg}
    </div>
    <div style="margin-top:8px;display:flex;gap:12px;font-size:10px;color:var(--text-dim);flex-wrap:wrap">
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#9ece6a;vertical-align:middle;margin-right:3px"></span> Done</span>
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#7aa2f7;vertical-align:middle;margin-right:3px"></span> In Progress</span>
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#e0af68;vertical-align:middle;margin-right:3px"></span> Waiting</span>
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#565f89;vertical-align:middle;margin-right:3px"></span> Backlog</span>
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#f7768e;vertical-align:middle;margin-right:3px"></span> Cancelled</span>
      <span style="margin-left:8px;opacity:0.6">${'—'.repeat(3)} red dashed = dependency</span>
    </div>
    <div style="margin-top:8px;font-size:10px;color:var(--text-dim)">${tasks.length} task(s) · ${data.range_start} → ${data.range_end}</div>
  `;
}

// ─── Tags View ──────────────────────────────────
async function loadTags() {
  const data = await api('GET', '/api/tags');
  const el = document.getElementById('tags-content');
  document.getElementById('tags-tasks-section').style.display = 'none';

  if (!data.tags || Object.keys(data.tags).length === 0) {
    el.innerHTML = '<div class="empty">No tags found</div>';
    return;
  }

  const sorted = Object.entries(data.tags).sort((a, b) => b[1] - a[1]);
  el.innerHTML = `
    <div class="tags-cloud">
      ${sorted.map(([tag, count]) => `
        <span class="tag-chip" onclick="openTagTasks('${tag}')">#${tag}<span class="tag-count">${count}</span></span>
      `).join('')}
    </div>
  `;
}

async function openTagTasks(tag) {
  const data = await api('GET', `/api/tags/tasks?tag=${encodeURIComponent(tag)}`);
  const section = document.getElementById('tags-tasks-section');
  document.getElementById('tags-tasks-title').textContent = '#' + tag;

  const list = document.getElementById('tags-tasks-list');
  if (!data.tasks || data.tasks.length === 0) {
    list.innerHTML = '<div class="empty">No tasks with this tag</div>';
  } else {
    cacheTasks(data.tasks);
    list.innerHTML = taskListHtml(data.tasks);
  }
  section.style.display = 'block';
  section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ─── Time Tracking ──────────────────────────────
async function loadTime() {
  const data = await api('GET', '/api/time');
  const el = document.getElementById('time-content');

  el.innerHTML = `
    <div class="time-toolbar">
      <button class="btn-sm" onclick="showLogTimeForm()">+ Log Time</button>
    </div>
    ${data.tasks && data.tasks.length ? `
      <div class="time-total">Total time: <strong>${data.total_formatted}</strong></div>
      ${data.tasks.map(item => `
        <div class="time-item">
          <span class="time-task-title">#${item.task.id} ${h(item.task.title)}</span>
          <span class="time-value">${item.formatted}</span>
          <button class="btn-xs" onclick="showLogTimeForm('${item.task.id}')">+</button>
        </div>
      `).join('')}
    ` : '<div class="empty">No time tracked</div>'}
  `;
}

function showLogTimeForm(prefillId = '') {
  // Populate task selector
  const select = document.getElementById('logtime-task');
  const allTasks = Object.values(taskCache);
  select.innerHTML = allTasks.map(t =>
    `<option value="${t.id}" ${t.id === prefillId ? 'selected' : ''}>#${t.id} — ${h(t.title).substring(0, 50)}</option>`
  ).join('');
  // If no tasks in cache, fetch them
  if (!allTasks.length) {
    api('GET', '/api/tasks').then(data => {
      cacheTasks(data.tasks);
      select.innerHTML = data.tasks.map(t =>
        `<option value="${t.id}" ${t.id === prefillId ? 'selected' : ''}>#${t.id} — ${h(t.title).substring(0, 50)}</option>`
      ).join('');
    });
  }
  document.getElementById('logtime-value').value = '';
  document.getElementById('modal-logtime').classList.add('open');
  document.getElementById('logtime-value').focus();
}

async function submitLogTime() {
  const taskId = document.getElementById('logtime-task').value;
  const timeStr = document.getElementById('logtime-value').value.trim();
  if (!taskId || !timeStr) return;
  const data = await api('POST', `/api/tasks/${taskId}/time`, { time: timeStr });
  if (data.ok) {
    closeModal('modal-logtime');
    loadView('time');
  }
}

// ─── Pomodoro Timer ─────────────────────────────
let pomoState = {
  running: false,
  mode: 'work',        // 'work' | 'break' | 'longbreak'
  secondsLeft: 25 * 60,
  interval: null,
  completedPomos: 0,   // in current cycle (resets after 4)
  workMin: 25,
  breakMin: 5,
  longMin: 15,
  sessions: [],        // { task, taskTitle, mode, duration, endedAt }
};

function loadPomodoro() {
  // Populate task selector
  const select = document.getElementById('pomo-task');
  const tasks = Object.values(taskCache);
  const buildOpts = (ts) => '<option value="">(no task — free focus)</option>' +
    ts.map(t => `<option value="${t.id}">#${t.id} — ${h(t.title).substring(0, 45)}</option>`).join('');
  if (tasks.length) { select.innerHTML = buildOpts(tasks); }
  else { api('GET', '/api/tasks').then(data => { cacheTasks(data.tasks); select.innerHTML = buildOpts(data.tasks); }); }
  pomoRenderDisplay();
  pomoRenderDots();
  pomoRenderHistory();
}

function pomoUpdateSettings() {
  pomoState.workMin = parseInt(document.getElementById('pomo-work-min').value) || 25;
  pomoState.breakMin = parseInt(document.getElementById('pomo-break-min').value) || 5;
  pomoState.longMin = parseInt(document.getElementById('pomo-long-min').value) || 15;
  if (!pomoState.running) {
    pomoState.secondsLeft = pomoState.workMin * 60;
    pomoRenderDisplay();
  }
}

function pomoStartPause() {
  if (pomoState.running) {
    // Pause
    clearInterval(pomoState.interval);
    pomoState.running = false;
    document.getElementById('pomo-start-btn').textContent = 'Resume';
  } else {
    // Start / Resume
    pomoState.running = true;
    document.getElementById('pomo-start-btn').textContent = 'Pause';
    pomoState.interval = setInterval(pomoTick, 1000);
    // Request notification permission
    if (Notification && Notification.permission === 'default') Notification.requestPermission();
  }
}

function pomoTick() {
  pomoState.secondsLeft--;
  pomoRenderDisplay();
  if (pomoState.secondsLeft <= 0) {
    clearInterval(pomoState.interval);
    pomoState.running = false;
    document.getElementById('pomo-start-btn').textContent = 'Start';
    pomoComplete();
  }
}

function pomoComplete() {
  const wasWork = pomoState.mode === 'work';
  const duration = wasWork ? pomoState.workMin : (pomoState.mode === 'break' ? pomoState.breakMin : pomoState.longMin);

  // Record session
  const taskId = document.getElementById('pomo-task').value;
  const taskTitle = taskId ? (taskCache[taskId] ? taskCache[taskId].title : `#${taskId}`) : '';
  pomoState.sessions.unshift({ task: taskId, taskTitle, mode: pomoState.mode, duration, endedAt: new Date() });
  pomoRenderHistory();

  // Auto-log time if work session + task selected + checkbox on
  if (wasWork && taskId && document.getElementById('pomo-autolog').checked) {
    api('POST', `/api/tasks/${taskId}/time`, { time: `${duration}m` });
  }

  // Notify
  pomoNotify(wasWork ? 'Work session complete!' : 'Break is over — back to work!');

  // Transition mode
  if (wasWork) {
    pomoState.completedPomos++;
    pomoRenderDots();
    if (pomoState.completedPomos >= 4) {
      pomoState.completedPomos = 0;
      pomoState.mode = 'longbreak';
      pomoState.secondsLeft = pomoState.longMin * 60;
    } else {
      pomoState.mode = 'break';
      pomoState.secondsLeft = pomoState.breakMin * 60;
    }
  } else {
    pomoState.mode = 'work';
    pomoState.secondsLeft = pomoState.workMin * 60;
  }
  pomoRenderDisplay();
  pomoRenderDots();
}

function pomoReset() {
  clearInterval(pomoState.interval);
  pomoState.running = false;
  pomoState.mode = 'work';
  pomoState.secondsLeft = pomoState.workMin * 60;
  pomoState.completedPomos = 0;
  document.getElementById('pomo-start-btn').textContent = 'Start';
  pomoRenderDisplay();
  pomoRenderDots();
}

function pomoSkip() {
  clearInterval(pomoState.interval);
  pomoState.running = false;
  document.getElementById('pomo-start-btn').textContent = 'Start';
  // Move to next mode without logging
  if (pomoState.mode === 'work') {
    pomoState.completedPomos++;
    pomoRenderDots();
    if (pomoState.completedPomos >= 4) {
      pomoState.completedPomos = 0;
      pomoState.mode = 'longbreak';
      pomoState.secondsLeft = pomoState.longMin * 60;
    } else {
      pomoState.mode = 'break';
      pomoState.secondsLeft = pomoState.breakMin * 60;
    }
  } else {
    pomoState.mode = 'work';
    pomoState.secondsLeft = pomoState.workMin * 60;
  }
  pomoRenderDisplay();
  pomoRenderDots();
}

function pomoRenderDisplay() {
  const min = Math.floor(pomoState.secondsLeft / 60);
  const sec = pomoState.secondsLeft % 60;
  const display = document.getElementById('pomo-display');
  display.textContent = `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  display.className = pomoState.mode === 'work' ? 'pomo-work' : 'pomo-break';
  document.getElementById('pomo-mode').textContent = pomoState.mode === 'work' ? 'WORK' : pomoState.mode === 'break' ? 'SHORT BREAK' : 'LONG BREAK';
  // Update page title with timer
  if (pomoState.running) {
    document.title = `${display.textContent} — Pomodoro | TTM`;
  } else {
    document.title = 'TextTaskManager';
  }
}

function pomoRenderDots() {
  const dots = document.querySelectorAll('.pomo-dot');
  dots.forEach((dot, i) => {
    dot.classList.toggle('filled', i < pomoState.completedPomos);
  });
}

function pomoRenderHistory() {
  const el = document.getElementById('pomo-history');
  if (!pomoState.sessions.length) { el.innerHTML = '<span style="color:var(--text-dim)">No sessions yet</span>'; return; }
  el.innerHTML = pomoState.sessions.slice(0, 15).map(s => {
    const time = s.endedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const modeLabel = s.mode === 'work' ? 'Work' : s.mode === 'break' ? 'Break' : 'Long Break';
    const taskLabel = s.taskTitle ? ` — ${h(s.taskTitle).substring(0, 30)}` : '';
    return `<div class="pomo-history-item"><span>${time} · ${modeLabel} (${s.duration}m)${taskLabel}</span></div>`;
  }).join('');
}

function pomoNotify(msg) {
  // Browser notification
  if (Notification && Notification.permission === 'granted') {
    new Notification('Pomodoro', { body: msg, icon: '🍅' });
  }
  // Also play a beep
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = 800;
    gain.gain.value = 0.3;
    osc.start(); osc.stop(ctx.currentTime + 0.3);
    setTimeout(() => { osc.start; }, 400); // double beep
    const osc2 = ctx.createOscillator();
    const gain2 = ctx.createGain();
    osc2.connect(gain2); gain2.connect(ctx.destination);
    osc2.frequency.value = 1000;
    gain2.gain.value = 0.3;
    osc2.start(ctx.currentTime + 0.4); osc2.stop(ctx.currentTime + 0.7);
  } catch(e) {}
}

// ─── Blockers View ──────────────────────────────
async function loadBlockers() {
  const data = await api('GET', '/api/blockers');
  const el = document.getElementById('blockers-content');

  el.innerHTML = `
    <div class="blocker-toolbar">
      <button class="btn-sm" onclick="showAddBlockerForm()">+ Add Blocker</button>
    </div>
    ${data.blockers && data.blockers.length ? data.blockers.map(b => `
      <div class="blocker-item ${b.is_blocked ? 'blocked' : ''}">
        <div class="blocker-header">
          <span class="blocker-title">#${b.task.id} ${h(b.task.title)}</span>
        </div>
        <div class="blocker-deps">
          ${b.blocked_by.length ? b.blocked_by.map(name => `
            <span class="blocker-dep dep-blocked-by">
              Blocked by: ${h(name)}
              <span class="dep-remove" onclick="event.stopPropagation();removeBlocker('${b.task.id}','${h(name).replace(/'/g,"\\'")}')">x</span>
            </span>
          `).join('') : ''}
          ${b.blocks.length ? b.blocks.map(name => `
            <span class="blocker-dep dep-blocks">
              Blocks: ${h(name)}
              <span class="dep-remove" onclick="event.stopPropagation();removeBlock('${b.task.id}','${h(name).replace(/'/g,"\\'")}')">x</span>
            </span>
          `).join('') : ''}
        </div>
      </div>
    `).join('') : '<div class="empty">No blockers</div>'}
  `;
}

// Blocker search state
let blockerTasksCache = [];
let blockerSearchSelectedIndex = { blocked: -1, blocker: -1 };

function showAddBlockerForm() {
  // Reset state
  document.getElementById('blocker-blocked-input').value = '';
  document.getElementById('blocker-blocker-input').value = '';
  document.getElementById('blocker-blocked-task').value = '';
  document.getElementById('blocker-blocker-task').value = '';
  document.getElementById('blocker-blocked-results').classList.remove('open');
  document.getElementById('blocker-blocker-results').classList.remove('open');
  document.getElementById('blocker-blocked-selected').classList.remove('has-selection');
  document.getElementById('blocker-blocker-selected').classList.remove('has-selection');
  document.getElementById('blocker-blocked-selected').innerHTML = '';
  document.getElementById('blocker-blocker-selected').innerHTML = '';
  blockerSearchSelectedIndex = { blocked: -1, blocker: -1 };

  // Ensure we have tasks cached
  const allTasks = Object.values(taskCache);
  if (allTasks.length) {
    blockerTasksCache = allTasks;
  } else {
    api('GET', '/api/tasks').then(data => {
      cacheTasks(data.tasks);
      blockerTasksCache = data.tasks;
    });
  }

  // Setup event listeners
  setupBlockerSearchInput('blocked');
  setupBlockerSearchInput('blocker');

  document.getElementById('modal-blocker').classList.add('open');
  document.getElementById('blocker-blocked-input').focus();
}

function setupBlockerSearchInput(type) {
  const input = document.getElementById(`blocker-${type}-input`);
  const results = document.getElementById(`blocker-${type}-results`);

  // Remove old listeners by cloning
  const newInput = input.cloneNode(true);
  input.parentNode.replaceChild(newInput, input);

  newInput.addEventListener('input', () => {
    const query = newInput.value.trim().toLowerCase();
    if (query.length === 0) {
      results.classList.remove('open');
      return;
    }
    const filtered = blockerTasksCache.filter(t =>
      t.title.toLowerCase().includes(query) ||
      String(t.id).includes(query)
    ).slice(0, 10);

    if (filtered.length === 0) {
      results.innerHTML = '<div class="task-search-result" style="color:var(--text-dim)">No tasks found</div>';
    } else {
      results.innerHTML = filtered.map((t, i) => `
        <div class="task-search-result${i === 0 ? ' selected' : ''}" data-id="${t.id}" data-title="${h(t.title)}">
          <span class="tsr-id">#${t.id}</span>
          <span class="tsr-state state-${t.state.toLowerCase().replace(/\s+/g, '-')}">${t.state}</span>
          ${h(t.title).substring(0, 40)}
        </div>
      `).join('');

      // Add click handlers
      results.querySelectorAll('.task-search-result[data-id]').forEach(el => {
        el.addEventListener('click', () => selectBlockerTask(type, el.dataset.id, el.dataset.title));
      });
    }
    blockerSearchSelectedIndex[type] = filtered.length > 0 ? 0 : -1;
    results.classList.add('open');
  });

  newInput.addEventListener('keydown', (e) => {
    const items = results.querySelectorAll('.task-search-result[data-id]');
    if (!items.length) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      blockerSearchSelectedIndex[type] = Math.min(blockerSearchSelectedIndex[type] + 1, items.length - 1);
      updateBlockerSearchSelection(type, items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      blockerSearchSelectedIndex[type] = Math.max(blockerSearchSelectedIndex[type] - 1, 0);
      updateBlockerSearchSelection(type, items);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const idx = blockerSearchSelectedIndex[type];
      if (idx >= 0 && items[idx]) {
        selectBlockerTask(type, items[idx].dataset.id, items[idx].dataset.title);
      }
    } else if (e.key === 'Escape') {
      results.classList.remove('open');
    }
  });

  newInput.addEventListener('blur', () => {
    // Delay to allow click on results
    setTimeout(() => results.classList.remove('open'), 150);
  });

  newInput.addEventListener('focus', () => {
    if (newInput.value.trim().length > 0) {
      results.classList.add('open');
    }
  });
}

function updateBlockerSearchSelection(type, items) {
  items.forEach((el, i) => {
    el.classList.toggle('selected', i === blockerSearchSelectedIndex[type]);
  });
  // Scroll into view
  const idx = blockerSearchSelectedIndex[type];
  if (items[idx]) {
    items[idx].scrollIntoView({ block: 'nearest' });
  }
}

function selectBlockerTask(type, id, title) {
  document.getElementById(`blocker-${type}-task`).value = id;
  document.getElementById(`blocker-${type}-input`).value = '';
  document.getElementById(`blocker-${type}-results`).classList.remove('open');

  const selectedEl = document.getElementById(`blocker-${type}-selected`);
  selectedEl.innerHTML = `
    <span class="tss-label">Selected:</span>
    <span class="tss-task"><strong>#${id}</strong> ${h(title).substring(0, 35)}</span>
    <span class="tss-clear" onclick="clearBlockerSelection('${type}')">&times;</span>
  `;
  selectedEl.classList.add('has-selection');

  // Focus next input if first selection
  if (type === 'blocked' && !document.getElementById('blocker-blocker-task').value) {
    document.getElementById('blocker-blocker-input').focus();
  }
}

function clearBlockerSelection(type) {
  document.getElementById(`blocker-${type}-task`).value = '';
  document.getElementById(`blocker-${type}-selected`).classList.remove('has-selection');
  document.getElementById(`blocker-${type}-selected`).innerHTML = '';
  document.getElementById(`blocker-${type}-input`).focus();
}

async function submitAddBlocker() {
  const blockedId = document.getElementById('blocker-blocked-task').value;
  const blockerId = document.getElementById('blocker-blocker-task').value;
  if (!blockedId || !blockerId) {
    alert('Please select both tasks');
    return;
  }
  if (blockedId === blockerId) { alert('A task cannot block itself'); return; }
  const data = await api('POST', '/api/blockers/add', { blocked_id: blockedId, blocker_id: blockerId });
  if (data.ok) {
    closeModal('modal-blocker');
    loadView('blockers');
  }
}

async function removeBlocker(blockedId, blockerTitle) {
  if (!confirm(`Remove blocker "${blockerTitle}" from task #${blockedId}?`)) return;
  // Need to find the blocker task by title to get its ID
  const tasks = await api('GET', '/api/tasks');
  const blocker = tasks.tasks.find(t => t.title.replace(/#\w+/g,'').trim() === blockerTitle || t.title.includes(blockerTitle));
  if (!blocker) { alert('Could not find blocker task'); return; }
  const data = await api('POST', '/api/blockers/delete', { blocked_id: blockedId, blocker_id: blocker.id });
  if (data.ok) loadView('blockers');
}

async function removeBlock(blockerId, blockedTitle) {
  if (!confirm(`Remove "blocks ${blockedTitle}" from task #${blockerId}?`)) return;
  const tasks = await api('GET', '/api/tasks');
  const blocked = tasks.tasks.find(t => t.title.replace(/#\w+/g,'').trim() === blockedTitle || t.title.includes(blockedTitle));
  if (!blocked) { alert('Could not find blocked task'); return; }
  const data = await api('POST', '/api/blockers/delete', { blocked_id: blocked.id, blocker_id: blockerId });
  if (data.ok) loadView('blockers');
}

// ─── Jira View ──────────────────────────────────
function jiraStatusClass(status) {
  const s = (status || '').toLowerCase();
  if (s.includes('done') || s.includes('closed') || s.includes('resolved')) return 'js-done';
  if (s.includes('backlog')) return 'js-todo';
  if (s.includes('progress') || s.includes('development') || s.includes('refining')) return 'js-progress';
  if (s.includes('review')) return 'js-review';
  if (s.includes('hold') || s.includes('waiting') || s.includes('blocked')) return 'js-hold';
  if (s.includes('cancel') || s.includes('discard')) return 'js-cancel';
  if (s.includes('qa') || s.includes('test')) return 'js-qa';
  return 'js-todo';
}

const JIRA_FILTER_LABELS = {
  active: 'Active', todo: 'To Do', progress: 'In Progress',
  review: 'In Review', blocked: 'Blocked', overdue: 'Overdue',
  done: 'Done', cancelled: 'Cancelled', notify: 'Notifications'
};
let jiraBaseUrl = '';
const jiraTransitionCache = {}; // cache by status name

// Chip click handlers
document.getElementById('jira-filters')?.addEventListener('click', (e) => {
  const chip = e.target.closest('.jira-chip');
  if (!chip) return;
  document.querySelectorAll('.jira-chip').forEach(c => c.classList.remove('active'));
  chip.classList.add('active');
  currentJiraFilter = chip.dataset.jiraF;
  loadJira();
});

async function loadJira() {
  const filter = currentJiraFilter;

  // If sidebar says "notify", hide filters bar, show notifications
  const filtersBar = document.getElementById('jira-filters');
  if (filter === 'notify') {
    filtersBar.style.display = 'none';
    document.getElementById('jira-view-title').textContent = 'Notifications';
  } else {
    filtersBar.style.display = 'flex';
    document.getElementById('jira-view-title').textContent = 'Jira Tasks';
  }

  const el = document.getElementById('jira-content');
  el.innerHTML = '<div class="empty">Loading...</div>';

  const data = await api('GET', `/api/jira?filter=${filter}`);

  if (!data.configured) {
    el.innerHTML = '<div class="empty">Jira not configured. Run <code>config jira</code> in the CLI.</div>';
    return;
  }
  if (data.error) {
    el.innerHTML = `<div class="empty">Error: ${h(data.error)}</div>`;
    return;
  }

  // Store base_url for "Open in Jira" links
  if (data.base_url) jiraBaseUrl = data.base_url;

  // Handle notifications view
  if (filter === 'notify') {
    renderJiraNotifications(data.notifications || []);
    return;
  }

  // Issue list view
  if (!data.issues || data.issues.length === 0) {
    const label = JIRA_FILTER_LABELS[filter] || 'active';
    el.innerHTML = `<div class="empty">No ${label.toLowerCase()} issues</div>`;
    return;
  }

  el.innerHTML = data.issues.map(issue => `
    <div class="jira-issue">
      <span class="jira-key">${issue.key}</span>
      <span class="jira-status ${jiraStatusClass(issue.status)}" onclick="event.stopPropagation();toggleJiraStatus(this, '${issue.key}', '${h(issue.status || '')}')">${issue.status || ''}</span>
      <span class="jira-title">${h(issue.summary || '')}</span>
      <div class="jira-actions">
        <button class="jira-btn jira-btn-open" onclick="openInJira('${issue.key}')" title="Open in browser">Open</button>
        <button class="jira-btn jira-btn-assign" onclick="assignJiraToTask('${issue.key}', '${h(issue.summary || '').replace(/'/g, "\\'")}')" title="Link to local task">Assign</button>
      </div>
    </div>
  `).join('');
}

function openInJira(key) {
  if (!jiraBaseUrl) { alert('Jira URL not available'); return; }
  const url = jiraBaseUrl.replace(/\/$/, '') + '/browse/' + key;
  window.open(url, '_blank');
}

async function assignJiraToTask(jiraKey, summary) {
  document.getElementById('jira-assign-key').value = jiraKey;
  document.getElementById('jira-assign-summary').value = summary;
  document.getElementById('jira-assign-title').textContent = `Assign ${jiraKey}`;
  document.getElementById('jira-assign-desc').textContent = summary;
  // Populate task selector
  const select = document.getElementById('jira-assign-task');
  const tasks = Object.values(taskCache);
  const buildOpts = (ts) => '<option value="">-- Select a task --</option>' +
    ts.map(t => `<option value="${t.id}">#${t.id} — ${h(t.title).substring(0, 40)}</option>`).join('');
  if (tasks.length) { select.innerHTML = buildOpts(tasks); }
  else { api('GET', '/api/tasks').then(data => { cacheTasks(data.tasks); select.innerHTML = buildOpts(data.tasks); }); }
  document.getElementById('modal-jira-assign').classList.add('open');
}

async function submitJiraAssignExisting() {
  const taskId = document.getElementById('jira-assign-task').value;
  const jiraKey = document.getElementById('jira-assign-key').value;
  if (!taskId) return;
  const data = await api('POST', `/api/tasks/${taskId}/edit`, { jira_key: jiraKey });
  if (data.ok || data.id) {
    closeModal('modal-jira-assign');
    loadView('tasks');
  }
}

async function submitJiraAssignNew() {
  const jiraKey = document.getElementById('jira-assign-key').value;
  const summary = document.getElementById('jira-assign-summary').value;
  const data = await api('POST', '/api/tasks', { title: summary, state: 'IN PROGRESS', jira_key: jiraKey });
  if (data.ok || data.id) {
    closeModal('modal-jira-assign');
    loadView('tasks');
  }
}

async function toggleJiraStatus(el, issueKey, currentStatus) {
  // Remove any existing dropdown
  document.querySelectorAll('.jira-move-dropdown').forEach(d => d.remove());

  // Use cached transitions if available for this status
  let transitions = jiraTransitionCache[currentStatus];
  if (!transitions) {
    const data = await api('GET', `/api/jira/transitions?key=${issueKey}`);
    transitions = data.transitions || [];
    if (currentStatus) jiraTransitionCache[currentStatus] = transitions;
  }

  if (transitions.length === 0) {
    alert('No transitions available for ' + issueKey);
    return;
  }

  // Create dropdown positioned relative to the status badge
  const dropdown = document.createElement('div');
  dropdown.className = 'jira-move-dropdown';
  dropdown.innerHTML = transitions.map(tr => `
    <div class="jira-move-option" onclick="event.stopPropagation();doJiraTransition('${issueKey}', '${tr.id}')">
      <span>${h(tr.name)}</span>
      ${tr.to && tr.to !== tr.name ? `<span style="color:var(--text-dim);font-size:10px">${h(tr.to)}</span>` : ''}
    </div>
  `).join('');

  el.appendChild(dropdown);

  // Close on outside click
  setTimeout(() => {
    document.addEventListener('click', function handler(e) {
      if (!dropdown.contains(e.target) && e.target !== el) {
        dropdown.remove();
        document.removeEventListener('click', handler);
      }
    });
  }, 0);
}

async function doJiraTransition(issueKey, transitionId) {
  document.querySelectorAll('.jira-move-dropdown').forEach(d => d.remove());

  const data = await api('POST', '/api/jira/transition', { key: issueKey, transition_id: transitionId });
  if (data.ok) {
    loadJira();
  } else {
    alert('Failed to move ' + issueKey + ': ' + (data.error || 'unknown error'));
  }
}

async function jiraSearch() {
  const query = document.getElementById('jira-search-input').value.trim();
  if (!query) return;

  const el = document.getElementById('jira-content');
  el.innerHTML = '<div class="empty">Searching...</div>';

  const data = await api('GET', `/api/jira?filter=find&q=${encodeURIComponent(query)}`);

  if (data.error) {
    el.innerHTML = `<div class="empty">Error: ${h(data.error)}</div>`;
    return;
  }
  if (data.base_url) jiraBaseUrl = data.base_url;
  if (!data.issues || data.issues.length === 0) {
    el.innerHTML = `<div class="empty">No results for "${h(query)}"</div>`;
    return;
  }

  el.innerHTML = `<p style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${data.issues.length} result(s) for "${h(query)}"</p>` +
    data.issues.map(issue => `
      <div class="jira-issue">
        <span class="jira-key">${issue.key}</span>
        <span class="jira-status ${jiraStatusClass(issue.status)}" onclick="event.stopPropagation();toggleJiraStatus(this, '${issue.key}', '${h(issue.status || '')}')">${issue.status || ''}</span>
        <span class="jira-title">${h(issue.summary || '')}</span>
        <div class="jira-actions">
          <button class="jira-btn jira-btn-open" onclick="openInJira('${issue.key}')" title="Open in browser">Open</button>
          <button class="jira-btn jira-btn-assign" onclick="assignJiraToTask('${issue.key}', '${h(issue.summary || '').replace(/'/g, "\\'")}')" title="Link to local task">Assign</button>
        </div>
      </div>
    `).join('');
}

function renderJiraNotifications(notifications) {
  const el = document.getElementById('jira-content');
  if (!notifications.length) {
    el.innerHTML = '<div class="empty">No unread notifications</div>';
    return;
  }

  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-size:11px;color:var(--text-dim)">
      <span>${notifications.length} unread</span>
      <button class="btn btn-sm" onclick="markAllJiraRead()" style="font-size:11px">Mark All Read</button>
    </div>
    ${notifications.map(n => `
    <div class="jira-notification">
      <div class="jn-header">
        <span class="jn-key" style="cursor:pointer" onclick="openInJira('${n.key}')">${n.key}</span>
        <span class="jn-summary">${h(n.summary)}</span>
        <span class="jn-reason">${n.reason}</span>
      </div>
      <div class="jn-body">${h(n.body)}</div>
      <div class="jn-meta">
        <span class="author">${h(n.author)}</span> &mdash; ${n.date}
        <button class="jn-mark-btn" onclick="markJiraRead('${n.id}')" title="Mark as read">✓</button>
      </div>
    </div>
  `).join('')}`;
}

async function markAllJiraRead() {
  const data = await api('POST', '/api/jira/mark-all');
  if (data.ok) { loadJira(); }
  else { alert('Failed to mark all as read: ' + (data.error || 'unknown error')); }
}

async function markJiraRead(id) {
  const data = await api('POST', '/api/jira/mark', { ids: [id] });
  if (data.ok) { loadJira(); }
  else { alert('Failed to mark as read: ' + (data.error || 'unknown error')); }
}

// ─── State Dropdown ─────────────────────────────
function toggleStateDropdown(el, taskId, currentState) {
  document.querySelectorAll('.state-dropdown').forEach(d => d.remove());

  const dd = document.createElement('div');
  dd.className = 'state-dropdown open';
  dd.innerHTML = STATES.map(s => `
    <div class="option${s === currentState ? ' current' : ''}"
         onclick="event.stopPropagation();changeState('${taskId}','${s}');this.parentElement.remove()">
      ${s}
    </div>
  `).join('');

  el.style.position = 'relative';
  el.appendChild(dd);

  setTimeout(() => {
    document.addEventListener('click', function handler() {
      dd.remove();
      document.removeEventListener('click', handler);
    }, { once: true });
  }, 0);
}

async function changeState(taskId, state) {
  await api('POST', `/api/tasks/${taskId}/state`, { state });
  loadView(currentView);
}

// ─── Sync View ──────────────────────────────────
async function loadSync() {
  const el = document.getElementById('sync-content');
  el.innerHTML = '<div class="empty">Loading...</div>';
  
  const data = await api('GET', '/api/sync/status');
  if (!data) {
    el.innerHTML = '<div class="empty">Failed to load sync status</div>';
    return;
  }
  
  if (!data.configured) {
    el.innerHTML = `
      <div class="sync-not-configured">
        <div class="sync-not-configured-icon">⚠</div>
        <h3>Sync not configured</h3>
        <p>Sync keeps your journals backed up to a private git repository,<br>
        allowing you to access them from multiple machines.</p>
        <p style="font-size:12px;color:var(--text-dim)">Configure in: <strong>Config → Sync / Git</strong></p>
      </div>
    `;
    return;
  }
  
  // Status icon and text
  let statusIcon, statusClass, statusText;
  switch (data.status) {
    case 'up_to_date':
      statusIcon = '✓';
      statusClass = 'ok';
      statusText = 'Up to date';
      break;
    case 'pending':
      statusIcon = '●';
      statusClass = 'pending';
      statusText = `Pending changes (${data.pending_files} file${data.pending_files !== 1 ? 's' : ''})`;
      break;
    case 'offline':
      statusIcon = '○';
      statusClass = 'offline';
      statusText = 'Offline';
      break;
    default:
      statusIcon = '✗';
      statusClass = 'error';
      statusText = 'Error';
  }
  
  // Format history
  let historyHtml = '';
  if (data.history && data.history.length > 0) {
    historyHtml = data.history.map(item => {
      // Parse and format date
      let dateStr = item.date;
      try {
        const d = new Date(item.date);
        dateStr = d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
      } catch (e) {}
      
      return `
        <div class="sync-history-item">
          <span class="sync-history-hash">${h(item.hash)}</span>
          <span class="sync-history-msg">${h(item.message)}</span>
          <span class="sync-history-date">${h(dateStr)}</span>
        </div>
      `;
    }).join('');
  } else {
    historyHtml = '<div class="empty">No sync history yet</div>';
  }
  
  el.innerHTML = `
    <div class="sync-container">
      <div class="sync-card">
        <h3>Status</h3>
        <div class="sync-status-row">
          <span class="sync-status-icon ${statusClass}">${statusIcon}</span>
          <span class="sync-status-text">${statusText}</span>
        </div>
        ${data.last_error ? `<div style="color:var(--red);font-size:12px;margin-bottom:12px">${h(data.last_error)}</div>` : ''}
        <div class="sync-field">
          <label>Remote</label>
          <input type="text" id="sync-remote" value="${h(data.remote || '')}" placeholder="https://github.com/user/repo.git">
        </div>
        <div class="sync-field">
          <label>Branch</label>
          <input type="text" id="sync-branch" value="${h(data.branch || 'main')}" placeholder="main">
        </div>
        <div class="sync-actions" style="flex-wrap:wrap">
          <div style="display:flex;gap:4px;align-items:center">
            <span style="font-size:11px;color:var(--text-dim);margin-right:4px">Pull:</span>
            <button class="btn btn-sm" onclick="syncPull('keep_local')" title="Commit local changes, then rebase on remote">Keep Local</button>
            <button class="btn btn-sm" onclick="syncPull('use_remote')" title="Discard local changes, use remote version">Use Remote</button>
            <button class="btn btn-sm" onclick="syncPull('merge')" title="Merge remote changes with local">Merge</button>
          </div>
          <div style="display:flex;gap:4px;align-items:center">
            <button class="btn btn-primary btn-sm" onclick="syncNow()" title="Push local changes to remote">Push</button>
            <button class="btn btn-sm" onclick="saveSyncSettings()">Save</button>
          </div>
        </div>
      </div>
      
      <div class="sync-card">
        <h3>Recent History</h3>
        ${historyHtml}
      </div>
    </div>
  `;
}

async function syncPull(strategy) {
  const btn = event.target;
  const original = btn.textContent;
  btn.textContent = '...';
  btn.disabled = true;
  try {
    const result = await api('POST', '/api/sync/pull', { strategy });
    if (result && result.success) {
      await loadSync();
    } else {
      alert('Pull failed: ' + (result?.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Pull failed: ' + e.message);
  }
  btn.textContent = original;
  btn.disabled = false;
}

async function syncNow() {
  const btn = event.target;
  const originalText = btn.textContent;
  btn.textContent = 'Pushing...';
  btn.disabled = true;
  
  try {
    const result = await api('POST', '/api/sync/push');
    if (result && result.success) {
      await loadSync();
    } else {
      alert('Push failed: ' + (result?.error || 'Unknown error'));
      btn.textContent = originalText;
      btn.disabled = false;
    }
  } catch (e) {
    alert('Push failed: ' + e.message);
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

async function saveSyncSettings() {
  const remote = document.getElementById('sync-remote').value.trim();
  const branch = document.getElementById('sync-branch').value.trim() || 'main';
  
  const result = await api('POST', '/api/sync/settings', { remote, branch });
  if (result && result.success) {
    await loadSync();
  } else {
    alert('Failed to save settings: ' + (result?.error || 'Unknown error'));
  }
}

// ─── Config View ────────────────────────────────
async function loadConfig() {
  const data = await api('GET', '/api/config');
  if (!data) return;

  const s = data.settings || {};
  const sec = data.secrets || {};

  // Populate Jira fields
  document.getElementById('cfg-jira-url').value = sec.jira_url || '';
  document.getElementById('cfg-jira-email').value = sec.jira_email || '';
  document.getElementById('cfg-jira-token').value = '';
  document.getElementById('cfg-jira-token-status').textContent = sec.jira_api_token ? '(set)' : '';
  document.getElementById('cfg-jira-account-id').value = sec.jira_account_id || '';

  // Populate Sync fields
  const sync = s.sync || {};
  document.getElementById('cfg-sync-enabled').value = sync.enabled ? 'true' : 'false';
  document.getElementById('cfg-sync-remote').value = sync.remote || '';
  document.getElementById('cfg-sync-branch').value = sync.branch || 'main';
  document.getElementById('cfg-sync-token').value = '';
  document.getElementById('cfg-sync-token-status').textContent = sec.sync_token ? '(set)' : '';

  // Populate General fields
  document.getElementById('cfg-date-format').value = s.date_format || '%d/%m/%Y';
  document.getElementById('cfg-agenda-days').value = s.agenda_days || 7;
  document.getElementById('cfg-default-state').value = s.default_state || 'BACKLOG';
  document.getElementById('cfg-default-priority').value = s.default_priority || '';
  document.getElementById('cfg-show-done').value = s.show_done_default ? 'true' : 'false';
  document.getElementById('cfg-weekly-report-days').value = s.weekly_report_days || 7;
  document.getElementById('cfg-max-undo').value = s.max_undo || 20;
  document.getElementById('cfg-prompt-format').value = s.prompt_format || '';

  // Populate States
  document.getElementById('cfg-states').value = (s.states || []).join(', ');
  document.getElementById('cfg-finished-states').value = (s.finished_states || []).join(', ');
  document.getElementById('cfg-progress-states').value = (s.progress_states || []).join(', ');
  document.getElementById('cfg-testing-states').value = (s.testing_states || []).join(', ');

  // Populate Priorities
  document.getElementById('cfg-priorities').value = (s.priorities || []).join(', ');

  // Populate Kanban
  document.getElementById('cfg-kanban-columns').value = (s.kanban_columns || []).join(', ');

  // Populate Sort
  document.getElementById('cfg-sort-by').value = s.sort_by || 'none';
  document.getElementById('cfg-sort-direction').value = s.sort_direction || 'asc';

  // Populate Email
  const email = s.email || {};
  document.getElementById('cfg-email-host').value = email.smtp_host || '';
  document.getElementById('cfg-email-port').value = email.smtp_port || 587;
  document.getElementById('cfg-email-user').value = email.smtp_user || '';
  document.getElementById('cfg-email-password').value = '';
  document.getElementById('cfg-email-from').value = email.from_address || '';
  document.getElementById('cfg-email-recipient').value = email.default_recipient || '';
  document.getElementById('cfg-email-prefix').value = email.subject_prefix || '[TaskManager]';

  // Populate Theme
  const themeSelect = document.getElementById('cfg-web-theme');
  if (themeSelect) themeSelect.value = s.web_theme || 'auto';

  // Apply server theme preference if user hasn't set one locally
  if (!localStorage.getItem('ttm-theme') && s.web_theme && s.web_theme !== 'auto') {
    applyTheme(s.web_theme);
  }

  // Desktop notifications checkbox
  const notifCb = document.getElementById('cfg-notif-enabled');
  if (notifCb) notifCb.checked = localStorage.getItem('ttm-notif-enabled') === 'true';

  document.getElementById('config-status').textContent = '';
}

async function saveConfig() {
  // Helper: split comma-separated string into trimmed array (filter blanks)
  const csvToArr = v => v.split(',').map(s => s.trim()).filter(Boolean);

  const payload = {
    settings: {
      sync: {
        enabled: document.getElementById('cfg-sync-enabled').value === 'true',
        remote: document.getElementById('cfg-sync-remote').value.trim(),
        branch: document.getElementById('cfg-sync-branch').value.trim() || 'main',
      },
      email: {
        smtp_host: document.getElementById('cfg-email-host').value.trim(),
        smtp_port: parseInt(document.getElementById('cfg-email-port').value) || 587,
        smtp_user: document.getElementById('cfg-email-user').value.trim(),
        smtp_password: document.getElementById('cfg-email-password').value.trim() || undefined,
        from_address: document.getElementById('cfg-email-from').value.trim(),
        default_recipient: document.getElementById('cfg-email-recipient').value.trim(),
        subject_prefix: document.getElementById('cfg-email-prefix').value.trim() || '[TaskManager]',
      },
      date_format: document.getElementById('cfg-date-format').value.trim(),
      agenda_days: parseInt(document.getElementById('cfg-agenda-days').value) || 7,
      default_state: document.getElementById('cfg-default-state').value.trim(),
      default_priority: document.getElementById('cfg-default-priority').value.trim() || null,
      show_done_default: document.getElementById('cfg-show-done').value === 'true',
      weekly_report_days: parseInt(document.getElementById('cfg-weekly-report-days').value) || 7,
      max_undo: parseInt(document.getElementById('cfg-max-undo').value) || 20,
      prompt_format: document.getElementById('cfg-prompt-format').value,
      web_theme: document.getElementById('cfg-web-theme').value,
      states: csvToArr(document.getElementById('cfg-states').value),
      finished_states: csvToArr(document.getElementById('cfg-finished-states').value),
      progress_states: csvToArr(document.getElementById('cfg-progress-states').value),
      testing_states: csvToArr(document.getElementById('cfg-testing-states').value),
      priorities: csvToArr(document.getElementById('cfg-priorities').value),
      kanban_columns: csvToArr(document.getElementById('cfg-kanban-columns').value),
      sort_by: document.getElementById('cfg-sort-by').value,
      sort_direction: document.getElementById('cfg-sort-direction').value,
    },
    secrets: {
      jira_url: document.getElementById('cfg-jira-url').value.trim(),
      jira_email: document.getElementById('cfg-jira-email').value.trim(),
      jira_api_token: document.getElementById('cfg-jira-token').value.trim(),
      jira_account_id: document.getElementById('cfg-jira-account-id').value.trim(),
      sync_token: document.getElementById('cfg-sync-token').value.trim(),
    },
  };

  // Don't send undefined smtp_password (keep existing)
  if (!payload.settings.email.smtp_password) delete payload.settings.email.smtp_password;

  const data = await api('POST', '/api/config', payload);
  const statusEl = document.getElementById('config-status');
  if (data.ok) {
    statusEl.textContent = 'Saved!';
    statusEl.style.color = 'var(--green)';
    // Apply theme change immediately
    const newTheme = document.getElementById('cfg-web-theme').value;
    if (newTheme === 'auto') {
      localStorage.removeItem('ttm-theme');
      applyTheme(window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    } else {
      localStorage.setItem('ttm-theme', newTheme);
      applyTheme(newTheme);
    }
  } else {
    statusEl.textContent = 'Error: ' + (data.errors || []).join(', ');
    statusEl.style.color = 'var(--red)';
  }
  setTimeout(() => { statusEl.textContent = ''; }, 3000);
}

// ─── Log View ───────────────────────────────────
async function loadLog() {
  const data = await api('GET', '/api/log');
  const el = document.getElementById('log-content');
  if (!data.entries || data.entries.length === 0) {
    el.innerHTML = '<div class="empty">No log entries yet</div>';
    return;
  }
  el.innerHTML = data.entries.slice().reverse().map(e => `
    <div class="log-entry">
      <span class="log-time">${e.time}</span>
      <span class="log-cat cat-${e.category}">${e.category}</span>
      <span class="log-msg">${h(e.message)}</span>
    </div>
  `).join('');
}

async function updateStatusBar() {
  const data = await api('GET', '/api/log');
  const bar = document.getElementById('status-bar');
  if (data.entries && data.entries.length > 0) {
    const last = data.entries[data.entries.length - 1];
    bar.textContent = `${last.time} [${last.category}] ${last.message}`;
  }
}

// Poll status bar every 10s
updateStatusBar();
setInterval(updateStatusBar, 10000);

// ─── Service Status Indicators ──────────────────
async function updateServiceStatus() {
  const data = await api('GET', '/api/status');
  const syncEl = document.querySelector('#ind-sync .ind-icon');
  const jiraEl = document.querySelector('#ind-jira .ind-icon');
  syncEl.textContent = data.sync ? '\u2713' : '\u2717';
  syncEl.className = 'ind-icon ' + (data.sync ? 'ind-ok' : 'ind-fail');
  jiraEl.textContent = data.jira ? '\u2713' : '\u2717';
  jiraEl.className = 'ind-icon ' + (data.jira ? 'ind-ok' : 'ind-fail');
}
updateServiceStatus();

// ─── Journal Selector ───────────────────────────
async function loadJournals() {
  const data = await api('GET', '/api/journals');
  if (!data || !data.journals) return;
  const sel = document.getElementById('journal-select');
  sel.innerHTML = data.journals.map(j =>
    `<option value="${h(j.name)}" ${j.current ? 'selected' : ''}>${h(j.stem)}</option>`
  ).join('');
}
async function switchJournal(name) {
  const data = await api('POST', '/api/journals/switch', { name });
  if (data && data.ok) {
    loadView(currentView);
  }
}
loadJournals();

// ─── Edit Modal ─────────────────────────────────
function openEditModal(taskId) {
  const task = taskCache[taskId];
  if (!task) return;

  document.getElementById('edit-task-id').value = taskId;
  document.getElementById('edit-title').value = stripTags(task.title);
  document.getElementById('edit-tags').value = (task.tags || []).join(', ');

  const stateEl = document.getElementById('edit-state');
  stateEl.innerHTML = STATES.map(s => `<option value="${s}" ${s === task.state ? 'selected' : ''}>${s}</option>`).join('');

  const priEl = document.getElementById('edit-priority');
  priEl.innerHTML = '<option value="">None</option>' + PRIORITIES.map(p => `<option value="${p}" ${p === task.priority ? 'selected' : ''}>${p}</option>`).join('');

  const dueEl = document.getElementById('edit-due');
  dueEl.value = task.due_date || '';
  dueEl.dispatchEvent(new Event('input'));

  const recEl = document.getElementById('edit-recurrence');
  recEl.value = task.recurrence || '';

  // Notes
  renderNotes(task.notes || []);

  // Linked notes
  renderEditLinkedNotes(task.linked_notes || []);

  // Subtasks
  renderSubtasksInModal(task.subtasks || []);

  document.getElementById('modal-edit').classList.add('open');
}

function renderNotes(notes) {
  const list = document.getElementById('edit-notes-list');
  list.innerHTML = notes.map((note, i) => `
    <li>
      <span class="note-text">${linkifyNote(note)}</span>
      <span class="note-edit" onclick="editNoteInModal(${i},'${h(note).replace(/'/g,"\\'")}')">edit</span>
      <span class="note-del" onclick="deleteNote(${i})">x</span>
    </li>
  `).join('');
}

function renderSubtasksInModal(subtasks) {
  const el = document.getElementById('edit-subtasks-list');
  if (!subtasks.length) {
    el.innerHTML = '<span style="font-size:11px;color:var(--text-dim)">No subtasks</span>';
    return;
  }
  el.innerHTML = subtasks.map(st => `
    <div class="subtask-row">
      <span class="st-state state-badge state-${st.state.replace(/ /g, '_')}">${st.state}</span>
      <span class="st-title">${h(st.title)}</span>
      <span class="st-edit" onclick="event.stopPropagation();openSubtaskModal('${st.id}')">edit</span>
      <span class="st-del" onclick="event.stopPropagation();deleteSubtaskFromModal('${st.id}')">x</span>
    </div>
  `).join('');
}

async function addNote() {
  const input = document.getElementById('edit-new-note');
  const note = input.value.trim();
  if (!note) return;

  const taskId = document.getElementById('edit-task-id').value;
  const data = await api('POST', `/api/tasks/${taskId}/notes`, { note });
  if (data.error) { alert(data.error); return; }
  if (data.notes) renderNotes(data.notes);
  if (data.id) taskCache[data.id] = data; (data.subtasks || []).forEach(st => subtaskCache[st.id] = st);
  const idx = allTasks.findIndex(t => t.id === data.id);
  if (idx >= 0) allTasks[idx] = data;
  input.value = '';
}

async function editNoteInModal(index, oldNote) {
  // Reuse the editnote modal but with a callback that refreshes the edit-task modal notes
  document.getElementById('editnote-task-id').value = document.getElementById('edit-task-id').value;
  document.getElementById('editnote-index').value = index;
  document.getElementById('editnote-text').value = oldNote;
  document.getElementById('modal-editnote').classList.add('open');
  document.getElementById('editnote-text').focus();
  // Override submit to refresh notes in edit modal
  window._editNoteCallback = async function() {
    const taskId = document.getElementById('editnote-task-id').value;
    const idx = parseInt(document.getElementById('editnote-index').value);
    const newNote = document.getElementById('editnote-text').value.trim();
    if (!newNote) return;
    const data = await api('POST', `/api/tasks/${taskId}/notes/edit`, { index: idx, note: newNote });
    if (data.notes) renderNotes(data.notes);
    if (data.id) { taskCache[data.id] = data; (data.subtasks || []).forEach(st => subtaskCache[st.id] = st); }
    closeModal('modal-editnote');
    window._editNoteCallback = null;
  };
}

// ─── Tag Suggestions ─────────────────────────────────
let cachedAllTags = null;

async function loadAllTags() {
  try {
    const data = await api('GET', '/api/tags');
    if (data && data.tags) {
      cachedAllTags = data.tags;
      return data.tags;
    }
  } catch (e) {
    console.error('Failed to load tags:', e);
  }
  return {};
}

function _renderTagSuggestions(suggestionsEl, inputEl, addFn) {
  const tags = cachedAllTags || {};
  let tagEntries = Object.entries(tags).sort((a, b) => b[1] - a[1]);
  // Filter by last typed word
  const parts = inputEl.value.split(',');
  const lastWord = (parts[parts.length - 1] || '').trim().toLowerCase();
  if (lastWord) {
    tagEntries = tagEntries.filter(([tag]) => tag.toLowerCase().includes(lastWord));
  }
  const currentTags = inputEl.value.split(',').map(t => t.trim().toLowerCase()).filter(Boolean);
  if (tagEntries.length === 0) {
    suggestionsEl.innerHTML = '<div class="tag-suggestions-header">No matching tags</div>';
  } else {
    suggestionsEl.innerHTML = `
      <div class="tag-suggestions-header">${lastWord ? 'Filtering by "' + h(lastWord) + '"' : 'Click to add existing tags'}</div>
      ${tagEntries.map(([tag, count]) => {
        const isAlreadyAdded = currentTags.includes(tag.toLowerCase());
        return `
          <div class="tag-suggestion-item ${isAlreadyAdded ? 'already-added' : ''}" 
               onclick="${addFn}('${tag.replace(/'/g, "\\'")}')">
            <span class="tag-name">${_highlightTag(tag, lastWord)}</span>
            <span class="tag-count">${count} task${count !== 1 ? 's' : ''}</span>
          </div>
        `;
      }).join('')}
    `;
  }
  suggestionsEl.classList.add('open');
}

function _highlightTag(tag, filter) {
  if (!filter) return '#' + tag;
  const idx = tag.toLowerCase().indexOf(filter);
  if (idx === -1) return '#' + tag;
  return '#' + tag.slice(0, idx) + '<strong>' + tag.slice(idx, idx + filter.length) + '</strong>' + tag.slice(idx + filter.length);
}

async function showTagSuggestions() {
  if (!cachedAllTags) await loadAllTags();
  _renderTagSuggestions(
    document.getElementById('edit-tag-suggestions'),
    document.getElementById('edit-tags'),
    'addTagSuggestion'
  );
}

function addTagSuggestion(tag) {
  const inputEl = document.getElementById('edit-tags');
  const parts = inputEl.value.split(',');
  parts[parts.length - 1] = tag;
  inputEl.value = parts.join(', ') + ', ';
  showTagSuggestions();
  inputEl.focus();
}

function hideTagSuggestions() {
  const el = document.getElementById('edit-tag-suggestions');
  if (el) el.classList.remove('open');
}

// ─── New Task Tag Suggestions ─────────────────────────
async function showNewTagSuggestions() {
  if (!cachedAllTags) await loadAllTags();
  _renderTagSuggestions(
    document.getElementById('new-tag-suggestions'),
    document.getElementById('new-tags'),
    'addNewTagSuggestion'
  );
}

function addNewTagSuggestion(tag) {
  const inputEl = document.getElementById('new-tags');
  const parts = inputEl.value.split(',');
  parts[parts.length - 1] = tag;
  inputEl.value = parts.join(', ') + ', ';
  showNewTagSuggestions();
  inputEl.focus();
}

function hideNewTagSuggestions() {
  const el = document.getElementById('new-tag-suggestions');
  if (el) el.classList.remove('open');
}

// ─── Subtask Tag Suggestions ─────────────────────────
async function showSubtaskTagSuggestions() {
  if (!cachedAllTags) await loadAllTags();
  _renderTagSuggestions(
    document.getElementById('subtask-tag-suggestions'),
    document.getElementById('subtask-edit-tags'),
    'addSubtaskTagSuggestion'
  );
}

function addSubtaskTagSuggestion(tag) {
  const inputEl = document.getElementById('subtask-edit-tags');
  const parts = inputEl.value.split(',');
  parts[parts.length - 1] = tag;
  inputEl.value = parts.join(', ') + ', ';
  showSubtaskTagSuggestions();
  inputEl.focus();
}

function hideSubtaskTagSuggestions() {
  const el = document.getElementById('subtask-tag-suggestions');
  if (el) el.classList.remove('open');
}

// Close suggestions when clicking outside
document.addEventListener('click', function(e) {
  const allContainers = document.querySelectorAll('.tag-input-container');
  allContainers.forEach(container => {
    if (container.contains(e.target)) return;
    const suggest = container.querySelector('.tag-suggestions');
    if (suggest) suggest.classList.remove('open');
  });
});

// Refresh tag cache when modal is opened
const originalOpenEditModal = openEditModal;
openEditModal = async function(taskId) {
  cachedAllTags = null; // Force refresh
  await loadAllTags();
  originalOpenEditModal(taskId);
};

async function deleteNote(index) {
  const taskId = document.getElementById('edit-task-id').value;
  const data = await api('POST', `/api/tasks/${taskId}/notes/delete`, { index });
  if (data.notes) renderNotes(data.notes);
  if (data.id) taskCache[data.id] = data; (data.subtasks || []).forEach(st => subtaskCache[st.id] = st);
}

async function addSubtask() {
  const input = document.getElementById('edit-new-subtask');
  const title = input.value.trim();
  if (!title) return;

  const taskId = document.getElementById('edit-task-id').value;
  const data = await api('POST', `/api/tasks/${taskId}/subtasks`, { title, state: 'BACKLOG' });
  if (data.subtasks) renderSubtasksInModal(data.subtasks);
  if (data.id) taskCache[data.id] = data; (data.subtasks || []).forEach(st => subtaskCache[st.id] = st);
  input.value = '';
}

async function deleteSubtaskFromModal(subtaskId) {
  if (!confirm('Delete this subtask?')) return;
  await api('POST', `/api/subtasks/${subtaskId}/delete`, {});
  // Refresh the parent task data
  const taskId = document.getElementById('edit-task-id').value;
  const freshTasks = await api('GET', `/api/tasks?view=${currentTaskView}`);
  if (freshTasks.tasks) {
    cacheTasks(freshTasks.tasks);
    const updated = taskCache[taskId];
    if (updated) renderSubtasksInModal(updated.subtasks || []);
  }
}

// ─── Linked Notes in Edit Modal ──────────────────
let _notesDropdownItems = [];

async function loadNotesDropdown() {
  const data = await api('GET', '/api/notes');
  _notesDropdownItems = (data.notes || []).map(n => n.path);
}

function renderEditLinkedNotes(linkedNotes) {
  window._editLinkedNotes = [...(linkedNotes || [])];
  const container = document.getElementById('edit-linked-notes');
  const list = window._editLinkedNotes;
  if (!list.length) {
    container.innerHTML = '<span style="font-size:11px;color:var(--text-dim)">No linked notes</span>';
  } else {
    container.innerHTML = list.map(n => `<span style="display:inline-flex;align-items:center;gap:3px;padding:2px 6px;font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:3px">${h(n)} <span onclick="unlinkNoteInEdit('${h(n)}')" style="cursor:pointer;font-size:14px;line-height:1;color:var(--error)">&times;</span></span>`).join('');
  }
  loadNotesDropdown();
}

function filterNotesDropdown(query) {
  const q = query.toLowerCase();
  const filtered = _notesDropdownItems.filter(p => p.toLowerCase().includes(q));
  const dd = document.getElementById('notes-dropdown');
  if (!filtered.length) { dd.style.display = 'none'; return; }
  dd.innerHTML = filtered.map(p =>
    `<div onclick="selectNoteDropdown('${h(p)}')" style="padding:4px 8px;cursor:pointer;font-size:11px;border-bottom:1px solid var(--border);transition:background 0.1s" onmouseover="this.style.background='var(--bg-hover)'" onmouseout="this.style.background='transparent'">${h(p)}</div>`
  ).join('');
  dd.style.display = 'block';
}

function selectNoteDropdown(path) {
  const input = document.getElementById('edit-link-note-input');
  input.value = path;
  document.getElementById('notes-dropdown').style.display = 'none';
  linkNoteInEdit();
}

function hideNotesDropdown() {
  setTimeout(() => { document.getElementById('notes-dropdown').style.display = 'none'; }, 150);
}

function linkNoteInEdit() {
  const input = document.getElementById('edit-link-note-input');
  const name = input.value.trim();
  if (!name) return;
  if (!window._editLinkedNotes) window._editLinkedNotes = [];
  if (!window._editLinkedNotes.includes(name)) {
    window._editLinkedNotes.push(name);
  }
  input.value = '';
  renderEditLinkedNotes(window._editLinkedNotes);
  document.getElementById('notes-dropdown').style.display = 'none';
}

function unlinkNoteInEdit(name) {
  if (!window._editLinkedNotes) return;
  window._editLinkedNotes = window._editLinkedNotes.filter(n => n !== name);
  renderEditLinkedNotes(window._editLinkedNotes);
}

async function saveTask() {
  const taskId = document.getElementById('edit-task-id').value;
  const title = document.getElementById('edit-title').value.trim();
  const state = document.getElementById('edit-state').value;
  const priority = document.getElementById('edit-priority').value;
  const recurrence = document.getElementById('edit-recurrence').value;
  const dueRaw = document.getElementById('edit-due').value;
  const tags = document.getElementById('edit-tags').value.split(',').map(t => t.trim()).filter(Boolean);

  const due_date = await parseDateText(dueRaw);

  const linkedNotes = (window._editLinkedNotes || []).filter(Boolean);
  await api('POST', `/api/tasks/${taskId}/edit`, { title, state, priority, recurrence: recurrence || undefined, due_date, tags, linked_notes: linkedNotes });
  window._editLinkedNotes = null;
  closeModal('modal-edit');
  loadView(currentView);
}

async function deleteCurrentTask() {
  const taskId = document.getElementById('edit-task-id').value;
  if (!confirm('Delete this task?')) return;
  await api('POST', `/api/tasks/${taskId}/delete`, {});
  closeModal('modal-edit');
  loadView(currentView);
}

// ─── Subtask Modal ──────────────────────────────
function openSubtaskModal(subtaskId) {
  const st = subtaskCache[subtaskId];
  if (!st) return;

  document.getElementById('subtask-edit-id').value = subtaskId;
  document.getElementById('subtask-edit-title').value = stripTags(st.title);
  const stDueEl = document.getElementById('subtask-edit-due');
  stDueEl.value = st.due_date || '';
  stDueEl.dispatchEvent(new Event('input'));
  document.getElementById('subtask-edit-tags').value = (st.tags || []).join(', ');

  const stateEl = document.getElementById('subtask-edit-state');
  stateEl.innerHTML = STATES.map(s => `<option value="${s}" ${s === st.state ? 'selected' : ''}>${s}</option>`).join('');

  const prioEl = document.getElementById('subtask-edit-priority');
  prioEl.value = st.priority || '';

  renderSubtaskNotes(st.notes || []);
  document.getElementById('subtask-new-note').value = '';
  renderSubtaskLinkedNotes(st.linked_notes || []);

  document.getElementById('modal-subtask').classList.add('open');
}

function renderSubtaskNotes(notes) {
  const el = document.getElementById('subtask-notes-list');
  if (!notes.length) {
    el.innerHTML = '<span style="font-size:11px;color:var(--text-dim)">No notes</span>';
    return;
  }
  el.innerHTML = notes.map((n, i) => `
    <div style="display:flex;align-items:center;gap:6px;padding:2px 0;font-size:12px">
      <span style="flex:1;color:var(--text)">${linkifyNote(n)}</span>
      <span style="cursor:pointer;color:var(--accent);opacity:0.7" onclick="editSubtaskNote(${i})" title="Edit">edit</span>
      <span style="cursor:pointer;color:var(--red);opacity:0.7" onclick="deleteSubtaskNote(${i})" title="Delete">x</span>
    </div>
  `).join('');
}

async function addSubtaskNote() {
  const subtaskId = document.getElementById('subtask-edit-id').value;
  const input = document.getElementById('subtask-new-note');
  const note = input.value.trim();
  if (!note) return;

  const data = await api('POST', `/api/subtasks/${subtaskId}/notes`, { note });
  if (data.error) { alert(data.error); return; }
  input.value = '';
  // Refresh subtask data
  const freshTasks = await api('GET', `/api/tasks?view=${currentTaskView}`);
  if (freshTasks && freshTasks.tasks) {
    cacheTasks(freshTasks.tasks);
  }
  if (currentTaskView !== 'all') {
    const allFreshTasks = await api('GET', '/api/tasks?view=all');
    if (allFreshTasks && allFreshTasks.tasks) cacheTasks(allFreshTasks.tasks);
  }
  const fresh = subtaskCache[subtaskId];
  if (fresh) renderSubtaskNotes(fresh.notes || []);
}

async function deleteSubtaskNote(noteIndex) {
  const subtaskId = document.getElementById('subtask-edit-id').value;
  await api('POST', `/api/subtasks/${subtaskId}/notes/delete`, { note_index: noteIndex });
  const freshTasks = await api('GET', `/api/tasks?view=${currentTaskView}`);
  if (freshTasks && freshTasks.tasks) {
    cacheTasks(freshTasks.tasks);
  }
  if (currentTaskView !== 'all') {
    const allFreshTasks = await api('GET', '/api/tasks?view=all');
    if (allFreshTasks && allFreshTasks.tasks) cacheTasks(allFreshTasks.tasks);
  }
  const fresh = subtaskCache[subtaskId];
  if (fresh) renderSubtaskNotes(fresh.notes || []);
}

async function editSubtaskNote(noteIndex) {
  const subtaskId = document.getElementById('subtask-edit-id').value;
  const st = subtaskCache[subtaskId];
  if (!st) return;
  const current = (st.notes || [])[noteIndex] || '';
  // Use the editnote modal with a subtask callback
  document.getElementById('editnote-task-id').value = subtaskId;
  document.getElementById('editnote-index').value = noteIndex;
  document.getElementById('editnote-text').value = current;
  document.getElementById('modal-editnote').classList.add('open');
  document.getElementById('editnote-text').focus();
  window._editNoteCallback = async function() {
    const newNote = document.getElementById('editnote-text').value.trim();
    if (!newNote) return;
    await api('POST', `/api/subtasks/${subtaskId}/notes/edit`, { note_index: parseInt(document.getElementById('editnote-index').value), note: newNote });
    const freshTasks = await api('GET', '/api/tasks');
    if (freshTasks && freshTasks.tasks) { cacheTasks(freshTasks.tasks); }
    const fresh = subtaskCache[subtaskId];
    if (fresh) renderSubtaskNotes(fresh.notes || []);
    closeModal('modal-editnote');
    window._editNoteCallback = null;
  };
}

// ─── Subtask Linked Notes ────────────────────
function renderSubtaskLinkedNotes(linkedNotes) {
  window._stLinkedNotes = [...(linkedNotes || [])];
  const container = document.getElementById('subtask-linked-notes');
  const list = window._stLinkedNotes;
  if (!list.length) {
    container.innerHTML = '<span style="font-size:11px;color:var(--text-dim)">No linked notes</span>';
  } else {
    container.innerHTML = list.map(n => `<span style="display:inline-flex;align-items:center;gap:3px;padding:2px 6px;font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:3px">${h(n)} <span onclick="unlinkSubtaskNote('${h(n)}')" style="cursor:pointer;font-size:14px;line-height:1;color:var(--error)">&times;</span></span>`).join('');
  }
}

function filterNotesDropdownST(query) {
  const q = query.toLowerCase();
  const filtered = _notesDropdownItems.filter(p => p.toLowerCase().includes(q));
  const dd = document.getElementById('notes-dropdown-st');
  if (!filtered.length) { dd.style.display = 'none'; return; }
  dd.innerHTML = filtered.map(p =>
    `<div onclick="selectNoteDropdownST('${h(p)}')" style="padding:4px 8px;cursor:pointer;font-size:11px;border-bottom:1px solid var(--border);transition:background 0.1s" onmouseover="this.style.background='var(--bg-hover)'" onmouseout="this.style.background='transparent'">${h(p)}</div>`
  ).join('');
  dd.style.display = 'block';
}

function hideNotesDropdownST() {
  setTimeout(() => { document.getElementById('notes-dropdown-st').style.display = 'none'; }, 150);
}

function selectNoteDropdownST(path) {
  document.getElementById('subtask-link-note-input').value = path;
  document.getElementById('notes-dropdown-st').style.display = 'none';
  linkSubtaskNote();
}

function linkSubtaskNote() {
  const input = document.getElementById('subtask-link-note-input');
  const name = input.value.trim();
  if (!name) return;
  if (!window._stLinkedNotes) window._stLinkedNotes = [];
  if (!window._stLinkedNotes.includes(name)) {
    window._stLinkedNotes.push(name);
  }
  input.value = '';
  renderSubtaskLinkedNotes(window._stLinkedNotes);
  document.getElementById('notes-dropdown-st').style.display = 'none';
}

function unlinkSubtaskNote(name) {
  if (!window._stLinkedNotes) return;
  window._stLinkedNotes = window._stLinkedNotes.filter(n => n !== name);
  renderSubtaskLinkedNotes(window._stLinkedNotes);
}

async function saveSubtask() {
  const subtaskId = document.getElementById('subtask-edit-id').value;
  const title = document.getElementById('subtask-edit-title').value.trim();
  const state = document.getElementById('subtask-edit-state').value;
  const dueRaw = document.getElementById('subtask-edit-due').value || '';
  const priority = document.getElementById('subtask-edit-priority').value || '';
  const tags = document.getElementById('subtask-edit-tags').value.split(',').map(t => t.trim()).filter(Boolean);

  const due_date = await parseDateText(dueRaw);

  if (!title) return;
  const linkedNotes = (window._stLinkedNotes || []).filter(Boolean);
  await api('POST', `/api/subtasks/${subtaskId}/edit`, { title, state, due_date, priority, tags, linked_notes: linkedNotes });
  window._stLinkedNotes = null;
  closeModal('modal-subtask');
  loadView(currentView);
}

async function deleteSubtask() {
  const subtaskId = document.getElementById('subtask-edit-id').value;
  if (!confirm('Delete this subtask?')) return;
  await api('POST', `/api/subtasks/${subtaskId}/delete`, {});
  closeModal('modal-subtask');
  loadView(currentView);
}

// ─── New Task Modal ─────────────────────────────
async function openNewTaskModal() {
  document.getElementById('new-title').value = '';
  document.getElementById('new-due').value = '';
  document.getElementById('new-date-preview').textContent = '';
  document.getElementById('new-date-preview').className = 'date-preview';
  document.getElementById('new-recurrence').value = '';
  document.getElementById('new-tags').value = '';

  const stateEl = document.getElementById('new-state');
  stateEl.innerHTML = STATES.map(s => `<option value="${s}" ${s === 'BACKLOG' ? 'selected' : ''}>${s}</option>`).join('');

  const priEl = document.getElementById('new-priority');
  priEl.innerHTML = '<option value="">None</option>' + PRIORITIES.map(p => `<option value="${p}">${p}</option>`).join('');

  // Refresh tag cache
  cachedAllTags = null;
  await loadAllTags();

  // Load templates
  await loadTemplateSelect();

  document.getElementById('modal-new').classList.add('open');
  setTimeout(() => document.getElementById('new-title').focus(), 100);
}

async function createTask() {
  const title = document.getElementById('new-title').value.trim();
  if (!title) return;

  const state = document.getElementById('new-state').value;
  const priority = document.getElementById('new-priority').value;
  const recurrence = document.getElementById('new-recurrence').value;
  const dueRaw = document.getElementById('new-due').value;
  const tags = document.getElementById('new-tags').value.split(',').map(t => t.trim()).filter(Boolean);

  const due_date = await parseDateText(dueRaw);

  await api('POST', '/api/tasks', { title, state, priority: priority || undefined, recurrence: recurrence || undefined, due_date: due_date || undefined, tags: tags.length ? tags : undefined });
  closeModal('modal-new');
  loadView(currentView);
}

// ─── Templates ────────────────────────────────────
let cachedTemplates = null;

async function loadTemplateSelect() {
  const data = await api('GET', '/api/templates');
  cachedTemplates = data.templates || [];
  const sel = document.getElementById('new-template');
  sel.innerHTML = '<option value="">— None —</option>' + cachedTemplates.map(t =>
    `<option value="${h(t.name)}">${h(t.name)}</option>`
  ).join('');
}

async function applyTemplate(name) {
  if (!name) return;
  const data = await api('POST', '/api/templates/apply', { name });
  if (data.ok) {
    closeModal('modal-new');
    loadView(currentView);
  }
}

async function saveAsTemplate() {
  const title = document.getElementById('edit-title').value.trim();
  if (!title) { alert('Enter a title first'); return; }
  const name = prompt('Template name:');
  if (!name) return;
  const state = document.getElementById('edit-state').value;
  const priority = document.getElementById('edit-priority').value;
  const recurrence = document.getElementById('edit-recurrence').value;
  // Collect subtask titles
  const subtaskEls = document.querySelectorAll('#edit-subtasks-list .subtask-row .st-title');
  const subtasks = Array.from(subtaskEls).map(el => el.textContent.trim()).filter(Boolean);
  const data = await api('POST', '/api/templates/save', { name, title, state, priority, recurrence, subtasks });
  if (data.ok) alert('Template "' + name + '" saved');
}

// ─── Natural Language Date Parsing ────────────────
let datePreviewTimeout = null;
async function previewDate(raw, previewId) {
  const el = document.getElementById(previewId);
  if (!el) return;
  const q = raw.trim();
  if (!q) { el.textContent = ''; el.className = 'date-preview'; return; }
  clearTimeout(datePreviewTimeout);
  datePreviewTimeout = setTimeout(async () => {
    const data = await api('GET', `/api/parse-date?q=${encodeURIComponent(q)}`);
    if (data.parsed) {
      el.textContent = '✓ ' + data.parsed;
      el.className = 'date-preview valid';
    } else {
      el.textContent = '? could not parse';
      el.className = 'date-preview invalid';
    }
  }, 300);
}

async function parseDateText(raw) {
  const q = (raw || '').trim();
  if (!q) return '';
  // Already in dd/mm/yyyy format
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(q)) return q;
  const data = await api('GET', `/api/parse-date?q=${encodeURIComponent(q)}`);
  return data.parsed || '';
}

// ─── Search ─────────────────────────────────────
const searchInput = document.getElementById('search-input');
const searchResults = document.getElementById('search-results');
let searchTimeout = null;
let searchSelectedIdx = -1;

function updateSearchSelection() {
  const items = searchResults.querySelectorAll('.search-result-item');
  items.forEach((el, i) => el.classList.toggle('sr-selected', i === searchSelectedIdx));
  if (searchSelectedIdx >= 0 && items[searchSelectedIdx]) {
    items[searchSelectedIdx].scrollIntoView({ block: 'nearest' });
  }
}

searchInput.addEventListener('keydown', e => {
  if (!searchResults.classList.contains('open')) return;
  const items = searchResults.querySelectorAll('.search-result-item');
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    searchSelectedIdx = Math.min(searchSelectedIdx + 1, items.length - 1);
    updateSearchSelection();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    searchSelectedIdx = Math.max(searchSelectedIdx - 1, 0);
    updateSearchSelection();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (searchSelectedIdx >= 0 && items[searchSelectedIdx]) {
      items[searchSelectedIdx].click();
    }
  }
});

searchInput.addEventListener('input', () => {
  clearTimeout(searchTimeout);
  searchSelectedIdx = -1;
  const q = searchInput.value.trim();
  if (q.length < 2) {
    searchResults.classList.remove('open');
    return;
  }
  searchTimeout = setTimeout(async () => {
    const data = await api('GET', `/api/search?q=${encodeURIComponent(q)}`);
    let html = '';
    if (data.tasks && data.tasks.length > 0) {
      cacheTasks(data.tasks);
      html += '<div style="font-size:9px;color:var(--text-dim);padding:4px 10px;text-transform:uppercase;letter-spacing:0.5px">Tasks</div>';
      html += data.tasks.slice(0, 12).map(t => `
        <div class="search-result-item" onclick="searchResultClick('${t.id}')">
          <span class="sr-id">#${t.id}</span>
          <span class="sr-state state-badge state-${t.state.replace(/ /g,'_')}" style="font-size:9px;padding:1px 4px">${t.state}</span>
          ${h(stripTags(t.title))}
        </div>
      `).join('');
    }
    if (data.notes && data.notes.length > 0) {
      if (html) html += '<div style="border-top:1px solid var(--border);margin:2px 0"></div>';
      html += '<div style="font-size:9px;color:var(--text-dim);padding:4px 10px;text-transform:uppercase;letter-spacing:0.5px">Notes</div>';
      html += data.notes.slice(0, 8).map(n => `
        <div class="search-result-item" onclick="searchNoteClick('${h(n.path)}')">
          <span style="color:var(--accent);margin-right:6px">📄</span>
          ${h(n.path)}
          ${n.snippet ? '<br><span style="font-size:10px;color:var(--text-dim)">' + h(n.snippet) + '</span>' : ''}
        </div>
      `).join('');
    }
    if (html) {
      searchResults.innerHTML = html;
      searchResults.classList.add('open');
    } else {
      searchResults.innerHTML = '<div class="search-result-item" style="color:var(--text-dim)">No results</div>';
      searchResults.classList.add('open');
    }
  }, 200);
});

searchInput.addEventListener('blur', () => {
  setTimeout(() => searchResults.classList.remove('open'), 200);
});

function searchResultClick(taskId) {
  searchResults.classList.remove('open');
  searchInput.value = '';
  openEditModal(taskId);
}

function searchNoteClick(path) {
  searchResults.classList.remove('open');
  searchInput.value = '';
  switchView('notes');
  openNoteView(path);
}

// ─── Modal helpers ──────────────────────────────
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  // Reset form fields so reopening doesn't show stale data
  const modal = document.getElementById(id);
  modal.querySelectorAll('input[type="text"], input[type="hidden"]').forEach(el => el.value = '');
  modal.querySelectorAll('.date-preview').forEach(el => { el.textContent = ''; el.className = 'date-preview'; });
  modal.querySelectorAll('textarea').forEach(el => el.value = '');
  modal.querySelectorAll('select').forEach(el => el.selectedIndex = 0);
  modal.querySelectorAll('.notes-list, .subtasks-section').forEach(el => el.innerHTML = '');
  // Clear any note-edit callback override
  if (id === 'modal-editnote') window._editNoteCallback = null;
}

// ─── Keyboard Shortcuts ─────────────────────────
const NAV_KEYS = { t: 'tasks', k: 'kanban', a: 'agenda', d: 'calendar', s: 'stats', w: 'weekly', b: 'burndown', e: 'gantt', g: 'tags', i: 'time', p: 'pomodoro', x: 'blockers', m: 'notes', j: 'jira:active', o: 'jira:notify', u: 'sync', c: 'config', l: 'log' };

document.addEventListener('keydown', e => {
  // Never intercept browser shortcuts (Ctrl/Cmd/Alt + key)
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  // Don't trigger shortcuts when typing in inputs
  const tag = e.target.tagName;
  const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
    searchResults.classList.remove('open');
    searchInput.blur();
    return;
  }

  if (isInput) return;

  if (NAV_KEYS[e.key]) {
    e.preventDefault();
    const target = NAV_KEYS[e.key];
    if (target.includes(':')) {
      const [view, filter] = target.split(':');
      currentJiraFilter = filter;
      switchView(view);
    } else {
      switchView(target);
    }
  } else if (e.key === 'r') {
    e.preventDefault();
    loadView(currentView);
  } else if (e.key === 'n') {
    e.preventDefault();
    openNewTaskModal();
  } else if (e.key === '/') {
    e.preventDefault();
    searchInput.focus();
  } else if (e.key === 'f') {
    e.preventDefault();
    const filterInput = document.getElementById('filter-text-input');
    if (filterInput) filterInput.focus();
  } else if (e.key === '\\') {
    e.preventDefault();
    toggleSidebar();
  } else if (currentView === 'calendar') {
    // Calendar-specific shortcuts
    if (e.key === '[' || e.key === 'h') {
      e.preventDefault();
      calendarPrev();
    } else if (e.key === ']' || e.key === 'l') {
      e.preventDefault();
      calendarNext();
    } else if (e.key === 'm') {
      e.preventDefault();
      setCalendarView('month');
    } else if (e.key === 'y') {
      e.preventDefault();
      setCalendarView('week');
    }
  }
});

// Close modal on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.remove('open');
  });
});

// ─── Sidebar Toggle ──────────────────────────────
function toggleSidebar() {
  const app = document.querySelector('.app');
  const collapsed = app.classList.toggle('sidebar-collapsed');
  const btn = document.getElementById('btn-collapse');
  btn.textContent = collapsed ? '☰' : '☰';
  try { localStorage.setItem('sidebar-collapsed', collapsed ? '1' : ''); } catch (e) {}
}
// Restore sidebar state on load
(function initSidebar() {
  const app = document.querySelector('.app');
  try {
    if (localStorage.getItem('sidebar-collapsed') === '1') {
      app.classList.add('sidebar-collapsed');
    }
  } catch (e) {}
})();

// ─── Notes ──────────────────────────────────────
let _notesList = [];
let _currentNotePath = null;

async function loadNotes() {
  const data = await api('GET', '/api/notes');
  _notesList = data.notes || [];
  renderNotesList();
}

function renderNotesList() {
  const container = document.getElementById('notes-content');
  const countEl = document.getElementById('notes-count');
  if (!_notesList.length) {
    container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim)">No notes yet. Click "+ New Note" to create one.</div>';
    if (countEl) countEl.textContent = '';
    return;
  }
  if (countEl) countEl.textContent = `${_notesList.length} note${_notesList.length !== 1 ? 's' : ''}`;
  const tree = {};
  for (const n of _notesList) {
    const parts = n.path.split('/');
    parts.pop();
    const folder = parts.join('/') || '/';
    if (!tree[folder]) tree[folder] = [];
    tree[folder].push(n);
  }
  const sortedFolders = Object.keys(tree).sort((a, b) => a === '/' ? -1 : b === '/' ? 1 : a.localeCompare(b));
  let html = '';
  for (const folder of sortedFolders) {
    const notes = tree[folder];
    if (folder === '/') {
      html += notes.map(n => renderNoteItem(n)).join('');
    } else {
      html += `<div class="note-folder-section"
        ondragover="noteSectionDragOver(event)"
        ondragleave="noteSectionDragLeave(event)"
        ondrop="noteSectionDrop(event, '${h(folder)}')">
        <div class="note-folder-header"><span class="folder-icon">📁</span>${h(folder)}</div>
        ${notes.map(n => renderNoteItem(n)).join('')}
      </div>`;
    }
  }
  container.innerHTML = html;
  container.ondragover = (e) => { e.preventDefault(); container.classList.add('drag-over-root'); };
  container.ondragleave = (e) => { if (!container.contains(e.relatedTarget)) container.classList.remove('drag-over-root'); };
  container.ondrop = (e) => { e.preventDefault(); container.classList.remove('drag-over-root'); noteSectionDrop(e, ''); };
}

function renderNoteItem(n) {
  const preview = (n.preview || '').substring(0, 80);
  const date = n.mtime ? new Date(n.mtime).toLocaleDateString() : '';
  const escapedPath = h(n.path);
  const escapedName = h(n.name);
  const escapedPreview = h(preview);
  return `<div class="note-item"
    draggable="true"
    ondragstart="noteDragStart(event, '${escapedPath}')"
    onclick="openNoteView('${escapedPath}')">
    <div class="note-item-row">
      <span class="note-icon">📄</span>
      <div class="note-body">
        <div class="note-title"
          data-note-path="${escapedPath}"
          onmouseenter="noteTooltipShow(event, '${escapedPath}')"
          onmouseleave="noteTooltipHide()">${escapedName}</div>
        <div class="note-preview-text">${escapedPreview || 'Empty note'}</div>
      </div>
      <div class="note-meta">
        <span class="note-date">${date}</span>
        <div class="note-toolbar">
          <span class="tb-btn tb-btn-preview" onclick="event.stopPropagation();previewNoteFile('${escapedPath}')">Preview</span>
          <span class="tb-btn tb-btn-move" onclick="event.stopPropagation();showMoveNoteModal('${escapedPath}')">Move</span>
          <span class="tb-btn tb-btn-del" onclick="event.stopPropagation();deleteNoteFromList('${escapedPath}')">Delete</span>
        </div>
      </div>
    </div>
  </div>`;
}

let _tooltipTimer = null;
let _tooltipPath = null;

function noteTooltipShow(event, path) {
  _tooltipPath = path;
  clearTimeout(_tooltipTimer);
  _tooltipTimer = setTimeout(async () => {
    if (_tooltipPath !== path) return;
    const tip = document.getElementById('note-tooltip');
    if (!tip) return;
    const rect = event.target.getBoundingClientRect();
    let top = rect.bottom + 6;
    let left = rect.left;
    tip.style.display = 'block';
    tip.innerHTML = '<div class="tip-loading">Loading preview...</div>';
    requestAnimationFrame(() => {
      const tw = tip.offsetWidth;
      const th = tip.offsetHeight;
      if (left + tw > window.innerWidth - 8) left = window.innerWidth - tw - 8;
      if (top + th > window.innerHeight - 8) top = rect.top - th - 6;
      if (left < 4) left = 4;
      if (top < 4) top = 4;
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    });
    try {
      const data = await api('GET', `/api/notes/read?name=${encodeURIComponent(path)}`);
      if (_tooltipPath !== path) return;
      const content = (data && data.content) || '';
      const previewContent = content.substring(0, 500);
      const rendered = renderMarkdownPreview(previewContent);
      tip.innerHTML = `<div class="tip-title">${h(path)}</div><div class="tip-content">${rendered || '<em>Empty note</em>'}</div>`;
    } catch (e) {
      tip.innerHTML = '<div class="tip-loading">Could not load preview</div>';
    }
  }, 400);
}

function noteTooltipHide() {
  clearTimeout(_tooltipTimer);
  _tooltipTimer = setTimeout(() => {
    _tooltipPath = null;
    const tip = document.getElementById('note-tooltip');
    if (tip) tip.style.display = 'none';
  }, 250);
}

function noteTooltipCancel() {
  clearTimeout(_tooltipTimer);
}

function noteTooltipImmediateHide() {
  clearTimeout(_tooltipTimer);
  _tooltipPath = null;
  const tip = document.getElementById('note-tooltip');
  if (tip) tip.style.display = 'none';
}

// ─── Drag & Drop Notes ────────────────────────────

let _dragNotePath = null;

function noteDragStart(event, path) {
  _dragNotePath = path;
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', path);
  event.target.classList.add('dragging');
}

function noteSectionDragOver(event) {
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  event.currentTarget.classList.add('drag-over-target');
}

function noteSectionDragLeave(event) {
  event.currentTarget.classList.remove('drag-over-target');
}

async function noteSectionDrop(event, targetFolder) {
  event.preventDefault();
  event.stopPropagation();
  document.querySelectorAll('.drag-over-target').forEach(el => el.classList.remove('drag-over-target'));
  document.querySelectorAll('.drag-over-root').forEach(el => el.classList.remove('drag-over-root'));
  document.querySelectorAll('.dragging').forEach(el => el.classList.remove('dragging'));

  const fromPath = _dragNotePath || event.dataTransfer.getData('text/plain');
  _dragNotePath = null;
  if (!fromPath) return;

  const fileName = fromPath.split('/').pop();
  const toPath = targetFolder ? targetFolder + '/' + fileName : fileName;
  if (toPath === fromPath) return;

  await api('POST', '/api/notes/move', { from: fromPath, to: toPath });
  loadNotes();
}

function renderMarkdownPreview(text) {
  if (!text) return '';
  let html = h(text);
  html = html.replace(/```(\w*)\n[\s\S]*?```/g, '<code>[...]</code>');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
  html = html.replace(/^### ?(.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## ?(.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# ?(.+)$/gm, '<h1>$1</h1>');
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  html = html.replace(/\n\n/g, '</p><p>');
  html = '<p>' + html + '</p>';
  html = html.replace(/<p[^>]*>\s*<\/p>/g, '');
  return html;
}

async function deleteNoteFromList(path) {
  if (!confirm(`Delete note "${path}"? This cannot be undone.`)) return;
  await api('POST', '/api/notes/delete', { name: path });
  loadNotes();
}

let _moveNotePath = null;

async function showMoveNoteModal(path) {
  _moveNotePath = path;
  document.getElementById('move-note-name').textContent = path;
  const select = document.getElementById('move-note-folder-select');
  const custom = document.getElementById('move-note-custom-folder');
  select.innerHTML = '<option value="">— Root folder —</option>';
  custom.value = '';
  try {
    const data = await api('GET', '/api/notes/folders');
    const folders = data.folders || [];
    for (const f of folders) {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = f;
      select.appendChild(opt);
    }
  } catch (e) {}
  const parts = path.split('/');
  if (parts.length > 1) {
    const currentFolder = parts.slice(0, -1).join('/');
    select.value = currentFolder;
    custom.value = currentFolder;
  }
  document.getElementById('move-note-modal').classList.add('open');
}

function closeMoveNoteModal() {
  document.getElementById('move-note-modal').classList.remove('open');
  _moveNotePath = null;
}

async function confirmMoveNote() {
  if (!_moveNotePath) return;
  const custom = document.getElementById('move-note-custom-folder').value.trim();
  const folder = custom || '';
  const fileName = _moveNotePath.split('/').pop();
  const toPath = folder ? folder + '/' + fileName : fileName;
  if (toPath === _moveNotePath) {
    closeMoveNoteModal();
    return;
  }
  if (!confirm(`Move "${_moveNotePath}" to "${toPath}"?`)) return;
  await api('POST', '/api/notes/move', { from: _moveNotePath, to: toPath });
  closeMoveNoteModal();
  loadNotes();
}

function showNewNoteModal() {
  _currentNotePath = null;
  document.getElementById('notes-modal-title').textContent = 'New Note';
  document.getElementById('notes-modal-path').value = '';
  document.getElementById('notes-modal-content').value = '';
  document.getElementById('notes-modal').classList.add('open');
  document.getElementById('notes-modal-path').focus();
}

function closeNotesModal() {
  document.getElementById('notes-modal').classList.remove('open');
}

async function saveNote() {
  const name = document.getElementById('notes-modal-path').value.trim();
  const content = document.getElementById('notes-modal-content').value;
  if (!name) { alert('Note path is required.'); return; }
  await api('POST', '/api/notes', { name, content });
  closeNotesModal();
  loadNotes();
}

function openNoteView(path) {
  _currentNotePath = path;
  document.getElementById('notes-view-title').textContent = path;
  document.getElementById('notes-edit-textarea').value = '';
  document.getElementById('notes-view-modal').classList.add('open');
  loadNoteContent(path);
}

function closeNotesViewModal() {
  document.getElementById('notes-view-modal').classList.remove('open');
  _currentNotePath = null;
}

async function loadNoteContent(path) {
  try {
    const data = await api('GET', `/api/notes/read?name=${encodeURIComponent(path)}`);
    if (data && data.content) {
      document.getElementById('notes-edit-textarea').value = data.content;
    }
    document.getElementById('notes-edit-textarea').focus();
  } catch (e) {
    document.getElementById('notes-edit-textarea').value = '';
  }
}

async function previewNoteFile(path) {
  const data = await api('GET', `/api/notes/read?name=${encodeURIComponent(path)}`);
  const content = (data && data.content) || '';
  const html = renderMarkdown(content);
  const title = path || 'Preview';
  const page = `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>${h(title)}</title><style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;line-height:1.6;color:#1e293b;background:#fff}pre{background:#f1f5f9;padding:12px;border-radius:6px;overflow-x:auto}code{background:#f1f5f9;padding:1px 4px;border-radius:3px;font-size:90%}a{color:#2563eb}h1,h2,h3{line-height:1.3}ul{padding-left:20px}</style></head><body>${html}</body></html>`;
  const blob = new Blob([page], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  const width = Math.min(1200, Math.max(720, Math.floor(window.screen.availWidth * 0.75)));
  const height = Math.min(900, Math.max(560, Math.floor(window.screen.availHeight * 0.82)));
  const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
  const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
  const features = `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`;
  const win = window.open(url, 'ttm-note-preview', features);
  if (win) { try { win.opener = null; } catch (e) {} win.focus(); }
  else { window.open(url, '_blank'); }
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function previewNote() {
  const content = document.getElementById('notes-edit-textarea').value || '';
  const html = renderMarkdown(content);
  const title = _currentNotePath || 'Preview';
  const page = `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>${h(title)}</title><style>body{font-family:system-ui,sans-serif;max-width:720px;margin:20px auto;padding:0 16px;line-height:1.6;color:#1e293b;background:#fff}pre{background:#f1f5f9;padding:12px;border-radius:6px;overflow-x:auto}code{background:#f1f5f9;padding:1px 4px;border-radius:3px;font-size:90%}a{color:#2563eb}h1,h2,h3{line-height:1.3}ul{padding-left:20px}</style></head><body>${html}</body></html>`;
  const blob = new Blob([page], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  const width = Math.min(1200, Math.max(720, Math.floor(window.screen.availWidth * 0.75)));
  const height = Math.min(900, Math.max(560, Math.floor(window.screen.availHeight * 0.82)));
  const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
  const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
  const features = `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`;
  const win = window.open(url, 'ttm-note-preview', features);
  if (win) {
    try { win.opener = null; } catch (e) {}
    win.focus();
  } else {
    window.open(url, '_blank');
  }
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function deleteCurrentNote() {
  if (!_currentNotePath) return;
  if (!confirm(`Delete note "${_currentNotePath}"?`)) return;
  await api('POST', '/api/notes/delete', { name: _currentNotePath });
  closeNotesViewModal();
  if (currentView === 'notes') loadNotes();
}

async function saveEditedNote() {
  if (!_currentNotePath) return;
  const content = document.getElementById('notes-edit-textarea').value;
  await api('POST', '/api/notes', { name: _currentNotePath, content });
  closeNotesViewModal();
  if (currentView === 'notes') loadNotes();
}

function renderMarkdown(text) {
  if (!text) return '';
  let html = h(text);
  // Code blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre style="background:var(--bg);padding:8px;border-radius:var(--radius);overflow-x:auto;font-size:12px"><code>$2</code></pre>');
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg);padding:1px 4px;border-radius:3px;font-size:12px">$1</code>');
  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Headers (space after # is optional)
  html = html.replace(/^### ?(.+)$/gm, '<h3 style="margin:12px 0 4px;font-size:14px">$1</h3>');
  html = html.replace(/^## ?(.+)$/gm, '<h2 style="margin:16px 0 6px;font-size:16px">$1</h2>');
  html = html.replace(/^# ?(.+)$/gm, '<h1 style="margin:20px 0 8px;font-size:18px">$1</h1>');
  // Unordered lists
  html = html.replace(/^- (.+)$/gm, '<li style="margin:2px 0">$1</li>');
  html = html.replace(/(<li.*<\/li>\n?)+/g, '<ul style="padding-left:20px;margin:4px 0">$&</ul>');
  // Ordered lists
  html = html.replace(/^\d+\. (.+)$/gm, '<li style="margin:2px 0">$1</li>');
  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:var(--accent, #4fc3f7)">$1</a>');
  // Paragraphs (double newlines)
  html = html.replace(/\n\n/g, '</p><p style="margin:8px 0">');
  html = '<p style="margin:8px 0">' + html + '</p>';
  // Remove truly empty paragraphs (whitespace only)
  html = html.replace(/<p[^>]*>\s*<\/p>/g, '');
  return html;
}

// ─── Linked Notes in Tasks ───────────────────────
function renderLinkedNotes(linkedNotes) {
  if (!linkedNotes || !linkedNotes.length) return '';
  return `<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">
    ${linkedNotes.map(n => `<span class="linked-note-badge" onclick="event.stopPropagation();openNoteView('${h(n)}')" style="display:inline-flex;align-items:center;gap:3px;padding:1px 6px;font-size:10px;background:var(--bg);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--accent, #4fc3f7)">${h(n)}</span>`).join('')}
  </div>`;
}

// ─── Utility ────────────────────────────────────
function h(str) { return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function openNoteUrl(event, encodedUrl) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const url = decodeURIComponent(encodedUrl);
  const width = Math.min(1200, Math.max(720, Math.floor(window.screen.availWidth * 0.75)));
  const height = Math.min(900, Math.max(560, Math.floor(window.screen.availHeight * 0.82)));
  const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
  const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
  const features = `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`;
  const opened = window.open(url, 'ttm-note-link', features);
  if (opened) {
    try { opened.opener = null; } catch (e) {}
    opened.focus();
  } else {
    window.open(url, '_blank');
  }
}
function getUrlFileAlias(parsed) {
  const candidates = [parsed.searchParams.get('id'), parsed.searchParams.get('file'), parsed.searchParams.get('source')];
  candidates.push(parsed.pathname);
  for (const candidate of candidates) {
    if (!candidate) continue;
    const segments = candidate.split('/').filter(Boolean).map(s => decodeURIComponent(s));
    const last = segments[segments.length - 1] || '';
    if (last && /\.[A-Za-z0-9]{1,8}$/.test(last)) return last;
  }
  return '';
}
function getUrlAlias(url) {
  try {
    const parsed = new URL(url);
    const fileAlias = getUrlFileAlias(parsed);
    if (fileAlias) return fileAlias;
    const segments = parsed.pathname.split('/').filter(Boolean).map(s => decodeURIComponent(s));
    const last = segments[segments.length - 1] || '';
    if (last && /\.[A-Za-z0-9]{1,8}$/.test(last)) return last;
    if (last) return `${parsed.hostname}/.../${last}`;
    return parsed.hostname;
  } catch (e) {
    return url.length > 48 ? url.slice(0, 45) + '...' : url;
  }
}
function linkifyNote(str) {
  const urlRe = /(https?:\/\/[^\s<>'"]+)/g;
  const exactUrlRe = /^https?:\/\/[^\s<>'"]+$/;
  return String(str || '').split(urlRe).map(part => {
    if (!exactUrlRe.test(part)) return h(part);
    const trailing = part.match(/[),.;:!?]+$/)?.[0] || '';
    const url = trailing ? part.slice(0, -trailing.length) : part;
    const alias = getUrlAlias(url);
    return `<a class="note-link" href="${h(url)}" title="${h(url)}" onclick="openNoteUrl(event,'${encodeURIComponent(url)}')">${h(alias)}</a>${h(trailing)}`;
  }).join('').replace(/\n/g, '<br>');
}
function stripTags(str) { return String(str || '').replace(/#\w+/g, '').replace(/\s{2,}/g, ' ').trim(); }

function isOverdue(dateStr) {
  if (!dateStr) return false;
  const parts = dateStr.split('/');
  if (parts.length !== 3) return false;
  const d = new Date(parts[2], parts[1] - 1, parts[0]);
  return d < new Date(new Date().toDateString());
}

function taskListHtml(tasks, showStateDropdown = true) {
  cacheTasks(tasks);
  return `<ul class="task-list">${tasks.map(t => renderTaskItem(t, showStateDropdown)).join('')}</ul>`;
}

// ─── Desktop Notifications ──────────────────────────────────────
let _notifEnabled = false;
let _notifSeen = new Set();
let _lastJiraCount = 0;
let _notifInterval = null;

function initNotifications() {
  _notifEnabled = localStorage.getItem('ttm-notif-enabled') === 'true';
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
  setTimeout(checkNotifications, 5000);
  _notifInterval = setInterval(checkNotifications, 30000);
}

function toggleNotifSetting() {
  const cb = document.getElementById('cfg-notif-enabled');
  _notifEnabled = cb.checked;
  localStorage.setItem('ttm-notif-enabled', _notifEnabled);
  if (_notifEnabled && 'Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function showDesktopNotif(title, body) {
  if (!_notifEnabled) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try { new Notification(title, { body }); } catch(e) {}
}

async function checkNotifications() {
  try {
    const [agenda, jira] = await Promise.all([
      api('GET', '/api/agenda?days=1'),
      api('GET', '/api/jira?filter=notify'),
    ]);

    let badge = 0;

    for (const t of (agenda.overdue || [])) {
      const key = 'ov-' + t.id;
      if (!_notifSeen.has(key)) {
        _notifSeen.add(key);
        showDesktopNotif('Task Overdue', (t.title || '') + ' (due ' + (t.due_date || '?') + ')');
      }
      badge++;
    }

    for (const t of (agenda.due_today || [])) {
      const key = 'due-' + t.id;
      if (!_notifSeen.has(key)) {
        _notifSeen.add(key);
        showDesktopNotif('Task Due Today', t.title || '');
      }
    }

    const notifs = jira.notifications || [];
    if (notifs.length > _lastJiraCount && _lastJiraCount > 0) {
      showDesktopNotif('Jira', (notifs.length - _lastJiraCount) + ' new notification(s)');
    }
    _lastJiraCount = notifs.length;
    badge += notifs.length;

    document.title = badge > 0 ? '(' + badge + ') TextTaskManager' : 'TextTaskManager';
  } catch(e) { /* ignore polling errors */ }
}

// ─── Init ───────────────────────────────────────
loadTasks('pending');
initNotifications();

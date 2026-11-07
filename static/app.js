/* ============================================================
   Docufy frontend — vanilla JS, no build step.
   ============================================================ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  documentId: null,
  documentMeta: null,
  ocr: null,            // { pages: [...] } for evidence highlights
  fields: [],           // [{ id, key, description, type, required }]
  results: null,
  lastRequest: null,
  zoom: 1,
  rotation: 0,
  busy: false,
  model: null,
  llmOk: false,
  projectName: "Untitled extraction",
};

const PRESETS = [
  { label: "Invoice total", data: { key: "invoice_total", description: "Total amount payable including tax, as a decimal number", type: "number" } },
  { label: "Invoice number", data: { key: "invoice_number", description: "The invoice or document reference number", type: "string" } },
  { label: "Vendor", data: { key: "vendor_name", description: "Company or person issuing the document", type: "string" } },
  { label: "Date", data: { key: "document_date", description: "Date the document was issued", type: "date" } },
  { label: "Due date", data: { key: "due_date", description: "Payment due date", type: "date" } },
  { label: "Line items", data: { key: "line_items", description: "One entry per line item, e.g. 'Description x2 @ 19.99'", type: "array" } },
  { label: "Currency", data: { key: "currency", description: "ISO currency code, e.g. USD, EUR", type: "string" } },
  { label: "Paid", data: { key: "is_paid", description: "Whether the document is marked as paid", type: "boolean" } },
  { label: "Patient name", data: { key: "patient_name", description: "Full name of the patient", type: "string" } },
  { label: "Address", data: { key: "address", description: "Full street address including city and postcode", type: "string" } },
];

// ---------------------------------------------------------------- utilities

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(message, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<span class="ic">${kind === "err" ? "⚠️" : kind === "ok" ? "✓" : "💡"}</span><span>${escapeHtml(message)}</span>`;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 320);
  }, kind === "err" ? 6000 : 3200);
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function confClass(c) {
  return c >= 0.8 ? "high" : c >= 0.5 ? "mid" : "low";
}

// A field "passes" when its confidence clears the user's threshold.
function threshold() {
  return Number($("#threshold").value) / 100;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// ---------------------------------------------------------------- tabs

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => {
      t.classList.toggle("active", t === tab);
      t.setAttribute("aria-selected", String(t === tab));
    });
    $$(".tab-panel").forEach((p) => p.classList.remove("active"));
    $(`#panel-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "request") refreshRequests();
  });
});

function showTab(name) {
  const tab = $(`.tab[data-tab="${name}"]`);
  if (tab) tab.click();
}

// ---------------------------------------------------------------- feature requests

const KINDS = {
  template: "template",
  field: "field",
  speed: "speed",
  bug: "bug",
  other: "other",
};

async function refreshRequests() {
  const list = $("#req-list");
  if (!list) return;
  try {
    const res = await fetch("/api/feature-requests");
    const data = await res.json();
    const items = data.requests || [];
    if (!items.length) {
      list.innerHTML = `<p class="muted req-empty">Nothing yet. Your first request shows up here.</p>`;
      return;
    }
    list.innerHTML = items
      .map(
        (r) => `
        <div class="req-item">
          <div class="req-item-top">
            <span class="req-item-title">${escapeHtml(r.title)}</span>
            <span class="req-kind">${escapeHtml(KINDS[r.kind] || r.kind)}</span>
          </div>
          ${r.detail ? `<div class="req-item-detail">${escapeHtml(r.detail)}</div>` : ""}
          <div class="req-item-meta">${escapeHtml(formatWhen(r.created_at))}</div>
        </div>`
      )
      .join("");
  } catch {
    list.innerHTML = `<p class="muted req-empty">Could not load requests.</p>`;
  }
}

function formatWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  return d.toLocaleDateString();
}

function initRequests() {
  const btn = $("#req-submit");
  const status = $("#req-status");
  const title = $("#req-title");
  if (!btn || !title) return;

  async function submit() {
    const text = title.value.trim();
    if (!text) {
      status.textContent = "Give it a title first.";
      status.className = "req-status is-bad";
      title.focus();
      return;
    }
    btn.disabled = true;
    status.textContent = "Saving…";
    status.className = "req-status";
    try {
      const res = await fetch("/api/feature-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: text,
          detail: $("#req-detail").value.trim(),
          kind: $("#req-kind").value,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not save the request.");
      title.value = "";
      $("#req-detail").value = "";
      status.textContent = `Saved as ${data.request.id}`;
      status.className = "req-status is-good";
      refreshRequests();
    } catch (err) {
      status.textContent = err.message;
      status.className = "req-status is-bad";
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", submit);
  // Enter in the title submits; Enter in the details box stays a newline.
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  });
}

// ---------------------------------------------------------------- health

async function checkHealth() {
  const chip = $("#llm-chip");
  const label = $("#llm-label");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const llm = data.llm;
    if (!llm.reachable) {
      chip.querySelector(".dot").className = "dot bad";
      label.textContent = "Ollama offline";
      chip.title = `Cannot reach ${llm.url}. Run: ollama serve`;
      state.model = llm.configured_model;
      state.llmOk = false;
    } else {
      const using = llm.model_installed
        ? llm.configured_model
        : llm.installed_models[0] || llm.configured_model;
      state.model = using;
      state.llmOk = true;
      if (llm.model_installed) {
        chip.querySelector(".dot").className = "dot ok";
        label.textContent = llm.configured_model;
        chip.title = `${llm.configured_model} via ${llm.url}`;
      } else {
        // Fall back to whatever is installed rather than failing the scrape.
        chip.querySelector(".dot").className = "dot warn";
        label.textContent = `${llm.configured_model} not pulled`;
        chip.title =
          `Run: ollama pull ${llm.configured_model}\n` +
          `Using instead: ${using}\nInstalled: ${llm.installed_models.join(", ") || "none"}`;
      }
    }
  } catch {
    chip.querySelector(".dot").className = "dot bad";
    label.textContent = "server unreachable";
    chip.title = "The Docufy backend is not responding.";
    state.llmOk = false;
  }
  updateScrapeState();
}

// ---------------------------------------------------------------- fields

function renderPresets() {
  const wrap = $("#presets");
  wrap.innerHTML = "";

  // Every built-in field, de-duplicated by key, in template order.
  const seen = new Set();
  const presets = [];
  Object.values(templateCatalog)
    .filter((t) => !t.custom)
    .forEach((t) => {
      (t.fields || []).forEach((f) => {
        if (seen.has(f.key) || f.type === "array") return; // lists are niche chips
        seen.add(f.key);
        presets.push({ label: f.key.replace(/_/g, " "), data: f });
      });
    });
  PRESETS.filter((p) => !seen.has(p.data.key)).forEach((p) => presets.push(p));

  presets.forEach((p) => {
    const btn = document.createElement("button");
    btn.className = "preset-chip";
    btn.textContent = p.label;
    btn.addEventListener("click", () => {
      if (state.fields.some((f) => f.key === p.data.key)) {
        toast(`"${p.data.key}" is already in your fields.`);
        return;
      }
      const f = addField(p.data);
      f.el.querySelector(".field-name").focus();
      f.el.animate(
        [{ boxShadow: "0 0 0 0 rgba(124,92,255,0.35)" }, { boxShadow: "0 0 0 8px rgba(124,92,255,0)" }],
        { duration: 700, easing: "ease-out" }
      );
    });
    wrap.appendChild(btn);
  });
}
function addField(data = {}) {
  const tpl = $("#field-template").content.cloneNode(true);
  const el = tpl.querySelector(".field-card");

  const field = {
    id: uid(),
    key: data.key || "",
    description: data.description || "",
    type: data.type || "string",
    required: !!data.required,
    // Slot names for structured lists (e.g. line items), kept off the form.
    slots: data.fields || null,
    el,
  };

  const nameInput = el.querySelector(".field-name");
  const descInput = el.querySelector(".field-desc");
  const typeSelect = el.querySelector(".field-type");
  const reqBox = el.querySelector(".field-required");

  nameInput.value = field.key;
  descInput.value = field.description;
  typeSelect.value = field.type;
  reqBox.checked = field.required;

  nameInput.addEventListener("input", () => {
    field.key = nameInput.value;
    markFieldsChanged();
  });
  // Normalise field names to snake_case on blur so the JSON keys stay clean.
  nameInput.addEventListener("blur", () => {
    const cleaned = nameInput.value.trim().replace(/\s+/g, "_").toLowerCase();
    if (cleaned && cleaned !== nameInput.value.trim()) {
      nameInput.value = cleaned;
      field.key = cleaned;
    }
  });
  descInput.addEventListener("input", () => {
    field.description = descInput.value;
    autosize(descInput);
  });
  typeSelect.addEventListener("change", () => {
    field.type = typeSelect.value;
  });
  reqBox.addEventListener("change", () => {
    field.required = reqBox.checked;
  });

  el.querySelector(".remove-field").addEventListener("click", () => {
    el.classList.add("removing");
    setTimeout(() => {
      el.remove();
      state.fields = state.fields.filter((f) => f.id !== field.id);
      markFieldsChanged();
    }, 200);
  });

  $("#field-list").appendChild(el);
  state.fields.push(field);
  autosize(descInput);

  if (!data.key) nameInput.focus();
  return field;
}

function autosize(ta) {
  ta.style.height = "auto";
  ta.style.height = `${Math.max(20, ta.scrollHeight)}px`;
}

function collectFields() {
  return state.fields
    .map((f) => {
      const out = {
        key: (f.el.querySelector(".field-name").value || "").trim(),
        description: (f.el.querySelector(".field-desc").value || "").trim(),
        type: f.el.querySelector(".field-type").value,
        required: f.el.querySelector(".field-required").checked,
      };
      if (f.slots) out.fields = f.slots;
      return out;
    })
    .filter((f) => f.key.length > 0);
}

function markFieldsChanged() {
  // Results no longer match the field list once it is edited.
  state.fields.forEach((f) => {
    const s = f.el.querySelector(".field-status");
    s.dataset.status = "idle";
    s.textContent = "";
  });
  updateScrapeState();
}

function updateScrapeState() {
  const btn = $("#scrape-btn");
  const meta = $("#action-meta");
  const hasDoc = !!state.documentId;
  const count = collectFields().length;
  btn.disabled = state.busy || !hasDoc || count === 0;
  if (state.busy) return;
  if (!hasDoc) meta.textContent = "Upload a document first";
  else if (count === 0) meta.textContent = "Add at least one field";
  else if (!state.llmOk) meta.textContent = "Local model offline — run `ollama serve`";
  else meta.textContent = `${count} field${count === 1 ? "" : "s"} ready · model ${state.model || "…"}`;
  meta.className = state.llmOk ? "action-meta" : "action-meta err";
}

$("#add-field").addEventListener("click", () => {
  addField();
  const list = $("#field-list");
  list.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "nearest" });
});

$("#clear-fields").addEventListener("click", () => {
  if (!state.fields.length) return;
  state.fields.forEach((f) => f.el.remove());
  state.fields = [];
  markFieldsChanged();
  toast("Fields cleared.");
});

// ---------------------------------------------------------------- templates

// Built-in field sets come from /templates.json so the UI and the test harness
// share one definition. User-made ones live in localStorage.
const CUSTOM_KEY = "docufy.customTemplates.v1";
let templateCatalog = {};      // id -> { label, hint, fields, sample?, custom? }
let currentTemplate = "";

function loadCustomTemplates() {
  try {
    return JSON.parse(localStorage.getItem(CUSTOM_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveCustomTemplates(all) {
  localStorage.setItem(CUSTOM_KEY, JSON.stringify(all));
}

async function loadTemplates() {
  try {
    const res = await fetch("/templates.json");
    const data = await res.json();
    templateCatalog = {};
    Object.entries(data.templates || {}).forEach(([id, t]) => {
      templateCatalog[id] = { ...t, custom: false };
    });
  } catch {
    templateCatalog = {};
  }

  Object.entries(loadCustomTemplates()).forEach(([id, t]) => {
    templateCatalog[`custom:${id}`] = { ...t, custom: true };
  });

  renderTemplateOptions();
}

function renderTemplateOptions() {
  const sel = $("#template-select");
  sel.innerHTML = "";
  const addGroup = (label, entries) => {
    if (!entries.length) return;
    const group = document.createElement("optgroup");
    group.label = label;
    entries.forEach(([id, t]) => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = t.label;
      group.appendChild(opt);
    });
    sel.appendChild(group);
  };

  const builtins = Object.entries(templateCatalog).filter(([, t]) => !t.custom);
  const customs = Object.entries(templateCatalog).filter(([, t]) => t.custom);
  addGroup("Templates", builtins);
  addGroup("Your templates", customs);
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "Custom (start empty)";
  sel.appendChild(custom);

  sel.value = currentTemplate && templateCatalog[currentTemplate] ? currentTemplate : "custom";
  syncTemplateControls();
}

function syncTemplateControls() {
  const t = templateCatalog[currentTemplate];
  $("#template-hint").textContent = t
    ? t.hint || ""
    : "Build your own field list, or clone a template and edit the copy.";
  $("#clone-template").hidden = !t;
  $("#save-template").hidden = !(t && t.custom);
}

function fieldsFromTemplate(t) {
  return (t?.fields || []).map((f) => ({
    key: f.key,
    description: f.description || "",
    type: f.type || "string",
    required: !!f.required,
    fields: f.fields || null,
  }));
}

function applyTemplateFields(fields) {
  state.fields.forEach((f) => f.el.remove());
  state.fields = [];
  $("#field-list").innerHTML = "";
  fields.forEach((data) => addField(data));
  markFieldsChanged();
}

function currentFieldData() {
  return collectFields().map((f) => {
    const match = templateCatalog[currentTemplate]?.fields?.find((t) => t.key === f.key);
    return match?.fields ? { ...f, fields: match.fields } : f;
  });
}

function confirmReplace(fields) {
  if (!state.fields.length) return true;
  const same =
    fields.length === state.fields.length &&
    fields.every((f, i) => collectFields()[i]?.key === f.key);
  if (same) return true;
  return window.confirm(
    `Replace your ${state.fields.length} current field${state.fields.length === 1 ? "" : "s"} ` +
    `with the ${fields.length} in this template?`
  );
}

$("#template-select").addEventListener("change", (e) => {
  const id = e.target.value;
  if (id === "custom") {
    currentTemplate = "custom";
    syncTemplateControls();
    return;
  }
  const t = templateCatalog[id];
  if (!t) return;
  const fields = fieldsFromTemplate(t);
  if (!confirmReplace(fields)) {
    e.target.value = currentTemplate || "custom";
    return;
  }
  currentTemplate = id;
  applyTemplateFields(fields);
  syncTemplateControls();
  toast(`Loaded the ${t.label} template — ${fields.length} fields`, "ok");
});

$("#clone-template").addEventListener("click", () => {
  const t = templateCatalog[currentTemplate];
  if (!t) return;
  const id = `${t.label.toLowerCase().replace(/[^a-z0-9]+/g, "_")}_copy`;
  const all = loadCustomTemplates();
  const unique = all[id] ? `${id}_${Object.keys(all).length + 1}` : id;
  all[unique] = {
    label: `${t.label} (my copy)`,
    hint: "Your copy. Edit the fields, then Save to keep the changes.",
    fields: fieldsFromTemplate(t),
  };
  saveCustomTemplates(all);
  templateCatalog[`custom:${unique}`] = { ...all[unique], custom: true };
  currentTemplate = `custom:${unique}`;
  renderTemplateOptions();
  toast(`Cloned to “${all[unique].label}”`, "ok");
});

$("#save-template").addEventListener("click", () => {
  if (!currentTemplate.startsWith("custom:")) return;
  const all = loadCustomTemplates();
  const id = currentTemplate.slice("custom:".length);
  const existing = all[id];
  const label = window.prompt("Template name", existing?.label || "My template");
  if (!label) return;
  const fields = currentFieldData();
  if (!fields.length) {
    toast("Add at least one field before saving.", "err");
    return;
  }
  all[id] = {
    label,
    hint: (existing?.hint || "Your saved template."),
    fields,
  };
  saveCustomTemplates(all);
  templateCatalog[currentTemplate] = { ...all[id], custom: true };
  renderTemplateOptions();
  toast(`Saved “${label}” with ${fields.length} fields`, "ok");
});
// ---------------------------------------------------------------- upload

const dropzone = $("#dropzone");
const fileInput = $("#file-input");

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) upload(fileInput.files[0]);
  fileInput.value = "";
});

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, () => dropzone.classList.remove("drag"))
);
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  const file = e.dataTransfer.files?.[0];
  if (file) upload(file);
});

// Also allow dropping anywhere over the viewer once a document is loaded.
const viewer = $("#viewer");
viewer.addEventListener("dragover", (e) => {
  e.preventDefault();
  if (state.documentId) dropzone.classList.add("drag");
});
viewer.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("drag");
  const file = e.dataTransfer?.files?.[0];
  if (file) upload(file);
});

async function upload(file) {
  if (state.busy) return;
  const okTypes = /\.(pdf|png|jpe?g|webp|bmp|tiff?|gif|heic|heif)$/i;
  if (!okTypes.test(file.name)) {
    toast("Unsupported file type. Use PDF, PNG, JPG, WEBP, TIFF, GIF, or HEIC.", "err");
    return;
  }
  if (file.size > 50 * 1024 * 1024) {
    toast("That file is bigger than 50 MB.", "err");
    return;
  }

  setBusy(true);
  showOverlay("Uploading…", "OCR runs when you scrape, so this is quick.");

  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/documents", { method: "POST", body: form });
    if (!res.ok) throw new Error(await errorText(res));
    const data = await res.json();

    state.documentId = data.document.document_id;
    state.documentMeta = data.document;
    state.results = null;
    state.ocr = null;

    renderDocument(data.document);

    // Reset result state for the new document.
    state.fields.forEach((f) => {
      const s = f.el.querySelector(".field-status");
      s.dataset.status = "idle";
      s.textContent = "";
    });
    $("#results-content").hidden = true;
    $("#results-empty").hidden = false;
    $("#result-count").hidden = true;
    $("#diagnostics").hidden = true;

    toast("Document uploaded — OCR runs on the next scrape.", "ok");

    // A first-run nudge: make the field builder the obvious next step.
    if (!state.fields.length) showTab("fields");
  } catch (err) {
    toast(err.message || "Upload failed.", "err");
  } finally {
    hideOverlay();
    setBusy(false);
    updateScrapeState();
  }
}
async function errorText(res) {
  try {
    const data = await res.json();
    return data.detail || res.statusText;
  } catch {
    return res.statusText;
  }
}

function renderDocument(meta) {
  $("#doc-name").textContent = meta.filename;
  $("#doc-meta").textContent = `${formatBytes(meta.bytes)} · reading on first scrape`;
  $("#file-icon").textContent = /\.pdf$/i.test(meta.filename) ? "📕" : "🖼️";

  dropzone.hidden = true;
  $("#zoom-tools").hidden = false;

  const pagesWrap = $("#pages");
  pagesWrap.hidden = false;
  pagesWrap.innerHTML = "";
  state.zoom = 0; // start fitted to width
  state.rotation = 0;

  // Page count isn't known until the PDF is opened. Show page 1 immediately and
  // add the rest once the server reports how many there are.
  addPageView(meta.document_id, 0, 0);
  $("#page-strip").hidden = true;
  applyZoom();
  discoverPages(meta.document_id);
}

function addPageView(documentId, index, delayIndex) {
  const pagesWrap = $("#pages");
  const wrap = document.createElement("div");
  wrap.className = "page-wrap";
  wrap.dataset.page = String(index);
  wrap.style.animationDelay = `${delayIndex * 60}ms`;

  const img = document.createElement("img");
  img.src = `/api/documents/${documentId}/pages/${index}.png`;
  img.alt = `Page ${index + 1}`;
  img.draggable = false;
  img.addEventListener("load", () => applyZoom());
  img.addEventListener("error", () => {
    wrap.remove();
    if (index > 0) return; // past the last page: nothing to show
    toast(`Could not render page ${index + 1}.`, "err");
  });

  const num = document.createElement("div");
  num.className = "page-num";
  num.textContent = `PAGE ${index + 1}`;

  wrap.append(img, num);
  pagesWrap.appendChild(wrap);
}

async function discoverPages(documentId) {
  let status;
  try {
    const res = await fetch(`/api/documents/${documentId}/status`);
    if (!res.ok) return;
    status = await res.json();
  } catch {
    return;
  }
  if (state.documentId !== documentId) return;

  const pages = Math.max(1, status.pages || 1);
  state.documentMeta = { ...state.documentMeta, ...status, pages };

  const existing = $$(".page-wrap").length;
  for (let i = existing; i < pages; i++) addPageView(documentId, i, i);

  renderPageStrip(documentId, pages);
  updateDocMeta();
  applyZoom();
}

function updateDocMeta() {
  const meta = state.documentMeta || {};
  const pages = meta.pages || $$(".page-wrap").length || 1;
  const bits = [`${pages} page${pages === 1 ? "" : "s"}`, formatBytes(meta.bytes || 0)];
  if (meta.ocr_confidence != null) bits.push(`OCR ${Math.round(meta.ocr_confidence * 100)}%`);
  else bits.push("OCR on scrape");
  $("#doc-meta").textContent = bits.join(" · ");
}
function renderPageStrip(documentId, pageCount) {
  const strip = $("#page-strip");
  strip.hidden = pageCount <= 1;
  strip.innerHTML = "";
  for (let i = 0; i < pageCount; i++) {
    const b = document.createElement("button");
    b.className = "page-thumb" + (i === 0 ? " active" : "");
    b.style.backgroundImage = `url(/api/documents/${documentId}/pages/${i}.png)`;
    b.title = `Page ${i + 1}`;
    b.addEventListener("click", () => {
      $$(".page-thumb").forEach((t) => t.classList.remove("active"));
      b.classList.add("active");
      const target = $(`.page-wrap[data-page="${i}"]`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    strip.appendChild(b);
  }

  // Sync the active thumbnail while scrolling.
  viewer.addEventListener(
    "scroll",
    debounce(() => {
      const wraps = $$(".page-wrap");
      const top = viewer.getBoundingClientRect().top + 60;
      let active = 0;
      wraps.forEach((w, idx) => {
        if (w.getBoundingClientRect().top <= top) active = idx;
      });
      $$(".page-thumb").forEach((t, idx) => t.classList.toggle("active", idx === active));
    }, 100)
  );
}

async function loadOcrAnnotations(documentId) {
  try {
    const res = await fetch(`/api/documents/${documentId}/ocr`);
    if (!res.ok) return;
    state.ocr = await res.json();
  } catch {
    state.ocr = null;
  }
}

// ---------------------------------------------------------------- zoom

function applyZoom() {
  const available = viewer.clientWidth - 44;
  $$(".page-wrap").forEach((wrap) => {
    const img = wrap.querySelector("img");
    if (!img || !img.naturalWidth) return;
    const fitWidth = Math.min(available, img.naturalWidth);
    const w = (state.zoom === 0 ? fitWidth : img.naturalWidth * state.zoom);
    wrap.style.width = `${w}px`;
    wrap.style.transform = state.rotation ? `rotate(${state.rotation}deg)` : "";
  });
  $("#zoom-level").textContent = state.zoom === 0 ? "Fit" : `${Math.round(state.zoom * 100)}%`;
  applyEvidence();
}

function zoomBy(delta) {
  const base = state.zoom === 0 ? 1 : state.zoom;
  state.zoom = Math.min(3, Math.max(0.25, +(base + delta).toFixed(2)));
  applyZoom();
}

$("#zoom-in").addEventListener("click", () => zoomBy(0.25));
$("#zoom-out").addEventListener("click", () => zoomBy(-0.25));
$("#zoom-fit").addEventListener("click", () => {
  state.zoom = 0;
  applyZoom();
});
$("#rotate").addEventListener("click", () => {
  state.rotation = (state.rotation + 90) % 360;
  applyZoom();
});

// Ctrl/Cmd + scroll to zoom inside the viewer.
viewer.addEventListener(
  "wheel",
  (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 0.1 : -0.1);
  },
  { passive: false }
);

window.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); zoomBy(0.25); }
  if (mod && e.key === "-") { e.preventDefault(); zoomBy(-0.25); }
  if (mod && e.key === "0") { e.preventDefault(); state.zoom = 0; applyZoom(); }
  if (mod && e.key === "Enter") { e.preventDefault(); if (!$("#scrape-btn").disabled) scrape(); }
  if (mod && e.key === "k") { e.preventDefault(); addField(); }
});

// ---------------------------------------------------------------- scraping

const scrapeBtn = $("#scrape-btn");

scrapeBtn.addEventListener("click", scrape);

function setBusy(busy, label = "Scrape document") {
  state.busy = busy;
  scrapeBtn.classList.toggle("loading", busy);
  $("#scrape-label").textContent = busy ? "Working…" : label;
  $("#progress-line").classList.toggle("on", busy);
  scrapeBtn.disabled = busy || !state.documentId || collectFields().length === 0;
}

function showOverlay(title, sub) {
  $("#overlay-title").textContent = title;
  $("#overlay-sub").textContent = sub;
  $("#overlay").hidden = false;
}
function hideOverlay() {
  $("#overlay").hidden = true;
}

let progressTimer = null;

function startProgress() {
  const meta = $("#action-meta");
  const started = performance.now();
  const tick = () => {
    const s = ((performance.now() - started) / 1000).toFixed(1);
    meta.textContent = `Reading the document with OCR, then ${state.model || "the model"}… ${s}s`;
    meta.className = "action-meta";
  };
  tick();
  clearInterval(progressTimer);
  progressTimer = setInterval(tick, 100);
  return () => {
    clearInterval(progressTimer);
    progressTimer = null;
    return (performance.now() - started) / 1000;
  };
}

async function scrape() {
  if (state.busy) return;
  if (!state.documentId) { toast("Upload a document first.", "err"); return; }
  const fields = currentFieldData();
  if (!fields.length) { toast("Add at least one field with a name.", "err"); return; }

  const duplicates = fields.map((f) => f.key.toLowerCase());
  if (new Set(duplicates).size !== duplicates.length) {
    toast("Field names must be unique.", "err");
    return;
  }

  setBusy(true);
  const stopProgress = startProgress();
  state.ocrStale = true;

  const body = {
    document_id: state.documentId,
    fields,
    model: state.model,
  };
  state.lastRequest = body;

  try {
    const res = await fetch("/api/scrape", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await errorText(res));
    const data = await res.json();

    state.results = data;
    renderResults(data);
    renderDiagnostics(data);
    renderRaw(body, data);
    markFieldStatuses(data);
    updatePassFail();
    updateScrapeState();

    // OCR has now run, so the viewer can show confidence and highlights.
    await refreshOcr(data.document_id);

    const secs = stopProgress();
    const d = data.diagnostics || {};
    $("#action-meta").textContent =
      `Done in ${secs.toFixed(1)}s · OCR ${(d.ocr_ms / 1000).toFixed(1)}s · ` +
      `model ${(d.llm_ms / 1000).toFixed(1)}s · avg confidence ` +
      `${Math.round(data.average_confidence * 100)}%`;
    $("#action-meta").className = "action-meta ok";
    toast(`Scraped ${data.fields.length} field${data.fields.length === 1 ? "" : "s"} in ${secs.toFixed(1)}s`, "ok");

    showTab("results");
    $("#result-count").hidden = false;
    $("#result-count").textContent = String(data.fields.length);
  } catch (err) {
    stopProgress();
    $("#action-meta").textContent = err.message || "Scrape failed";
    $("#action-meta").className = "action-meta err";
    toast(err.message || "Scrape failed.", "err");
  } finally {
    setBusy(false);
  }
}

async function refreshOcr(documentId) {
  try {
    const res = await fetch(`/api/documents/${documentId}/ocr`);
    if (!res.ok) return;
    state.ocr = await res.json();
    state.ocrStale = false;
    const meta = state.documentMeta || {};
    state.documentMeta = {
      ...meta,
      pages: state.ocr.pages.length || meta.pages,
      ocr_confidence: state.ocr.confidence,
    };
    updateDocMeta();
  } catch {
    /* highlights are a nicety; the scrape still stands */
  }
}

function renderDiagnostics(data) {
  const d = data.diagnostics || {};
  const box = $("#diagnostics");
  if (!d || !Object.keys(d).length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  const secs = (ms) => (ms == null ? "—" : `${(ms / 1000).toFixed(2)}s`);
  const cells = [
    ["Total", secs(d.total_ms)],
    ["OCR", `${secs(d.ocr_ms)}${d.ocr_chars ? ` · ${d.ocr_chars} ch` : ""}`],
    ["Model", secs(d.llm_ms), d.llm_ms > 20000 ? "warn" : ""],
    ["Prompt", d.prompt_tokens ? `${d.prompt_tokens} tok` : `${d.prompt_chars || 0} ch`],
    ["Output", d.output_tokens ? `${d.output_tokens} tok` : `${d.response_chars || 0} ch`],
    ["Speed", d.tokens_per_second ? `${d.tokens_per_second} tok/s` : "—"],
    ["Load", secs(d.load_ms)],
    ["First token", secs(d.prompt_eval_ms)],
    ["Attempts", d.attempts ?? "—", d.repaired ? "warn" : ""],
    ["Thinking", d.think ? "on" : "off"],
    ["Context", d.num_ctx ? `${d.num_ctx}` : "—"],
    ["OCR conf.", d.ocr_confidence != null ? `${Math.round(d.ocr_confidence * 100)}%` : "—"],
  ];

  $("#diag-grid").innerHTML = cells
    .map(([k, v, cls]) => `<div class="diag-cell ${cls || ""}"><span class="k">${escapeHtml(k)}</span><span class="v">${escapeHtml(String(v))}</span></div>`)
    .join("");

  $("#diag-sub").textContent = `${data.model || "model"} · no cache, every run is real`;
  $("#diag-steps").innerHTML = renderSteps(d, data);
  $("#raw-response").textContent = data.raw_response || "(empty reply)";
}

// The extraction runs as a pipeline: a fast values-only pass, then, only for
// fields whose value could not be found in the document, a careful pass.
function renderSteps(d, data) {
  const runs = d.runs || [];
  if (!runs.length) return "";

  const total = d.total_fields || 0;
  const verified = d.verified == null ? total : d.verified;
  const escalated = Math.max(0, total - verified);
  const esc = runs.find((r) => r.stage === "verify");

  const steps = [];

  const first = runs.find((r) => r.stage === "fast") || runs[0];
  steps.push(`
    <div class="diag-step">
      <span class="step-no">1</span>
      <div class="step-body">
        <div class="step-title">Read <span class="model-name">${escapeHtml(first.model || "")}</span> — values only</div>
        <div class="step-meta">${first.fields ?? total} fields · ${first.output_tokens ?? "—"} tokens out</div>
      </div>
      <span class="step-time">${((first.wall_ms || 0) / 1000).toFixed(1)}s</span>
    </div>`);

  // Grounding: did we find each value in the document text?
  const grounded = escalated
    ? (esc ? `Escalating ${escalated} of ${total} fields` : `${escalated} of ${total} fields not found in the text`)
    : "All values found in the document";
  steps.push(`
    <div class="diag-step ${escalated ? "escalate" : "held"}">
      <span class="step-no">${escalated ? "!" : "✓"}</span>
      <div class="step-body">
        <div class="step-title">${grounded}</div>
        <div class="step-meta">matched against the OCR text</div>
      </div>
      <span class="step-time">${verified}/${total}</span>
    </div>`);

  if (esc) {
    steps.push(`
      <div class="diag-step">
        <span class="step-no">2</span>
        <div class="step-body">
          <div class="step-title">Step up to <span class="model-name">${escapeHtml(esc.model || "")}</span> — careful pass</div>
          <div class="step-meta">only the ${esc.fields} unverified field${esc.fields === 1 ? "" : "s"} · ${esc.output_tokens ?? "—"} tokens out</div>
        </div>
        <span class="step-time">${((esc.wall_ms || 0) / 1000).toFixed(1)}s</span>
      </div>`);
  }

  return steps.join("");
}
function markFieldStatuses(data) {
  const byKey = new Map(data.fields.map((f) => [f.key.toLowerCase(), f]));
  state.fields.forEach((f) => {
    const key = (f.el.querySelector(".field-name").value || "").trim().toLowerCase();
    const result = byKey.get(key);
    const status = f.el.querySelector(".field-status");
    if (!result) { status.dataset.status = "idle"; status.textContent = ""; return; }
    const conf = Math.round(result.confidence * 100);
    if (result.value === null || (Array.isArray(result.value) && !result.value.length)) {
      status.dataset.status = "miss";
      status.textContent = "not found";
    } else if (result.confidence < 0.55) {
      status.dataset.status = "low";
      status.textContent = `${conf}% low`;
    } else {
      status.dataset.status = "done";
      status.textContent = `${conf}%`;
    }
  });
}

function renderValue(value, type) {
  if (value === null) return { html: `<span class="null">not found</span>`, cls: "null" };
  if (Array.isArray(value)) {
    if (!value.length) return { html: `<span class="null">none found</span>`, cls: "null" };
    // Structured rows (e.g. line items) read better as a small table.
    if (value.every((v) => v && typeof v === "object" && !Array.isArray(v))) {
      const cols = Object.keys(value[0]);
      const head = cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
      const body = value
        .map((row) => `<tr>${cols.map((c) => `<td>${escapeHtml(String(row[c] ?? ""))}</td>`).join("")}</tr>`)
        .join("");
      return {
        html: `<div class="rc-table-wrap"><table class="rc-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`,
        cls: "list",
      };
    }
    const items = value.map((v) => `<li>${escapeHtml(typeof v === "object" ? JSON.stringify(v) : String(v))}</li>`).join("");
    return { html: `<ul style="margin:0;padding-left:18px;font-size:13.5px;font-weight:500">${items}</ul>`, cls: "list" };
  }
  if (typeof value === "object") {
    return { html: escapeHtml(JSON.stringify(value, null, 2)), cls: "json" };
  }
  if (typeof value === "boolean") {
    return { html: value ? "true" : "false", cls: "" };
  }
  return { html: escapeHtml(String(value)), cls: "" };
}

function renderResults(data) {
  $("#results-empty").hidden = true;
  $("#results-content").hidden = false;

  const wrap = $("#result-cards");
  wrap.innerHTML = "";
  data.fields.forEach((f, i) => {
    const card = document.createElement("div");
    card.className = "result-card";
    card.style.animationDelay = `${i * 45}ms`;

    // Hovering a result highlights the source text in the document.
    card.addEventListener("mouseenter", () => highlightEvidence(f));
    card.addEventListener("mouseleave", clearHighlight);
    card.addEventListener("click", () => {
      if (f.value === null) return;
      const text = typeof f.value === "string" ? f.value : JSON.stringify(f.value, null, 2);
      copyText(String(text), "Value copied");
    });

    wrap.appendChild(card);
  });

  if (!data.fields.length) {
    wrap.innerHTML = `<div class="results-empty"><p>The model returned no fields.</p></div>`;
  }
  updatePassFail();
}

/** Colour every card and the summary ring against the current threshold. */
function updatePassFail() {
  const data = state.results;
  if (!data) return;
  const t = threshold();
  const cards = $$(".result-card");

  let passed = 0;
  let scored = 0;

  data.fields.forEach((f, i) => {
    const card = cards[i];
    if (!card) return;

    const isEmpty = f.value === null || (Array.isArray(f.value) && f.value.length === 0);
    const isPass = !isEmpty && f.confidence >= t;
    if (!isEmpty) scored++;
    if (isPass) passed++;

    const conf = Math.round(f.confidence * 100);
    const v = renderValue(f.value, f.type);
    const verdict = isEmpty ? "MISSING" : isPass ? "PASS" : "FAIL";

    card.className = `result-card ${isEmpty ? "is-missing" : isPass ? "is-pass" : "is-fail"}`;
    card.innerHTML = `
      <div class="rc-top">
        <span class="rc-key">${escapeHtml(f.key)} <span class="rc-type">${escapeHtml(f.type)}</span></span>
        <span class="rc-verdict">${verdict}</span>
      </div>
      <div class="rc-value ${v.cls}">${v.html}</div>
      <div class="rc-foot">
        <span class="conf-bar"><i style="width:${conf}%"></i></span>
        <span class="conf-num">${conf}% confidence</span>
        ${f.evidence ? `<span class="rc-evidence" title="${escapeHtml(f.evidence)}">“${escapeHtml(f.evidence)}”</span>` : ""}
      </div>
      ${
        f.ocr_warning
          ? `<div class="rc-ocr-warn" title="This value is based on text that may have been misread.">
               <span class="rc-ocr-icon">!</span>
               Double-check this value against the page
             </div>`
          : ""
      }
    `;
  });

  const total = data.fields.length;
  const pct = Math.round(t * 100);
  $("#threshold-value").textContent = `${pct}%`;
  const countEl = $("#threshold-count");
  countEl.textContent = `${passed}/${total} pass`;
  countEl.className = `threshold-count ${passed === total ? "all-pass" : passed === 0 ? "all-fail" : ""}`;
  countEl.title = "Fields with a confidence score missing counts as fail";

  // Summary ring shows the pass rate, not the raw average.
  const rate = total ? passed / total : 0;
  const circumference = 2 * Math.PI * 19;
  const ring = $("#ring-fg");
  ring.style.strokeDasharray = String(circumference);
  ring.style.stroke = rate === 1 ? "var(--good)" : rate > 0 ? "var(--warn)" : "var(--bad)";
  requestAnimationFrame(() => {
    ring.style.strokeDashoffset = String(circumference * (1 - rate));
  });
  $("#score-value").textContent = `${passed}/${total}`;

  $("#score-title").textContent =
    rate === 1 ? "All fields passed" : rate === 0 ? "No fields passed" : "Some fields need a look";
  const missing = total - scored;
  const d = data.diagnostics || {};
  $("#score-sub").textContent =
    `≥${pct}% to pass · ${((d.total_ms || 0) / 1000).toFixed(1)}s` +
    (missing ? ` · ${missing} not found` : "");
}

// ---------------------------------------------------------------- evidence highlight

let activeHighlight = null;

function normalizeText(s) {
  return String(s).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function findWordBoxes(evidence, pageWords) {
  // OCR boxes are usually whole phrases/columns, so we locate the evidence as
  // a substring of the page's normalised text mapped back onto each box.
  const target = normalizeText(evidence);
  if (!target) return [];

  const normed = pageWords.map((w) => normalizeText(w.text));

  // Build the page string, remembering which box each character came from.
  let page = "";
  const owner = [];
  pageWords.forEach((w, i) => {
    if (!normed[i]) return;
    if (page) { page += " "; owner.push(-1); }
    page += normed[i];
    for (let k = 0; k < normed[i].length; k++) owner.push(i);
  });

  const hits = new Map(); // box index -> matched char count
  let from = 0;
  while (target && from <= page.length - target.length) {
    const at = page.indexOf(target, from);
    if (at === -1) break;
    for (let ci = at; ci < at + target.length; ci++) {
      const bi = owner[ci];
      if (bi === -1) continue;
      hits.set(bi, (hits.get(bi) || 0) + 1);
    }
    from = at + target.length;
  }

  // If nothing matched verbatim, fall back to the single greatest overlap
  // between the evidence and one OCR box.
  if (!hits.size) {
    const tWords = new Set(target.split(" "));
    let best = -1;
    let bestScore = 0;
    normed.forEach((n, i) => {
      if (!n) return;
      const score = n.split(" ").filter((w) => tWords.has(w)).length;
      if (score > bestScore) { bestScore = score; best = i; }
    });
    if (best >= 0 && bestScore >= 2) hits.set(best, bestScore);
  }

  return [...hits.keys()].map((i) => pageWords[i]);
}

function highlightEvidence(field) {
  clearHighlight();
  if (!state.ocr || !field.evidence) return;
  const boxes = [];
  state.ocr.pages.forEach((page) => {
    const found = findWordBoxes(field.evidence, page.words || []);
    found.forEach((w) => boxes.push({ page: page.index, box: w.box }));
  });
  if (!boxes.length) return;

  boxes.forEach(({ page, box }) => {
    const wrap = $(`.page-wrap[data-page="${page}"]`);
    if (!wrap) return;
    const el = document.createElement("div");
    el.className = "evidence-hit";
    el.style.left = `${box[0] * 100}%`;
    el.style.top = `${box[1] * 100}%`;
    el.style.width = `${(box[2] - box[0]) * 100}%`;
    el.style.height = `${(box[3] - box[1]) * 100}%`;
    wrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add("on"));
    if (!activeHighlight) activeHighlight = [];
    activeHighlight.push(el);
  });

  // Bring the first hit into view if it is off-screen.
  const first = boxes[0];
  const wrap = $(`.page-wrap[data-page="${first.page}"]`);
  if (wrap) {
    const rect = wrap.getBoundingClientRect();
    const viewerRect = viewer.getBoundingClientRect();
    const targetY = rect.top + rect.height * first.box[1];
    if (targetY < viewerRect.top + 40 || targetY > viewerRect.bottom - 40) {
      wrap.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }
}

function clearHighlight() {
  if (activeHighlight) activeHighlight.forEach((el) => el.remove());
  activeHighlight = null;
}

function applyEvidence() {
  // Re-anchor highlights after zoom/resize (positions are percentage-based,
  // so nothing to do beyond clearing stale ones).
  clearHighlight();
}

// ---------------------------------------------------------------- raw API view

function syntaxHighlight(json) {
  const escaped = escapeHtml(json);
  return escaped.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "tok-num";
      if (/^"/.test(match)) {
        cls = /:$/.test(match) ? "tok-key" : "tok-str";
      } else if (/true|false/.test(match)) {
        cls = "tok-bool";
      } else if (/null/.test(match)) {
        cls = "tok-null";
      }
      return `<span class="${cls}">${match}</span>`;
    }
  );
}

function renderRaw(requestBody, response) {
  const minified = $("#raw-view").value === "minified";
  const json = minified ? JSON.stringify(response) : JSON.stringify(response, null, 2);
  $("#raw-json").innerHTML = syntaxHighlight(json);
  $("#raw-request").textContent =
    "POST /api/scrape\nContent-Type: application/json\n\n" +
    JSON.stringify(requestBody, null, 2);
}

$("#raw-view").addEventListener("change", () => {
  if (state.results) renderRaw(state.lastRequest, state.results);
});

$("#copy-json").addEventListener("click", () => {
  if (!state.results) return toast("Nothing to copy yet.");
  copyText(JSON.stringify(state.results, null, 2), "API response copied");
});

$("#download-json").addEventListener("click", () => {
  if (!state.results) return toast("Nothing to download yet.");
  const blob = new Blob([JSON.stringify(state.results, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `docufy-${state.documentMeta?.filename?.replace(/\.[^.]+$/, "") || "result"}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("JSON downloaded", "ok");
});

async function copyText(text, msg) {
  try {
    await navigator.clipboard.writeText(text);
    toast(msg || "Copied", "ok");
  } catch {
    // Clipboard API needs a secure context; localhost qualifies, but fall back anyway.
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast(msg || "Copied", "ok");
  }
}

// ---------------------------------------------------------------- model self-check

const checkBtn = $("#test-model");
const checkOverlay = $("#check-overlay");

checkOverlay.addEventListener("click", (e) => {
  if (e.target === checkOverlay) checkOverlay.hidden = true;
});
$("#check-close").addEventListener("click", () => {
  checkOverlay.hidden = true;
});

checkBtn.addEventListener("click", async () => {
  checkOverlay.hidden = false;
  $("#check-title").textContent = "Testing your model…";
  $("#check-sub").textContent = "Sending a tiny invoice with known answers to your local model.";
  $("#check-body").innerHTML = `<div class="spinner" style="margin:14px auto"></div>`;
  checkBtn.querySelector(".dot").className = "dot warn";

  try {
    const res = await fetch(`/api/self-check${state.model ? `?model=${encodeURIComponent(state.model)}` : ""}`, {
      method: "POST",
    });
    if (!res.ok) throw new Error(await errorText(res));
    lastCheck = await res.json();
    renderCheck();
  } catch (err) {
    checkBtn.querySelector(".dot").className = "dot bad";
    $("#check-title").textContent = "The model did not respond";
    $("#check-sub").textContent = err.message || "Unknown error";
    $("#check-body").innerHTML = "";
  }
});

let lastCheck = null;

function renderCheck() {
  const data = lastCheck;
  if (!data) return;
  const t = threshold();
  const fmt = (v) => (v === null || v === undefined ? "null" : JSON.stringify(v));

  const rows = data.fields.map((f) => {
    // A field with no value can never pass, whatever the threshold.
    const passed = f.value !== null && f.confidence >= t;
    return `<div class="check-row ${passed ? "pass" : "fail"}">
      <span class="check-mark">${passed ? "✓" : "✕"}</span>
      <code>${escapeHtml(f.key)}</code>
      <span class="check-got">${escapeHtml(fmt(f.value))}</span>
      <span class="check-conf">${Math.round(f.confidence * 100)}%</span>
      <span class="check-exp">want ${escapeHtml(fmt(f.expected))}</span>
    </div>`;
  });

  const passedCount = data.fields.filter((f) => f.value !== null && f.confidence >= t).length;
  const total = data.fields.length;
  const allPass = passedCount === total;

  checkBtn.querySelector(".dot").className = allPass ? "dot ok" : "dot warn";
  $("#check-title").textContent = allPass
    ? `Model passed — ${passedCount}/${total}`
    : `Model check — ${passedCount}/${total} pass`;
  $("#check-sub").textContent =
    `${data.model} · ${(data.duration_ms / 1000).toFixed(1)}s · pass line ${Math.round(t * 100)}% · ` +
    (allPass
      ? "Ready to scrape real documents."
      : "Red rows scored below your pass line. A larger model (e.g. qwen3:14b) often helps.");
  $("#check-body").innerHTML = rows.join("");
}

$("#threshold").addEventListener("input", () => {
  updatePassFail();
  renderCheck();
});

// ---------------------------------------------------------------- boot

window.addEventListener("resize", debounce(applyZoom, 120));

(async function boot() {
  // Field presets are the union of every built-in template's fields, so Quick
  // add always offers something useful regardless of the selected type.
  await loadTemplates();
  renderPresets();

  // Start on the invoice template: the most common thing people drop in.
  if (templateCatalog.invoice) {
    currentTemplate = "invoice";
    renderTemplateOptions();
    applyTemplateFields(fieldsFromTemplate(templateCatalog.invoice));
  } else {
    addField();
  }

  updateScrapeState();
  checkHealth();
  initRequests();
  setInterval(checkHealth, 30000);
})();

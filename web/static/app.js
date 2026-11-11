// Browser client. Text from the model and the data goes through textContent only: nothing it says runs as HTML.
(function () {
  const $ = (id) => document.getElementById(id);
  const KEY = "wfp-agent-chats";
  const EXAMPLES = [
    ["Trace a variable", "How was rCSIBorrow merged across the rounds, and what was wrong with it?"],
    ["Check the data", "Which validation checks fail, and which data issues are still open?"],
    ["Follow a trend", "How has poor or borderline food consumption changed across the annual surveys?"],
    ["Compare regions", "Quelles régions ont le plus souffert de chocs au cours des 12 derniers mois en 2023 ?"],
  ];
  let chats = load(), current = null, running = false;

  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(chats.slice(0, 30), (k, v) => (k === "tool_outputs" ? undefined : v))); } catch (e) { /* the chat still works without saved history */ } }
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

  // Small safe renderer: headings, lists, tables, bold and inline code, built as DOM nodes.
  function inline(parent, text) {
    text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).forEach((p) => {
      if (p.startsWith("**") && p.endsWith("**") && p.length > 4) parent.append(el("strong", "", p.slice(2, -2)));
      else if (p.startsWith("`") && p.endsWith("`") && p.length > 2) parent.append(el("code", "", p.slice(1, -1)));
      else if (p) parent.append(document.createTextNode(p));
    });
  }
  function markdown(container, text) {
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length;) {
      const l = lines[i];
      if (!l.trim()) { i++; continue; }
      if (/^\|.*\|\s*$/.test(l) && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
        const cells = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const t = el("table", "md"), head = t.createTHead().insertRow();
        cells(l).forEach((c) => { const th = el("th"); inline(th, c); head.append(th); });
        const body = t.createTBody();
        for (i += 2; i < lines.length && /^\|.*\|\s*$/.test(lines[i]); i++) { const r = body.insertRow(); cells(lines[i]).forEach((c) => inline(r.insertCell(), c)); }
        container.append(t); continue;
      }
      const h = l.match(/^(#{1,4})\s+(.*)/);
      if (h) { const e = el("h4"); inline(e, h[2]); container.append(e); i++; continue; }
      if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
        const list = el(/^\s*\d/.test(l) ? "ol" : "ul");
        for (; i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i]); i++) { const li = el("li"); inline(li, lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "")); list.append(li); }
        container.append(list); continue;
      }
      const p = el("p");
      inline(p, l);
      container.append(p); i++;
    }
  }

  function fmt(v) {
    if (v === null || v === undefined) return "–";
    if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toFixed(1);
    if (typeof v === "boolean") return v ? "yes" : "no";
    return String(v);
  }
  function csvCell(v) {
    let s = v === null || v === undefined ? "" : String(v);
    if (/^[=+\-@\t\r]/.test(s) && typeof v !== "number") s = "'" + s; // a text cell cannot run as a spreadsheet formula
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  function download(name, text, type) {
    const a = el("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + text], { type }));
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function table(pub) {
    const box = el("div", "result-table");
    const bar = el("div", "table-bar");
    const filter = el("input");
    filter.type = "search"; filter.placeholder = "Filter rows"; filter.setAttribute("aria-label", "Filter rows");
    const count = el("span", "muted");
    const csv = el("button", "small", "Download CSV");
    csv.type = "button";
    bar.append(filter, count, csv);
    const wrap = el("div", "table-wrap"), t = el("table", "data");
    const head = t.createTHead().insertRow(), body = t.createTBody();
    let shown = pub.rows.slice(), sortBy = null, up = true;
    pub.columns.forEach((c) => {
      const th = el("th");
      const b = el("button", "sort", c.replace(/_/g, " "));
      b.type = "button";
      b.onclick = () => { up = sortBy === c ? !up : true; sortBy = c; paint(); };
      th.append(b); head.append(th);
    });
    function paint() {
      const q = filter.value.trim().toLowerCase();
      shown = pub.rows.filter((r) => !q || pub.columns.some((c) => String(r[c] ?? "").toLowerCase().includes(q)));
      if (sortBy) shown.sort((a, b) => { const x = a[sortBy], y = b[sortBy]; if (x === y) return 0; if (x === null) return 1; if (y === null) return -1; return (x > y ? 1 : -1) * (up ? 1 : -1); });
      body.textContent = "";
      shown.forEach((r) => {
        const tr = body.insertRow();
        pub.columns.forEach((c) => { const td = tr.insertCell(); td.textContent = fmt(r[c]); td.title = fmt(r[c]); if (typeof r[c] === "number") td.className = "num"; });
      });
      count.textContent = `${shown.length} of ${pub.rows.length} rows`;
    }
    filter.oninput = paint;
    csv.onclick = () => download(`WFP_Chad_${pub.result_id}.csv`, [pub.columns.join(",")].concat(shown.map((r) => pub.columns.map((c) => csvCell(r[c])).join(","))).join("\n"), "text/csv");
    paint();
    wrap.append(t); box.append(bar, wrap);
    return box;
  }

  function sources(entry) {
    const d = el("details", "sources");
    d.append(el("summary", "", `Sources and tool calls (${(entry.tool_calls || []).length})`));
    const ol = el("ol");
    (entry.tool_calls || []).forEach((c) => {
      const li = el("li");
      li.append(el("code", "", c.tool), document.createTextNode(" " + Object.entries(c.args || {}).map(([k, v]) => `${k}=${v}`).join(", ")));
      ol.append(li);
    });
    d.append(ol);
    return d;
  }

  // The automatic check of the answer's figures against the tool results (wfp_agent/grounding.py).
  function grounding(entry) {
    const g = entry.grounding;
    if (!g || !g.checked) return null;
    if (g.ok) return el("p", "grounding ok", `All ${g.checked} figures of this answer were found in the tool results.`);
    const p = el("p", "grounding warn", `Not found in the tool results, check before use: ${g.unverified.join(", ")}.`);
    p.title = "The assistant must take every figure from its tools. These were not found there, at the precision written.";
    return p;
  }

  // Everything behind an answer, to audit or rebuild it: question, answer, tool calls and what they returned, model and data version.
  function traceButton(entry) {
    const b = el("button", "trace", "Download the trace (JSON)");
    b.type = "button";
    b.onclick = () => download(`WFP_Chad_trace_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.json`,
      JSON.stringify({ question: entry.question, answer: entry.text, grounding: entry.grounding, provenance: entry.provenance,
        tool_calls: entry.tool_calls, tool_outputs: entry.tool_outputs, published: entry.published }, null, 2), "application/json");
    return b;
  }

  function renderTurn(entry) {
    const turn = el("article", "turn");
    turn.append(el("div", "q", entry.question));
    const a = el("div", "a");
    if (entry.error) a.append(el("p", "error", entry.error));
    else {
      const text = el("div", "answer-text");
      markdown(text, entry.text || "");
      a.append(text);
      const pub = entry.published;
      if (pub) {
        const r = el("div", "result");
        r.append(el("h3", "", pub.title));
        if (pub.chart) {
          const c = el("div", "chart");
          r.append(c);
          requestAnimationFrame(() => window.wfpChart.draw(c, pub).catch(() => c.replaceWith(el("p", "error", "The chart could not be drawn; the table below has the figures."))));
        }
        if (pub.note) r.append(el("p", "note", pub.note));
        r.append(table(pub));
        // A link back to the dashboard page that holds these figures (http or https only).
        if (pub.dashboard && /^https?:\/\//.test(pub.dashboard.url)) {
          const p = el("p", "dashboard-link");
          const link = el("a", "", `See it in the dashboard: ${pub.dashboard.page} page`);
          link.href = pub.dashboard.url; link.target = "_blank"; link.rel = "noopener";
          p.append(link);
          r.append(p);
        }
        a.append(r);
      }
      const g = grounding(entry);
      if (g) a.append(g);
      a.append(sources(entry));
      if (entry.tool_calls && entry.tool_calls.length) a.append(traceButton(entry));
    }
    turn.append(a);
    return turn;
  }

  function paintSidebar() {
    const ul = $("history");
    ul.textContent = "";
    chats.forEach((c) => {
      const li = el("li"), b = el("button", c === current ? "active" : "", c.title);
      b.type = "button";
      b.onclick = () => { if (!running) { current = c; paintAll(); } };
      li.append(b); ul.append(li);
    });
    const n = current ? current.turns.reduce((s, t) => s + (t.tool_calls || []).length, 0) : 0;
    $("tool-count").textContent = n;
    $("tool-note").textContent = n ? "In this chat" : "None in this chat";
  }
  function paintAll() {
    const thread = $("thread");
    thread.textContent = "";
    $("welcome").hidden = !!(current && current.turns.length);
    if (current) current.turns.forEach((t) => thread.append(renderTurn(t)));
    paintSidebar();
    thread.lastElementChild && thread.lastElementChild.scrollIntoView({ block: "start" });
  }

  async function ask(question) {
    if (running) return;
    if (!current) { current = { title: question.slice(0, 60), turns: [] }; chats.unshift(current); }
    running = true;
    $("send").disabled = true;
    $("welcome").hidden = true;
    const history = current.turns.flatMap((t) => (t.error ? [] : [{ role: "user", content: t.question }, { role: "assistant", content: t.text || "(no text)" }]));
    const live = el("article", "turn");
    live.append(el("div", "q", question));
    const steps = el("ol", "steps");
    const wait = el("p", "working", "Working…");
    live.append(wait, steps);
    $("thread").append(live);
    live.scrollIntoView({ block: "start" });
    const entry = { question, tool_calls: [] };
    try {
      const res = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, history }) });
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || `The server answered ${res.status}.`); }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, cut); buf = buf.slice(cut + 2);
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === "tool") { steps.append(el("li", "", `${ev.tool} ${Object.values(ev.args || {}).join(" · ")}`)); $("tool-count").textContent = Number($("tool-count").textContent) + 1; }
          if (ev.type === "answer") Object.assign(entry, { text: ev.text, published: ev.published, tool_calls: ev.tool_calls, grounding: ev.grounding,
            provenance: ev.provenance, tool_outputs: ev.tool_outputs });
          if (ev.type === "error") entry.error = ev.message;
        }
      }
      if (!entry.text && !entry.error) entry.error = "The connection closed before an answer arrived.";
    } catch (e) {
      entry.error = e.message;
    }
    current.turns.push(entry);
    save();
    running = false;
    $("send").disabled = false;
    live.remove();
    $("thread").append(renderTurn(entry));
    paintSidebar();
    $("thread").lastElementChild.scrollIntoView({ block: "start" });
  }

  function start(status) {
    $("lock").hidden = true;
    $("app").hidden = false;
    const banner = $("banner");
    const notes = [];
    if (status.data && status.data.synthetic) notes.push("SYNTHETIC DEMO DATA: every value is invented.");
    if (!status.data) notes.push("The agent's tables are not built: run python build_agent_data.py.");
    if (!status.has_api_key) notes.push("No ANTHROPIC_API_KEY is set: the assistant cannot answer yet.");
    banner.textContent = notes.join(" "); banner.hidden = !notes.length;
    if (status.data) $("kicker").textContent = `${status.data.rounds} rounds · ${Number(status.data.households).toLocaleString("en-US")} households · 2018–2023`;
    const box = $("examples");
    box.textContent = "";
    EXAMPLES.forEach(([title, q], i) => {
      const b = el("button", "example");
      b.type = "button";
      b.append(el("span", "num", String(i + 1).padStart(2, "0")), el("span", "", title));
      b.onclick = () => { $("question").value = q; ask(q); $("question").value = ""; };
      box.append(b);
    });
    paintAll();
  }

  $("ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("question").value.trim(); if (q) { $("question").value = ""; ask(q); } });
  $("question").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("ask").requestSubmit(); } });
  $("new-chat").onclick = () => { if (!running) { current = null; paintAll(); $("question").focus(); } };
  $("lock").addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await fetch("/api/unlock", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: $("password").value }) });
    if (res.ok) { $("password").value = ""; boot(); }
    else { const j = await res.json().catch(() => ({})); $("lock-error").textContent = j.detail || "Could not unlock."; }
  });

  async function boot() {
    const status = await fetch("/api/status").then((r) => r.json());
    if (status.locked) { $("app").hidden = true; $("lock").hidden = false; $("password").focus(); } else start(status);
  }
  boot();
})();

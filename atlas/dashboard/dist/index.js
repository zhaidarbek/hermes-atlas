/**
 * Atlas: the Board and Settings, inner tabs of the Atlas dashboard tab. Plain IIFE, no build step:
 * React comes from window.__HERMES_PLUGIN_SDK__, and the data from the plugin's backend at
 * /api/plugins/atlas/ (atlas/board_api.py, atlas/settings_api.py).
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  const { React } = SDK;
  const h = React.createElement;
  const { useState, useEffect, useCallback, useRef } = SDK.hooks;

  const API = "/api/plugins/atlas";
  const COLUMNS = ["active", "parked", "dropped", "done"];
  const SEEN_AFTER_MS = 2000;
  const SEEN_BATCH_MS = 250;
  const SEEN_RETRY_MS = 5000;
  const SEEN_TRIES = 3;

  function send(path, method, body) {
    return SDK.fetchJSON(`${API}${path}`, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function openTranscript(path) {
    SDK.authedFetch(`${API}/${path}`)
      .then(function (res) { return res.text(); })
      .then(function (text) {
        const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
        window.open(url, "_blank", "noopener");
      });
  }

  function Badge(props) {
    const s = props.statement;
    return h("span", { className: "atlas-badge", "data-mark": s.mark, tabIndex: 0 },
      s.mark,
      h("span", { className: "atlas-pop", role: "tooltip" },
        s.citation || (s.mark === "I" ? "Atlas's inference" : "From the record"),
        s.transcript ? h("a", {
          href: "#",
          onClick: function (e) { e.preventDefault(); openTranscript(s.transcript); },
        }, "Open the transcript") : null));
  }

  function Line(props) {
    if (!props.statement) return null;
    return h("div", { className: "atlas-line" },
      h("span", { className: "atlas-label" }, props.label),
      h("span", null, props.statement.text, " ", h(Badge, { statement: props.statement })));
  }

  function useSeen(card, onSeen) {
    const ref = useRef(null);
    useEffect(function () {
      const el = ref.current;
      if (!el || !(card.new || card.changed) || !card.upto) return undefined;
      let timer = null;
      let onScreen = false;
      const stop = function () { if (timer) { clearTimeout(timer); timer = null; } };
      const start = function () {
        stop();
        if (onScreen && document.visibilityState === "visible") {
          timer = setTimeout(function () { onSeen(card); }, SEEN_AFTER_MS);
        }
      };
      const io = new IntersectionObserver(function (entries) {
        onScreen = entries.some(function (e) { return e.isIntersecting; });
        start();
      }, { threshold: 0.6 });
      io.observe(el);
      document.addEventListener("visibilitychange", start);
      return function () { stop(); io.disconnect(); document.removeEventListener("visibilitychange", start); };
    }, [card.id, card.upto, card.new, card.changed]);
    return ref;
  }

  function CardView(props) {
    const c = props.card;
    const [copied, setCopied] = useState(false);
    const ref = useSeen(c, props.onSeen);
    const copy = function () {
      navigator.clipboard.writeText(c.resume_prompt).then(function () {
        setCopied(true);
        setTimeout(function () { setCopied(false); }, 1500);
      });
    };
    const head = [c.name, c.kind, c.last_worked].filter(Boolean).join(" · ");
    return h("div", {
      ref: ref, className: "atlas-card", draggable: true, "data-pursuit": c.id,
      onDragStart: function (e) { e.dataTransfer.setData("text/atlas-pursuit", c.id); e.dataTransfer.effectAllowed = "move"; },
    },
      h("div", { className: "atlas-head" },
        props.column === "active" ? h("button", {
          className: "atlas-star", "aria-pressed": c.focus, title: c.focus ? "In Focus" : "Add to Focus",
          onClick: function () { props.onFocus(c, !c.focus); },
        }, c.focus ? "★" : "☆") : null,
        h("span", { className: "atlas-name" }, head),
        c.new ? h("span", { className: "atlas-mark" }, "NEW") : c.changed ? h("span", { className: "atlas-mark" }, "CHANGED") : null),
      c.proposals.map(function (p, i) {
        return h("div", { key: i, className: "atlas-proposal" }, p.text, " ", h(Badge, { statement: p }));
      }),
      h(Line, { label: "Left off", statement: c.left_off }),
      h(Line, { label: "Next", statement: c.next }),
      h("button", { className: "atlas-copy", onClick: copy }, copied ? "Copied" : "Copy prompt"));
  }

  function Column(props) {
    const [over, setOver] = useState(false);
    return h("section", {
      className: "atlas-column" + (over ? " atlas-over" : ""), "data-status": props.status,
      onDragOver: function (e) { e.preventDefault(); setOver(true); },
      onDragLeave: function () { setOver(false); },
      onDrop: function (e) {
        e.preventDefault();
        setOver(false);
        const id = e.dataTransfer.getData("text/atlas-pursuit");
        if (id) props.onMove(id, props.status);
      },
    },
      h("h3", null, `${props.status.toUpperCase()} (${props.cards.length})`),
      props.cards.map(function (c) {
        return h(CardView, { key: c.id, card: c, column: props.status, onFocus: props.onFocus, onSeen: props.onSeen });
      }));
  }

  function BoardPage() {
    const [board, setBoard] = useState(null);
    const [error, setError] = useState(null);
    const unseen = useRef({});
    const flushing = useRef(null);
    const load = useCallback(function () {
      return SDK.fetchJSON(`${API}/board`).then(function (b) { setBoard(b); setError(null); },
        function (e) { setError(String(e.message || e)); });
    }, []);
    useEffect(function () { load(); }, [load]);
    useEffect(function () { return function () { clearTimeout(flushing.current); }; }, []);
    const after = function (p) { return p.then(load, function (e) { setError(String(e.message || e)); }); };
    const flushSeen = function (tries) {
      flushing.current = null;
      const marks = unseen.current;
      unseen.current = {};
      send("/seen", "POST", { marks: marks }).then(load, function (e) {
        setError(String(e.message || e));
        if (tries + 1 >= SEEN_TRIES) return;
        unseen.current = Object.assign(marks, unseen.current);
        flushing.current = setTimeout(function () { flushSeen(tries + 1); }, SEEN_RETRY_MS);
      });
    };
    const onFocus = function (c, on) { after(send(`/pursuits/${encodeURIComponent(c.id)}`, "PATCH", { focus: on })); };
    const onMove = function (id, status) { after(send(`/pursuits/${encodeURIComponent(id)}`, "PATCH", { status: status })); };
    const onSeen = function (c) {
      unseen.current[c.id] = c.upto;
      if (!flushing.current) flushing.current = setTimeout(function () { flushSeen(0); }, SEEN_BATCH_MS);
    };
    if (!board) return h("div", { className: "atlas-board" }, error || "Loading the Board…");
    return h("div", { className: "atlas-board" },
      board.in_focus > board.focus_soft_cap ? h("p", { className: "atlas-focus-count" }, `${board.in_focus} in Focus`) : null,
      error ? h("p", { className: "atlas-error" }, error) : null,
      h("div", { className: "atlas-columns" }, COLUMNS.map(function (s) {
        return h(Column, { key: s, status: s, cards: board.columns[s] || [], onFocus: onFocus, onMove: onMove, onSeen: onSeen });
      })));
  }

  function Row(props) {
    return h("div", { className: "atlas-row" },
      h("span", { className: "atlas-label" }, props.label),
      h("span", { className: "atlas-value" }, props.children));
  }

  function Remove(props) {
    return h("button", { className: "atlas-remove", title: "Remove", "aria-label": "Remove " + props.what, onClick: props.onClick }, "×");
  }

  function Field(props) {
    const [value, setValue] = useState(props.value == null ? "" : String(props.value));
    useEffect(function () { setValue(props.value == null ? "" : String(props.value)); }, [props.value]);
    return h("form", {
      className: "atlas-field",
      onSubmit: function (e) { e.preventDefault(); props.onSave(value, function () { if (props.clear) setValue(""); }); },
    },
      props.before || null,
      h("input", {
        type: props.type || "text", value: value, placeholder: props.placeholder || "", "aria-label": props.label,
        onChange: function (e) { setValue(e.target.value); },
      }),
      h("button", { type: "submit" }, props.button || "Save"));
  }

  function SettingsPage() {
    const [settings, setSettings] = useState(null);
    const [error, setError] = useState(null);
    const [store, setStore] = useState("codex");
    const [day, setDay] = useState(null);
    const load = useCallback(function () {
      return SDK.fetchJSON(`${API}/settings`).then(function (s) { setSettings(s); setError(null); },
        function (e) { setError(String(e.message || e)); });
    }, []);
    useEffect(function () { load(); }, [load]);
    const change = function (patch, done) {
      send("/settings", "PATCH", patch).then(function () { if (done) done(); return load(); },
        function (e) { setError(String(e.message || e)); });
    };
    if (!settings) return h("div", { className: "atlas-settings" }, error || "Loading Settings…");
    const s = settings;
    const reviewDay = day || s.review.day;
    return h("div", { className: "atlas-settings" },
      error ? h("p", { className: "atlas-error", role: "alert" }, error) : null,
      h("section", null,
        h("h3", null, "Found on this machine"),
        s.found.map(function (f) {
          return h(Row, { key: f.harness, label: f.label }, f.roots.map(function (r) {
            return h("span", { key: r, className: "atlas-root" }, r, r === f.always
              ? h("span", { className: "atlas-quiet", title: "Atlas always reads this folder. To leave part of it out, add an exclusion." }, " found")
              : h(Remove, { what: r, onClick: function () { change({ remove_root: { harness: f.harness, root: r } }); } }));
          }));
        }),
        s.interfaces.map(function (i) { return h(Row, { key: i.label, label: i.label }, i.root + " (Interface)"); }),
        h(Field, {
          label: "Add a folder", placeholder: "~/second-codex-home", button: "Add folder", clear: true,
          before: h("select", { "aria-label": "Harness", value: store, onChange: function (e) { setStore(e.target.value); } },
            s.stores.map(function (o) { return h("option", { key: o.harness, value: o.harness }, o.label); })),
          onSave: function (v, done) { change({ add_root: { harness: store, root: v.trim() } }, done); },
        })),
      h("section", null,
        h("h3", null, "Added in settings"),
        s.added.length ? s.added.map(function (a) { return h(Row, { key: a.label, label: a.label }, a.state); })
          : h("p", { className: "atlas-quiet" }, "No web chats yet."),
        h("div", { className: "atlas-buttons" }, s.addable.map(function (a) {
          return h("button", { key: a.harness, onClick: function () { change({ add_harness: a.harness }); } }, "Add " + a.label);
        }))),
      h("section", null,
        h("h3", null, "Exclusions"),
        h("p", { className: "atlas-quiet" }, "Sessions run in these folders, or below them, are never collected."),
        s.exclusions.length ? s.exclusions.map(function (x) {
          return h("div", { key: x, className: "atlas-root" }, x,
            h(Remove, { what: x, onClick: function () { change({ remove_exclusion: x }); } }));
        }) : h("p", { className: "atlas-quiet" }, "None named."),
        h(Field, {
          label: "Add an exclusion", placeholder: "~/clients", button: "Add exclusion", clear: true,
          onSave: function (v, done) { change({ add_exclusion: v.trim() }, done); },
        })),
      h("section", null,
        h("h3", null, `Times (${s.timezone})`),
        h(Row, { label: "Daily Brief" }, h(Field, {
          label: "Daily Brief time", type: "time", value: s.brief_at,
          onSave: function (v) { change({ brief_at: v }); },
        })),
        h(Row, { label: "Review" }, h(Field, {
          label: "Review time", type: "time", value: s.review.at,
          before: h("select", { "aria-label": "Review day", value: reviewDay, onChange: function (e) { setDay(e.target.value); } },
            s.days.map(function (d) { return h("option", { key: d, value: d }, d[0].toUpperCase() + d.slice(1)); })),
          onSave: function (v) { change({ review: { day: reviewDay, at: v } }, function () { setDay(null); }); },
        })),
        h(Row, { label: "Active Time gap" }, h(Field, {
          label: "Active Time gap in minutes", type: "number", value: s.gap_minutes,
          onSave: function (v) { change({ gap_minutes: Number(v) }); },
        }), " minutes")),
      h("section", null,
        h("h3", null, "Atlas's own chat"),
        h("p", { className: "atlas-quiet" }, s.atlas_chat
          ? "Every message you type in this chat or topic is Atlas's."
          : "Atlas uses your Hermes home chat. Name a chat or topic to give Atlas its own."),
        h(Field, {
          label: "Atlas's own chat", value: s.atlas_chat, placeholder: "telegram:<chat id>:<topic id>",
          onSave: function (v) { change({ atlas_chat: v.trim() || null }); },
        })));
  }

  function AtlasTab() {
    const [tab, setTab] = useState("board");
    const tabs = [["board", "Board"], ["settings", "Settings"]];
    return h("div", { className: "atlas-tab" },
      h("nav", { className: "atlas-tabs", role: "tablist" }, tabs.map(function (t) {
        return h("button", {
          key: t[0], role: "tab", "aria-selected": tab === t[0],
          className: tab === t[0] ? "atlas-tab-on" : "atlas-tab-off",
          onClick: function () { setTab(t[0]); },
        }, t[1]);
      })),
      tab === "board" ? h(BoardPage) : h(SettingsPage));
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("atlas", AtlasTab);
  }
})();

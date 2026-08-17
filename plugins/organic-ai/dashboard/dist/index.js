(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  var React = SDK.React;
  var hooks = SDK.hooks || React;
  var h = React.createElement;
  var useState = hooks.useState || React.useState;
  var useEffect = hooks.useEffect || React.useEffect;
  var useCallback = hooks.useCallback || React.useCallback;
  var API_BASE = "/api/plugins/organic-ai";

  function api(path, options) {
    return SDK.fetchJSON(API_BASE + path, options);
  }

  function postJSON(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function asText(value, fallback) {
    if (value === undefined || value === null || value === "") return fallback || "";
    if (typeof value === "boolean") return value ? "yes" : "no";
    return String(value);
  }

  function errorText(error) {
    var raw = String((error && error.message) || error || "Unknown error");
    var match = raw.match(/\{.*\}$/);
    if (match) {
      try {
        var parsed = JSON.parse(match[0]);
        if (parsed && parsed.detail) return String(parsed.detail);
      } catch (_) {}
    }
    return raw;
  }

  function formatBytes(value) {
    var n = Number(value || 0);
    if (!Number.isFinite(n) || n <= 0) return "0 B";
    var units = ["B", "KB", "MB", "GB"];
    var idx = 0;
    while (n >= 1024 && idx < units.length - 1) {
      n = n / 1024;
      idx += 1;
    }
    return (idx === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[idx];
  }

  function formatTime(value) {
    if (!value) return "";
    var d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString();
  }

  function pill(text, tone) {
    return h("span", { className: "oa-pill " + (tone || "") }, text);
  }

  function KeyValue(props) {
    return h("div", { className: "oa-kv" },
      h("span", null, props.label),
      h("strong", null, asText(props.value, props.fallback || "none"))
    );
  }

  function Stat(props) {
    return h("div", { className: "oa-stat " + (props.tone || "") },
      h("span", null, props.label),
      h("strong", null, asText(props.value, "0"))
    );
  }

  function Panel(props) {
    return h("section", { className: "oa-panel " + (props.className || "") },
      h("div", { className: "oa-panel-head" },
        h("h3", null, props.title),
        props.action || null
      ),
      h("div", { className: "oa-panel-body" }, props.children)
    );
  }

  function Empty(props) {
    return h("div", { className: "oa-empty" }, props.children || "No data");
  }

  function ActionButton(props) {
    return h("button", {
      className: "oa-btn " + (props.primary ? "primary " : "") + (props.danger ? "danger " : ""),
      disabled: !!props.disabled,
      title: props.title || props.children,
      onClick: props.onClick,
      type: "button",
    }, props.children);
  }

  function pipelineStatus(last, name) {
    if (!last) return "waiting";
    var meta = last.metadata || {};
    var route = String(last.route || "");
    if (name === "semantic") return last.request_id ? "done" : "waiting";
    if (name === "gate") return last.gate ? "done" : "waiting";
    if (name === "executive") return route ? "done" : "waiting";
    if (name === "weave") {
      return meta.active_weave_claim_count || meta.raw_retrieval_count || meta.preflight_confidence ? "done" : "waiting";
    }
    if (name === "processor") return meta.processor_cycles ? "done" : (meta.core_invoked ? "waiting" : "skipped");
    if (name === "evidence") return meta.growth_invoked || meta.growth_evidence_count ? "done" : "skipped";
    if (name === "compiler") return meta.growth_invoked || meta.memory_committed || meta.claims_added ? "done" : "skipped";
    if (name === "memory") return meta.memory_ids && meta.memory_ids.length ? "done" : (meta.growth_invoked ? "done" : "waiting");
    if (name === "result") return last.answer ? "done" : "waiting";
    if (name === "complete") return meta.semantic_completeness_complete === true ? "done" : (meta.semantic_completeness_complete === false ? "needs-loop" : "waiting");
    return "waiting";
  }

  function Pipeline(props) {
    var last = props.last;
    var steps = [
      ["semantic", "Semantic Interface LLM"],
      ["gate", "Interaction Gate"],
      ["executive", "Organic Executive"],
      ["weave", "Cognition / Active Weave"],
      ["processor", "Organic Processor"],
      ["evidence", "Evidence Broker / web"],
      ["compiler", "Memory Compiler / Validator"],
      ["memory", "Living Memory"],
      ["result", "Result"],
      ["complete", "Completeness loop"],
    ];
    return h("div", { className: "oa-pipeline" },
      steps.map(function (step) {
        var status = pipelineStatus(last, step[0]);
        return h("div", { key: step[0], className: "oa-step " + status },
          h("span", { className: "oa-step-dot" }),
          h("span", { className: "oa-step-name" }, step[1]),
          h("span", { className: "oa-step-status" }, status)
        );
      })
    );
  }

  function Message(props) {
    var msg = props.message;
    var meta = msg.metadata || {};
    return h("div", { className: "oa-message " + msg.role },
      h("div", { className: "oa-message-text" }, msg.text),
      msg.route || meta.route ? h("div", { className: "oa-message-meta" },
        pill(msg.route || meta.route, "info"),
        meta.processor_cycles ? pill("processor", "good") : pill("processor skipped", ""),
        meta.growth_invoked ? pill("growth", "good") : pill("growth idle", ""),
        meta.semantic_completeness_complete === true ? pill("complete", "good") : null,
        meta.hard_blocked ? pill("blocked", "bad") : null
      ) : null
    );
  }

  function ObjectList(props) {
    var rows = props.rows || [];
    if (!rows.length) return h(Empty, null, "No " + props.label);
    return h("div", { className: "oa-list" },
      rows.slice(0, props.limit || 20).map(function (row, index) {
        var title = row[props.titleKey] || row.goal || row.title || row.name || row.task_id || row.claim || "item";
        var sub = row[props.subKey] || row.status || row.url || row.path || row.created_at || row.modified || "";
        return h("div", { className: "oa-row", key: String(title) + index },
          h("div", { className: "oa-row-title" }, String(title)),
          h("div", { className: "oa-row-sub" }, props.formatSub ? props.formatSub(row, sub) : String(sub || ""))
        );
      })
    );
  }

  function ToolsList(props) {
    var rows = props.rows || [];
    if (!rows.length) return h(Empty, null, "No tools");
    return h("div", { className: "oa-list" },
      rows.map(function (tool, index) {
        var routes = (tool.allowed_routes || []).join(", ");
        var safe = tool.semantic_safe ? "semantic safe" : "gate controlled";
        return h("div", { className: "oa-row", key: String(tool.name) + index },
          h("div", { className: "oa-row-title" }, tool.name),
          h("div", { className: "oa-row-sub" }, routes ? routes + " | " + safe : tool.description || "")
        );
      })
    );
  }

  function Diagnostics(props) {
    var state = props.state || {};
    return h("pre", { className: "oa-json" }, JSON.stringify({
      state: state,
      last_response: props.last || null,
      memory: props.memory || null,
    }, null, 2));
  }

  function OrganicPage() {
    var statePair = useState(null);
    var state = statePair[0];
    var setState = statePair[1];
    var tasksPair = useState([]);
    var tasks = tasksPair[0];
    var setTasks = tasksPair[1];
    var sourcesPair = useState([]);
    var sources = sourcesPair[0];
    var setSources = sourcesPair[1];
    var packagesPair = useState([]);
    var packages = packagesPair[0];
    var setPackages = packagesPair[1];
    var toolsPair = useState([]);
    var tools = toolsPair[0];
    var setTools = toolsPair[1];
    var memoryPair = useState({ concepts: [], relations: [] });
    var memory = memoryPair[0];
    var setMemory = memoryPair[1];
    var tabPair = useState("overview");
    var tab = tabPair[0];
    var setTab = tabPair[1];
    var busyPair = useState(false);
    var busy = busyPair[0];
    var setBusy = busyPair[1];
    var textPair = useState("");
    var text = textPair[0];
    var setText = textPair[1];
    var errorPair = useState("");
    var error = errorPair[0];
    var setError = errorPair[1];
    var noticePair = useState("");
    var notice = noticePair[0];
    var setNotice = noticePair[1];
    var transcriptPair = useState([]);
    var transcript = transcriptPair[0];
    var setTranscript = transcriptPair[1];
    var last = transcript.length ? transcript[transcript.length - 1].response : null;

    var refresh = useCallback(function () {
      return Promise.all([
        api("/state"),
        api("/tasks?limit=30").catch(function () { return { tasks: [] }; }),
        api("/sources?limit=30").catch(function () { return { sources: [] }; }),
        api("/tools").catch(function () { return { tools: [], mvp_tools: [] }; }),
        api("/packages?limit=20").catch(function () { return { packages: [], mirrored_packages: [] }; }),
        api("/memory?limit=60").catch(function () { return { concepts: [], relations: [] }; }),
      ]).then(function (results) {
        setState(results[0]);
        setTasks(results[1].tasks || []);
        setSources(results[2].sources || []);
        setTools([].concat(results[3].tools || [], results[3].mvp_tools || []));
        setPackages([].concat(results[4].packages || [], results[4].mirrored_packages || []));
        setMemory(results[5] || { concepts: [], relations: [] });
        setError("");
      }).catch(function (err) {
        setError(errorText(err));
      });
    }, []);

    useEffect(function () {
      refresh();
      var timer = window.setInterval(function () {
        if (!busy) refresh();
      }, 5000);
      return function () { window.clearInterval(timer); };
    }, [refresh, busy]);

    function runAction(label, fn) {
      setBusy(true);
      setNotice(label);
      setError("");
      return fn().then(function (result) {
        if (result && result.path) setNotice(result.path);
        else setNotice(label + " complete");
        return refresh();
      }).catch(function (err) {
        setError(errorText(err));
      }).finally(function () {
        setBusy(false);
      });
    }

    function send() {
      var clean = text.trim();
      if (!clean || busy) return;
      setText("");
      setTranscript(function (rows) {
        return rows.concat([{ role: "user", text: clean, time: Date.now() }]);
      });
      setBusy(true);
      setNotice("Running Organic workflow");
      setError("");
      postJSON("/message", { text: clean }).then(function (response) {
        setTranscript(function (rows) {
          return rows.concat([{
            role: "assistant",
            text: response.answer || "",
            route: response.route,
            metadata: response.metadata || {},
            response: response,
            time: Date.now(),
          }]);
        });
        setNotice("Workflow complete");
        return refresh();
      }).catch(function (err) {
        var msg = errorText(err);
        setError(msg);
        setTranscript(function (rows) {
          return rows.concat([{ role: "error", text: msg, time: Date.now() }]);
        });
      }).finally(function () {
        setBusy(false);
      });
    }

    var runtime = (state && state.runtime) || {};
    var engine = (state && state.engine) || {};
    var counts = (state && (state.counts || engine.counts)) || {};
    var settings = (state && state.settings) || {};
    var web = (state && (state.web || engine.web)) || {};
    var mcp = (state && state.mcp) || {};
    var rolling = (state && state.rolling_context) || {};
    var chat = (state && state.hermes_chat) || {};
    var executive = engine.executive || engine.core || {};
    var processor = engine.processor || {};
    var startupError = state && state.startup_error;
    var healthy = runtime.status !== "error";

    var tabs = [
      ["overview", "Overview"],
      ["memory", "Memory"],
      ["sources", "Sources"],
      ["packages", "Packages"],
      ["tools", "Tools"],
      ["diagnostics", "Debug"],
    ];

    return h("div", { className: "oa-root" },
      h("div", { className: "oa-titlebar" },
        h("div", null,
          h("h1", null, "Organic AI"),
          h("div", { className: "oa-subtitle" }, "Hermes dashboard fork")
        ),
        h("div", { className: "oa-status" },
          pill(healthy ? "runtime ready" : "runtime error", healthy ? "good" : "bad"),
          pill(engine.idle_growth_enabled ? "idle growth on" : "idle growth off", engine.idle_growth_enabled ? "good" : "warn"),
          pill(mcp.connected ? "mcp connected" : (mcp.configured ? "mcp configured" : "mcp missing"), mcp.connected ? "good" : "warn"),
          pill(settings.model || chat.model || "model unknown", "info")
        )
      ),
      startupError ? h("div", { className: "oa-alert" }, startupError) : null,
      error ? h("div", { className: "oa-alert" }, error) : null,
      notice ? h("div", { className: "oa-notice" }, notice) : null,
      h("div", { className: "oa-actions" },
        h(ActionButton, { onClick: refresh, disabled: busy }, "Refresh"),
        h(ActionButton, {
          primary: true,
          disabled: busy,
          onClick: function () { runAction("Growth requested", function () { return postJSON("/growth", { cycles: 1 }); }); },
        }, "Run Growth"),
        h(ActionButton, {
          disabled: busy,
          onClick: function () { runAction("Idle growth changed", function () { return postJSON("/idle", { enabled: !engine.idle_growth_enabled }); }); },
        }, engine.idle_growth_enabled ? "Idle Off" : "Idle On"),
        h(ActionButton, {
          disabled: busy,
          onClick: function () { runAction("Review package", function () { return postJSON("/export", { reason: "manual_hermes_dashboard" }); }); },
        }, "Export Review"),
        h(ActionButton, {
          disabled: busy,
          onClick: function () { runAction("MCP registered", function () { return postJSON("/mcp/register", {}); }); },
        }, "Register MCP"),
        h(ActionButton, {
          disabled: busy,
          onClick: function () { runAction("Runtime restarted", function () { return postJSON("/restart-runtime", {}); }); },
        }, "Restart Runtime")
      ),
      h("div", { className: "oa-layout" },
        h("main", { className: "oa-chat" },
          h(Panel, { title: "Conversation" },
            h("div", { className: "oa-transcript" },
              transcript.length ? transcript.map(function (msg, index) {
                return h(Message, { key: index, message: msg });
              }) : h(Empty, null, "No conversation in this dashboard session")
            ),
            h("div", { className: "oa-composer" },
              h("textarea", {
                value: text,
                disabled: busy,
                placeholder: "Ask through the Organic workflow",
                onChange: function (event) { setText(event.target.value); },
                onKeyDown: function (event) {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    send();
                  }
                },
              }),
              h(ActionButton, { primary: true, disabled: busy || !text.trim(), onClick: send }, busy ? "Running" : "Send")
            )
          ),
          h(Panel, { title: "Pipeline" }, h(Pipeline, { last: last }))
        ),
        h("aside", { className: "oa-side" },
          h("div", { className: "oa-stats" },
            h(Stat, { label: "Sources", value: counts.sources || 0 }),
            h(Stat, { label: "Claims", value: counts.claims || 0 }),
            h(Stat, { label: "Concepts", value: counts.concepts || 0 }),
            h(Stat, { label: "Relations", value: counts.relations || 0 }),
            h(Stat, { label: "Run Results", value: state ? state.run_result_count : 0 }),
            h(Stat, { label: "Packages", value: state ? state.review_package_count : 0 })
          ),
          h(Panel, { title: "Organic Engine" },
            h(KeyValue, { label: "Running", value: engine.running }),
            h(KeyValue, { label: "Current task", value: engine.current_task_id }),
            h(KeyValue, { label: "Idle growth", value: engine.idle_growth_enabled }),
            h(KeyValue, { label: "User active", value: engine.external_user_active }),
            h(KeyValue, { label: "Manual budget", value: engine.manual_growth_budget || 0 }),
            h(KeyValue, { label: "Idle cycles", value: engine.completed_idle_cycles || 0 }),
            h(KeyValue, { label: "Executive mode", value: executive.mode || executive.kind || "available" }),
            h(KeyValue, { label: "Processor mode", value: processor.mode || "unavailable" }),
            h(KeyValue, { label: "Processor experiences", value: processor.experience_count || 0 }),
            h(KeyValue, { label: "Processor composites", value: processor.composite_count || 0 }),
            h(KeyValue, { label: "Processor state", value: processor.state_bytes || 0 })
          ),
          h(Panel, { title: "Semantic Interface" },
            h(KeyValue, { label: "Mode", value: settings.semantic_mode }),
            h(KeyValue, { label: "Adapter", value: settings.semantic_adapter }),
            h(KeyValue, { label: "Model", value: settings.model }),
            h(KeyValue, { label: "Ollama URL", value: settings.ollama_base_url }),
            h(KeyValue, { label: "PydanticAI", value: state && state.pydantic_ai_available ? "available" : "not installed" })
          ),
          h(Panel, { title: "Hermes Chat Bridge" },
            h(KeyValue, { label: "Toolsets", value: chat.toolsets }),
            h(KeyValue, { label: "Provider", value: chat.provider }),
            h(KeyValue, { label: "Model", value: chat.model }),
            h(KeyValue, { label: "Base URL", value: chat.base_url }),
            h(KeyValue, { label: "Web", value: web.mode || web.provider || "auto" })
          )
        )
      ),
      h("div", { className: "oa-tabs" },
        tabs.map(function (entry) {
          return h("button", {
            key: entry[0],
            type: "button",
            className: tab === entry[0] ? "active" : "",
            onClick: function () { setTab(entry[0]); },
          }, entry[1]);
        })
      ),
      h("div", { className: "oa-tabbody" },
        tab === "overview" ? h("div", { className: "oa-grid two" },
          h(Panel, { title: "Recent Tasks" }, h(ObjectList, { rows: tasks, label: "tasks", titleKey: "goal", subKey: "status" })),
          h(Panel, { title: "Rolling Cognition" },
            h(KeyValue, { label: "Active objective", value: rolling.active_objective }),
            h(KeyValue, { label: "Updated", value: rolling.updated_at }),
            h("pre", { className: "oa-json small" }, rolling.summary || "")
          )
        ) : null,
        tab === "memory" ? h("div", { className: "oa-grid two" },
          h(Panel, { title: "Concepts" }, h(ObjectList, { rows: memory.concepts || [], label: "concepts", titleKey: "label", subKey: "kind" })),
          h(Panel, { title: "Relations" }, h(ObjectList, { rows: memory.relations || [], label: "relations", titleKey: "relation", subKey: "confidence" }))
        ) : null,
        tab === "sources" ? h(Panel, { title: "Sources" },
          h(ObjectList, { rows: sources, label: "sources", titleKey: "title", subKey: "url" })
        ) : null,
        tab === "packages" ? h(Panel, { title: "Review Packages" },
          h(ObjectList, {
            rows: packages,
            label: "packages",
            titleKey: "name",
            subKey: "path",
            formatSub: function (row, sub) { return formatBytes(row.bytes) + " | " + formatTime(row.modified) + " | " + sub; },
          })
        ) : null,
        tab === "tools" ? h("div", { className: "oa-grid two" },
          h(Panel, { title: "Organic Tools" }, h(ToolsList, { rows: tools })),
          h(Panel, { title: "MCP" },
            h(KeyValue, { label: "Configured", value: mcp.configured }),
            h(KeyValue, { label: "Connected", value: mcp.connected }),
            h(KeyValue, { label: "Discovery", value: mcp.discovery_in_flight ? "running" : "idle" }),
            h("pre", { className: "oa-json small" }, JSON.stringify(mcp.config || mcp.status || {}, null, 2))
          )
        ) : null,
        tab === "diagnostics" ? h(Panel, { title: "Debug Information" }, h(Diagnostics, { state: state, last: last, memory: memory })) : null
      )
    );
  }

  window.__HERMES_PLUGINS__.register("organic-ai", OrganicPage);
})();

import React, { useEffect, useState, useRef, useCallback, memo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Bot,
  Plus,
  Search,
  FolderOpen,
  MessageSquare,
  Settings2,
  PanelLeft,
  PanelRight,
  Ellipsis,
  Paperclip,
  ArrowUp,
  Square,
  Play,
  RotateCw,
  X,
  Copy,
  FileText,
  Download,
  History,
  Brain,
  HardDrive,
  ChevronDown,
  ChevronRight,
  Check,
  CheckCheck,
  LoaderCircle,
  Pin,
  Trash2,
  ExternalLink,
  GitBranch,
  BookOpen,
  Puzzle,
  BookmarkCheck,
  Monitor,
  ListChecks,
  AlertCircle,
  Globe,
  Archive,
  Upload,
  Save,
} from "lucide-react";
import {
  api,
  imageFile,
  visibleMessage,
  FileItem,
  Message,
  Chat,
  Job,
} from "./api";

import { Attachment, ChatMessage } from "./components/Messages";
import { DialogView } from "./components/Dialogs";

type Dialog = { type: string; file?: FileItem; section?: string; data?: any };
const phases: Record<string, [string, string]> = {
  preparing: ["Preparing request", "Připravuji dotaz"],
  loading_model: ["Loading model", "Načítám model"],
  thinking: ["Thinking", "Přemýšlím"],
  answering: ["Writing answer", "Píšu odpověď"],
  preparing_tool: ["Preparing action", "Připravuji akci"],
  executing: ["Running action", "Provádím akci"],
  idle: ["Ready", "Připraven"],
};

export function App() {
  const [app, setApp] = useState<any>(null),
    [sid, setSid] = useState(""),
    [chat, setChat] = useState<Chat | null>(null),
    [detail, setDetail] = useState<any>(null);
  const [runtime, setRuntime] = useState<any>({}),
    [tab, setTab] = useState("results"),
    [panel, setPanel] = useState(true),
    [nav, setNav] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null),
    [text, setText] = useState(""),
    [attachments, setAttachments] = useState<FileItem[]>([]),
    [uploading, setUploading] = useState(0);
  const [toast, setToast] = useState(""),
    [search, setSearch] = useState(""),
    [searchResults, setSearchResults] = useState<any[] | null>(null),
    [sending, setSending] = useState(false),
    [delivery, setDelivery] = useState("steer"),
    [connected, setConnected] = useState(true),
    [newMessages, setNewMessages] = useState(false);
  const sidRef = useRef(""),
    scrollRef = useRef<HTMLDivElement>(null),
    fileRef = useRef<HTMLInputElement>(null),
    textRef = useRef<HTMLTextAreaElement>(null),
    draftReady = useRef(false),
    stick = useRef(true),
    pendingRequest = useRef<{
      id: string;
      text: string;
      files: string[];
    } | null>(null),
    loadGeneration = useRef(0);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (app?.active?.session_id !== sid) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [app?.active?.session_id, sid]);
  const cs = app?.preferences?.language === "cs";
  useEffect(() => {
    if (app?.preferences?.send_mode) setDelivery(app.preferences.send_mode);
  }, [app?.preferences?.send_mode]);
  const tr = useCallback((en: string, cz: string) => (cs ? cz : en), [cs]);
  const error = useCallback(
    (e: unknown) => setToast(e instanceof Error ? e.message : String(e)),
    [],
  );
  const refresh = useCallback(async () => {
    const next = await api(
      "/api/state" + (sidRef.current ? "?session_id=" + sidRef.current : ""),
    );
    setApp(next);
    if (!sidRef.current) setSid(next.session_id);
    return next;
  }, []);
  const refreshDetail = useCallback(async () => {
    const current = sidRef.current;
    if (current) {
      const value = await api("/api/sessions/" + current + "/detail");
      if (sidRef.current === current) setDetail(value);
    }
  }, []);
  const reloadChat = useCallback(async () => {
    const current = sidRef.current;
    if (!current) return;
    const value = await api<Chat>("/api/sessions/" + current);
    if (sidRef.current === current)
      setChat((old) =>
        old ? { ...value, messages: reconcileMessages(old, value) } : value,
      );
  }, []);
  useEffect(() => {
    refresh().catch(error);
  }, [refresh, error]);
  useEffect(() => {
    if (!sid) return;
    sidRef.current = sid;
    draftReady.current = false;
    loadGeneration.current++;
    const generation = loadGeneration.current;
    setChat(null);
    setDetail(null);
    setText("");
    setAttachments([]);
    stick.current = true;
    setNewMessages(false);
    api<Chat>("/api/sessions/" + sid)
      .then((value) => {
        if (generation !== loadGeneration.current) return;
        setChat(value);
        let draft = value.draft;
        try {
          const local = JSON.parse(
            localStorage.getItem("marvin.draft." + sid) || "null",
          );
          if (local) draft = local;
        } catch {}
        setText(draft.text || "");
        setAttachments(draft.attachments || []);
        setPanel(value.meta.work_mode !== "discussion");
        draftReady.current = true;
      })
      .catch(error);
    api("/api/sessions/" + sid + "/detail")
      .then((value) => {
        if (generation === loadGeneration.current) setDetail(value);
      })
      .catch(error);
    api("/api/sessions/" + sid + "/select", "POST").catch(error);
  }, [sid, error]);
  useEffect(() => {
    if (!app) return;
    document.title = "Marvin v" + app.version;
    document.documentElement.lang = cs ? "cs" : "en";
    document.documentElement.dataset.theme = app.preferences.theme || "dark";
    document.documentElement.dataset.density =
      app.preferences.density || "comfortable";
  }, [app, cs]);
  useEffect(() => {
    if (!app) return;
    const source = new EventSource("/api/events?after=" + app.sequence);
    let timer: ReturnType<typeof setTimeout> | undefined;
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (event) => {
      const row = JSON.parse(event.data),
        p = row.payload;
      const current = row.session_id === sidRef.current;
      if (row.kind === "live" && current)
        setChat((old) => (old ? { ...old, live: p } : old));
      if (row.kind === "message" && current)
        setChat((old) =>
          old ? { ...old, messages: mergeMessages(old.messages, [p]) } : old,
        );
      if (row.kind === "navigate" && current) setSid(p.session_id);
      if (
        [
          "submission",
          "run_status",
          "session_changed",
          "queue_changed",
          "settings_changed",
          "tool_completed",
        ].includes(row.kind)
      ) {
        clearTimeout(timer);
        timer = setTimeout(() => {
          refresh().catch(error);
          if (current) {
            reloadChat().catch(error);
            refreshDetail().catch(error);
          }
        }, 180);
      }
      if (row.kind === "run_status" && p.status === "failed")
        setToast(p.error || p.text || "Task failed");
    };
    return () => {
      source.close();
      clearTimeout(timer);
    };
  }, [!!app, refresh, reloadChat, refreshDetail, error]);
  useEffect(() => {
    if (!app) return;
    const poll = () =>
      api("/api/runtime")
        .then((value) => {
          setRuntime(value);
          if (panel && tab === "progress") refreshDetail().catch(error);
        })
        .catch(() => {});
    poll();
    const timer = setInterval(poll, 5000);
    return () => clearInterval(timer);
  }, [!!app, panel, tab, refreshDetail, error]);
  useEffect(() => {
    if (!draftReady.current || !sid) return;
    localStorage.setItem(
      "marvin.draft." + sid,
      JSON.stringify({ text, attachments }),
    );
    const timer = setTimeout(
      () =>
        api("/api/sessions/" + sid + "/actions/draft", "POST", {
          text,
          attachments,
        }).catch(error),
      350,
    );
    return () => clearTimeout(timer);
  }, [text, attachments, sid, error]);
  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    if (stick.current)
      requestAnimationFrame(() => {
        scroller.scrollTop = scroller.scrollHeight;
      });
    else setNewMessages(true);
  }, [chat?.messages, chat?.live]);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (search.trim())
        api("/api/search?query=" + encodeURIComponent(search.trim()))
          .then(setSearchResults)
          .catch(error);
      else setSearchResults(null);
    }, 250);
    return () => clearTimeout(timer);
  }, [search, error]);
  const act = useCallback(
    async (action: string, payload: any = {}) => {
      const result = await api(
        "/api/sessions/" + sidRef.current + "/actions/" + action,
        "POST",
        payload,
      );
      if (result.session_id) {
        sidRef.current = result.session_id;
        setSid(result.session_id);
      }
      if (action === "delete") return result;
      await refresh();
      await reloadChat();
      await refreshDetail();
      return result;
    },
    [refresh, reloadChat, refreshDetail],
  );
  const settings = async (value: any) => {
    await api("/api/settings", "PATCH", value);
    await refresh();
  };
  const addFiles = async (files: File[]) => {
    const current = sidRef.current;
    setUploading((n) => n + files.length);
    try {
      const results = await Promise.all(
        files.map(async (file) => {
          const form = new FormData();
          form.append("file", file);
          return api<FileItem>(
            "/api/sessions/" + current + "/attachments",
            "POST",
            form,
          );
        }),
      );
      if (sidRef.current === current)
        setAttachments((items) => [...items, ...results]);
      else {
        const draft = await api<Chat>("/api/sessions/" + current);
        await api("/api/sessions/" + current + "/actions/draft", "POST", {
          ...draft.draft,
          attachments: [...(draft.draft.attachments || []), ...results],
        });
      }
    } catch (e) {
      error(e);
    } finally {
      setUploading((n) => n - files.length);
    }
  };
  const submit = async () => {
    if (sending || uploading || (!text.trim() && !attachments.length)) return;
    const current = sidRef.current;
    setSending(true);
    const files = attachments.map((f) => f.id);
    try {
      pendingRequest.current = JSON.parse(
        localStorage.getItem("marvin.send." + current) || "null",
      );
    } catch {}
    const draft = { text, files };
    if (
      !pendingRequest.current ||
      pendingRequest.current.text !== text ||
      pendingRequest.current.files.join() !== files.join()
    )
      pendingRequest.current = { id: crypto.randomUUID(), ...draft };
    localStorage.setItem(
      "marvin.send." + current,
      JSON.stringify(pendingRequest.current),
    );
    try {
      const result = await api("/api/sessions/" + current + "/submit", "POST", {
        text,
        attachments: files,
        request_id: pendingRequest.current.id,
        delivery,
      });
      if (sidRef.current === current) {
        setText("");
        setAttachments([]);
      }
      await api("/api/sessions/" + current + "/actions/draft", "POST", {
        text: "",
        attachments: [],
      });
      pendingRequest.current = null;
      localStorage.removeItem("marvin.send." + current);
      localStorage.removeItem("marvin.draft." + current);
      await refresh();
      await reloadChat();
      if (result.status === "steering")
        setToast(tr("Clarification received", "Upřesnění přijato"));
    } catch (e) {
      error(e);
    } finally {
      setSending(false);
    }
  };
  const pick = async (folder = true) => {
    const value = await api("/api/pick", "POST", { folder });
    return value.path as string | null;
  };
  const selectProject = async (id: string) => {
    const value = await api("/api/projects/select", "POST", { id: id || null });
    sidRef.current = value.session_id;
    setSid(value.session_id);
    setNav(false);
    await refresh();
  };
  const newChat = async () => {
    const project = app?.projects?.find(
      (p: any) => p.path === chat?.meta.workspace,
    );
    const value = await api("/api/sessions", "POST", {
      project_id: project?.id,
      mode: chat?.meta.work_mode || "discussion",
    });
    sidRef.current = value.session_id;
    setSid(value.session_id);
    setNav(false);
    await refresh();
  };
  const openExport = async (format: string, research = false) => {
    const file = await act("export", { format, research });
    setDialog({ type: "preview", file });
  };
  const retryAnswer = useCallback(
    () => act("retry").catch(error),
    [act, error],
  );
  const openSource = useCallback(
    (id: string) => setDialog({ type: "sources", data: id }),
    [],
  );
  const closeDialog = useCallback(() => setDialog(null), []);
  const openFile = useCallback(
    (file: FileItem) => setDialog({ type: "preview", file }),
    [],
  );
  const active = app?.active?.session_id === sid,
    mode = chat?.meta.work_mode || "discussion";
  const project = app?.projects?.find(
    (p: any) => p.path === chat?.meta.workspace,
  );
  const queued: Job[] = (app?.queue || []).filter(
    (j: Job) =>
      j.session_id === sid && ["queued", "steering"].includes(j.status),
  );
  const interrupted = (chat?.jobs || [])
    .filter((j) =>
      ["interrupted", "stopped", "failed", "waiting_confirmation"].includes(
        j.status,
      ),
    )
    .at(-1);
  const live = chat?.live;
  const savedLive = chat?.messages.some(
    (m) =>
      m.role === "assistant" &&
      m.run_id === live?.run_id &&
      m.step_id === live?.step,
  );
  const contextUsed =
    Object.values(detail?.context?.breakdown || {}).reduce<number>(
      (a, v) => a + Number(v),
      0,
    ) ||
    detail?.context?.estimated_tokens ||
    0;
  if (!app)
    return (
      <div className="startup">
        <Bot size={32} />
        <h1>Marvin</h1>
        <p>{toast || "Loading workspace…"}</p>
      </div>
    );
  return (
    <div
      className="app"
      onDragOver={(e) => {
        if ([...e.dataTransfer.types].includes("Files")) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={(e) => {
        if (e.dataTransfer.files.length) {
          e.preventDefault();
          addFiles([...e.dataTransfer.files]);
        }
      }}
    >
      <header className="topbar">
        <button
          className="icon mobile-nav"
          aria-label={tr("Projects and chats", "Projekty a chaty")}
          onClick={() => setNav(!nav)}
        >
          <PanelLeft />
        </button>
        <div className="brand">
          <Bot />
          Marvin <small>v{app.version}</small>
        </div>
        <span className="spacer" />
        <button
          className="runtime-button"
          onClick={() => setDialog({ type: "settings", section: "model" })}
        >
          <span
            className={
              "dot " + (runtime.status === "running" ? "ready" : "waiting")
            }
          />
          <span>
            {
              app.models.find(
                (m: any) => m.id === (runtime.model || app.preferences.model),
              )?.name
            }
          </span>
          <span className="muted">
            {runtime.switch?.status === "starting"
              ? tr("Loading", "Načítám")
              : runtime.status === "running"
                ? tr("Ready", "Připraven")
                : tr("Stopped", "Zastaven")}
          </span>
          <ChevronDown />
        </button>
        <button
          className="icon"
          title={tr("Settings", "Nastavení")}
          aria-label={tr("Settings", "Nastavení")}
          onClick={() => setDialog({ type: "settings", section: "model" })}
        >
          <Settings2 />
        </button>
      </header>
      <div className="workspace">
        <aside className={"sidebar " + (nav ? "open" : "")}>
          <button className="positive" onClick={() => newChat().catch(error)}>
            <Plus />
            {tr("New chat", "Nový chat")}
          </button>
          <label className="search">
            <Search />
            <input
              aria-label={tr("Search chats", "Hledat v chatech")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={tr("Search chats", "Hledat v chatech")}
            />
          </label>
          <label className="section-label">{tr("PROJECT", "PROJEKT")}</label>
          <div className="row">
            <select
              aria-label={tr("Project", "Projekt")}
              value={project?.id || ""}
              onChange={(e) => selectProject(e.target.value).catch(error)}
            >
              <option value="">{tr("No project", "Bez projektu")}</option>
              {app.projects.map((p: any) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button
              className="icon"
              aria-label={tr("Project actions", "Operace projektu")}
              onClick={() => setDialog({ type: "project" })}
            >
              <Ellipsis />
            </button>
          </div>
          <label className="section-label">
            {searchResults
              ? tr("SEARCH RESULTS", "VÝSLEDKY HLEDÁNÍ")
              : tr("CONVERSATIONS", "KONVERZACE")}
          </label>
          <nav
            className="chat-list"
            aria-label={tr("Conversations", "Konverzace")}
          >
            {(
              searchResults ||
              app.sessions.filter(
                (s: any) =>
                  (s.workspace || null) === (chat?.meta.workspace || null),
              )
            ).map((s: any) => (
              <button
                key={s.id}
                className={sid === s.id ? "selected" : ""}
                onClick={() => {
                  setSid(s.id);
                  setNav(false);
                }}
              >
                <MessageSquare />
                <span>
                  {s.title || tr("New conversation", "Nová konverzace")}
                  {app.active?.session_id === s.id && (
                    <small className="green">{tr("Working", "Pracuji")}</small>
                  )}
                  {searchResults && <small>{s.snippet}</small>}
                </span>
              </button>
            ))}
          </nav>
          <button
            className="nav-button"
            onClick={() => setDialog({ type: "library" })}
          >
            <FolderOpen />
            {tr("Project documents", "Podklady projektu")}
          </button>
          <button
            className="nav-button"
            onClick={() => setDialog({ type: "decisions" })}
          >
            <BookmarkCheck />
            {tr("Project decisions", "Přijatá rozhodnutí")}
          </button>
          <div className="sidebar-bottom">
            <button
              className="nav-button"
              onClick={() => setDialog({ type: "settings", section: "memory" })}
            >
              <Brain />
              {tr("Memory and skills", "Paměť a skilly")}
            </button>
            <button
              className="nav-button"
              onClick={() => setDialog({ type: "settings", section: "data" })}
            >
              <HardDrive />
              {tr("Data and backups", "Data a zálohy")}
            </button>
          </div>
        </aside>
        <main className="main">
          <div className="chat-heading">
            <input
              key={chat?.id || sid}
              aria-label={tr("Chat title", "Název chatu")}
              defaultValue={chat?.meta.title || ""}
              placeholder={tr("New conversation", "Nová konverzace")}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  act("rename", { title: e.currentTarget.value }).catch(error);
                  e.currentTarget.blur();
                }
              }}
            />
            <select
              aria-label={tr("Work mode", "Pracovní režim")}
              value={mode}
              onChange={(e) =>
                act("mode", { mode: e.target.value }).catch(error)
              }
            >
              {app.modes.map((m: any, i: number) => (
                <option key={m.id} value={m.id}>
                  {cs
                    ? ["Diskuze", "Výzkum", "Psaní", "Vývoj", "Počítač"][i]
                    : m.label}
                </option>
              ))}
            </select>
            <button
              className="icon"
              aria-label={tr("Toggle details", "Zobrazit detail")}
              onClick={() => setPanel(!panel)}
            >
              <PanelRight />
            </button>
            <button
              className="icon"
              aria-label={tr("Chat actions", "Operace chatu")}
              onClick={() => setDialog({ type: "chat" })}
            >
              <Ellipsis />
            </button>
          </div>
          <div className={"chat-workspace " + (panel ? "with-detail" : "")}>
            <div className="chat-column">
              <div
                className="messages"
                ref={scrollRef}
                onScroll={() => {
                  const s = scrollRef.current;
                  if (s) {
                    stick.current =
                      s.scrollHeight - s.scrollTop - s.clientHeight < 80;
                    if (stick.current) setNewMessages(false);
                  }
                }}
              >
                {chat?.before !== null && chat?.before !== undefined && (
                  <button
                    className="older"
                    onClick={async () => {
                      const value = await api<Chat>(
                        "/api/sessions/" + sid + "?before=" + chat.before,
                      );
                      setChat((old) =>
                        old
                          ? {
                              ...old,
                              messages: mergeMessages(
                                value.messages,
                                old.messages,
                              ),
                              before: value.before,
                            }
                          : old,
                      );
                    }}
                  >
                    {tr("Earlier messages", "Starší zprávy")}
                  </button>
                )}
                {chat?.messages.filter(visibleMessage).map((m) => (
                  <ChatMessage
                    key={m.id}
                    message={m}
                    cs={cs}
                    openFile={openFile}
                    openSource={openSource}
                    retry={retryAnswer}
                  />
                ))}
                {chat && !chat.messages.some(visibleMessage) && (
                  <div className="empty-chat">
                    <Bot size={34} />
                    <h2>
                      {tr(
                        "What shall we work on?",
                        "Na čem spolu budeme pracovat?",
                      )}
                    </h2>
                  </div>
                )}
                {active && !savedLive && (live?.text || live?.reasoning) && (
                  <article className="message assistant live">
                    <div className="message-label">
                      <Bot />
                      Marvin
                    </div>
                    {live.reasoning && (
                      <details open={false}>
                        <summary>{tr("Thinking", "Přemýšlení")}</summary>
                        <Markdown remarkPlugins={[remarkGfm]}>
                          {live.reasoning}
                        </Markdown>
                      </details>
                    )}
                    {live.text && (
                      <Markdown remarkPlugins={[remarkGfm]}>
                        {live.text}
                      </Markdown>
                    )}
                  </article>
                )}
                {queued.length > 0 && app.queue_paused && (
                  <button
                    className="positive"
                    onClick={() =>
                      api("/api/queue/resume", "POST")
                        .then(refresh)
                        .catch(error)
                    }
                  >
                    <Play />
                    {tr("Resume queued messages", "Spustit zprávy ve frontě")}
                  </button>
                )}
                {queued.map((job) => (
                  <div className="queued" key={job.id}>
                    <div className="row">
                      <ListChecks />
                      <strong>
                        {job.status === "steering"
                          ? tr("Clarification received", "Upřesnění přijato")
                          : tr("After current task", "Po aktuální úloze")}
                      </strong>
                      <span className="spacer" />
                      <button
                        className="icon"
                        aria-label={tr(
                          "Edit queued message",
                          "Upravit zprávu ve frontě",
                        )}
                        onClick={() =>
                          setDialog({ type: "queue-edit", data: job })
                        }
                      >
                        <FileText />
                      </button>
                      <button
                        className="icon"
                        aria-label={tr(
                          "Cancel queued message",
                          "Zrušit zprávu ve frontě",
                        )}
                        onClick={() =>
                          api("/api/queue/" + job.id, "PATCH", { cancel: true })
                            .then(refresh)
                            .catch(error)
                        }
                      >
                        <X />
                      </button>
                    </div>
                    <p>{job.payload.text}</p>
                  </div>
                ))}
              </div>
              {newMessages && (
                <button
                  className="new-messages"
                  onClick={() => {
                    stick.current = true;
                    const s = scrollRef.current;
                    if (s) s.scrollTop = s.scrollHeight;
                    setNewMessages(false);
                  }}
                >
                  {tr("New messages", "Nové zprávy")}
                  <ChevronDown />
                </button>
              )}
              <div className="composer-area">
                {interrupted && !active && (
                  <div className="attention">
                    <div>
                      <strong>
                        {interrupted.status === "waiting_confirmation"
                          ? tr("Waiting for confirmation", "Čekám na potvrzení")
                          : tr(
                              "Task can be continued",
                              "Úloha může pokračovat",
                            )}
                      </strong>
                      {interrupted.payload.error && (
                        <p>{interrupted.payload.error}</p>
                      )}
                    </div>
                    <button
                      className="positive"
                      onClick={() =>
                        act("resume", {
                          approve:
                            interrupted.status === "waiting_confirmation"
                              ? true
                              : undefined,
                        }).catch(error)
                      }
                    >
                      <Play />
                      {interrupted.status === "waiting_confirmation"
                        ? tr("Allow", "Povolit")
                        : tr("Continue", "Pokračovat")}
                    </button>
                    {interrupted.status === "waiting_confirmation" && (
                      <button
                        className="danger"
                        onClick={() =>
                          act("resume", { approve: false }).catch(error)
                        }
                      >
                        {tr("Deny", "Zamítnout")}
                      </button>
                    )}
                  </div>
                )}
                {active && (
                  <div className="activity" role="status">
                    <LoaderCircle className="spin" />
                    <span>
                      {tr(...(phases[live?.phase] || phases.preparing))}
                      {live?.tool && " · " + live.tool}
                      {live?.tool_chars > 0 &&
                        " · " + Math.round(live.tool_chars / 1024) + " KB"}
                      {live?.started &&
                        " · " +
                          Math.max(0, Math.round(now / 1000 - live.started)) +
                          " s"}
                      {" · ~" +
                        formatTokens(
                          Math.round(
                            ((live?.text?.length || 0) +
                              (live?.reasoning?.length || 0) +
                              (live?.tool_chars || 0)) /
                              3.6,
                          ),
                        ) +
                        " tok"}
                    </span>
                    <button
                      onClick={() => {
                        setTab("progress");
                        setPanel(true);
                      }}
                    >
                      {tr("Progress", "Průběh")}
                      <ChevronRight />
                    </button>
                  </div>
                )}
                {app.active && !active && (
                  <div className="activity muted">
                    <span>
                      {tr(
                        "Another chat is working; this message will be queued.",
                        "Pracuje jiný chat; tato zpráva se zařadí do fronty.",
                      )}
                    </span>
                  </div>
                )}
                <div
                  className="composer"
                  onPaste={(e) => {
                    const files = [...e.clipboardData.items]
                      .filter(
                        (i) => i.kind === "file" && i.type.startsWith("image/"),
                      )
                      .map((i) => i.getAsFile())
                      .filter((x): x is File => !!x);
                    if (files.length) {
                      e.preventDefault();
                      addFiles(files);
                    }
                  }}
                >
                  <div className="attachment-list">
                    {attachments.map((file) => (
                      <Attachment
                        key={file.id}
                        file={file}
                        open={() => openFile(file)}
                        remove={() =>
                          setAttachments((items) =>
                            items.filter((i) => i.id !== file.id),
                          )
                        }
                        cs={cs}
                      />
                    ))}
                    {uploading > 0 && (
                      <span className="uploading">
                        <LoaderCircle className="spin" />
                        {tr("Adding attachments", "Přidávám přílohy")} (
                        {uploading})
                      </span>
                    )}
                  </div>
                  <textarea
                    ref={textRef}
                    aria-label={tr("Message", "Zpráva")}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                      if (
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        !e.nativeEvent.isComposing
                      ) {
                        e.preventDefault();
                        submit();
                      }
                    }}
                    placeholder={
                      active
                        ? tr("Add a clarification…", "Doplňte upřesnění…")
                        : tr("Type a message…", "Napište zprávu…")
                    }
                  />
                  {text.startsWith("/") && !text.includes("\n") && (
                    <div className="slash-menu">
                      {Object.entries(app.commands as Record<string, string>)
                        .filter(([key]) => key.startsWith(text.split(" ")[0]))
                        .map(([key, value]) => (
                          <button
                            key={key}
                            onClick={() => {
                              setText(key + " ");
                              textRef.current?.focus();
                            }}
                          >
                            <code>{key}</code>
                            <span>{value}</span>
                          </button>
                        ))}
                    </div>
                  )}
                  <div className="composer-toolbar">
                    <button
                      className="attach"
                      onClick={() => fileRef.current?.click()}
                    >
                      <Paperclip />
                      Attach
                    </button>
                    <input
                      ref={fileRef}
                      type="file"
                      multiple
                      hidden
                      onChange={(e) => {
                        if (e.target.files) addFiles([...e.target.files]);
                        e.target.value = "";
                      }}
                    />
                    <select
                      aria-label={tr("Thinking", "Myšlení")}
                      value={app.preferences.thinking}
                      onChange={(e) =>
                        settings({ thinking: e.target.value }).catch(error)
                      }
                    >
                      {["xhigh", "medium", "low", "off"].map((e) => (
                        <option key={e} value={e}>
                          {tr("Thinking", "Myšlení")}: {e}
                        </option>
                      ))}
                    </select>
                    <span className="spacer" />
                    {active && (
                      <select
                        aria-label={tr("Message delivery", "Zpracování zprávy")}
                        value={delivery}
                        onChange={(e) => setDelivery(e.target.value)}
                      >
                        <option value="steer">
                          {tr("Clarify now", "Upřesnit nyní")}
                        </option>
                        <option value="queue">
                          {tr("After completion", "Po dokončení")}
                        </option>
                      </select>
                    )}
                    {active && (
                      <button
                        className="icon danger stop"
                        aria-label={tr("Stop task", "Zastavit úlohu")}
                        title={tr("Stop task", "Zastavit úlohu")}
                        onClick={() => act("stop").catch(error)}
                      >
                        <Square />
                      </button>
                    )}
                    <button
                      className="icon positive send"
                      disabled={sending || uploading > 0}
                      aria-label={tr("Send", "Odeslat")}
                      title={tr("Send", "Odeslat")}
                      onClick={submit}
                    >
                      {sending ? (
                        <LoaderCircle className="spin" />
                      ) : (
                        <ArrowUp />
                      )}
                    </button>
                  </div>
                </div>
                <div className="composer-footer">
                  <span>
                    {connected
                      ? tr("Saved locally", "Uloženo lokálně")
                      : tr("Reconnecting…", "Obnovuji spojení…")}
                  </span>
                  <button
                    onClick={() => {
                      setTab("context");
                      setPanel(true);
                    }}
                  >
                    {tr("Context", "Kontext")}: ~{formatTokens(contextUsed)} /{" "}
                    {formatTokens(detail?.context?.limit || 0)}
                    <ChevronRight />
                  </button>
                </div>
              </div>
            </div>
            {panel && (
              <aside className="detail">
                <nav className="detail-tabs">
                  {[
                    ["results", "Results", "Výsledky"],
                    ["progress", "Progress", "Průběh"],
                    ["context", "Context", "Kontext"],
                  ].map(([id, en, cz]) => (
                    <button
                      key={id}
                      className={tab === id ? "selected" : ""}
                      onClick={() => setTab(id)}
                    >
                      {tr(en, cz)}
                    </button>
                  ))}
                </nav>
                <div className="detail-body">
                  {tab === "results" ? (
                    <>
                      <section>
                        <h3>{tr("This conversation", "V této konverzaci")}</h3>
                        {!detail?.results?.length && (
                          <p className="muted">
                            {tr(
                              "Created files will appear here.",
                              "Zde se objeví vytvořené soubory.",
                            )}
                          </p>
                        )}
                        {detail?.results?.map((f: FileItem) => (
                          <div className="file-row" key={f.id}>
                            <FileText />
                            <button onClick={() => openFile(f)}>
                              <strong>{f.name}</strong>
                              <small>
                                {f.kind === "changed"
                                  ? tr("Changed file", "Upravený soubor")
                                  : tr("Result", "Výsledek")}
                              </small>
                            </button>
                            <button
                              className="icon"
                              aria-label={tr("Open folder", "Otevřít složku")}
                              onClick={() =>
                                api("/api/files/" + f.id + "/open", "POST", {
                                  folder: true,
                                }).catch(error)
                              }
                            >
                              <FolderOpen />
                            </button>
                          </div>
                        ))}
                      </section>
                      {detail?.research && (
                        <section>
                          <h3>{tr("Research", "Výzkum")}</h3>
                          <p>
                            {detail.research.sources.length}{" "}
                            {tr("loaded sources", "načtených zdrojů")}
                          </p>
                          <button
                            onClick={() => setDialog({ type: "sources" })}
                          >
                            <Globe />
                            {tr("All sources", "Všechny zdroje")}
                          </button>
                          <div className="row">
                            <button
                              onClick={() =>
                                openExport("pdf", true).catch(error)
                              }
                            >
                              PDF
                            </button>
                            <button
                              onClick={() =>
                                openExport("docx", true).catch(error)
                              }
                            >
                              DOCX
                            </button>
                            <button
                              onClick={() =>
                                act("export_sources")
                                  .then((f) =>
                                    setDialog({ type: "preview", file: f }),
                                  )
                                  .catch(error)
                              }
                            >
                              <Download />
                              {tr("Sources", "Zdroje")}
                            </button>
                          </div>
                        </section>
                      )}
                      <section>
                        <h3>{tr("Validation", "Ověření")}</h3>
                        {detail?.plan?.validations?.length ? (
                          detail.plan.validations.map((v: any, i: number) => (
                            <div className="check-row" key={i}>
                              {v.status === "passed" ? (
                                <CheckCheck className="green" />
                              ) : (
                                <AlertCircle className="amber" />
                              )}
                              <span>
                                {v.label}
                                <small>{v.status}</small>
                              </span>
                            </div>
                          ))
                        ) : (
                          <p className="muted">
                            {tr(
                              "No completed checks recorded.",
                              "Zatím nejsou zaznamenané dokončené kontroly.",
                            )}
                          </p>
                        )}
                      </section>
                      <section>
                        <button
                          className="wide"
                          onClick={() => setDialog({ type: "export" })}
                        >
                          <Download />
                          {tr("Export conversation", "Exportovat konverzaci")}
                        </button>
                        <button
                          className="wide"
                          onClick={() => setDialog({ type: "checkpoints" })}
                        >
                          <History />
                          {tr("Restore points", "Body obnovy")}
                        </button>
                      </section>
                    </>
                  ) : tab === "progress" ? (
                    <>
                      <section>
                        <h3>
                          {detail?.plan?.goal ||
                            tr("Current task", "Aktuální úloha")}
                        </h3>
                        {detail?.plan?.steps?.map((s: any) => (
                          <div className="check-row" key={s.id}>
                            {s.status === "completed" ? (
                              <Check className="green" />
                            ) : s.status === "in_progress" ? (
                              <LoaderCircle className="spin" />
                            ) : (
                              <span className="small-circle" />
                            )}
                            <span>
                              {s.text}
                              <small>{s.note}</small>
                            </span>
                          </div>
                        ))}
                      </section>
                      <section>
                        <h3>{tr("Activity history", "Historie průběhu")}</h3>
                        {detail?.notices?.map((n: any) => (
                          <p key={n.seq}>{n.text}</p>
                        ))}
                      </section>
                      <section>
                        <h3>{tr("Processes", "Procesy")}</h3>
                        {detail?.processes?.map((p: any) => (
                          <div className="process" key={p.process_id}>
                            <strong>{p.command}</strong>
                            <p>
                              {p.status} · {Math.round(p.elapsed_seconds)} s
                            </p>
                            <button
                              onClick={() =>
                                setDialog({
                                  type: "process-output",
                                  data: { id: p.process_id },
                                })
                              }
                            >
                              <FileText />
                              {tr("Output", "Výstup")}
                            </button>
                            {p.status === "running" && (
                              <button
                                className="danger"
                                onClick={() =>
                                  act("stop_process", {
                                    id: p.process_id,
                                  }).catch(error)
                                }
                              >
                                <Square />
                                {tr("Stop", "Zastavit")}
                              </button>
                            )}
                          </div>
                        ))}
                      </section>
                      <section>
                        <h3>Browser</h3>
                        <p>
                          {detail?.browser?.running
                            ? detail.browser.url
                            : tr("Closed", "Zavřený")}
                        </p>
                        {detail?.browser?.running && (
                          <button
                            onClick={() => act("close_browser").catch(error)}
                          >
                            <X />
                            {tr("Close browser", "Zavřít browser")}
                          </button>
                        )}
                      </section>
                      <section>
                        <h3>{tr("Files changed", "Změny souborů")}</h3>
                        {detail?.changes?.files
                          ?.filter((f: any) => f.changed)
                          .map((f: any) => (
                            <p key={f.path}>{f.path}</p>
                          ))}
                        <button
                          className="wide"
                          onClick={() => act("revert").catch(error)}
                        >
                          <History />
                          {tr("Revert task changes", "Vrátit změny úlohy")}
                        </button>
                      </section>
                    </>
                  ) : (
                    <>
                      <section>
                        <h3>{tr("Context usage", "Využití kontextu")}</h3>
                        <div className="meter">
                          <div
                            style={{
                              width:
                                Math.min(
                                  100,
                                  (100 * contextUsed) /
                                    (detail?.context?.limit || 1),
                                ) + "%",
                            }}
                          />
                        </div>
                        <p>
                          ~{formatTokens(contextUsed)} /{" "}
                          {formatTokens(detail?.context?.limit || 0)}
                        </p>
                        <p className="muted">
                          {tr(
                            "Estimate; measured usage below is from the last completed request.",
                            "Odhad; naměřené hodnoty níže patří poslednímu dokončenému dotazu.",
                          )}
                        </p>
                        {detail?.context?.usage?.prompt_tokens !==
                          undefined && (
                          <p>
                            {tr("Measured input", "Naměřený vstup")}:{" "}
                            {detail.context.usage.prompt_tokens}
                            <br />
                            {tr("Generated", "Vygenerováno")}:{" "}
                            {detail.context.usage.completion_tokens}
                          </p>
                        )}
                      </section>
                      <section>
                        <h3>{tr("Active memories", "Aktivní paměti")}</h3>
                        {detail?.context?.snapshot && (
                          <button
                            onClick={() =>
                              setDialog({
                                type: "context-snapshot",
                                data: detail.context.snapshot,
                              })
                            }
                          >
                            <History />
                            {tr(
                              "Context used by this run",
                              "Kontext použitý tímto během",
                            )}
                          </button>
                        )}
                        {["global", "mode", "project"].map((scope, i) => (
                          <div className="check-row" key={scope}>
                            {detail?.memory?.[scope]?.path ? (
                              <Check className="green" />
                            ) : (
                              <X />
                            )}
                            <span>
                              {cs
                                ? ["Globální", "Režimová", "Projektová"][i]
                                : ["Global", "Work mode", "Project"][i]}
                            </span>
                          </div>
                        ))}
                        <button
                          onClick={() =>
                            setDialog({ type: "settings", section: "memory" })
                          }
                        >
                          <Brain />
                          {tr("Open memories", "Otevřít paměti")}
                        </button>
                      </section>
                      <section>
                        <h3>{tr("Pinned files", "Připnuté soubory")}</h3>
                        {detail?.context?.pinned_files?.map((p: string) => (
                          <div className="file-row" key={p}>
                            <Pin />
                            <span>{p.split(/[\\/]/).at(-1)}</span>
                            <button
                              className="icon"
                              onClick={() =>
                                act("unpin", { path: p }).catch(error)
                              }
                              aria-label={tr("Unpin", "Odepnout")}
                            >
                              <X />
                            </button>
                          </div>
                        ))}
                        <button
                          onClick={() =>
                            pick(false)
                              .then((p) => p && act("pin", { path: p }))
                              .catch(error)
                          }
                        >
                          <Plus />
                          {tr("Pin file", "Připnout soubor")}
                        </button>
                        <button onClick={() => act("clear_pins").catch(error)}>
                          {tr("Unpin all", "Odepnout vše")}
                        </button>
                      </section>
                      <section>
                        <h3>{tr("Loaded skills", "Načtené skilly")}</h3>
                        {detail?.context?.active_skills?.map((s: string) => (
                          <p key={s}>{s}</p>
                        ))}
                        <button
                          className="wide"
                          onClick={() => act("compress").catch(error)}
                        >
                          <Archive />
                          {tr("Compress", "Komprimovat")}
                        </button>
                        <button
                          className="wide"
                          onClick={() => act("handoff").catch(error)}
                        >
                          <MessageSquare />
                          {tr("Hand off to new chat", "Předat novému chatu")}
                        </button>
                      </section>
                    </>
                  )}
                </div>
              </aside>
            )}
          </div>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <span>{toast}</span>
          <button
            className="icon"
            aria-label={tr("Dismiss", "Zavřít")}
            onClick={() => setToast("")}
          >
            <X />
          </button>
        </div>
      )}
      {dialog && (
        <DialogView
          dialog={dialog}
          close={closeDialog}
          setDialog={setDialog}
          app={app}
          chat={chat}
          detail={detail}
          sid={sid}
          tr={tr}
          cs={cs}
          act={act}
          settings={settings}
          error={error}
          refresh={refresh}
          setSid={setSid}
          pick={pick}
          runtime={runtime}
        />
      )}
    </div>
  );
}

function mergeMessages(before: Message[], after: Message[]) {
  const result = [...before],
    positions = new Map(result.map((m, i) => [m.id, i]));
  after.forEach((m) => {
    const index = positions.get(m.id);
    if (index !== undefined) result[index] = m;
    else {
      positions.set(m.id, result.length);
      result.push(m);
    }
  });
  return result;
}
function reconcileMessages(old: Chat, next: Chat) {
  if (next.before === null) return next.messages;
  const first = old.messages.findIndex((m) => m.id === next.messages[0]?.id);
  return first > 0
    ? [...old.messages.slice(0, first), ...next.messages]
    : next.messages;
}
function formatTokens(value: number) {
  return value >= 1000
    ? (value / 1000).toFixed(value < 10000 ? 1 : 0) + "k"
    : String(value);
}

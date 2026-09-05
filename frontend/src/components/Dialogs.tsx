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
} from "../api";

import { DecisionEditor } from "./Decisions";

export function DialogView(props: any) {
  const {
    dialog,
    close,
    setDialog,
    app,
    chat,
    detail,
    sid,
    tr,
    cs,
    act,
    settings,
    error,
    refresh,
    setSid,
    pick,
    runtime,
  } = props;
  const [content, setContent] = useState(""),
    [name, setName] = useState(""),
    [scope, setScope] = useState("global"),
    [library, setLibrary] = useState<FileItem[]>([]),
    [backup, setBackup] = useState<any>({}),
    [maintenance, setMaintenance] = useState<any[]>([]),
    [page, setPage] = useState(1),
    [format, setFormat] = useState("pdf"),
    [busy, setBusy] = useState(false);
  const memoryDirty = useRef(false);
  const section = dialog.section || "model";
  const project = app.projects.find(
    (p: any) => p.path === chat?.meta.workspace,
  );
  const call = async (fn: () => Promise<any>) => {
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      error(e);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const modal = document.querySelector<HTMLElement>(".modal");
    const surfaces =
      document.querySelectorAll<HTMLElement>(".workspace,.topbar");
    surfaces.forEach((element) => (element.inert = true));
    modal?.querySelector<HTMLButtonElement>("button")?.focus();
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab" && modal) {
        const controls = Array.from(
          modal.querySelectorAll<HTMLElement>(
            "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]",
          ),
        ).filter((element) => element.getClientRects().length > 0);
        const first = controls[0],
          last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      surfaces.forEach((element) => (element.inert = false));
      previous?.focus();
    };
  }, [close]);
  useEffect(() => {
    setName(dialog.type === "queue-edit" ? dialog.data.payload.text : "");
    if (dialog.type === "library")
      api("/api/sessions/" + sid + "/library")
        .then(setLibrary)
        .catch(error);
    if (dialog.type === "settings" && section === "data") {
      api("/api/backup").then(setBackup).catch(error);
      api("/api/maintenance").then(setMaintenance).catch(error);
    }
  }, [dialog.type, section, sid, error]);
  useEffect(() => {
    if (
      dialog.type === "settings" &&
      section === "memory" &&
      !memoryDirty.current
    )
      setContent(detail?.memory?.[scope]?.content || "");
  }, [dialog.type, section, scope, detail]);
  useEffect(() => {
    const file = dialog.file;
    if (
      dialog.type === "preview" &&
      file &&
      !imageFile(file) &&
      !file.name.toLowerCase().endsWith(".pdf")
    )
      api("/api/files/" + file.id + "/preview?start=" + page + "&count=50")
        .then((v) => setContent(v.content))
        .catch(error);
  }, [dialog.file, page, error]);
  const title =
    dialog.type === "settings"
      ? tr("Settings", "Nastavení")
      : dialog.type === "preview"
        ? dialog.file.name
        : (
            {
              project: tr("Project", "Projekt"),
              chat: tr("Conversation", "Konverzace"),
              "delete-chat": tr("Delete conversation", "Smazat konverzaci"),
              library: tr("Project documents", "Podklady projektu"),
              sources: tr("Sources", "Zdroje"),
              checkpoints: tr("Restore points", "Body obnovy"),
              export: tr("Export", "Export"),
              decisions: tr("Project decisions", "Přijatá rozhodnutí"),
              "queue-edit": tr("Queued message", "Zpráva ve frontě"),
            } as any
          )[dialog.type] || dialog.type;
  const settingsSections = [
    ["model", "Model and device", "Model a zařízení"],
    ["behavior", "Behavior", "Chování"],
    ["memory", "Memory and skills", "Paměť a skilly"],
    ["data", "Data and backups", "Data a zálohy"],
    ["appearance", "Appearance and language", "Vzhled a jazyk"],
    ["help", "Help and manuals", "Nápověda a manuály"],
  ];
  const finishSelect = async (result: any) => {
    if (result?.session_id) setSid(result.session_id);
    await refresh();
    close();
  };
  useEffect(() => {
    if (dialog.type !== "process-output") return;
    let cancelled = false;
    const load = () =>
      api("/api/sessions/" + sid + "/processes/" + dialog.data.id)
        .then((result) => {
          if (!cancelled) setContent(result.output || "");
        })
        .catch(error);
    load();
    const timer = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [dialog.type, dialog.data?.id, sid, error]);
  useEffect(() => {
    if (dialog.type !== "settings" || section !== "data") return;
    const timer = setInterval(() => {
      api("/api/maintenance").then(setMaintenance).catch(error);
      api("/api/backup").then(setBackup).catch(error);
    }, 3000);
    return () => clearInterval(timer);
  }, [dialog.type, section, error]);
  return (
    <div
      className="modal-shade"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className={
          "modal " + (dialog.type === "preview" ? "preview-modal" : "")
        }
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <span className="spacer" />
          <button
            className="icon"
            onClick={close}
            aria-label={tr("Close", "Zavřít")}
          >
            <X />
          </button>
        </header>
        <div
          className={
            "modal-body " +
            (dialog.type === "settings" ? "settings-layout" : "")
          }
        >
          {dialog.type === "settings" && (
            <nav>
              {settingsSections.map(([id, en, cz]) => (
                <button
                  key={id}
                  className={section === id ? "selected" : ""}
                  onClick={() => setDialog({ ...dialog, section: id })}
                >
                  {tr(en, cz)}
                </button>
              ))}
            </nav>
          )}
          <div className="modal-content">
            {dialog.type === "settings" && section === "model" && (
              <>
                <label>
                  {tr("Model", "Model")}
                  <select
                    value={app.preferences.model}
                    onChange={(e) =>
                      call(() => settings({ model: e.target.value }))
                    }
                  >
                    {app.models.map((m: any) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                        {!m.installed
                          ? " · " + tr("not installed", "nenainstalován")
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {tr("KV cache profile", "Profil KV cache")}
                  <select
                    value={
                      app.models.find(
                        (m: any) => m.id === app.preferences.model,
                      )?.profile
                    }
                    onChange={(e) =>
                      call(() =>
                        settings({
                          kv_cache_modes: {
                            ...app.preferences.kv_cache_modes,
                            [app.preferences.model]: e.target.value,
                          },
                        }),
                      )
                    }
                  >
                    {app.models
                      .find((m: any) => m.id === app.preferences.model)
                      ?.profiles.map((p: any) => (
                        <option key={p.id} value={p.id}>
                          {cs ? p.label_cs || p.label : p.label}
                        </option>
                      ))}
                  </select>
                </label>
                <p>
                  {tr("Vision", "Obrazové vstupy")}:{" "}
                  {app.models.find((m: any) => m.id === app.preferences.model)
                    ?.vision
                    ? tr("available", "dostupné")
                    : tr("text-only model", "pouze textový model")}
                </p>
                <label>
                  {tr("GPU VRAM", "Paměť GPU")}
                  <select
                    value={app.preferences.vram_gb || "auto"}
                    onChange={(e) =>
                      call(() =>
                        settings({
                          vram_gb:
                            e.target.value === "auto"
                              ? "auto"
                              : Number(e.target.value),
                        }),
                      )
                    }
                  >
                    {["auto", 16, 24, 32, 48, 64, 96].map((value) => (
                      <option key={value} value={value}>
                        {value === "auto"
                          ? tr("Automatic detection", "Automatická detekce")
                          : value + " GB"}
                      </option>
                    ))}
                  </select>
                </label>
                <p>
                  VRAM: {runtime.vram || "—"} · Python {runtime.python || "—"}
                </p>
                {app.active && (
                  <p className="amber">
                    {tr(
                      "New settings apply to the next request.",
                      "Nové nastavení platí pro následující dotaz.",
                    )}
                  </p>
                )}
                <div className="row">
                  {["start", "stop", "restart"].map((command) => (
                    <button
                      key={command}
                      className={command === "stop" ? "danger" : "outline"}
                      onClick={() =>
                        call(() => api("/api/runtime/" + command, "POST"))
                      }
                    >
                      {command === "start" ? (
                        <Play />
                      ) : command === "stop" ? (
                        <Square />
                      ) : (
                        <RotateCw />
                      )}
                      {command}
                    </button>
                  ))}
                </div>
                {runtime.switch?.error && (
                  <p className="error">{runtime.switch.error}</p>
                )}
              </>
            )}
            {dialog.type === "settings" && section === "behavior" && (
              <>
                <label>
                  {tr("Autonomy", "Samostatnost")}
                  <select
                    value={app.preferences.autonomy}
                    onChange={(e) =>
                      call(() => settings({ autonomy: e.target.value }))
                    }
                  >
                    {["supervised", "semi", "auto"].map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                </label>
                <label>
                  {tr("Default message delivery", "Výchozí zpracování zprávy")}
                  <select
                    value={app.preferences.send_mode}
                    onChange={(e) =>
                      call(() => settings({ send_mode: e.target.value }))
                    }
                  >
                    <option value="steer">
                      {tr("Clarify now", "Upřesnit nyní")}
                    </option>
                    <option value="queue">
                      {tr("After completion", "Po dokončení")}
                    </option>
                  </select>
                </label>
                <p>
                  {tr(
                    "Drafts and received messages are saved automatically.",
                    "Drafty a přijaté zprávy se ukládají automaticky.",
                  )}
                </p>
              </>
            )}
            {dialog.type === "settings" && section === "appearance" && (
              <>
                <label>
                  {tr("Theme", "Vzhled")}
                  <select
                    value={app.preferences.theme}
                    onChange={(e) =>
                      call(() => settings({ theme: e.target.value }))
                    }
                  >
                    <option value="dark">{tr("Dark", "Tmavý")}</option>
                    <option value="light">{tr("Light", "Světlý")}</option>
                    <option value="system">
                      {tr("System", "Podle systému")}
                    </option>
                  </select>
                </label>
                <label>
                  {tr("Language", "Jazyk")}
                  <select
                    value={app.preferences.language}
                    onChange={(e) =>
                      call(() => settings({ language: e.target.value }))
                    }
                  >
                    <option value="en">English</option>
                    <option value="cs">Čeština</option>
                  </select>
                </label>
                <label>
                  {tr("Spacing", "Rozestupy")}
                  <select
                    value={app.preferences.density}
                    onChange={(e) =>
                      call(() => settings({ density: e.target.value }))
                    }
                  >
                    <option value="comfortable">
                      {tr("Comfortable", "Pohodlné")}
                    </option>
                    <option value="compact">
                      {tr("Compact", "Kompaktní")}
                    </option>
                  </select>
                </label>
              </>
            )}
            {dialog.type === "settings" && section === "memory" && (
              <>
                <div className="row">
                  {["global", "mode", "project"].map((s, i) => (
                    <button
                      key={s}
                      className={scope === s ? "positive" : ""}
                      onClick={() => {
                        memoryDirty.current = false;
                        setScope(s);
                      }}
                      disabled={!detail?.memory?.[s]?.path}
                    >
                      {cs
                        ? ["Globální", "Režimová", "Projektová"][i]
                        : ["Global", "Work mode", "Project"][i]}
                    </button>
                  ))}
                </div>
                <textarea
                  className="memory-editor"
                  aria-label={tr("Memory text", "Text paměti")}
                  value={content}
                  onChange={(e) => {
                    memoryDirty.current = true;
                    setContent(e.target.value);
                  }}
                />
                <button
                  className="positive"
                  onClick={() => call(() => act("memory", { scope, content }))}
                >
                  <Save />
                  {tr("Save memory", "Uložit paměť")}
                </button>
                <h3>{tr("Skills", "Skilly")}</h3>
                <div className="row">
                  <button
                    onClick={() =>
                      call(() => act("open_skill_folder", { scope: "user" }))
                    }
                  >
                    <FolderOpen />
                    {tr("User skills", "Uživatelské skilly")}
                  </button>
                  <button
                    disabled={!project}
                    onClick={() =>
                      call(() => act("open_skill_folder", { scope: "project" }))
                    }
                  >
                    <FolderOpen />
                    {tr("Project skills", "Projektové skilly")}
                  </button>
                </div>
                <div className="skill-list">
                  {detail?.skills?.map((s: any) => (
                    <div key={s.name}>
                      <button
                        onClick={() =>
                          call(async () => {
                            const value = await act("read_skill", {
                              name: s.name,
                            });
                            setDialog({
                              type: "skill-content",
                              data: value.content,
                            });
                          })
                        }
                      >
                        <Puzzle />
                        {s.name}
                      </button>
                      <small>
                        {s.source} · {s.description}
                      </small>
                      <button
                        onClick={() =>
                          call(() => act("skill", { argument: s.name }))
                        }
                      >
                        {tr("Use skill", "Použít skill")}
                      </button>
                    </div>
                  ))}
                </div>
                <label>
                  {tr("New skill topic", "Téma nového skillu")}
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <button
                  onClick={() =>
                    call(() => act("skill", { argument: "new " + name }))
                  }
                >
                  <Plus />
                  {tr("Design a skill", "Navrhnout skill")}
                </button>
              </>
            )}
            {dialog.type === "settings" && section === "data" && (
              <>
                <h3>
                  {tr("Projects and conversations", "Projekty a konverzace")}
                </h3>
                <div className="row">
                  <button
                    disabled={!project}
                    onClick={() =>
                      call(async () => {
                        const file = await api(
                          "/api/projects/" + project.id + "/export",
                          "POST",
                          { session_id: sid },
                        );
                        setDialog({ type: "preview", file });
                      })
                    }
                  >
                    <Download />
                    {tr("Export project", "Exportovat projekt")}
                  </button>
                  <label className="file-picker">
                    <Upload />
                    {tr("Import project", "Importovat projekt")}
                    <input
                      type="file"
                      accept=".zip"
                      onChange={(e) =>
                        call(async () => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          const form = new FormData();
                          form.append("file", file);
                          const stored = await api(
                            "/api/sessions/" + sid + "/attachments",
                            "POST",
                            form,
                          );
                          return finishSelect(
                            await api("/api/projects/import", "POST", {
                              file_id: stored.id,
                            }),
                          );
                        })
                      }
                    />
                  </label>
                  <label className="file-picker">
                    <Upload />
                    {tr("Import chat JSONL", "Importovat chat JSONL")}
                    <input
                      type="file"
                      accept=".jsonl"
                      onChange={(e) =>
                        call(async () => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          const form = new FormData();
                          form.append("file", file);
                          const stored = await api(
                            "/api/sessions/" + sid + "/attachments",
                            "POST",
                            form,
                          );
                          return finishSelect(
                            await api(
                              "/api/sessions/" + sid + "/import-chat",
                              "POST",
                              { file_id: stored.id },
                            ),
                          );
                        })
                      }
                    />
                  </label>
                </div>
                <h3>
                  {tr("Model and runtime backup", "Záloha modelů a prostředí")}
                </h3>
                <p>
                  {tr(
                    "Internet first, local backup when a download fails.",
                    "Nejprve internet, lokální záloha při selhání stažení.",
                  )}
                </p>
                <p className="path">
                  {backup.path ||
                    tr("No fallback selected", "Záloha není vybraná")}
                </p>
                <div className="row">
                  {["create", "select", "verify", "clear"].map((op, i) => (
                    <button
                      key={op}
                      onClick={() =>
                        call(async () => {
                          let path = backup.path;
                          if (op === "create" || op === "select")
                            path = await pick(true);
                          if (!path && op !== "clear") return;
                          const value = await api("/api/backup/" + op, "POST", {
                            path,
                            session_id: sid,
                          });
                          setBackup(await api("/api/backup"));
                          setMaintenance(await api("/api/maintenance"));
                          return value;
                        })
                      }
                    >
                      {[<Plus />, <FolderOpen />, <CheckCheck />, <X />][i]}
                      {cs
                        ? ["Vytvořit", "Vybrat", "Ověřit", "Zapomenout"][i]
                        : ["Create", "Select", "Verify", "Clear"][i]}
                    </button>
                  ))}
                </div>
                {maintenance.map((p) => (
                  <p key={p.process_id}>
                    {p.status} · {Math.round(p.elapsed_seconds)} s
                  </p>
                ))}
                <button
                  onClick={() =>
                    api("/api/maintenance").then(setMaintenance).catch(error)
                  }
                >
                  <RotateCw />
                  {tr("Refresh operations", "Obnovit stav operací")}
                </button>
              </>
            )}
            {dialog.type === "settings" && section === "help" && (
              <>
                <h3>Marvin v{app.version}</h3>
                <p>Python 3.12 · Windows · llama.cpp</p>
                <div className="row">
                  <a className="button" href="/api/manual/en" target="_blank">
                    <BookOpen />
                    English PDF
                  </a>
                  <a className="button" href="/api/manual/cs" target="_blank">
                    <BookOpen />
                    Český PDF
                  </a>
                </div>
                <h3>{tr("Commands", "Příkazy")}</h3>
                {Object.entries(app.commands).map(([key, value]) => (
                  <p key={key}>
                    <code>{key}</code> · {String(value)}
                  </p>
                ))}
              </>
            )}
            {dialog.type === "project" && (
              <>
                <label>
                  {tr("New project name", "Název nového projektu")}
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <button
                  className="positive"
                  disabled={!name.trim()}
                  onClick={() =>
                    call(async () =>
                      finishSelect(
                        await api("/api/projects", "POST", {
                          name,
                          mode: chat?.meta.work_mode || "discussion",
                        }),
                      ),
                    )
                  }
                >
                  <Plus />
                  {tr("Create project", "Vytvořit projekt")}
                </button>
                <button
                  onClick={() =>
                    call(async () => {
                      const path = await pick(true);
                      if (path)
                        return finishSelect(
                          await api("/api/projects", "POST", { path }),
                        );
                    })
                  }
                >
                  <FolderOpen />
                  {tr("Attach existing folder", "Připojit existující složku")}
                </button>
                {project && (
                  <div className="danger-zone">
                    <p className="path">{project.path}</p>
                    <button
                      className="danger"
                      onClick={() =>
                        setDialog({ type: "delete-project", data: project })
                      }
                    >
                      <Trash2 />
                      {tr(
                        "Delete project and folder",
                        "Smazat projekt i složku",
                      )}
                    </button>
                  </div>
                )}
              </>
            )}
            {dialog.type === "delete-project" && (
              <>
                <p>
                  {tr(
                    "Delete this project, its files and conversations?",
                    "Smazat tento projekt, jeho soubory i konverzace?",
                  )}
                </p>
                <p className="path">{dialog.data.path}</p>
                <button
                  className="danger"
                  onClick={() =>
                    call(async () =>
                      finishSelect(
                        await api("/api/projects/" + dialog.data.id, "DELETE"),
                      ),
                    )
                  }
                >
                  <Trash2 />
                  {tr("Delete", "Smazat")}
                </button>
              </>
            )}
            {dialog.type === "chat" && (
              <>
                <label>
                  {tr("Move to project", "Přesunout do projektu")}
                  <select
                    value={project?.id || ""}
                    onChange={(e) =>
                      call(async () => {
                        await act("move", {
                          project_id: e.target.value || null,
                        });
                        close();
                      })
                    }
                  >
                    <option value="">{tr("No project", "Bez projektu")}</option>
                    {app.projects.map((p: any) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="row">
                  <button onClick={() => call(() => act("undo"))}>
                    <History />
                    {tr("Undo last turn", "Vrátit poslední kolo")}
                  </button>
                  <button
                    onClick={() =>
                      call(async () => finishSelect(await act("fork")))
                    }
                  >
                    <GitBranch />
                    {tr("Branch", "Větev")}
                  </button>
                  <button onClick={() => setDialog({ type: "export" })}>
                    <Download />
                    {tr("Export", "Export")}
                  </button>
                  <button
                    className="danger"
                    onClick={() => setDialog({ type: "delete-chat" })}
                  >
                    <Trash2 />
                    {tr("Delete chat", "Smazat chat")}
                  </button>
                </div>
              </>
            )}
            {dialog.type === "delete-chat" && (
              <>
                <p>
                  {tr(
                    "Delete this conversation and its attachments?",
                    "Smazat tuto konverzaci a její přílohy?",
                  )}
                </p>
                <button
                  className="danger"
                  onClick={() =>
                    call(async () => {
                      await act("delete");
                      const next = await api("/api/state");
                      setSid(next.session_id);
                      await refresh();
                      close();
                    })
                  }
                >
                  {tr("Delete", "Smazat")}
                </button>
              </>
            )}
            {dialog.type === "preview" && (
              <>
                <div className="preview-toolbar">
                  {/\.(py|exe|bat|cmd|ps1)$/i.test(dialog.file.name) && (
                    <button
                      className="positive"
                      onClick={() =>
                        call(() =>
                          api("/api/files/" + dialog.file.id + "/run", "POST"),
                        )
                      }
                    >
                      <Play />
                      {tr("Run", "Spustit")}
                    </button>
                  )}
                  <button
                    onClick={() =>
                      api(
                        "/api/files/" + dialog.file.id + "/open",
                        "POST",
                        {},
                      ).catch(error)
                    }
                  >
                    <ExternalLink />
                    {tr("Open file", "Otevřít soubor")}
                  </button>
                  <button
                    onClick={() =>
                      api("/api/files/" + dialog.file.id + "/open", "POST", {
                        folder: true,
                      }).catch(error)
                    }
                  >
                    <FolderOpen />
                    {tr("Open folder", "Otevřít složku")}
                  </button>
                  <a
                    className="button"
                    href={dialog.file.url + "?download=true"}
                    download
                  >
                    <Download />
                    {tr("Download", "Stáhnout")}
                  </a>
                </div>
                {/\.html?$/i.test(dialog.file.name) ? (
                  <iframe
                    className="pdf-preview"
                    title={dialog.file.name}
                    sandbox="allow-scripts allow-forms allow-downloads"
                    src={
                      "/api/preview/" +
                      dialog.file.id +
                      "/" +
                      encodeURIComponent(dialog.file.path.split(/[\\/]/).at(-1))
                    }
                  />
                ) : imageFile(dialog.file) ? (
                  <img
                    className="large-image"
                    src={dialog.file.url}
                    alt={dialog.file.name}
                  />
                ) : dialog.file.name.toLowerCase().endsWith(".pdf") ? (
                  <iframe
                    className="pdf-preview"
                    title={dialog.file.name}
                    src={dialog.file.url}
                  />
                ) : (
                  <>
                    <pre className="document-preview">{content}</pre>
                    <div className="row">
                      <button
                        disabled={page <= 1}
                        onClick={() => setPage((p) => Math.max(1, p - 50))}
                      >
                        {tr("Previous range", "Předchozí část")}
                      </button>
                      <span>
                        {page}–{page + 49}
                      </span>
                      <button onClick={() => setPage((p) => p + 50)}>
                        {tr("Next range", "Další část")}
                      </button>
                    </div>
                  </>
                )}
              </>
            )}
            {dialog.type === "export" && (
              <>
                <label>
                  {tr("Format", "Formát")}
                  <select
                    value={format}
                    onChange={(e) => setFormat(e.target.value)}
                  >
                    <option value="pdf">PDF</option>
                    <option value="docx">Word</option>
                    <option value="md">Markdown</option>
                    <option value="jsonl">JSONL</option>
                  </select>
                </label>
                <button
                  className="positive"
                  onClick={() =>
                    call(async () =>
                      setDialog({
                        type: "preview",
                        file: await act("export", { format }),
                      }),
                    )
                  }
                >
                  <Download />
                  {tr("Export", "Exportovat")}
                </button>
              </>
            )}
            {dialog.type === "library" && (
              <>
                <div className="library">
                  {library.map((file) => (
                    <div className="file-row" key={file.id}>
                      <FileText />
                      <button
                        onClick={() => setDialog({ type: "preview", file })}
                      >
                        {file.name}
                      </button>
                      <button
                        className="icon"
                        title={tr("Pin", "Připnout")}
                        onClick={() =>
                          call(() => act("pin", { path: file.path }))
                        }
                      >
                        <Pin />
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  onClick={() =>
                    call(async () => {
                      const path = await pick(false);
                      if (path) await act("pin", { path });
                    })
                  }
                >
                  <Plus />
                  {tr("Pin a file", "Připnout soubor")}
                </button>
              </>
            )}
            {dialog.type === "sources" && (
              <div className="sources">
                {detail?.research?.sources
                  ?.filter((s: any) => !dialog.data || s.id === dialog.data)
                  .map((s: any) => (
                    <section key={s.id}>
                      <h3>
                        [{s.id}] {s.title}
                      </h3>
                      <a href={s.url} target="_blank" rel="noreferrer">
                        {s.url}
                      </a>
                      <p className="muted">
                        {tr("Loaded", "Načteno")} ·{" "}
                        {new Date(s.fetched_at * 1000).toLocaleString()}
                      </p>
                      <pre>{s.content}</pre>
                    </section>
                  ))}
                {!dialog.data &&
                  detail?.research?.candidates
                    ?.filter(
                      (c: any) =>
                        !detail.research.sources.some(
                          (s: any) => s.url === c.url,
                        ),
                    )
                    .map((c: any) => (
                      <section key={c.url}>
                        <h3>{c.title}</h3>
                        <a href={c.url} target="_blank" rel="noreferrer">
                          {c.url}
                        </a>
                        <p>{tr("Found, not loaded", "Nalezeno, nenačteno")}</p>
                      </section>
                    ))}
              </div>
            )}
            {dialog.type === "checkpoints" && (
              <>
                <label>
                  {tr("New restore point", "Nový bod obnovy")}
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <button
                  className="positive"
                  onClick={() =>
                    call(() => act("checkpoint", { argument: name }))
                  }
                >
                  <Plus />
                  {tr("Create", "Vytvořit")}
                </button>
                {detail?.checkpoints?.map((cp: any) => (
                  <div className="file-row" key={cp.id}>
                    <History />
                    <div>
                      <strong>{cp.label}</strong>
                      <small>
                        {cp.files} {tr("files", "souborů")} ·{" "}
                        {new Date(cp.created * 1000).toLocaleString()}
                      </small>
                    </div>
                    <button
                      disabled={cp.restored}
                      onClick={() =>
                        call(async () => {
                          const result = await act("restore", { id: cp.id });
                          if (result.errors?.length && window.confirm(tr(
                            "Some files changed after this checkpoint. Replace them with the saved versions?",
                            "Některé soubory se od bodu obnovy změnily. Nahradit je uloženými verzemi?",
                          ))) {
                            const forced = await act("restore", { id: cp.id, force: true });
                            if (forced.errors?.length) error(forced.errors.join("\n"));
                          } else if (result.errors?.length)
                            error(result.errors.join("\n"));
                        })
                      }
                    >
                      {cp.restored
                        ? tr("Restored", "Obnoveno")
                        : tr("Restore", "Obnovit")}
                    </button>
                  </div>
                ))}
              </>
            )}
            {dialog.type === "queue-edit" && (
              <>
                <textarea
                  className="memory-editor"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
                <button
                  className="positive"
                  onClick={() =>
                    call(async () => {
                      await api("/api/queue/" + dialog.data.id, "PATCH", {
                        text: name,
                      });
                      await refresh();
                      close();
                    })
                  }
                >
                  <Save />
                  {tr("Save", "Uložit")}
                </button>
              </>
            )}
            {dialog.type === "skill-content" && (
              <Markdown remarkPlugins={[remarkGfm]}>{dialog.data}</Markdown>
            )}
            {dialog.type === "process-output" && (
              <pre className="document-preview">{content}</pre>
            )}
            {dialog.type === "context-snapshot" && (
              <>
                <p>
                  {dialog.data.model} · {dialog.data.work_mode} ·{" "}
                  {new Date(dialog.data.created * 1000).toLocaleString()}
                </p>
                {dialog.data.memory.map((memory: any) => (
                  <section key={memory.scope}>
                    <h3>
                      {memory.scope} · {memory.sha256.slice(0, 10)}
                    </h3>
                    <Markdown remarkPlugins={[remarkGfm]}>
                      {memory.content}
                    </Markdown>
                  </section>
                ))}
              </>
            )}
            {dialog.type === "decisions" && (
              <DecisionEditor
                items={detail?.decisions || []}
                tr={tr}
                enabled={!!project}
                save={(value: any) => call(() => act("decision", value))}
                openChat={(id: string) => {
                  setSid(id);
                  close();
                }}
              />
            )}
            {busy && (
              <div className="busy">
                <LoaderCircle className="spin" />
                {tr("Working…", "Pracuji…")}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

import { memo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Bot,
  X,
  Copy,
  RotateCw,
  FileText,
  Check,
  AlertCircle,
} from "lucide-react";
import { FileItem, Message, imageFile } from "../api";

export const Attachment = memo(
  ({
    file,
    open,
    remove,
    cs,
  }: {
    file: FileItem;
    open: () => void;
    remove?: () => void;
    cs: boolean;
  }) => (
    <div className="attachment">
      <button
        className="thumbnail"
        aria-label={(cs ? "Zvětšit " : "Enlarge ") + file.name}
        onClick={open}
      >
        {imageFile(file) ? (
          <img src={file.url} alt={file.name} />
        ) : (
          <FileText />
        )}
      </button>
      {remove && (
        <button
          className="remove"
          aria-label={(cs ? "Odebrat " : "Remove ") + file.name}
          onClick={remove}
        >
          <X />
        </button>
      )}
      <span>{file.name}</span>
    </div>
  ),
);
export const ChatMessage = memo(
  ({
    message: m,
    cs,
    openFile,
    openSource,
    retry,
  }: {
    message: Message;
    cs: boolean;
    openFile: (f: FileItem) => void;
    openSource: (id: string) => void;
    retry: () => void;
  }) => {
    if (m.role === "tool")
      return (
        <details className="tool-message">
          <summary>
            {m.tool_status === "error" ? (
              <AlertCircle size={13} className="amber" />
            ) : (
              <Check size={13} />
            )}{" "}
            {m.name}
          </summary>
          <pre>{m.content}</pre>
        </details>
      );
    return (
      <article className={"message " + m.role}>
        <div className="message-label">
          {m.role === "assistant" ? (
            <>
              <Bot />
              Marvin
            </>
          ) : cs ? (
            "Vy"
          ) : (
            "You"
          )}
          <time>
            {m.created
              ? new Date(m.created * 1000).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : ""}
          </time>
        </div>
        <div className="message-content">
          {m.reasoning && (
            <details>
              <summary>{cs ? "Přemýšlení" : "Thinking"}</summary>
              <Markdown remarkPlugins={[remarkGfm]}>{m.reasoning}</Markdown>
            </details>
          )}
          {m.content && (
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children }) =>
                  href?.startsWith("#source-") ? (
                    <button
                      className="citation"
                      onClick={() => openSource(href.slice(8))}
                    >
                      {children}
                    </button>
                  ) : (
                    <a href={href} target="_blank" rel="noreferrer">
                      {children}
                    </a>
                  ),
              }}
            >
              {String(m.content).replace(/\[(S\d+)\]/g, "[$1](#source-$1)")}
            </Markdown>
          )}
          {m.files && (
            <div className="attachment-list">
              {m.files.map((f) => (
                <Attachment
                  key={f.id}
                  file={f}
                  open={() => openFile(f)}
                  cs={cs}
                />
              ))}
            </div>
          )}
        </div>
        {m.role === "assistant" && (
          <div className="message-actions">
            <button
              onClick={() => navigator.clipboard.writeText(m.content || "")}
            >
              <Copy />
              {cs ? "Kopírovat" : "Copy"}
            </button>
            <button onClick={retry}>
              <RotateCw />
              {cs ? "Znovu" : "Retry"}
            </button>
          </div>
        )}
      </article>
    );
  },
);

// Dialogs use the same real service actions as the toolbar and slash commands.

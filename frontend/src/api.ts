export type FileItem = {
  id: string;
  name: string;
  path: string;
  url: string;
  kind?: string;
  exists?: boolean;
  mtime?: number;
};
export type Message = {
  id: string;
  role: string;
  content: string;
  reasoning?: string;
  files?: FileItem[];
  created?: number;
  run_id?: string;
  step_id?: number;
  tool_calls?: any[];
  name?: string;
  tool_status?: string;
};
export type Job = {
  id: string;
  status: string;
  session_id: string;
  payload: {
    text: string;
    attachments: string[];
    error?: string;
    [key: string]: any;
  };
};
export type Chat = {
  id: string;
  meta: Record<string, any>;
  messages: Message[];
  before: number | null;
  live: any;
  draft: { text?: string; attachments?: FileItem[] };
  jobs: Job[];
};
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers:
      body instanceof FormData
        ? undefined
        : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!response.ok) {
    let text = response.statusText;
    try {
      text = (await response.json()).detail || text;
    } catch {}
    throw new Error(typeof text === "string" ? text : JSON.stringify(text));
  }
  return response.json();
}
export const imageFile = (file: FileItem) =>
  /\.(png|jpe?g|gif|bmp|webp)$/i.test(file.name || file.path);
export const visibleMessage = (message: Message) =>
  message.role !== "system" &&
  !(
    message.role === "user" &&
    /^\[(TASK PROTOCOL|WRITING PROTOCOL|PROGRESS UPDATE|FINAL SUMMARY|WRITING SUMMARY|RESEARCH PLAN|DYNAMIC TASK CONTEXT|The following image|LOOP WARNING)/.test(
      message.content || "",
    )
  );

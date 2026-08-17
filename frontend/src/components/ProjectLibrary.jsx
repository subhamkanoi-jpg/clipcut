import { useCallback, useEffect, useState } from "react";
import { Film, Trash2, Sparkles, Clock3 } from "lucide-react";
import { api, forgetProject, thumbUrl } from "../api";
import { formatTime } from "../lib/captions";

export default function ProjectLibrary({ currentId, onOpen, refreshKey, compact = false }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(null);

  const load = useCallback(() => {
    api
      .get("/projects")
      .then(({ data }) => setItems(data.projects || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const remove = async (e, pid) => {
    e.stopPropagation();
    setBusy(pid);
    try {
      await api.delete(`/projects/${pid}`);
      forgetProject(pid);
      setItems((prev) => prev.filter((p) => p.id !== pid));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-3 min-h-0" data-testid="project-library">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Film className="w-4 h-4 text-accent" />
          <p className="font-mono text-xs uppercase tracking-wider text-zinc-400">
            Project Library
          </p>
        </div>
        <span className="font-mono text-[10px] text-zinc-600" data-testid="library-count">
          {items.length}
        </span>
      </div>

      {items.length === 0 ? (
        <p className="text-[11px] text-zinc-600" data-testid="library-empty">
          No reels yet. Your uploads land here.
        </p>
      ) : (
        <div
          className={`flex flex-col gap-2 overflow-y-auto pr-1 ${
            compact ? "max-h-52" : "max-h-[46vh]"
          }`}
        >
          {items.map((p) => (
            <button
              key={p.id}
              data-testid={`library-item-${p.id}`}
              onClick={() => onOpen(p)}
              className={`group flex gap-3 items-center text-left rounded-lg border p-2 transition-colors duration-150 ${
                p.id === currentId
                  ? "border-primary bg-primary/5"
                  : "border-zinc-800 hover:border-zinc-600 hover:bg-zinc-900/60"
              }`}
            >
              <span className="w-16 h-10 rounded bg-zinc-900 overflow-hidden shrink-0 flex items-center justify-center">
                {p.has_thumb ? (
                  <img
                    src={thumbUrl(p.id)}
                    alt=""
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                    }}
                  />
                ) : (
                  <Clock3 className="w-4 h-4 text-zinc-700" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[11px] font-medium truncate" title={p.filename}>
                  {p.filename}
                </span>
                <span className="flex items-center gap-2 mt-0.5">
                  <span className="font-mono text-[10px] text-zinc-500">
                    {formatTime(p.duration)}
                  </span>
                  {p.export_status === "done" && (
                    <span className="flex items-center gap-1 font-mono text-[9px] uppercase text-primary">
                      <Sparkles className="w-2.5 h-2.5" /> reel
                    </span>
                  )}
                  {p.status === "transcribing" && (
                    <span className="font-mono text-[9px] uppercase text-accent">
                      transcribing
                    </span>
                  )}
                  {p.status === "error" && (
                    <span className="font-mono text-[9px] uppercase text-red-400">failed</span>
                  )}
                </span>
              </span>
              <span
                data-testid={`library-delete-${p.id}`}
                role="button"
                onClick={(e) => remove(e, p.id)}
                className="opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-red-400 transition-colors duration-150 shrink-0"
              >
                {busy === p.id ? (
                  <span className="font-mono text-[9px]">...</span>
                ) : (
                  <Trash2 className="w-3.5 h-3.5" />
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

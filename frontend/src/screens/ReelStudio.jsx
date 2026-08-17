import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Clapperboard, UploadCloud, Loader2, Sparkles, Download, SlidersHorizontal,
  Scissors, ZoomIn, Type, Smartphone, Monitor, RotateCcw, CheckCircle2, Cloud,
} from "lucide-react";
import { api, uploadVideo, exportVideoUrl, downloadUrl, sleep } from "../api";
import { CAPTION_STYLES, formatTime } from "../lib/captions";
import ProjectLibrary from "../components/ProjectLibrary";

const STAGE_LABEL = {
  cutting: "Cutting on speech beats + zooming",
  captioning: "Burning karaoke captions",
  mastering: "Mastering audio to -14 LUFS",
  done: "Finishing up",
};

export default function ReelStudio({ onOpenEditor }) {
  const [phase, setPhase] = useState("idle");
  const [progress, setProgress] = useState(0);
  const [exportState, setExportState] = useState(null);
  const [project, setProject] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [fileName, setFileName] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [cloud, setCloud] = useState({ enabled: false });
  const [opts, setOpts] = useState({
    aspect: "9:16",
    cinematic: true,
    karaoke: true,
    punch_ins: true,
    zoom_intensity: 1.0,
    punch_sensitivity: 0.5,
    caption_style: "bold",
  });
  const inputRef = useRef(null);
  const stopped = useRef(false);

  useEffect(() => {
    api.get("/storage/status").then(({ data }) => setCloud(data)).catch(() => {});
    return () => {
      stopped.current = true;
    };
  }, []);

  const pollProject = useCallback(async (pid, test) => {
    for (let i = 0; i < 300; i++) {
      await sleep(2000);
      if (stopped.current) return null;
      const { data } = await api.get(`/projects/${pid}`);
      if (test(data)) return data;
      if (data.status === "error") throw new Error(data.error || "transcription failed");
      if (data.export?.status === "error") throw new Error(data.export.error || "render failed");
    }
    throw new Error("timed out — try a shorter clip");
  }, []);

  const generate = useCallback(
    async (pid) => {
      setPhase("rendering");
      setExportState({ status: "processing", progress: 0, stage: "cutting" });
      await api.post(`/projects/${pid}/export`, {
        caption_style: opts.caption_style,
        burn_captions: true,
        aspect: opts.aspect,
        cinematic: opts.cinematic,
        karaoke: opts.karaoke,
        punch_ins: opts.punch_ins,
        zoom_intensity: opts.zoom_intensity,
        punch_sensitivity: opts.punch_sensitivity,
      });
      const poll = setInterval(async () => {
        try {
          const { data } = await api.get(`/projects/${pid}`);
          setExportState(data.export);
        } catch {}
      }, 1800);
      try {
        const done = await pollProject(pid, (d) => d.export?.status === "done");
        setProject(done);
        setExportState(done.export);
        setPhase("done");
        setRefreshKey((k) => k + 1);
        toast.success("Your reel is ready");
      } finally {
        clearInterval(poll);
      }
    },
    [opts, pollProject]
  );

  const handleFile = useCallback(
    async (file) => {
      if (!file) return;
      if (!/\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(file.name)) {
        toast.error("Please upload a video file (mp4, mov, webm...)");
        return;
      }
      setFileName(file.name);
      setPhase("uploading");
      setProgress(0);
      try {
        const pid = await uploadVideo(file, setProgress);
        setRefreshKey((k) => k + 1);
        setPhase("transcribing");
        const ready = await pollProject(pid, (d) => d.status === "ready");
        setProject(ready);
        await generate(pid);
      } catch (e) {
        toast.error(e?.response?.data?.detail || e.message || "Something went wrong");
        setPhase("idle");
      }
    },
    [generate, pollProject]
  );

  const openLibraryItem = useCallback(
    async (p) => {
      const { data } = await api.get(`/projects/${p.id}`);
      if (data.export?.status === "done") {
        setProject(data);
        setExportState(data.export);
        setFileName(data.filename);
        setOpts((o) => ({
          ...o,
          ...(data.reel_settings || {}),
          caption_style: data.caption_style || o.caption_style,
        }));
        setPhase("done");
      } else if (data.status === "ready") {
        onOpenEditor(p.id);
      } else {
        toast.message("Still transcribing this clip — hang tight");
      }
    },
    [onOpenEditor]
  );

  const busy = ["uploading", "transcribing", "rendering"].includes(phase);
  const meta = exportState?.meta;

  return (
    <div className="min-h-dvh w-full flex flex-col md:h-full md:flex-row md:overflow-hidden" data-testid="reel-studio">
      <aside className="w-full shrink-0 bg-[#09090b] border-b border-zinc-800/50 p-4 flex flex-col gap-5 md:w-72 md:border-b-0 md:border-r md:p-6 md:gap-6 md:overflow-y-auto">
        <div className="flex items-center gap-2">
          <Clapperboard className="w-5 h-5 text-primary" />
          <span className="font-heading text-xl font-bold tracking-tight">ClipCut</span>
          <span className="ml-auto font-mono text-[9px] uppercase tracking-wider text-zinc-600">
            reel studio
          </span>
        </div>

        <ProjectLibrary
          currentId={project?.id}
          onOpen={openLibraryItem}
          refreshKey={refreshKey}
        />

        <div className="mt-auto border border-zinc-800 rounded-lg p-3" data-testid="cloud-status">
          <div className="flex items-center gap-2">
            <Cloud className={`w-3.5 h-3.5 ${cloud.blob ? "text-primary" : "text-zinc-600"}`} />
            <p className="font-mono text-[10px] uppercase tracking-wider text-zinc-400">
              Vercel Cloud
            </p>
          </div>
          <p className="text-[10px] text-zinc-600 mt-1">
            {cloud.blob
              ? "Private Blob media · Neon project storage"
              : "Checking secure cloud storage…"}
          </p>
        </div>
      </aside>

      <main className="flex-1 bg-black relative flex flex-col p-4 sm:p-6 md:p-10 md:overflow-y-auto min-w-0">
        {phase === "idle" && (
          <div className="w-full max-w-3xl fade-up">
            <h1 className="font-heading text-4xl sm:text-5xl tracking-tighter font-bold">
              Upload &amp; Generate
            </h1>
            <p className="text-zinc-400 text-sm mt-3 max-w-xl">
              One clip in, one cinematic reel out. We transcribe every word, cut on
              your speech beats, push and pull the camera with your energy, and light
              up captions word-by-word.
            </p>

            <div
              data-testid="upload-dropzone"
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                handleFile(e.dataTransfer.files?.[0]);
              }}
              className={`mt-8 cursor-pointer border-2 border-dashed rounded-xl p-14 flex items-center gap-6 transition-colors duration-200 ${
                dragOver
                  ? "border-primary bg-primary/5"
                  : "border-zinc-800 hover:border-primary hover:bg-primary/5"
              }`}
            >
              <UploadCloud className="w-12 h-12 text-primary shrink-0" />
              <div>
                <p className="font-heading text-lg font-semibold">Drop your talking clip</p>
                <p className="text-zinc-500 text-sm mt-1">
                  MP4, MOV, WEBM — we handle the rest
                </p>
              </div>
              <input
                ref={inputRef}
                data-testid="upload-file-input"
                type="file"
                accept="video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi"
                className="hidden"
                onChange={(e) => handleFile(e.target.files?.[0])}
              />
            </div>

            <Options opts={opts} setOpts={setOpts} />
          </div>
        )}

        {busy && (
          <div className="w-full max-w-2xl fade-up" data-testid="generate-progress">
            <h2 className="font-heading text-2xl font-semibold tracking-tight">
              Building your reel
            </h2>
            <p className="text-zinc-500 text-sm mt-1 truncate">{fileName}</p>

            <div className="mt-8 flex flex-col gap-3">
              <Step
                testId="step-upload"
                label="Uploading source"
                state={phase === "uploading" ? "active" : "done"}
                detail={phase === "uploading" ? `${progress}%` : "complete"}
              />
              <Step
                testId="step-transcribe"
                label="Transcribing speech (ElevenLabs Scribe)"
                state={
                  phase === "transcribing" ? "active" : phase === "uploading" ? "idle" : "done"
                }
                detail={phase === "transcribing" ? "word timestamps" : ""}
              />
              <Step
                testId="step-render"
                label="Auto-cut, cinematic zooms, karaoke captions"
                state={phase === "rendering" ? "active" : "idle"}
                detail={
                  phase === "rendering"
                    ? `${STAGE_LABEL[exportState?.stage] || "working"} · ${exportState?.progress || 0}%`
                    : ""
                }
              />
            </div>

            {phase === "rendering" && (
              <div className="mt-6 h-2 bg-zinc-900 rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-[width] duration-500"
                  style={{ width: `${exportState?.progress || 0}%` }}
                />
              </div>
            )}
          </div>
        )}

        {phase === "done" && project && (
          <div className="w-full fade-up" data-testid="reel-result">
            <div className="flex items-start gap-6 flex-wrap">
              <div className="flex-1 min-w-[320px]">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-5 h-5 text-primary" />
                  <h2 className="font-heading text-2xl font-semibold tracking-tight">
                    Your reel is ready
                  </h2>
                </div>
                <p className="text-zinc-500 text-sm mt-1 truncate">{project.filename}</p>

                <div className="mt-6 grid grid-cols-2 sm:grid-cols-5 gap-3">
                  <Stat testId="stat-cuts" label="Cuts made" value={project.cuts?.spans?.filter((s) => !s.disabled).length ?? 0} />
                  <Stat testId="stat-zooms" label="Camera moves" value={meta?.moves?.length ?? 0} />
                  <Stat testId="stat-punches" label="Punch-ins" value={meta?.punch_count ?? 0} />
                  <Stat testId="stat-duration" label="Final length" value={formatTime(project.cuts?.kept_duration)} />
                  <Stat testId="stat-format" label="Format" value={meta ? `${meta.width}×${meta.height}` : "—"} />
                </div>

                {meta?.moves?.length > 0 && (
                  <div className="mt-6" data-testid="move-timeline">
                    <p className="font-mono text-xs uppercase tracking-wider text-zinc-400 mb-2">
                      Speech-driven camera plan
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {meta.moves.slice(0, 24).map((m) => (
                        <span
                          key={m.index}
                          className="font-mono text-[10px] px-2 py-1 rounded border border-zinc-800 bg-zinc-900/60 text-zinc-400"
                        >
                          {m.kind} · {m.z0}→{m.z1}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {meta?.punches?.length > 0 && (
                  <div className="mt-5" data-testid="punch-list">
                    <p className="font-mono text-xs uppercase tracking-wider text-zinc-400 mb-2">
                      Punched words
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {meta.punches.map((p, i) => (
                        <span
                          key={`${p.t}-${i}`}
                          className="font-heading text-[11px] font-bold uppercase px-2 py-1 rounded border border-primary/40 bg-primary/10 text-primary"
                        >
                          {p.word}
                          <span className="font-mono text-[9px] text-primary/60 ml-1.5">
                            {p.t}s
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div className="mt-8 flex flex-wrap gap-3">
                  <a
                    data-testid="download-button"
                    href={downloadUrl(project.id)}
                    className="flex items-center gap-2 bg-primary text-black font-heading font-bold text-sm rounded-full px-6 py-2.5 hover:scale-[1.03] transition-transform duration-150"
                  >
                    <Download className="w-4 h-4" /> Download reel
                  </a>
                  <button
                    data-testid="open-editor-button"
                    onClick={() => onOpenEditor(project.id)}
                    className="flex items-center gap-2 border border-zinc-700 text-sm rounded-full px-5 py-2.5 hover:border-primary hover:text-primary transition-colors duration-150"
                  >
                    <SlidersHorizontal className="w-4 h-4" /> Fine-tune in editor
                  </button>
                  <button
                    data-testid="regenerate-button"
                    onClick={() => generate(project.id)}
                    className="flex items-center gap-2 text-sm text-zinc-500 hover:text-white transition-colors duration-150"
                  >
                    <RotateCcw className="w-4 h-4" /> Re-generate
                  </button>
                  <button
                    data-testid="new-reel-button"
                    onClick={() => {
                      setProject(null);
                      setExportState(null);
                      setPhase("idle");
                    }}
                    className="flex items-center gap-2 text-sm text-zinc-500 hover:text-white transition-colors duration-150"
                  >
                    New reel
                  </button>
                </div>

                {project.cloud?.url && (
                  <p className="mt-4 font-mono text-[10px] text-zinc-600 break-all" data-testid="cloud-url">
                    Cloudinary: {project.cloud.url}
                  </p>
                )}
                {project.cloud?.error && (
                  <p className="mt-4 font-mono text-[10px] text-red-400/80 break-all" data-testid="cloud-error">
                    Cloudinary upload skipped: {project.cloud.error}
                  </p>
                )}

                <Options opts={opts} setOpts={setOpts} />
              </div>

              <div
                className={`bg-zinc-950 rounded-xl overflow-hidden border border-zinc-800 ${
                  meta && meta.height > meta.width
                    ? "w-[280px] aspect-[9/16]"
                    : "w-[520px] aspect-video"
                }`}
              >
                <video
                  data-testid="reel-preview"
                  src={exportVideoUrl(project.id, exportState?.finished_at)}
                  className="w-full h-full object-contain"
                  controls
                  playsInline
                />
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function Options({ opts, setOpts }) {
  const set = (patch) => setOpts((o) => ({ ...o, ...patch }));
  return (
    <div className="mt-10 border-t border-zinc-900 pt-8" data-testid="reel-options">
      <p className="font-mono text-xs uppercase tracking-wider text-zinc-400 mb-4">
        Reel recipe
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="border border-zinc-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Smartphone className="w-4 h-4 text-accent" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-400">
              Aspect
            </span>
          </div>
          <div className="flex gap-2">
            <Pill
              testId="aspect-vertical"
              active={opts.aspect === "9:16"}
              onClick={() => set({ aspect: "9:16" })}
              icon={Smartphone}
              label="9:16 reframe"
            />
            <Pill
              testId="aspect-original"
              active={opts.aspect === "original"}
              onClick={() => set({ aspect: "original" })}
              icon={Monitor}
              label="Original"
            />
          </div>
          <p className="text-[10px] text-zinc-600 mt-2">
            Landscape clips get cropped with the speaker auto-centered.
          </p>
        </div>

        <div className="border border-zinc-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <ZoomIn className="w-4 h-4 text-accent" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-400">
              Cinematic zooms
            </span>
          </div>
          <Toggle
            testId="cinematic-toggle"
            label="Speech-driven camera"
            checked={opts.cinematic}
            onChange={(v) => set({ cinematic: v })}
          />
          <div className="mt-2">
            <Toggle
              testId="punch-ins-toggle"
              label="Keyword punch-ins"
              checked={opts.punch_ins}
              onChange={(v) => set({ punch_ins: v })}
            />
            <p className="text-[10px] text-zinc-600 mt-1">
              Hard zoom snap on your most emphasised words.
            </p>
            <div className="mt-2.5">
              <div className="flex justify-between mb-1.5">
                <span className="text-[10px] text-zinc-500">Punch sensitivity</span>
                <span className="font-mono text-[10px] text-primary">
                  {opts.punch_sensitivity <= 0.34
                    ? "Rare · big"
                    : opts.punch_sensitivity >= 0.67
                    ? "Frequent · subtle"
                    : "Balanced"}
                </span>
              </div>
              <input
                data-testid="punch-sensitivity-slider"
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={opts.punch_sensitivity}
                disabled={!opts.cinematic || !opts.punch_ins}
                onChange={(e) => set({ punch_sensitivity: parseFloat(e.target.value) })}
              />
              <div className="flex justify-between mt-1">
                <span className="text-[9px] text-zinc-600">Rare &amp; big</span>
                <span className="text-[9px] text-zinc-600">Frequent &amp; subtle</span>
              </div>
            </div>
          </div>
          <div className="mt-3">
            <div className="flex justify-between mb-1.5">
              <span className="text-[10px] text-zinc-500">Intensity</span>
              <span className="font-mono text-[10px] text-primary">
                {Number(opts.zoom_intensity).toFixed(1)}×
              </span>
            </div>
            <input
              data-testid="zoom-intensity-slider"
              type="range"
              min="0.4"
              max="1.6"
              step="0.1"
              value={opts.zoom_intensity}
              disabled={!opts.cinematic}
              onChange={(e) => set({ zoom_intensity: parseFloat(e.target.value) })}
            />
          </div>
        </div>

        <div className="border border-zinc-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Type className="w-4 h-4 text-accent" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-400">
              Captions
            </span>
          </div>
          <Toggle
            testId="karaoke-toggle"
            label="Karaoke word highlight"
            checked={opts.karaoke}
            onChange={(v) => set({ karaoke: v })}
          />
          <div className="grid grid-cols-2 gap-2 mt-3">
            {CAPTION_STYLES.map((s) => (
              <button
                key={s.key}
                data-testid={`style-card-${s.key}`}
                onClick={() => set({ caption_style: s.key })}
                className={`rounded border px-2 py-1.5 bg-zinc-900/80 transition-transform duration-150 hover:-translate-y-0.5 ${
                  opts.caption_style === s.key
                    ? "border-primary shadow-[inset_0_0_12px_rgba(212,255,0,0.15)]"
                    : "border-zinc-800 hover:border-zinc-600"
                }`}
              >
                <span style={{ ...s.css, fontSize: "0.6rem" }}>
                  {s.uppercase ? "WORDS" : "words"}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
      <p className="flex items-center gap-2 text-[10px] text-zinc-600 mt-4">
        <Scissors className="w-3 h-3" /> Silences and filler words are cut automatically —
        adjust sensitivity in the editor.
      </p>
    </div>
  );
}

function Pill({ active, onClick, icon: Icon, label, testId }) {
  return (
    <button
      data-testid={testId}
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] border transition-colors duration-150 ${
        active
          ? "border-primary bg-primary/10 text-primary"
          : "border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-white"
      }`}
    >
      <Icon className="w-3 h-3" /> {label}
    </button>
  );
}

function Toggle({ label, checked, onChange, testId }) {
  return (
    <div className="flex items-center justify-between">
      <p className="text-[11px] text-zinc-300">{label}</p>
      <button
        data-testid={testId}
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`w-9 h-5 rounded-full relative transition-colors duration-200 ${
          checked ? "bg-primary" : "bg-zinc-700"
        }`}
      >
        <span
          className={`absolute top-0.5 w-4 h-4 rounded-full bg-black transition-transform duration-200 ${
            checked ? "translate-x-[18px]" : "translate-x-0.5"
          }`}
        />
      </button>
    </div>
  );
}

function Step({ label, state, detail, testId }) {
  return (
    <div className="flex items-center gap-3" data-testid={testId}>
      <span className="w-6 h-6 rounded-full border border-zinc-800 flex items-center justify-center shrink-0">
        {state === "done" ? (
          <CheckCircle2 className="w-4 h-4 text-primary" />
        ) : state === "active" ? (
          <Loader2 className="w-3.5 h-3.5 text-primary animate-spin" />
        ) : (
          <span className="w-1.5 h-1.5 rounded-full bg-zinc-700" />
        )}
      </span>
      <span className={`text-sm ${state === "idle" ? "text-zinc-600" : "text-zinc-200"}`}>
        {label}
      </span>
      {detail && <span className="ml-auto font-mono text-[10px] text-zinc-500">{detail}</span>}
    </div>
  );
}

function Stat({ label, value, testId }) {
  return (
    <div className="border border-zinc-800 rounded-lg p-3" data-testid={testId}>
      <p className="font-heading text-lg font-bold text-primary">{value}</p>
      <p className="text-[10px] text-zinc-500 mt-0.5">{label}</p>
    </div>
  );
}

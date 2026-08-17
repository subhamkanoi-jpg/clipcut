import React, { useState } from "react";
import { Loader2, RefreshCw, Play, Lock, Unlock, Eye, EyeOff, LayoutTemplate } from "lucide-react";

export default function PlanReview({ project, onRegenerate, onRender, onOverlayPatch }) {
  const plan = project?.plan;
  if (!plan) return null;

  const totalS = plan.total_duration_s || 1;
  const overlays = plan.overlays || [];
  const ranges = plan.ranges || [];

  const [loadingOverlay, setLoadingOverlay] = useState(null);
  const [isRegenerating, setIsRegenerating] = useState(false);

  const handlePatch = async (oid, patch) => {
    setLoadingOverlay(oid);
    try {
      await onOverlayPatch(oid, patch);
    } finally {
      setLoadingOverlay(null);
    }
  };

  const toPct = (time) => `${(time / totalS) * 100}%`;

  return (
    <div className="w-full max-w-4xl fade-up" data-testid="plan-review-screen">
      <h2 className="font-heading text-3xl tracking-tight font-bold mb-2">Review AI Edit Plan</h2>
      <p className="text-zinc-400 text-sm mb-8">
        Review the planned cuts and overlays. Lock what you like, disable what you don't, or swap queries.
      </p>

      <div className="bg-[#09090b] border border-zinc-800/50 rounded-xl p-6 shadow-xl flex flex-col gap-6">
        <div className="flex flex-col gap-4 relative ml-16 mr-4">
          
          {/* Timeline Axis */}
          <div className="h-4 border-b border-zinc-800 relative mb-2">
            {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
              <div
                key={tick}
                className="absolute top-0 bottom-0 border-l border-zinc-800"
                style={{ left: `${tick * 100}%` }}
              >
                <span className="absolute -top-4 -translate-x-1/2 font-mono text-[9px] text-zinc-500">
                  {Math.round(tick * totalS)}s
                </span>
              </div>
            ))}
          </div>

          {/* Ranges Lane */}
          <div className="relative h-12 bg-zinc-950 border border-zinc-800 rounded flex items-center">
            <span className="absolute -left-16 font-mono text-[10px] uppercase text-zinc-500 w-14 text-right pr-2">Ranges</span>
            {ranges.map((r, i) => {
              const startPct = (r.start / totalS) * 100;
              const widthPct = ((r.end - r.start) / totalS) * 100;
              return (
                <div
                  key={i}
                  className="absolute top-1 bottom-1 bg-zinc-800 border border-zinc-700 rounded text-[9px] text-zinc-300 p-1 truncate cursor-help group"
                  style={{ left: `${startPct}%`, width: `${widthPct}%` }}
                >
                  <span className="block truncate">{r.reason || "Kept"}</span>
                  {r.zoom && <span className="text-primary font-mono text-[8px]">{r.zoom}x</span>}
                  
                  {/* Tooltip */}
                  <div className="hidden group-hover:block absolute bottom-full mb-1 left-0 bg-black border border-zinc-700 p-2 rounded z-10 w-48 shadow-lg whitespace-normal break-words text-xs">
                    <p className="text-primary font-mono mb-1">{r.start.toFixed(1)}s - {r.end.toFixed(1)}s</p>
                    {r.reason && <p className="text-zinc-300">Reason: {r.reason}</p>}
                    {r.variation && <p className="text-zinc-400 mt-1">Move: {r.variation}</p>}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Overlays Lane */}
          <div className="relative h-16 bg-zinc-950 border border-zinc-800 rounded flex items-center mt-2">
            <span className="absolute -left-16 font-mono text-[10px] uppercase text-zinc-500 w-14 text-right pr-2">Visuals</span>
            {overlays.map((ov) => {
              const left = toPct(ov.start_in_output);
              const width = toPct(ov.duration);
              const isLoading = loadingOverlay === ov.id;
              
              return (
                <div
                  key={ov.id}
                  className={`absolute top-1 bottom-1 rounded border overflow-hidden flex flex-col transition-opacity ${
                    ov.enabled ? "bg-zinc-800 border-zinc-600" : "bg-zinc-900 border-zinc-800 opacity-50"
                  }`}
                  style={{ left, width }}
                >
                  <div className="bg-zinc-900 px-2 py-1 flex items-center justify-between border-b border-zinc-800">
                    <span className="font-mono text-[9px] text-zinc-400 flex items-center gap-1">
                      {ov.kind === "broll" ? <LayoutTemplate className="w-3 h-3" /> : null}
                      {ov.kind}
                    </span>
                    <div className="flex gap-1">
                      <button
                        disabled={isLoading}
                        onClick={() => handlePatch(ov.id, { enabled: !ov.enabled })}
                        className="hover:text-primary transition-colors text-zinc-500"
                        title={ov.enabled ? "Disable" : "Enable"}
                      >
                        {ov.enabled ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
                      </button>
                      <button
                        disabled={isLoading}
                        onClick={() => handlePatch(ov.id, { locked: !ov.locked })}
                        className={`hover:text-primary transition-colors ${ov.locked ? "text-primary" : "text-zinc-500"}`}
                        title={ov.locked ? "Unlock" : "Lock"}
                      >
                        {ov.locked ? <Lock className="w-3 h-3" /> : <Unlock className="w-3 h-3" />}
                      </button>
                    </div>
                  </div>
                  <div className="px-2 py-1 flex-1 flex flex-col justify-center">
                    {isLoading ? (
                      <Loader2 className="w-3 h-3 animate-spin text-zinc-500 mx-auto" />
                    ) : (
                      <>
                        <input 
                          type="text" 
                          defaultValue={ov.query} 
                          className="bg-transparent border-none text-[10px] font-medium text-white w-full outline-none focus:text-primary"
                          onBlur={(e) => {
                            if (e.target.value !== ov.query) {
                              handlePatch(ov.id, { query: e.target.value });
                            }
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.target.blur();
                            }
                          }}
                        />
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Audio Lane */}
          <div className="relative h-10 bg-zinc-950/50 border border-zinc-800/50 rounded flex items-center mt-2">
            <span className="absolute -left-16 font-mono text-[10px] uppercase text-zinc-600 w-14 text-right pr-2">Audio</span>
            <div className="w-full flex justify-center items-center h-full">
              <span className="text-[10px] text-zinc-600 font-mono tracking-wider">(Audio overlays reserved for v2)</span>
            </div>
          </div>
          
        </div>

        {/* Footer Actions */}
        <div className="flex justify-end gap-3 pt-4 border-t border-zinc-800">
          <button
            data-testid="regenerate-plan-button"
            disabled={isRegenerating}
            onClick={async () => {
              setIsRegenerating(true);
              try {
                await onRegenerate();
              } finally {
                setIsRegenerating(false);
              }
            }}
            className="flex items-center gap-2 border border-zinc-700 text-sm rounded-full px-5 py-2.5 hover:border-primary hover:text-primary transition-colors disabled:opacity-50"
          >
            {isRegenerating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Regenerate unlocked
          </button>
          
          <button
            data-testid="commit-plan-button"
            onClick={onRender}
            className="flex items-center gap-2 bg-primary text-black font-heading font-bold text-sm rounded-full px-6 py-2.5 hover:scale-[1.03] transition-transform duration-150 shadow-[0_0_15px_rgba(212,255,0,0.2)]"
          >
            <Play className="w-4 h-4" fill="currentColor" /> Render Reel
          </button>
        </div>
      </div>
    </div>
  );
}

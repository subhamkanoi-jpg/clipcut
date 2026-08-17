import { useState } from "react";
import { Toaster } from "sonner";
import ReelStudio from "./screens/ReelStudio";
import Editor from "./screens/Editor";

export default function App() {
  const [view, setView] = useState({ mode: "studio", projectId: null });

  return (
    <div className="min-h-dvh w-full bg-background text-white md:h-screen md:overflow-hidden">
      <Toaster theme="dark" position="top-center" richColors />
      {view.mode === "editor" ? (
        <Editor
          projectId={view.projectId}
          key={view.projectId}
          onReset={() => setView({ mode: "studio", projectId: null })}
          onOpenProject={(pid) => setView({ mode: "editor", projectId: pid })}
        />
      ) : (
        <ReelStudio
          onOpenEditor={(pid) => setView({ mode: "editor", projectId: pid })}
        />
      )}
    </div>
  );
}

import axios from "axios";

export const API = process.env.REACT_APP_API_URL || "/api";
const STORAGE_KEY = "clipcut-project-access";

function readAccess() {
  try {
    return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveAccess(value) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
}

function rememberProject(pid, token) {
  const access = readAccess();
  access[pid] = token;
  saveAccess(access);
}

export function forgetProject(pid) {
  const access = readAccess();
  delete access[pid];
  saveAccess(access);
}

export const api = axios.create({ baseURL: API });
api.interceptors.request.use((config) => {
  const access = readAccess();
  const match = config.url?.match(/\/projects\/([0-9a-f-]{36})/i);
  if (match && access[match[1]]) config.headers["X-Project-Token"] = access[match[1]];
  config.headers["X-Project-Tokens"] = Object.values(access).join(",");
  return config;
});

function mediaUrl(pid, suffix, bust) {
  const token = readAccess()[pid] || "";
  const params = new URLSearchParams({ access_token: token });
  if (bust) params.set("t", bust);
  return `${API}/projects/${pid}/${suffix}?${params}`;
}

export const videoUrl = (pid) => mediaUrl(pid, "video");
export const exportVideoUrl = (pid, bust) => mediaUrl(pid, "export/video", bust);
export const downloadUrl = (pid) => mediaUrl(pid, "export/download");
export const thumbUrl = (pid) => mediaUrl(pid, "thumbnail");

const CHUNK_SIZE = 5 * 1024 * 1024;

export async function uploadVideo(file, onProgress) {
  const { data } = await api.post("/projects/upload/init", { filename: file.name, size: file.size });
  const pid = data.project_id;
  rememberProject(pid, data.project_token);
  const total = Math.ceil(file.size / CHUNK_SIZE);
  for (let i = 0; i < total; i++) {
    const form = new FormData();
    form.append("index", i);
    form.append("chunk", file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE), "chunk");
    await api.post(`/projects/${pid}/upload/chunk`, form);
    onProgress(Math.round(((i + 1) / total) * 100));
  }
  await api.post(`/projects/${pid}/upload/complete`);
  return pid;
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

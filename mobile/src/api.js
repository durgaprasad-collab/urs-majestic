const API_BASE = import.meta.env.VITE_API_BASE || "https://admin.ursmajestic.com";
const TOKEN_KEY = "urs_staff_token";
const SESSION_KEY = "urs_staff_session";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getSession() {
  const raw = localStorage.getItem(SESSION_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function saveSession({ access_token, user_id, name, is_owner }) {
  if (access_token) localStorage.setItem(TOKEN_KEY, access_token);
  localStorage.setItem(SESSION_KEY, JSON.stringify({ user_id, name, is_owner }));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(SESSION_KEY);
}

async function request(path, { method = "GET", body } = {}) {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    clearSession();
    window.location.reload();
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  login: (username, password) => request("/api/auth/login", { method: "POST", body: { username, password } }),
  me: () => request("/api/auth/me"),
  listRequisitions: (status) => request(`/api/requisitions/${status ? `?status_filter=${status}` : ""}`),
  createRequisition: (payload) => request("/api/requisitions/", { method: "POST", body: payload }),
  decideRequisition: (id, approve, decision_note) =>
    request(`/api/requisitions/${id}/decision`, { method: "PATCH", body: { approve, decision_note } }),
  lowStock: () => request("/api/stock/low"),
  submitStockCount: (payload) => request("/api/stock/count", { method: "POST", body: payload }),
  registerDevice: (token, platform) => request("/api/stock/register-device", { method: "POST", body: { token, platform } }),
  listIngredients: () => request("/api/ingredients/"),
};

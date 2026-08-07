export async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("session-expired"));
    throw new Error("登录已失效");
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg || JSON.stringify(item)).join("；")
      : body.detail;
    throw new Error(detail || `请求失败：${response.status}`);
  }

  if (response.status === 204) return null;
  return response.json();
}

export function appendQuery(params, name, value) {
  if (value !== null && value !== undefined && value !== "") {
    params.set(name, String(value));
  }
}

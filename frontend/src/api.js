// fetch() resolves on HTTP errors, so every call goes through here: a failed
// request always becomes a rejected promise carrying the server's message.
export async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try {
    body = await res.json();
  } catch {
    // Non-JSON body; the status check below still reports the failure.
  }
  if (!res.ok) {
    const detail = body?.detail;
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`);
  }
  return body;
}

export const postJson = (path, data) => api(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
});

export const query = (params) => new URLSearchParams(params).toString();

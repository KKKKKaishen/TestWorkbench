// 极简 API 封装：JSON + 错误统一抛出
export async function api(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(path, opts)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail || JSON.stringify(j)
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.json()
}

export const get = (p) => api('GET', p)
export const post = (p, b) => api('POST', p, b)
export const put = (p, b) => api('PUT', p, b)
export const del = (p) => api('DELETE', p)

export async function uploadAttachment(ownerType, ownerId, file) {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`/wb/attachments?owner_type=${ownerType}&owner_id=${ownerId}`, {
    method: 'POST', body: fd
  })
  if (!res.ok) throw new Error('上传失败: ' + res.status)
  return res.json()
}

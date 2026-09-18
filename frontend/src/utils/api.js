export const API_BASE = window.API_BASE || ''

export function apiJson(path, options = {}) {
  return fetch(`${API_BASE}${path}`, options).then(async (response) => {
    const text = await response.text()
    let data = {}
    try {
      data = text ? JSON.parse(text) : {}
    } catch {
      data = { detail: text }
    }
    if (!response.ok) {
      throw new Error(data.detail || data.mensaje || 'La peticion fallo')
    }
    return data
  })
}

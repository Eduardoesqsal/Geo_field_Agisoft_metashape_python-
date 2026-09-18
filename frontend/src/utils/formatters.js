export function formatDate(value) {
  if (!value) return '-'
  try {
    return new Date(value).toLocaleString('es-MX')
  } catch {
    return value
  }
}

export function formatAreaHa(value) {
  if (!Number.isFinite(value)) return '-'
  return `${value.toLocaleString('es-MX', { maximumFractionDigits: 2 })} ha`
}

export function stateTone(status) {
  if (status?.step === 'error') return 'danger'
  if (status?.running) return 'success'
  if (status?.step === 'finalizado') return 'success'
  return 'idle'
}

export function processingProgress(status) {
  const steps = {
    iniciando: 0,
    cargando_fotos: 1,
    alineando_camaras: 2,
    construyendo_mde: 3,
    construyendo_ortomosaico: 4,
    exportando_resultado: 5,
    generando_curvas: 6,
  }
  if (status?.step === 'finalizado' && !status?.running) return 100
  const step = steps[status?.step] ?? 0
  return Math.round((step / 6) * 100)
}

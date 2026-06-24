import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'

const API_BASE = window.API_BASE || ''

const DEFAULT_CENTER = [23.6345, -102.5528]

function formatDate(value) {
  if (!value) return '-'
  try {
    return new Date(value).toLocaleString('es-MX')
  } catch {
    return value
  }
}

function stateTone(status) {
  if (status?.step === 'error') return 'danger'
  if (status?.running) return 'success'
  if (status?.step === 'finalizado') return 'success'
  return 'idle'
}

function apiJson(path, options = {}) {
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

function MapView({ points, overlay, finalMode }) {
  const mapRef = useRef(null)
  const mapInstanceRef = useRef(null)
  const baseLayerRef = useRef(null)
  const pointsLayerRef = useRef(null)
  const overlayLayerRef = useRef(null)
  const hasUserInteractedRef = useRef(false)
  const pointsFocusTokenRef = useRef(null)
  const overlayFocusTokenRef = useRef(null)

  const BingAerialLayer = useMemo(
    () =>
      L.TileLayer.extend({
        getTileUrl(coords) {
          const zoom = coords.z
          const x = coords.x
          const y = coords.y
          let quadKey = ''
          for (let i = zoom; i > 0; i -= 1) {
            let digit = 0
            const mask = 1 << (i - 1)
            if ((x & mask) !== 0) digit += 1
            if ((y & mask) !== 0) digit += 2
            quadKey += digit.toString()
          }
          const subdomain = Math.abs(x + y) % 4
          return `https://ecn.t${subdomain}.tiles.virtualearth.net/tiles/a${quadKey}.jpeg?g=1`
        },
      }),
    [],
  )

  useEffect(() => {
    if (mapInstanceRef.current) return

    const map = L.map(mapRef.current, {
      zoomControl: true,
      preferCanvas: true,
      maxZoom: 24,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      scrollWheelZoom: true,
    }).setView(DEFAULT_CENTER, 5)

    const baseLayer = new BingAerialLayer('', {
      attribution: '&copy; Microsoft Bing',
      maxZoom: 24,
      maxNativeZoom: 19,
      subdomains: ['0', '1', '2', '3'],
    })

    map.dragging.enable()
    map.doubleClickZoom.enable()
    map.boxZoom.enable()
    map.touchZoom.enable()
    map.keyboard.enable()

    baseLayerRef.current = baseLayer
    baseLayer.addTo(map)

    const markInteraction = () => {
      hasUserInteractedRef.current = true
    }

    map.on('dragstart zoomstart', markInteraction)

    mapInstanceRef.current = map
    pointsLayerRef.current = L.layerGroup().addTo(map)

    const handleResize = () => {
      setTimeout(() => map.invalidateSize(), 120)
    }

    window.addEventListener('resize', handleResize)
    setTimeout(() => map.invalidateSize(), 120)

    return () => {
      window.removeEventListener('resize', handleResize)
      map.off('dragstart zoomstart', markInteraction)
      map.remove()
      mapInstanceRef.current = null
      baseLayerRef.current = null
      pointsLayerRef.current = null
      overlayLayerRef.current = null
      hasUserInteractedRef.current = false
      pointsFocusTokenRef.current = null
      overlayFocusTokenRef.current = null
    }
  }, [])

  const pointsSignature = useMemo(
    () =>
      (points || [])
        .filter((point) => Number.isFinite(point?.lat) && Number.isFinite(point?.lon))
        .map((point) => `${point.lat.toFixed(6)},${point.lon.toFixed(6)},${point.nombre || ''}`)
        .join('|'),
    [points],
  )

  const overlayToken = useMemo(
    () => (overlay?.disponible && overlay?.bounds ? overlay.cache_buster || overlay.ruta || 'overlay' : null),
    [overlay],
  )

  useEffect(() => {
    const map = mapInstanceRef.current
    const pointsLayer = pointsLayerRef.current
    if (!map || !pointsLayer) return

    pointsLayer.clearLayers()
    const validPoints = (points || []).filter(
      (point) => Number.isFinite(point?.lat) && Number.isFinite(point?.lon),
    )

    if (finalMode && overlay?.disponible) {
      return
    }

    if (!validPoints.length) {
      if (!hasUserInteractedRef.current) {
        map.setView(DEFAULT_CENTER, 5)
      }
      return
    }

    validPoints.forEach((point, index) => {
      const latlng = [point.lat, point.lon]
      L.circleMarker(latlng, {
        radius: 6,
        color: '#ffffff',
        weight: 1,
        fillColor: '#2563eb',
        fillOpacity: 0.95,
      })
        .addTo(pointsLayer)
        .bindPopup(`${index + 1}. ${point.nombre || 'Foto'}`)
    })

    if (hasUserInteractedRef.current) return
    if (pointsFocusTokenRef.current === pointsSignature) return

    if (validPoints.length === 1) {
      map.setView([validPoints[0].lat, validPoints[0].lon], 17)
    } else {
      map.fitBounds(
        validPoints.map((point) => [point.lat, point.lon]),
        { padding: [36, 36] },
      )
    }
    pointsFocusTokenRef.current = pointsSignature
  }, [pointsSignature, finalMode, overlay?.disponible])

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    const baseLayer = baseLayerRef.current

    if (!finalMode || !overlay?.disponible || !overlay?.bounds) {
      if (overlayLayerRef.current) {
        map.removeLayer(overlayLayerRef.current)
        overlayLayerRef.current = null
      }
      if (baseLayer && !map.hasLayer(baseLayer)) baseLayer.addTo(map)
      overlayFocusTokenRef.current = null
      return
    }

    if (baseLayer && !map.hasLayer(baseLayer)) {
      baseLayer.addTo(map)
    }

    if (!overlayLayerRef.current || overlayLayerRef.current.__token !== overlayToken) {
      if (overlayLayerRef.current) {
        map.removeLayer(overlayLayerRef.current)
      }

      const layer = L.tileLayer(`${API_BASE}/tiles/rgb/{z}/{x}/{y}.png?v=${encodeURIComponent(overlayToken)}`, {
        bounds: overlay.bounds,
        opacity: 1,
        tileSize: 256,
        maxZoom: 24,
        maxNativeZoom: 24,
        keepBuffer: 4,
        updateWhenZooming: false,
        updateWhenIdle: true,
        noWrap: true,
        detectRetina: true,
      })
      layer.__token = overlayToken
      overlayLayerRef.current = layer
      overlayFocusTokenRef.current = null
    }

    if (!map.hasLayer(overlayLayerRef.current)) {
      overlayLayerRef.current.addTo(map)
    }

    overlayLayerRef.current.bringToFront?.()

    if (hasUserInteractedRef.current) return
    if (overlayFocusTokenRef.current === overlayToken) return

    try {
      map.fitBounds(overlay.bounds, { padding: [32, 32] })
    } catch {
      map.setView(DEFAULT_CENTER, 5)
    }
    overlayFocusTokenRef.current = overlayToken
  }, [finalMode, overlay, overlayToken])

  return <div ref={mapRef} className="map-canvas" />
}

function Icon({ name }) {
  const icons = {
    activity: (
      <path d="M4 12h4l2-5 3 10 2-5h5" />
    ),
    folder: (
      <>
        <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        <path d="M3 9h18" />
      </>
    ),
    camera: (
      <>
        <path d="M4 8a2 2 0 0 1 2-2h2l1.5-2h5L16 6h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" />
        <circle cx="12" cy="12" r="3.2" />
      </>
    ),
    mapPinned: (
      <>
        <path d="M9 18 3 21V6l6-3 6 3 6-3v15l-6 3-6-3Z" />
        <path d="M9 3v15" />
        <path d="M15 6v15" />
        <path d="M12 9.2a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4Z" />
      </>
    ),
    target: (
      <>
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="12" r="3" />
      </>
    ),
    sliders: (
      <>
        <path d="M4 6h16" />
        <circle cx="9" cy="6" r="2" />
        <path d="M4 12h16" />
        <circle cx="15" cy="12" r="2" />
        <path d="M4 18h16" />
        <circle cx="11" cy="18" r="2" />
      </>
    ),
    map: (
      <>
        <path d="M9 18 3 21V6l6-3 6 3 6-3v15l-6 3-6-3Z" />
        <path d="M9 3v15" />
        <path d="M15 6v15" />
      </>
    ),
    fileUp: (
      <>
        <path d="M12 3v12" />
        <path d="m7 8 5-5 5 5" />
        <path d="M5 15v4h14v-4" />
      </>
    ),
    cloud: (
      <>
        <path d="M7 18h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6.6 8.5 3.5 3.5 0 0 0 7 18Z" />
      </>
    ),
    list: (
      <>
        <path d="M8 6h12" />
        <path d="M8 12h12" />
        <path d="M8 18h12" />
        <circle cx="4" cy="6" r="1" />
        <circle cx="4" cy="12" r="1" />
        <circle cx="4" cy="18" r="1" />
      </>
    ),
    globe: (
      <>
        <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z" />
        <path d="M3 12h18" />
        <path d="M12 3c2.5 2.5 4 5.7 4 9s-1.5 6.5-4 9c-2.5-2.5-4-5.7-4-9s1.5-6.5 4-9Z" />
      </>
    ),
    chart: (
      <>
        <path d="M4 19V5" />
        <path d="M4 19h16" />
        <path d="M8 16v-4" />
        <path d="M12 16V8" />
        <path d="M16 16v-6" />
      </>
    ),
    upload: (
      <>
        <path d="M12 3v10" />
        <path d="m7 8 5-5 5 5" />
        <path d="M5 15v4h14v-4" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 12a8 8 0 0 1-13.66 5.66" />
        <path d="M4 12a8 8 0 0 1 13.66-5.66" />
        <path d="m14 3 3.66 3.66L14 10.31" />
        <path d="m10 21-3.66-3.66L10 13.69" />
      </>
    ),
    stop: (
      <rect x="7" y="7" width="10" height="10" rx="2" />
    ),
    play: (
      <path d="M8 5v14l11-7-11-7Z" />
    ),
    save: (
      <>
        <path d="M5 5h10l4 4v10H5z" />
        <path d="M9 5v6h6V5" />
        <path d="M8 19h8" />
      </>
    ),
    menu: (
      <>
        <path d="M4 6h16" />
        <path d="M4 12h16" />
        <path d="M4 18h16" />
      </>
    ),
    x: (
      <>
        <path d="M18 6 6 18" />
        <path d="m6 6 12 12" />
      </>
    ),
  }

  return (
    <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {icons[name] || icons.activity}
    </svg>
  )
}

export default function App() {
  const [status, setStatus] = useState(null)
  const [logs, setLogs] = useState([])
  const [ingesta, setIngesta] = useState(null)
  const [overlay, setOverlay] = useState(null)
  const [projectName, setProjectName] = useState('')
  const [cameraModel, setCameraModel] = useState('mavic_3m')
  const [driveUrl, setDriveUrl] = useState('')
  const [showDrive, setShowDrive] = useState(false)
  const [notice, setNotice] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [zipFileKey, setZipFileKey] = useState(0)
  const [panelOpen, setPanelOpen] = useState(window.innerWidth > 760)
  const zipInputRef = useRef(null)

  useEffect(() => {
    const check = () => setPanelOpen(window.innerWidth > 760)
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [])

  const finalMode = Boolean(status?.step === 'finalizado' && !status?.running)
  const points = ingesta?.puntos_gps || []
  const metrics = useMemo(
    () => [
      { label: 'Estado', value: status?.running ? 'Ejecutando' : status?.step || '-' },
      { label: 'Paso', value: status?.step || '-' },
      { label: 'Proyecto', value: ingesta?.nombre_proyecto || '-' },
      { label: 'Modelo', value: ingesta?.camera_model || '-' },
      { label: 'Archivos', value: ingesta?.total_archivos ?? '-' },
      { label: 'Imagenes validas', value: ingesta?.imagenes_validas ?? '-' },
      { label: 'Puntos GPS', value: points.length },
      { label: 'Orto RGB', value: overlay?.disponible ? 'Listo' : 'No disponible' },
      { label: 'Orto MS', value: overlay?.disponible_ms ? 'Listo' : 'No disponible' },
    ],
    [status, ingesta, overlay, points.length],
  )

  const refreshAll = async () => {
    setRefreshing(true)
    try {
      const [statusData, logsData, ingestaData, overlayData] = await Promise.all([
        apiJson('/status'),
        apiJson('/logs'),
        apiJson('/ingesta/estado'),
        apiJson('/overlay/status'),
      ])
      setStatus(statusData)
      setLogs(logsData.logs || [])
      setIngesta(ingestaData)
      setOverlay(overlayData)
      setProjectName((current) => current || ingestaData.nombre_proyecto || '')
      setCameraModel((current) => current || ingestaData.camera_model || 'mavic_3m')
    } catch (error) {
      setNotice({ kind: 'error', text: error.message || 'No se pudo actualizar el estado' })
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    refreshAll()
    const timer = setInterval(refreshAll, 3500)
    return () => clearInterval(timer)
  }, [])

  const showMessage = (kind, text) => {
    setNotice({ kind, text })
    window.clearTimeout(showMessage._timer)
    showMessage._timer = window.setTimeout(() => setNotice(null), 4500)
  }

  const saveProjectName = async () => {
    const nombre = projectName.trim()
    if (!nombre) {
      showMessage('error', 'Escribe un nombre de proyecto antes de guardarlo')
      return
    }

    try {
      const formData = new FormData()
      formData.append('nombre', nombre)
      const data = await apiJson('/proyecto/nombre', { method: 'POST', body: formData })
      setProjectName(data.nombre_proyecto || nombre)
      setIngesta((current) => ({ ...(current || {}), nombre_proyecto: data.nombre_proyecto || nombre }))
      showMessage('success', data.mensaje || 'Nombre de proyecto guardado')
    } catch (error) {
      showMessage('error', error.message)
    }
  }

  const newProject = async () => {
    try {
      const formData = new FormData()
      if (projectName.trim()) formData.append('nombre', projectName.trim())
      formData.append('camera_model', cameraModel)
      const data = await apiJson('/proyecto/nuevo', { method: 'POST', body: formData })
      setProjectName('')
      setCameraModel(data.camera_model || 'mavic_3m')
      setDriveUrl('')
      setShowDrive(false)
      setUploadProgress(null)
      showMessage('success', data.mensaje || 'Proyecto reiniciado')
      await refreshAll()
    } catch (error) {
      showMessage('error', error.message || 'No se pudo reiniciar el proyecto')
    }
  }

  const uploadZip = () => {
    zipInputRef.current?.click()
  }

  const handleZipChange = (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.zip')) {
      showMessage('error', 'El archivo debe ser .zip')
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    formData.append('archivo', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/ingesta/zip`, true)
    xhr.responseType = 'json'

    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable) {
        setUploadProgress(Math.round((evt.loaded / evt.total) * 100))
      } else {
        setUploadProgress(50)
      }
    }

    xhr.onload = async () => {
      try {
        const data = xhr.response || {}
        if (xhr.status < 200 || xhr.status >= 300) {
          throw new Error(data.detail || data.mensaje || 'No se pudo subir el ZIP')
        }
        setUploadProgress(100)
        showMessage('success', data.mensaje || 'ZIP cargado correctamente')
        setZipFileKey((value) => value + 1)
        await refreshAll()
      } catch (error) {
        showMessage('error', error.message || 'Error al subir ZIP')
      } finally {
        window.setTimeout(() => setUploadProgress(null), 700)
      }
    }

    xhr.onerror = () => {
      showMessage('error', 'Error de red al subir ZIP')
      window.setTimeout(() => setUploadProgress(null), 700)
    }

    setUploadProgress(0)
    xhr.send(formData)
  }

  const submitDrive = async () => {
    const url = driveUrl.trim()
    if (!url || !url.includes('drive.google.com')) {
      showMessage('error', 'La URL debe contener drive.google.com')
      return
    }

    try {
      const formData = new FormData()
      formData.append('url', url)
      const data = await apiJson('/ingesta/drive', { method: 'POST', body: formData })
      showMessage('success', data.mensaje || 'ZIP descargado correctamente')
      setDriveUrl('')
      setShowDrive(false)
      await refreshAll()
    } catch (error) {
      showMessage('error', error.message || 'Error al descargar desde Drive')
    }
  }

  const startProcess = async () => {
    try {
      const formData = new FormData()
      if (projectName.trim()) formData.append('nombre_proyecto', projectName.trim())
      formData.append('camera_model', cameraModel)
      const data = await apiJson('/procesar', { method: 'POST', body: formData })
      if (data.nombre_proyecto) setProjectName(data.nombre_proyecto)
      if (data.camera_model) setCameraModel(data.camera_model)
      showMessage('success', data.message || 'Proceso iniciado')
      await refreshAll()
    } catch (error) {
      showMessage('error', error.message || 'No se pudo iniciar el proceso')
    }
  }

  const stopProcess = async () => {
    try {
      const data = await apiJson('/stop', { method: 'POST' })
      showMessage('success', data.message || 'Se solicito detener el proceso')
      await refreshAll()
    } catch (error) {
      showMessage('error', error.message || 'No se pudo detener el proceso')
    }
  }

  return (
    <div className="app-shell">
      <div className="map-stage">
        <MapView points={points} overlay={overlay} finalMode={finalMode} />
      </div>
      <div className="backdrop backdrop-a" />
      <div className="backdrop backdrop-b" />

      <header className="topbar">
        <div className="brand">
          <h1>GEOFIELD</h1>
        </div>
        <div className={`status-pill ${stateTone(status)}`}>
          <span className="status-dot" />
          <div>
            <strong>{status?.running ? 'Ejecutando' : status?.step || 'Listo'}</strong>
            <span>{status?.message || 'Esperando accion'}</span>
          </div>
        </div>
      </header>

      <button
        className={`panel-toggle ${panelOpen ? 'is-open' : ''}`}
        onClick={() => setPanelOpen((v) => !v)}
        aria-label="Alternar panel de control"
      >
        <Icon name={panelOpen ? 'x' : 'menu'} />
      </button>

      <aside className={`control-panel ${panelOpen ? 'panel-open' : 'panel-closed'}`}>
        <div className="panel-layout">
          <div className="panel-top">
            <div className="panel-column panel-column-left">
            <section className="card glass-card panel-mini-card panel-control-card">
              <div className="section-head">
                <h2>
                  <Icon name="mapPinned" />
                  Centro de control
                </h2>
                <span>{overlay?.disponible ? 'Overlay listo' : 'Sin overlay'}</span>
              </div>
              <div className="panel-badge panel-badge-wide">
                <Icon name="activity" />
                <span>{finalMode ? 'RGB en tiles' : 'Vista de vuelo'}</span>
              </div>
            </section>

            <section className="card glass-card panel-state-card">
              <div className="section-head">
                <h2>
                  <Icon name="globe" />
                  Estado
                </h2>
                <span>{formatDate(status?.started_at)}</span>
              </div>
              <dl className="status-list status-list-tight">
                <div>
                  <dt>Inicio</dt>
                  <dd>{formatDate(status?.started_at)}</dd>
                </div>
                <div>
                  <dt>Fin</dt>
                  <dd>{formatDate(status?.finished_at)}</dd>
                </div>
                <div>
                  <dt>Origen</dt>
                  <dd>{ingesta?.origen || '-'}</dd>
                </div>
                <div>
                  <dt>Actualizado</dt>
                  <dd>{formatDate(ingesta?.actualizado_en)}</dd>
                </div>
              </dl>
            </section>

            </div>

            <div className="panel-column panel-column-right">
              <section className="card glass-card panel-span-compact">
              <div className="section-head">
                <h2>
                  <Icon name="list" />
                  Logs
                </h2>
                  <span>{logs.length} lineas</span>
                </div>
                <pre className="logs-panel logs-panel-tight">{logs.length ? logs.join('\n') : 'Esperando eventos...'}</pre>
              </section>

              <section className="card glass-card panel-span-compact">
              <div className="section-head">
                <h2>
                  <Icon name="chart" />
                  Resumen
                </h2>
                  <span>{metrics.length} indicadores</span>
                </div>
                <div className="metrics-grid metrics-grid-compact metrics-grid-2">
                  {metrics.map((item) => (
                    <article className="metric metric-light" key={item.label}>
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </article>
                  ))}
                </div>
              </section>
            </div>
          </div>

          <section className="card glass-card panel-span-compact panel-bottom-card panel-control-card">
            <div className="section-head">
              <h2>
                <Icon name="sliders" />
                Controles
              </h2>
              <span>{status?.running ? 'Proceso activo' : 'Listo para iniciar'}</span>
            </div>

              <div className="toolbar-grid toolbar-grid-compact">
                <label className="field">
                  <span className="field-label">
                    <Icon name="folder" />
                    Nombre del proyecto
                  </span>
                  <input
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                    placeholder="test_agisoft"
                  />
                </label>
                <label className="field">
                  <span className="field-label">
                    <Icon name="camera" />
                    Modelo de camara
                  </span>
                  <select value={cameraModel} onChange={(event) => setCameraModel(event.target.value)}>
                    <option value="mavic_3m">Mavic 3 Multispectral</option>
                    <option value="rededge_m">MicaSense RedEdge-M</option>
                  </select>
              </label>
            </div>

              <div className="action-row action-row-tight">
                <button className="primary" onClick={startProcess} disabled={status?.running}>
                  <Icon name="play" />
                  Procesar
                </button>
                <button className="secondary" onClick={refreshAll} disabled={refreshing}>
                  <Icon name="refresh" />
                  Actualizar
                </button>
                <button className="secondary" onClick={stopProcess} disabled={!status?.running}>
                  <Icon name="stop" />
                  Detener
                </button>
                <button className="accent" onClick={uploadZip}>
                  <Icon name="upload" />
                  Subir ZIP
                </button>
                <button className="accent" onClick={() => setShowDrive((value) => !value)}>
                  <Icon name="cloud" />
                  Enlace Drive
              </button>
                <button className="secondary" onClick={saveProjectName}>
                  <Icon name="save" />
                  Guardar nombre
                </button>
                <button className="secondary" onClick={newProject} disabled={status?.running}>
                  <Icon name="folder" />
                  Nuevo proyecto
                </button>
              </div>

            <input
              ref={zipInputRef}
              key={zipFileKey}
              type="file"
              accept=".zip"
              hidden
              onChange={handleZipChange}
            />

            {showDrive ? (
              <div className="drive-box drive-box-white">
                <input
                  value={driveUrl}
                  onChange={(event) => setDriveUrl(event.target.value)}
                  placeholder="Pega un enlace publico de Google Drive"
                />
                <button className="primary" onClick={submitDrive}>
                  Enviar
                </button>
                <button className="secondary" onClick={() => setShowDrive(false)}>
                  Cancelar
                </button>
              </div>
            ) : null}

            {uploadProgress !== null ? (
              <div className="progress-wrap">
                <div className="progress-label">
                  {uploadProgress < 100 ? `Subiendo ZIP: ${uploadProgress}%` : 'ZIP cargado'}
                </div>
                <div className="progress-bar">
                  <div className="progress-fill" style={{ width: `${uploadProgress}%` }} />
                </div>
              </div>
            ) : null}

            {notice ? <div className={`notice ${notice.kind}`}>{notice.text}</div> : null}
          </section>
        </div>
      </aside>
    </div>
  )
}

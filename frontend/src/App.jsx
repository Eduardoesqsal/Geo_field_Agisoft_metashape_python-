import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import logo from './assets/logo.png'
import { API_BASE, apiJson } from './utils/api'
import { DEFAULT_CENTER } from './utils/constants'
import { formatAreaHa, formatDate, processingProgress } from './utils/formatters'

function MapView({ points, datasetId, overlay, overlayVisible, finalMode, vectorOverlay, vectorVisible, drawingRoi, roiPoints, onRoiPoint }) {
  const mapRef = useRef(null)
  const mapInstanceRef = useRef(null)
  const baseLayerRef = useRef(null)
  const pointsLayerRef = useRef(null)
  const overlayLayerRef = useRef(null)
  const vectorLayerRef = useRef(null)
  const roiDraftLayerRef = useRef(null)
  const hasUserInteractedRef = useRef(false)
  const pointsFocusTokenRef = useRef(null)
  const focusedDatasetRef = useRef(null)
  const overlayFocusTokenRef = useRef(null)
  const vectorFocusTokenRef = useRef(null)
  const [vectorGeojson, setVectorGeojson] = useState(null)

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
      zoomControl: false,
      preferCanvas: true,
      maxZoom: 24,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      scrollWheelZoom: true,
    }).setView(DEFAULT_CENTER, 5)
    L.control.zoom({ position: 'topright' }).addTo(map)

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
      vectorLayerRef.current = null
      roiDraftLayerRef.current = null
      hasUserInteractedRef.current = false
      pointsFocusTokenRef.current = null
      focusedDatasetRef.current = null
      overlayFocusTokenRef.current = null
      vectorFocusTokenRef.current = null
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
  const overlayReady = Boolean(finalMode && overlay?.disponible && overlay?.bounds)

  const vectorToken = useMemo(
    () =>
      vectorOverlay?.disponible && vectorOverlay?.bounds
        ? vectorOverlay.cache_buster || vectorOverlay.nombre || 'vector'
        : null,
    [vectorOverlay],
  )

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !drawingRoi) return
    const handleClick = (event) => {
      hasUserInteractedRef.current = true
      onRoiPoint([event.latlng.lng, event.latlng.lat])
    }
    map.doubleClickZoom.disable()
    map.on('click', handleClick)
    return () => {
      map.off('click', handleClick)
      map.doubleClickZoom.enable()
    }
  }, [drawingRoi, onRoiPoint])

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return
    if (roiDraftLayerRef.current) map.removeLayer(roiDraftLayerRef.current)
    roiDraftLayerRef.current = null
    if (!drawingRoi || !roiPoints.length) return
    const latlngs = roiPoints.map(([lon, lat]) => [lat, lon])
    const capa = L.layerGroup().addTo(map)
    if (latlngs.length >= 3) {
      L.polygon(latlngs, { color: '#06b6d4', weight: 3, fillColor: '#06b6d4', fillOpacity: 0.2 }).addTo(capa)
    } else if (latlngs.length >= 2) {
      L.polyline(latlngs, { color: '#06b6d4', weight: 3 }).addTo(capa)
    }
    latlngs.forEach((latlng, index) => {
      L.circleMarker(latlng, {
        radius: 5, color: '#ffffff', weight: 2, fillColor: '#0891b2', fillOpacity: 1,
        bubblingMouseEvents: false,
      }).bindTooltip(`Vertice ${index + 1}`).addTo(capa)
    })
    roiDraftLayerRef.current = capa
    return () => {
      map.removeLayer(capa)
      if (roiDraftLayerRef.current === capa) roiDraftLayerRef.current = null
    }
  }, [drawingRoi, roiPoints])

  useEffect(() => {
    const map = mapInstanceRef.current
    const pointsLayer = pointsLayerRef.current
    if (!map || !pointsLayer) return

    if (focusedDatasetRef.current !== datasetId) {
      focusedDatasetRef.current = datasetId
      hasUserInteractedRef.current = false
      pointsFocusTokenRef.current = null
    }
    pointsLayer.clearLayers()
    const validPoints = (points || []).filter(
      (point) => Number.isFinite(point?.lat) && Number.isFinite(point?.lon),
    )

    if (overlayReady) {
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
    const focusToken = `${datasetId || 'legacy'}:${pointsSignature}`
    if (pointsFocusTokenRef.current === focusToken) return

    if (validPoints.length === 1) {
      map.setView([validPoints[0].lat, validPoints[0].lon], 17)
    } else {
      map.fitBounds(
        validPoints.map((point) => [point.lat, point.lon]),
        { padding: [36, 36] },
      )
    }
    pointsFocusTokenRef.current = focusToken
  }, [datasetId, pointsSignature, overlayReady])

  useEffect(() => {
    if (!vectorToken) {
      setVectorGeojson(null)
      return
    }

    let cancelled = false

    apiJson('/overlay/vector.geojson')
      .then((data) => {
        if (!cancelled) {
          setVectorGeojson(data)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setVectorGeojson(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [vectorToken])

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    const baseLayer = baseLayerRef.current

    if (!overlayReady || !overlayVisible) {
      if (overlayLayerRef.current) {
        map.removeLayer(overlayLayerRef.current)
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
        detectRetina: false,
      })
      layer.__token = overlayToken
      overlayLayerRef.current = layer
      overlayFocusTokenRef.current = null
    }

    if (overlayFocusTokenRef.current === overlayToken) return

    try {
      map.fitBounds(overlay.bounds, { padding: [32, 32] })
    } catch {
      map.setView(DEFAULT_CENTER, 5)
    }
    overlayFocusTokenRef.current = overlayToken

    if (!map.hasLayer(overlayLayerRef.current)) {
      overlayLayerRef.current.addTo(map)
    }

    overlayLayerRef.current.bringToFront?.()
  }, [overlayReady, overlayVisible, overlay, overlayToken])

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    if (!vectorVisible || !vectorOverlay?.disponible || !vectorOverlay?.bounds || !vectorGeojson) {
      if (vectorLayerRef.current) {
        map.removeLayer(vectorLayerRef.current)
      }
      return
    }

    if (!vectorLayerRef.current || vectorLayerRef.current.__token !== vectorToken) {
      if (vectorLayerRef.current) {
        map.removeLayer(vectorLayerRef.current)
      }

      const layer = L.geoJSON(vectorGeojson, {
        style: {
          color: '#64ff10',
          weight: 2,
          fillColor: '#64ff10',
          fillOpacity: 0,
        },
        onEachFeature(feature, leafletLayer) {
          const props = feature?.properties || {}
          const title = props.name || props.Name || props.nombre || 'Poligono'
          leafletLayer.bindPopup(title)
        },
      })
      layer.__token = vectorToken
      vectorLayerRef.current = layer
      vectorFocusTokenRef.current = null
    }

    if (!map.hasLayer(vectorLayerRef.current)) {
      vectorLayerRef.current.addTo(map)
    }

    vectorLayerRef.current.bringToFront?.()

    if (hasUserInteractedRef.current) return
    if (vectorFocusTokenRef.current === vectorToken) return

    try {
      map.fitBounds(vectorOverlay.bounds, { padding: [26, 26] })
    } catch {
      map.setView(DEFAULT_CENTER, 5)
    }
    vectorFocusTokenRef.current = vectorToken
  }, [vectorVisible, vectorOverlay, vectorToken, vectorGeojson])

  return <div ref={mapRef} className={`map-canvas${drawingRoi ? ' is-drawing-roi' : ''}`} />
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
    bell: (
      <>
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" />
        <path d="M10 21h4" />
      </>
    ),
    polygon: (
      <>
        <path d="M6 5.5 18 6.5 20 17 10 20 4 16Z" strokeLinejoin="round" />
        <circle cx="6" cy="5.5" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="18" cy="6.5" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="20" cy="17" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="10" cy="20" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="4" cy="16" r="1.6" fill="currentColor" stroke="none" />
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
    trash: (
      <>
        <path d="M4 7h16" />
        <path d="M9 7V4h6v3" />
        <path d="m6 7 1 13h10l1-13" />
        <path d="M10 11v5" />
        <path d="M14 11v5" />
      </>
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
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    flag: (
      <>
        <path d="M5 21V4" />
        <path d="M5 5h10l-1.5 3L15 11H5" />
      </>
    ),
    database: (
      <>
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
        <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
      </>
    ),
    image: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <circle cx="9" cy="9" r="2" />
        <path d="m4 17 5-5 4 4 2-2 5 5" />
      </>
    ),
    layers: (
      <>
        <path d="m12 3 9 5-9 5-9-5 9-5Z" />
        <path d="m3 12 9 5 9-5" />
        <path d="m3 16 9 5 9-5" />
      </>
    ),
    route: (
      <>
        <circle cx="6" cy="18" r="2" />
        <circle cx="18" cy="6" r="2" />
        <path d="M8 18h3a3 3 0 0 0 3-3V9a3 3 0 0 1 3-3" />
      </>
    ),
    monitor: (
      <>
        <rect x="4" y="5" width="16" height="12" rx="2" />
        <path d="M8 21h8" />
        <path d="M12 17v4" />
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
  const [overlayVisible, setOverlayVisible] = useState(true)
  const [projectName, setProjectName] = useState('')
  const [newProjectName, setNewProjectName] = useState('')
  const [cameraModel, setCameraModel] = useState('mavic_3_rgb')
  const [driveUrl, setDriveUrl] = useState('')
  const [showDrive, setShowDrive] = useState(false)
  const [notice, setNotice] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(null)
  const [vectorUploadProgress, setVectorUploadProgress] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [zipFileKey, setZipFileKey] = useState(0)
  const [vectorFileKey, setVectorFileKey] = useState(0)
  const [vectorVisible, setVectorVisible] = useState(true)
  const [drawingRoi, setDrawingRoi] = useState(false)
  const [roiPoints, setRoiPoints] = useState([])
  const [vectorOverlay, setVectorOverlay] = useState(null)
  const [catalog, setCatalog] = useState({ proyectos: [], datasets: [], project_id: null, dataset_id: 'legacy' })
  const [jobQueue, setJobQueue] = useState({ enabled: false, running: false, jobs: [] })
  const [jobSensors, setJobSensors] = useState({})
  const [activeModal, setActiveModal] = useState(null)
  const [deletingProjectId, setDeletingProjectId] = useState(null)
  const [savingProject, setSavingProject] = useState(false)
  const [mapSession, setMapSession] = useState(0)
  const [projectPrepared, setProjectPrepared] = useState(() => window.sessionStorage.getItem('geofield-project-prepared') === '1')
  const zipInputRef = useRef(null)
  const vectorInputRef = useRef(null)
  const seenReadyJobsRef = useRef(null)
  const pendingPrepareRef = useRef(null)
  const previousRunningRef = useRef(false)
  const promptedCompletionRef = useRef(null)

  const addRoiPoint = useCallback(([lon, lat]) => {
    setRoiPoints((current) => {
      const ultimo = current[current.length - 1]
      if (ultimo && Math.abs(ultimo[0] - lon) < 1e-8 && Math.abs(ultimo[1] - lat) < 1e-8) return current
      return [...current, [lon, lat]]
    })
  }, [])

  const finalMode = Boolean(status?.step === 'finalizado' && !status?.running)
  const processingPercent = processingProgress(status)
  const points = ingesta?.puntos_gps || []
  const waitingJobs = (jobQueue.jobs || []).filter((job) =>
    ['pending', 'downloading', 'ready', 'preparing', 'prepared'].includes(job.state)).length
  const currentReceivedJob = (jobQueue.jobs || []).find((job) => job.project_id && job.project_id === ingesta?.project_id)
  const canDeleteDataset = (project) => Boolean(project && !project.id.startsWith('legacy:')
    && project.dataset_id && project.dataset_id !== 'legacy'
    && !catalog.proyectos.some((item) => item.id !== project.id && item.dataset_id === project.dataset_id))

  const refreshAll = async () => {
    setRefreshing(true)
    try {
      const [statusData, logsData, ingestaData, overlayData, vectorData, queueData] = await Promise.all([
        apiJson('/status'),
        apiJson('/logs'),
        apiJson('/ingesta/estado'),
        apiJson('/overlay/status'),
        apiJson('/overlay/shapefile/status'),
        apiJson('/trabajos'),
      ])
      setStatus(statusData)
      setLogs(logsData.logs || [])
      setIngesta(ingestaData)
      setOverlay(overlayData)
      setVectorOverlay(vectorData)
      setJobQueue(queueData)
      if (previousRunningRef.current && !statusData.running && queueData.next_job
        && ['finalizado', 'error'].includes(statusData.step)) {
        const succeeded = statusData.step === 'finalizado'
        showMessage(succeeded ? 'success' : 'error',
          `${succeeded ? 'Terminó el procesamiento' : 'El trabajo terminó con error'}. Siguiente trabajo listo: ${queueData.next_job.job_code}`)
      }
      previousRunningRef.current = Boolean(statusData.running)
      if (pendingPrepareRef.current && !statusData.running) {
        const preparedJob = (queueData.jobs || []).find((job) =>
          `${job.organization_id}:${job.batch_id}` === pendingPrepareRef.current)
        if (preparedJob?.state === 'prepared' && ingestaData.project_id === preparedJob.project_id) {
          pendingPrepareRef.current = null
          setActiveModal('images')
          showMessage('success', `Proyecto ${preparedJob.job_code} listo: revisa las fotos y el ROI antes de procesar`)
        } else if (preparedJob?.state === 'failed') {
          pendingPrepareRef.current = null
          showMessage('error', preparedJob.error || 'No se pudo preparar el trabajo')
        }
      }
      const readyIds = new Set((queueData.jobs || [])
        .filter((job) => job.state === 'ready')
        .map((job) => `${job.organization_id}:${job.batch_id}`))
      if (seenReadyJobsRef.current) {
        const newest = (queueData.jobs || []).find((job) => job.state === 'ready'
          && !seenReadyJobsRef.current.has(`${job.organization_id}:${job.batch_id}`))
        if (newest) showMessage('success', `Nuevo trabajo recibido: ${newest.job_code}`)
      }
      seenReadyJobsRef.current = readyIds
      setProjectName(ingestaData.project_id ? ingestaData.nombre_proyecto || '' : '')
      if (ingestaData.project_id) setCameraModel(ingestaData.camera_model || 'mavic_3_rgb')
      setProjectPrepared(Boolean(ingestaData.project_id))
    } catch (error) {
      setNotice({ kind: 'error', text: error.message || 'No se pudo actualizar el estado' })
    } finally {
      setRefreshing(false)
    }
  }

  const refreshCatalog = async () => {
    const data = await apiJson('/proyectos')
    setCatalog(data)
  }

  useEffect(() => {
    refreshAll()
    refreshCatalog().catch(() => {})
    const timer = setInterval(refreshAll, 3500)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!status?.running && status?.step === 'finalizado') refreshCatalog().catch(() => {})
  }, [status?.running, status?.step])

  useEffect(() => {
    if (status?.running || status?.step !== 'finalizado' || !status.started_at || !ingesta?.project_id) return
    const completionKey = `${ingesta.project_id}:${status.started_at}`
    if (promptedCompletionRef.current === completionKey) return
    promptedCompletionRef.current = completionKey
    setActiveModal('save')
  }, [status?.running, status?.step, status?.started_at, ingesta?.project_id])

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key !== 'Escape' || !activeModal) return
      setActiveModal(null)
      setDrawingRoi(false)
      setRoiPoints([])
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeModal])

  const showMessage = (kind, text) => {
    setNotice({ kind, text })
    window.clearTimeout(showMessage._timer)
    showMessage._timer = window.setTimeout(() => setNotice(null), 4500)
  }

  const newProject = async () => {
    if (!newProjectName.trim()) {
      showMessage('error', 'Escribe el nombre del trabajo')
      return
    }
    try {
      const formData = new FormData()
      formData.append('nombre', newProjectName.trim())
      formData.append('camera_model', cameraModel)
      const data = await apiJson('/proyecto/nuevo', { method: 'POST', body: formData })
      setProjectName(data.nombre_proyecto || '')
      setNewProjectName('')
      setCameraModel(data.camera_model || 'mavic_3_rgb')
      setDriveUrl('')
      setShowDrive(false)
      setUploadProgress(null)
      setDrawingRoi(false)
      setRoiPoints([])
      setProjectPrepared(true)
      window.sessionStorage.setItem('geofield-project-prepared', '1')
      showMessage('success', data.mensaje || 'Proyecto reiniciado')
      await refreshAll()
      await refreshCatalog()
    } catch (error) {
      showMessage('error', error.message || 'No se pudo reiniciar el proyecto')
    }
  }

  const selectProject = async (projectId, openMap = false) => {
    if (!projectId) return
    try {
      const formData = new FormData()
      formData.append('project_id', projectId)
      const data = await apiJson('/proyectos/seleccionar', { method: 'POST', body: formData })
      setProjectName(data.nombre_proyecto)
      setCameraModel(data.camera_model)
      setDrawingRoi(false)
      setRoiPoints([])
      setProjectPrepared(true)
      window.sessionStorage.setItem('geofield-project-prepared', '1')
      await refreshAll()
      await refreshCatalog()
      if (openMap) {
        setOverlayVisible(true)
        setVectorVisible(true)
        setActiveModal(null)
      }
      showMessage('success', data.mensaje)
    } catch (error) {
      showMessage('error', error.message || 'No se pudo seleccionar el proyecto')
    }
  }

  const prepareIncomingJob = async (job) => {
    const key = `${job.organization_id}:${job.batch_id}`
    const sensor = jobSensors[key]
    if (!sensor) {
      showMessage('error', 'Selecciona el sensor del trabajo')
      return
    }
    try {
      const formData = new FormData()
      formData.append('camera_model', sensor)
      const data = await apiJson(`/trabajos/${encodeURIComponent(job.organization_id)}/${encodeURIComponent(job.batch_id)}/preparar`, {
        method: 'POST', body: formData,
      })
      pendingPrepareRef.current = key
      setCameraModel(sensor)
      showMessage('success', data.mensaje || 'Preparando las fotos recibidas')
      await refreshAll()
    } catch (error) {
      showMessage('error', error.message || 'No se pudo preparar el trabajo')
    }
  }

  const deleteProject = async () => {
    const project = catalog.proyectos.find((item) => item.id === catalog.project_id)
    if (project) await deleteProjectWithFiles(project, canDeleteDataset(project))
  }

  const deleteProjectWithFiles = async (project, deleteDataset) => {
    if (!project || status?.running || deletingProjectId || project.id.startsWith('legacy:')) return
    if (deleteDataset && !canDeleteDataset(project)) return
    const confirmation = deleteDataset
      ? `¿Borrar definitivamente el proyecto "${project.nombre}" y el dataset "${project.dataset_id}" con todas sus fotos?`
      : `¿Borrar definitivamente el proyecto "${project.nombre}"? Su dataset se conservará porque es compartido o pertenece al área original.`
    if (!window.confirm(confirmation)) return
    setDeletingProjectId(project.id)
    try {
      const data = await apiJson(`/proyectos/${encodeURIComponent(project.id)}?delete_dataset=${deleteDataset}`, { method: 'DELETE' })
      if (catalog.project_id === project.id) {
        setProjectName('')
        setProjectPrepared(false)
        window.sessionStorage.removeItem('geofield-project-prepared')
      }
      await refreshAll()
      await refreshCatalog()
      showMessage('success', data.mensaje || 'Proyecto borrado')
    } catch (error) {
      showMessage('error', error.message || 'No se pudo borrar el proyecto')
    } finally {
      setDeletingProjectId(null)
    }
  }

  const selectDataset = async (datasetId) => {
    if (!datasetId) return
    try {
      const formData = new FormData()
      formData.append('dataset_id', datasetId)
      const data = await apiJson('/datasets/seleccionar', { method: 'POST', body: formData })
      await refreshAll()
      await refreshCatalog()
      showMessage('success', data.mensaje)
    } catch (error) {
      showMessage('error', error.message || 'No se pudo seleccionar el dataset')
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
        await refreshCatalog()
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

  const uploadVector = () => {
    vectorInputRef.current?.click()
  }

  const saveRoi = async () => {
    if (roiPoints.length < 3) {
      showMessage('error', 'Marca al menos tres vertices en el mapa')
      return
    }
    try {
      const data = await apiJson('/overlay/vector/roi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ coordinates: roiPoints }),
      })
      setDrawingRoi(false)
      setRoiPoints([])
      setVectorVisible(true)
      showMessage('success', data.mensaje)
      await refreshAll()
      await refreshCatalog()
    } catch (error) {
      showMessage('error', error.message || 'No se pudo guardar el ROI')
    }
  }

  const removeClip = async () => {
    try {
      const data = await apiJson('/overlay/vector/recorte', { method: 'DELETE' })
      showMessage('success', data.mensaje)
      await refreshAll()
      await refreshCatalog()
    } catch (error) {
      showMessage('error', error.message || 'No se pudo quitar el recorte')
    }
  }

  const handleVectorChange = (event) => {
    const file = event.target.files?.[0]
    if (!file) return

    const ext = file.name.toLowerCase().split('.').pop()
    if (!['zip', 'kml', 'kmz', 'geojson', 'json', 'shp'].includes(ext)) {
      showMessage('error', 'El archivo debe ser .zip, .kml, .kmz, .geojson, .json o .shp')
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    formData.append('archivo', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/overlay/vector`, true)
    xhr.responseType = 'json'

    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable) {
        setVectorUploadProgress(Math.round((evt.loaded / evt.total) * 100))
      } else {
        setVectorUploadProgress(50)
      }
    }

    xhr.onload = async () => {
      try {
        const data = xhr.response || {}
        if (xhr.status < 200 || xhr.status >= 300) {
          throw new Error(data.detail || data.mensaje || 'No se pudo importar la capa')
        }
        setVectorUploadProgress(100)
        setVectorVisible(true)
        setVectorFileKey((value) => value + 1)
        showMessage('success', data.mensaje || 'Capa vectorial importada')
        await refreshAll()
      } catch (error) {
        showMessage('error', error.message || 'Error al importar la capa')
      } finally {
        window.setTimeout(() => setVectorUploadProgress(null), 700)
      }
    }

    xhr.onerror = () => {
      showMessage('error', 'Error de red al importar la capa')
      window.setTimeout(() => setVectorUploadProgress(null), 700)
    }

    setVectorUploadProgress(0)
    xhr.send(formData)
  }

  const submitDrive = async () => {
    const url = driveUrl.trim()
    if (!url || (!url.includes('drive.google.com') && !/^[A-Za-z0-9_.-]+:.+/.test(url))) {
      showMessage('error', 'Usa una URL de Google Drive o una ruta rclone como gdrive:carpeta')
      return
    }

    try {
      const formData = new FormData()
      formData.append('url', url)
      const data = await apiJson('/ingesta/drive', { method: 'POST', body: formData })
      showMessage('success', data.mensaje || 'Carpeta o ZIP descargado correctamente')
      setDriveUrl('')
      setShowDrive(false)
      await refreshAll()
      await refreshCatalog()
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
      await refreshCatalog()
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

  const resetToBaseMap = async () => {
    setActiveModal(null)
    setProjectName('')
    setNewProjectName('')
    setProjectPrepared(false)
    setDrawingRoi(false)
    setRoiPoints([])
    setOverlayVisible(true)
    setVectorVisible(true)
    setMapSession((current) => current + 1)
    window.sessionStorage.removeItem('geofield-project-prepared')
    await refreshAll()
    await refreshCatalog()
  }

  const leaveForNextProject = async (path) => {
    if (savingProject || status?.running) return
    setSavingProject(true)
    try {
      const data = await apiJson(path, { method: 'POST' })
      await resetToBaseMap()
      showMessage('success', data.mensaje || 'Mapa base listo para el siguiente proyecto')
    } catch (error) {
      showMessage('error', error.message || 'No se pudo volver al mapa base')
    } finally {
      setSavingProject(false)
    }
  }

  const saveProjectAndContinue = () => leaveForNextProject('/proyecto/finalizar')
  const returnToBaseMap = () => leaveForNextProject('/proyecto/cerrar')

  const toggleModal = (modal) => {
    const closing = activeModal === modal
    if (drawingRoi && (closing || modal !== 'roi')) {
      setDrawingRoi(false)
      setRoiPoints([])
    }
    setActiveModal(closing ? null : modal)
  }

  return (
    <div className="app-shell">
      <div className="map-stage">
        <MapView
          key={mapSession}
          points={points}
          datasetId={ingesta?.dataset_id}
          overlay={overlay}
          overlayVisible={overlayVisible}
          finalMode={finalMode}
          vectorOverlay={vectorOverlay}
          vectorVisible={vectorVisible}
          drawingRoi={drawingRoi}
          roiPoints={roiPoints}
          onRoiPoint={addRoiPoint}
        />
        {drawingRoi && <div className="roi-map-hint">Marca los vértices del ROI en el mapa · {roiPoints.length} puntos</div>}
      </div>
      <div className="backdrop backdrop-a" />
      <div className="backdrop backdrop-b" />

      <header className="topbar workspace-topbar">
        <div className="brand"><img src={logo} alt="GeoField" /></div>
      </header>

      <nav className="workspace-dock workspace-dock-left" aria-label="Herramientas de trabajo">
        <button type="button" className={`dock-button ${activeModal === 'jobs' ? 'is-active' : ''} ${waitingJobs ? 'has-jobs' : ''}`}
          onClick={() => toggleModal('jobs')} title="Trabajos recibidos" aria-label="Trabajos recibidos">
          <Icon name="bell" />
          {waitingJobs > 0 && <span className="dock-count">{waitingJobs > 99 ? '99+' : waitingJobs}</span>}
        </button>
        <button type="button" className={`dock-button ${activeModal === 'project' ? 'is-active' : ''}`}
          onClick={() => toggleModal('project')} title="Crear proyecto" aria-label="Crear proyecto">
          <Icon name="folder" />
        </button>
        <button type="button" className={`dock-button ${activeModal === 'images' ? 'is-active' : ''}`}
          onClick={() => toggleModal('images')} title="Imágenes" aria-label="Imágenes"
          disabled={!projectPrepared}>
          <Icon name="fileUp" />
          {(ingesta?.imagenes_validas || 0) > 0 && <span className="dock-count dock-count-photos">{ingesta.imagenes_validas > 99 ? '99+' : ingesta.imagenes_validas}</span>}
        </button>
        <button type="button" className={`dock-button ${activeModal === 'roi' ? 'is-active' : ''}`}
          onClick={() => toggleModal('roi')} title="Importar o dibujar ROI" aria-label="Importar o dibujar ROI"
          disabled={!projectPrepared}>
          <Icon name="polygon" />
          {vectorOverlay?.recorte_activo && <span className="dock-roi-ready" aria-label="ROI activo" />}
        </button>
        <button type="button" className={`dock-button ${activeModal === 'delete' ? 'is-active' : ''}`}
          onClick={() => { if (activeModal !== 'delete') refreshCatalog().catch(() => {}); toggleModal('delete') }}
          title="Proyectos" aria-label="Proyectos">
          <Icon name="map" />
        </button>
      </nav>

      <div className="workspace-dock workspace-dock-right">
        <button type="button" className={`dock-button ${activeModal === 'logs' ? 'is-active' : ''}`}
          onClick={() => toggleModal('logs')} title="Ver logs" aria-label="Ver logs">
          <Icon name="list" />
        </button>
      </div>

      {activeModal && (
        <div className={`workspace-modal-layer ${drawingRoi ? 'is-drawing' : ''}`}
          onMouseDown={(event) => { if (event.target === event.currentTarget && !drawingRoi) setActiveModal(null) }}>
          <section className={`workspace-modal workspace-modal-${activeModal}`} role="dialog"
            aria-modal={!drawingRoi} aria-labelledby="workspace-modal-title">
            <div className="workspace-modal-header">
              <div>
                <span className="workspace-eyebrow">GEOFIELD · MOTOR LOCAL</span>
                <h2 id="workspace-modal-title">
                  {activeModal === 'jobs' && 'Trabajos recibidos'}
                  {activeModal === 'project' && 'Crear proyecto'}
                  {activeModal === 'images' && 'Imágenes'}
                  {activeModal === 'roi' && 'Importar o dibujar ROI'}
                  {activeModal === 'logs' && 'Logs de procesamiento'}
                  {activeModal === 'delete' && 'Proyectos'}
                  {activeModal === 'save' && 'Proyecto terminado'}
                </h2>
              </div>
              <button className="modal-close" type="button" aria-label="Cerrar"
                onClick={() => { setActiveModal(null); setDrawingRoi(false); setRoiPoints([]) }}>
                <Icon name="x" />
              </button>
            </div>

            {activeModal === 'jobs' && (
              <div className="workspace-modal-content">
                <div className="workspace-inline-status">
                  <span className={`workspace-connection-dot ${jobQueue.running ? 'is-online' : ''}`} />
                  <span>{jobQueue.running ? 'Receptor activo' : 'Receptor sin configurar'}</span>
                  <strong>{waitingJobs} en cola</strong>
                </div>
                {jobQueue.error && <p className="job-queue-error">{jobQueue.error}</p>}
                {ingesta?.project_id && jobQueue.next_job &&
                  <p className="project-help">Guarda o cierra el proyecto actual para abrir el siguiente trabajo.</p>}
                <div className="workspace-table-wrap">
                  <table className="workspace-table">
                    <thead><tr><th>Trabajo</th><th>Organización</th><th>ZIP</th><th>Estado</th><th>Actualizado</th><th>Sensor</th><th>Acción</th></tr></thead>
                    <tbody>
                      {(jobQueue.jobs || []).map((job) => {
                        const key = `${job.organization_id}:${job.batch_id}`
                        const isNext = jobQueue.next_job?.organization_id === job.organization_id
                          && jobQueue.next_job?.batch_id === job.batch_id
                        return <tr key={key}>
                          <td><strong>{job.job_code}</strong>{job.error && <small className="job-queue-error">{job.error}</small>}</td>
                          <td>{job.organization_name || job.organization_id}</td>
                          <td>{job.file_count}</td>
                          <td><span className={`job-queue-state job-queue-state-${job.state}`}>
                            {({ pending: 'Nuevo', downloading: 'Descargando', ready: 'En espera', preparing: 'Preparando',
                              prepared: 'Listo para procesar', processing: 'Procesando', done: 'Terminado',
                              failed: 'Falló', cancelled: 'Cancelado', error: 'Error de descarga' })[job.state] || job.state}
                          </span></td>
                          <td>{formatDate(job.updated_at)}</td>
                          <td>{job.state === 'ready' ? <select className="job-sensor-select" value={jobSensors[key] || ''}
                            onChange={(event) => setJobSensors((current) => ({ ...current, [key]: event.target.value }))}>
                            <option value="">Seleccionar</option>
                            <option value="mavic_3_rgb">Mavic 3 RGB</option>
                            <option value="mavic_3m">Mavic 3 MS</option>
                            <option value="rededge_m">MicaSense RedEdge-M</option>
                          </select> : job.project_id ? ({ mavic_3_rgb: 'Mavic 3 RGB', mavic_3m: 'Mavic 3 MS', rededge_m: 'MicaSense RedEdge-M' })[
                            catalog.proyectos.find((item) => item.id === job.project_id)?.camera_model] || '-' : '-'}</td>
                          <td>{job.state === 'ready' ? <button className="round-action is-primary" type="button"
                            onClick={() => prepareIncomingJob(job)}
                            disabled={!isNext || !jobSensors[key] || status?.running || Boolean(ingesta?.project_id)}>
                            <Icon name="folder" /> Preparar
                          </button> : job.project_id ? <button className="round-action" type="button"
                            onClick={() => selectProject(job.project_id, true)}
                            disabled={status?.running || Boolean(ingesta?.project_id && ingesta.project_id !== job.project_id)}>
                            <Icon name="map" /> Abrir
                          </button> : '-'}</td>
                        </tr>
                      })}
                      {!jobQueue.jobs?.length && <tr><td colSpan="7" className="workspace-empty">Aún no hay trabajos recibidos.</td></tr>}
                    </tbody>
                  </table>
                </div>
                <button className="round-action" onClick={refreshAll} disabled={refreshing}><Icon name="refresh" /> Actualizar</button>
              </div>
            )}

            {activeModal === 'project' && (
              <div className="workspace-modal-content workspace-form">
                <label className="field"><span className="field-label">Nombre del trabajo</span>
                  <input value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)}
                    placeholder="Ej: Rancho_2026_10_30" disabled={status?.running} autoFocus />
                </label>
                <label className="field"><span className="field-label">Sensor</span>
                  <select value={cameraModel} onChange={(event) => setCameraModel(event.target.value)} disabled={status?.running}>
                    <option value="mavic_3_rgb">Mavic 3 RGB</option>
                    <option value="mavic_3m">Mavic 3 Multispectral</option>
                    <option value="rededge_m">MicaSense RedEdge-M</option>
                  </select>
                </label>
                <button className="round-action is-primary workspace-primary-action" onClick={newProject} disabled={status?.running || !newProjectName.trim()}>
                  <Icon name="folder" /> Crear proyecto
                </button>
                <details className="workspace-existing-projects">
                  <summary>Proyectos guardados</summary>
                  <label className="field"><span className="field-label">Abrir proyecto existente</span>
                    <select value={catalog.project_id || ''} onChange={(event) => selectProject(event.target.value)} disabled={status?.running}>
                      <option value="">Seleccionar proyecto</option>
                      {catalog.proyectos.map((item) => <option key={item.id} value={item.id}>{item.nombre}</option>)}
                    </select>
                  </label>
                  <button className="round-action is-danger" onClick={deleteProject}
                    disabled={status?.running || Boolean(deletingProjectId) || !catalog.project_id || catalog.project_id.startsWith('legacy:')}>
                    <Icon name="trash" /> {canDeleteDataset(catalog.proyectos.find((item) => item.id === catalog.project_id))
                      ? 'Borrar proyecto y dataset' : 'Borrar proyecto seleccionado'}
                  </button>
                </details>
              </div>
            )}

            {activeModal === 'images' && (
              <div className="workspace-modal-content workspace-images-content">
                <div className="workspace-photo-count">
                  <Icon name="image" />
                  <div><strong>{ingesta?.imagenes_validas ?? 0} fotos</strong><span>{projectName || 'Proyecto actual'}</span></div>
                </div>
                <p className="project-help">{currentReceivedJob
                  ? `Fotos recibidas para ${currentReceivedJob.job_code}. Revisa sus puntos azules y configura el ROI antes de procesar.`
                  : 'Sube las fotos originales en ZIP. Sus ubicaciones aparecerán como puntos azules en el mapa.'}</p>
                <div className="workspace-action-row">
                  <button className="round-action is-accent" onClick={uploadZip} disabled={status?.running || Boolean(currentReceivedJob)}><Icon name="upload" /> Subir imágenes ZIP</button>
                  <button className="round-action" onClick={() => setShowDrive((value) => !value)} disabled={status?.running || Boolean(currentReceivedJob)}><Icon name="cloud" /> Importar desde Drive</button>
                </div>
                {showDrive && <div className="drive-box drive-box-white">
                  <input value={driveUrl} onChange={(event) => setDriveUrl(event.target.value)} placeholder="URL de Drive o ruta rclone" />
                  <button className="primary" onClick={submitDrive}>Importar</button>
                </div>}
                {uploadProgress !== null && <div className="progress-wrap"><div className="progress-label">ZIP: {uploadProgress}%</div>
                  <div className="progress-bar"><div className="progress-fill" style={{ width: `${uploadProgress}%` }} /></div></div>}
                <label className="field"><span className="field-label">Dataset de fotos</span>
                  <select value={catalog.datasets.some((item) => item.id === catalog.dataset_id) ? catalog.dataset_id : ''}
                    onChange={(event) => selectDataset(event.target.value)} disabled={status?.running || Boolean(currentReceivedJob)}>
                    <option value="">Seleccionar dataset</option>
                    {catalog.datasets.map((item) => <option key={item.id} value={item.id}>{item.nombre} · {item.imagenes_validas} fotos</option>)}
                  </select>
                </label>

                <div className="workspace-divider" />
                <div className="workspace-action-row">
                  <button className="round-action is-primary" onClick={startProcess} disabled={status?.running || drawingRoi || !(ingesta?.imagenes_validas > 0)}>
                    <Icon name="play" /> Procesar proyecto
                  </button>
                  <button className="round-action is-warning" onClick={stopProcess} disabled={!status?.running || status?.step === 'preparando_dataset'}><Icon name="stop" /> Detener</button>
                  <button className="round-action" onClick={() => setOverlayVisible((value) => !value)} disabled={!overlay?.disponible}>
                    <Icon name="layers" /> {overlayVisible ? 'Ocultar ortomosaico' : 'Mostrar ortomosaico'}
                  </button>
                </div>
                {finalMode && ingesta?.project_id && <button className="round-action is-primary workspace-primary-action" onClick={() => setActiveModal('save')}>
                  <Icon name="save" /> Guardar y volver al mapa base
                </button>}
              </div>
            )}

            {activeModal === 'roi' && (
              <div className="workspace-modal-content workspace-roi-content">
                <div className="workspace-section-title"><h3>Área de interés</h3><span>{vectorOverlay?.recorte_activo ? 'Recorte activo' : 'Sin recorte'}</span></div>
                <p className="project-help">Importa KML, KMZ, GeoJSON o un shapefile en ZIP; también puedes dibujar el ROI sobre el mapa.</p>
                <div className="workspace-action-row">
                  <button className="round-action is-accent" onClick={uploadVector} disabled={status?.running || drawingRoi}>
                    <Icon name="polygon" /> Importar ROI
                  </button>
                  {!drawingRoi && <button className="round-action" onClick={() => { setRoiPoints([]); setDrawingRoi(true) }} disabled={status?.running}>
                    <Icon name="target" /> Dibujar polígono
                  </button>}
                  {drawingRoi && <>
                    <button className="round-action is-primary" onClick={saveRoi} disabled={roiPoints.length < 3}><Icon name="save" /> Guardar ROI</button>
                    <button className="round-action" onClick={() => setRoiPoints((current) => current.slice(0, -1))} disabled={!roiPoints.length}><Icon name="refresh" /> Deshacer</button>
                    <button className="round-action" onClick={() => { setDrawingRoi(false); setRoiPoints([]) }}><Icon name="x" /> Cancelar dibujo</button>
                  </>}
                </div>
                {drawingRoi && <p className="vector-clip-note">Marca al menos tres puntos en el mapa y pulsa Guardar ROI. Este modal permanece abierto mientras dibujas.</p>}
                {vectorUploadProgress !== null && <div className="progress-wrap"><div className="progress-label">ROI: {vectorUploadProgress}%</div>
                  <div className="progress-bar"><div className="progress-fill" style={{ width: `${vectorUploadProgress}%` }} /></div></div>}
                {vectorOverlay?.disponible && <div className="workspace-roi-details">
                  <span>{vectorOverlay.nombre || 'ROI'} · {formatAreaHa(vectorOverlay.superficie_ha)}</span>
                  <button className="round-action" onClick={() => setVectorVisible((value) => !value)} aria-pressed={vectorVisible}>
                    <Icon name="mapPinned" /> {vectorVisible ? 'Ocultar ROI' : 'Mostrar ROI'}
                  </button>
                  {vectorOverlay.recorte_activo && <button className="round-action" onClick={removeClip} disabled={status?.running || drawingRoi}>
                    Quitar recorte
                  </button>}
                </div>}
                {vectorOverlay?.recorte_activo && <p className="project-help">Mostrar u ocultar cambia la vista del mapa. «Quitar recorte» desactiva el recorte del siguiente procesamiento.</p>}
              </div>
            )}

            {activeModal === 'save' && (
              <div className="workspace-modal-content workspace-form">
                <p className="project-help">El procesamiento de <strong>{ingesta?.nombre_proyecto || 'este proyecto'}</strong> terminó. Sus archivos ya están en la carpeta del proyecto.</p>
                {jobQueue.next_job && <p className="project-help">Siguiente en espera: <strong>{jobQueue.next_job.job_code}</strong>. Al guardar podrás abrirlo desde Trabajos.</p>}
                <p className="project-help">¿Guardar el trabajo y volver al mapa base para comenzar el siguiente?</p>
                <div className="workspace-action-row">
                  <button className="round-action is-primary" onClick={saveProjectAndContinue} disabled={savingProject}>
                    <Icon name="save" /> {savingProject ? 'Guardando...' : 'Sí, guardar y continuar'}
                  </button>
                  <button className="round-action" onClick={() => setActiveModal(null)} disabled={savingProject}>Seguir revisando</button>
                </div>
              </div>
            )}

            {activeModal === 'delete' && (
              <div className="workspace-modal-content">
                <p className="project-help">Selecciona un proyecto para abrirlo o bórralo. Sus fotos también se borrarán cuando ningún otro proyecto use el dataset.</p>
                <div className="workspace-table-wrap">
                  <table className="workspace-table">
                    <thead><tr><th>Proyecto</th><th>Sensor</th><th>Dataset</th><th>Fotos</th><th>Acción</th></tr></thead>
                    <tbody>
                      {catalog.proyectos.map((project) => {
                        const shared = catalog.proyectos.some((item) => item.id !== project.id && item.dataset_id === project.dataset_id)
                        const blocked = project.id.startsWith('legacy:')
                        const deleteDataset = canDeleteDataset(project)
                        const reason = project.id.startsWith('legacy:') ? 'Proyecto antiguo sin carpeta propia'
                          : project.dataset_id === 'legacy' ? 'El dataset original se conservará'
                            : shared ? 'El dataset se conservará: otro proyecto lo usa' : null
                        return <tr key={project.id}>
                          <td><strong>{project.nombre}</strong></td>
                          <td>{({ mavic_3_rgb: 'Mavic 3 RGB', mavic_3m: 'Mavic 3 MS', rededge_m: 'MicaSense RedEdge-M' })[project.camera_model] || project.camera_model || '-'}</td>
                          <td>{project.dataset_id || '-'}</td>
                          <td>{catalog.datasets.find((item) => item.id === project.dataset_id)?.imagenes_validas ?? 0}</td>
                          <td><div className="workspace-action-row">
                            <button type="button" className="round-action is-primary" onClick={() => selectProject(project.id, true)}
                              disabled={status?.running || Boolean(deletingProjectId)} title={`Abrir ${project.nombre}`}>
                              <Icon name="map" /> {project.ortomosaico_listo ? 'Ver orto' : 'Abrir'}
                            </button>
                            <button type="button" className="round-action is-danger" onClick={() => deleteProjectWithFiles(project, deleteDataset)}
                              disabled={status?.running || Boolean(deletingProjectId) || blocked}
                              title={reason || 'Borrar proyecto y dataset'}>
                              <Icon name="trash" /> {deletingProjectId === project.id ? 'Borrando...' : deleteDataset ? 'Borrar proyecto y fotos' : 'Borrar proyecto'}
                            </button>
                          </div>{reason && <small>{reason}</small>}</td>
                        </tr>
                      })}
                      {!catalog.proyectos.length && <tr><td colSpan="5" className="workspace-empty">Aún no hay proyectos.</td></tr>}
                    </tbody>
                  </table>
                </div>
                <button className="round-action" onClick={refreshCatalog} disabled={Boolean(deletingProjectId)}><Icon name="refresh" /> Actualizar</button>
                <button className="round-action is-primary workspace-primary-action" onClick={returnToBaseMap}
                  disabled={status?.running || savingProject || Boolean(deletingProjectId)}>
                  <Icon name="map" /> {savingProject ? 'Volviendo al mapa...' : 'Volver al mapa base para el siguiente proyecto'}
                </button>
              </div>
            )}

            {activeModal === 'logs' && (
              <div className="workspace-modal-content">
                <div className="workspace-inline-status"><span className="status-dot" />
                  <span>{status?.message || 'Esperando eventos'}</span><strong>{processingPercent}%</strong></div>
                <div className="progress-bar"><div className="progress-fill progress-fill-processing" style={{ width: `${processingPercent}%` }} /></div>
                <pre className="logs-panel workspace-logs">{logs.length ? logs.join('\n') : 'Esperando eventos...'}</pre>
                <button className="round-action" onClick={refreshAll} disabled={refreshing}><Icon name="refresh" /> Actualizar logs</button>
              </div>
            )}
          </section>
        </div>
      )}

      <input ref={zipInputRef} key={zipFileKey} type="file" accept=".zip" hidden onChange={handleZipChange} />
      <input ref={vectorInputRef} key={vectorFileKey} type="file" accept=".zip,.kml,.kmz,.geojson" hidden onChange={handleVectorChange} />
      {notice && <div className={`notice workspace-notice ${notice.kind}`} role="status">{notice.text}</div>}
    </div>
  )
}

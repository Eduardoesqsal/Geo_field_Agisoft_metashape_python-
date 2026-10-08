import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { API_BASE, apiJson } from './utils/api'
import { DEFAULT_CENTER } from './utils/constants'
import { formatAreaHa, formatDate, processingProgress, stateTone } from './utils/formatters'

function MapView({ points, overlay, overlayVisible, finalMode, vectorOverlay, vectorVisible, curvasNivel, contourLabelsVisible, drawingRoi, roiPoints, onRoiPoint }) {
  const mapRef = useRef(null)
  const mapInstanceRef = useRef(null)
  const baseLayerRef = useRef(null)
  const pointsLayerRef = useRef(null)
  const overlayLayerRef = useRef(null)
  const vectorLayerRef = useRef(null)
  const roiDraftLayerRef = useRef(null)
  const curvasLayerRef = useRef(null)
  const hasUserInteractedRef = useRef(false)
  const pointsFocusTokenRef = useRef(null)
  const overlayFocusTokenRef = useRef(null)
  const vectorFocusTokenRef = useRef(null)
  const [curvasGeojson, setCurvasGeojson] = useState(null)
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
      vectorLayerRef.current = null
      roiDraftLayerRef.current = null
      curvasLayerRef.current = null
      hasUserInteractedRef.current = false
      pointsFocusTokenRef.current = null
      overlayFocusTokenRef.current = null
      vectorFocusTokenRef.current = null
      setCurvasGeojson(null)
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
  const overlayReady = Boolean(overlay?.disponible && overlay?.bounds)

  const vectorToken = useMemo(
    () =>
      vectorOverlay?.disponible && vectorOverlay?.bounds
        ? vectorOverlay.cache_buster || vectorOverlay.nombre || 'vector'
        : null,
    [vectorOverlay],
  )
  const curvasToken = useMemo(
    () => (curvasNivel?.disponible ? curvasNivel.cache_buster || 'curvas-5m' : null),
    [curvasNivel],
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
        fillColor: '#f97316',
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
  }, [pointsSignature, overlayReady])

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
    if (!curvasToken) {
      setCurvasGeojson(null)
      return
    }

    let cancelled = false
    apiJson('/overlay/contours.geojson')
      .then((data) => {
        if (!cancelled) setCurvasGeojson(data)
      })
      .catch(() => {
        if (!cancelled) setCurvasGeojson(null)
      })

    return () => {
      cancelled = true
    }
  }, [curvasToken])

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
          color: '#f59e0b',
          weight: 2,
          fillColor: '#f59e0b',
          fillOpacity: 0.18,
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

  useEffect(() => {
    const map = mapInstanceRef.current
    const geojson = curvasGeojson
    if (!map) return

    if (!curvasToken || !geojson || !curvasNivel?.bounds) {
      if (curvasLayerRef.current) {
        map.removeLayer(curvasLayerRef.current)
        curvasLayerRef.current = null
      }
      return
    }

    const curvasLayerToken = `${curvasToken}-${contourLabelsVisible ? 'labels' : 'no-labels'}`
    if (!curvasLayerRef.current || curvasLayerRef.current.__token !== curvasLayerToken) {
      if (curvasLayerRef.current) map.removeLayer(curvasLayerRef.current)
      const layer = L.geoJSON(geojson, {
        style: (feature) => ({
          color: feature?.properties?.es_maestra ? '#2563eb' : '#d946ef',
          weight: feature?.properties?.es_maestra ? 5 : 2.5,
          opacity: feature?.properties?.es_maestra ? 1 : 0.88,
        }),
        onEachFeature(feature, leafletLayer) {
          const props = feature?.properties || {}
          const elevation = props.elevacion_m ?? props.ELEVATION ?? props.Elevation ?? props.elevation ?? props.altitude ?? props.name
          if (contourLabelsVisible && elevation !== undefined && elevation !== null) {
            leafletLayer.bindTooltip(`${elevation} m`, {
              sticky: true,
              permanent: Boolean(props.es_maestra),
              direction: 'center',
              className: 'contour-label',
            })
          }
        },
      })
      layer.__token = curvasLayerToken
      curvasLayerRef.current = layer
    }

    if (!map.hasLayer(curvasLayerRef.current)) curvasLayerRef.current.addTo(map)
    curvasLayerRef.current.bringToFront?.()
  }, [curvasToken, curvasNivel, curvasGeojson, contourLabelsVisible])

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
  const [contourLabelsVisible, setContourLabelsVisible] = useState(true)
  const [vectorOverlay, setVectorOverlay] = useState(null)
  const [curvasNivel, setCurvasNivel] = useState(null)
  const [intervaloCurvaDelgada, setIntervaloCurvaDelgada] = useState(5)
  const [intervaloCurvaMaestra, setIntervaloCurvaMaestra] = useState(25)
  const [panelOpen, setPanelOpen] = useState(window.innerWidth > 760)
  const zipInputRef = useRef(null)
  const vectorInputRef = useRef(null)

  const addRoiPoint = useCallback(([lon, lat]) => {
    setRoiPoints((current) => {
      const ultimo = current[current.length - 1]
      if (ultimo && Math.abs(ultimo[0] - lon) < 1e-8 && Math.abs(ultimo[1] - lat) < 1e-8) return current
      return [...current, [lon, lat]]
    })
  }, [])

  useEffect(() => {
    const check = () => setPanelOpen(window.innerWidth > 760)
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [])

  const finalMode = Boolean(status?.step === 'finalizado' && !status?.running)
  const processingPercent = processingProgress(status)
  const points = ingesta?.puntos_gps || []
  const metrics = useMemo(
    () => [
      { label: 'Estado', value: status?.running ? 'Ejecutando' : status?.step || '-', icon: 'activity' },
      { label: 'Paso', value: status?.step || '-', icon: 'flag' },
      { label: 'Proyecto', value: ingesta?.nombre_proyecto || '-', icon: 'folder' },
      { label: 'Modelo', value: ingesta?.camera_model || '-', icon: 'camera' },
      { label: 'Archivos', value: ingesta?.total_archivos ?? '-', icon: 'database' },
      { label: 'Imagenes validas', value: ingesta?.imagenes_validas ?? '-', icon: 'image' },
      { label: 'Puntos GPS', value: points.length, icon: 'route' },
      { label: 'Orto RGB', value: overlay?.disponible ? 'Listo' : 'No disponible', icon: 'mapPinned' },
      { label: 'Orto MS', value: overlay?.disponible_ms ? 'Listo' : 'No disponible', icon: 'layers' },
      { label: 'Vector', value: vectorOverlay?.disponible ? 'Listo' : 'No disponible', icon: 'map' },
      {
        label: 'Curvas',
        value: curvasNivel?.disponible
          ? `${curvasNivel.intervalo_m} / ${curvasNivel.intervalo_maestra_m} m`
          : 'No disponibles',
        icon: 'chart',
      },
    ],
    [status, ingesta, overlay, vectorOverlay, curvasNivel, points.length],
  )

  const refreshAll = async () => {
    setRefreshing(true)
    try {
      const [statusData, logsData, ingestaData, overlayData, vectorData, curvasData] = await Promise.all([
        apiJson('/status'),
        apiJson('/logs'),
        apiJson('/ingesta/estado'),
        apiJson('/overlay/status'),
        apiJson('/overlay/shapefile/status'),
        apiJson('/overlay/contours/status'),
      ])
      setStatus(statusData)
      setLogs(logsData.logs || [])
      setIngesta(ingestaData)
      setOverlay(overlayData)
      setVectorOverlay(vectorData)
      setCurvasNivel(curvasData)
      setProjectName((current) => current || ingestaData.nombre_proyecto || '')
      setCameraModel((current) => current || ingestaData.camera_model || 'mavic_3_rgb')
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
      setCameraModel(data.camera_model || 'mavic_3_rgb')
      setDriveUrl('')
      setShowDrive(false)
      setUploadProgress(null)
      setDrawingRoi(false)
      setRoiPoints([])
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
    } catch (error) {
      showMessage('error', error.message || 'No se pudo guardar el ROI')
    }
  }

  const removeClip = async () => {
    try {
      const data = await apiJson('/overlay/vector/recorte', { method: 'DELETE' })
      showMessage('success', data.mensaje)
      await refreshAll()
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

  const generateContours = async () => {
    const intervalo_m = Number(intervaloCurvaDelgada)
    const intervalo_maestra_m = Number(intervaloCurvaMaestra)
    if (!Number.isFinite(intervalo_m) || !Number.isFinite(intervalo_maestra_m) || intervalo_m <= 0 || intervalo_maestra_m <= 0) {
      showMessage('error', 'Los intervalos deben ser mayores que cero')
      return
    }
    if (intervalo_maestra_m < intervalo_m || Math.abs(intervalo_maestra_m / intervalo_m - Math.round(intervalo_maestra_m / intervalo_m)) > 1e-6) {
      showMessage('error', 'El intervalo maestro debe ser multiplo del intervalo delgado')
      return
    }
    try {
      const data = await apiJson('/overlay/contours/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ intervalo_m, intervalo_maestra_m }),
      })
      showMessage('success', data.message || 'Generacion de curvas iniciada')
      await refreshAll()
    } catch (error) {
      showMessage('error', error.message || 'No se pudieron generar las curvas')
    }
  }

  const deleteContours = async () => {
    if (!window.confirm('¿Eliminar las curvas de nivel actuales? Podras generarlas nuevamente.')) return

    try {
      const data = await apiJson('/overlay/contours', { method: 'DELETE' })
      setCurvasNivel((current) => ({
        ...(current || {}),
        disponible: false,
        bounds: null,
        cache_buster: null,
        feature_count: null,
      }))
      showMessage('success', data.message || 'Curvas de nivel eliminadas')
      await refreshAll()
    } catch (error) {
      showMessage('error', error.message || 'No se pudieron eliminar las curvas')
    }
  }

  return (
    <div className="app-shell">
      <div className="map-stage">
        <MapView
          points={points}
          overlay={overlay}
          overlayVisible={overlayVisible}
          finalMode={finalMode}
          vectorOverlay={vectorOverlay}
          vectorVisible={vectorVisible}
          curvasNivel={curvasNivel}
          contourLabelsVisible={contourLabelsVisible}
          drawingRoi={drawingRoi}
          roiPoints={roiPoints}
          onRoiPoint={addRoiPoint}
        />
        {drawingRoi && <div className="roi-map-hint">Haz clic en el mapa para marcar el ROI · {roiPoints.length} vertices</div>}
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
              <button
                className="secondary layer-toggle"
                onClick={() => setOverlayVisible((value) => !value)}
                disabled={!overlay?.disponible}
              >
                <Icon name="mapPinned" />
                {overlayVisible ? 'Ocultar ortomosaico' : 'Mostrar ortomosaico'}
              </button>
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
                  <span className="detail-icon"><Icon name="play" /></span>
                  <div className="detail-copy">
                    <dt>Inicio</dt>
                    <dd>{formatDate(status?.started_at)}</dd>
                  </div>
                </div>
                <div>
                  <span className="detail-icon"><Icon name="flag" /></span>
                  <div className="detail-copy">
                    <dt>Fin</dt>
                    <dd>{formatDate(status?.finished_at)}</dd>
                  </div>
                </div>
                <div>
                  <span className="detail-icon"><Icon name="database" /></span>
                  <div className="detail-copy">
                    <dt>Origen</dt>
                    <dd>{ingesta?.origen || '-'}</dd>
                  </div>
                </div>
                <div>
                  <span className="detail-icon"><Icon name="clock" /></span>
                  <div className="detail-copy">
                    <dt>Actualizado</dt>
                    <dd>{formatDate(ingesta?.actualizado_en)}</dd>
                  </div>
                </div>
              </dl>
              <div className="progress-wrap process-progress" aria-live="polite">
                <div className="progress-label">
                  <span>{status?.running ? 'Progreso del procesamiento' : status?.step === 'finalizado' ? 'Procesamiento terminado' : 'Progreso'}</span>
                  <strong>{processingPercent}%</strong>
                </div>
                <div
                  className="progress-bar"
                  role="progressbar"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-valuenow={processingPercent}
                >
                  <div className="progress-fill progress-fill-processing" style={{ width: `${processingPercent}%` }} />
                </div>
                <span className="process-progress-message">{status?.message || 'Esperando accion'}</span>
              </div>
            </section>

            <section className="card glass-card panel-vector-card">
              <div className="section-head">
                <h2>
                  <Icon name="map" />
                  Capa vectorial
                </h2>
                <span>
                  {vectorOverlay?.disponible ? (vectorVisible ? 'Polígono visible' : 'Polígono oculto') : 'Sin capa'}
                </span>
              </div>
              <div className="vector-summary">
                <div>
                  <span className="detail-icon"><Icon name="folder" /></span>
                  <span className="detail-copy">
                    <span>Nombre</span>
                    <strong>{vectorOverlay?.nombre || '-'}</strong>
                  </span>
                </div>
                <div>
                  <span className="detail-icon"><Icon name="layers" /></span>
                  <span className="detail-copy">
                    <span>Formato</span>
                    <strong>{vectorOverlay?.formato || '-'}</strong>
                  </span>
                </div>
                <div>
                  <span className="detail-icon"><Icon name="map" /></span>
                  <span className="detail-copy">
                    <span>Superficie</span>
                    <strong>{formatAreaHa(vectorOverlay?.superficie_ha)}</strong>
                  </span>
                </div>
                <div>
                  <span className="detail-icon"><Icon name="target" /></span>
                  <span className="detail-copy">
                    <span>Poligonos</span>
                    <strong>{vectorOverlay?.feature_count ?? '-'}</strong>
                  </span>
                </div>
              </div>
              <div className="vector-actions">
                <button className="round-action is-accent" onClick={uploadVector} disabled={status?.running || drawingRoi}>
                  <span className="round-action-icon"><Icon name="upload" /></span>
                  <span>Importar capa</span>
                </button>
                {!drawingRoi && (
                  <button className="round-action is-accent" onClick={() => { setRoiPoints([]); setDrawingRoi(true) }} disabled={status?.running}>
                    <span className="round-action-icon"><Icon name="target" /></span>
                    <span>Dibujar ROI</span>
                  </button>
                )}
                {drawingRoi && (
                  <>
                    <button className="round-action is-primary" onClick={saveRoi} disabled={roiPoints.length < 3}>
                      <span className="round-action-icon"><Icon name="save" /></span>
                      <span>Guardar ROI</span>
                    </button>
                    <button className="round-action" onClick={() => setRoiPoints((points) => points.slice(0, -1))} disabled={!roiPoints.length}>
                      <span className="round-action-icon"><Icon name="refresh" /></span>
                      <span>Deshacer punto</span>
                    </button>
                    <button className="round-action" onClick={() => { setDrawingRoi(false); setRoiPoints([]) }}>
                      <span className="round-action-icon"><Icon name="stop" /></span>
                      <span>Cancelar dibujo</span>
                    </button>
                  </>
                )}
                <button
                  className="round-action"
                  onClick={() => setVectorVisible((value) => !value)}
                  disabled={!vectorOverlay?.disponible}
                  aria-pressed={vectorVisible && Boolean(vectorOverlay?.disponible)}
                >
                  <span className="round-action-icon"><Icon name="mapPinned" /></span>
                  <span>{vectorVisible ? 'Ocultar polígono' : 'Mostrar polígono'}</span>
                </button>
                {vectorOverlay?.recorte_activo && (
                  <button className="round-action" onClick={removeClip} disabled={status?.running || drawingRoi}>
                    <span className="round-action-icon"><Icon name="target" /></span>
                    <span>Quitar recorte</span>
                  </button>
                )}
              </div>
              <p className="vector-clip-note">
                {vectorOverlay?.recorte_activo
                  ? 'Al procesar, el ROI recortará los ortomosaicos RGB y multiespectral. Fuera del polígono quedará transparente.'
                  : drawingRoi
                    ? 'Haz clic en el mapa para marcar al menos tres vertices y luego pulsa Guardar ROI.'
                    : 'Importa un KML o dibuja un ROI en el mapa. Sin recorte se exportan los ortomosaicos completos.'}
              </p>
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
                      <span className="metric-icon"><Icon name={item.icon} /></span>
                      <span className="metric-copy">
                        <span>{item.label}</span>
                        <strong>{item.value}</strong>
                      </span>
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
                    <option value="mavic_3_rgb">Mavic 3 RGB</option>
                    <option value="mavic_3m">Mavic 3 Multispectral</option>
                    <option value="rededge_m">MicaSense RedEdge-M</option>
                  </select>
              </label>
            </div>

              <div className="toolbar-grid toolbar-grid-compact">
                <label className="field">
                  <span className="field-label">
                    <Icon name="chart" />
                    Curvas delgadas (m)
                  </span>
                  <input type="number" min="0.1" step="0.1" value={intervaloCurvaDelgada} onChange={(event) => setIntervaloCurvaDelgada(event.target.value)} />
                </label>
                <label className="field">
                  <span className="field-label">
                    <Icon name="chart" />
                    Curvas maestras (m)
                  </span>
                  <input type="number" min="0.1" step="0.1" value={intervaloCurvaMaestra} onChange={(event) => setIntervaloCurvaMaestra(event.target.value)} />
                </label>
              </div>

              <div className="action-row action-row-tight">
                <button className="round-action is-primary" onClick={startProcess} disabled={status?.running || drawingRoi}>
                  <span className="round-action-icon"><Icon name="play" /></span>
                  <span>Procesar</span>
                </button>
                <button
                  className="round-action is-accent"
                  onClick={generateContours}
                  disabled={status?.running}
                  title="Genera curvas delgadas y maestras desde el proyecto Metashape existente"
                >
                  <span className="round-action-icon"><Icon name="chart" /></span>
                  <span>Generar curvas</span>
                </button>
                <button
                  className="round-action"
                  onClick={() => setContourLabelsVisible((value) => !value)}
                  disabled={!curvasNivel?.disponible}
                >
                  <span className="round-action-icon"><Icon name="mapPinned" /></span>
                  <span>{contourLabelsVisible ? 'Ocultar cotas' : 'Mostrar cotas'}</span>
                </button>
                <button
                  className="round-action is-danger"
                  onClick={deleteContours}
                  disabled={status?.running || !curvasNivel?.disponible}
                  title="Elimina las curvas actuales para poder generarlas nuevamente"
                >
                  <span className="round-action-icon"><Icon name="trash" /></span>
                  <span>Eliminar curvas</span>
                </button>
                <button className="round-action" onClick={refreshAll} disabled={refreshing}>
                  <span className="round-action-icon"><Icon name="refresh" /></span>
                  <span>Actualizar</span>
                </button>
                <button className="round-action is-warning" onClick={stopProcess} disabled={!status?.running}>
                  <span className="round-action-icon"><Icon name="stop" /></span>
                  <span>Detener</span>
                </button>
                <button className="round-action is-accent" onClick={uploadZip}>
                  <span className="round-action-icon"><Icon name="upload" /></span>
                  <span>Subir ZIP</span>
                </button>
                <button className="round-action is-accent" onClick={() => setShowDrive((value) => !value)}>
                  <span className="round-action-icon"><Icon name="cloud" /></span>
                  <span>Enlace Drive</span>
                </button>
                <button className="round-action" onClick={saveProjectName}>
                  <span className="round-action-icon"><Icon name="save" /></span>
                  <span>Guardar nombre</span>
                </button>
                <button className="round-action" onClick={newProject} disabled={status?.running}>
                  <span className="round-action-icon"><Icon name="folder" /></span>
                  <span>Nuevo proyecto</span>
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

            <input
              ref={vectorInputRef}
              key={vectorFileKey}
              type="file"
              accept=".zip,.kml,.kmz,.geojson,.json,.shp"
              hidden
              onChange={handleVectorChange}
            />

            {showDrive ? (
              <div className="drive-box drive-box-white">
                <input
                  value={driveUrl}
                  onChange={(event) => setDriveUrl(event.target.value)}
                  placeholder="URL de Drive o ruta rclone: gdrive:MisImagenes/Vuelo01"
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

            {vectorUploadProgress !== null ? (
              <div className="progress-wrap">
                <div className="progress-label">
                  {vectorUploadProgress < 100 ? `Importando capa: ${vectorUploadProgress}%` : 'Capa importada'}
                </div>
                <div className="progress-bar">
                  <div className="progress-fill progress-fill-vector" style={{ width: `${vectorUploadProgress}%` }} />
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

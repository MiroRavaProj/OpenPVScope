import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl, { Map, GeoJSONSource, LngLatLike, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, GeoJsonFc, MapLayerInfo } from "./api";
import type { DetectModality, DetectRunMode } from "./DetectionTools";

const EMPTY_FC: GeoJsonFc = { type: "FeatureCollection", features: [] };

type PlantMode = "detection" | "segmentation";
type Basemap = "osm" | "satellite";

export interface PlantMapProps {
  mode: PlantMode;
  drawEnabled: boolean;
  editCorners: boolean;
  modality: DetectModality;
  showPanels: DetectRunMode;
  refreshKey: number;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onAoiSaved?: () => void;
  onCornersEdited?: () => void;
  onError?: (msg: string) => void;
}

function setGeoJson(map: Map, sourceId: string, data: GeoJsonFc) {
  const src = map.getSource(sourceId) as GeoJSONSource | undefined;
  if (src) src.setData(data as never);
}

function absoluteTileUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${window.location.origin}${path.startsWith("/") ? "" : "/"}${path}`;
}

function draftFeatureCollection(corners: number[][]): GeoJsonFc {
  if (corners.length === 0) return EMPTY_FC;
  if (corners.length < 3) {
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { kind: "draft" },
          geometry: { type: "LineString", coordinates: corners },
        },
      ],
    };
  }
  const ring = [...corners];
  if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) {
    ring.push(ring[0]);
  }
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { kind: "draft" },
        geometry: { type: "Polygon", coordinates: [ring] },
      },
    ],
  };
}

function openRingFromAoi(fc: GeoJsonFc): number[][] | null {
  const feat = fc.features?.[0];
  if (!feat || feat.geometry?.type !== "Polygon") return null;
  const coords = feat.geometry.coordinates as number[][][];
  const ring = coords?.[0];
  if (!ring || ring.length < 4) return null;
  return ring.slice(0, 4).map((p) => [Number(p[0]), Number(p[1])]);
}

function mergePanels(rgb: GeoJsonFc, thermal: GeoJsonFc, mode: DetectRunMode): GeoJsonFc {
  if (mode === "rgb") return rgb;
  if (mode === "thermal") return thermal;
  return {
    type: "FeatureCollection",
    features: [...(rgb.features || []), ...(thermal.features || [])],
  };
}

function addOrthoTileLayers(map: Map, layers: MapLayerInfo[]) {
  for (const layer of layers) {
    const srcId = layer.id;
    try {
      if (map.getLayer(`${srcId}-raster`)) map.removeLayer(`${srcId}-raster`);
      if (map.getSource(srcId)) map.removeSource(srcId);
    } catch {
      /* ignore */
    }
    const b = layer.bounds;
    map.addSource(srcId, {
      type: "raster",
      tiles: [absoluteTileUrl(layer.tile_url)],
      tileSize: layer.tile_size ?? 256,
      maxzoom: Math.max(layer.maxzoom ?? 22, 22),
      minzoom: 0,
      bounds: [b.left, b.bottom, b.right, b.top],
      attribution: "Ortho",
    });
    const before = map.getLayer("aoi-fill") ? "aoi-fill" : undefined;
    map.addLayer(
      {
        id: `${srcId}-raster`,
        type: "raster",
        source: srcId,
        paint: { "raster-opacity": 1, "raster-fade-duration": 0 },
      },
      before,
    );
  }
}

function applyOpacities(map: Map, rgb: number, thermal: number) {
  if (map.getLayer("rgb-raster")) {
    map.setPaintProperty("rgb-raster", "raster-opacity", rgb);
    map.setLayoutProperty("rgb-raster", "visibility", rgb <= 0.01 ? "none" : "visible");
  }
  if (map.getLayer("thermal-raster")) {
    map.setPaintProperty("thermal-raster", "raster-opacity", thermal);
    map.setLayoutProperty("thermal-raster", "visibility", thermal <= 0.01 ? "none" : "visible");
  }
}

function applyBasemap(map: Map, basemap: Basemap) {
  if (map.getLayer("basemap-osm")) {
    map.setLayoutProperty("basemap-osm", "visibility", basemap === "osm" ? "visible" : "none");
  }
  if (map.getLayer("basemap-sat")) {
    map.setLayoutProperty("basemap-sat", "visibility", basemap === "satellite" ? "visible" : "none");
  }
}

export function PlantMap(props: PlantMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const layersRef = useRef<MapLayerInfo[]>([]);
  const readyRef = useRef(false);
  const cornersRef = useRef<number[][]>([]);
  const markersRef = useRef<Marker[]>([]);
  const editMarkersRef = useRef<Marker[]>([]);
  const editRingRef = useRef<number[][]>([]);
  const drawEnabledRef = useRef(props.drawEnabled && props.mode === "detection");
  const editCornersRef = useRef(props.editCorners && props.mode === "detection");
  const modalityRef = useRef(props.modality);
  const [rgbOpacity, setRgbOpacity] = useState(0.92);
  const [thermalOpacity, setThermalOpacity] = useState(0);
  const [basemap, setBasemap] = useState<Basemap>("satellite");
  const [mapReady, setMapReady] = useState(false);
  const [cornerCount, setCornerCount] = useState(0);
  const propsRef = useRef(props);
  propsRef.current = props;
  const opacityRef = useRef({ rgb: 0.92, thermal: 0 });
  opacityRef.current = { rgb: rgbOpacity, thermal: thermalOpacity };
  drawEnabledRef.current = props.drawEnabled && props.mode === "detection";
  editCornersRef.current = props.editCorners && props.mode === "detection";
  modalityRef.current = props.modality;

  const clearDraft = useCallback((map?: Map | null) => {
    cornersRef.current = [];
    setCornerCount(0);
    for (const m of markersRef.current) m.remove();
    markersRef.current = [];
    const m = map ?? mapRef.current;
    if (m?.getSource("aoi-draft")) {
      setGeoJson(m, "aoi-draft", EMPTY_FC);
    }
  }, []);

  const clearEditMarkers = useCallback(() => {
    for (const m of editMarkersRef.current) m.remove();
    editMarkersRef.current = [];
    editRingRef.current = [];
  }, []);

  const loadVectors = useCallback(async (map: Map) => {
    const modality = propsRef.current.modality;
    const showPanels = propsRef.current.showPanels;
    try {
      const [aoi, grid, panelsRgb, panelsTh, pairs] = await Promise.all([
        api.detectionGeojson("aoi", modality),
        api.detectionGeojson("grid", modality),
        api.detectionGeojson("panels", "rgb"),
        api.detectionGeojson("panels", "thermal"),
        api.segmentationPairsGeojson(),
      ]);
      setGeoJson(map, "aoi", aoi);
      setGeoJson(map, "grid", grid);
      setGeoJson(map, "panels", mergePanels(panelsRgb, panelsTh, showPanels));
      setGeoJson(map, "pairs", pairs);
      return aoi;
    } catch (e) {
      console.error(e);
      return null;
    }
  }, []);

  const finishAoi = useCallback(
    async (map: Map, ring: number[][]) => {
      try {
        await api.putAoi(
          ring.map((p) => [p[0], p[1]]),
          { modality: modalityRef.current },
        );
        clearDraft(map);
        await loadVectors(map);
        propsRef.current.onAoiSaved?.();
      } catch (err) {
        propsRef.current.onError?.(String(err));
        clearDraft(map);
      }
    },
    [clearDraft, loadVectors],
  );

  const commitEditedCorners = useCallback(
    async (map: Map, ring: number[][]) => {
      try {
        await api.putAoi(
          ring.map((p) => [p[0], p[1]]),
          { modality: modalityRef.current, regenerate_grid: true },
        );
        setGeoJson(map, "aoi-draft", EMPTY_FC);
        await loadVectors(map);
        propsRef.current.onCornersEdited?.();
      } catch (err) {
        propsRef.current.onError?.(String(err));
      }
    },
    [loadVectors],
  );

  const syncEditMarkers = useCallback(
    async (map: Map) => {
      clearEditMarkers();
      if (!editCornersRef.current) return;
      const aoi = await api.detectionGeojson("aoi", modalityRef.current);
      const ring = openRingFromAoi(aoi);
      if (!ring) return;
      editRingRef.current = ring.map((p) => [...p]);
      setGeoJson(map, "aoi-draft", draftFeatureCollection(editRingRef.current));

      ring.forEach((pt, idx) => {
        const el = document.createElement("div");
        el.className = "aoi-corner-marker aoi-corner-edit";
        el.textContent = String(idx + 1);
        const marker = new maplibregl.Marker({ element: el, draggable: true })
          .setLngLat(pt as LngLatLike)
          .addTo(map);
        marker.on("drag", () => {
          const ll = marker.getLngLat();
          editRingRef.current[idx] = [ll.lng, ll.lat];
          setGeoJson(map, "aoi-draft", draftFeatureCollection(editRingRef.current));
        });
        marker.on("dragend", () => {
          const ll = marker.getLngLat();
          editRingRef.current[idx] = [ll.lng, ll.lat];
          void commitEditedCorners(
            map,
            editRingRef.current.map((p) => [...p]),
          );
        });
        editMarkersRef.current.push(marker);
      });
    },
    [clearEditMarkers, commitEditedCorners],
  );

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          "basemap-osm": {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap",
            maxzoom: 19,
          },
          "basemap-sat": {
            type: "raster",
            tiles: [
              "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            ],
            tileSize: 256,
            attribution: "© Esri",
            maxzoom: 19,
          },
        },
        layers: [
          { id: "basemap-osm", type: "raster", source: "basemap-osm", layout: { visibility: "none" } },
          { id: "basemap-sat", type: "raster", source: "basemap-sat", layout: { visibility: "visible" } },
        ],
      },
      center: [12.5, 42.0],
      zoom: 5,
      maxZoom: 24,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;

    const onMapClick = (ev: maplibregl.MapMouseEvent) => {
      if (editCornersRef.current) return;
      if (!drawEnabledRef.current) {
        const feats = map.queryRenderedFeatures(ev.point, {
          layers: ["panels-fill", "pairs-line"].filter((lid) => map.getLayer(lid)),
        });
        if (!feats.length) propsRef.current.onSelect(null);
        return;
      }

      ev.preventDefault();
      const pt: number[] = [ev.lngLat.lng, ev.lngLat.lat];
      const next = [...cornersRef.current, pt];
      cornersRef.current = next;
      setCornerCount(next.length);

      const el = document.createElement("div");
      el.className = "aoi-corner-marker";
      el.textContent = String(next.length);
      markersRef.current.push(new maplibregl.Marker({ element: el }).setLngLat(pt as LngLatLike).addTo(map));

      setGeoJson(map, "aoi-draft", draftFeatureCollection(next));

      if (next.length >= 4) {
        void finishAoi(map, next.slice(0, 4));
      }
    };

    map.on("load", async () => {
      map.addSource("aoi-draft", { type: "geojson", data: EMPTY_FC as never });
      for (const id of ["aoi", "grid", "panels", "pairs"] as const) {
        map.addSource(id, { type: "geojson", data: EMPTY_FC as never });
      }

      map.addLayer({
        id: "aoi-draft-fill",
        type: "fill",
        source: "aoi-draft",
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": "#e6a23c", "fill-opacity": 0.45 },
      });
      map.addLayer({
        id: "aoi-draft-line",
        type: "line",
        source: "aoi-draft",
        paint: { "line-color": "#e6a23c", "line-width": 3 },
      });

      map.addLayer({
        id: "aoi-fill",
        type: "fill",
        source: "aoi",
        paint: { "fill-color": "#e6a23c", "fill-opacity": 0.28 },
      });
      map.addLayer({
        id: "aoi-line",
        type: "line",
        source: "aoi",
        paint: { "line-color": "#e6a23c", "line-width": 2.5 },
      });
      map.addLayer({
        id: "grid-line",
        type: "line",
        source: "grid",
        paint: { "line-color": "#5b9fd4", "line-width": 1.25, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "panels-fill",
        type: "fill",
        source: "panels",
        paint: {
          "fill-color": [
            "case",
            ["==", ["get", "id"], propsRef.current.selectedId ?? ""],
            "#e6a23c",
            [
              "case",
              ["==", ["get", "modality"], "thermal"],
              "#c45c26",
              "#5b9fd4",
            ],
          ],
          "fill-opacity": 0.35,
        },
      });
      map.addLayer({
        id: "panels-line",
        type: "line",
        source: "panels",
        paint: {
          "line-color": [
            "case",
            ["==", ["get", "modality"], "thermal"],
            "#e89a6a",
            "#8ec8f0",
          ],
          "line-width": 1.5,
        },
      });
      map.addLayer({
        id: "pairs-line",
        type: "line",
        source: "pairs",
        paint: { "line-color": "#ff7a59", "line-width": 2, "line-opacity": 0.9 },
      });

      try {
        const { layers } = await api.mapLayers();
        layersRef.current = layers;
        addOrthoTileLayers(map, layers);
        applyOpacities(map, opacityRef.current.rgb, opacityRef.current.thermal);
        if (layers[0]) {
          const f = layers[0].bounds;
          map.fitBounds(
            [
              [f.left, f.bottom],
              [f.right, f.top],
            ],
            { padding: 48, maxZoom: 20 },
          );
        }
      } catch (e) {
        console.error(e);
        propsRef.current.onError?.(`Map layers failed: ${String(e)}`);
      }

      await loadVectors(map);
      readyRef.current = true;
      setMapReady(true);

      map.on("click", "panels-fill", (ev) => {
        if (drawEnabledRef.current || editCornersRef.current) return;
        const f = ev.features?.[0];
        propsRef.current.onSelect(String(f?.properties?.id ?? "") || null);
      });
      map.on("click", "pairs-line", (ev) => {
        if (drawEnabledRef.current || editCornersRef.current) return;
        const f = ev.features?.[0];
        propsRef.current.onSelect(String(f?.properties?.id ?? "") || null);
      });
    });

    map.on("click", onMapClick);

    return () => {
      readyRef.current = false;
      setMapReady(false);
      clearDraft(map);
      clearEditMarkers();
      map.off("click", onMapClick);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    applyBasemap(map, basemap);
  }, [basemap, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    applyOpacities(map, rgbOpacity, thermalOpacity);
  }, [rgbOpacity, thermalOpacity, mapReady]);

  useEffect(() => {
    if (props.mode !== "detection") return;
    if (props.modality === "thermal") {
      setRgbOpacity(0);
      setThermalOpacity(1);
    } else {
      setRgbOpacity(1);
      setThermalOpacity(0);
    }
  }, [props.modality, props.mode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    void (async () => {
      try {
        const { layers } = await api.mapLayers();
        layersRef.current = layers;
        addOrthoTileLayers(map, layers);
        applyOpacities(map, opacityRef.current.rgb, opacityRef.current.thermal);
      } catch {
        /* ignore */
      }
      await loadVectors(map);
      if (editCornersRef.current) {
        await syncEditMarkers(map);
      }
    })();
  }, [props.refreshKey, props.modality, props.showPanels, loadVectors, mapReady, syncEditMarkers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!(props.drawEnabled && props.mode === "detection")) {
      clearDraft(map);
      if (!props.editCorners) map.getCanvas().style.cursor = "";
    } else {
      map.getCanvas().style.cursor = "crosshair";
    }
  }, [props.drawEnabled, props.mode, props.editCorners, clearDraft]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    if (props.editCorners && props.mode === "detection") {
      map.getCanvas().style.cursor = "move";
      void syncEditMarkers(map);
    } else {
      clearEditMarkers();
      if (map.getSource("aoi-draft") && !props.drawEnabled) {
        setGeoJson(map, "aoi-draft", EMPTY_FC);
      }
      if (!props.drawEnabled) map.getCanvas().style.cursor = "";
    }
  }, [
    props.editCorners,
    props.mode,
    props.modality,
    props.drawEnabled,
    mapReady,
    syncEditMarkers,
    clearEditMarkers,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer("panels-fill")) return;
    map.setPaintProperty("panels-fill", "fill-color", [
      "case",
      ["==", ["get", "id"], props.selectedId ?? ""],
      "#e6a23c",
      ["case", ["==", ["get", "modality"], "thermal"], "#c45c26", "#5b9fd4"],
    ]);
  }, [props.selectedId]);

  return (
    <div className="plant-map-root">
      <div className="plant-map-canvas" ref={containerRef} />
      {props.drawEnabled && props.mode === "detection" && (
        <div className="aoi-draw-hint">
          Click 4 corners of the panel block ({cornerCount}/4) — saves on the 4th click (
          {props.modality.toUpperCase()})
        </div>
      )}
      {props.editCorners && props.mode === "detection" && (
        <div className="aoi-draw-hint">
          Drag the 4 frame corners ({props.modality.toUpperCase()}) — grid regenerates on release
        </div>
      )}
      <div className="layer-dock">
        <div className="layer-dock-title">Layers</div>
        <div className="basemap-toggle" role="group" aria-label="Basemap">
          <button
            type="button"
            className={basemap === "osm" ? "active" : ""}
            onClick={() => setBasemap("osm")}
          >
            Streets
          </button>
          <button
            type="button"
            className={basemap === "satellite" ? "active" : ""}
            onClick={() => setBasemap("satellite")}
          >
            Satellite
          </button>
        </div>
        <label className="layer-row">
          <span>RGB {Math.round(rgbOpacity * 100)}%</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={rgbOpacity}
            onChange={(e) => setRgbOpacity(Number(e.target.value))}
          />
        </label>
        <label className="layer-row">
          <span>Thermal {Math.round(thermalOpacity * 100)}%</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={thermalOpacity}
            onChange={(e) => setThermalOpacity(Number(e.target.value))}
          />
        </label>
        <button
          type="button"
          className="ghost"
          onClick={() => {
            const map = mapRef.current;
            const layers = layersRef.current;
            if (!map || !layers[0]) return;
            const f = layers[0].bounds;
            map.fitBounds(
              [
                [f.left, f.bottom],
                [f.right, f.top],
              ],
              { padding: 48, maxZoom: 20 },
            );
          }}
        >
          Fit bounds
        </button>
      </div>
    </div>
  );
}

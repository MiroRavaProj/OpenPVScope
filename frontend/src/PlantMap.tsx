import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import maplibregl, { Map, GeoJSONSource, LngLatLike, Marker, MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, GeoJsonFc, MapLayerInfo } from "./api";
import type { DetectModality, DetectRunMode } from "./DetectionTools";
import type { Basemap } from "./LayerDock";
import { useT } from "./i18n";

type TFn = ReturnType<typeof useT>;

const EMPTY_FC: GeoJsonFc = { type: "FeatureCollection", features: [] };

type PlantMode = "detection" | "segmentation";

export interface PlantMapProps {
  mode: PlantMode;
  drawEnabled: boolean;
  editCorners: boolean;
  modality: DetectModality;
  showPanels: DetectRunMode;
  displayConfidenceRgb?: number;
  displayConfidenceThermal?: number;
  refreshKey: number;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onAoiSaved?: () => void;
  onCornersEdited?: () => void;
  onError?: (msg: string) => void;
  /** Pre-colorized pairs GeoJSON (segmentation). */
  pairsGeojson?: GeoJsonFc | null;
  thermalColoring?: boolean;
  rgbOpacity: number;
  thermalOpacity: number;
  basemap: Basemap;
  onRgbOpacityChange: (v: number) => void;
  onThermalOpacityChange: (v: number) => void;
  /** Optional ref filled with fitBounds() for the Layers dock. */
  fitBoundsRef?: MutableRefObject<(() => void) | null>;
}

type TooltipState = {
  x: number;
  y: number;
  html: string;
} | null;

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

function mergePanels(
  rgb: GeoJsonFc,
  thermal: GeoJsonFc,
  mode: DetectRunMode,
  minConfidenceRgb: number,
  minConfidenceThermal: number,
): GeoJsonFc {
  const filterFeats = (fc: GeoJsonFc, minConfidence: number) =>
    (fc.features || []).filter((f) => {
      const c = Number(f.properties?.confidence ?? 0);
      return minConfidence <= 0 || c >= minConfidence;
    });
  if (mode === "rgb") {
    return { type: "FeatureCollection", features: filterFeats(rgb, minConfidenceRgb) };
  }
  if (mode === "thermal") {
    return { type: "FeatureCollection", features: filterFeats(thermal, minConfidenceThermal) };
  }
  return {
    type: "FeatureCollection",
    features: [
      ...filterFeats(rgb, minConfidenceRgb),
      ...filterFeats(thermal, minConfidenceThermal),
    ],
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

function setLayerVis(map: Map, id: string, visible: boolean) {
  if (!map.getLayer(id)) return;
  map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
}

function applyModeVisibility(map: Map, mode: PlantMode) {
  const det = mode === "detection";
  for (const id of ["aoi-fill", "aoi-line", "grid-line", "panels-fill", "panels-line"]) {
    setLayerVis(map, id, det);
  }
  for (const id of ["pairs-fill", "pairs-line"]) {
    setLayerVis(map, id, !det);
  }
}

function fmtNum(v: unknown, digits = 2): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

function pairTooltipHtml(t: TFn, p: Record<string, unknown>, lng: number, lat: number): string {
  const idRaw = String(p.id ?? "");
  const id = `${idRaw.slice(0, 10)}${idRaw.length > 10 ? "…" : ""}`;
  return `
    <div class="tip-title">${t("map.tipPairTitle", { id })}</div>
    <div>${t("map.tipIouConfDist", { iou: fmtNum(p.iou), conf: fmtNum(p.confidence), dist: fmtNum(p.distance_m) })}</div>
    <div>${t("map.tipRgbThermalIds", { rgb: String(p.rgb_id ?? "—"), thermal: String(p.thermal_id ?? "—") })}</div>
    <div>${t("map.tipMinMax", { min: fmtNum(p.min_temperature), max: fmtNum(p.max_temperature) })}</div>
    <div>${t("map.tipMeanMed", { mean: fmtNum(p.mean_temperature), med: fmtNum(p.median_temperature) })}</div>
    <div>${t("map.tipStdVar", { std: fmtNum(p.std_temperature), var: fmtNum(p.var_temperature) })}</div>
    ${p.valid_pixels != null ? `<div>${t("map.tipPixels", { n: String(p.valid_pixels) })}</div>` : ""}
    <div class="tip-coords">${lat.toFixed(6)}, ${lng.toFixed(6)}</div>
    <div class="tip-hint">${t("map.tipClickImages")}</div>
  `;
}

function panelTooltipHtml(t: TFn, p: Record<string, unknown>, lng: number, lat: number): string {
  const idRaw = String(p.id ?? "");
  const id = `${idRaw.slice(0, 10)}${idRaw.length > 10 ? "…" : ""}`;
  const mod = String(p.modality ?? "rgb").toUpperCase();
  return `
    <div class="tip-title">${t("map.tipPanelTitle", { id })}</div>
    <div>${t("map.tipModality", { modality: mod })}</div>
    <div>${t("map.tipConfidence", { value: fmtNum(p.confidence) })}</div>
    <div class="tip-coords">${lat.toFixed(6)}, ${lng.toFixed(6)}</div>
  `;
}

export function PlantMap(props: PlantMapProps) {
  const t = useT();
  const tRef = useRef(t);
  tRef.current = t;
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
  const modeRef = useRef(props.mode);
  const [mapReady, setMapReady] = useState(false);
  const [cornerCount, setCornerCount] = useState(0);
  const [tooltip, setTooltip] = useState<TooltipState>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const opacityRef = useRef({ rgb: props.rgbOpacity, thermal: props.thermalOpacity });
  opacityRef.current = { rgb: props.rgbOpacity, thermal: props.thermalOpacity };
  drawEnabledRef.current = props.drawEnabled && props.mode === "detection";
  editCornersRef.current = props.editCorners && props.mode === "detection";
  modalityRef.current = props.modality;
  modeRef.current = props.mode;

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
    const minConfRgb = propsRef.current.displayConfidenceRgb ?? 0;
    const minConfThermal = propsRef.current.displayConfidenceThermal ?? 0;
    const mode = propsRef.current.mode;
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
      if (mode === "segmentation") {
        setGeoJson(map, "panels", EMPTY_FC);
        const override = propsRef.current.pairsGeojson;
        setGeoJson(map, "pairs", override ?? pairs);
      } else {
        setGeoJson(
          map,
          "panels",
          mergePanels(panelsRgb, panelsTh, showPanels, minConfRgb, minConfThermal),
        );
        setGeoJson(map, "pairs", EMPTY_FC);
      }
      applyModeVisibility(map, mode);
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
        const layers = (
          modeRef.current === "segmentation"
            ? ["pairs-fill", "pairs-line"]
            : ["panels-fill"]
        ).filter((lid) => map.getLayer(lid));
        const feats = map.queryRenderedFeatures(ev.point, { layers });
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
            ["case", ["==", ["get", "modality"], "thermal"], "#c45c26", "#5b9fd4"],
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
        id: "pairs-fill",
        type: "fill",
        source: "pairs",
        paint: {
          "fill-color": [
            "case",
            ["==", ["get", "id"], propsRef.current.selectedId ?? ""],
            "#ffffff",
            ["coalesce", ["get", "fill_color"], "#808080"],
          ],
          "fill-opacity": [
            "case",
            ["==", ["get", "id"], propsRef.current.selectedId ?? ""],
            0.55,
            0.8,
          ],
        },
      });
      map.addLayer({
        id: "pairs-line",
        type: "line",
        source: "pairs",
        paint: {
          "line-color": [
            "case",
            ["==", ["get", "id"], propsRef.current.selectedId ?? ""],
            "#ffffff",
            "#222222",
          ],
          "line-width": [
            "case",
            ["==", ["get", "id"], propsRef.current.selectedId ?? ""],
            2.5,
            0.8,
          ],
          "line-opacity": 0.95,
        },
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
        propsRef.current.onError?.(tRef.current("map.layersFailed", { error: String(e) }));
      }

      await loadVectors(map);
      readyRef.current = true;
      setMapReady(true);

      const onPanelClick = (ev: MapLayerMouseEvent) => {
        if (drawEnabledRef.current || editCornersRef.current) return;
        const f = ev.features?.[0];
        propsRef.current.onSelect(String(f?.properties?.id ?? "") || null);
      };
      map.on("click", "panels-fill", onPanelClick);
      map.on("click", "pairs-fill", onPanelClick);
      map.on("click", "pairs-line", onPanelClick);

      const hoverLayers = ["panels-fill", "pairs-fill"];
      for (const lid of hoverLayers) {
        map.on("mousemove", lid, (ev) => {
          const f = ev.features?.[0];
          if (!f?.properties) {
            setTooltip(null);
            return;
          }
          map.getCanvas().style.cursor = "pointer";
          const p = f.properties as Record<string, unknown>;
          const html =
            lid === "pairs-fill"
              ? pairTooltipHtml(tRef.current, p, ev.lngLat.lng, ev.lngLat.lat)
              : panelTooltipHtml(tRef.current, p, ev.lngLat.lng, ev.lngLat.lat);
          setTooltip({ x: ev.point.x + 14, y: ev.point.y + 14, html });
        });
        map.on("mouseleave", lid, () => {
          setTooltip(null);
          if (!drawEnabledRef.current && !editCornersRef.current) {
            map.getCanvas().style.cursor = "";
          }
        });
      }
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
    applyBasemap(map, props.basemap);
  }, [props.basemap, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    applyOpacities(map, props.rgbOpacity, props.thermalOpacity);
  }, [props.rgbOpacity, props.thermalOpacity, mapReady]);

  useEffect(() => {
    if (props.mode === "segmentation") {
      props.onRgbOpacityChange(0.7);
      props.onThermalOpacityChange(0.5);
      return;
    }
    if (props.modality === "thermal") {
      props.onRgbOpacityChange(0);
      props.onThermalOpacityChange(1);
    } else {
      props.onRgbOpacityChange(1);
      props.onThermalOpacityChange(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.modality, props.mode]);

  useEffect(() => {
    if (!props.fitBoundsRef) return;
    props.fitBoundsRef.current = () => {
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
    };
    return () => {
      if (props.fitBoundsRef) props.fitBoundsRef.current = null;
    };
  }, [props.fitBoundsRef, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    applyModeVisibility(map, props.mode);
  }, [props.mode, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    if (props.mode === "segmentation" && props.pairsGeojson) {
      setGeoJson(map, "pairs", props.pairsGeojson);
      return;
    }
  }, [props.pairsGeojson, props.mode, mapReady]);

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
  }, [
    props.refreshKey,
    props.modality,
    props.showPanels,
    props.displayConfidenceRgb,
    props.displayConfidenceThermal,
    props.mode,
    loadVectors,
    mapReady,
    syncEditMarkers,
  ]);

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
    if (!map) return;
    const sid = props.selectedId ?? "";
    if (map.getLayer("panels-fill")) {
      map.setPaintProperty("panels-fill", "fill-color", [
        "case",
        ["==", ["get", "id"], sid],
        "#e6a23c",
        ["case", ["==", ["get", "modality"], "thermal"], "#c45c26", "#5b9fd4"],
      ]);
    }
    if (map.getLayer("pairs-fill")) {
      map.setPaintProperty("pairs-fill", "fill-color", [
        "case",
        ["==", ["get", "id"], sid],
        "#ffffff",
        ["coalesce", ["get", "fill_color"], "#808080"],
      ]);
      map.setPaintProperty("pairs-fill", "fill-opacity", [
        "case",
        ["==", ["get", "id"], sid],
        0.55,
        props.thermalColoring ? 0.8 : 0.4,
      ]);
    }
    if (map.getLayer("pairs-line")) {
      map.setPaintProperty("pairs-line", "line-color", [
        "case",
        ["==", ["get", "id"], sid],
        "#ffffff",
        "#222222",
      ]);
      map.setPaintProperty("pairs-line", "line-width", [
        "case",
        ["==", ["get", "id"], sid],
        2.5,
        0.8,
      ]);
    }
  }, [props.selectedId, props.thermalColoring]);

  return (
    <div className="plant-map-root">
      <div className="plant-map-canvas" ref={containerRef} />
      {tooltip && (
        <div
          className="map-panel-tooltip"
          style={{ left: tooltip.x, top: tooltip.y }}
          dangerouslySetInnerHTML={{ __html: tooltip.html }}
        />
      )}
      {props.drawEnabled && props.mode === "detection" && (
        <div className="aoi-draw-hint">
          {t("map.drawHint", { count: cornerCount, modality: props.modality.toUpperCase() })}
        </div>
      )}
      {props.editCorners && props.mode === "detection" && (
        <div className="aoi-draw-hint">
          {t("map.editHint", { modality: props.modality.toUpperCase() })}
        </div>
      )}
    </div>
  );
}

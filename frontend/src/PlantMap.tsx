import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl, { Map, GeoJSONSource } from "maplibre-gl";
import MapboxDraw from "@mapbox/mapbox-gl-draw";
import "maplibre-gl/dist/maplibre-gl.css";
import "@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css";
import { api, GeoJsonFc, MapLayerInfo } from "./api";

const EMPTY_FC: GeoJsonFc = { type: "FeatureCollection", features: [] };

type PlantMode = "detection" | "segmentation";
type Basemap = "osm" | "satellite";

export interface PlantMapProps {
  mode: PlantMode;
  drawEnabled: boolean;
  refreshKey: number;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onAoiSaved?: () => void;
  onError?: (msg: string) => void;
}

function setGeoJson(map: Map, sourceId: string, data: GeoJsonFc) {
  const src = map.getSource(sourceId) as GeoJSONSource | undefined;
  if (src) src.setData(data as never);
}

function openRing(coords: number[][]): number[][] {
  if (coords.length < 2) return coords;
  const a = coords[0];
  const b = coords[coords.length - 1];
  if (a[0] === b[0] && a[1] === b[1]) return coords.slice(0, -1);
  return coords;
}

function absoluteTileUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${window.location.origin}${path.startsWith("/") ? "" : "/"}${path}`;
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
        paint: {
          "raster-opacity": 1,
          "raster-fade-duration": 0,
        },
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
  const drawRef = useRef<MapboxDraw | null>(null);
  const layersRef = useRef<MapLayerInfo[]>([]);
  const readyRef = useRef(false);
  const [rgbOpacity, setRgbOpacity] = useState(0.92);
  const [thermalOpacity, setThermalOpacity] = useState(0);
  const [basemap, setBasemap] = useState<Basemap>("satellite");
  const [mapReady, setMapReady] = useState(false);
  const propsRef = useRef(props);
  propsRef.current = props;
  const opacityRef = useRef({ rgb: 0.92, thermal: 0 });
  opacityRef.current = { rgb: rgbOpacity, thermal: thermalOpacity };

  const loadVectors = useCallback(async (map: Map) => {
    try {
      const [aoi, grid, panels, pairs] = await Promise.all([
        api.detectionGeojson("aoi"),
        api.detectionGeojson("grid"),
        api.detectionGeojson("panels"),
        api.segmentationPairsGeojson(),
      ]);
      setGeoJson(map, "aoi", aoi);
      setGeoJson(map, "grid", grid);
      setGeoJson(map, "panels", panels);
      setGeoJson(map, "pairs", pairs);
    } catch (e) {
      console.error(e);
    }
  }, []);

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
          {
            id: "basemap-osm",
            type: "raster",
            source: "basemap-osm",
            layout: { visibility: "none" },
          },
          {
            id: "basemap-sat",
            type: "raster",
            source: "basemap-sat",
            layout: { visibility: "visible" },
          },
        ],
      },
      center: [12.5, 42.0],
      zoom: 5,
      maxZoom: 24,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;

    const draw = new MapboxDraw({
      displayControlsDefault: false,
      controls: {},
      defaultMode: "simple_select",
    });
    drawRef.current = draw;

    const onDrawCreate = async (e: {
      features: Array<{ geometry: { type: string; coordinates: number[][][] } }>;
    }) => {
      const feat = e.features[0];
      if (!feat || feat.geometry.type !== "Polygon") return;
      const ring = openRing(feat.geometry.coordinates[0] as number[][]);
      if (ring.length !== 4) {
        propsRef.current.onError?.(
          "AOI needs exactly 4 corners — click four points then double-click to finish",
        );
        draw.deleteAll();
        return;
      }
      try {
        await api.putAoi(ring.map((p) => [p[0], p[1]]));
        draw.deleteAll();
        await loadVectors(map);
        propsRef.current.onAoiSaved?.();
      } catch (err) {
        propsRef.current.onError?.(String(err));
        draw.deleteAll();
      }
    };

    map.on("load", async () => {
      for (const id of ["aoi", "grid", "panels", "pairs"] as const) {
        map.addSource(id, { type: "geojson", data: EMPTY_FC as never });
      }
      map.addLayer({
        id: "aoi-fill",
        type: "fill",
        source: "aoi",
        paint: { "fill-color": "#3ecf8e", "fill-opacity": 0.12 },
      });
      map.addLayer({
        id: "aoi-line",
        type: "line",
        source: "aoi",
        paint: { "line-color": "#3ecf8e", "line-width": 2 },
      });
      map.addLayer({
        id: "grid-line",
        type: "line",
        source: "grid",
        paint: { "line-color": "#5b9fd4", "line-width": 1, "line-opacity": 0.85 },
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
            "#5b9fd4",
          ],
          "fill-opacity": 0.35,
        },
      });
      map.addLayer({
        id: "panels-line",
        type: "line",
        source: "panels",
        paint: { "line-color": "#8ec8f0", "line-width": 1.5 },
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
        const f = ev.features?.[0];
        const id = String(f?.properties?.id ?? "");
        propsRef.current.onSelect(id || null);
      });
      map.on("click", "pairs-line", (ev) => {
        const f = ev.features?.[0];
        const id = String(f?.properties?.id ?? "");
        propsRef.current.onSelect(id || null);
      });
      map.on("click", (ev) => {
        const feats = map.queryRenderedFeatures(ev.point, {
          layers: ["panels-fill", "pairs-line"].filter((lid) => map.getLayer(lid)),
        });
        if (!feats.length) propsRef.current.onSelect(null);
      });
      map.on("mouseenter", "panels-fill", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "panels-fill", () => {
        map.getCanvas().style.cursor = "";
      });
    });

    map.on("draw.create", onDrawCreate as never);

    return () => {
      readyRef.current = false;
      setMapReady(false);
      map.off("draw.create", onDrawCreate as never);
      map.remove();
      mapRef.current = null;
      drawRef.current = null;
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
    })();
  }, [props.refreshKey, loadVectors, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    const draw = drawRef.current;
    if (!map || !draw) return;

    const hasDraw = map.hasControl(draw as unknown as maplibregl.IControl);
    if (props.drawEnabled && props.mode === "detection") {
      if (!hasDraw) map.addControl(draw as unknown as maplibregl.IControl, "top-left");
      draw.changeMode("draw_polygon");
    } else if (hasDraw) {
      try {
        draw.deleteAll();
      } catch {
        /* ignore */
      }
      map.removeControl(draw as unknown as maplibregl.IControl);
    }
  }, [props.drawEnabled, props.mode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer("panels-fill")) return;
    map.setPaintProperty("panels-fill", "fill-color", [
      "case",
      ["==", ["get", "id"], props.selectedId ?? ""],
      "#e6a23c",
      "#5b9fd4",
    ]);
  }, [props.selectedId]);

  return (
    <div className="plant-map-root">
      <div className="plant-map-canvas" ref={containerRef} />
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

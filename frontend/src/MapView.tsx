import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api } from "./api";

type Basemap = "osm" | "satellite";

function absoluteTileUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${window.location.origin}${path.startsWith("/") ? "" : "/"}${path}`;
}

/** Lightweight plant overview (photogrammetry step) — full-res XYZ orthos. */
export function MapView() {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [basemap, setBasemap] = useState<Basemap>("satellite");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    const map = new maplibregl.Map({
      container: ref.current,
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

    let cancelled = false;

    map.on("load", async () => {
      try {
        const { layers } = await api.mapLayers();
        if (cancelled || !layers.length) return;
        for (const layer of layers) {
          if (map.getSource(layer.id)) continue;
          const b = layer.bounds;
          map.addSource(layer.id, {
            type: "raster",
            tiles: [absoluteTileUrl(layer.tile_url)],
            tileSize: layer.tile_size ?? 256,
            maxzoom: Math.max(layer.maxzoom ?? 22, 22),
            bounds: [b.left, b.bottom, b.right, b.top],
          });
          map.addLayer({
            id: `${layer.id}-layer`,
            type: "raster",
            source: layer.id,
            paint: {
              "raster-opacity": layer.id === "thermal" ? 0.65 : 0.9,
              "raster-fade-duration": 0,
            },
          });
        }
        const first = layers[0].bounds;
        map.fitBounds(
          [
            [first.left, first.bottom],
            [first.right, first.top],
          ],
          { padding: 40, maxZoom: 20 },
        );
      } catch (e) {
        console.error(e);
      }
      if (!cancelled) setReady(true);
    });

    return () => {
      cancelled = true;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (map.getLayer("basemap-osm")) {
      map.setLayoutProperty("basemap-osm", "visibility", basemap === "osm" ? "visible" : "none");
    }
    if (map.getLayer("basemap-sat")) {
      map.setLayoutProperty("basemap-sat", "visibility", basemap === "satellite" ? "visible" : "none");
    }
  }, [basemap, ready]);

  return (
    <div className="map-wrap map-wrap-with-dock">
      <div ref={ref} style={{ width: "100%", height: "100%" }} />
      <div className="layer-dock mapview-dock">
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
      </div>
    </div>
  );
}

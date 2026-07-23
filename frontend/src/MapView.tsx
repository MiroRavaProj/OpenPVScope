import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api } from "./api";

export function MapView() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const map = new maplibregl.Map({
      container: ref.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap",
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
      center: [12.5, 42.0],
      zoom: 5,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    let cancelled = false;

    const addLayers = async () => {
      try {
        const { layers } = await api.mapLayers();
        if (cancelled || !layers.length) return;
        for (const layer of layers) {
          if (map.getSource(layer.id)) continue;
          const b = layer.bounds;
          map.addSource(layer.id, {
            type: "image",
            url: layer.png_url,
            coordinates: [
              [b.left, b.top],
              [b.right, b.top],
              [b.right, b.bottom],
              [b.left, b.bottom],
            ],
          });
          map.addLayer({
            id: `${layer.id}-layer`,
            type: "raster",
            source: layer.id,
            paint: { "raster-opacity": layer.id === "thermal" ? 0.65 : 0.9 },
          });
        }
        const first = layers[0].bounds;
        map.fitBounds(
          [
            [first.left, first.bottom],
            [first.right, first.top],
          ],
          { padding: 40 },
        );
      } catch (e) {
        console.error(e);
      }
    };

    if (map.loaded()) void addLayers();
    else map.on("load", () => void addLayers());

    return () => {
      cancelled = true;
      map.remove();
    };
  }, []);

  return <div className="map-wrap" ref={ref} />;
}

import { useEffect, useRef, useState } from "react";
import { useAppStore } from "../state/store";

export function usePanZoom(svgRef: React.RefObject<SVGSVGElement | null>) {
  const viewport = useAppStore((s) => s.viewport);
  const setViewport = useAppStore((s) => s.setViewport);
  const draggingRef = useRef(false);
  const [isDragging, setIsDragging] = useState(false);
  const lastPos = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      const { pan, zoom } = useAppStore.getState().viewport;
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      const newZoom = Math.max(0.1, Math.min(20, zoom * factor));
      const newPanX = mouseX - (mouseX - pan.x) * (newZoom / zoom);
      const newPanY = mouseY - (mouseY - pan.y) * (newZoom / zoom);

      setViewport({ zoom: newZoom, pan: { x: newPanX, y: newPanY } });
    };

    const onMouseDown = (e: MouseEvent) => {
      if (e.button === 1 || e.button === 2) {
        e.preventDefault();
        draggingRef.current = true;
        setIsDragging(true);
        lastPos.current = { x: e.clientX, y: e.clientY };
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const dx = e.clientX - lastPos.current.x;
      const dy = e.clientY - lastPos.current.y;
      lastPos.current = { x: e.clientX, y: e.clientY };
      const { pan } = useAppStore.getState().viewport;
      setViewport({ pan: { x: pan.x + dx, y: pan.y + dy } });
    };

    const onMouseUp = () => {
      draggingRef.current = false;
      setIsDragging(false);
    };
    const onContextMenu = (e: MouseEvent) => e.preventDefault();

    svg.addEventListener("wheel", onWheel, { passive: false });
    svg.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    svg.addEventListener("contextmenu", onContextMenu);

    return () => {
      svg.removeEventListener("wheel", onWheel);
      svg.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      svg.removeEventListener("contextmenu", onContextMenu);
    };
  }, [svgRef, setViewport]);

  return { viewport, isDragging };
}

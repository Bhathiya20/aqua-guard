import { useEffect, useRef, useState } from "react";
import jsQR from "jsqr";
import { Camera } from "lucide-react";
import { Button } from "../ui/button";

/** Camera QR reader (jsQR on video frames). The camera only works on https or localhost; otherwise the caller falls back to manual entry. */
export function QrScanner({ onText, onError }: { onText: (t: string) => void; onError?: (m: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [on, setOn] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!on) return;
    let stop = false;
    let stream: MediaStream | null = null;
    let raf = 0;
    (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser blocks the camera on plain http. Use the manual fields below, or open the app over https / localhost.");
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        const v = video.current!;
        v.srcObject = stream; v.setAttribute("playsinline", "true");
        await v.play();
        const cv = document.createElement("canvas");
        const ctx = cv.getContext("2d", { willReadFrequently: true })!;
        const tick = () => {
          if (stop) return;
          if (v.readyState >= 2 && v.videoWidth) {
            cv.width = v.videoWidth; cv.height = v.videoHeight;
            ctx.drawImage(v, 0, 0);
            const img = ctx.getImageData(0, 0, cv.width, cv.height);
            const hit = jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" });
            if (hit?.data) { setOn(false); onText(hit.data); return; }
          }
          raf = requestAnimationFrame(tick);
        };
        tick();
      } catch (e) {
        const m = e instanceof Error ? e.message : "Camera not available";
        setMsg(m); onError?.(m); setOn(false);
      }
    })();
    return () => { stop = true; cancelAnimationFrame(raf); stream?.getTracks().forEach((t) => t.stop()); };
  }, [on]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-2">
      {on ? (
        <div className="relative overflow-hidden rounded-lg border bg-black">
          <video ref={video} className="w-full max-h-64 object-cover" muted />
          <Button type="button" size="sm" variant="secondary" className="absolute bottom-2 right-2" onClick={() => setOn(false)}>Stop camera</Button>
        </div>
      ) : (
        <Button type="button" variant="outline" className="w-full" onClick={() => { setMsg(""); setOn(true); }}><Camera className="mr-2 h-4 w-4" />Scan the QR code on the node</Button>
      )}
      {msg && <p className="text-xs text-amber-700">{msg}</p>}
    </div>
  );
}

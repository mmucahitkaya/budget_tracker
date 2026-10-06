const MAX_SIDE = 2400;

/** Downscales large phone photos (including HEIC) to JPEG before upload. */
export async function prepareUpload(file: File): Promise<File> {
  if (file.type === "application/pdf") return file;
  const small = file.size < 1_500_000 && (file.type === "image/jpeg" || file.type === "image/png");
  if (small) return file;
  try {
    const url = URL.createObjectURL(file);
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = reject;
      i.src = url;
    });
    const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", 0.86));
    if (!blob) return file;
    const name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg" });
  } catch {
    // If the browser can't decode it (e.g. some HEICs) send the original; the server converts it
    return file;
  }
}

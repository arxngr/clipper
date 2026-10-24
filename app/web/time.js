export function parseTime(value) {
  const text = value.trim().toLowerCase();
  if (!text) return null;
  if (/^\d+(\.\d+)?m$/.test(text)) return Number(text.slice(0, -1)) * 60;
  if (/^\d+(\.\d+)?s?$/.test(text)) return Number(text.replace(/s$/, ""));
  if (
    !/^\d+:\d{2}(:\d{2}(\.\d+)?)?$/.test(text) &&
    !/^\d+:\d{2}(\.\d+)?$/.test(text)
  ) {
    throw new Error("Use a time like 2:30, 01:02:30, or 2m.");
  }
  const parts = text.split(":").map(Number);
  if (parts.slice(1).some((part) => part >= 60)) {
    throw new Error("Seconds and minutes in timestamps must be below 60.");
  }
  return parts.reduce((total, part) => total * 60 + part, 0);
}

export function timeLabel(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return "Unknown duration";
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const secs = String(rounded % 60).padStart(2, "0");
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}`
    : `${minutes}:${secs}`;
}

export function buildRanges(rows, duration) {
  if (!rows.length || rows.length > 20)
    throw new Error("Choose between 1 and 20 clips.");
  return rows.map((row, index) => {
    try {
      const start = parseTime(row.start) ?? 0;
      const end = parseTime(row.end);
      if (!Number.isFinite(start) || (end !== null && !Number.isFinite(end)))
        throw new Error("Enter a valid time.");
      if (end !== null && end <= start)
        throw new Error("The end must be after the start.");
      if (
        duration != null &&
        (start >= duration || (end !== null && end > duration))
      )
        throw new Error("The range is beyond the end of the video.");
      return { start, end };
    } catch (error) {
      throw new Error(`Clip ${index + 1}: ${error.message}`);
    }
  });
}

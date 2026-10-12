// Checks that the recorded timeline lines up with the video clock, by finding the frames where the
// caption box appears and disappears (its navy, blue-tinted padding at x 28-40, y 688-696).
import { spawnSync } from 'node:child_process';

/** [{ t, on }] for every caption on/off edge in the video. */
export function captionEdges(video) {
  const out = spawnSync('ffmpeg', ['-hide_banner', '-i', video, '-vf', 'crop=12:8:28:688,showinfo', '-f', 'null', '-'], { maxBuffer: 256 * 1024 * 1024 }).stderr.toString();
  const edges = [];
  let prev = false;
  for (const l of out.split('\n')) {
    const m = l.match(/pts_time:([\d.]+).*?mean:\[(\d+) (\d+) (\d+)\]/);
    if (!m) continue;
    const on = +m[2] < 90 && +m[3] > 133; // dark and blue-tinted: the caption box, not a dark sidebar
    if (on !== prev) edges.push({ t: +m[1], on });
    prev = on;
  }
  return edges;
}

/**
 * Offset of the video clock vs the timeline (from the first caption), and the largest deviation of
 * any later visible edge from its nearest timeline boundary once that offset is applied.
 */
export function syncReport(video, timeline) {
  const edges = captionEdges(video);
  if (!edges.length) return { offset: null, edges: 0, max_deviation: null };
  const offset = +(edges[0].t - timeline[0].start).toFixed(3);
  let max = 0;
  for (const e of edges.slice(1)) {
    const marks = timeline.map((s) => (e.on ? s.start : s.end) + offset);
    const d = Math.min(...marks.map((x) => Math.abs(x - e.t)));
    max = Math.max(max, d);
  }
  return { offset, edges: edges.length, max_deviation: +max.toFixed(3) };
}

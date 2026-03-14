import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import {
  ChevronLeft,
  Download,
  MoreVertical,
  SkipBack,
  SkipForward,
  Play,
  Pause,
  Search,
  Send,
  Plus,
} from "lucide-react";
import { cn } from "@/lib/utils";

const TAB_SCRIPT = "script";
const TAB_QA = "qa";
const TAB_NOTES = "notes";
const HIGHLIGHT_EXIT_MS = 220;

function clamp(value, min, max) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.max(min, Math.min(max, value));
}

function clamp01(value) {
  return clamp(value, 0, 1);
}

function formatClock(ms) {
  const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const min = String(Math.floor(total / 60)).padStart(2, "0");
  const sec = String(total % 60).padStart(2, "0");
  return `${min}:${sec}`;
}

function sanitizeLectureTitle(rawTitle, jobId = "") {
  const value = typeof rawTitle === "string" ? rawTitle.trim() : "";
  if (!value) {
    return "";
  }

  const normalized = value.toLowerCase();
  const normalizedJobId = String(jobId || "").trim().toLowerCase();
  if (normalizedJobId) {
    if (normalized === normalizedJobId || normalized === `lecture ${normalizedJobId}`) {
      return "";
    }
  }

  if (/^lecture\s+[a-f0-9-]{6,}$/i.test(value)) {
    return "";
  }

  return value;
}

function getSlideId(slide) {
  if (typeof slide?.slide_id === "string" && slide.slide_id) {
    return slide.slide_id;
  }
  if (typeof slide?.image_name === "string" && slide.image_name) {
    return slide.image_name;
  }
  return "";
}

function normalizeDwellMs(step) {
  const dwellMs = Number.isInteger(step?.dwell_ms) ? step.dwell_ms : 3500;
  return dwellMs > 0 ? dwellMs : 3500;
}

function hasPerStepAudioTiming(payload) {
  const slides = payload?.slides || [];
  let totalSteps = 0;
  for (const slide of slides) {
    const steps = Array.isArray(slide?.steps) ? slide.steps : [];
    for (const step of steps) {
      totalSteps += 1;
      const startMs = Number(step?.audio_start_ms);
      const endMs = Number(step?.audio_end_ms);
      if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
        return false;
      }
    }
  }
  return totalSteps > 0;
}

function buildPlaybackTimeline(payload) {
  const slides = payload?.slides || [];
  const usePerStepTiming = hasPerStepAudioTiming(payload);

  const timeline = [];
  let cursor = 0;

  slides.forEach((slideData, slideIndex) => {
    const steps = Array.isArray(slideData?.steps) ? slideData.steps : [];
    const slideEntry = {
      slideIndex,
      startMs: cursor,
      endMs: cursor,
      steps: [],
    };

    steps.forEach((step, stepIndex) => {
      let stepStart = cursor;
      let durationMs = Math.max(250, normalizeDwellMs(step));
      if (usePerStepTiming) {
        const rawStart = Number(step?.audio_start_ms);
        const rawEnd = Number(step?.audio_end_ms);
        stepStart = Math.max(cursor, Math.round(rawStart));
        durationMs = Math.max(250, Math.round(rawEnd) - Math.round(rawStart));
      }
      const stepEnd = stepStart + durationMs;
      slideEntry.steps.push({
        stepIndex,
        startMs: stepStart,
        endMs: stepEnd,
      });
      cursor = stepEnd;
    });

    slideEntry.endMs = cursor;
    timeline.push(slideEntry);
  });

  return {
    timeline,
    totalMs: cursor,
  };
}

function getCurrentGlobalMs(timeline, slideIndex, stepIndex) {
  const slideEntry = Array.isArray(timeline) ? timeline[slideIndex] : null;
  if (!slideEntry) {
    return 0;
  }
  if (!Array.isArray(slideEntry.steps) || slideEntry.steps.length === 0) {
    return Number(slideEntry.startMs) || 0;
  }

  const safeStep = clamp(stepIndex, 0, slideEntry.steps.length - 1);
  const step = slideEntry.steps[safeStep];
  return Number(step?.startMs) || Number(slideEntry.startMs) || 0;
}

function locateTimelinePosition(timeline, totalMs, globalMs) {
  if (!Array.isArray(timeline) || timeline.length === 0) {
    return null;
  }

  const limit = Number.isFinite(totalMs) && totalMs > 0 ? totalMs : globalMs;
  const clamped = Math.max(0, Math.min(Number(globalMs) || 0, limit));

  for (let slideIdx = 0; slideIdx < timeline.length; slideIdx += 1) {
    const slide = timeline[slideIdx];
    const isLastSlide = slideIdx === timeline.length - 1;
    if (clamped < (Number(slide?.endMs) || 0) || isLastSlide) {
      const steps = Array.isArray(slide?.steps) ? slide.steps : [];
      if (steps.length === 0) {
        return { slideIndex: slideIdx, stepIndex: 0 };
      }
      for (let stepIdx = 0; stepIdx < steps.length; stepIdx += 1) {
        const step = steps[stepIdx];
        const isLastStep = stepIdx === steps.length - 1;
        if (clamped < (Number(step?.endMs) || 0) || (isLastSlide && isLastStep)) {
          return { slideIndex: slideIdx, stepIndex: stepIdx };
        }
      }
      return { slideIndex: slideIdx, stepIndex: steps.length - 1 };
    }
  }

  return { slideIndex: timeline.length - 1, stepIndex: 0 };
}

function slideStepCountSignature(payload) {
  const slides = payload?.slides || [];
  return slides
    .map((slide) => {
      if (!Array.isArray(slide?.steps)) {
        return "x";
      }
      return String(slide.steps.length);
    })
    .join(",");
}

function shouldPollLecturePayload(payload) {
  if (!payload || !Array.isArray(payload.slides) || payload.slides.length === 0) {
    return true;
  }
  if (!payload.audio_url) {
    return true;
  }
  return payload.slides.some((slide) => !Array.isArray(slide?.steps) || slide.steps.length === 0);
}

function getLocalBackendOrigin() {
  if (typeof window === "undefined") {
    return "";
  }
  const { protocol, hostname, port } = window.location;
  const isLocalHost = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
  if (!isLocalHost) {
    return "";
  }
  if (port === "8100") {
    return "";
  }
  return `${protocol}//${hostname}:8100`;
}

function buildApiUrl(path) {
  const directOrigin = getLocalBackendOrigin();
  return directOrigin ? `${directOrigin}${path}` : path;
}

function absolutizeApiUrl(url, backendOrigin) {
  if (typeof url !== "string" || !url) {
    return url;
  }
  if (/^https?:\/\//i.test(url)) {
    return url;
  }
  if (!backendOrigin) {
    return url;
  }
  if (url.startsWith("/")) {
    return `${backendOrigin}${url}`;
  }
  return url;
}

function normalizeLecturePayload(payload) {
  if (!payload || typeof payload !== "object") {
    return payload;
  }

  const backendOrigin = getLocalBackendOrigin();
  const slides = Array.isArray(payload.slides)
    ? payload.slides.map((slide) => {
        if (!slide || typeof slide !== "object") {
          return slide;
        }
        return {
          ...slide,
          image_url: absolutizeApiUrl(slide.image_url, backendOrigin),
          thumbnail_url: absolutizeApiUrl(slide.thumbnail_url, backendOrigin),
          rendered_step_urls: Array.isArray(slide.rendered_step_urls)
            ? slide.rendered_step_urls.map((url) => absolutizeApiUrl(url, backendOrigin))
            : [],
        };
      })
    : [];

  return {
    ...payload,
    input_pdf_url: absolutizeApiUrl(payload.input_pdf_url, backendOrigin),
    audio_url: absolutizeApiUrl(payload.audio_url, backendOrigin),
    slides,
  };
}

function getRenderedStepUrl(slide, stepIndex) {
  if (!slide || !Array.isArray(slide.rendered_step_urls)) {
    return null;
  }
  if (stepIndex < 0 || stepIndex >= slide.rendered_step_urls.length) {
    return null;
  }
  const url = slide.rendered_step_urls[stepIndex];
  return typeof url === "string" && url ? url : null;
}

function mapScaledPolygon(poly, geometry) {
  if (!Array.isArray(poly) || !geometry) {
    return [];
  }

  const scaled = [];
  poly.forEach((pt) => {
    if (!Array.isArray(pt) || pt.length < 2) {
      return;
    }
    const x = Number(pt[0]);
    const y = Number(pt[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return;
    }
    scaled.push([
      geometry.offsetX + x * geometry.scaleX,
      geometry.offsetY + y * geometry.scaleY,
    ]);
  });

  return scaled;
}

function mapScaledBbox(bbox, geometry) {
  if (!Array.isArray(bbox) || bbox.length !== 4 || !geometry) {
    return null;
  }

  const x1 = Number(bbox[0]);
  const y1 = Number(bbox[1]);
  const x2 = Number(bbox[2]);
  const y2 = Number(bbox[3]);
  if (!Number.isFinite(x1) || !Number.isFinite(y1) || !Number.isFinite(x2) || !Number.isFinite(y2)) {
    return null;
  }

  const left = geometry.offsetX + Math.min(x1, x2) * geometry.scaleX;
  const top = geometry.offsetY + Math.min(y1, y2) * geometry.scaleY;
  const width = Math.abs(x2 - x1) * geometry.scaleX;
  const height = Math.abs(y2 - y1) * geometry.scaleY;
  if (width < 2 || height < 2) {
    return null;
  }

  return { left, top, width, height };
}

const TEXT_HIGHLIGHT_PAD_PX = 4;

function expandScaledPolygon(points, pad) {
  if (!Array.isArray(points) || points.length < 3 || pad <= 0) {
    return points;
  }
  let cx = 0;
  let cy = 0;
  for (const p of points) {
    cx += p[0];
    cy += p[1];
  }
  cx /= points.length;
  cy /= points.length;
  return points.map((p) => {
    const dx = p[0] - cx;
    const dy = p[1] - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < 0.5) {
      return p;
    }
    const scale = (dist + pad) / dist;
    return [cx + dx * scale, cy + dy * scale];
  });
}

function toSvgPolygonPoints(points) {
  return points.map((pt) => `${pt[0]},${pt[1]}`).join(" ");
}

function toCssPolygonPoints(points) {
  return points.map((pt) => `${pt[0]}px ${pt[1]}px`).join(", ");
}

function _catmullRomToBezier(p0, p1, p2, p3) {
  return {
    cp1: [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6],
    cp2: [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6],
    end: p2,
  };
}

function toSmoothSvgPath(points) {
  if (!Array.isArray(points) || points.length < 3) {
    return "";
  }
  const n = points.length;
  const p = (i) => points[((i % n) + n) % n];
  let d = `M${p(0)[0]},${p(0)[1]}`;
  for (let i = 0; i < n; i++) {
    const seg = _catmullRomToBezier(p(i - 1), p(i), p(i + 1), p(i + 2));
    d += ` C${seg.cp1[0]},${seg.cp1[1]} ${seg.cp2[0]},${seg.cp2[1]} ${seg.end[0]},${seg.end[1]}`;
  }
  return d + "Z";
}

function toLinearSvgPath(points) {
  if (!Array.isArray(points) || points.length < 3) {
    return "";
  }
  const first = points[0];
  let d = `M${first[0]},${first[1]}`;
  for (let i = 1; i < points.length; i += 1) {
    const pt = points[i];
    d += ` L${pt[0]},${pt[1]}`;
  }
  return d + "Z";
}

function smoothPolygonPoints(points, samplesPerSegment = 6) {
  if (!Array.isArray(points) || points.length < 3) {
    return points;
  }
  const n = points.length;
  const p = (i) => points[((i % n) + n) % n];
  const result = [];
  for (let i = 0; i < n; i++) {
    const p0 = p(i - 1);
    const p1 = p(i);
    const p2 = p(i + 1);
    const p3 = p(i + 2);
    for (let t = 0; t < samplesPerSegment; t++) {
      const s = t / samplesPerSegment;
      const s2 = s * s;
      const s3 = s2 * s;
      result.push([
        0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * s + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * s2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * s3),
        0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * s + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * s2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * s3),
      ]);
    }
  }
  return result;
}

function polygonLength(points) {
  if (!Array.isArray(points) || points.length < 2) {
    return 0;
  }
  let len = 0;
  for (let i = 0; i < points.length; i += 1) {
    const p1 = points[i];
    const p2 = points[(i + 1) % points.length];
    if (!Array.isArray(p1) || !Array.isArray(p2)) {
      continue;
    }
    const dx = Number(p2[0]) - Number(p1[0]);
    const dy = Number(p2[1]) - Number(p1[1]);
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) {
      continue;
    }
    len += Math.sqrt(dx * dx + dy * dy);
  }
  return len;
}

function resolveExpandedIdsForSlide(slide, seedIds) {
  const regionMap = new Map();
  const clusterMap = new Map();
  const groupMap = new Map();

  const regions = Array.isArray(slide?.regions) ? slide.regions : [];
  regions.forEach((region) => {
    if (region && typeof region.id === "string") {
      regionMap.set(region.id, region);
    }
  });

  const clusters = Array.isArray(slide?.clusters) ? slide.clusters : [];
  clusters.forEach((cluster) => {
    if (cluster && typeof cluster.id === "string") {
      clusterMap.set(cluster.id, cluster);
    }
  });

  const groups = Array.isArray(slide?.groups) ? slide.groups : [];
  groups.forEach((group) => {
    if (group && typeof group.id === "string") {
      groupMap.set(group.id, group);
    }
  });

  const visited = new Set();

  function activateId(id) {
    if (!id || visited.has(id)) {
      return;
    }
    visited.add(id);

    if (id.startsWith("g:")) {
      const group = groupMap.get(id);
      if (group && Array.isArray(group.children)) {
        group.children.forEach((childId) => activateId(childId));
      }
      return;
    }

    if (id.startsWith("c:")) {
      const cluster = clusterMap.get(id);
      if (cluster && Array.isArray(cluster.region_ids)) {
        cluster.region_ids.forEach((regionId) => activateId(regionId));
      }
    }
  }

  (Array.isArray(seedIds) ? seedIds : []).forEach((id) => {
    if (typeof id === "string") {
      activateId(id);
    }
  });

  return { visited, regionMap };
}

function getQaProgressLabel(progress) {
  if (!progress || typeof progress !== "object") {
    return "Working...";
  }
  const stage = typeof progress.stage === "string" ? progress.stage : "";
  const byStage = {
    request_start: "Preparing request...",
    index_ready: "Loading lecture index...",
    query_rewrite_done: "Rewriting query...",
    embedding_ready: "Embedding question...",
    retrieval_ready: "Retrieving relevant context...",
    rerank_done: "Reranking context...",
    context_ready: "Packing context...",
    answerability_done: "Checking answerability...",
    answer_generating: "Generating answer...",
    answer_ready: "Answer generated.",
    verification_done: "Verifying grounding...",
    request_done: "Finalizing response...",
  };

  if (stage && byStage[stage]) {
    return byStage[stage];
  }
  if (typeof progress.message === "string" && progress.message.trim()) {
    return progress.message.trim();
  }
  return "Working...";
}

async function streamQaAnswer({
  jobId,
  question,
  signal,
  onProgress,
  onDelta,
  onResult,
}) {
  const response = await fetch(buildApiUrl(`/api/jobs/${encodeURIComponent(jobId)}/qa/answer/stream`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });

  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = typeof payload?.detail === "string" ? payload.detail : "";
    } catch {
      try {
        detail = await response.text();
      } catch {
        detail = "";
      }
    }
    throw new Error(detail || `Question failed: ${response.status}`);
  }

  if (!response.body) {
    throw new Error("Streaming response body is unavailable.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalResult = null;

  const dispatchEventBlock = (block) => {
    if (!block || !block.trim() || block.trimStart().startsWith(":")) {
      return;
    }

    let eventName = "message";
    const dataLines = [];
    const lines = String(block).replace(/\r/g, "").split("\n");

    lines.forEach((line) => {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim() || "message";
        return;
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    });

    if (dataLines.length === 0) {
      return;
    }

    let parsed = null;
    try {
      parsed = JSON.parse(dataLines.join("\n"));
    } catch {
      parsed = null;
    }

    if (eventName === "progress") {
      if (typeof onProgress === "function") {
        onProgress(parsed || {});
      }
      return;
    }

    if (eventName === "delta") {
      const text = typeof parsed?.text === "string" ? parsed.text : "";
      if (text && typeof onDelta === "function") {
        onDelta(text);
      }
      return;
    }

    if (eventName === "result") {
      if (parsed && typeof parsed === "object") {
        finalResult = parsed;
        if (typeof onResult === "function") {
          onResult(parsed);
        }
      }
      return;
    }

    if (eventName === "error") {
      const detail = typeof parsed?.detail === "string" ? parsed.detail : "Question failed.";
      throw new Error(detail);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      dispatchEventBlock(block);
      boundary = buffer.indexOf("\n\n");
    }
  }

  const tail = buffer.trim();
  if (tail) {
    dispatchEventBlock(tail);
  }

  if (finalResult && typeof finalResult === "object") {
    return finalResult;
  }

  throw new Error("Streaming ended before final result.");
}

function collectLineSlideRefs(line) {
  const highlights = Array.isArray(line?.highlights) ? line.highlights : [];
  const map = new Map();
  highlights.forEach((hl) => {
    if (!hl || typeof hl.slide_id !== "string") {
      return;
    }
    const slideId = hl.slide_id;
    const slideNo = Number.isFinite(Number(hl.slide_number)) ? Number(hl.slide_number) : null;
    if (!map.has(slideId) || (map.get(slideId) == null && slideNo != null)) {
      map.set(slideId, slideNo);
    }
  });

  return Array.from(map.entries())
    .sort((a, b) => {
      const aNo = Number.isFinite(a[1]) ? Number(a[1]) : Number.MAX_SAFE_INTEGER;
      const bNo = Number.isFinite(b[1]) ? Number(b[1]) : Number.MAX_SAFE_INTEGER;
      if (aNo !== bNo) {
        return aNo - bNo;
      }
      return String(a[0]).localeCompare(String(b[0]));
    })
    .map(([slideId, slideNo]) => ({
      slideId,
      label: Number.isFinite(slideNo) ? `Slide ${slideNo}` : slideId,
    }));
}

function toActiveOverlayItems(items) {
  return Array.isArray(items) ? items.map((item) => ({ ...item, phase: "active" })) : [];
}

function emptyOverlayShapeState() {
  return { boxes: [], lifts: [], underlays: [], polygons: [] };
}

export default function LecturePage() {
  const router = useRouter();
  const routeJobId = typeof router.query.job_id === "string" ? router.query.job_id : "";

  const lectureMainRef = useRef(null);
  const slidePaneRef = useRef(null);
  const slideFrameRef = useRef(null);
  const imageContainerRef = useRef(null);
  const mainImageRef = useRef(null);
  const timelineRailRef = useRef(null);
  const audioRef = useRef(null);
  const qaThreadRef = useRef(null);
  const qaTextareaRef = useRef(null);

  const preloadedUrlsRef = useRef(new Set());
  const qaAbortRef = useRef(null);
  const qaRequestSeqRef = useRef(0);
  const qaScrollRafRef = useRef(0);
  const splitRafRef = useRef(0);
  const timelineScrubPointerIdRef = useRef(null);

  const lectureRef = useRef(null);
  const currentSlideIndexRef = useRef(0);
  const currentStepIndexRef = useRef(0);
  const playbackTimelineRef = useRef({ timeline: [], totalMs: 0 });
  const overlayExitTimersRef = useRef(new Map());
  const overlaySlideIdRef = useRef("");

  const [lecture, setLecture] = useState(null);
  const [lectureLoadError, setLectureLoadError] = useState("");
  const [slideMessage, setSlideMessage] = useState("Loading slide...");
  const [mainImageSrc, setMainImageSrc] = useState("");
  const [mainImageName, setMainImageName] = useState("");

  const [currentSlideIndex, setCurrentSlideIndex] = useState(0);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [currentTab, setCurrentTab] = useState(TAB_SCRIPT);

  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1.0);
  const [audioClockMs, setAudioClockMs] = useState(0);
  const [audioDurationMs, setAudioDurationMs] = useState(0);

  const [imageGeometry, setImageGeometry] = useState(null);

  const [qaQuestion, setQaQuestion] = useState("");
  const [qaTurns, setQaTurns] = useState([]);
  const [qaError, setQaError] = useState("");
  const [qaLoading, setQaLoading] = useState(false);
  const [qaActiveTurnId, setQaActiveTurnId] = useState("");
  const [qaActiveLineIndex, setQaActiveLineIndex] = useState(-1);
  const [qaActiveLineSlideId, setQaActiveLineSlideId] = useState("");

  const [searchFlash, setSearchFlash] = useState(false);
  const [animatedOverlayShapes, setAnimatedOverlayShapes] = useState(emptyOverlayShapeState);
  const [videoExportLoading, setVideoExportLoading] = useState(false);

  const [contentRevealed, setContentRevealed] = useState(false);
  const [overlayExiting, setOverlayExiting] = useState(false);
  const [overlayVisible, setOverlayVisible] = useState(true);
  const hasMarkedInitialLoad = useRef(false);

  const slides = Array.isArray(lecture?.slides) ? lecture.slides : [];
  const currentSlide = slides[currentSlideIndex] || null;
  const currentSteps = Array.isArray(currentSlide?.steps) ? currentSlide.steps : [];
  const currentStep = currentSteps[currentStepIndex] || null;

  const playbackTimeline = useMemo(() => buildPlaybackTimeline(lecture), [lecture]);

  useEffect(() => { lectureRef.current = lecture; }, [lecture]);
  useEffect(() => { currentSlideIndexRef.current = currentSlideIndex; }, [currentSlideIndex]);
  useEffect(() => { currentStepIndexRef.current = currentStepIndex; }, [currentStepIndex]);
  useEffect(() => { playbackTimelineRef.current = playbackTimeline; }, [playbackTimeline]);

  const slideIndexById = useMemo(() => {
    const map = new Map();
    slides.forEach((slide, idx) => {
      const id = getSlideId(slide);
      if (id) { map.set(id, idx); }
    });
    return map;
  }, [slides]);

  const playbackProgress = useMemo(() => {
    const audio = audioRef.current;
    if (audio && lecture?.audio_url && Number.isFinite(audio.duration) && audio.duration > 0) {
      return clamp01(audio.currentTime / audio.duration);
    }
    if (playbackTimeline.totalMs > 0) {
      const currentGlobalMs = getCurrentGlobalMs(playbackTimeline.timeline, currentSlideIndex, currentStepIndex);
      return clamp01(currentGlobalMs / playbackTimeline.totalMs);
    }
    if (slides.length <= 1) { return 0; }
    return clamp01(currentSlideIndex / (slides.length - 1));
  }, [currentSlideIndex, currentStepIndex, lecture?.audio_url, playbackTimeline.timeline, playbackTimeline.totalMs, slides.length, audioClockMs]);

  const trackTitle = useMemo(() => {
    if (!currentSlide) { return "Slide"; }
    const slideNo = Number.isFinite(Number(currentSlide.slide_number)) ? Number(currentSlide.slide_number) : currentSlideIndex + 1;
    const cleanTitle = sanitizeLectureTitle(lecture?.title, routeJobId);
    if (cleanTitle) { return `${cleanTitle} • Slide ${slideNo}`; }
    return `Slide ${slideNo}`;
  }, [currentSlide, currentSlideIndex, lecture?.title, routeJobId]);

  const stepCounterText = useMemo(() => {
    if (!currentSlide) { return "00:00 - 00:00"; }
    const slideTimeline = playbackTimeline.timeline[currentSlideIndex] || null;
    const timelineStep = slideTimeline && Array.isArray(slideTimeline.steps) && slideTimeline.steps.length > 0
      ? slideTimeline.steps[clamp(currentStepIndex, 0, slideTimeline.steps.length - 1)] : null;
    const useTimeline = Boolean(lecture?.audio_url);
    const startMs = useTimeline ? Number(timelineStep?.startMs) || 0 : Number(currentStep?.start_ms) || 0;
    const endMs = useTimeline ? Number(timelineStep?.endMs) || startMs : startMs + normalizeDwellMs(currentStep);
    return `${formatClock(startMs)} - ${formatClock(endMs)}`;
  }, [currentSlide, currentSlideIndex, currentStep, currentStepIndex, lecture?.audio_url, playbackTimeline.timeline]);

  const trackTimeText = useMemo(() => {
    const audio = audioRef.current;
    if (audio && lecture?.audio_url && Number.isFinite(audio.duration) && audio.duration > 0) {
      return `${formatClock(audioClockMs)} / ${formatClock(audioDurationMs)}`;
    }
    const startMs = getCurrentGlobalMs(playbackTimeline.timeline, currentSlideIndex, currentStepIndex);
    const fallbackTotalMs = playbackTimeline.totalMs > 0 ? playbackTimeline.totalMs : currentSteps.reduce((sum, step) => sum + normalizeDwellMs(step), 0);
    return `${formatClock(startMs)} / ${formatClock(fallbackTotalMs)}`;
  }, [audioClockMs, audioDurationMs, currentSlideIndex, currentStepIndex, currentSteps, lecture?.audio_url, playbackTimeline.timeline, playbackTimeline.totalMs]);

  const slideCounterText = useMemo(() => {
    if (!currentSlide) { return "Slide 1"; }
    const slideNo = Number.isFinite(Number(currentSlide.slide_number)) ? Number(currentSlide.slide_number) : currentSlideIndex + 1;
    return `Slide ${slideNo}${slides.length ? ` of ${slides.length}` : ""}`;
  }, [currentSlide, currentSlideIndex, slides.length]);

  const activeTurn = useMemo(() => {
    if (!qaActiveTurnId) { return null; }
    return qaTurns.find((turn) => turn && turn.id === qaActiveTurnId) || null;
  }, [qaActiveTurnId, qaTurns]);

  const activeAnswerLines = useMemo(() => {
    const lines = activeTurn?.result?.answer_lines;
    return Array.isArray(lines) ? lines : [];
  }, [activeTurn]);

  const activeQaLine = qaActiveLineIndex >= 0 && qaActiveLineIndex < activeAnswerLines.length ? activeAnswerLines[qaActiveLineIndex] : null;

  const activeSeedRegionIds = useMemo(() => {
    if (!currentSlide) { return []; }
    if (currentTab === TAB_SCRIPT) {
      return Array.isArray(currentStep?.region_ids) ? currentStep.region_ids.filter((id) => typeof id === "string") : [];
    }
    if (currentTab === TAB_QA && activeQaLine) {
      const currentSlideId = getSlideId(currentSlide);
      if (!currentSlideId) { return []; }
      const targetSlideId = qaActiveLineSlideId || currentSlideId;
      if (targetSlideId !== currentSlideId) { return []; }
      const lineRegionIds = (Array.isArray(activeQaLine.highlights) ? activeQaLine.highlights : [])
        .filter((item) => item && item.slide_id === currentSlideId && typeof item.region_id === "string")
        .map((item) => item.region_id);
      const out = [];
      const seen = new Set();
      lineRegionIds.forEach((id) => { if (typeof id !== "string" || !id || seen.has(id)) { return; } seen.add(id); out.push(id); });
      return out;
    }
    return [];
  }, [currentSlide, currentStep, currentTab, activeQaLine, qaActiveLineSlideId]);

  const activeHighlightIds = useMemo(() => {
    if (!currentSlide || activeSeedRegionIds.length === 0) { return []; }
    const { visited } = resolveExpandedIdsForSlide(currentSlide, activeSeedRegionIds);
    return Array.from(visited);
  }, [currentSlide, activeSeedRegionIds]);

  const activeHighlightIdSet = useMemo(() => new Set(activeHighlightIds), [activeHighlightIds]);

  const rawOverlayShapes = useMemo(() => {
    if (!currentSlide || !imageGeometry || activeHighlightIds.length === 0) {
      return { boxes: [], lifts: [], underlays: [], polygons: [] };
    }
    const slideIdForAnim = getSlideId(currentSlide) || "slide";
    const playStepAnimScope = currentTab === TAB_SCRIPT && isPlaying
      ? `:play:${slideIdForAnim}:${currentStepIndex}`
      : "";
    const allowTextHighlights = currentTab === TAB_QA;
    const boxes = [];
    const underlays = [];
    const lifts = [];
    const polygons = [];
    const pushBox = ({ id, kind, bbox, partIndex = 0 }) => {
      const scaled = mapScaledBbox(bbox, imageGeometry);
      if (!scaled) { return; }
      const pad = kind !== "visual" ? TEXT_HIGHLIGHT_PAD_PX : 0;
      boxes.push({ key: `${id}:box:${partIndex}${playStepAnimScope}`, id, kind: kind === "visual" ? "visual" : "text", left: scaled.left - pad, top: scaled.top - pad, width: scaled.width + pad * 2, height: scaled.height + pad * 2 });
    };
    const regions = Array.isArray(currentSlide.regions) ? currentSlide.regions : [];
    regions.forEach((region) => {
      const id = typeof region?.id === "string" ? region.id : "";
      if (!id || !activeHighlightIdSet.has(id)) { return; }
      const kind = String(region?.kind || "text").toLowerCase() === "visual" ? "visual" : "text";
      if (kind === "text" && !allowTextHighlights) { return; }
      const addPolygonPart = (poly, partIndex) => {
        let scaled = mapScaledPolygon(poly, imageGeometry);
        if (scaled.length < 3) { return false; }
        if (kind !== "visual") { scaled = expandScaledPolygon(scaled, TEXT_HIGHLIGHT_PAD_PX); }
        const pathD = kind === "visual" ? toSmoothSvgPath(scaled) : toLinearSvgPath(scaled);
        const len = polygonLength(scaled);
        polygons.push({ key: `${id}:poly:${partIndex}${playStepAnimScope}`, id, kind, pathD, len });
        if (kind === "visual") {
          const smoothed = smoothPolygonPoints(scaled, 6);
          const clipPath = `polygon(${toCssPolygonPoints(smoothed)})`;
          underlays.push({ key: `${id}:underlay:${partIndex}${playStepAnimScope}`, id, clipPath });
          lifts.push({ key: `${id}:lift:${partIndex}${playStepAnimScope}`, id, clipPath });
        }
        return true;
      };
      let polygonCount = 0;
      if (Array.isArray(region?.polygons) && region.polygons.length > 0) {
        region.polygons.forEach((poly, idx) => { if (addPolygonPart(poly, idx)) { polygonCount += 1; } });
      }
      if (polygonCount === 0 && Array.isArray(region?.polygon) && region.polygon.length >= 3) {
        if (addPolygonPart(region.polygon, 0)) { polygonCount = 1; }
      }
      if (polygonCount === 0) { pushBox({ id, kind, bbox: region?.bbox }); }
    });
    return { boxes, lifts, underlays, polygons };
  }, [currentSlide, imageGeometry, activeHighlightIdSet, activeHighlightIds.length, currentTab, isPlaying, currentStepIndex]);

  useEffect(() => {
    const clearAllOverlayTimers = () => {
      for (const timerId of overlayExitTimersRef.current.values()) {
        clearTimeout(timerId);
      }
      overlayExitTimersRef.current.clear();
    };

    const slideId = currentSlide ? getSlideId(currentSlide) : "";
    if (overlaySlideIdRef.current !== slideId) {
      overlaySlideIdRef.current = slideId;
      clearAllOverlayTimers();
      setAnimatedOverlayShapes({
        boxes: toActiveOverlayItems(rawOverlayShapes.boxes),
        underlays: toActiveOverlayItems(rawOverlayShapes.underlays),
        lifts: toActiveOverlayItems(rawOverlayShapes.lifts),
        polygons: toActiveOverlayItems(rawOverlayShapes.polygons),
      });
      return;
    }

    const clearRemovalTimer = (groupName, key) => {
      const timerKey = `${groupName}:${key}`;
      const timerId = overlayExitTimersRef.current.get(timerKey);
      if (timerId == null) {
        return;
      }
      clearTimeout(timerId);
      overlayExitTimersRef.current.delete(timerKey);
    };

    const scheduleRemoval = (groupName, key) => {
      const timerKey = `${groupName}:${key}`;
      if (overlayExitTimersRef.current.has(timerKey)) {
        return;
      }
      const timerId = setTimeout(() => {
        overlayExitTimersRef.current.delete(timerKey);
        setAnimatedOverlayShapes((prev) => ({
          ...prev,
          [groupName]: (Array.isArray(prev?.[groupName]) ? prev[groupName] : []).filter(
            (item) => item && item.key !== key
          ),
        }));
      }, HIGHLIGHT_EXIT_MS);
      overlayExitTimersRef.current.set(timerKey, timerId);
    };

    const mergeGroupWithExit = (prevGroup, nextGroup, groupName) => {
      const prevItems = Array.isArray(prevGroup) ? prevGroup : [];
      const nextItems = Array.isArray(nextGroup) ? nextGroup : [];
      const nextKeySet = new Set();
      const merged = [];

      nextItems.forEach((item) => {
        if (!item || typeof item.key !== "string") {
          return;
        }
        nextKeySet.add(item.key);
        clearRemovalTimer(groupName, item.key);
        merged.push({ ...item, phase: "active" });
      });

      prevItems.forEach((item) => {
        if (!item || typeof item.key !== "string") {
          return;
        }
        if (nextKeySet.has(item.key)) {
          return;
        }
        if (item.phase === "exiting") {
          merged.push(item);
          return;
        }
        merged.push({ ...item, phase: "exiting" });
        scheduleRemoval(groupName, item.key);
      });

      return merged;
    };

    setAnimatedOverlayShapes((prev) => ({
      boxes: toActiveOverlayItems(rawOverlayShapes.boxes),
      underlays: mergeGroupWithExit(prev.underlays, rawOverlayShapes.underlays, "underlays"),
      lifts: mergeGroupWithExit(prev.lifts, rawOverlayShapes.lifts, "lifts"),
      polygons: mergeGroupWithExit(prev.polygons, rawOverlayShapes.polygons, "polygons"),
    }));
  }, [currentSlide, rawOverlayShapes]);

  useEffect(() => {
    return () => {
      for (const timerId of overlayExitTimersRef.current.values()) {
        clearTimeout(timerId);
      }
      overlayExitTimersRef.current.clear();
    };
  }, []);

  const overlayShapes = animatedOverlayShapes;

  const updateImageGeometry = useCallback(() => {
    const imageEl = mainImageRef.current;
    const containerEl = imageContainerRef.current;
    if (!imageEl || !containerEl || !currentSlide) { setImageGeometry(null); return; }
    const rect = imageEl.getBoundingClientRect();
    const containerRect = containerEl.getBoundingClientRect();
    if (!rect.width || !rect.height || !containerRect.width || !containerRect.height) { setImageGeometry(null); return; }
    const sourceWidth = Number(currentSlide.image_width) || imageEl.naturalWidth || 1;
    const sourceHeight = Number(currentSlide.image_height) || imageEl.naturalHeight || 1;
    if (!sourceWidth || !sourceHeight) { setImageGeometry(null); return; }
    setImageGeometry({
      offsetX: rect.left - containerRect.left, offsetY: rect.top - containerRect.top,
      width: rect.width, height: rect.height, sourceWidth, sourceHeight,
      scaleX: rect.width / sourceWidth, scaleY: rect.height / sourceHeight,
    });
  }, [currentSlide]);

  const updateAdaptiveLectureSplit = useCallback(() => {
    const lectureMain = lectureMainRef.current;
    const slidePane = slidePaneRef.current;
    const slideFrame = slideFrameRef.current;
    const imageContainer = imageContainerRef.current;
    if (!lectureMain || !slidePane || !slideFrame || !imageContainer) { return; }
    if (typeof window === "undefined") { return; }
    if (window.innerWidth <= 1080) {
      lectureMain.style.removeProperty("--lecture-left-pane-width");
      lectureMain.style.removeProperty("--lecture-right-pane-min");
      return;
    }
    const mainRect = lectureMain.getBoundingClientRect();
    const paneRect = slidePane.getBoundingClientRect();
    const frameRect = slideFrame.getBoundingClientRect();
    const canvasRect = imageContainer.getBoundingClientRect();
    if (mainRect.width <= 0 || paneRect.width <= 0 || frameRect.height <= 0) { return; }
    const computed = window.getComputedStyle(lectureMain);
    const gap = Number.parseFloat(computed.columnGap || computed.gap || "8") || 8;
    const usableWidth = Math.max(0, mainRect.width - gap);
    if (usableWidth <= 0) { return; }
    const imageEl = mainImageRef.current;
    const sourceWidth = Number(currentSlide?.image_width) || imageEl?.naturalWidth || 0;
    const sourceHeight = Number(currentSlide?.image_height) || imageEl?.naturalHeight || 0;
    const aspect = sourceWidth > 1 && sourceHeight > 1 ? clamp(sourceWidth / sourceHeight, 0.6, 2.2) : 4 / 3;
    const canvasHeight = canvasRect.height > 0 ? canvasRect.height : frameRect.height;
    const contentWidth = canvasRect.width > 0 ? canvasRect.width : frameRect.width;
    const slidePaneChromeX = Math.max(0, paneRect.width - contentWidth);
    const targetLeftFromHeight = Math.round(canvasHeight * aspect + slidePaneChromeX);
    const rightMinByRatio = Math.round(usableWidth * 0.2);
    const rightMinPx = Math.max(280, rightMinByRatio);
    const leftMinByRatio = Math.round(usableWidth * 0.5);
    const leftMaxByRatio = Math.round(usableWidth * 0.8);
    const leftMaxByRightMin = Math.max(0, usableWidth - rightMinPx);
    const leftMax = Math.max(0, Math.min(leftMaxByRatio, leftMaxByRightMin));
    if (leftMax <= 0) {
      lectureMain.style.removeProperty("--lecture-left-pane-width");
      lectureMain.style.removeProperty("--lecture-right-pane-min");
      return;
    }
    const leftMin = Math.min(leftMinByRatio, leftMax);
    const leftWidth = Math.max(leftMin, Math.min(targetLeftFromHeight, leftMax));
    lectureMain.style.setProperty("--lecture-left-pane-width", `${leftWidth}px`);
    lectureMain.style.setProperty("--lecture-right-pane-min", `${rightMinPx}px`);
  }, [currentSlide]);

  const scheduleAdaptiveLectureSplit = useCallback(() => {
    if (splitRafRef.current) { cancelAnimationFrame(splitRafRef.current); }
    splitRafRef.current = requestAnimationFrame(() => { splitRafRef.current = 0; updateAdaptiveLectureSplit(); });
  }, [updateAdaptiveLectureSplit]);

  const pausePlaybackForManualNavigation = useCallback(() => {
    const audio = audioRef.current;
    if (audio && !audio.paused) { audio.pause(); }
    setIsPlaying(false);
  }, []);

  const seekAudioToCurrentUiPosition = useCallback(
    ({ nextSlideIndex = currentSlideIndexRef.current, nextStepIndex = currentStepIndexRef.current } = {}) => {
      const audio = audioRef.current;
      if (!audio || !lecture?.audio_url) { return; }
      const targetMs = getCurrentGlobalMs(playbackTimelineRef.current.timeline, nextSlideIndex, nextStepIndex);
      audio.currentTime = Math.max(0, targetMs / 1000);
      setAudioClockMs(targetMs);
    },
    [lecture?.audio_url]
  );

  const goToSlide = useCallback(
    (index, options = {}) => {
      const { pause = true, seekAudio = true, resetQaTarget = false } = options;
      if (!slides.length) { return; }
      const safeIndex = clamp(index, 0, slides.length - 1);
      if (pause) { pausePlaybackForManualNavigation(); }
      setCurrentSlideIndex(safeIndex);
      setCurrentStepIndex(0);
      setMainImageName("");
      if (resetQaTarget) { setQaActiveLineSlideId(""); }
      if (seekAudio) {
        requestAnimationFrame(() => { seekAudioToCurrentUiPosition({ nextSlideIndex: safeIndex, nextStepIndex: 0 }); });
      }
    },
    [slides.length, pausePlaybackForManualNavigation, seekAudioToCurrentUiPosition]
  );

  const goToStep = useCallback(
    (index, options = {}) => {
      const { pause = true, seekAudio = true } = options;
      if (!currentSteps.length) { setCurrentStepIndex(0); return; }
      const safeIndex = clamp(index, 0, currentSteps.length - 1);
      if (pause) { pausePlaybackForManualNavigation(); }
      setCurrentStepIndex(safeIndex);
      if (seekAudio) {
        requestAnimationFrame(() => { seekAudioToCurrentUiPosition({ nextSlideIndex: currentSlideIndexRef.current, nextStepIndex: safeIndex }); });
      }
    },
    [currentSteps.length, pausePlaybackForManualNavigation, seekAudioToCurrentUiPosition]
  );

  const applyLecturePayload = useCallback((payload, options = {}) => {
    const preservePosition = Boolean(options.preservePosition);
    const normalized = normalizeLecturePayload(payload);
    if (!normalized || typeof normalized !== "object") { return; }
    let nextSlideIndex = 0;
    let nextStepIndex = 0;
    if (preservePosition && lectureRef.current) {
      const previousLecture = lectureRef.current;
      const previousSlide = previousLecture?.slides?.[currentSlideIndexRef.current];
      const previousSlideNumber = Number(previousSlide?.slide_number);
      if (Number.isFinite(previousSlideNumber) && Array.isArray(normalized.slides)) {
        const foundSlide = normalized.slides.findIndex((slide) => Number(slide?.slide_number) === previousSlideNumber);
        if (foundSlide >= 0) { nextSlideIndex = foundSlide; }
      }
      const oldTimeline = playbackTimelineRef.current;
      const previousGlobalMs = getCurrentGlobalMs(oldTimeline.timeline, currentSlideIndexRef.current, currentStepIndexRef.current);
      const newTimeline = buildPlaybackTimeline(normalized);
      if (previousGlobalMs > 0) {
        const location = locateTimelinePosition(newTimeline.timeline, newTimeline.totalMs, previousGlobalMs);
        if (location) { nextSlideIndex = location.slideIndex; nextStepIndex = location.stepIndex; }
      }
    }
    setLecture(normalized);
    setCurrentSlideIndex(nextSlideIndex);
    setCurrentStepIndex(nextStepIndex);
    setMainImageName("");
    setLectureLoadError("");
  }, []);

  useEffect(() => {
    if (!router.isReady) { return; }
    if (!routeJobId) { setLecture(null); setLectureLoadError("No job id was provided."); setSlideMessage("No job id was provided."); return; }
    let cancelled = false;
    async function loadLecture() {
      setLectureLoadError(""); setSlideMessage("Loading slide...");
      try {
        const response = await fetch(buildApiUrl(`/api/jobs/${encodeURIComponent(routeJobId)}/lecture`));
        const payload = await response.json();
        if (!response.ok) { throw new Error(payload?.detail || `Request failed: ${response.status}`); }
        if (cancelled) { return; }
        applyLecturePayload(payload, { preservePosition: false });
      } catch (error) {
        if (cancelled) { return; }
        setLecture(null); setLectureLoadError(error?.message || "Unable to load lecture payload."); setSlideMessage(error?.message || "Unable to load lecture payload.");
      }
    }
    void loadLecture();
    return () => { cancelled = true; };
  }, [router.isReady, routeJobId, applyLecturePayload]);

  useEffect(() => {
    if (!routeJobId || !lecture) { return; }
    if (!shouldPollLecturePayload(lecture)) { return; }
    let cancelled = false; let inFlight = false;
    const refresh = async () => {
      if (cancelled || inFlight) { return; } inFlight = true;
      try {
        const response = await fetch(buildApiUrl(`/api/jobs/${encodeURIComponent(routeJobId)}/lecture`));
        const payload = await response.json();
        if (!response.ok || !payload || typeof payload !== "object") { return; }
        const previous = lectureRef.current;
        const changed = !previous || previous.audio_url !== payload.audio_url || slideStepCountSignature(previous) !== slideStepCountSignature(payload) || (Array.isArray(previous.slides) ? previous.slides.length : 0) !== (Array.isArray(payload.slides) ? payload.slides.length : 0);
        if (changed && !cancelled) { applyLecturePayload(payload, { preservePosition: true }); }
      } catch { /* keep previous */ } finally { inFlight = false; }
    };
    const interval = setInterval(() => { void refresh(); }, 8000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [routeJobId, lecture, applyLecturePayload]);

  useEffect(() => {
    if (!lecture || hasMarkedInitialLoad.current) { return; }
    hasMarkedInitialLoad.current = true;
    const holdTimer = setTimeout(() => {
      setContentRevealed(true);
      setOverlayExiting(true);
    }, 1000);
    const removeTimer = setTimeout(() => {
      setOverlayVisible(false);
    }, 1600);
    return () => { clearTimeout(holdTimer); clearTimeout(removeTimer); };
  }, [lecture]);

  useEffect(() => { if (!slides.length) { setCurrentSlideIndex(0); setCurrentStepIndex(0); return; } setCurrentSlideIndex((prev) => clamp(prev, 0, slides.length - 1)); }, [slides.length]);
  useEffect(() => { if (!currentSteps.length) { setCurrentStepIndex(0); return; } setCurrentStepIndex((prev) => clamp(prev, 0, currentSteps.length - 1)); }, [currentSteps.length, currentSlideIndex]);

  useEffect(() => {
    if (!currentSlide) { setMainImageSrc(""); setMainImageName(""); return; }
    const isScript = currentTab === TAB_SCRIPT;
    const targetSrc = isScript ? getRenderedStepUrl(currentSlide, currentStepIndex) || currentSlide.image_url || "" : currentSlide.image_url || "";
    const nextImageName = typeof currentSlide.image_name === "string" ? currentSlide.image_name : "";
    if (mainImageName !== nextImageName || mainImageSrc !== targetSrc) { setSlideMessage("Loading slide..."); }
    setMainImageName(nextImageName); setMainImageSrc(targetSrc);
    const renderedUrls = Array.isArray(currentSlide.rendered_step_urls) ? currentSlide.rendered_step_urls : [];
    renderedUrls.forEach((url) => { if (!url || preloadedUrlsRef.current.has(url)) { return; } const image = new Image(); image.src = url; preloadedUrlsRef.current.add(url); });
  }, [currentSlide, currentStepIndex, currentTab, mainImageName, mainImageSrc]);

  useEffect(() => { updateImageGeometry(); scheduleAdaptiveLectureSplit(); }, [currentSlide, currentStepIndex, currentTab, mainImageSrc, updateImageGeometry, scheduleAdaptiveLectureSplit]);

  useEffect(() => {
    const onResize = () => { updateImageGeometry(); scheduleAdaptiveLectureSplit(); };
    window.addEventListener("resize", onResize);
    let observer = null;
    if (window.ResizeObserver && imageContainerRef.current) {
      observer = new ResizeObserver(() => { updateImageGeometry(); scheduleAdaptiveLectureSplit(); });
      observer.observe(imageContainerRef.current);
    }
    return () => { window.removeEventListener("resize", onResize); if (observer) { observer.disconnect(); } };
  }, [updateImageGeometry, scheduleAdaptiveLectureSplit]);

  useEffect(() => { const audio = audioRef.current; if (!audio) { return; } audio.playbackRate = playbackRate; }, [playbackRate, lecture?.audio_url]);
  useEffect(() => { if (currentTab === TAB_SCRIPT) { return; } if (!isPlaying) { return; } const audio = audioRef.current; if (audio && !audio.paused) { audio.pause(); } setIsPlaying(false); }, [currentTab, isPlaying]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !lecture?.audio_url) { return; }
    const onLoadedMetadata = () => { setAudioDurationMs(Math.max(0, Math.round(audio.duration * 1000))); setAudioClockMs(Math.max(0, Math.round(audio.currentTime * 1000))); };
    const onTimeUpdate = () => {
      const currentMs = Math.max(0, Math.round(audio.currentTime * 1000)); setAudioClockMs(currentMs);
      const location = locateTimelinePosition(playbackTimelineRef.current.timeline, playbackTimelineRef.current.totalMs, currentMs);
      if (!location) { return; }
      setCurrentSlideIndex((prev) => (prev === location.slideIndex ? prev : location.slideIndex));
      setCurrentStepIndex((prev) => (prev === location.stepIndex ? prev : location.stepIndex));
    };
    const onEnded = () => { setIsPlaying(false); setAudioClockMs(Math.max(0, Math.round(audio.currentTime * 1000))); };
    const onSeeked = () => { setAudioClockMs(Math.max(0, Math.round(audio.currentTime * 1000))); };
    const onError = () => { setIsPlaying(false); };
    audio.addEventListener("loadedmetadata", onLoadedMetadata); audio.addEventListener("timeupdate", onTimeUpdate); audio.addEventListener("ended", onEnded); audio.addEventListener("seeked", onSeeked); audio.addEventListener("error", onError);
    return () => { audio.removeEventListener("loadedmetadata", onLoadedMetadata); audio.removeEventListener("timeupdate", onTimeUpdate); audio.removeEventListener("ended", onEnded); audio.removeEventListener("seeked", onSeeked); audio.removeEventListener("error", onError); };
  }, [lecture?.audio_url]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !lecture?.audio_url) { return; }
    if (isPlaying) { void audio.play().catch(() => { setIsPlaying(false); }); return; }
    if (!audio.paused) { audio.pause(); }
  }, [isPlaying, lecture?.audio_url]);

  useEffect(() => {
    if (!isPlaying || currentTab !== TAB_SCRIPT) { return; }
    if (lecture?.audio_url && audioRef.current) { return; }
    if (!currentSteps.length) { setIsPlaying(false); return; }
    const dwellMs = normalizeDwellMs(currentStep);
    const adjustedDwellMs = Math.max(120, Math.round(dwellMs / playbackRate));
    const timer = setTimeout(() => {
      const nextStep = currentStepIndex + 1;
      if (nextStep < currentSteps.length) { setCurrentStepIndex(nextStep); return; }
      const nextSlide = currentSlideIndex + 1;
      if (nextSlide < slides.length) { setCurrentSlideIndex(nextSlide); setCurrentStepIndex(0); return; }
      setIsPlaying(false);
    }, adjustedDwellMs);
    return () => { clearTimeout(timer); };
  }, [isPlaying, currentTab, lecture?.audio_url, currentSteps, currentStep, currentStepIndex, currentSlideIndex, playbackRate, slides.length]);

  useEffect(() => {
    if (qaRequestSeqRef.current === 0) { return; } if (currentTab !== TAB_QA) { return; }
    if (qaScrollRafRef.current) { cancelAnimationFrame(qaScrollRafRef.current); }
    qaScrollRafRef.current = requestAnimationFrame(() => { qaScrollRafRef.current = 0; const thread = qaThreadRef.current; if (!thread) { return; } thread.scrollTop = thread.scrollHeight; });
  }, [qaTurns, currentTab]);

  useEffect(() => { return () => { if (qaAbortRef.current) { qaAbortRef.current.abort(); } if (qaScrollRafRef.current) { cancelAnimationFrame(qaScrollRafRef.current); } if (splitRafRef.current) { cancelAnimationFrame(splitRafRef.current); } }; }, []);

  const updateQaTurn = useCallback((turnId, updater) => {
    setQaTurns((prev) => prev.map((turn) => { if (!turn || turn.id !== turnId) { return turn; } if (typeof updater === "function") { return updater(turn); } return { ...turn, ...updater }; }));
  }, []);

  const handleSubmitQa = useCallback(async () => {
    if (!routeJobId) { return; }
    const question = (qaQuestion || "").trim();
    if (!question) { setQaError("Enter a question first."); return; }
    if (qaAbortRef.current) { qaAbortRef.current.abort(); qaAbortRef.current = null; }
    const controller = new AbortController(); qaAbortRef.current = controller;
    const requestSeq = qaRequestSeqRef.current + 1; qaRequestSeqRef.current = requestSeq;
    const turnId = `qa-${requestSeq}-${Date.now()}`;
    setQaTurns((prev) => [...prev, { id: turnId, question, status: "loading", result: null, error: "", progressMessage: "Preparing request...", streamText: "" }]);
    setQaQuestion(""); setQaError(""); setQaLoading(true); setQaActiveTurnId(turnId); setQaActiveLineIndex(-1); setQaActiveLineSlideId("");
    try {
      const payload = await streamQaAnswer({
        jobId: routeJobId, question, signal: controller.signal,
        onProgress: (progress) => { updateQaTurn(turnId, (turn) => { if (!turn || turn.status !== "loading") { return turn; } return { ...turn, progressMessage: getQaProgressLabel(progress) }; }); },
        onDelta: (deltaText) => { updateQaTurn(turnId, (turn) => { if (!turn || turn.status !== "loading") { return turn; } return { ...turn, streamText: `${typeof turn.streamText === "string" ? turn.streamText : ""}${deltaText}` }; }); },
        onResult: () => { updateQaTurn(turnId, (turn) => { if (!turn || turn.status !== "loading") { return turn; } return { ...turn, progressMessage: "Finalizing response..." }; }); },
      });
      if (requestSeq !== qaRequestSeqRef.current) { return; }
      setQaTurns((prev) => prev.map((turn) => { if (!turn || turn.id !== turnId) { return turn; } return { ...turn, status: "done", result: payload, error: "", streamText: "" }; }));
      setQaActiveTurnId(turnId); setQaActiveLineIndex(-1); setQaActiveLineSlideId(""); setQaError("");
    } catch (error) {
      if (requestSeq !== qaRequestSeqRef.current) { return; } if (error?.name === "AbortError") { return; }
      const message = error?.message || "Unable to answer the question.";
      setQaTurns((prev) => prev.map((turn) => { if (!turn || turn.id !== turnId) { return turn; } return { ...turn, status: "error", error: message, result: null }; }));
      setQaError(message);
    } finally { if (requestSeq === qaRequestSeqRef.current) { setQaLoading(false); qaAbortRef.current = null; } }
  }, [qaQuestion, routeJobId, updateQaTurn]);

  const activateQaAnswerLine = useCallback(
    (lineIndex, turnId, slideId) => {
      const turn = qaTurns.find((item) => item && item.id === turnId);
      const lines = Array.isArray(turn?.result?.answer_lines) ? turn.result.answer_lines : [];
      const line = lines[lineIndex];
      if (!line) { return; }
      setQaActiveTurnId(turnId); setQaActiveLineIndex(lineIndex); setQaActiveLineSlideId(typeof slideId === "string" ? slideId : "");
      if (typeof slideId !== "string" || !slideId) { return; }
      const targetSlideIndex = slideIndexById.get(slideId);
      if (typeof targetSlideIndex !== "number") { return; }
      if (targetSlideIndex !== currentSlideIndexRef.current) { goToSlide(targetSlideIndex, { pause: true, seekAudio: true, resetQaTarget: false }); }
    },
    [qaTurns, slideIndexById, goToSlide]
  );

  const onTimelinePointerDown = useCallback(
    (event) => {
      if (event.button !== 0) { return; }
      if (event.target instanceof Element && event.target.closest(".slide-dot")) { return; }
      const rail = timelineRailRef.current;
      if (!rail) { return; }
      const getProgressFromX = (clientX) => { const rect = rail.getBoundingClientRect(); if (!rect.width) { return 0; } const x = Math.max(0, Math.min(rect.width, clientX - rect.left)); return x / rect.width; };
      const scrubToProgress = (progressRatio) => {
        const progress = clamp01(progressRatio);
        const maxTimelineMs = lecture?.audio_url && audioRef.current && Number.isFinite(audioRef.current.duration) && audioRef.current.duration > 0 ? audioRef.current.duration * 1000 : playbackTimeline.totalMs;
        if (maxTimelineMs > 0) {
          const location = locateTimelinePosition(playbackTimeline.timeline, playbackTimeline.totalMs, progress * maxTimelineMs);
          if (location) { setCurrentSlideIndex(location.slideIndex); setCurrentStepIndex(location.stepIndex); seekAudioToCurrentUiPosition({ nextSlideIndex: location.slideIndex, nextStepIndex: location.stepIndex }); return; }
        }
        if (slides.length > 0) { const targetSlideIndex = Math.round(progress * Math.max(0, slides.length - 1)); setCurrentSlideIndex(targetSlideIndex); setCurrentStepIndex(0); seekAudioToCurrentUiPosition({ nextSlideIndex: targetSlideIndex, nextStepIndex: 0 }); }
      };
      pausePlaybackForManualNavigation(); timelineScrubPointerIdRef.current = event.pointerId;
      try { rail.setPointerCapture(event.pointerId); } catch { /* ignore */ }
      scrubToProgress(getProgressFromX(event.clientX));
    },
    [lecture?.audio_url, playbackTimeline.timeline, playbackTimeline.totalMs, slides.length, pausePlaybackForManualNavigation, seekAudioToCurrentUiPosition]
  );

  const onTimelinePointerMove = useCallback(
    (event) => {
      const pointerId = timelineScrubPointerIdRef.current;
      if (pointerId == null || event.pointerId !== pointerId) { return; }
      const rail = timelineRailRef.current;
      if (!rail) { return; }
      const rect = rail.getBoundingClientRect();
      if (!rect.width) { return; }
      const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
      const progress = x / rect.width;
      const maxTimelineMs = lecture?.audio_url && audioRef.current && Number.isFinite(audioRef.current.duration) && audioRef.current.duration > 0 ? audioRef.current.duration * 1000 : playbackTimeline.totalMs;
      if (maxTimelineMs > 0) {
        const location = locateTimelinePosition(playbackTimeline.timeline, playbackTimeline.totalMs, progress * maxTimelineMs);
        if (location) { setCurrentSlideIndex(location.slideIndex); setCurrentStepIndex(location.stepIndex); seekAudioToCurrentUiPosition({ nextSlideIndex: location.slideIndex, nextStepIndex: location.stepIndex }); }
        return;
      }
      if (slides.length > 0) { const targetSlideIndex = Math.round(progress * Math.max(0, slides.length - 1)); setCurrentSlideIndex(targetSlideIndex); setCurrentStepIndex(0); seekAudioToCurrentUiPosition({ nextSlideIndex: targetSlideIndex, nextStepIndex: 0 }); }
    },
    [lecture?.audio_url, playbackTimeline.timeline, playbackTimeline.totalMs, seekAudioToCurrentUiPosition, slides.length]
  );

  const endTimelineScrub = useCallback((event) => {
    const pointerId = timelineScrubPointerIdRef.current;
    if (pointerId == null) { return; } if (event && event.pointerId !== pointerId) { return; }
    const rail = timelineRailRef.current;
    if (rail) { try { if (rail.hasPointerCapture?.(pointerId)) { rail.releasePointerCapture(pointerId); } } catch { /* ignore */ } }
    timelineScrubPointerIdRef.current = null;
  }, []);

  const handleMainImageLoad = useCallback(() => { setSlideMessage(""); updateImageGeometry(); scheduleAdaptiveLectureSplit(); }, [updateImageGeometry, scheduleAdaptiveLectureSplit]);
  const handleMainImageError = useCallback(() => { setSlideMessage("Slide image could not be loaded."); updateImageGeometry(); scheduleAdaptiveLectureSplit(); }, [updateImageGeometry, scheduleAdaptiveLectureSplit]);

  const handlePlayClick = useCallback(async () => {
    if (!lecture || currentTab !== TAB_SCRIPT) { return; }
    const audio = audioRef.current;
    const hasNarrationAudio = Boolean(lecture.audio_url && audio);
    if (hasNarrationAudio) {
      if (isPlaying) { audio.pause(); setIsPlaying(false); return; }
      if (audio.ended) { audio.currentTime = 0; setCurrentSlideIndex(0); setCurrentStepIndex(0); }
      setIsPlaying(true); try { await audio.play(); } catch { setIsPlaying(false); } return;
    }
    if (isPlaying) { setIsPlaying(false); return; }
    if (!currentSteps.length) { setIsPlaying(false); return; }
    setIsPlaying(true);
  }, [lecture, currentTab, isPlaying, currentSteps.length]);

  const handleTabChange = useCallback(
    (nextTab) => {
      if (nextTab === currentTab) { return; }
      if (isPlaying) { pausePlaybackForManualNavigation(); }
      setCurrentTab(nextTab); setSearchFlash(false); scheduleAdaptiveLectureSplit();
    },
    [currentTab, isPlaying, pausePlaybackForManualNavigation, scheduleAdaptiveLectureSplit]
  );

  const handleScriptSearchClick = useCallback(() => {
    if (currentTab !== TAB_SCRIPT) { return; }
    setSearchFlash(true); window.setTimeout(() => { setSearchFlash(false); }, 1400);
  }, [currentTab]);

  const handleExportVideo = useCallback(async () => {
    if (!routeJobId || videoExportLoading) { return; }
    setVideoExportLoading(true);
    try {
      const response = await fetch(buildApiUrl(`/api/jobs/${encodeURIComponent(routeJobId)}/export-video`), {
        method: "POST",
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok) {
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `Video export failed: ${response.status}`);
      }
      const backendOrigin = getLocalBackendOrigin();
      const videoUrl = absolutizeApiUrl(typeof payload?.video_url === "string" ? payload.video_url : "", backendOrigin);
      if (!videoUrl) {
        throw new Error("Video export completed, but no download URL was returned.");
      }
      const anchor = document.createElement("a");
      anchor.href = videoUrl;
      anchor.rel = "noopener";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      window.alert(error?.message || "Unable to export narrated video.");
    } finally {
      setVideoExportLoading(false);
    }
  }, [routeJobId, videoExportLoading]);

  const downloadPdfHref = typeof lecture?.input_pdf_url === "string" && lecture.input_pdf_url ? lecture.input_pdf_url : "#";
  const hasSlides = slides.length > 0;
  const playbackProgressPct = `${(playbackProgress * 100).toFixed(2)}%`;

  const TABS = [
    { id: TAB_SCRIPT, label: "Script" },
    { id: TAB_QA, label: "Q&A" },
    { id: TAB_NOTES, label: "Notes" },
  ];

  const showOverlay = overlayVisible && !lectureLoadError;

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>SlideParser - Lecture View</title>
      </Head>

      <div
        className="relative h-dvh grid grid-rows-[auto_minmax(0,1fr)] overflow-hidden bg-background"
        style={{ "--playback-progress": String(playbackProgress), "--playback-progress-pct": playbackProgressPct }}
      >
        {/* ── Loading Overlay ── */}
        {showOverlay && (
          <div className={cn(
            "lec-loading-overlay absolute inset-0 z-50 flex flex-col items-center justify-center",
            overlayExiting && "lec-overlay-exit"
          )}>
            <div className="flex flex-col items-center gap-6">
              {/* Logo with breathing glow */}
              <div className="relative">
                <div className="absolute -inset-4 rounded-full bg-primary/10 blur-xl" style={{ animation: "lec-logo-breathe 2.4s ease-in-out infinite" }} />
                <div className="relative w-16 h-16 rounded-2xl overflow-hidden shadow-lg ring-1 ring-border/50" style={{ animation: "lec-logo-breathe 2.4s ease-in-out infinite" }}>
                  <img src="/slideparser_logo.png" alt="" className="block w-full h-full object-contain" />
                </div>
              </div>

              {/* Spinner ring */}
              <div className="lec-loading-ring w-8 h-8 rounded-full" />

              {/* Loading dots */}
              <div className="flex items-center gap-1.5">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className="lec-loading-dot w-1.5 h-1.5 rounded-full bg-muted-foreground/60"
                    style={{ animationDelay: `${i * 160}ms` }}
                  />
                ))}
              </div>

              {/* Progress bar */}
              <div className="lec-loading-bar-track w-48 h-[3px] bg-border/60 mt-1" />

              <p className="text-sm font-medium text-muted-foreground/70 tracking-wide">
                Preparing your lecture
              </p>
            </div>
          </div>
        )}

        {/* Topbar */}
        <header className={cn("relative z-10 flex items-center gap-4 px-5 py-2.5 border-b bg-card", !contentRevealed && "opacity-0", contentRevealed && "lec-reveal-header")}>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center overflow-hidden">
              <img
                src="/slideparser_logo.png"
                alt="SlideParser logo"
                className="block w-full h-full object-contain"
                loading="eager"
              />
            </div>
            <span className="font-extrabold text-sm tracking-tight">SlideParser</span>
          </div>

          <div className="flex items-center gap-2 ml-auto">
            <Button variant="outline" size="sm" className="rounded-full" asChild>
              <Link href="/"><ChevronLeft className="w-4 h-4 mr-1" />My Lectures</Link>
            </Button>
            <Button
              variant="default"
              size="sm"
              className="rounded-full"
              onClick={handleExportVideo}
              disabled={!routeJobId || videoExportLoading}
            >
              <Download className="w-4 h-4 mr-1.5" />
              {videoExportLoading ? "Exporting..." : "Narrate + Export Video"}
            </Button>
            <Button variant="outline" size="sm" className="rounded-full" asChild>
              <a href={downloadPdfHref} target="_blank" rel="noopener"><Download className="w-4 h-4 mr-1.5" />Download PDF</a>
            </Button>
            <Button variant="outline" size="icon" className="rounded-full w-8 h-8" aria-label="More options">
              <MoreVertical className="w-4 h-4" />
            </Button>
          </div>
        </header>

        {/* Main layout */}
        <main ref={lectureMainRef} className="relative z-[1] min-h-0 grid grid-cols-[minmax(0,1fr)_420px] overflow-hidden max-[1080px]:grid-cols-1 max-[1080px]:grid-rows-[minmax(200px,1fr)_minmax(0,1fr)]">
          {/* Player column */}
          <div className="min-h-0 flex flex-col overflow-y-auto bg-background scrollbar-thin">
            {/* Slide pane */}
            <section ref={slidePaneRef} className={cn("relative z-[1] flex-1 min-h-[240px] flex items-center justify-center bg-black max-[1080px]:min-h-[180px]", !contentRevealed && "opacity-0", contentRevealed && "lec-reveal-slide")}>
              <div ref={slideFrameRef} className="relative w-full h-full min-h-0 overflow-hidden flex items-center justify-center">
                <div ref={imageContainerRef} className="relative w-full h-full min-h-0 flex items-center justify-center overflow-hidden">
                  <img
                    ref={mainImageRef}
                    className="relative z-[1] !w-auto !h-auto max-w-full max-h-full object-contain block bg-black"
                    alt="Slide preview"
                    src={mainImageSrc || undefined}
                    onLoad={handleMainImageLoad}
                    onError={handleMainImageError}
                  />
                  <div className="highlight-overlay" aria-hidden="true">
                    {overlayShapes.boxes.map((box) => (
                      <div
                        key={box.key}
                        className={`highlight-box ${box.kind} ${box.phase === "exiting" ? "exiting" : "active"}`}
                        data-id={box.id}
                        style={{ left: box.left, top: box.top, width: box.width, height: box.height, zIndex: 3 }}
                      />
                    ))}
                    {overlayShapes.underlays.map((layer) => (
                      <div
                        key={layer.key}
                        className={`highlight-lift-underlay visual ${
                          layer.phase === "exiting" ? "exiting" : "active"
                        }`}
                        data-id={layer.id}
                        style={{ clipPath: layer.clipPath, WebkitClipPath: layer.clipPath }}
                      />
                    ))}
                    {overlayShapes.lifts.map((layer) => (
                      <div
                        key={layer.key}
                        className={`highlight-lift visual ${layer.phase === "exiting" ? "exiting" : "active"}`}
                        data-id={layer.id}
                        style={{
                          clipPath: layer.clipPath,
                          WebkitClipPath: layer.clipPath,
                          backgroundImage: mainImageSrc ? `url(${mainImageSrc})` : "none",
                          backgroundSize: imageGeometry
                            ? `${imageGeometry.width}px ${imageGeometry.height}px`
                            : "auto",
                          backgroundPosition: imageGeometry
                            ? `${imageGeometry.offsetX}px ${imageGeometry.offsetY}px`
                            : "0 0",
                        }}
                      />
                    ))}
                    {overlayShapes.polygons.length > 0 && (
                      <svg style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", pointerEvents: "none", zIndex: 4 }}>
                        {overlayShapes.polygons.map((poly) => (
                          <path
                            key={poly.key}
                            className={`highlight-polygon ${poly.kind} ${
                              poly.phase === "exiting" ? "exiting" : "active"
                            }`}
                            data-id={poly.id}
                            d={poly.pathD}
                            style={{ "--poly-len": String(poly.len || 240) }}
                          />
                        ))}
                      </svg>
                    )}
                  </div>
                </div>
                {slideMessage && <p className="absolute inset-x-4 bottom-4 m-0 rounded-md px-3.5 py-2.5 text-center text-sm font-medium text-white/95 bg-black/70 backdrop-blur-sm">{slideMessage}</p>}
              </div>
            </section>

            {/* Timeline rail */}
            <div
              ref={timelineRailRef}
              className={cn("timeline-rail relative shrink-0 h-[3px] bg-muted cursor-pointer z-[5] hover:h-[5px] after:bg-primary", !contentRevealed && "opacity-0", contentRevealed && "lec-reveal-timeline")}
              aria-label="Slide timeline"
              style={{ "--timeline-progress": String(playbackProgress), "--timeline-progress-pct": playbackProgressPct }}
              onPointerDown={onTimelinePointerDown}
              onPointerMove={onTimelinePointerMove}
              onPointerUp={endTimelineScrub}
              onPointerCancel={endTimelineScrub}
              onLostPointerCapture={() => { timelineScrubPointerIdRef.current = null; }}
            />

            {/* Player controls */}
            <div className={cn("shrink-0 flex items-center gap-2 px-5 py-2 bg-card", !contentRevealed && "opacity-0", contentRevealed && "lec-reveal-controls")}>
              <div className="inline-flex items-center gap-0.5">
                <Button variant="ghost" size="icon" className="w-9 h-9 rounded-full" aria-label="Previous slide" onClick={() => { goToSlide(currentSlideIndex - 1, { pause: true, seekAudio: true, resetQaTarget: false }); }}>
                  <SkipBack className="w-4 h-4" />
                </Button>
                <Button size="icon" className="w-10 h-10 rounded-full" aria-label={isPlaying ? "Pause script" : "Play script"} onClick={() => { void handlePlayClick(); }}>
                  {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
                </Button>
                <Button variant="ghost" size="icon" className="w-9 h-9 rounded-full" aria-label="Next slide" onClick={() => { goToSlide(currentSlideIndex + 1, { pause: true, seekAudio: true, resetQaTarget: false }); }}>
                  <SkipForward className="w-4 h-4" />
                </Button>
              </div>

              <span className="text-xs font-medium text-muted-foreground whitespace-nowrap tabular-nums">{trackTimeText}</span>
              <Badge variant="secondary" className="text-[0.72rem] font-medium">{slideCounterText}</Badge>

              <div className="flex items-center gap-2 ml-auto min-w-0">
                <span className="min-w-[36px] text-right font-semibold text-xs text-muted-foreground tabular-nums">{playbackRate.toFixed(1)}x</span>
                <input
                  id="speedRange"
                  type="range"
                  min="50"
                  max="500"
                  step="5"
                  value={Math.round(playbackRate * 100)}
                  aria-label="Playback speed"
                  className="w-20 max-w-full accent-primary bg-transparent"
                  onChange={(event) => {
                    const raw = Number.parseFloat(event.target.value || "100");
                    const nextRate = clamp(raw / 100, 0.5, 5.0);
                    setPlaybackRate(nextRate);
                  }}
                />
              </div>
            </div>

            {/* Player meta */}
            <div className={cn("shrink-0 px-5 pt-3.5 pb-1.5 bg-card", !contentRevealed && "opacity-0", contentRevealed && "lec-reveal-meta")}>
              <p className="m-0 font-bold text-base leading-snug tracking-tight">{trackTitle}</p>
            </div>

            {/* Thumbnail strip */}
            <div className={cn("shrink-0 px-5 py-2.5 pb-4 bg-card border-t max-[1080px]:hidden", !contentRevealed && "opacity-0", contentRevealed && "lec-reveal-thumbs")}>
              <div className="flex flex-nowrap gap-2.5 items-center overflow-x-auto overflow-y-hidden py-1 scrollbar-thin">
                {slides.map((slide, idx) => {
                  const slideNumber = Number.isFinite(Number(slide?.slide_number)) ? Number(slide.slide_number) : idx + 1;
                  const thumbUrl = (typeof slide?.thumbnail_url === "string" && slide.thumbnail_url) || (typeof slide?.image_url === "string" && slide.image_url) || "";
                  return (
                    <button
                      key={`${getSlideId(slide) || "slide"}:${idx}`}
                      type="button"
                      className={cn(
                        "slide-dot relative shrink-0 w-[140px] h-[80px] border-2 rounded-lg bg-muted p-0 overflow-hidden cursor-pointer transition-all",
                        idx === currentSlideIndex
                          ? "border-primary ring-1 ring-primary"
                          : "border-transparent hover:border-muted-foreground/30 hover:-translate-y-0.5"
                      )}
                      title={`Slide ${slideNumber}`}
                      aria-label={`Go to slide ${slideNumber}`}
                      onClick={() => { goToSlide(idx, { pause: true, seekAudio: true, resetQaTarget: false }); }}
                    >
                      <span className={cn("block w-full h-full bg-muted", !thumbUrl && "grid place-items-center text-muted-foreground text-sm font-bold")}>
                        {thumbUrl ? <img className="block w-full h-full object-contain object-center bg-background" src={thumbUrl} alt="" loading="lazy" decoding="async" /> : String(slideNumber)}
                      </span>
                      <span className="absolute right-1.5 bottom-1.5 z-[1] min-w-[20px] h-[18px] rounded px-1.5 inline-flex items-center justify-center text-[0.6rem] font-bold text-white bg-black/65 backdrop-blur-sm">
                        {slideNumber}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Sidebar */}
          <aside className={cn("min-h-0 grid grid-rows-[auto_minmax(0,1fr)] overflow-hidden border-l bg-card max-[1080px]:border-l-0 max-[1080px]:border-t", !contentRevealed && "opacity-0", contentRevealed && "lec-reveal-sidebar")}>
            {/* Sidebar tabs */}
            <div className="flex items-center gap-0 px-4 border-b overflow-x-auto scrollbar-none" role="tablist" aria-label="Panel tabs">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  className={cn(
                    "relative border-0 bg-transparent px-4 py-3 text-sm font-medium cursor-pointer whitespace-nowrap transition-colors text-muted-foreground hover:text-foreground",
                    currentTab === tab.id && "text-foreground font-semibold after:absolute after:bottom-[-1px] after:left-4 after:right-4 after:h-0.5 after:rounded-t after:bg-primary"
                  )}
                  type="button"
                  onClick={() => handleTabChange(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Panel content */}
            <div className={cn("min-h-0 overflow-auto p-2 px-3 pb-4 scrollbar-thin", currentTab === TAB_SCRIPT && "relative pl-1")}>
              {currentTab === TAB_SCRIPT && (
                <>
                  <div className="sticky top-0 z-10 px-2 pt-1 pb-2 bg-card">
                    <Button variant="outline" size="sm" className="w-full justify-start rounded-lg text-xs h-8 text-muted-foreground" onClick={handleScriptSearchClick}>
                      <Search className="w-3.5 h-3.5 mr-2 shrink-0" />Search transcript…
                    </Button>
                  </div>
                  {searchFlash && <p className="mx-2 my-2 text-sm text-muted-foreground">Search is a placeholder in this version.</p>}
                  {!hasSlides && !lectureLoadError && <p className="mx-2 my-4 text-sm text-muted-foreground">No slides are available for this lecture.</p>}
                  {lectureLoadError && <p className="mx-2 my-4 text-sm text-muted-foreground">{lectureLoadError}</p>}
                  {hasSlides && currentSteps.length === 0 && <p className="mx-2 my-4 text-sm text-muted-foreground">No script was generated for this slide.</p>}

                  {hasSlides && currentSteps.map((step, idx) => (
                    <button
                      key={`${currentSlide?.image_name || "slide"}:${idx}`}
                      type="button"
                      className={cn(
                        "w-full border-0 bg-transparent text-left p-2.5 px-3 mb-0.5 rounded-md cursor-pointer transition-colors border-l-[3px] border-l-transparent",
                        "grid grid-cols-[52px_minmax(0,74ch)] gap-x-2 items-baseline justify-start",
                        "hover:bg-accent",
                        idx === currentStepIndex && "bg-accent border-l-primary"
                      )}
                      data-step-index={idx}
                      onClick={() => { goToStep(idx, { pause: true, seekAudio: true }); }}
                      onMouseEnter={() => { if (!isPlaying && currentTab === TAB_SCRIPT) { setCurrentStepIndex(idx); } }}
                      onFocus={() => { if (!isPlaying && currentTab === TAB_SCRIPT) { setCurrentStepIndex(idx); } }}
                    >
                      <span className={cn("col-start-1 justify-self-end m-0 text-[0.72rem] font-semibold tabular-nums", idx === currentStepIndex ? "text-primary font-bold" : "text-muted-foreground")}>
                        {formatClock(step?.start_ms || 0)}
                      </span>
                      <p className="col-start-2 m-0 text-sm leading-relaxed break-words">{typeof step?.line === "string" ? step.line : ""}</p>
                    </button>
                  ))}
                </>
              )}

              {currentTab === TAB_QA && (
                <div className="min-h-full h-full grid grid-rows-[minmax(0,1fr)_auto] gap-2.5">
                  <div ref={qaThreadRef} className="min-h-0 flex flex-col gap-3 overflow-auto py-1 scrollbar-thin">
                    {qaTurns.length === 0 ? (
                      <div className="m-auto p-5 rounded-lg border border-dashed bg-muted/50 text-center">
                        <p className="m-0 text-sm font-bold">Ask anything about the slides</p>
                        <p className="mt-2 text-xs text-muted-foreground leading-relaxed">I retrieve the most relevant explanation text and return clickable highlights you can jump to.</p>
                      </div>
                    ) : (
                      qaTurns.map((turn) => {
                        const turnLines = Array.isArray(turn?.result?.answer_lines) ? turn.result.answer_lines : [];
                        return (
                          <div key={turn.id} className="grid gap-2.5">
                            {/* User message */}
                            <div className="grid justify-items-end">
                              <div className="max-w-[88%] rounded-2xl rounded-br-sm px-3.5 py-3 bg-primary text-primary-foreground">
                                <p className="m-0 mb-1 text-[0.62rem] font-bold tracking-wider uppercase opacity-65">You</p>
                                <p className="m-0 text-sm leading-relaxed">{typeof turn?.question === "string" ? turn.question : ""}</p>
                              </div>
                            </div>

                            {/* Loading */}
                            {turn.status === "loading" && (
                              <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2.5 items-start">
                                <Avatar className="w-7 h-7">
                                  <AvatarImage src="/slideparser_logo.png" alt="SlideParser logo" className="object-contain bg-background" />
                                  <AvatarFallback className="bg-primary text-primary-foreground text-[0.56rem] font-extrabold">SP</AvatarFallback>
                                </Avatar>
                                <div className="rounded-2xl rounded-bl-sm px-3.5 py-3 border bg-muted/50">
                                  <p className="m-0 mb-1 text-[0.62rem] font-bold tracking-wider uppercase text-muted-foreground">SlideParser</p>
                                  {typeof turn.streamText === "string" && turn.streamText.trim() ? (
                                    <p className="m-0 text-sm leading-relaxed whitespace-pre-wrap">{turn.streamText}</p>
                                  ) : (
                                    <p className="m-0 text-sm font-medium text-muted-foreground">{typeof turn.progressMessage === "string" && turn.progressMessage ? turn.progressMessage : "Running retrieval and generating an answer..."}</p>
                                  )}
                                </div>
                              </div>
                            )}

                            {/* Error */}
                            {turn.status === "error" && (
                              <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2.5 items-start">
                                <Avatar className="w-7 h-7">
                                  <AvatarImage src="/slideparser_logo.png" alt="SlideParser logo" className="object-contain bg-background" />
                                  <AvatarFallback className="bg-primary text-primary-foreground text-[0.56rem] font-extrabold">SP</AvatarFallback>
                                </Avatar>
                                <div className="rounded-2xl rounded-bl-sm px-3.5 py-3 border bg-muted/50">
                                  <p className="m-0 mb-1 text-[0.62rem] font-bold tracking-wider uppercase text-muted-foreground">SlideParser</p>
                                  <div className="m-0 p-2.5 rounded-md text-sm font-medium text-destructive bg-destructive/10 border border-destructive/20" role="alert">
                                    {typeof turn.error === "string" && turn.error ? turn.error : "Unable to answer the question."}
                                  </div>
                                </div>
                              </div>
                            )}

                            {/* Done */}
                            {turn.status === "done" && (
                              <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2.5 items-start">
                                <Avatar className="w-7 h-7">
                                  <AvatarImage src="/slideparser_logo.png" alt="SlideParser logo" className="object-contain bg-background" />
                                  <AvatarFallback className="bg-primary text-primary-foreground text-[0.56rem] font-extrabold">SP</AvatarFallback>
                                </Avatar>
                                <div className="rounded-2xl rounded-bl-sm px-3.5 py-3 border bg-muted/50">
                                  <p className="m-0 mb-1 text-[0.62rem] font-bold tracking-wider uppercase text-muted-foreground">SlideParser</p>
                                  {turnLines.length === 0 ? (
                                    <p className="m-0 text-sm leading-relaxed">{typeof turn?.result?.answer_text === "string" && turn.result.answer_text.trim() ? turn.result.answer_text.trim() : "I couldn't extract structured answer lines for this question."}</p>
                                  ) : (
                                    <div className="grid gap-1">
                                      {turnLines.map((line, idx) => {
                                        const isActive = turn.id === qaActiveTurnId && idx === qaActiveLineIndex && Boolean(qaActiveLineSlideId);
                                        const slideRefs = collectLineSlideRefs(line);
                                        return (
                                          <div key={`${turn.id}:line:${idx}`} className={cn("rounded-md overflow-hidden", isActive && "bg-accent")}>
                                            <div className={cn("flex items-end gap-2 flex-wrap p-2 rounded-md transition-colors hover:bg-accent/50", isActive && "bg-accent")}>
                                              <div className="flex-1 min-w-0">
                                                <p className="m-0 text-sm leading-relaxed break-words">{typeof line?.text === "string" ? line.text : ""}</p>
                                              </div>
                                              <div className="inline-flex flex-wrap gap-1 items-center ml-auto">
                                                {slideRefs.map((ref) => (
                                                  <button
                                                    key={`${turn.id}:line:${idx}:slide:${ref.slideId}`}
                                                    type="button"
                                                    className="border rounded-full px-2.5 py-0.5 text-[0.68rem] font-semibold cursor-pointer transition-colors text-primary bg-muted hover:bg-accent hover:border-primary"
                                                    onClick={() => { activateQaAnswerLine(idx, turn.id, ref.slideId); }}
                                                  >
                                                    {ref.label}
                                                  </button>
                                                ))}
                                              </div>
                                            </div>
                                          </div>
                                        );
                                      })}
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>

                  {/* Q&A form */}
                  <form
                    className="grid gap-2 p-3 rounded-lg border bg-card shadow-sm"
                    onSubmit={(event) => { event.preventDefault(); void handleSubmitQa(); }}
                  >
                    <label className="sr-only" htmlFor="qaQuestionInput">Ask a question about the lecture slides</label>
                    <Textarea
                      ref={qaTextareaRef}
                      id="qaQuestionInput"
                      className="min-h-[60px] max-h-[160px] resize-y text-sm"
                      rows={2}
                      placeholder="Ask about this lecture..."
                      value={qaQuestion}
                      disabled={qaLoading}
                      onChange={(event) => { setQaQuestion(event.target.value || ""); if (qaError) { setQaError(""); } }}
                      onKeyDown={(event) => { if (event.key !== "Enter" || event.shiftKey) { return; } event.preventDefault(); if (!qaLoading) { void handleSubmitQa(); } }}
                    />
                    <div className="grid gap-1.5">
                      <div className="flex items-center justify-between gap-2.5">
                        <Button type="button" variant="outline" size="icon" className="w-7 h-7 rounded-full" aria-label="More actions" onClick={(e) => e.currentTarget.blur()}>
                          <Plus className="w-3.5 h-3.5" />
                        </Button>
                        <Button type="submit" size="sm" className="rounded-full" disabled={qaLoading}>
                          {qaLoading ? "Thinking..." : <><Send className="w-3.5 h-3.5 mr-1.5" />Send</>}
                        </Button>
                      </div>
                      <p className="m-0 text-[0.7rem] leading-snug text-muted-foreground">Click a Slide badge to highlight referenced regions on that slide.</p>
                      {qaError && <p className="m-0 p-2.5 rounded-md text-sm font-medium text-destructive bg-destructive/10 border border-destructive/20" role="alert">{qaError}</p>}
                    </div>
                  </form>
                </div>
              )}

              {currentTab === TAB_NOTES && <p className="mx-2 my-4 text-sm text-muted-foreground">Notes view is a placeholder in this version.</p>}
            </div>
          </aside>
        </main>

        <audio ref={audioRef} src={typeof lecture?.audio_url === "string" ? lecture.audio_url : undefined} preload="metadata" hidden />
      </div>
    </>
  );
}

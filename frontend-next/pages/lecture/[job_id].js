import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const TAB_SCRIPT = "script";
const TAB_QA = "qa";
const TAB_NOTES = "notes";
const TAB_RESOURCES = "resources";

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

function toSvgPolygonPoints(points) {
  return points.map((pt) => `${pt[0]},${pt[1]}`).join(" ");
}

function toCssPolygonPoints(points) {
  return points.map((pt) => `${pt[0]}px ${pt[1]}px`).join(", ");
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

  const slides = Array.isArray(lecture?.slides) ? lecture.slides : [];
  const currentSlide = slides[currentSlideIndex] || null;
  const currentSteps = Array.isArray(currentSlide?.steps) ? currentSlide.steps : [];
  const currentStep = currentSteps[currentStepIndex] || null;

  const playbackTimeline = useMemo(() => buildPlaybackTimeline(lecture), [lecture]);

  useEffect(() => {
    lectureRef.current = lecture;
  }, [lecture]);

  useEffect(() => {
    currentSlideIndexRef.current = currentSlideIndex;
  }, [currentSlideIndex]);

  useEffect(() => {
    currentStepIndexRef.current = currentStepIndex;
  }, [currentStepIndex]);

  useEffect(() => {
    playbackTimelineRef.current = playbackTimeline;
  }, [playbackTimeline]);

  const slideIndexById = useMemo(() => {
    const map = new Map();
    slides.forEach((slide, idx) => {
      const id = getSlideId(slide);
      if (id) {
        map.set(id, idx);
      }
    });
    return map;
  }, [slides]);

  const playbackProgress = useMemo(() => {
    const audio = audioRef.current;
    if (audio && lecture?.audio_url && Number.isFinite(audio.duration) && audio.duration > 0) {
      return clamp01(audio.currentTime / audio.duration);
    }

    if (playbackTimeline.totalMs > 0) {
      const currentGlobalMs = getCurrentGlobalMs(
        playbackTimeline.timeline,
        currentSlideIndex,
        currentStepIndex
      );
      return clamp01(currentGlobalMs / playbackTimeline.totalMs);
    }

    if (slides.length <= 1) {
      return 0;
    }
    return clamp01(currentSlideIndex / (slides.length - 1));
  }, [
    currentSlideIndex,
    currentStepIndex,
    lecture?.audio_url,
    playbackTimeline.timeline,
    playbackTimeline.totalMs,
    slides.length,
    audioClockMs,
  ]);

  const trackTitle = useMemo(() => {
    if (!currentSlide) {
      return "Slide";
    }
    const slideNo = Number.isFinite(Number(currentSlide.slide_number))
      ? Number(currentSlide.slide_number)
      : currentSlideIndex + 1;
    const cleanTitle = sanitizeLectureTitle(lecture?.title, routeJobId);
    if (cleanTitle) {
      return `${cleanTitle} • Slide ${slideNo}`;
    }
    return `Slide ${slideNo}`;
  }, [currentSlide, currentSlideIndex, lecture?.title, routeJobId]);

  const stepCounterText = useMemo(() => {
    if (!currentSlide) {
      return "00:00 - 00:00";
    }

    const slideTimeline = playbackTimeline.timeline[currentSlideIndex] || null;
    const timelineStep =
      slideTimeline && Array.isArray(slideTimeline.steps) && slideTimeline.steps.length > 0
        ? slideTimeline.steps[clamp(currentStepIndex, 0, slideTimeline.steps.length - 1)]
        : null;

    const useTimeline = Boolean(lecture?.audio_url);
    const startMs = useTimeline
      ? Number(timelineStep?.startMs) || 0
      : Number(currentStep?.start_ms) || 0;

    const endMs = useTimeline
      ? Number(timelineStep?.endMs) || startMs
      : startMs + normalizeDwellMs(currentStep);

    return `${formatClock(startMs)} - ${formatClock(endMs)}`;
  }, [
    currentSlide,
    currentSlideIndex,
    currentStep,
    currentStepIndex,
    lecture?.audio_url,
    playbackTimeline.timeline,
  ]);

  const trackTimeText = useMemo(() => {
    const audio = audioRef.current;
    if (audio && lecture?.audio_url && Number.isFinite(audio.duration) && audio.duration > 0) {
      return `${formatClock(audioClockMs)} / ${formatClock(audioDurationMs)}`;
    }

    const startMs = getCurrentGlobalMs(playbackTimeline.timeline, currentSlideIndex, currentStepIndex);
    const fallbackTotalMs =
      playbackTimeline.totalMs > 0
        ? playbackTimeline.totalMs
        : currentSteps.reduce((sum, step) => sum + normalizeDwellMs(step), 0);
    return `${formatClock(startMs)} / ${formatClock(fallbackTotalMs)}`;
  }, [
    audioClockMs,
    audioDurationMs,
    currentSlideIndex,
    currentStepIndex,
    currentSteps,
    lecture?.audio_url,
    playbackTimeline.timeline,
    playbackTimeline.totalMs,
  ]);

  const slideCounterText = useMemo(() => {
    if (!currentSlide) {
      return "Slide 1";
    }
    const slideNo = Number.isFinite(Number(currentSlide.slide_number))
      ? Number(currentSlide.slide_number)
      : currentSlideIndex + 1;
    return `Slide ${slideNo}${slides.length ? ` of ${slides.length}` : ""}`;
  }, [currentSlide, currentSlideIndex, slides.length]);

  const activeTurn = useMemo(() => {
    if (!qaActiveTurnId) {
      return null;
    }
    return qaTurns.find((turn) => turn && turn.id === qaActiveTurnId) || null;
  }, [qaActiveTurnId, qaTurns]);

  const activeAnswerLines = useMemo(() => {
    const lines = activeTurn?.result?.answer_lines;
    return Array.isArray(lines) ? lines : [];
  }, [activeTurn]);

  const activeQaLine =
    qaActiveLineIndex >= 0 && qaActiveLineIndex < activeAnswerLines.length
      ? activeAnswerLines[qaActiveLineIndex]
      : null;

  const activeSeedRegionIds = useMemo(() => {
    if (!currentSlide) {
      return [];
    }

    if (currentTab === TAB_SCRIPT) {
      return Array.isArray(currentStep?.region_ids)
        ? currentStep.region_ids.filter((id) => typeof id === "string")
        : [];
    }

    if (currentTab === TAB_QA && activeQaLine) {
      const currentSlideId = getSlideId(currentSlide);
      if (!currentSlideId) {
        return [];
      }

      const targetSlideId = qaActiveLineSlideId || currentSlideId;
      if (targetSlideId !== currentSlideId) {
        return [];
      }

      const lineRegionIds = (Array.isArray(activeQaLine.highlights) ? activeQaLine.highlights : [])
        .filter((item) => item && item.slide_id === currentSlideId && typeof item.region_id === "string")
        .map((item) => item.region_id);

      const out = [];
      const seen = new Set();
      lineRegionIds.forEach((id) => {
        if (typeof id !== "string" || !id || seen.has(id)) {
          return;
        }
        seen.add(id);
        out.push(id);
      });
      return out;
    }

    return [];
  }, [currentSlide, currentStep, currentTab, activeQaLine, qaActiveLineSlideId]);

  const activeHighlightIds = useMemo(() => {
    if (!currentSlide || activeSeedRegionIds.length === 0) {
      return [];
    }
    const { visited } = resolveExpandedIdsForSlide(currentSlide, activeSeedRegionIds);
    return Array.from(visited);
  }, [currentSlide, activeSeedRegionIds]);

  const activeHighlightIdSet = useMemo(() => new Set(activeHighlightIds), [activeHighlightIds]);

  const overlayShapes = useMemo(() => {
    if (!currentSlide || !imageGeometry || activeHighlightIds.length === 0) {
      return {
        boxes: [],
        lifts: [],
        underlays: [],
        polygons: [],
      };
    }

    const allowTextHighlights = currentTab === TAB_QA;
    const boxes = [];
    const underlays = [];
    const lifts = [];
    const polygons = [];
    const pushBox = ({ id, kind, bbox, partIndex = 0 }) => {
      const scaled = mapScaledBbox(bbox, imageGeometry);
      if (!scaled) {
        return;
      }
      boxes.push({
        key: `${id}:box:${partIndex}`,
        id,
        kind: kind === "visual" ? "visual" : "text",
        left: scaled.left,
        top: scaled.top,
        width: scaled.width,
        height: scaled.height,
      });
    };

    const regions = Array.isArray(currentSlide.regions) ? currentSlide.regions : [];
    regions.forEach((region) => {
      const id = typeof region?.id === "string" ? region.id : "";
      if (!id || !activeHighlightIdSet.has(id)) {
        return;
      }

      const kind = String(region?.kind || "text").toLowerCase() === "visual" ? "visual" : "text";
      if (kind === "text" && !allowTextHighlights) {
        return;
      }

      const addPolygonPart = (poly, partIndex) => {
        const scaled = mapScaledPolygon(poly, imageGeometry);
        if (scaled.length < 3) {
          return false;
        }

        const points = toSvgPolygonPoints(scaled);
        const len = polygonLength(scaled);
        polygons.push({
          key: `${id}:poly:${partIndex}`,
          id,
          kind,
          points,
          len,
        });

        if (kind === "visual") {
          const clipPath = `polygon(${toCssPolygonPoints(scaled)})`;
          underlays.push({
            key: `${id}:underlay:${partIndex}`,
            id,
            clipPath,
          });
          lifts.push({
            key: `${id}:lift:${partIndex}`,
            id,
            clipPath,
          });
        }
        return true;
      };

      let polygonCount = 0;
      if (Array.isArray(region?.polygons) && region.polygons.length > 0) {
        region.polygons.forEach((poly, idx) => {
          if (addPolygonPart(poly, idx)) {
            polygonCount += 1;
          }
        });
      }

      if (polygonCount === 0 && Array.isArray(region?.polygon) && region.polygon.length >= 3) {
        if (addPolygonPart(region.polygon, 0)) {
          polygonCount = 1;
        }
      }

      if (polygonCount === 0) {
        pushBox({
          id,
          kind,
          bbox: region?.bbox,
        });
      }
    });

    return { boxes, lifts, underlays, polygons };
  }, [currentSlide, imageGeometry, activeHighlightIdSet, activeHighlightIds.length, currentTab]);

  const updateImageGeometry = useCallback(() => {
    const imageEl = mainImageRef.current;
    const containerEl = imageContainerRef.current;
    if (!imageEl || !containerEl || !currentSlide) {
      setImageGeometry(null);
      return;
    }

    const rect = imageEl.getBoundingClientRect();
    const containerRect = containerEl.getBoundingClientRect();
    if (!rect.width || !rect.height || !containerRect.width || !containerRect.height) {
      setImageGeometry(null);
      return;
    }

    const sourceWidth = Number(currentSlide.image_width) || imageEl.naturalWidth || 1;
    const sourceHeight = Number(currentSlide.image_height) || imageEl.naturalHeight || 1;
    if (!sourceWidth || !sourceHeight) {
      setImageGeometry(null);
      return;
    }

    const nextGeometry = {
      offsetX: rect.left - containerRect.left,
      offsetY: rect.top - containerRect.top,
      width: rect.width,
      height: rect.height,
      sourceWidth,
      sourceHeight,
      scaleX: rect.width / sourceWidth,
      scaleY: rect.height / sourceHeight,
    };

    setImageGeometry(nextGeometry);
  }, [currentSlide]);

  const updateAdaptiveLectureSplit = useCallback(() => {
    const lectureMain = lectureMainRef.current;
    const slidePane = slidePaneRef.current;
    const slideFrame = slideFrameRef.current;
    const imageContainer = imageContainerRef.current;

    if (!lectureMain || !slidePane || !slideFrame || !imageContainer) {
      return;
    }

    if (typeof window === "undefined") {
      return;
    }

    if (window.innerWidth <= 1080) {
      lectureMain.style.removeProperty("--lecture-left-pane-width");
      lectureMain.style.removeProperty("--lecture-right-pane-min");
      return;
    }

    const mainRect = lectureMain.getBoundingClientRect();
    const paneRect = slidePane.getBoundingClientRect();
    const frameRect = slideFrame.getBoundingClientRect();
    const canvasRect = imageContainer.getBoundingClientRect();

    if (mainRect.width <= 0 || paneRect.width <= 0 || frameRect.height <= 0) {
      return;
    }

    const computed = window.getComputedStyle(lectureMain);
    const gap = Number.parseFloat(computed.columnGap || computed.gap || "8") || 8;
    const usableWidth = Math.max(0, mainRect.width - gap);
    if (usableWidth <= 0) {
      return;
    }

    const imageEl = mainImageRef.current;
    const sourceWidth = Number(currentSlide?.image_width) || imageEl?.naturalWidth || 0;
    const sourceHeight = Number(currentSlide?.image_height) || imageEl?.naturalHeight || 0;
    const aspect =
      sourceWidth > 1 && sourceHeight > 1
        ? clamp(sourceWidth / sourceHeight, 0.6, 2.2)
        : 4 / 3;

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
    if (splitRafRef.current) {
      cancelAnimationFrame(splitRafRef.current);
    }
    splitRafRef.current = requestAnimationFrame(() => {
      splitRafRef.current = 0;
      updateAdaptiveLectureSplit();
    });
  }, [updateAdaptiveLectureSplit]);

  const pausePlaybackForManualNavigation = useCallback(() => {
    const audio = audioRef.current;
    if (audio && !audio.paused) {
      audio.pause();
    }
    setIsPlaying(false);
  }, []);

  const seekAudioToCurrentUiPosition = useCallback(
    ({ nextSlideIndex = currentSlideIndexRef.current, nextStepIndex = currentStepIndexRef.current } = {}) => {
      const audio = audioRef.current;
      if (!audio || !lecture?.audio_url) {
        return;
      }

      const targetMs = getCurrentGlobalMs(
        playbackTimelineRef.current.timeline,
        nextSlideIndex,
        nextStepIndex
      );
      audio.currentTime = Math.max(0, targetMs / 1000);
      setAudioClockMs(targetMs);
    },
    [lecture?.audio_url]
  );

  const goToSlide = useCallback(
    (index, options = {}) => {
      const { pause = true, seekAudio = true, resetQaTarget = false } = options;
      if (!slides.length) {
        return;
      }

      const safeIndex = clamp(index, 0, slides.length - 1);
      if (pause) {
        pausePlaybackForManualNavigation();
      }

      setCurrentSlideIndex(safeIndex);
      setCurrentStepIndex(0);
      setMainImageName("");
      if (resetQaTarget) {
        setQaActiveLineSlideId("");
      }

      if (seekAudio) {
        requestAnimationFrame(() => {
          seekAudioToCurrentUiPosition({ nextSlideIndex: safeIndex, nextStepIndex: 0 });
        });
      }
    },
    [slides.length, pausePlaybackForManualNavigation, seekAudioToCurrentUiPosition]
  );

  const goToStep = useCallback(
    (index, options = {}) => {
      const { pause = true, seekAudio = true } = options;
      if (!currentSteps.length) {
        setCurrentStepIndex(0);
        return;
      }
      const safeIndex = clamp(index, 0, currentSteps.length - 1);
      if (pause) {
        pausePlaybackForManualNavigation();
      }
      setCurrentStepIndex(safeIndex);
      if (seekAudio) {
        requestAnimationFrame(() => {
          seekAudioToCurrentUiPosition({ nextSlideIndex: currentSlideIndexRef.current, nextStepIndex: safeIndex });
        });
      }
    },
    [currentSteps.length, pausePlaybackForManualNavigation, seekAudioToCurrentUiPosition]
  );

  const applyLecturePayload = useCallback((payload, options = {}) => {
    const preservePosition = Boolean(options.preservePosition);
    const normalized = normalizeLecturePayload(payload);
    if (!normalized || typeof normalized !== "object") {
      return;
    }

    let nextSlideIndex = 0;
    let nextStepIndex = 0;

    if (preservePosition && lectureRef.current) {
      const previousLecture = lectureRef.current;
      const previousSlide = previousLecture?.slides?.[currentSlideIndexRef.current];
      const previousSlideNumber = Number(previousSlide?.slide_number);

      if (Number.isFinite(previousSlideNumber) && Array.isArray(normalized.slides)) {
        const foundSlide = normalized.slides.findIndex(
          (slide) => Number(slide?.slide_number) === previousSlideNumber
        );
        if (foundSlide >= 0) {
          nextSlideIndex = foundSlide;
        }
      }

      const oldTimeline = playbackTimelineRef.current;
      const previousGlobalMs = getCurrentGlobalMs(
        oldTimeline.timeline,
        currentSlideIndexRef.current,
        currentStepIndexRef.current
      );

      const newTimeline = buildPlaybackTimeline(normalized);
      if (previousGlobalMs > 0) {
        const location = locateTimelinePosition(newTimeline.timeline, newTimeline.totalMs, previousGlobalMs);
        if (location) {
          nextSlideIndex = location.slideIndex;
          nextStepIndex = location.stepIndex;
        }
      }
    }

    setLecture(normalized);
    setCurrentSlideIndex(nextSlideIndex);
    setCurrentStepIndex(nextStepIndex);
    setMainImageName("");
    setLectureLoadError("");
  }, []);

  useEffect(() => {
    if (!router.isReady) {
      return;
    }

    if (!routeJobId) {
      setLecture(null);
      setLectureLoadError("No job id was provided.");
      setSlideMessage("No job id was provided.");
      return;
    }

    let cancelled = false;

    async function loadLecture() {
      setLectureLoadError("");
      setSlideMessage("Loading slide...");

      try {
        const response = await fetch(buildApiUrl(`/api/jobs/${encodeURIComponent(routeJobId)}/lecture`));
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload?.detail || `Request failed: ${response.status}`);
        }
        if (cancelled) {
          return;
        }

        applyLecturePayload(payload, { preservePosition: false });
      } catch (error) {
        if (cancelled) {
          return;
        }
        setLecture(null);
        setLectureLoadError(error?.message || "Unable to load lecture payload.");
        setSlideMessage(error?.message || "Unable to load lecture payload.");
      }
    }

    void loadLecture();

    return () => {
      cancelled = true;
    };
  }, [router.isReady, routeJobId, applyLecturePayload]);

  useEffect(() => {
    if (!routeJobId || !lecture) {
      return;
    }
    if (!shouldPollLecturePayload(lecture)) {
      return;
    }

    let cancelled = false;
    let inFlight = false;

    const refresh = async () => {
      if (cancelled || inFlight) {
        return;
      }
      inFlight = true;
      try {
        const response = await fetch(buildApiUrl(`/api/jobs/${encodeURIComponent(routeJobId)}/lecture`));
        const payload = await response.json();
        if (!response.ok || !payload || typeof payload !== "object") {
          return;
        }

        const previous = lectureRef.current;
        const changed =
          !previous ||
          previous.audio_url !== payload.audio_url ||
          slideStepCountSignature(previous) !== slideStepCountSignature(payload) ||
          (Array.isArray(previous.slides) ? previous.slides.length : 0) !==
            (Array.isArray(payload.slides) ? payload.slides.length : 0);

        if (changed && !cancelled) {
          applyLecturePayload(payload, { preservePosition: true });
        }
      } catch {
        // Keep previous payload if refresh fails.
      } finally {
        inFlight = false;
      }
    };

    const interval = setInterval(() => {
      void refresh();
    }, 8000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [routeJobId, lecture, applyLecturePayload]);

  useEffect(() => {
    if (!slides.length) {
      setCurrentSlideIndex(0);
      setCurrentStepIndex(0);
      return;
    }

    setCurrentSlideIndex((prev) => clamp(prev, 0, slides.length - 1));
  }, [slides.length]);

  useEffect(() => {
    if (!currentSteps.length) {
      setCurrentStepIndex(0);
      return;
    }
    setCurrentStepIndex((prev) => clamp(prev, 0, currentSteps.length - 1));
  }, [currentSteps.length, currentSlideIndex]);

  useEffect(() => {
    if (!currentSlide) {
      setMainImageSrc("");
      setMainImageName("");
      return;
    }

    const isScript = currentTab === TAB_SCRIPT;
    const targetSrc = isScript
      ? getRenderedStepUrl(currentSlide, currentStepIndex) || currentSlide.image_url || ""
      : currentSlide.image_url || "";

    const nextImageName = typeof currentSlide.image_name === "string" ? currentSlide.image_name : "";

    if (mainImageName !== nextImageName || mainImageSrc !== targetSrc) {
      setSlideMessage("Loading slide...");
    }

    setMainImageName(nextImageName);
    setMainImageSrc(targetSrc);

    const renderedUrls = Array.isArray(currentSlide.rendered_step_urls) ? currentSlide.rendered_step_urls : [];
    renderedUrls.forEach((url) => {
      if (!url || preloadedUrlsRef.current.has(url)) {
        return;
      }
      const image = new Image();
      image.src = url;
      preloadedUrlsRef.current.add(url);
    });
  }, [currentSlide, currentStepIndex, currentTab, mainImageName, mainImageSrc]);

  useEffect(() => {
    updateImageGeometry();
    scheduleAdaptiveLectureSplit();
  }, [
    currentSlide,
    currentStepIndex,
    currentTab,
    mainImageSrc,
    updateImageGeometry,
    scheduleAdaptiveLectureSplit,
  ]);

  useEffect(() => {
    const onResize = () => {
      updateImageGeometry();
      scheduleAdaptiveLectureSplit();
    };

    window.addEventListener("resize", onResize);

    let observer = null;
    if (window.ResizeObserver && imageContainerRef.current) {
      observer = new ResizeObserver(() => {
        updateImageGeometry();
        scheduleAdaptiveLectureSplit();
      });
      observer.observe(imageContainerRef.current);
    }

    return () => {
      window.removeEventListener("resize", onResize);
      if (observer) {
        observer.disconnect();
      }
    };
  }, [updateImageGeometry, scheduleAdaptiveLectureSplit]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) {
      return;
    }
    audio.playbackRate = playbackRate;
  }, [playbackRate, lecture?.audio_url]);

  useEffect(() => {
    if (currentTab === TAB_SCRIPT) {
      return;
    }
    if (!isPlaying) {
      return;
    }
    const audio = audioRef.current;
    if (audio && !audio.paused) {
      audio.pause();
    }
    setIsPlaying(false);
  }, [currentTab, isPlaying]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !lecture?.audio_url) {
      return;
    }

    const onLoadedMetadata = () => {
      setAudioDurationMs(Math.max(0, Math.round(audio.duration * 1000)));
      setAudioClockMs(Math.max(0, Math.round(audio.currentTime * 1000)));
    };

    const onTimeUpdate = () => {
      const currentMs = Math.max(0, Math.round(audio.currentTime * 1000));
      setAudioClockMs(currentMs);

      const location = locateTimelinePosition(
        playbackTimelineRef.current.timeline,
        playbackTimelineRef.current.totalMs,
        currentMs
      );
      if (!location) {
        return;
      }

      setCurrentSlideIndex((prev) => (prev === location.slideIndex ? prev : location.slideIndex));
      setCurrentStepIndex((prev) => (prev === location.stepIndex ? prev : location.stepIndex));
    };

    const onEnded = () => {
      setIsPlaying(false);
      setAudioClockMs(Math.max(0, Math.round(audio.currentTime * 1000)));
    };

    const onSeeked = () => {
      setAudioClockMs(Math.max(0, Math.round(audio.currentTime * 1000)));
    };

    const onError = () => {
      setIsPlaying(false);
    };

    audio.addEventListener("loadedmetadata", onLoadedMetadata);
    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("seeked", onSeeked);
    audio.addEventListener("error", onError);

    return () => {
      audio.removeEventListener("loadedmetadata", onLoadedMetadata);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("seeked", onSeeked);
      audio.removeEventListener("error", onError);
    };
  }, [lecture?.audio_url]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !lecture?.audio_url) {
      return;
    }

    if (isPlaying) {
      void audio.play().catch(() => {
        setIsPlaying(false);
      });
      return;
    }

    if (!audio.paused) {
      audio.pause();
    }
  }, [isPlaying, lecture?.audio_url]);

  useEffect(() => {
    if (!isPlaying || currentTab !== TAB_SCRIPT) {
      return;
    }
    if (lecture?.audio_url && audioRef.current) {
      return;
    }

    if (!currentSteps.length) {
      setIsPlaying(false);
      return;
    }

    const dwellMs = normalizeDwellMs(currentStep);
    const adjustedDwellMs = Math.max(120, Math.round(dwellMs / playbackRate));

    const timer = setTimeout(() => {
      const nextStep = currentStepIndex + 1;
      if (nextStep < currentSteps.length) {
        setCurrentStepIndex(nextStep);
        return;
      }

      const nextSlide = currentSlideIndex + 1;
      if (nextSlide < slides.length) {
        setCurrentSlideIndex(nextSlide);
        setCurrentStepIndex(0);
        return;
      }

      setIsPlaying(false);
    }, adjustedDwellMs);

    return () => {
      clearTimeout(timer);
    };
  }, [
    isPlaying,
    currentTab,
    lecture?.audio_url,
    currentSteps,
    currentStep,
    currentStepIndex,
    currentSlideIndex,
    playbackRate,
    slides.length,
  ]);

  useEffect(() => {
    if (qaRequestSeqRef.current === 0) {
      return;
    }
    if (currentTab !== TAB_QA) {
      return;
    }
    if (qaScrollRafRef.current) {
      cancelAnimationFrame(qaScrollRafRef.current);
    }
    qaScrollRafRef.current = requestAnimationFrame(() => {
      qaScrollRafRef.current = 0;
      const thread = qaThreadRef.current;
      if (!thread) {
        return;
      }
      thread.scrollTop = thread.scrollHeight;
    });
  }, [qaTurns, currentTab]);

  useEffect(() => {
    return () => {
      if (qaAbortRef.current) {
        qaAbortRef.current.abort();
      }
      if (qaScrollRafRef.current) {
        cancelAnimationFrame(qaScrollRafRef.current);
      }
      if (splitRafRef.current) {
        cancelAnimationFrame(splitRafRef.current);
      }
    };
  }, []);

  const updateQaTurn = useCallback((turnId, updater) => {
    setQaTurns((prev) =>
      prev.map((turn) => {
        if (!turn || turn.id !== turnId) {
          return turn;
        }
        if (typeof updater === "function") {
          return updater(turn);
        }
        return { ...turn, ...updater };
      })
    );
  }, []);

  const handleSubmitQa = useCallback(async () => {
    if (!routeJobId) {
      return;
    }

    const question = (qaQuestion || "").trim();
    if (!question) {
      setQaError("Enter a question first.");
      return;
    }

    if (qaAbortRef.current) {
      qaAbortRef.current.abort();
      qaAbortRef.current = null;
    }

    const controller = new AbortController();
    qaAbortRef.current = controller;

    const requestSeq = qaRequestSeqRef.current + 1;
    qaRequestSeqRef.current = requestSeq;

    const turnId = `qa-${requestSeq}-${Date.now()}`;

    setQaTurns((prev) => [
      ...prev,
      {
        id: turnId,
        question,
        status: "loading",
        result: null,
        error: "",
        progressMessage: "Preparing request...",
        streamText: "",
      },
    ]);

    setQaQuestion("");
    setQaError("");
    setQaLoading(true);
    setQaActiveTurnId(turnId);
    setQaActiveLineIndex(-1);
    setQaActiveLineSlideId("");

    try {
      const payload = await streamQaAnswer({
        jobId: routeJobId,
        question,
        signal: controller.signal,
        onProgress: (progress) => {
          updateQaTurn(turnId, (turn) => {
            if (!turn || turn.status !== "loading") {
              return turn;
            }
            return {
              ...turn,
              progressMessage: getQaProgressLabel(progress),
            };
          });
        },
        onDelta: (deltaText) => {
          updateQaTurn(turnId, (turn) => {
            if (!turn || turn.status !== "loading") {
              return turn;
            }
            return {
              ...turn,
              streamText: `${typeof turn.streamText === "string" ? turn.streamText : ""}${deltaText}`,
            };
          });
        },
        onResult: () => {
          updateQaTurn(turnId, (turn) => {
            if (!turn || turn.status !== "loading") {
              return turn;
            }
            return {
              ...turn,
              progressMessage: "Finalizing response...",
            };
          });
        },
      });

      if (requestSeq !== qaRequestSeqRef.current) {
        return;
      }

      setQaTurns((prev) =>
        prev.map((turn) => {
          if (!turn || turn.id !== turnId) {
            return turn;
          }
          return {
            ...turn,
            status: "done",
            result: payload,
            error: "",
            streamText: "",
          };
        })
      );
      setQaActiveTurnId(turnId);
      setQaActiveLineIndex(-1);
      setQaActiveLineSlideId("");
      setQaError("");
    } catch (error) {
      if (requestSeq !== qaRequestSeqRef.current) {
        return;
      }
      if (error?.name === "AbortError") {
        return;
      }
      const message = error?.message || "Unable to answer the question.";
      setQaTurns((prev) =>
        prev.map((turn) => {
          if (!turn || turn.id !== turnId) {
            return turn;
          }
          return {
            ...turn,
            status: "error",
            error: message,
            result: null,
          };
        })
      );
      setQaError(message);
    } finally {
      if (requestSeq === qaRequestSeqRef.current) {
        setQaLoading(false);
        qaAbortRef.current = null;
      }
    }
  }, [qaQuestion, routeJobId, updateQaTurn]);

  const activateQaAnswerLine = useCallback(
    (lineIndex, turnId, slideId) => {
      const turn = qaTurns.find((item) => item && item.id === turnId);
      const lines = Array.isArray(turn?.result?.answer_lines) ? turn.result.answer_lines : [];
      const line = lines[lineIndex];
      if (!line) {
        return;
      }

      setQaActiveTurnId(turnId);
      setQaActiveLineIndex(lineIndex);
      setQaActiveLineSlideId(typeof slideId === "string" ? slideId : "");

      if (typeof slideId !== "string" || !slideId) {
        return;
      }

      const targetSlideIndex = slideIndexById.get(slideId);
      if (typeof targetSlideIndex !== "number") {
        return;
      }

      if (targetSlideIndex !== currentSlideIndexRef.current) {
        goToSlide(targetSlideIndex, {
          pause: true,
          seekAudio: true,
          resetQaTarget: false,
        });
      }
    },
    [qaTurns, slideIndexById, goToSlide]
  );

  const onTimelinePointerDown = useCallback(
    (event) => {
      if (event.button !== 0) {
        return;
      }
      if (event.target instanceof Element && event.target.closest(".slide-dot")) {
        return;
      }

      const rail = timelineRailRef.current;
      if (!rail) {
        return;
      }

      const getProgressFromX = (clientX) => {
        const rect = rail.getBoundingClientRect();
        if (!rect.width) {
          return 0;
        }
        const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
        return x / rect.width;
      };

      const scrubToProgress = (progressRatio) => {
        const progress = clamp01(progressRatio);
        const maxTimelineMs =
          lecture?.audio_url && audioRef.current && Number.isFinite(audioRef.current.duration) && audioRef.current.duration > 0
            ? audioRef.current.duration * 1000
            : playbackTimeline.totalMs;

        if (maxTimelineMs > 0) {
          const location = locateTimelinePosition(
            playbackTimeline.timeline,
            playbackTimeline.totalMs,
            progress * maxTimelineMs
          );
          if (location) {
            setCurrentSlideIndex(location.slideIndex);
            setCurrentStepIndex(location.stepIndex);
            seekAudioToCurrentUiPosition({
              nextSlideIndex: location.slideIndex,
              nextStepIndex: location.stepIndex,
            });
            return;
          }
        }

        if (slides.length > 0) {
          const targetSlideIndex = Math.round(progress * Math.max(0, slides.length - 1));
          setCurrentSlideIndex(targetSlideIndex);
          setCurrentStepIndex(0);
          seekAudioToCurrentUiPosition({ nextSlideIndex: targetSlideIndex, nextStepIndex: 0 });
        }
      };

      pausePlaybackForManualNavigation();
      timelineScrubPointerIdRef.current = event.pointerId;

      try {
        rail.setPointerCapture(event.pointerId);
      } catch {
        // Ignore pointer capture failures.
      }

      scrubToProgress(getProgressFromX(event.clientX));
    },
    [
      lecture?.audio_url,
      playbackTimeline.timeline,
      playbackTimeline.totalMs,
      slides.length,
      pausePlaybackForManualNavigation,
      seekAudioToCurrentUiPosition,
    ]
  );

  const onTimelinePointerMove = useCallback(
    (event) => {
      const pointerId = timelineScrubPointerIdRef.current;
      if (pointerId == null || event.pointerId !== pointerId) {
        return;
      }

      const rail = timelineRailRef.current;
      if (!rail) {
        return;
      }

      const rect = rail.getBoundingClientRect();
      if (!rect.width) {
        return;
      }
      const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
      const progress = x / rect.width;

      const maxTimelineMs =
        lecture?.audio_url && audioRef.current && Number.isFinite(audioRef.current.duration) && audioRef.current.duration > 0
          ? audioRef.current.duration * 1000
          : playbackTimeline.totalMs;

      if (maxTimelineMs > 0) {
        const location = locateTimelinePosition(
          playbackTimeline.timeline,
          playbackTimeline.totalMs,
          progress * maxTimelineMs
        );
        if (location) {
          setCurrentSlideIndex(location.slideIndex);
          setCurrentStepIndex(location.stepIndex);
          seekAudioToCurrentUiPosition({
            nextSlideIndex: location.slideIndex,
            nextStepIndex: location.stepIndex,
          });
        }
        return;
      }

      if (slides.length > 0) {
        const targetSlideIndex = Math.round(progress * Math.max(0, slides.length - 1));
        setCurrentSlideIndex(targetSlideIndex);
        setCurrentStepIndex(0);
        seekAudioToCurrentUiPosition({ nextSlideIndex: targetSlideIndex, nextStepIndex: 0 });
      }
    },
    [
      lecture?.audio_url,
      playbackTimeline.timeline,
      playbackTimeline.totalMs,
      seekAudioToCurrentUiPosition,
      slides.length,
    ]
  );

  const endTimelineScrub = useCallback((event) => {
    const pointerId = timelineScrubPointerIdRef.current;
    if (pointerId == null) {
      return;
    }
    if (event && event.pointerId !== pointerId) {
      return;
    }

    const rail = timelineRailRef.current;
    if (rail) {
      try {
        if (rail.hasPointerCapture?.(pointerId)) {
          rail.releasePointerCapture(pointerId);
        }
      } catch {
        // Ignore release failures.
      }
    }

    timelineScrubPointerIdRef.current = null;
  }, []);

  const handleMainImageLoad = useCallback(() => {
    setSlideMessage("");
    updateImageGeometry();
    scheduleAdaptiveLectureSplit();
  }, [updateImageGeometry, scheduleAdaptiveLectureSplit]);

  const handleMainImageError = useCallback(() => {
    setSlideMessage("Slide image could not be loaded.");
    updateImageGeometry();
    scheduleAdaptiveLectureSplit();
  }, [updateImageGeometry, scheduleAdaptiveLectureSplit]);

  const handlePlayClick = useCallback(async () => {
    if (!lecture || currentTab !== TAB_SCRIPT) {
      return;
    }

    const audio = audioRef.current;
    const hasNarrationAudio = Boolean(lecture.audio_url && audio);

    if (hasNarrationAudio) {
      if (isPlaying) {
        audio.pause();
        setIsPlaying(false);
        return;
      }

      if (audio.ended) {
        audio.currentTime = 0;
        setCurrentSlideIndex(0);
        setCurrentStepIndex(0);
      }

      setIsPlaying(true);
      try {
        await audio.play();
      } catch {
        setIsPlaying(false);
      }
      return;
    }

    if (isPlaying) {
      setIsPlaying(false);
      return;
    }

    if (!currentSteps.length) {
      setIsPlaying(false);
      return;
    }

    setIsPlaying(true);
  }, [lecture, currentTab, isPlaying, currentSteps.length]);

  const handleTabChange = useCallback(
    (nextTab) => {
      if (nextTab === currentTab) {
        return;
      }

      if (isPlaying) {
        pausePlaybackForManualNavigation();
      }
      setCurrentTab(nextTab);
      setSearchFlash(false);
      scheduleAdaptiveLectureSplit();
    },
    [currentTab, isPlaying, pausePlaybackForManualNavigation, scheduleAdaptiveLectureSplit]
  );

  const handleScriptSearchClick = useCallback(() => {
    if (currentTab !== TAB_SCRIPT) {
      return;
    }
    setSearchFlash(true);
    window.setTimeout(() => {
      setSearchFlash(false);
    }, 1400);
  }, [currentTab]);

  const downloadPdfHref =
    typeof lecture?.input_pdf_url === "string" && lecture.input_pdf_url ? lecture.input_pdf_url : "#";

  const hasSlides = slides.length > 0;

  const playbackProgressPct = `${(playbackProgress * 100).toFixed(2)}%`;

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>NeuroNote - Lecture View</title>
      </Head>

      <div
        className={`lecture-shell${isPlaying ? " is-playing" : ""}`}
        style={{
          "--playback-progress": String(playbackProgress),
          "--playback-progress-pct": playbackProgressPct,
        }}
      >
        <div className="lecture-ambient lecture-ambient-a" aria-hidden="true" />
        <div className="lecture-ambient lecture-ambient-b" aria-hidden="true" />

        <header className="lecture-topbar">
          <div className="lecture-brand">
            <div className="lecture-brand-logo">NN</div>
            <div className="lecture-brand-text">NeuroNote</div>
          </div>

          <div className="lecture-head-actions">
            <div className="lecture-pill-group">
              <Link className="back-btn" href="/">
                My Lectures
              </Link>
            </div>

            <div className="lecture-tabs" role="tablist" aria-label="Panel tabs">
              <button
                className={`lecture-tab${currentTab === TAB_SCRIPT ? " lecture-tab-active" : ""}`}
                type="button"
                onClick={() => handleTabChange(TAB_SCRIPT)}
              >
                Script
              </button>
              <button
                className={`lecture-tab${currentTab === TAB_QA ? " lecture-tab-active" : ""}`}
                type="button"
                onClick={() => handleTabChange(TAB_QA)}
              >
                Q&amp;A
              </button>
              <button
                className={`lecture-tab${currentTab === TAB_NOTES ? " lecture-tab-active" : ""}`}
                type="button"
                onClick={() => handleTabChange(TAB_NOTES)}
              >
                Notes
              </button>
              <button
                className={`lecture-tab${currentTab === TAB_RESOURCES ? " lecture-tab-active" : ""}`}
                type="button"
                onClick={() => handleTabChange(TAB_RESOURCES)}
              >
                Resources
              </button>
            </div>

            <div className="lecture-pill-group lecture-pill-group-right">
              <a className="download-btn" href={downloadPdfHref} target="_blank" rel="noopener">
                Download PDF
              </a>
              <button className="menu-btn" type="button" aria-label="More options">
                &#8942;
              </button>
            </div>
          </div>
        </header>

        <main ref={lectureMainRef} className="lecture-main">
          <section ref={slidePaneRef} className="slide-pane">
            <div className="slide-pane-caption" aria-hidden="true">
              Slide Preview
            </div>
            <div ref={slideFrameRef} className="slide-frame">
              <div ref={imageContainerRef} className="slide-canvas">
                <img
                  ref={mainImageRef}
                  className="slide-image"
                  alt="Slide preview"
                  src={mainImageSrc || undefined}
                  onLoad={handleMainImageLoad}
                  onError={handleMainImageError}
                />
                <div className="highlight-overlay" aria-hidden="true">
                  {overlayShapes.boxes.map((box) => (
                    <div
                      key={box.key}
                      className={`highlight-box ${box.kind} active`}
                      data-id={box.id}
                      style={{
                        left: box.left,
                        top: box.top,
                        width: box.width,
                        height: box.height,
                        zIndex: 3,
                      }}
                    />
                  ))}
                  {overlayShapes.underlays.map((layer) => (
                    <div
                      key={layer.key}
                      className="highlight-lift-underlay visual active"
                      data-id={layer.id}
                      style={{
                        clipPath: layer.clipPath,
                        WebkitClipPath: layer.clipPath,
                      }}
                    />
                  ))}
                  {overlayShapes.lifts.map((layer) => (
                    <div
                      key={layer.key}
                      className="highlight-lift visual active"
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
                    <svg
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        height: "100%",
                        pointerEvents: "none",
                        zIndex: 4,
                      }}
                    >
                      {overlayShapes.polygons.map((poly) => (
                        <polygon
                          key={poly.key}
                          className={`highlight-polygon ${poly.kind} active`}
                          data-id={poly.id}
                          points={poly.points}
                          style={{ "--poly-len": String(poly.len || 240) }}
                        />
                      ))}
                    </svg>
                  )}
                </div>
              </div>

              {slideMessage ? <p className="slide-message">{slideMessage}</p> : null}
            </div>
          </section>

          <aside className="script-pane">
            <div className="script-pane-head">
              <div>
                <h2>
                  {currentTab === TAB_SCRIPT
                    ? currentSlide?.script_title || "Lecture Script"
                    : currentTab === TAB_QA
                      ? "Q&A"
                      : currentTab === TAB_NOTES
                        ? "Lecture Notes"
                        : "Resources"}
                </h2>
              </div>

              <button
                type="button"
                onClick={handleScriptSearchClick}
                hidden={currentTab !== TAB_SCRIPT}
                disabled={currentTab !== TAB_SCRIPT}
              >
                Search
              </button>
            </div>

            <div
              className={`script-panel${currentTab === TAB_QA ? " script-panel-qa" : ""}${
                currentTab === TAB_SCRIPT ? " script-panel-timeline" : ""
              }`}
            >
              {currentTab === TAB_SCRIPT && (
                <>
                  {searchFlash && <p className="script-placeholder">Search is a placeholder in this version.</p>}

                  {!hasSlides && !lectureLoadError && (
                    <p className="script-placeholder">No slides are available for this lecture.</p>
                  )}

                  {lectureLoadError && <p className="script-placeholder">{lectureLoadError}</p>}

                  {hasSlides && currentSteps.length === 0 && (
                    <p className="script-placeholder">No script was generated for this slide.</p>
                  )}

                  {hasSlides &&
                    currentSteps.map((step, idx) => (
                      <button
                        key={`${currentSlide?.image_name || "slide"}:${idx}`}
                        type="button"
                        className={`script-item${idx === currentStepIndex ? " script-item-active" : ""}`}
                        data-step-index={idx}
                        onClick={() => {
                          goToStep(idx, { pause: true, seekAudio: true });
                        }}
                        onMouseEnter={() => {
                          if (!isPlaying && currentTab === TAB_SCRIPT) {
                            setCurrentStepIndex(idx);
                          }
                        }}
                        onFocus={() => {
                          if (!isPlaying && currentTab === TAB_SCRIPT) {
                            setCurrentStepIndex(idx);
                          }
                        }}
                      >
                        <span className="script-time">{formatClock(step?.start_ms || 0)}</span>
                        <p className="script-line">{typeof step?.line === "string" ? step.line : ""}</p>
                      </button>
                    ))}
                </>
              )}

              {currentTab === TAB_QA && (
                <div className="qa-shell qa-shell-chat">
                  <div ref={qaThreadRef} className="qa-thread">
                    {qaTurns.length === 0 ? (
                      <div className="qa-chat-empty">
                        <p className="qa-chat-empty-title">Ask anything about the slides</p>
                        <p className="qa-chat-empty-text">
                          I retrieve the most relevant explanation text and return clickable highlights you can jump to.
                        </p>
                      </div>
                    ) : (
                      qaTurns.map((turn) => {
                        const turnLines = Array.isArray(turn?.result?.answer_lines)
                          ? turn.result.answer_lines
                          : [];

                        return (
                          <div key={turn.id} className="qa-turn">
                            <div className="qa-chat-row qa-chat-row-user">
                              <div className="qa-chat-bubble qa-chat-bubble-user">
                                <p className="qa-chat-role">You</p>
                                <p className="qa-chat-text">{typeof turn?.question === "string" ? turn.question : ""}</p>
                              </div>
                            </div>

                            {turn.status === "loading" && (
                              <div className="qa-chat-row qa-chat-row-assistant qa-chat-row-status">
                                <div className="qa-chat-avatar qa-chat-avatar-assistant" aria-hidden="true">
                                  NN
                                </div>
                                <div className="qa-chat-bubble qa-chat-bubble-assistant">
                                  <p className="qa-chat-role">NeuroNote</p>
                                  {typeof turn.streamText === "string" && turn.streamText.trim() ? (
                                    <p className="qa-chat-text qa-chat-stream">{turn.streamText}</p>
                                  ) : (
                                    <p className="qa-status qa-chat-typing">
                                      {typeof turn.progressMessage === "string" && turn.progressMessage
                                        ? turn.progressMessage
                                        : "Running retrieval and generating an answer..."}
                                    </p>
                                  )}
                                </div>
                              </div>
                            )}

                            {turn.status === "error" && (
                              <div className="qa-chat-row qa-chat-row-assistant qa-chat-row-error">
                                <div className="qa-chat-avatar qa-chat-avatar-assistant" aria-hidden="true">
                                  NN
                                </div>
                                <div className="qa-chat-bubble qa-chat-bubble-assistant">
                                  <p className="qa-chat-role">NeuroNote</p>
                                  <div className="qa-error" role="alert">
                                    {typeof turn.error === "string" && turn.error
                                      ? turn.error
                                      : "Unable to answer the question."}
                                  </div>
                                </div>
                              </div>
                            )}

                            {turn.status === "done" && (
                              <div className="qa-chat-row qa-chat-row-assistant qa-chat-row-answer">
                                <div className="qa-chat-avatar qa-chat-avatar-assistant" aria-hidden="true">
                                  NN
                                </div>
                                <div className="qa-chat-bubble qa-chat-bubble-assistant">
                                  <p className="qa-chat-role">NeuroNote</p>

                                  {turnLines.length === 0 ? (
                                    <p className="qa-chat-text">
                                      {typeof turn?.result?.answer_text === "string" && turn.result.answer_text.trim()
                                        ? turn.result.answer_text.trim()
                                        : "I couldn't extract structured answer lines for this question."}
                                    </p>
                                  ) : (
                                    <div className="qa-answer-list">
                                      {turnLines.map((line, idx) => {
                                        const isActive =
                                          turn.id === qaActiveTurnId &&
                                          idx === qaActiveLineIndex &&
                                          Boolean(qaActiveLineSlideId);
                                        const slideRefs = collectLineSlideRefs(line);
                                        return (
                                          <div
                                            key={`${turn.id}:line:${idx}`}
                                            className={`qa-answer-card${isActive ? " qa-answer-card-active" : ""}`}
                                          >
                                            <div className="qa-answer-line-inline">
                                              <div className="qa-answer-text-wrap">
                                                <p className="qa-answer-text">
                                                  {typeof line?.text === "string" ? line.text : ""}
                                                </p>
                                              </div>
                                              <div className="qa-slide-badge-list">
                                                {slideRefs.map((ref) => (
                                                  <button
                                                    key={`${turn.id}:line:${idx}:slide:${ref.slideId}`}
                                                    type="button"
                                                    className="qa-highlight-chip qa-slide-badge"
                                                    onClick={() => {
                                                      activateQaAnswerLine(idx, turn.id, ref.slideId);
                                                    }}
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

                  <form
                    className="qa-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void handleSubmitQa();
                    }}
                  >
                    <label className="qa-label qa-label-sr-only" htmlFor="qaQuestionInput">
                      Ask a question about the lecture slides
                    </label>

                    <div className="qa-composer-main">
                      <textarea
                        ref={qaTextareaRef}
                        id="qaQuestionInput"
                        className="qa-input"
                        rows={2}
                        placeholder="Ask about this lecture..."
                        value={qaQuestion}
                        disabled={qaLoading}
                        onChange={(event) => {
                          setQaQuestion(event.target.value || "");
                          if (qaError) {
                            setQaError("");
                          }
                        }}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" || event.shiftKey) {
                            return;
                          }
                          event.preventDefault();
                          if (!qaLoading) {
                            void handleSubmitQa();
                          }
                        }}
                      />

                      <div className="qa-form-controls">
                        <div className="qa-composer-toolbar">
                          <button
                            type="button"
                            className="qa-attach-btn"
                            aria-label="More actions"
                            onClick={(event) => {
                              event.currentTarget.blur();
                            }}
                          >
                            +
                          </button>
                          <button type="submit" className="qa-submit-btn" disabled={qaLoading}>
                            {qaLoading ? "Thinking..." : "Send"}
                          </button>
                        </div>

                        <p className="qa-hint">
                          Click a Slide badge to highlight referenced regions on that slide.
                        </p>

                        {qaError && (
                          <p className="qa-error" role="alert">
                            {qaError}
                          </p>
                        )}
                      </div>
                    </div>
                  </form>
                </div>
              )}

              {currentTab === TAB_NOTES && (
                <p className="script-placeholder">Notes view is a placeholder in this version.</p>
              )}

              {currentTab === TAB_RESOURCES && (
                <p className="script-placeholder">Resources view is a placeholder in this version.</p>
              )}
            </div>
          </aside>
        </main>

        <footer className="lecture-bottom-bar">
          <div className="lecture-bottom-ambient" aria-hidden="true" />
          <section className="timeline-strip">
            <div className="timeline-top">
              <p className="slide-counter">{slideCounterText}</p>
              <p className="step-counter">{stepCounterText}</p>
            </div>
            <div
              ref={timelineRailRef}
              className="timeline-rail"
              aria-label="Slide timeline"
              style={{
                "--timeline-progress": String(playbackProgress),
                "--timeline-progress-pct": playbackProgressPct,
              }}
              onPointerDown={onTimelinePointerDown}
              onPointerMove={onTimelinePointerMove}
              onPointerUp={endTimelineScrub}
              onPointerCancel={endTimelineScrub}
              onLostPointerCapture={() => {
                timelineScrubPointerIdRef.current = null;
              }}
            >
              <div className="dots-track">
                {slides.map((slide, idx) => {
                  const slideNumber = Number.isFinite(Number(slide?.slide_number))
                    ? Number(slide.slide_number)
                    : idx + 1;
                  const thumbUrl =
                    (typeof slide?.thumbnail_url === "string" && slide.thumbnail_url) ||
                    (typeof slide?.image_url === "string" && slide.image_url) ||
                    "";

                  return (
                    <button
                      key={`${getSlideId(slide) || "slide"}:${idx}`}
                      type="button"
                      className={`slide-dot${idx === currentSlideIndex ? " slide-dot-active" : ""}`}
                      title={`Slide ${slideNumber}`}
                      aria-label={`Go to slide ${slideNumber}`}
                      onClick={() => {
                        goToSlide(idx, {
                          pause: true,
                          seekAudio: true,
                          resetQaTarget: false,
                        });
                      }}
                    >
                      <span className={`slide-dot-thumb${!thumbUrl ? " slide-dot-thumb-fallback" : ""}`}>
                        {thumbUrl ? (
                          <img src={thumbUrl} alt="" loading="lazy" decoding="async" />
                        ) : (
                          String(slideNumber)
                        )}
                      </span>
                      <span className="slide-dot-label">{slideNumber}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </section>

          <div className="lecture-controls">
            <div className="track-info">
              <p className="track-title">{trackTitle}</p>
              <p className="track-time">{trackTimeText}</p>
            </div>

            <div className="transport">
              <button
                type="button"
                aria-label="Previous slide"
                onClick={() => {
                  goToSlide(currentSlideIndex - 1, {
                    pause: true,
                    seekAudio: true,
                    resetQaTarget: false,
                  });
                }}
              >
                &#9664;
              </button>

              <button
                className="play-btn"
                type="button"
                aria-label={isPlaying ? "Pause script" : "Play script"}
                onClick={() => {
                  void handlePlayClick();
                }}
              >
                {isPlaying ? "\u23f8" : "\u25b6"}
              </button>

              <button
                type="button"
                aria-label="Next slide"
                onClick={() => {
                  goToSlide(currentSlideIndex + 1, {
                    pause: true,
                    seekAudio: true,
                    resetQaTarget: false,
                  });
                }}
              >
                &#9654;
              </button>
            </div>

            <div className="right-controls">
              <span id="rateLabel">{playbackRate.toFixed(1)}x</span>
              <input
                id="speedRange"
                type="range"
                min="50"
                max="500"
                step="5"
                value={Math.round(playbackRate * 100)}
                aria-label="Playback speed"
                onChange={(event) => {
                  const raw = Number.parseFloat(event.target.value || "100");
                  const nextRate = clamp(raw / 100, 0.5, 5.0);
                  setPlaybackRate(nextRate);
                }}
              />
            </div>
          </div>
        </footer>

        <audio
          ref={audioRef}
          src={typeof lecture?.audio_url === "string" ? lecture.audio_url : undefined}
          preload="metadata"
          hidden
        />
      </div>
    </>
  );
}

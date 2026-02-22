const lectureTitle = document.getElementById("lectureTitle");
const lectureSubhead = document.getElementById("lectureSubhead");
const downloadPdfBtn = document.getElementById("downloadPdfBtn");
const imageContainer = document.getElementById("imageContainer");
const mainImage = document.getElementById("mainImage");
const textTransitionCanvas = document.getElementById("textTransitionCanvas");
const highlightOverlay = document.getElementById("highlightOverlay");
const slideMessage = document.getElementById("slideMessage");
const scriptTitle = document.getElementById("scriptTitle");
const scriptPanel = document.getElementById("scriptPanel");
const scriptSearchBtn = document.getElementById("scriptSearchBtn");
const slideCounter = document.getElementById("slideCounter");
const stepCounter = document.getElementById("stepCounter");
const dotsTrack = document.getElementById("dotsTrack");
const trackTitle = document.getElementById("trackTitle");
const trackTime = document.getElementById("trackTime");
const prevSlideBtn = document.getElementById("prevSlideBtn");
const playBtn = document.getElementById("playBtn");
const nextSlideBtn = document.getElementById("nextSlideBtn");
const rateLabel = document.getElementById("rateLabel");
const speedRange = document.getElementById("speedRange");
const tabButtons = Array.from(document.querySelectorAll(".lecture-tab"));

const state = {
  jobId: "",
  lecture: null,
  currentSlideIndex: 0,
  currentStepIndex: 0,
  currentTab: "script",
  isPlaying: false,
  timer: null,
  currentImageName: "",
  renderedStepKey: "",
  preloadedUrls: new Set(),
  playbackRate: 1.0,
  transitionToken: 0,
  narrationAudio: null,
  audioSyncInterval: null,
  playbackTimeline: [],
  totalTimelineMs: 0,
  lectureRefreshTimer: null,
  lectureRefreshInFlight: false,
};

function getJobIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts.length >= 2 ? decodeURIComponent(parts[1]) : "";
}

function getCurrentSlide() {
  const slides = state.lecture?.slides || [];
  return slides[state.currentSlideIndex] || null;
}

function getCurrentSteps() {
  const slide = getCurrentSlide();
  if (!slide || !Array.isArray(slide.steps)) {
    return [];
  }
  return slide.steps;
}

function getCurrentStep() {
  const steps = getCurrentSteps();
  if (steps.length === 0) {
    return null;
  }
  const index = Math.max(0, Math.min(state.currentStepIndex, steps.length - 1));
  return steps[index];
}

function normalizeDwellMs(step) {
  const dwellMs = Number.isInteger(step?.dwell_ms) ? step.dwell_ms : 3500;
  return dwellMs > 0 ? dwellMs : 3500;
}

function hasPerStepAudioTiming() {
  const slides = state.lecture?.slides || [];
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

function stopLectureRefreshPolling() {
  if (state.lectureRefreshTimer) {
    clearInterval(state.lectureRefreshTimer);
    state.lectureRefreshTimer = null;
  }
}

async function applyLecturePayload(payload, options = {}) {
  const preservePosition = Boolean(options.preservePosition);
  const previousLecture = state.lecture;
  const previousSlideNumber = Number(previousLecture?.slides?.[state.currentSlideIndex]?.slide_number);
  const wasPlaying = state.isPlaying;
  const previousGlobalMs = preservePosition && previousLecture ? getCurrentGlobalMs() : 0;

  if (wasPlaying) {
    setPlaying(false);
  }

  state.lecture = payload;
  if (preservePosition) {
    if (Number.isFinite(previousSlideNumber)) {
      const nextSlideIndex = (payload.slides || []).findIndex(
        (slide) => Number(slide?.slide_number) === previousSlideNumber
      );
      if (nextSlideIndex >= 0) {
        state.currentSlideIndex = nextSlideIndex;
      }
    }
  } else {
    state.currentSlideIndex = 0;
    state.currentStepIndex = 0;
  }

  buildPlaybackTimeline();
  if (preservePosition && previousGlobalMs > 0) {
    const location = locateTimelinePosition(previousGlobalMs);
    if (location) {
      state.currentSlideIndex = location.slideIndex;
      state.currentStepIndex = location.stepIndex;
    }
  }

  state.currentImageName = "";
  state.renderedStepKey = "";
  setupNarrationAudio(payload.audio_url || "");
  setPlaybackRate(state.playbackRate);
  renderAll();

  if (wasPlaying) {
    if (hasNarrationAudio()) {
      state.narrationAudio.currentTime = Math.max(0, previousGlobalMs / 1000);
      setPlaying(true);
      try {
        await state.narrationAudio.play();
        syncUiToAudioPosition();
      } catch {
        setPlaying(false);
      }
    } else {
      setPlaying(true);
      schedulePlaybackTick();
    }
  }
}

async function refreshLecturePayload() {
  if (!state.jobId || state.lectureRefreshInFlight) {
    return;
  }
  state.lectureRefreshInFlight = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(state.jobId)}/lecture`);
    const payload = await response.json();
    if (!response.ok || !payload || typeof payload !== "object") {
      return;
    }

    const previous = state.lecture;
    const changed =
      !previous ||
      previous.audio_url !== payload.audio_url ||
      slideStepCountSignature(previous) !== slideStepCountSignature(payload) ||
      (previous.slides || []).length !== (payload.slides || []).length;

    if (changed) {
      await applyLecturePayload(payload, { preservePosition: true });
    }

    if (!shouldPollLecturePayload(payload)) {
      stopLectureRefreshPolling();
    }
  } catch {
    // Keep the last known payload if refresh fails temporarily.
  } finally {
    state.lectureRefreshInFlight = false;
  }
}

function ensureLectureRefreshPolling() {
  if (!shouldPollLecturePayload(state.lecture)) {
    stopLectureRefreshPolling();
    return;
  }
  if (state.lectureRefreshTimer) {
    return;
  }
  state.lectureRefreshTimer = setInterval(() => {
    void refreshLecturePayload();
  }, 8000);
}

function buildPlaybackTimeline() {
  const slides = state.lecture?.slides || [];
  const usePerStepTiming = hasPerStepAudioTiming();

  const timeline = [];
  let cursor = 0;
  slides.forEach((slideData, slideIndex) => {
    const steps = Array.isArray(slideData?.steps) ? slideData.steps : [];
    const slide = {
      slideIndex,
      startMs: cursor,
      endMs: cursor,
      steps: [],
    };

    steps.forEach((step, stepIndex) => {
      let stepStart = cursor;
      let durationMs = Math.max(250, normalizeDwellMs(step));
      if (usePerStepTiming) {
        const rawStart = Number(step.audio_start_ms);
        const rawEnd = Number(step.audio_end_ms);
        stepStart = Math.max(cursor, Math.round(rawStart));
        durationMs = Math.max(250, Math.round(rawEnd) - Math.round(rawStart));
      }
      const stepEnd = stepStart + durationMs;
      slide.steps.push({
        stepIndex,
        startMs: stepStart,
        endMs: stepEnd,
      });
      cursor = stepEnd;
    });

    slide.endMs = cursor;
    timeline.push(slide);
  });

  state.playbackTimeline = timeline;
  state.totalTimelineMs = cursor;
}

function getCurrentGlobalMs() {
  const slideEntry = state.playbackTimeline[state.currentSlideIndex];
  if (!slideEntry) {
    return 0;
  }
  if (!slideEntry.steps.length) {
    return slideEntry.startMs;
  }
  const step = slideEntry.steps[Math.max(0, Math.min(state.currentStepIndex, slideEntry.steps.length - 1))];
  return step ? step.startMs : slideEntry.startMs;
}

function locateTimelinePosition(globalMs) {
  const timeline = state.playbackTimeline;
  if (!timeline.length) {
    return null;
  }

  const clamped = Math.max(0, Math.min(globalMs, state.totalTimelineMs || globalMs));
  for (let slideIdx = 0; slideIdx < timeline.length; slideIdx += 1) {
    const slide = timeline[slideIdx];
    const isLastSlide = slideIdx === timeline.length - 1;
    if (clamped < slide.endMs || isLastSlide) {
      if (!slide.steps.length) {
        return { slideIndex: slideIdx, stepIndex: 0 };
      }
      for (let stepIdx = 0; stepIdx < slide.steps.length; stepIdx += 1) {
        const step = slide.steps[stepIdx];
        const isLastStep = stepIdx === slide.steps.length - 1;
        if (clamped < step.endMs || (isLastSlide && isLastStep)) {
          return { slideIndex: slideIdx, stepIndex: stepIdx };
        }
      }
      return { slideIndex: slideIdx, stepIndex: slide.steps.length - 1 };
    }
  }
  return { slideIndex: timeline.length - 1, stepIndex: 0 };
}

function formatClock(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const min = String(Math.floor(total / 60)).padStart(2, "0");
  const sec = String(total % 60).padStart(2, "0");
  return `${min}:${sec}`;
}

function clearPlaybackTimer() {
  if (state.timer) {
    clearTimeout(state.timer);
    state.timer = null;
  }
}

function setPlaying(next) {
  state.isPlaying = next;
  playBtn.textContent = next ? "\u23f8" : "\u25b6";
  playBtn.setAttribute("aria-label", next ? "Pause script" : "Play script");
  if (next && hasNarrationAudio() && !state.audioSyncInterval) {
    state.audioSyncInterval = setInterval(() => {
      if (state.isPlaying) {
        syncUiToAudioPosition();
      }
    }, 120);
  }
  if (!next) {
    clearPlaybackTimer();
    if (state.audioSyncInterval) {
      clearInterval(state.audioSyncInterval);
      state.audioSyncInterval = null;
    }
  }
}

function hasNarrationAudio() {
  return Boolean(state.narrationAudio && state.lecture?.audio_url);
}

function teardownNarrationAudio() {
  if (!state.narrationAudio) {
    return;
  }
  if (state.audioSyncInterval) {
    clearInterval(state.audioSyncInterval);
    state.audioSyncInterval = null;
  }
  state.narrationAudio.pause();
  state.narrationAudio.src = "";
  state.narrationAudio = null;
}

function syncUiToAudioPosition() {
  if (!hasNarrationAudio()) {
    return;
  }
  const audio = state.narrationAudio;
  const location = locateTimelinePosition(audio.currentTime * 1000);
  if (!location) {
    updateMeta();
    return;
  }

  const sameSlide = location.slideIndex === state.currentSlideIndex;
  const sameStep = location.stepIndex === state.currentStepIndex;
  if (sameSlide && sameStep) {
    updateMeta();
    return;
  }

  if (!sameSlide) {
    state.currentSlideIndex = location.slideIndex;
    state.currentStepIndex = location.stepIndex;
    state.currentImageName = "";
    state.renderedStepKey = "";
    renderAll();
    return;
  }
  goToStep(location.stepIndex);
}

function seekAudioToCurrentUiPosition() {
  if (!hasNarrationAudio()) {
    return;
  }
  const targetMs = getCurrentGlobalMs();
  state.narrationAudio.currentTime = Math.max(0, targetMs / 1000);
  syncUiToAudioPosition();
}

function setupNarrationAudio(audioUrl) {
  teardownNarrationAudio();
  if (!audioUrl) {
    return;
  }

  const audio = new Audio(audioUrl);
  audio.preload = "metadata";

  audio.addEventListener("loadedmetadata", () => {
    syncUiToAudioPosition();
    updateMeta();
  });
  audio.addEventListener("timeupdate", () => {
    if (state.isPlaying) {
      syncUiToAudioPosition();
    } else {
      updateMeta();
    }
  });
  audio.addEventListener("ended", () => {
    setPlaying(false);
    syncUiToAudioPosition();
  });
  audio.addEventListener("seeked", () => {
    syncUiToAudioPosition();
  });
  audio.addEventListener("error", () => {
    // If the audio URL is stale/missing, keep script playback working as fallback.
    teardownNarrationAudio();
    updateMeta();
  });

  state.narrationAudio = audio;
}

function clampPlaybackRate(value) {
  if (!Number.isFinite(value)) {
    return 1.0;
  }
  return Math.max(0.5, Math.min(5.0, value));
}

function setPlaybackRate(value, options = {}) {
  const nextRate = clampPlaybackRate(value);
  state.playbackRate = nextRate;
  if (hasNarrationAudio()) {
    state.narrationAudio.playbackRate = nextRate;
  }
  if (rateLabel) {
    rateLabel.textContent = `${nextRate.toFixed(1)}x`;
  }
  if (speedRange) {
    speedRange.value = String(Math.round(nextRate * 100));
  }
  if (options.reschedule && state.isPlaying && !hasNarrationAudio()) {
    schedulePlaybackTick();
  }
}

function setMainImageSource(url) {
  if (mainImage.getAttribute("src") !== url) {
    mainImage.src = url;
  }
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

function preloadRenderedForSlide(slide) {
  if (!slide || !Array.isArray(slide.rendered_step_urls)) {
    return;
  }
  slide.rendered_step_urls.forEach((url) => {
    if (!url || state.preloadedUrls.has(url)) {
      return;
    }
    const img = new Image();
    img.src = url;
    state.preloadedUrls.add(url);
  });
}

function hideTextTransitionCanvas() {
  if (!textTransitionCanvas) {
    return;
  }
  const ctx = textTransitionCanvas.getContext("2d");
  if (ctx) {
    ctx.clearRect(0, 0, textTransitionCanvas.width, textTransitionCanvas.height);
  }
  textTransitionCanvas.style.display = "none";
}

function cancelTextTransition() {
  state.transitionToken += 1;
  hideTextTransitionCanvas();
}

function loadTransitionImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Failed to load transition image: ${url}`));
    img.src = url;
  });
}

function buildCharacterSegments(textRegions) {
  const segments = [];
  textRegions.forEach((region) => {
    if (!region || !Array.isArray(region.bbox) || region.bbox.length !== 4) {
      return;
    }
    const [x1Raw, y1Raw, x2Raw, y2Raw] = region.bbox;
    const x1 = Math.floor(Math.min(x1Raw, x2Raw));
    const x2 = Math.ceil(Math.max(x1Raw, x2Raw));
    const y1 = Math.floor(Math.min(y1Raw, y2Raw));
    const y2 = Math.ceil(Math.max(y1Raw, y2Raw));
    if (x2 <= x1 || y2 <= y1) {
      return;
    }

    const text = typeof region.display === "string" && region.display.trim() ? region.display.trim() : "x";
    const chars = Math.max(1, text.length);
    const segmentWidth = Math.max(5, Math.floor((x2 - x1) / chars));

    let xPos = x1;
    while (xPos < x2) {
      const segEnd = Math.min(xPos + segmentWidth, x2);
      segments.push({ x1: xPos, x2: segEnd, y1, y2 });
      xPos = segEnd;
    }
  });

  segments.sort((a, b) => (a.y1 === b.y1 ? a.x1 - b.x1 : a.y1 - b.y1));
  return segments;
}

async function animateTextTransition({ token, startUrl, endUrl, textRegions }) {
  if (!textTransitionCanvas || !textRegions.length) {
    return false;
  }
  const slide = getCurrentSlide();
  if (!slide) {
    return false;
  }

  let startImg;
  let endImg;
  try {
    [startImg, endImg] = await Promise.all([loadTransitionImage(startUrl), loadTransitionImage(endUrl)]);
  } catch {
    return false;
  }

  if (token !== state.transitionToken) {
    return false;
  }

  const rect = mainImage.getBoundingClientRect();
  const containerRect = imageContainer.getBoundingClientRect();
  if (!rect.width || !rect.height || !containerRect.width || !containerRect.height) {
    return false;
  }

  const sourceWidth = Number(slide.image_width) || endImg.naturalWidth || startImg.naturalWidth || 1;
  const sourceHeight = Number(slide.image_height) || endImg.naturalHeight || startImg.naturalHeight || 1;
  const rawSegments = buildCharacterSegments(textRegions);
  const segments = rawSegments
    .map((seg) => ({
      x1: Math.max(0, Math.min(sourceWidth, seg.x1)),
      x2: Math.max(0, Math.min(sourceWidth, seg.x2)),
      y1: Math.max(0, Math.min(sourceHeight, seg.y1)),
      y2: Math.max(0, Math.min(sourceHeight, seg.y2)),
    }))
    .filter((seg) => seg.x2 > seg.x1 && seg.y2 > seg.y1);

  if (!segments.length) {
    return false;
  }

  const dpr = window.devicePixelRatio || 1;
  const canvas = textTransitionCanvas;
  canvas.style.display = "block";
  canvas.style.left = `${rect.left - containerRect.left}px`;
  canvas.style.top = `${rect.top - containerRect.top}px`;
  canvas.style.width = `${rect.width}px`;
  canvas.style.height = `${rect.height}px`;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    hideTextTransitionCanvas();
    return false;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const drawStartFrame = () => {
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.drawImage(startImg, 0, 0, sourceWidth, sourceHeight, 0, 0, rect.width, rect.height);
  };

  drawStartFrame();
  const durationMs = 800;
  const totalSegments = segments.length;
  const scaleX = rect.width / sourceWidth;
  const scaleY = rect.height / sourceHeight;

  return await new Promise((resolve) => {
    const started = performance.now();
    const tick = (now) => {
      if (token !== state.transitionToken) {
        hideTextTransitionCanvas();
        resolve(false);
        return;
      }

      const progress = Math.max(0, Math.min(1, (now - started) / durationMs));
      const segmentsToReveal = Math.max(0, Math.min(totalSegments, Math.floor(progress * totalSegments)));

      drawStartFrame();
      for (let idx = 0; idx < segmentsToReveal; idx += 1) {
        const seg = segments[idx];
        const sx = seg.x1;
        const sy = seg.y1;
        const sw = seg.x2 - seg.x1;
        const sh = seg.y2 - seg.y1;
        const dx = sx * scaleX;
        const dy = sy * scaleY;
        const dw = sw * scaleX;
        const dh = sh * scaleY;
        ctx.drawImage(endImg, sx, sy, sw, sh, dx, dy, dw, dh);
      }

      if (progress >= 1) {
        hideTextTransitionCanvas();
        resolve(true);
        return;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

async function applyPrecomputedStepImage(stepIndex, activeTextRegions = []) {
  const slide = getCurrentSlide();
  if (!slide || state.currentTab !== "script") {
    return;
  }
  const steps = getCurrentSteps();
  if (stepIndex < 0 || stepIndex >= steps.length) {
    return;
  }

  preloadRenderedForSlide(slide);
  const stepKey = `${slide.image_name}:${stepIndex}`;
  const renderedUrl = getRenderedStepUrl(slide, stepIndex) || slide.image_url;
  if (state.renderedStepKey === stepKey && mainImage.getAttribute("src") === renderedUrl) {
    return;
  }
  state.renderedStepKey = stepKey;

  const startUrl = mainImage.getAttribute("src") || slide.image_url;
  if (!renderedUrl || startUrl === renderedUrl || !Array.isArray(activeTextRegions) || !activeTextRegions.length) {
    cancelTextTransition();
    setMainImageSource(renderedUrl || slide.image_url);
    return;
  }

  const token = state.transitionToken + 1;
  state.transitionToken = token;
  await animateTextTransition({
    token,
    startUrl,
    endUrl: renderedUrl,
    textRegions: activeTextRegions,
  });

  if (token !== state.transitionToken) {
    return;
  }
  setMainImageSource(renderedUrl);
}

function updatePolygonMetrics(polygonEl) {
  if (!(polygonEl instanceof SVGPolygonElement)) {
    return;
  }
  try {
    const len = polygonEl.getTotalLength();
    if (Number.isFinite(len) && len > 1) {
      polygonEl.style.setProperty("--poly-len", String(len));
      if (!polygonEl.classList.contains("active") && !polygonEl.classList.contains("entering")) {
        polygonEl.style.strokeDasharray = String(len);
        polygonEl.style.strokeDashoffset = String(len);
      }
    }
  } catch {
    // Ignore invalid polygons during transient layout states.
  }
}

function mapScaledPolygon(poly, offsetX, offsetY, scaleX, scaleY) {
  if (!Array.isArray(poly)) {
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
    scaled.push([offsetX + x * scaleX, offsetY + y * scaleY]);
  });
  return scaled;
}

function toSvgPolygonPoints(points) {
  return points.map((pt) => `${pt[0]},${pt[1]}`).join(" ");
}

function toCssPolygonPoints(points) {
  return points.map((pt) => `${pt[0]}px ${pt[1]}px`).join(", ");
}

function clearHighlights() {
  highlightOverlay
    .querySelectorAll(".highlight-box, .highlight-polygon, .highlight-lift, .highlight-lift-underlay")
    .forEach((el) => el.classList.remove("active", "entering", "exiting"));
}

function applyOverlayActivation(nextActiveIds) {
  const elements = highlightOverlay.querySelectorAll("[data-id]");
  elements.forEach((el) => {
    const id = el.dataset.id || "";
    const shouldBeActive = nextActiveIds.has(id);
    if (shouldBeActive) {
      if (el.classList.contains("highlight-polygon")) {
        updatePolygonMetrics(el);
      }
      el.classList.add("active");
    } else {
      el.classList.remove("active");
    }
  });
}

function setActiveScriptRow(index) {
  scriptPanel.querySelectorAll(".script-item").forEach((row) => {
    const rowIndex = Number.parseInt(row.dataset.stepIndex || "-1", 10);
    row.classList.toggle("script-item-active", rowIndex === index);
  });
}

function updateMeta() {
  const slide = getCurrentSlide();
  if (!slide) {
    return;
  }
  const steps = getCurrentSteps();
  const totalSlides = state.lecture?.slides?.length || 0;
  const current = getCurrentStep();
  const slideTimeline = state.playbackTimeline[state.currentSlideIndex];
  const timelineStep =
    slideTimeline && slideTimeline.steps.length > 0
      ? slideTimeline.steps[Math.max(0, Math.min(state.currentStepIndex, slideTimeline.steps.length - 1))]
      : null;
  const useTimeline = hasNarrationAudio();

  slideCounter.textContent = `Slide ${slide.slide_number}${totalSlides ? ` of ${totalSlides}` : ""}`;

  const fallbackTotalMs = steps.reduce((sum, step) => sum + normalizeDwellMs(step), 0);
  const startMs = useTimeline && timelineStep ? timelineStep.startMs : current ? current.start_ms || 0 : 0;
  const endMs = useTimeline && timelineStep ? timelineStep.endMs : current ? startMs + normalizeDwellMs(current) : 0;
  const totalMs = useTimeline && state.totalTimelineMs > 0 ? state.totalTimelineMs : fallbackTotalMs;

  stepCounter.textContent = `${formatClock(startMs)} - ${formatClock(endMs)}`;
  if (hasNarrationAudio() && Number.isFinite(state.narrationAudio.duration) && state.narrationAudio.duration > 0) {
    const currentAudioMs = Math.max(0, state.narrationAudio.currentTime * 1000);
    const totalAudioMs = Math.max(0, state.narrationAudio.duration * 1000);
    trackTime.textContent = `${formatClock(currentAudioMs)} / ${formatClock(totalAudioMs)}`;
  } else {
    trackTime.textContent = `${formatClock(startMs)} / ${formatClock(totalMs)}`;
  }
  trackTitle.textContent = `${state.lecture.title} • Slide ${slide.slide_number}`;
}

function createHighlightBoxes() {
  highlightOverlay.innerHTML = "";
  const slide = getCurrentSlide();
  if (!slide) {
    return;
  }

  const regions = Array.isArray(slide.regions) ? slide.regions : [];
  const clusters = Array.isArray(slide.clusters) ? slide.clusters : [];
  const groups = Array.isArray(slide.groups) ? slide.groups : [];

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.id = "highlightSvg";
  svg.style.cssText = "position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;";
  highlightOverlay.appendChild(svg);

  regions.forEach((region) => {
    const regionId = region?.id;
    const kind = region?.kind;
    if (!regionId || !kind) {
      return;
    }

    const appendVisualLift = (partIndex) => {
      const part = Number.isInteger(partIndex) ? String(partIndex) : null;
      const underlay = document.createElement("div");
      underlay.dataset.id = regionId;
      underlay.dataset.type = "lift-underlay";
      if (part !== null) {
        underlay.dataset.part = part;
      }
      underlay.classList.add("highlight-lift-underlay", "visual");
      highlightOverlay.appendChild(underlay);

      const lift = document.createElement("div");
      lift.dataset.id = regionId;
      lift.dataset.type = "lift";
      if (part !== null) {
        lift.dataset.part = part;
      }
      lift.classList.add("highlight-lift", "visual");
      highlightOverlay.appendChild(lift);
    };

    if (kind === "visual") {
      if (Array.isArray(region.polygons) && region.polygons.length > 0) {
        region.polygons.forEach((_, idx) => {
          appendVisualLift(idx);
        });
        return;
      }

      if (Array.isArray(region.polygon) && region.polygon.length >= 3) {
        appendVisualLift(0);
      }
      // Visual detections are polygon-only; do not fallback to bbox.
      return;
    }

    const appendPolygon = (partIndex) => {
      const part = Number.isInteger(partIndex) ? String(partIndex) : null;
      const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      polygon.dataset.id = regionId;
      polygon.dataset.type = "polygon";
      if (part !== null) {
        polygon.dataset.part = part;
      }
      polygon.classList.add("highlight-polygon", kind);
      svg.appendChild(polygon);
    };

    if (Array.isArray(region.polygons) && region.polygons.length > 0) {
      region.polygons.forEach((_, idx) => {
        appendPolygon(idx);
      });
      return;
    }

    if (Array.isArray(region.polygon) && region.polygon.length >= 3) {
      appendPolygon(0);
      return;
    }

    if (Array.isArray(region.bbox) && region.bbox.length === 4) {
      const box = document.createElement("div");
      box.className = `highlight-box ${kind}`;
      box.dataset.id = regionId;
      highlightOverlay.appendChild(box);
    }
  });

  clusters.forEach((cluster) => {
    if (!cluster?.id || !Array.isArray(cluster.bbox) || cluster.bbox.length !== 4) {
      return;
    }
    const box = document.createElement("div");
    box.className = "highlight-box cluster";
    box.dataset.id = cluster.id;
    highlightOverlay.appendChild(box);
  });

  groups.forEach((group) => {
    if (!group?.id || !Array.isArray(group.bbox) || group.bbox.length !== 4) {
      return;
    }
    const box = document.createElement("div");
    box.className = "highlight-box group";
    box.dataset.id = group.id;
    highlightOverlay.appendChild(box);
  });

  updateHighlightPositions();
}

function updateHighlightPositions() {
  const slide = getCurrentSlide();
  if (!slide) {
    return;
  }
  const rect = mainImage.getBoundingClientRect();
  const containerRect = imageContainer.getBoundingClientRect();
  if (!rect.width || !rect.height || !containerRect.width || !containerRect.height) {
    return;
  }

  const imageNaturalWidth = Number(slide.image_width) || mainImage.naturalWidth || 1;
  const imageNaturalHeight = Number(slide.image_height) || mainImage.naturalHeight || 1;
  if (!imageNaturalWidth || !imageNaturalHeight) {
    return;
  }

  const offsetX = rect.left - containerRect.left;
  const offsetY = rect.top - containerRect.top;
  const scaleX = rect.width / imageNaturalWidth;
  const scaleY = rect.height / imageNaturalHeight;
  const currentImageUrl = mainImage.currentSrc || mainImage.getAttribute("src") || slide.image_url || "";
  const escapedImageUrl = currentImageUrl.replace(/(["\\])/g, "\\$1");
  const liftBackgroundImage = currentImageUrl ? `url("${escapedImageUrl}")` : "none";
  const liftBackgroundSize = `${rect.width}px ${rect.height}px`;
  const liftBackgroundPosition = `${offsetX}px ${offsetY}px`;

  const regions = Array.isArray(slide.regions) ? slide.regions : [];
  const clusters = Array.isArray(slide.clusters) ? slide.clusters : [];
  const groups = Array.isArray(slide.groups) ? slide.groups : [];

  regions.forEach((region) => {
    if (!region?.id) {
      return;
    }

    if (region.kind === "visual") {
      const visualLayers = highlightOverlay.querySelectorAll(
        `.highlight-lift[data-id="${region.id}"], .highlight-lift-underlay[data-id="${region.id}"]`
      );
      if (!visualLayers.length) {
        return;
      }

      const applyLiftPolygon = (layerEl, poly) => {
        const scaled = mapScaledPolygon(poly, offsetX, offsetY, scaleX, scaleY);
        if (scaled.length < 3) {
          layerEl.style.display = "none";
          layerEl.style.clipPath = "none";
          layerEl.style.webkitClipPath = "none";
          if (layerEl.classList.contains("highlight-lift")) {
            layerEl.style.backgroundImage = "none";
          }
          return;
        }

        layerEl.style.display = "";
        if (layerEl.classList.contains("highlight-lift")) {
          layerEl.style.backgroundImage = liftBackgroundImage;
          layerEl.style.backgroundSize = liftBackgroundSize;
          layerEl.style.backgroundPosition = liftBackgroundPosition;
        }

        const cssPolygon = `polygon(${toCssPolygonPoints(scaled)})`;
        layerEl.style.clipPath = cssPolygon;
        layerEl.style.webkitClipPath = cssPolygon;
      };

      if (Array.isArray(region.polygons) && region.polygons.length > 0) {
        visualLayers.forEach((layerEl) => {
          const idx = Number.parseInt(layerEl.dataset.part || "0", 10);
          const poly = region.polygons[idx];
          applyLiftPolygon(layerEl, poly);
        });
        return;
      }

      if (Array.isArray(region.polygon) && region.polygon.length >= 3) {
        visualLayers.forEach((layerEl) => {
          applyLiftPolygon(layerEl, region.polygon);
        });
      }
      // Visual detections stay polygon-only on position updates too.
      return;
    }

    if (Array.isArray(region.polygons) && region.polygons.length > 0) {
      const parts = highlightOverlay.querySelectorAll(`polygon[data-id="${region.id}"]`);
      parts.forEach((part) => {
        const idx = Number.parseInt(part.dataset.part || "0", 10);
        const scaled = mapScaledPolygon(region.polygons[idx], offsetX, offsetY, scaleX, scaleY);
        if (scaled.length < 3) {
          return;
        }
        const points = toSvgPolygonPoints(scaled);
        part.setAttribute("points", points);
        updatePolygonMetrics(part);
      });
      return;
    }

    if (Array.isArray(region.polygon) && region.polygon.length >= 3) {
      const polygonEls = highlightOverlay.querySelectorAll(`polygon[data-id="${region.id}"]`);
      if (!polygonEls.length) {
        return;
      }
      const scaled = mapScaledPolygon(region.polygon, offsetX, offsetY, scaleX, scaleY);
      if (scaled.length < 3) {
        return;
      }
      const points = toSvgPolygonPoints(scaled);
      polygonEls.forEach((polygonEl) => {
        polygonEl.setAttribute("points", points);
        updatePolygonMetrics(polygonEl);
      });
      return;
    }

    if (Array.isArray(region.bbox) && region.bbox.length === 4) {
      const box = highlightOverlay.querySelector(`div[data-id="${region.id}"]`);
      if (!box) {
        return;
      }
      const [x1, y1, x2, y2] = region.bbox;
      const pad = 4;
      box.style.left = `${offsetX + x1 * scaleX - pad}px`;
      box.style.top = `${offsetY + y1 * scaleY - pad}px`;
      box.style.width = `${(x2 - x1) * scaleX + pad * 2}px`;
      box.style.height = `${(y2 - y1) * scaleY + pad * 2}px`;
    }
  });

  clusters.forEach((cluster) => {
    if (!cluster?.id || !Array.isArray(cluster.bbox) || cluster.bbox.length !== 4) {
      return;
    }
    const box = highlightOverlay.querySelector(`div[data-id="${cluster.id}"]`);
    if (!box) {
      return;
    }
    const [x1, y1, x2, y2] = cluster.bbox;
    const pad = 8;
    box.style.left = `${offsetX + x1 * scaleX - pad}px`;
    box.style.top = `${offsetY + y1 * scaleY - pad}px`;
    box.style.width = `${(x2 - x1) * scaleX + pad * 2}px`;
    box.style.height = `${(y2 - y1) * scaleY + pad * 2}px`;
  });

  groups.forEach((group) => {
    if (!group?.id || !Array.isArray(group.bbox) || group.bbox.length !== 4) {
      return;
    }
    const box = highlightOverlay.querySelector(`div[data-id="${group.id}"]`);
    if (!box) {
      return;
    }
    const [x1, y1, x2, y2] = group.bbox;
    const pad = 8;
    box.style.left = `${offsetX + x1 * scaleX - pad}px`;
    box.style.top = `${offsetY + y1 * scaleY - pad}px`;
    box.style.width = `${(x2 - x1) * scaleX + pad * 2}px`;
    box.style.height = `${(y2 - y1) * scaleY + pad * 2}px`;
  });
}

function highlightStep(index) {
  const slide = getCurrentSlide();
  const steps = getCurrentSteps();
  if (!slide || index < 0 || index >= steps.length) {
    clearHighlights();
    return;
  }

  state.currentStepIndex = index;
  setActiveScriptRow(index);
  updateMeta();

  const step = steps[index];
  const regions = Array.isArray(slide.regions) ? slide.regions : [];
  const regionMap = new Map();
  regions.forEach((region) => {
    if (region && typeof region.id === "string") {
      regionMap.set(region.id, region);
    }
  });
  const clusters = Array.isArray(slide.clusters) ? slide.clusters : [];
  const groups = Array.isArray(slide.groups) ? slide.groups : [];
  const clusterMap = new Map();
  clusters.forEach((cluster) => {
    if (cluster && typeof cluster.id === "string") {
      clusterMap.set(cluster.id, cluster);
    }
  });
  const groupMap = new Map();
  groups.forEach((group) => {
    if (group && typeof group.id === "string") {
      groupMap.set(group.id, group);
    }
  });

  function activateId(id, visited) {
    if (!id || visited.has(id)) {
      return;
    }
    visited.add(id);

    if (id.startsWith("g:")) {
      const group = groupMap.get(id);
      if (group && Array.isArray(group.children)) {
        group.children.forEach((childId) => activateId(childId, visited));
      }
      return;
    }

    if (id.startsWith("c:")) {
      const cluster = clusterMap.get(id);
      if (cluster && Array.isArray(cluster.region_ids)) {
        cluster.region_ids.forEach((regionId) => activateId(regionId, visited));
      }
    }
  }

  const visited = new Set();
  (Array.isArray(step.region_ids) ? step.region_ids : []).forEach((id) => activateId(id, visited));

  const activeOverlayIds = new Set();
  const activeTextRegions = [];
  visited.forEach((id) => {
    const region = regionMap.get(id);
    if (region && region.kind === "text") {
      activeTextRegions.push(region);
      return;
    }
    activeOverlayIds.add(id);
  });

  applyOverlayActivation(activeOverlayIds);
  void applyPrecomputedStepImage(index, activeTextRegions);
}

function renderScriptPanel() {
  scriptPanel.innerHTML = "";
  const slide = getCurrentSlide();
  if (!slide) {
    return;
  }

  if (state.currentTab !== "script") {
    scriptTitle.textContent = state.currentTab === "notes" ? "Lecture Notes" : "Resources";
    const placeholder = document.createElement("p");
    placeholder.className = "script-placeholder";
    placeholder.textContent =
      state.currentTab === "notes"
        ? "Notes view is a placeholder in this version."
        : "Resources view is a placeholder in this version.";
    scriptPanel.appendChild(placeholder);
    return;
  }

  scriptTitle.textContent = slide.script_title || "Lecture Script";
  const steps = getCurrentSteps();
  if (steps.length === 0) {
    const placeholder = document.createElement("p");
    placeholder.className = "script-placeholder";
    placeholder.textContent = "No script was generated for this slide.";
    scriptPanel.appendChild(placeholder);
    return;
  }

  steps.forEach((step, idx) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `script-item${idx === state.currentStepIndex ? " script-item-active" : ""}`;
    row.dataset.stepIndex = String(idx);

    const time = document.createElement("span");
    time.className = "script-time";
    time.textContent = formatClock(step.start_ms || 0);
    row.appendChild(time);

    const line = document.createElement("p");
    line.className = "script-line";
    line.textContent = step.line || "";
    row.appendChild(line);

    scriptPanel.appendChild(row);
  });
}

function renderDots() {
  dotsTrack.innerHTML = "";
  const slides = state.lecture?.slides || [];
  slides.forEach((slide, idx) => {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = `slide-dot${idx === state.currentSlideIndex ? " slide-dot-active" : ""}`;
    dot.dataset.slideIndex = String(idx);
    dot.title = `Slide ${slide.slide_number}`;
    dotsTrack.appendChild(dot);
  });
}

function renderSlideImage() {
  const slide = getCurrentSlide();
  if (!slide) {
    cancelTextTransition();
    state.currentImageName = "";
    mainImage.removeAttribute("src");
    slideMessage.textContent = "No slides are available for this lecture.";
    slideMessage.style.display = "block";
    return;
  }

  if (state.currentImageName !== slide.image_name) {
    cancelTextTransition();
    state.currentImageName = slide.image_name;
    state.renderedStepKey = "";
    slideMessage.textContent = "Loading slide...";
    slideMessage.style.display = "block";
    preloadRenderedForSlide(slide);
    setMainImageSource(slide.image_url);
  }
}

function renderAll() {
  renderSlideImage();
  renderScriptPanel();
  createHighlightBoxes();
  renderDots();
  updateMeta();

  if (state.currentTab === "script") {
    const steps = getCurrentSteps();
    if (steps.length > 0) {
      const safeIndex = Math.max(0, Math.min(state.currentStepIndex, steps.length - 1));
      highlightStep(safeIndex);
    } else {
      clearHighlights();
    }
  } else {
    clearHighlights();
    const slide = getCurrentSlide();
    if (slide?.image_url) {
      cancelTextTransition();
      state.renderedStepKey = "";
      setMainImageSource(slide.image_url);
    }
  }
}

function goToSlide(index) {
  const slides = state.lecture?.slides || [];
  if (slides.length === 0) {
    return;
  }
  const safeIndex = Math.max(0, Math.min(index, slides.length - 1));
  state.currentSlideIndex = safeIndex;
  state.currentStepIndex = 0;
  state.currentImageName = "";
  state.renderedStepKey = "";
  renderAll();
}

function goToStep(index) {
  const steps = getCurrentSteps();
  if (steps.length === 0) {
    state.currentStepIndex = 0;
    clearHighlights();
    updateMeta();
    return;
  }
  const safeIndex = Math.max(0, Math.min(index, steps.length - 1));
  highlightStep(safeIndex);
}

function schedulePlaybackTick() {
  clearPlaybackTimer();
  if (!state.isPlaying) {
    return;
  }

  const steps = getCurrentSteps();
  if (steps.length === 0) {
    void refreshLecturePayload();
    setPlaying(false);
    return;
  }

  const current = getCurrentStep();
  const dwellMs = current?.dwell_ms || 4000;
  const adjustedDwellMs = Math.max(120, Math.round(dwellMs / state.playbackRate));
  state.timer = setTimeout(() => {
    if (!state.isPlaying) {
      return;
    }

    const nextStep = state.currentStepIndex + 1;
    if (nextStep < steps.length) {
      highlightStep(nextStep);
      schedulePlaybackTick();
      return;
    }

    const slides = state.lecture?.slides || [];
    const nextSlide = state.currentSlideIndex + 1;
    if (nextSlide < slides.length) {
      goToSlide(nextSlide);
      schedulePlaybackTick();
      return;
    }

    setPlaying(false);
  }, adjustedDwellMs);
}

async function loadLecture() {
  const jobId = getJobIdFromPath();
  state.jobId = jobId;
  if (!jobId) {
    lectureTitle.textContent = "Invalid lecture URL";
    lectureSubhead.textContent = "Missing job id";
    slideMessage.textContent = "No job id was provided.";
    slideMessage.style.display = "block";
    return;
  }

  lectureSubhead.textContent = `JOB ${jobId}`;

  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/lecture`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.detail || `Request failed: ${response.status}`);
    }

    await applyLecturePayload(payload, { preservePosition: false });
    ensureLectureRefreshPolling();

    lectureTitle.textContent = payload.title || jobId;
    lectureSubhead.textContent = `MODULE 1 • JOB ${jobId}`;
    downloadPdfBtn.href = payload.input_pdf_url || "#";
  } catch (error) {
    lectureTitle.textContent = "Failed to load lecture";
    lectureSubhead.textContent = `JOB ${jobId}`;
    slideMessage.textContent = error.message || "Unable to load lecture payload.";
    slideMessage.style.display = "block";
  }
}

mainImage.addEventListener("load", () => {
  slideMessage.style.display = "none";
  updateHighlightPositions();
});

mainImage.addEventListener("error", () => {
  slideMessage.textContent = "Slide image could not be loaded.";
  slideMessage.style.display = "block";
});

scriptPanel.addEventListener("click", (event) => {
  const row = event.target.closest(".script-item");
  if (!row) {
    return;
  }
  const next = Number.parseInt(row.dataset.stepIndex || "0", 10);
  if (hasNarrationAudio() && state.isPlaying) {
    state.narrationAudio.pause();
  }
  setPlaying(false);
  goToStep(Number.isNaN(next) ? 0 : next);
  seekAudioToCurrentUiPosition();
});

scriptPanel.addEventListener("mouseover", (event) => {
  if (state.isPlaying || state.currentTab !== "script") {
    return;
  }
  const row = event.target.closest(".script-item");
  if (!row) {
    return;
  }
  const next = Number.parseInt(row.dataset.stepIndex || "0", 10);
  if (!Number.isNaN(next)) {
    highlightStep(next);
  }
});

dotsTrack.addEventListener("click", (event) => {
  const dot = event.target.closest(".slide-dot");
  if (!dot) {
    return;
  }
  const next = Number.parseInt(dot.dataset.slideIndex || "0", 10);
  if (hasNarrationAudio() && state.isPlaying) {
    state.narrationAudio.pause();
  }
  setPlaying(false);
  goToSlide(Number.isNaN(next) ? 0 : next);
  seekAudioToCurrentUiPosition();
});

prevSlideBtn.addEventListener("click", () => {
  if (hasNarrationAudio() && state.isPlaying) {
    state.narrationAudio.pause();
  }
  setPlaying(false);
  goToSlide(state.currentSlideIndex - 1);
  seekAudioToCurrentUiPosition();
});

nextSlideBtn.addEventListener("click", () => {
  if (hasNarrationAudio() && state.isPlaying) {
    state.narrationAudio.pause();
  }
  setPlaying(false);
  goToSlide(state.currentSlideIndex + 1);
  seekAudioToCurrentUiPosition();
});

playBtn.addEventListener("click", async () => {
  if (!state.lecture || state.currentTab !== "script") {
    return;
  }

  if (hasNarrationAudio()) {
    if (state.isPlaying) {
      state.narrationAudio.pause();
      setPlaying(false);
      return;
    }

    if (state.narrationAudio.ended) {
      state.narrationAudio.currentTime = 0;
      state.currentSlideIndex = 0;
      state.currentStepIndex = 0;
      state.currentImageName = "";
      state.renderedStepKey = "";
      renderAll();
    }

    setPlaying(true);
    try {
      await state.narrationAudio.play();
      syncUiToAudioPosition();
    } catch {
      setPlaying(false);
    }
    return;
  }

  const next = !state.isPlaying;
  setPlaying(next);
  if (next) {
    const steps = getCurrentSteps();
    if (steps.length === 0) {
      setPlaying(false);
      return;
    }
    state.currentStepIndex = 0;
    highlightStep(state.currentStepIndex);
    schedulePlaybackTick();
  }
});

speedRange.addEventListener("input", () => {
  const raw = Number.parseFloat(speedRange.value || "100");
  setPlaybackRate(raw / 100, { reschedule: true });
});

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    if (state.isPlaying && hasNarrationAudio()) {
      state.narrationAudio.pause();
      setPlaying(false);
    }
    state.currentTab = btn.dataset.tab || "script";
    tabButtons.forEach((node) => {
      node.classList.toggle("lecture-tab-active", node === btn);
    });
    renderScriptPanel();
    if (state.currentTab === "script") {
      createHighlightBoxes();
      goToStep(state.currentStepIndex);
    } else {
      cancelTextTransition();
      clearHighlights();
    }
  });
});

scriptSearchBtn.addEventListener("click", () => {
  const message = document.createElement("p");
  message.className = "script-placeholder";
  message.textContent = "Search is a placeholder in this version.";
  scriptPanel.prepend(message);
  setTimeout(() => message.remove(), 1400);
});

window.addEventListener("resize", updateHighlightPositions);
if (window.ResizeObserver) {
  new ResizeObserver(updateHighlightPositions).observe(imageContainer);
}
window.addEventListener("beforeunload", stopLectureRefreshPolling);

setPlaybackRate(1.0);
loadLecture();

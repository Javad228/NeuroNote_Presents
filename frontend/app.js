const jobsGrid = document.getElementById("jobsGrid");
const statusEl = document.getElementById("status");
const searchInput = document.getElementById("searchInput");
const newProjectBtn = document.getElementById("newProjectBtn");

let allJobs = [];

function escapeHtml(input) {
  return String(input)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function relativeTime(isoValue) {
  if (!isoValue) {
    return "Unknown time";
  }

  const then = new Date(isoValue).getTime();
  if (Number.isNaN(then)) {
    return "Unknown time";
  }

  const deltaSec = Math.floor((Date.now() - then) / 1000);
  if (deltaSec < 60) {
    return "Just now";
  }
  if (deltaSec < 3600) {
    const min = Math.floor(deltaSec / 60);
    return `${min} min ago`;
  }
  if (deltaSec < 86400) {
    const hr = Math.floor(deltaSec / 3600);
    return `${hr} hr ago`;
  }
  if (deltaSec < 604800) {
    const day = Math.floor(deltaSec / 86400);
    return `${day} day ago`;
  }

  return new Date(then).toLocaleDateString();
}

function buildJobCard(job) {
  const title = escapeHtml(job.title || job.job_id);
  const timeLabel = relativeTime(job.updated_at);
  const status = job.status === "complete" ? "complete" : "partial";
  const statusLabel = status === "complete" ? "READY" : "PARTIAL";
  const pageCount = Number.isInteger(job.page_count) ? `Pages: ${job.page_count}` : "Pages: -";
  const chunkCount = Number.isInteger(job.chunk_count) ? `Chunks: ${job.chunk_count}` : "Chunks: -";

  const thumbHtml = job.thumbnail_url
    ? `<img class="job-thumb" src="${escapeHtml(job.thumbnail_url)}" alt="${title}" loading="lazy" />`
    : `<div class="job-thumb-placeholder" aria-hidden="true"></div>`;

  return `
    <article class="job-card" role="listitem" aria-label="${title}" data-job-id="${escapeHtml(
      job.job_id || ""
    )}" tabindex="0">
      ${thumbHtml}
      <div class="job-body">
        <h3 class="job-title">${title}</h3>
        <div class="job-meta">${escapeHtml(timeLabel)}</div>
        <div class="job-meta">${escapeHtml(pageCount)} · ${escapeHtml(chunkCount)}</div>
        <span class="status-pill status-${status}">${statusLabel}</span>
      </div>
    </article>
  `;
}

function buildCreateCard() {
  return `
    <article class="create-card" role="listitem" aria-label="Create new project">
      <div class="create-plus">+</div>
      <p class="create-title">Create New</p>
      <p class="create-subtitle">From PDF or Slides</p>
    </article>
  `;
}

function renderJobs(jobs) {
  if (!Array.isArray(jobs) || jobs.length === 0) {
    jobsGrid.innerHTML = buildCreateCard();
    statusEl.textContent = "No jobs yet.";
    return;
  }

  const cards = jobs.map(buildJobCard).join("") + buildCreateCard();
  jobsGrid.innerHTML = cards;
  statusEl.textContent = `${jobs.length} job${jobs.length === 1 ? "" : "s"} shown`;
}

function applySearchFilter() {
  const q = searchInput.value.trim().toLowerCase();
  if (!q) {
    renderJobs(allJobs);
    return;
  }

  const filtered = allJobs.filter((job) => {
    const hay = `${job.title || ""} ${job.job_id || ""} ${job.input_pdf_name || ""}`.toLowerCase();
    return hay.includes(q);
  });

  renderJobs(filtered);
}

async function loadJobs() {
  statusEl.textContent = "Loading jobs...";
  try {
    const response = await fetch("/api/jobs");
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.detail || `Request failed: ${response.status}`);
    }

    allJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    renderJobs(allJobs);
  } catch (error) {
    statusEl.textContent = "Failed to load jobs.";
    jobsGrid.innerHTML = buildCreateCard();
    console.error(error);
  }
}

searchInput.addEventListener("input", applySearchFilter);

newProjectBtn.addEventListener("click", () => {
  window.location.href = "/new-project";
});

jobsGrid.addEventListener("click", (event) => {
  const createCard = event.target.closest(".create-card");
  if (createCard) {
    window.location.href = "/new-project";
    return;
  }

  const jobCard = event.target.closest(".job-card");
  if (jobCard && jobCard.dataset.jobId) {
    window.location.href = `/lecture/${encodeURIComponent(jobCard.dataset.jobId)}`;
  }
});

jobsGrid.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  const jobCard = event.target.closest(".job-card");
  if (!jobCard || !jobCard.dataset.jobId) {
    return;
  }
  event.preventDefault();
  window.location.href = `/lecture/${encodeURIComponent(jobCard.dataset.jobId)}`;
});

loadJobs();

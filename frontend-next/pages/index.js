import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";

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
    return `${Math.floor(deltaSec / 60)} min ago`;
  }
  if (deltaSec < 86400) {
    return `${Math.floor(deltaSec / 3600)} hr ago`;
  }
  if (deltaSec < 604800) {
    return `${Math.floor(deltaSec / 86400)} day ago`;
  }
  return new Date(then).toLocaleDateString();
}

function JobCard({ job, onOpen }) {
  const title = job.title || job.job_id;
  const timeLabel = relativeTime(job.updated_at);
  const status = job.status === "complete" ? "complete" : "partial";
  const statusLabel = status === "complete" ? "READY" : "PARTIAL";
  const pageCount = Number.isInteger(job.page_count) ? `Pages: ${job.page_count}` : "Pages: -";
  const chunkCount = Number.isInteger(job.chunk_count) ? `Chunks: ${job.chunk_count}` : "Chunks: -";

  return (
    <article
      className="job-card"
      role="listitem"
      aria-label={title}
      data-job-id={job.job_id || ""}
      tabIndex={0}
      onClick={() => onOpen(job.job_id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(job.job_id);
        }
      }}
    >
      {job.thumbnail_url ? (
        <img className="job-thumb" src={job.thumbnail_url} alt={title} loading="lazy" />
      ) : (
        <div className="job-thumb-placeholder" aria-hidden="true" />
      )}
      <div className="job-body">
        <h3 className="job-title">{title}</h3>
        <div className="job-meta">{timeLabel}</div>
        <div className="job-meta">
          {pageCount} · {chunkCount}
        </div>
        <span className={`status-pill status-${status}`}>{statusLabel}</span>
      </div>
    </article>
  );
}

function CreateCard({ onCreate }) {
  return (
    <article className="create-card" role="listitem" aria-label="Create new project" onClick={onCreate}>
      <div className="create-plus">+</div>
      <p className="create-title">Create New</p>
      <p className="create-subtitle">From PDF or Slides</p>
    </article>
  );
}

export default function DashboardPage({ theme = "light", setTheme }) {
  const router = useRouter();
  const [jobs, setJobs] = useState([]);
  const [statusText, setStatusText] = useState("Loading jobs...");
  const [searchQuery, setSearchQuery] = useState("");
  const isDark = theme === "dark";

  useEffect(() => {
    let cancelled = false;
    async function loadJobs() {
      setStatusText("Loading jobs...");
      try {
        const response = await fetch("/api/jobs");
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload?.detail || `Request failed: ${response.status}`);
        }
        const nextJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
        if (cancelled) {
          return;
        }
        setJobs(nextJobs);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setJobs([]);
        setStatusText("Failed to load jobs.");
      }
    }
    void loadJobs();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredJobs = jobs.filter((job) => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) {
      return true;
    }
    const hay = `${job.title || ""} ${job.job_id || ""} ${job.input_pdf_name || ""}`.toLowerCase();
    return hay.includes(q);
  });

  useEffect(() => {
    if (!jobs.length) {
      if (statusText !== "Failed to load jobs.") {
        setStatusText("No jobs yet.");
      }
      return;
    }
    setStatusText(`${filteredJobs.length} job${filteredJobs.length === 1 ? "" : "s"} shown`);
  }, [jobs, filteredJobs.length, statusText]);

  const openJob = (jobId) => {
    if (!jobId) {
      return;
    }
    void router.push(`/lecture/${encodeURIComponent(jobId)}`);
  };

  const toggleTheme = () => {
    if (typeof setTheme === "function") {
      setTheme(isDark ? "light" : "dark");
      return;
    }
    if (typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent("nn:theme:set", {
          detail: { theme: isDark ? "light" : "dark" },
        })
      );
    }
  };

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>NeuroNote Dashboard</title>
      </Head>

      <div className="app-shell">
        <div className="ambient-orb ambient-orb-a" aria-hidden="true" />
        <div className="ambient-orb ambient-orb-b" aria-hidden="true" />

        <aside className="sidebar">
          <div className="brand-row">
            <div className="brand-logo">NN</div>
            <div className="brand-text">NeuroNote</div>
          </div>

          <div className="sidebar-group">
            <div className="group-title">My Projects</div>
            <button className="nav-item nav-item-active" type="button">
              Dashboard
            </button>
            <button className="nav-item" type="button">
              Recent
            </button>
            <button className="nav-item" type="button">
              Favorites
            </button>
            <button className="nav-item" type="button">
              Archived
            </button>
          </div>

          <div className="sidebar-group sidebar-group-bottom">
            <button className="nav-item" type="button">
              Settings
            </button>
            <button className="nav-item" type="button">
              Help &amp; Tutorials
            </button>
          </div>

          <div className="profile-card">
            <div className="profile-avatar">J</div>
            <div>
              <div className="profile-name">Local User</div>
              <div className="profile-plan">Free Plan</div>
            </div>
          </div>
        </aside>

        <main className="main-panel">
          <section className="hero-card" aria-label="Dashboard overview">
            <header className="topbar">
              <div className="topbar-copy">
                <p className="eyebrow">Workspace</p>
                <h1>Dashboard</h1>
                <p className="subhead">overview</p>
              </div>

              <div className="topbar-actions">
                <input
                  className="search-input"
                  type="text"
                  placeholder="Search projects..."
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                />
                <button
                  className="theme-toggle-btn"
                  type="button"
                  onClick={toggleTheme}
                  aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                >
                  {isDark ? "Light" : "Dark"}
                </button>
                <button className="new-project-btn" type="button" onClick={() => void router.push("/new-project")}>
                  + New Project
                </button>
              </div>
            </header>

          </section>

          <section className="content-section">
            <div className="section-header">
              <div>
                <p className="eyebrow">Library</p>
                <h2>Recent Jobs</h2>
              </div>
              <div className="section-chip" aria-hidden="true">
                Local cache
              </div>
            </div>

            <p className="status-text">{statusText}</p>

            <div className="jobs-grid" aria-live="polite">
              {filteredJobs.map((job, index) => (
                <JobCard key={job.job_id || `job-${index}`} job={job} onOpen={openJob} />
              ))}
              <CreateCard onCreate={() => void router.push("/new-project")} />
            </div>
          </section>
        </main>
      </div>
    </>
  );
}

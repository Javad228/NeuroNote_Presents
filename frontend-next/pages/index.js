import Head from "next/head";
import Script from "next/script";

export default function DashboardPage() {
  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>NeuroNote Dashboard</title>
        <link rel="stylesheet" href="/static/style.css" />
      </Head>

      <div className="app-shell">
        <div className="ambient-orb ambient-orb-a" aria-hidden="true" />
        <div className="ambient-orb ambient-orb-b" aria-hidden="true" />

        <aside className="sidebar">
          <div className="window-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>

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
                  id="searchInput"
                  className="search-input"
                  type="text"
                  placeholder="Search projects..."
                />
                <button id="newProjectBtn" className="new-project-btn" type="button">
                  + New Project
                </button>
              </div>
            </header>

            <div className="hero-metrics" aria-hidden="true">
              <div className="hero-metric">
                <span>Pipeline</span>
                <strong>Ready</strong>
              </div>
              <div className="hero-metric">
                <span>Rendering</span>
                <strong>Local</strong>
              </div>
              <div className="hero-metric">
                <span>Theme</span>
                <strong>Glass</strong>
              </div>
            </div>
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

            <p id="status" className="status-text">
              Loading jobs...
            </p>

            <div id="jobsGrid" className="jobs-grid" aria-live="polite" />
          </section>
        </main>
      </div>

      <Script src="/static/app.js" strategy="afterInteractive" />
    </>
  );
}

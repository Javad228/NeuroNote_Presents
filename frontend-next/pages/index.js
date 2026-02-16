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
          <header className="topbar">
            <div>
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

          <section className="content-section">
            <div className="section-header">
              <h2>Recent Jobs</h2>
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

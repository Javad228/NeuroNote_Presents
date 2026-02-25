import Head from "next/head";
import Script from "next/script";

export default function NewProjectPage() {
  const backendOrigin = (process.env.NEXT_PUBLIC_BACKEND_ORIGIN || "").replace(/\/+$/, "");

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>NeuroNote - New Project</title>
        <link rel="stylesheet" href="/static/new-project.css" />
      </Head>

      <div className="np-shell">
        <div className="np-ambient np-ambient-a" aria-hidden="true" />
        <div className="np-ambient np-ambient-b" aria-hidden="true" />

        <header className="np-topbar">
          <div className="np-window-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>

          <div className="np-brand">
            <div className="np-brand-logo">NN</div>
            <div className="np-brand-text">NeuroNote</div>
            <span className="np-beta">Studio</span>
          </div>

          <nav className="np-nav" aria-label="Primary">
            <a href="/" className="np-nav-link">
              My Projects
            </a>
            <a href="/new-project" className="np-nav-link np-nav-link-active">
              New Project
            </a>
            <button id="accountBtn" className="np-nav-link np-nav-btn" type="button">
              Account
            </button>
          </nav>

          <button id="avatarBtn" className="np-avatar" type="button" aria-label="Profile">
            J
          </button>
        </header>

        <main className="np-main">
          <section className="np-left np-panel">
            <div className="np-section-title-wrap">
              <span className="np-step-pill">Step 1</span>
              <h1 className="np-section-title">Upload slides</h1>
              <p className="np-section-subtitle">Upload your presentation deck to start building the lecture.</p>
              <div className="np-mini-chips" aria-hidden="true">
                <span className="np-mini-chip">PDF pipeline</span>
                <span className="np-mini-chip">Local processing</span>
              </div>
            </div>

            <div id="dropzone" className="np-dropzone" role="button" tabIndex={0} aria-label="Upload PDF file">
              <div className="np-dropzone-gloss" aria-hidden="true" />
              <div className="np-upload-icon">&#9729;</div>
              <p className="np-drop-main">Drag &amp; drop your file here</p>
              <p className="np-drop-sub">
                or <span>browse from your computer</span>
              </p>
              <p className="np-drop-hint">Supports .pdf (Max 50MB)</p>
              <input id="fileInput" type="file" accept="application/pdf,.pdf" hidden />
            </div>

            <p id="fileStatus" className="np-file-status">
              No file selected.
            </p>

            <section className="np-options-card" aria-label="Processing options">
              <h2>Processing Options</h2>

              <label className="np-checkbox-row">
                <input id="optDetect" type="checkbox" />
                <span>Detect diagrams automatically</span>
              </label>

              <label className="np-checkbox-row">
                <input id="optEnhance" type="checkbox" defaultChecked />
                <span>Enhance low-res images</span>
              </label>

              <label className="np-checkbox-row">
                <input id="optCaptions" type="checkbox" />
                <span>Generate closed captions</span>
              </label>
            </section>
          </section>

          <section className="np-right np-panel">
            <div className="np-section-title-wrap">
              <span className="np-step-pill">Step 2</span>
              <h1 className="np-section-title">Select Voice</h1>
              <p className="np-section-subtitle">Choose the persona and pacing for your AI lecturer.</p>
              <div className="np-mini-chips" aria-hidden="true">
                <span className="np-mini-chip">3 presets</span>
                <span className="np-mini-chip">Preview snippets</span>
              </div>
            </div>

            <div id="voiceList" className="np-voice-list" role="radiogroup" aria-label="Voice selection">
              <button className="np-voice-card" data-voice="sarah" type="button" role="radio" aria-checked="false">
                <div className="np-voice-head">
                  <div className="np-voice-avatar">S</div>
                  <div>
                    <p className="np-voice-name">Professor Sarah</p>
                    <p className="np-voice-sub">Academic &amp; Clear</p>
                  </div>
                </div>
                <div className="np-audio-bar">
                  <span style={{ width: "18%" }} />
                  <small>0:14</small>
                </div>
              </button>

              <button
                className="np-voice-card np-voice-card-selected"
                data-voice="david"
                type="button"
                role="radio"
                aria-checked="true"
              >
                <div className="np-voice-head">
                  <div className="np-voice-avatar">D</div>
                  <div>
                    <p className="np-voice-name">Tech Lead David</p>
                    <p className="np-voice-sub">Modern &amp; Energetic</p>
                  </div>
                  <span className="np-check">&#10003;</span>
                </div>
                <div className="np-audio-bar">
                  <span style={{ width: "35%" }} />
                  <small>0:08</small>
                </div>
              </button>

              <button className="np-voice-card" data-voice="maya" type="button" role="radio" aria-checked="false">
                <div className="np-voice-head">
                  <div className="np-voice-avatar">M</div>
                  <div>
                    <p className="np-voice-name">Storyteller Maya</p>
                    <p className="np-voice-sub">Warm &amp; Narrative</p>
                  </div>
                </div>
                <div className="np-audio-bar">
                  <span style={{ width: "12%" }} />
                  <small>0:12</small>
                </div>
              </button>
            </div>

            <button id="generateBtn" className="np-generate" type="button" disabled>
              Generate Lecture
            </button>
            <p className="np-estimate">Estimated time: ~2 mins</p>
            <p id="submitStatus" className="np-submit-status" aria-live="polite" />
          </section>
        </main>

        <footer className="np-footer">
          <span>&copy; 2026 NeuroNote</span>
          <div className="np-footer-links">
            <button type="button" id="helpBtn">
              Help
            </button>
            <button type="button" id="termsBtn">
              Terms
            </button>
            <button type="button" id="privacyBtn">
              Privacy
            </button>
          </div>
        </footer>
      </div>

      <Script id="np-backend-origin" strategy="beforeInteractive">
        {`window.__BACKEND_ORIGIN__ = ${JSON.stringify(backendOrigin)};`}
      </Script>
      <Script src="/static/new-project.js" strategy="afterInteractive" />
    </>
  );
}

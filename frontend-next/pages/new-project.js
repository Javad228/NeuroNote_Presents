import Head from "next/head";
import Script from "next/script";

export default function NewProjectPage() {
  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>LecturAI - New Project</title>
        <link rel="stylesheet" href="/static/new-project.css" />
      </Head>

      <div className="np-shell">
        <header className="np-topbar">
          <div className="np-brand">
            <div className="np-brand-logo">NN</div>
            <div className="np-brand-text">LecturAI</div>
            <span className="np-beta">Beta</span>
          </div>

          <nav className="np-nav" aria-label="Primary">
            <a href="/" className="np-nav-link">
              My Lectures
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
          <section className="np-left">
            <div className="np-section-title-wrap">
              <h1 className="np-section-title">1. Upload slides</h1>
              <p className="np-section-subtitle">Upload your presentation deck to get started.</p>
            </div>

            <div id="dropzone" className="np-dropzone" role="button" tabIndex={0} aria-label="Upload PDF file">
              <div className="np-upload-icon">&#9729;</div>
              <p className="np-drop-main">Drag &amp; drop your file here</p>
              <p className="np-drop-sub">
                or <span>browse from your computer</span>
              </p>
              <p className="np-drop-hint">Supports .pdf, .pptx (Max 50MB)</p>
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

          <section className="np-right">
            <div className="np-section-title-wrap">
              <h1 className="np-section-title">2. Select Voice</h1>
              <p className="np-section-subtitle">Choose the persona for your AI lecturer.</p>
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
          <span>&copy; 2023 LecturAI Inc.</span>
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

      <Script src="/static/new-project.js" strategy="afterInteractive" />
    </>
  );
}

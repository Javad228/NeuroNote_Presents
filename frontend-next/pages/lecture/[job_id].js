import Head from "next/head";
import Script from "next/script";

export default function LecturePage() {
  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>LecturAI - Lecture View</title>
        <link rel="stylesheet" href="/static/lecture.css?v=19" />
      </Head>

      <div className="lecture-shell">
        <header className="lecture-topbar">
          <div className="lecture-brand">
            <div className="lecture-brand-logo">NN</div>
            <div className="lecture-brand-text">LecturAI</div>
          </div>

          <div className="lecture-head-meta">
            <h1 id="lectureTitle" className="lecture-title">
              Loading lecture...
            </h1>
            <p id="lectureSubhead" className="lecture-subhead">
              Module 1
            </p>
          </div>

          <div className="lecture-head-actions">
            <a className="back-btn" href="/">
              My Lectures
            </a>
            <div className="lecture-tabs" role="tablist" aria-label="Panel tabs">
              <button className="lecture-tab lecture-tab-active" id="tabScript" type="button" data-tab="script">
                Script
              </button>
              <button className="lecture-tab" id="tabNotes" type="button" data-tab="notes">
                Notes
              </button>
              <button className="lecture-tab" id="tabResources" type="button" data-tab="resources">
                Resources
              </button>
            </div>
            <a id="downloadPdfBtn" className="download-btn" href="#" target="_blank" rel="noopener">
              Download PDF
            </a>
            <button className="menu-btn" type="button" aria-label="More options">
              &#8942;
            </button>
          </div>
        </header>

        <main className="lecture-main">
          <section className="slide-pane">
            <div id="slideFrame" className="slide-frame">
              <div id="imageContainer" className="slide-canvas">
                <img id="mainImage" className="slide-image" alt="Slide preview" />
                <canvas id="textTransitionCanvas" className="text-transition-canvas" aria-hidden="true" />
                <div id="highlightOverlay" className="highlight-overlay" aria-hidden="true" />
              </div>
              <p id="slideMessage" className="slide-message">
                Loading slide...
              </p>
            </div>
          </section>

          <aside className="script-pane">
            <div className="script-pane-head">
              <h2 id="scriptTitle">Lecture Script</h2>
              <button id="scriptSearchBtn" type="button">
                Search
              </button>
            </div>
            <div id="scriptPanel" className="script-panel" />
          </aside>
        </main>

        <section className="timeline-strip">
          <div className="timeline-top">
            <p id="slideCounter" className="slide-counter">
              Slide 1
            </p>
            <p id="stepCounter" className="step-counter">
              00:00 - 00:00
            </p>
          </div>
          <div id="dotsTrack" className="dots-track" />
        </section>

        <footer className="lecture-controls">
          <div className="track-info">
            <p id="trackTitle" className="track-title">
              Lecture
            </p>
            <p id="trackTime" className="track-time">
              00:00 / 00:00
            </p>
          </div>
          <div className="transport">
            <button id="prevSlideBtn" type="button" aria-label="Previous slide">
              &#9664;
            </button>
            <button id="playBtn" className="play-btn" type="button" aria-label="Play script">
              &#9658;
            </button>
            <button id="nextSlideBtn" type="button" aria-label="Next slide">
              &#9654;
            </button>
          </div>
          <div className="right-controls">
            <span id="rateLabel">1.0x</span>
            <input
              id="speedRange"
              type="range"
              min="50"
              max="500"
              step="5"
              defaultValue="100"
              aria-label="Playback speed"
            />
          </div>
        </footer>
      </div>

      <Script src="/static/lecture.js?v=20" strategy="afterInteractive" />
    </>
  );
}

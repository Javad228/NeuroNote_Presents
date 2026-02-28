import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useRef, useState } from "react";

const MAX_FILE_BYTES = 50 * 1024 * 1024;
const DEFAULT_VOICE = "david";
const VOICES = [
  { id: "sarah", avatar: "S", name: "Professor Sarah", subtitle: "Academic & Clear", width: "18%", length: "0:14" },
  { id: "david", avatar: "D", name: "Tech Lead David", subtitle: "Modern & Energetic", width: "35%", length: "0:08" },
  { id: "maya", avatar: "M", name: "Storyteller Maya", subtitle: "Warm & Narrative", width: "12%", length: "0:12" },
];

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function resolveBackendOrigin() {
  const configured = trimTrailingSlash(process.env.NEXT_PUBLIC_BACKEND_ORIGIN || "");
  if (configured) {
    return configured;
  }
  if (typeof window === "undefined") {
    return "";
  }
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") {
    return `${window.location.protocol}//${host}:8100`;
  }
  return "";
}

function formatFileSize(bytes) {
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

function validateFile(file) {
  if (!file) {
    return { ok: false, message: "Choose a PDF file to continue." };
  }
  const name = (file.name || "").toLowerCase();
  const isPdf = file.type === "application/pdf" || name.endsWith(".pdf");
  if (!isPdf) {
    return { ok: false, message: "Only PDF upload is supported right now." };
  }
  if (file.size > MAX_FILE_BYTES) {
    return { ok: false, message: "File is larger than 50MB." };
  }
  return { ok: true, message: `Selected: ${file.name} (${formatFileSize(file.size)})` };
}

export default function NewProjectPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileStatus, setFileStatus] = useState("No file selected.");
  const [submitStatus, setSubmitStatus] = useState("");
  const [submitError, setSubmitError] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [selectedVoice, setSelectedVoice] = useState(DEFAULT_VOICE);

  const fileValidation = validateFile(selectedFile);
  const canSubmit = fileValidation.ok && !isSubmitting;

  const applyFile = (file, source = "picker") => {
    const validation = validateFile(file);
    if (!validation.ok) {
      setSelectedFile(null);
      setFileStatus(validation.message);
      setSubmitStatus("");
      setSubmitError(true);
      return;
    }

    setSelectedFile(file);
    setFileStatus(validation.message);
    if (source === "drop") {
      setSubmitStatus("File added from drop area.");
    } else {
      setSubmitStatus("");
    }
    setSubmitError(false);
  };

  const submitPdf = async () => {
    const validation = validateFile(selectedFile);
    if (!validation.ok) {
      setFileStatus(validation.message);
      setSubmitError(true);
      return;
    }

    const backendOrigin = resolveBackendOrigin();
    const processUrl = `${backendOrigin}/api/process-pdf?method=pelt&use_cache=true&skip_generation=false`;

    setIsSubmitting(true);
    setSubmitError(false);
    setSubmitStatus("Submitting PDF to pipeline...");

    const formData = new FormData();
    formData.append("pdf", selectedFile);

    try {
      const response = await fetch(processUrl, {
        method: "POST",
        body: formData,
      });
      const rawBody = await response.text();
      let payload = null;
      try {
        payload = rawBody ? JSON.parse(rawBody) : null;
      } catch {
        payload = null;
      }

      if (!response.ok) {
        const message = payload?.detail || (rawBody || "").trim() || `Request failed with status ${response.status}.`;
        throw new Error(message);
      }

      setSubmitStatus("Lecture generated successfully. Redirecting to dashboard...");
      setTimeout(() => {
        void router.push("/");
      }, 700);
    } catch (error) {
      setSubmitStatus(error?.message || "Failed to generate lecture.");
      setSubmitError(true);
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>NeuroNote - New Project</title>
      </Head>

      <div className="np-shell">
        <div className="np-ambient np-ambient-a" aria-hidden="true" />
        <div className="np-ambient np-ambient-b" aria-hidden="true" />

        <header className="np-topbar">
          <div className="np-brand">
            <div className="np-brand-logo">NN</div>
            <div className="np-brand-text">NeuroNote</div>
            <span className="np-beta">Studio</span>
          </div>

          <nav className="np-nav" aria-label="Primary">
            <Link href="/" className="np-nav-link">
              My Projects
            </Link>
            <Link href="/new-project" className="np-nav-link np-nav-link-active">
              New Project
            </Link>
            <button
              className="np-nav-link np-nav-btn"
              type="button"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Account is not wired in this version.");
              }}
            >
              Account
            </button>
          </nav>

          <button
            className="np-avatar"
            type="button"
            aria-label="Profile"
            onClick={() => {
              setSubmitError(false);
              setSubmitStatus("Profile is not wired in this version.");
            }}
          >
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

            <div
              className={`np-dropzone${dragActive ? " np-dropzone-drag" : ""}`}
              role="button"
              tabIndex={0}
              aria-label="Upload PDF file"
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={(event) => {
                event.preventDefault();
                setDragActive(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragActive(false);
                const files = event.dataTransfer?.files;
                if (!files || files.length === 0) {
                  setSubmitError(true);
                  setFileStatus("No file was dropped.");
                  return;
                }
                if (files.length > 1) {
                  setSubmitError(false);
                  setSubmitStatus("Multiple files detected; using the first file only.");
                }
                applyFile(files[0], "drop");
              }}
            >
              <div className="np-dropzone-gloss" aria-hidden="true" />
              <div className="np-upload-icon">&#9729;</div>
              <p className="np-drop-main">Drag &amp; drop your file here</p>
              <p className="np-drop-sub">
                or <span>browse from your computer</span>
              </p>
              <p className="np-drop-hint">Supports .pdf (Max 50MB)</p>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                hidden
                onChange={(event) => {
                  const file = event.target.files && event.target.files[0];
                  applyFile(file, "picker");
                }}
              />
            </div>

            <p className={`np-file-status${submitError && !fileValidation.ok ? " np-file-status-error" : ""}`}>
              {fileStatus}
            </p>

            <section className="np-options-card" aria-label="Processing options">
              <h2>Processing Options</h2>

              <label className="np-checkbox-row">
                <input type="checkbox" />
                <span>Detect diagrams automatically</span>
              </label>

              <label className="np-checkbox-row">
                <input type="checkbox" defaultChecked />
                <span>Enhance low-res images</span>
              </label>

              <label className="np-checkbox-row">
                <input type="checkbox" />
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

            <div className="np-voice-list" role="radiogroup" aria-label="Voice selection">
              {VOICES.map((voice) => {
                const selected = voice.id === selectedVoice;
                return (
                  <button
                    key={voice.id}
                    className={`np-voice-card${selected ? " np-voice-card-selected" : ""}`}
                    data-voice={voice.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setSelectedVoice(voice.id)}
                  >
                    <div className="np-voice-head">
                      <div className="np-voice-avatar">{voice.avatar}</div>
                      <div>
                        <p className="np-voice-name">{voice.name}</p>
                        <p className="np-voice-sub">{voice.subtitle}</p>
                      </div>
                      {selected ? <span className="np-check">&#10003;</span> : null}
                    </div>
                    <div className="np-audio-bar">
                      <span style={{ width: voice.width }} />
                      <small>{voice.length}</small>
                    </div>
                  </button>
                );
              })}
            </div>

            <button className="np-generate" type="button" disabled={!canSubmit} onClick={() => void submitPdf()}>
              {isSubmitting ? "Generating..." : "Generate Lecture"}
            </button>
            <p className="np-estimate">Estimated time: ~2 mins</p>
            <p className={`np-submit-status${submitError ? " np-submit-status-error" : ""}`} aria-live="polite">
              {submitStatus}
            </p>
          </section>
        </main>

        <footer className="np-footer">
          <span>&copy; 2026 NeuroNote</span>
          <div className="np-footer-links">
            <button
              type="button"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Help is not wired in this version.");
              }}
            >
              Help
            </button>
            <button
              type="button"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Terms is not wired in this version.");
              }}
            >
              Terms
            </button>
            <button
              type="button"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Privacy is not wired in this version.");
              }}
            >
              Privacy
            </button>
          </div>
        </footer>
      </div>
    </>
  );
}

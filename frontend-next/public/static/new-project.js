const MAX_FILE_BYTES = 50 * 1024 * 1024;

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function resolveBackendOrigin() {
  const configured = trimTrailingSlash(window.__BACKEND_ORIGIN__);
  if (configured) {
    return configured;
  }

  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") {
    return `${window.location.protocol}//${host}:8100`;
  }

  return "";
}

const BACKEND_ORIGIN = resolveBackendOrigin();
const PROCESS_URL = `${BACKEND_ORIGIN}/api/process-pdf?method=pelt&use_cache=true&skip_generation=false`;

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const fileStatus = document.getElementById("fileStatus");
const submitStatus = document.getElementById("submitStatus");
const generateBtn = document.getElementById("generateBtn");
const voiceList = document.getElementById("voiceList");
const accountBtn = document.getElementById("accountBtn");
const avatarBtn = document.getElementById("avatarBtn");
const helpBtn = document.getElementById("helpBtn");
const termsBtn = document.getElementById("termsBtn");
const privacyBtn = document.getElementById("privacyBtn");

let selectedFile = null;

function formatFileSize(bytes) {
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

function setFileStatus(message, isError = false) {
  fileStatus.textContent = message;
  fileStatus.classList.toggle("np-file-status-error", isError);
}

function setSubmitStatus(message, isError = false) {
  submitStatus.textContent = message;
  submitStatus.classList.toggle("np-submit-status-error", isError);
}

function isPdfFile(file) {
  const name = (file.name || "").toLowerCase();
  return file.type === "application/pdf" || name.endsWith(".pdf");
}

function validateFile(file) {
  if (!file) {
    return { ok: false, message: "Choose a PDF file to continue." };
  }
  if (!isPdfFile(file)) {
    return { ok: false, message: "Only PDF upload is supported right now." };
  }
  if (file.size > MAX_FILE_BYTES) {
    return { ok: false, message: "File is larger than 50MB." };
  }
  return { ok: true, message: `Selected: ${file.name} (${formatFileSize(file.size)})` };
}

function refreshGenerateState() {
  const valid = validateFile(selectedFile).ok;
  generateBtn.disabled = !valid;
}

function applyFile(file, source = "picker") {
  const validation = validateFile(file);
  if (!validation.ok) {
    selectedFile = null;
    setFileStatus(validation.message, true);
    setSubmitStatus("");
    refreshGenerateState();
    return;
  }

  selectedFile = file;
  setFileStatus(validation.message, false);
  if (source === "drop") {
    setSubmitStatus("File added from drop area.", false);
  } else {
    setSubmitStatus("");
  }
  refreshGenerateState();
}

function pickFile() {
  fileInput.click();
}

dropzone.addEventListener("click", pickFile);
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    pickFile();
  }
});

fileInput.addEventListener("change", (event) => {
  const file = event.target.files && event.target.files[0];
  applyFile(file, "picker");
});

["dragenter", "dragover"].forEach((name) => {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("np-dropzone-drag");
  });
});

["dragleave", "drop"].forEach((name) => {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("np-dropzone-drag");
  });
});

dropzone.addEventListener("drop", (event) => {
  const files = event.dataTransfer?.files;
  if (!files || files.length === 0) {
    setFileStatus("No file was dropped.", true);
    refreshGenerateState();
    return;
  }

  if (files.length > 1) {
    setSubmitStatus("Multiple files detected; using the first file only.", false);
  }

  applyFile(files[0], "drop");
});

voiceList.addEventListener("click", (event) => {
  const card = event.target.closest(".np-voice-card");
  if (!card) {
    return;
  }

  for (const node of voiceList.querySelectorAll(".np-voice-card")) {
    node.classList.remove("np-voice-card-selected");
    node.setAttribute("aria-checked", "false");
    const check = node.querySelector(".np-check");
    if (check) {
      check.remove();
    }
  }

  card.classList.add("np-voice-card-selected");
  card.setAttribute("aria-checked", "true");
  const head = card.querySelector(".np-voice-head");
  if (head && !card.querySelector(".np-check")) {
    const check = document.createElement("span");
    check.className = "np-check";
    check.textContent = "\u2713";
    head.appendChild(check);
  }
});

async function submitPdf() {
  const validation = validateFile(selectedFile);
  if (!validation.ok) {
    setFileStatus(validation.message, true);
    refreshGenerateState();
    return;
  }

  generateBtn.disabled = true;
  generateBtn.textContent = "Generating...";
  setSubmitStatus("Submitting PDF to pipeline...", false);

  const formData = new FormData();
  formData.append("pdf", selectedFile);

  try {
    const response = await fetch(PROCESS_URL, {
      method: "POST",
      body: formData,
    });

    const rawBody = await response.text();
    let payload = null;
    try {
      payload = rawBody ? JSON.parse(rawBody) : null;
    } catch (jsonError) {
      payload = null;
    }

    if (!response.ok) {
      const message =
        payload?.detail || (rawBody || "").trim() || `Request failed with status ${response.status}.`;
      throw new Error(message);
    }

    setSubmitStatus("Lecture generated successfully. Redirecting to dashboard...", false);
    setTimeout(() => {
      window.location.href = "/";
    }, 700);
  } catch (error) {
    setSubmitStatus(error.message || "Failed to generate lecture.", true);
    generateBtn.textContent = "Generate Lecture";
    refreshGenerateState();
  }
}

generateBtn.addEventListener("click", submitPdf);

function bindPlaceholder(control, label) {
  control.addEventListener("click", () => {
    setSubmitStatus(`${label} is not wired in this version.`, false);
  });
}

bindPlaceholder(accountBtn, "Account");
bindPlaceholder(avatarBtn, "Profile");
bindPlaceholder(helpBtn, "Help");
bindPlaceholder(termsBtn, "Terms");
bindPlaceholder(privacyBtn, "Privacy");

setFileStatus("No file selected.");
refreshGenerateState();

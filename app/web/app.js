import { buildRanges, timeLabel } from "./time.js";

const $ = (selector) => document.querySelector(selector);
const state = {
  format: "mp4",
  mode: "full",
  ranges: [{ start: "0:00", end: "" }],
  source: null,
  jobs: [],
  ready: false,
  submitting: false,
  validRanges: true,
};
const terminal = new Set(["completed", "failed", "cancelled"]);
let inspection;
let toastTimer;

function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4500);
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json();
  if (!response.ok) {
    const message = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join(" ")
      : data.detail;
    throw new Error(message || "This request could not be completed.");
  }
  return data;
}

function showError(message) {
  $("#form-error").textContent = message;
  $("#form-error").hidden = !message;
}

function updateSubmitButton() {
  $("#submit-button").disabled =
    !state.ready || state.submitting || !state.validRanges || !$("#url").value.trim()
    || state.jobs.some((job) => !terminal.has(job.status));
}

function requestBody() {
  return {
    url: $("#url").value.trim(),
    format: state.format,
    ranges:
      state.mode === "clips"
        ? buildRanges(state.ranges, state.source?.duration)
        : [],
    quality: Number($("#quality").value),
    bitrate: Number($("#bitrate").value),
  };
}

function renderRanges() {
  const fragment = document.createDocumentFragment();
  state.ranges.forEach((range, index) => {
    const row = $("#range-template").content.firstElementChild.cloneNode(true);
    row.dataset.rangeIndex = index;
    row.querySelector(".clip-number").textContent = String(index + 1).padStart(
      2,
      "0",
    );
    for (const field of ["start", "end"]) {
      const input = row.querySelector(`.range-${field}`);
      input.value = range[field];
      input.dataset.field = field;
      input.setAttribute(
        "aria-label",
        `Clip ${index + 1} ${field === "start" ? "from" : "to"}`,
      );
    }
    const remove = row.querySelector(".remove-range");
    remove.setAttribute("aria-label", `Remove clip ${index + 1}`);
    remove.disabled = state.ranges.length === 1;
    fragment.append(row);
  });
  $("#range-list").replaceChildren(fragment);
  $("#add-range").disabled = state.ranges.length >= 20;
  updateSummary();
}

function updateSummary() {
  const audio = state.format === "mp3";
  $("#full-mode").textContent = audio ? "Full audio" : "Full video";
  const custom = state.mode === "clips";
  const count = custom ? state.ranges.length : 1;
  const batch = count > 1;
  $("#quality-caption").textContent = audio
    ? `${$("#bitrate").value} kbps`
    : `${$("#quality").value}p`;
  $("#quality").hidden = audio;
  $("#quality-label").hidden = audio;
  $("#bitrate").hidden = !audio;
  $("#bitrate-label").hidden = !audio;
  $("#range-controls").hidden = !custom;
  $("#zip-hint").hidden = !batch;
  $("#submit-button span").textContent = state.submitting
    ? "Adding download…"
    : batch
      ? `Download ${count} clips`
      : custom
        ? "Download clip"
        : audio ? "Download audio" : "Download video";
  try {
    const { ranges } = requestBody();
    const durations = ranges.map((range) =>
      (range.end ?? state.source?.duration) == null
        ? null
        : (range.end ?? state.source.duration) - range.start,
    );
    const duration = custom
      ? durations.some((value) => value === null)
        ? null
        : durations.reduce((sum, value) => sum + value, 0)
      : state.source?.duration;
    const length = duration != null
      ? timeLabel(duration)
      : custom ? "To end" : audio ? "Full audio" : "Full video";
    $("#download-summary").textContent = [
      audio ? "MP3" : "MP4",
      audio ? `${$("#bitrate").value} kbps` : `${$("#quality").value}p`,
      batch ? `${count} clips · ${length} · ZIP` : length,
    ].join(" · ");
    showError("");
    state.validRanges = true;
  } catch (error) {
    $("#download-summary").textContent = "Check clip times";
    showError(error.message);
    state.validRanges = false;
  }
  updateSubmitButton();
}

document.querySelectorAll("[data-format]").forEach((button) => {
  button.addEventListener("click", () => {
    state.format = button.dataset.format;
    document.querySelectorAll("[data-format]").forEach((item) => {
      const selected = item === button;
      item.classList.toggle("selected", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    updateSummary();
  });
});

document.querySelectorAll("[data-range-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    state.mode = button.dataset.rangeMode;
    document.querySelectorAll("[data-range-mode]").forEach((item) => {
      const selected = item === button;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    updateSummary();
  });
});

$("#add-range").addEventListener("click", () => {
  if (state.ranges.length >= 20) return;
  state.ranges.push({ start: "0:00", end: "" });
  renderRanges();
  $("#range-list").lastElementChild.querySelector("input").focus();
});

$("#range-list").addEventListener("input", (event) => {
  const field = event.target.dataset.field;
  if (!field) return;
  state.ranges[Number(event.target.closest(".range-row").dataset.rangeIndex)][
    field
  ] = event.target.value;
  updateSummary();
});

$("#range-list").addEventListener("click", (event) => {
  const button = event.target.closest(".remove-range");
  if (!button || state.ranges.length < 2) return;
  const index = Number(button.closest(".range-row").dataset.rangeIndex);
  state.ranges.splice(index, 1);
  renderRanges();
  $("#range-list")
    .children[Math.min(index, state.ranges.length - 1)].querySelector("input")
    .focus();
});

for (const id of ["#quality", "#bitrate"])
  $(id).addEventListener("change", updateSummary);
$("#url").addEventListener("input", () => {
  inspection?.abort();
  state.source = null;
  $("#source-preview").hidden = true;
  updateSummary();
});

$("#inspect-button").addEventListener("click", async () => {
  if (!$("#url").reportValidity() || !$("#url").value.trim()) return;
  const url = $("#url").value.trim();
  inspection?.abort();
  const controller = new AbortController();
  inspection = controller;
  $("#inspect-button").disabled = true;
  $("#inspect-button").textContent = "Loading…";
  showError("");
  try {
    const source = await api("/inspect", {
      method: "POST",
      body: JSON.stringify({ url }),
      signal: controller.signal,
    });
    if (url !== $("#url").value.trim()) return;
    state.source = source;
    $("#source-platform").textContent = source.platform;
    $("#source-title").textContent = source.title;
    $("#source-duration").textContent = timeLabel(source.duration);
    $("#source-preview").hidden = false;
    const container = $(".preview-image");
    container.querySelector("img")?.remove();
    container.querySelector("svg").removeAttribute("hidden");
    if (source.thumbnail?.startsWith("https://")) {
      const image = document.createElement("img");
      image.alt = "Video thumbnail";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("load", () =>
        container.querySelector("svg").setAttribute("hidden", ""),
      );
      image.addEventListener("error", () => {
        image.remove();
        container.querySelector("svg").removeAttribute("hidden");
      });
      image.src = source.thumbnail;
      container.prepend(image);
    }
    updateSummary();
  } catch (error) {
    if (error.name !== "AbortError") showError(error.message);
  } finally {
    if (inspection === controller) {
      $("#inspect-button").disabled = false;
      $("#inspect-button").textContent = "Preview";
    }
  }
});

$("#download-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.submitting || !state.ready) return;
  try {
    const body = requestBody();
    state.submitting = true;
    updateSummary();
    const job = await api("/jobs", { method: "POST", body: JSON.stringify(body) });
    state.jobs = [job];
    renderJobs();
  } catch (error) {
    showError(error.message);
  } finally {
    state.submitting = false;
    const error = $("#form-error").hidden ? "" : $("#form-error").textContent;
    updateSummary();
    if (error) showError(error);
  }
});

function sizeLabel(bytes) {
  return bytes < 1024 ** 2
    ? `${Math.round(bytes / 1024)} KB`
    : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function renderJobs() {
  const list = $("#jobs");
  const focusedId =
    document.activeElement?.closest("[data-job-id]")?.dataset.jobId;
  const focusedAction = document.activeElement?.dataset.action;
  const fragment = document.createDocumentFragment();
  for (const job of state.jobs) {
    const element =
      $("#job-template").content.firstElementChild.cloneNode(true);
    element.dataset.jobId = job.id;
    element.querySelector("h3").textContent = job.title;
    element.querySelector("h3").title = job.title;
    const batch =
      job.filename?.endsWith(".zip") || job.request.ranges?.length > 1;
    const audio = job.request.format === "mp3";
    element.querySelector(".job-type").classList.toggle("audio", audio);
    element
      .querySelector(".job-type use")
      .setAttribute(
        "href",
        batch ? "#i-folder" : audio ? "#i-music" : "#i-film",
      );
    const badge = element.querySelector(".job-badge");
    badge.textContent =
      job.status === "completed"
        ? "Ready"
        : job.status === "failed"
          ? "Failed"
          : job.status === "cancelled"
            ? "Cancelled"
            : job.stage;
    badge.classList.add(job.status);
    const details = [job.request.format.toUpperCase()];
    if (batch) details.push(`${job.request.ranges.length} clips · ZIP`);
    if (job.platform) details.push(job.platform);
    if (job.duration != null) details.push(timeLabel(job.duration));
    if (job.size) details.push(sizeLabel(job.size));
    if (!terminal.has(job.status) && job.progress != null)
      details.push(`${Math.round(job.progress)}%`);
    element.querySelector(".job-meta").textContent = details.join(" · ");
    const progress = element.querySelector(".job-progress");
    progress.hidden = terminal.has(job.status);
    progress.classList.toggle("indeterminate", job.progress == null);
    progress.firstElementChild.style.width = `${job.progress ?? 0}%`;
    if (job.error) {
      element.querySelector(".job-error").hidden = false;
      element.querySelector(".job-error").textContent = job.error;
    }
    if (job.status === "completed") {
      const link = element.querySelector(".save-button");
      link.hidden = false;
      link.href = `/api/jobs/${job.id}/file`;
      link.download = job.filename;
      link.dataset.action = "save";
      link.querySelector("span").textContent = batch ? "Save ZIP" : "Save file";
      link.setAttribute("aria-label", `${batch ? "Save ZIP" : "Save file"}: ${job.title}`);
    } else {
      const action = terminal.has(job.status) ? "retry" : "cancel";
      const button = element.querySelector(`.${action}-button`);
      button.hidden = false;
      button.dataset.action = action;
    }
    fragment.append(element);
  }
  list.replaceChildren(fragment);
  if (focusedId && focusedAction)
    list
      .querySelector(
        `[data-job-id="${focusedId}"] [data-action="${focusedAction}"]`,
      )
      ?.focus({ preventScroll: true });
  $("#current-download").hidden = state.jobs.length === 0;
  updateSubmitButton();
}

$("#jobs").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const job = state.jobs.find(
    (item) => item.id === button.closest("[data-job-id]").dataset.jobId,
  );
  button.disabled = true;
  try {
    if (button.dataset.action === "cancel")
      await api(`/jobs/${job.id}/cancel`, { method: "POST" });
    else {
      const retried = await api("/jobs", { method: "POST", body: JSON.stringify(job.request) });
      state.jobs = [retried];
      renderJobs();
    }
    await refreshJobs();
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
});

async function refreshJobs() {
  if (!state.jobs.length || state.jobs.every((job) => terminal.has(job.status))) return;
  const jobs = await Promise.all(state.jobs.map((job) => api(`/jobs/${job.id}`)));
  if (JSON.stringify(jobs) !== JSON.stringify(state.jobs)) {
    state.jobs = jobs;
    renderJobs();
  }
}

async function refreshHealth() {
  try {
    const health = await api("/health");
    state.ready = health.ready;
    $("#setup-notice").hidden = health.ready;
  } catch {
    state.ready = false;
  }
  updateSubmitButton();
}

async function poll() {
  try {
    await refreshJobs();
  } catch {
    await refreshHealth();
  }
  setTimeout(poll, document.hidden ? 6000 : 1500);
}

renderRanges();
await refreshHealth();
poll();
setInterval(refreshHealth, 15000);

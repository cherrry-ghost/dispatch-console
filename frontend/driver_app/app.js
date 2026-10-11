// Driver PWA: jobs list + proof-of-delivery, with an offline queue.
// Deliveries made while offline are stored in localStorage and synced
// automatically when the connection returns.
const QUEUE_KEY = "driver_pending_deliveries";
const JOBS_KEY = "driver_last_jobs";

const $ = (id) => document.getElementById(id);

function setNet(online) {
  const el = $("net");
  el.textContent = online ? "online" : "offline";
  el.className = "badge " + (online ? "online" : "offline");
}
setNet(navigator.onLine);
addEventListener("online", () => { setNet(true); flushQueue(); });
addEventListener("offline", () => setNet(false));

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

function readQueue() {
  try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); }
  catch { return []; }
}
function writeQueue(q) { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); }

function showQueue() {
  const q = readQueue();
  $("queue").textContent = q.length
    ? `${q.length} delivery confirmation(s) waiting to sync (offline)`
    : "";
}

async function deliver(job, note, photoFile) {
  const form = new FormData();
  form.append("note", note || "");
  if (photoFile) form.append("photo", photoFile);
  const res = await fetch(
    `/driver/${job.driver_id}/jobs/${job.assignment_id}/deliver`,
    { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function queueDelivery(job, note, photoFile, done) {
  // photo is stored as a data URL; it syncs (with proof) once back online
  const entry = { driver_id: job.driver_id, assignment_id: job.assignment_id,
                  note };
  const finish = (dataUrl) => {
    if (dataUrl) entry.photo_data = dataUrl;
    writeQueue([...readQueue(), entry]);
    showQueue();
    done(true);
  };
  if (photoFile) {
    const reader = new FileReader();
    reader.onload = () => finish(reader.result);
    reader.onerror = () => finish(null);
    reader.readAsDataURL(photoFile);
  } else {
    finish(null);
  }
}

async function flushQueue() {
  const q = readQueue();
  const remaining = [];
  for (const entry of q) {
    try {
      const form = new FormData();
      form.append("note", entry.note || "");
      if (entry.photo_data) {
        const blob = await (await fetch(entry.photo_data)).blob();
        form.append("photo", blob, "proof.jpg");
      }
      const res = await fetch(
        `/driver/${entry.driver_id}/jobs/${entry.assignment_id}/deliver`,
        { method: "POST", body: form });
      if (!res.ok) remaining.push(entry);  // 409 etc: keep and retry later
    } catch {
      remaining.push(entry);               // still offline
    }
  }
  writeQueue(remaining);
  showQueue();
  if (q.length !== remaining.length) loadJobs();
}

async function loadJobs() {
  const driverId = Number($("driverId").value || 1);
  try {
    const res = await fetch(`/driver/${driverId}/jobs`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    localStorage.setItem(JOBS_KEY, JSON.stringify(data));
    render(data, driverId);
    flushQueue();
  } catch (err) {
    const cached = localStorage.getItem(JOBS_KEY);
    if (cached) {
      render(JSON.parse(cached), driverId);
      $("jobs").insertAdjacentHTML("afterbegin",
        `<p class="meta">Showing cached jobs (offline).</p>`);
    } else {
      $("jobs").innerHTML = `<p>Could not load jobs: ${err.message}</p>`;
    }
  }
}

function render(data, driverId) {
  const jobs = data.jobs || [];
  if (!jobs.length) {
    $("jobs").innerHTML =
      `<p>No active jobs for ${data.driver?.full_name || "this driver"}.</p>`;
    return;
  }
  $("jobs").innerHTML = jobs.map((j) => `
    <div class="job" data-id="${j.assignment_id}">
      <h3>Job #${j.assignment_id} — Shipment #${j.shipment_id}</h3>
      <p>${j.description || "(no description)"} · ${j.weight_kg} kg</p>
      <p>From <b>${j.origin_name}</b> to <b>${j.destination_name}</b></p>
      <p class="meta">Vehicle ${j.plate} (${j.vehicle_type})
         · window ${j.ready_minute ?? "–"}–${j.deadline_minute ?? "–"} min
         · status ${j.status}</p>
      <div class="deliver">
        <input type="text" placeholder="Delivery note (optional)"
               id="note-${j.assignment_id}" />
        <label>Photo proof (optional): <input type="file" accept="image/*"
               capture="environment" id="photo-${j.assignment_id}" /></label>
        <button data-deliver="${j.assignment_id}">Mark delivered</button>
      </div>
    </div>`).join("");

  $("jobs").querySelectorAll("[data-deliver]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.deliver;
      const jobEl = btn.closest(".job");
      const note = $(`note-${id}`).value;
      const photo = $(`photo-${id}`).files[0] || null;
      const job = { driver_id: driverId, assignment_id: Number(id) };
      btn.disabled = true;
      btn.textContent = "Saving…";
      const done = (queued) => {
        jobEl.querySelector(".deliver").innerHTML =
          `<span class="done">✔ Delivered${queued ? " — will sync when online" : ""}</span>`;
        if (!queued) loadJobs();
      };
      try {
        if (navigator.onLine) { await deliver(job, note, photo); done(false); }
        else { queueDelivery(job, note, photo, done); }
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "Retry";
        alert("Could not confirm delivery: " + err.message);
      }
    });
  });
}

$("load").addEventListener("click", loadJobs);
loadJobs();

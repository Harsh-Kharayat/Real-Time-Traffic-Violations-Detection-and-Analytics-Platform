
alert("script loaded");

let lastTotal = 0;
let pollTimer = null;

const rowStore = {};


// CLOCK

(function tickClock() {
  const p = (n) => String(n).padStart(2, "0");

  setInterval(() => {
    const d = new Date();

    document.getElementById("clock").textContent =
      p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }, 1000);
})();

// FILE PICKER

document.getElementById("videoInput").addEventListener("change", function () {
  document.getElementById("fileName").textContent = this.files.length
    ? this.files[0].name
    : "No file chosen";
});

// VIDEO UPLOAD

async function uploadVideo() {
  const inp = document.getElementById("videoInput");

  if (!inp.files.length) {
    alert("Please choose a video file first.");
    return;
  }

  const fd = new FormData();
  fd.append("video", inp.files[0]);

  setStatus("Uploading...", "running");
  showProgress(true);

  document.getElementById("startBtn").disabled = true;

  try {
    const r = await fetch("/upload", {
      method: "POST",
      body: fd,
    });

    const d = await r.json();

    if (!r.ok) {
      setStatus("Error: " + (d.error || "Upload failed"), "error");

      showProgress(false);
      return;
    }

    setStatus(
      "Processing started — dashboard updates automatically.",
      "running",
    );

    startPoll();
  } catch (e) {
    setStatus("Upload failed: " + e.message, "error");

    showProgress(false);

    document.getElementById("startBtn").disabled = false;
  }
}

function startPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
  }

  pollTimer = setInterval(async () => {
    try {
      const d = await (await fetch("/status")).json();

      if (d.running) {
        setStatus(
          "Processing video — violations being logged in real time...",
          "running",
        );
      } else if (d.error) {
        setStatus("Error: " + d.error, "error");

        showProgress(false);

        clearInterval(pollTimer);

        document.getElementById("startBtn").disabled = false;
      } else {
        setStatus("Processing complete!", "done");

        showProgress(false);

        clearInterval(pollTimer);

        document.getElementById("startBtn").disabled = false;

        loadAll();
      }
    } catch (e) {}
  }, 3000);
}

function setStatus(msg, cls) {
  const el = document.getElementById("statusLine");

  el.textContent = msg;

  el.className = "status-line " + (cls || "");
}

function showProgress(on) {
  document.getElementById("progressWrap").style.display = on ? "block" : "none";
}

// CLEAR DATA


async function clearData() {
  if (!confirm("Delete ALL violation records? This cannot be undone.")) return;

  await fetch("/violations", {
    method: "DELETE",
  });

  lastTotal = 0;

  loadAll();

  flash("All data cleared");
}


// STATS


async function loadStats() {
  try {
    const r = await fetch("/stats");

    if (!r.ok) return;

    const s = await r.json();

    document.getElementById("s-total").textContent = s.total ?? "–";

    document.getElementById("s-speed").textContent = s.overspeed ?? "–";

    document.getElementById("s-helmet").textContent = s.no_helmet ?? "–";

    document.getElementById("s-cars").textContent = s.cars ?? "–";

    document.getElementById("s-bikes").textContent = s.bikes ?? "–";

    document.getElementById("s-avg").textContent =
      s.avg_speed != null ? s.avg_speed + " km/h" : "–";

    if (lastTotal > 0 && s.total > lastTotal) {
      flash("New violation recorded (total: " + s.total + ")");
    }

    lastTotal = s.total ?? 0;
  } catch (e) {
    console.error("stats error", e);
  }
}

// VIOLATIONS TABLE

async function loadViolations() {
  const type = document.getElementById("f-type").value;

  const viol = document.getElementById("f-viol").value;

  let url = "/violations?limit=500";

  if (type) {
    url += "&type=" + encodeURIComponent(type);
  }

  if (viol) {
    url += "&violation=" + encodeURIComponent(viol);
  }

  try {
    const r = await fetch(url);

    if (!r.ok) {
      console.error("violations fetch", r.status);
      return;
    }

    const rows = await r.json();

    const tbody = document.getElementById("tbody");

    const emptyM = document.getElementById("emptyMsg");

    if (!rows.length) {
      tbody.innerHTML = "";

      emptyM.style.display = "block";

      return;
    }

    emptyM.style.display = "none";

    Object.keys(rowStore).forEach((k) => delete rowStore[k]);

    rows.forEach((row) => {
      rowStore[row.id] = row;
    });

    const limit = (row) => (row.type === "car" ? 60 : 50);

    tbody.innerHTML = rows
      .map((row) => {
        const fast = row.speed > limit(row);

        const spdCls = fast ? "speed-high" : "speed-ok";

        const icon = `<span class="type-icon ${row.type}">
${row.type === "car" ? "CAR" : "BIKE"}
</span>`;

        const imgFile = row.image_path
          ? row.image_path.split(/[\\/]/).pop()
          : "";

        const imgTag = imgFile
          ? `<img
class="img-thumb"
src="/image/${imgFile}"
alt="ev"
onclick="openModal(${row.id})"
onerror="this.style.display='none'"
>`
          : `<span style="color:var(--dim);font-size:.8rem">—</span>`;

        return `
<tr>
<td class="mono">${row.id}</td>

<td>
${icon}
</td>

<td class="mono">
#${row.vehicle_id}
</td>

<td class="${spdCls}">
${row.speed} km/h
</td>

<td>
${makeBadge(row.violation)}
</td>

<td class="mono"
style="font-size:.78rem;color:var(--muted)">
${row.timestamp}
</td>

<td>
${imgTag}
</td>

</tr>
`;
      })
      .join("");
  } catch (e) {
    console.error("loadViolations", e);
  }
}

function makeBadge(v) {
  if (!v) return "";

  const lo = v.toLowerCase();

  if (lo.includes("overspeed") && lo.includes("helmet")) {
    return `
<span class="badge both">
⚡ ${v}
</span>
`;
  }

  if (lo.includes("overspeed")) {
    return `
<span class="badge overspeed">
⬆ OverSpeed
</span>
`;
  }

  return `
<span class="badge no-helmet">
⛑ No Helmet
</span>
`;
}



// FILTERS
function applyFilter() {
  loadViolations();
}

function resetFilters() {
  document.getElementById("f-type").value = "";

  document.getElementById("f-viol").value = "";

  loadViolations();
}

function manualRefresh() {
  loadAll();
}

async function loadAll() {
  await loadStats();
  await loadViolations();
}

// MODAL


function openModal(rowId) {
  const row = rowStore[rowId];

  if (!row) return;

  const imgFile = row.image_path ? row.image_path.split(/[\\/]/).pop() : "";

  document.getElementById("modalImg").src = imgFile ? "/image/" + imgFile : "";

  document.getElementById("modalMeta").innerHTML = `
<div>
<span>Vehicle ID</span>
#${row.vehicle_id}
</div>

<div>
<span>Type</span>
${(row.type || "").toUpperCase()}
</div>

<div>
<span>Speed</span>
${row.speed} km/h
</div>

<div>
<span>Violation</span>
${row.violation}
</div>

<div>
<span>Timestamp</span>
${row.timestamp}
</div>
`;

  document.getElementById("modalBg").classList.add("open");
}

function closeModal() {
  document.getElementById("modalBg").classList.remove("open");

  document.getElementById("modalImg").src = "";
}

document.getElementById("modalBg").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) {
    closeModal();
  }
});

// ALERT BAR

function flash(msg) {
  const b = document.getElementById("alert-bar");

  b.textContent = msg;

  b.classList.add("show");

  setTimeout(() => {
    b.classList.remove("show");
  }, 3500);
}

// BOOT


loadAll();

setInterval(loadAll, 5000);

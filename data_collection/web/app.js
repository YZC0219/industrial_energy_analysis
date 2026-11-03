const $ = (id) => document.getElementById(id);

async function getJson(url, opts) {
  const res = await fetch(url, { cache: "no-store", ...opts });
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

function drawSeries(points) {
  const canvas = $("seriesChart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 900;
  const cssH = 260;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  ctx.strokeStyle = "rgba(236,231,223,0.08)";
  for (let i = 0; i < 5; i++) {
    const y = 24 + ((cssH - 48) * i) / 4;
    ctx.beginPath();
    ctx.moveTo(16, y);
    ctx.lineTo(cssW - 16, y);
    ctx.stroke();
  }
  if (!points.length) {
    ctx.fillStyle = "#9aa3b0";
    ctx.font = "14px IBM Plex Sans, sans-serif";
    ctx.fillText("点击右上角运行真实采集 + Spark", 24, cssH / 2);
    return;
  }

  const padX = 28, padY = 28;
  const w = cssW - padX * 2, h = cssH - padY * 2;
  const drawLine = (key, color) => {
    const vals = points.map((p) => p[key]).filter((v) => v != null);
    if (!vals.length) return;
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = Math.max(max - min, 1e-6);
    ctx.beginPath();
    let started = false;
    points.forEach((p, i) => {
      if (p[key] == null) return;
      const x = padX + (points.length === 1 ? w / 2 : (w * i) / (points.length - 1));
      const y = padY + h - ((p[key] - min) / span) * h;
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
    points.forEach((p, i) => {
      if (!p.peak || p[key] == null) return;
      const x = padX + (points.length === 1 ? w / 2 : (w * i) / (points.length - 1));
      const y = padY + h - ((p[key] - min) / span) * h;
      ctx.beginPath();
      ctx.arc(x, y, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = "#e8786a";
      ctx.fill();
    });
  };
  drawLine("temp", "#e8b86d");
  drawLine("pm25", "#3ecfbf");
}

function peakSum(obj) {
  if (!obj || typeof obj !== "object") return "—";
  return Object.values(obj).reduce((a, b) => a + (Number(b) || 0), 0);
}

async function refresh() {
  const [overview, samples, series, ods] = await Promise.all([
    getJson("/api/v1/overview"),
    getJson("/api/v1/samples?limit=30"),
    getJson("/api/v1/series?city_id=SH"),
    getJson("/api/v1/ods"),
  ]);

  const nature = overview.data_nature || {};
  $("natureBanner").textContent =
    `数据性质：${nature.nature_zh || "模式预报产品"} · 来源 ${nature.provider || "Open-Meteo"} · 文档 ${nature.docs || ""}`;

  $("batchId").textContent = overview.batch_id
    ? `${overview.batch_id} / ${overview.engine || "—"}`
    : "尚未运行";
  $("rows").textContent = overview.rows != null ? Number(overview.rows).toLocaleString("zh-CN") : "—";
  const rec = overview.reconcile || {};
  $("dupes").textContent = rec.duplicates_removed != null ? rec.duplicates_removed : "—";
  $("peaks").textContent = peakSum(rec.pollution_peaks_marked);
  $("policy").textContent = overview.cleaning_policy
    ? JSON.stringify(overview.cleaning_policy)
    : overview.architecture_note || "";
  $("note").textContent = overview.hdfs_dwd
    ? `HDFS: ${overview.hdfs_dwd}`
    : overview.quality_report || "";

  $("steps").innerHTML = (overview.steps || [])
    .map((s) => `<li><strong>${s.name}</strong><span>${s.detail || ""}</span></li>`)
    .join("") || "<li><strong>等待运行</strong><span>选择引擎后采集</span></li>";

  const recs = ods.manifest?.records || [];
  $("odsBody").innerHTML =
    recs
      .map(
        (r) => `<tr>
        <td>${r.city_name || r.city_id}</td>
        <td>${r.kind}</td>
        <td>${r.hourly_points}</td>
        <td>${r.data_nature || ""}</td>
      </tr>`
      )
      .join("") || "<tr><td colspan='4'>无 ODS</td></tr>";

  const cs = ods.manifest?.collect_log_summary;
  $("collectMeta").textContent = cs
    ? `采集UTC ${cs.collected_at_utc || ""} · HTTP重试 ${cs.http_retry_count ?? "—"} · 失败尝试 ${cs.http_failed_attempt_count ?? "—"}`
    : "";

  $("sampleBody").innerHTML =
    (samples.samples || [])
      .map(
        (s) => `<tr>
        <td>${s.city_name || s.city_id || ""}</td>
        <td>${s.ts_local || ""}</td>
        <td>${s.temperature_2m ?? ""}</td>
        <td>${s.pm2_5 ?? ""}</td>
        <td>${s.european_aqi ?? ""}</td>
        <td>${s.flag_peak_pm2_5 ?? ""}</td>
      </tr>`
      )
      .join("") || "<tr><td colspan='6'>无 DWD</td></tr>";

  drawSeries(series.points || []);
}

async function runPipeline() {
  const btn = $("btnRun");
  const st = $("runStatus");
  const engine = $("engine").value;
  btn.disabled = true;
  st.dataset.state = "live";
  st.textContent = `运行中（${engine}）…`;
  try {
    const result = await getJson(
      `/api/v1/run?past_days=3&forecast_days=1&engine=${encodeURIComponent(engine)}`,
      { method: "POST" }
    );
    if (!result.ok) throw new Error(result.error || "失败");
    st.textContent = result.fallback
      ? `完成（回退 ${result.fallback}）`
      : `完成 · ${engine}`;
    await refresh();
  } catch (err) {
    st.dataset.state = "err";
    st.textContent = String(err.message || err);
  } finally {
    btn.disabled = false;
  }
}

$("btnRun").addEventListener("click", runPipeline);
refresh().catch(console.error);
window.addEventListener("resize", () => refresh().catch(() => {}));

const state = {
  jobs: [],
  stats: { total: 0, reviewed: 0 },
  filter: "all",
  selectedId: null,
  selectedPlatform: null,
  selectedSampleName: "positioning-v1",
  detailRequest: 0,
  view: "calibration",
  calibration: null,
  calibratedResumeText: "",
  calibratedFilename: "",
  collectionRunning: false,
  calibrationRunning: false,
  preferenceSaving: false,
  learning: null,
  matching: null,
  matchFilter: "all",
  selectedMatchId: null,
  selectedMatchPlatform: null,
  matchDetailRequest: 0,
  boundary: null,
  freshness: null,
  freshnessFilter: "detail_due",
  jobUpdate: null,
  outreach: null,
  selectedOutreachId: null,
  selectedOutreachPlatform: null,
  outreachDetail: null,
  outreachRequest: 0,
  greetingDirty: false,
  resumeDirty: false,
  sendTask: null,
  sendBusy: false,
  sendRequest: 0,
  pendingAssetId: null,
  progressRequest: 0,
};

const labels = {
  buckets: { favorites: "收藏样本", core: "核心样本", stretch: "挑战样本", exclude: "排除样本" },
  tiers: { core: "当前可投", adjacent: "相邻可投", stretch: "有门槛", exclude: "不建议投" },
  tracks: {
    fde: "FDE",
    agent_engineering: "Agent",
    rag_engineering: "RAG",
    ai_application: "AI 应用",
    solution_delivery: "方案交付",
    ai_product_delivery: "AI 产品 / FDE",
    pure_java: "传统 Java",
    product: "产品",
    sales: "销售 / 商务",
    other: "其他",
  },
  risks: {
    third_party: "第三方招聘待确认",
    outsourcing_risk: "疑似外包 / 派遣",
    seniority_gap: "经验门槛偏高",
    salary_stretch: "薪资带宽偏高",
    graduate_only: "应届资格待确认",
    internship: "实习岗位",
    pure_java: "偏纯 Java",
    onsite_heavy: "长期 / 高频驻场",
    non_engineering_role: "非研发岗",
    product_role: "偏产品岗",
    overseas_assignment: "海外派驻",
    non_ai_role: "AI 相关度低",
  },
  recommendations: {
    priority: "优先投",
    challenge: "挑战投",
    not_fit: "当前不建议投",
  },
  interests: { want: "想投", maybe: "再看", reject: "放弃" },
  conflicts: { strong: "强冲突", soft: "软冲突", aligned: "判断一致" },
  information: {
    sufficient: "信息较完整",
    partial: "部分信息缺失",
    insufficient: "信息不足",
  },
  freshness: {
    active: "有效",
    aging: "超过周期",
    stale: "扫描未见",
    closed: "明确关闭",
    inaccessible: "暂不可访问",
    archived: "已归档",
    detail_due: "JD 超期",
    retry_due: "详情重试",
    scan_attention: "列表复扫",
    detail_missing: "待补 JD",
  },
  roleTracks: {
    active: "仍在招聘",
    dormant: "方向沉寂",
    sunset: "方向停止",
    archived: "已归档",
  },
};

const analysisFields = [
  ["direction", "directionValue", "directionEvidence"],
  ["technical_match", "technicalMatchValue", "technicalMatchEvidence"],
  ["business_value", "businessValue", "businessEvidence"],
  ["attainability", "attainabilityValue", "attainabilityEvidence"],
  ["salary_reachability", "salaryReachabilityValue", "salaryEvidence"],
  ["employment_risk", "employmentRiskValue", "employmentEvidence"],
];

const DEFAULT_SAMPLE_NAME = "positioning-v1";
const preferenceTrackOrder = [
  "fde",
  "agent_engineering",
  "rag_engineering",
  "ai_application",
  "solution_delivery",
  "ai_product_delivery",
  "pure_java",
  "product",
  "sales",
];

const elements = {
  list: document.querySelector("#jobList"),
  visibleCount: document.querySelector("#visibleCount"),
  progressText: document.querySelector("#progressText"),
  progressBar: document.querySelector("#progressBar"),
  detailEmpty: document.querySelector("#detailEmpty"),
  detailContent: document.querySelector("#detailContent"),
  decisionForm: document.querySelector("#decisionForm"),
  formError: document.querySelector("#formError"),
  saveButton: document.querySelector("#saveButton"),
  saveState: document.querySelector("#saveState"),
  correctionForm: document.querySelector("#correctionForm"),
  correctionError: document.querySelector("#correctionError"),
  correctionButton: document.querySelector("#correctionButton"),
  correctionState: document.querySelector("#correctionState"),
  learningIntent: document.querySelector("#learningIntent"),
  calibrationView: document.querySelector("#calibrationView"),
  calibrationError: document.querySelector("#calibrationError"),
  calibrationStatus: document.querySelector("#calibrationStatus"),
  resumeFile: document.querySelector("#resumeFile"),
  calibrateResumeButton: document.querySelector("#calibrateResumeButton"),
  collectResumeJobsButton: document.querySelector("#collectResumeJobsButton"),
  resumeFileAction: document.querySelector(".file-action"),
  resumeSkillList: document.querySelector("#resumeSkillList"),
  collectionQueries: document.querySelector("#collectionQueries"),
  preferenceSignals: document.querySelector("#preferenceSignals"),
  calibrationUnknowns: document.querySelector("#calibrationUnknowns"),
  preferenceConfirmation: document.querySelector("#preferenceConfirmation"),
  preferenceForm: document.querySelector("#preferenceForm"),
  preferenceTrackOptions: document.querySelector("#preferenceTrackOptions"),
  maximumExperience: document.querySelector("#maximumExperience"),
  rejectThirdParty: document.querySelector("#rejectThirdParty"),
  rejectDispatch: document.querySelector("#rejectDispatch"),
  rejectLongTermOverseas: document.querySelector("#rejectLongTermOverseas"),
  preferenceFormError: document.querySelector("#preferenceFormError"),
  preferenceSaveState: document.querySelector("#preferenceSaveState"),
  savePreferencesButton: document.querySelector("#savePreferencesButton"),
  preferenceVersion: document.querySelector("#preferenceVersion"),
  preferenceAlignment: document.querySelector("#preferenceAlignment"),
  reviewView: document.querySelector("#reviewView"),
  learningView: document.querySelector("#learningView"),
  learningPriorities: document.querySelector("#learningPriorities"),
  marketDemands: document.querySelector("#marketDemands"),
  noteSignals: document.querySelector("#noteSignals"),
  learningError: document.querySelector("#learningError"),
  matchingView: document.querySelector("#matchingView"),
  matchJobList: document.querySelector("#matchJobList"),
  matchVisibleCount: document.querySelector("#matchVisibleCount"),
  matchDetailEmpty: document.querySelector("#matchDetailEmpty"),
  matchDetailContent: document.querySelector("#matchDetailContent"),
  matchingError: document.querySelector("#matchingError"),
  refreshStaleMatchesButton: document.querySelector("#refreshStaleMatchesButton"),
  refreshStaleMatchesState: document.querySelector("#refreshStaleMatchesState"),
  boundaryView: document.querySelector("#boundaryView"),
  boundaryInsights: document.querySelector("#boundaryInsights"),
  boundaryGroups: document.querySelector("#boundaryGroups"),
  boundaryRuleGate: document.querySelector("#boundaryRuleGate"),
  boundaryError: document.querySelector("#boundaryError"),
  freshnessView: document.querySelector("#freshnessView"),
  freshnessJobList: document.querySelector("#freshnessJobList"),
  freshnessVisibleCount: document.querySelector("#freshnessVisibleCount"),
  refreshPlan: document.querySelector("#refreshPlan"),
  freshnessError: document.querySelector("#freshnessError"),
  startJobUpdate: document.querySelector("#startJobUpdate"),
  executeJobUpdate: document.querySelector("#executeJobUpdate"),
  stopJobUpdate: document.querySelector("#stopJobUpdate"),
  jobUpdateStatus: document.querySelector("#jobUpdateStatus"),
  outreachView: document.querySelector("#outreachView"),
  outreachJobList: document.querySelector("#outreachJobList"),
  outreachEmpty: document.querySelector("#outreachEmpty"),
  outreachContent: document.querySelector("#outreachContent"),
  generateOutreach: document.querySelector("#generateOutreach"),
  greetingEditor: document.querySelector("#greetingEditor"),
  resumeEditor: document.querySelector("#resumeEditor"),
  saveGreeting: document.querySelector("#saveGreeting"),
  saveResume: document.querySelector("#saveResume"),
  confirmGreeting: document.querySelector("#confirmGreeting"),
  confirmResume: document.querySelector("#confirmResume"),
  downloadOutreachDocx: document.querySelector("#downloadOutreachDocx"),
  outreachResumeImage: document.querySelector("#outreachResumeImage"),
  outreachImageEmpty: document.querySelector("#outreachImageEmpty"),
  outreachResumeSource: document.querySelector("#outreachResumeSource"),
  outreachAssetSection: document.querySelector("#outreachAssetSection"),
  outreachAssetOptions: document.querySelector("#outreachAssetOptions"),
  outreachAssetCurrent: document.querySelector("#outreachAssetCurrent"),
  outreachError: document.querySelector("#outreachError"),
  outreachJdText: document.querySelector("#outreachJdText"),
  outreachStrengths: document.querySelector("#outreachStrengths"),
  outreachGaps: document.querySelector("#outreachGaps"),
  outreachChanges: document.querySelector("#outreachChanges"),
  outreachScope: document.querySelector("#outreachScope"),
  outreachMobileTabs: document.querySelector("#outreachMobileTabs"),
  outreachCollapseToolbar: document.querySelector(".outreach-collapse-toolbar"),
  authorizeSend: document.querySelector("#authorizeSend"),
  executeSend: document.querySelector("#executeSend"),
  outreachSendBadge: document.querySelector("#outreachSendBadge"),
  outreachSendTask: document.querySelector("#outreachSendTask"),
  outreachSendStatus: document.querySelector("#outreachSendStatus"),
  outreachSendActions: document.querySelector("#outreachSendActions"),
  outreachProgressSection: document.querySelector("#outreachProgressSection"),
  outreachProgressTimeline: document.querySelector("#outreachProgressTimeline"),
  outreachProgressCount: document.querySelector("#outreachProgressCount"),
  outreachProgressForm: document.querySelector("#outreachProgressForm"),
  progressEventType: document.querySelector("#progressEventType"),
  progressNote: document.querySelector("#progressNote"),
  progressEvidence: document.querySelector("#progressEvidence"),
};

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({ error: "服务返回了无法读取的内容" }));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function filteredJobs() {
  if (state.filter === "all") return state.jobs;
  if (state.filter === "unreviewed") return state.jobs.filter((job) => !job.reviewed);
  return state.jobs.filter((job) => job.interest === state.filter);
}

function renderProgress() {
  const total = state.stats.total || 0;
  const reviewed = state.stats.reviewed || 0;
  elements.progressText.textContent = `${reviewed} / ${total}`;
  elements.progressBar.style.width = `${total ? (reviewed / total) * 100 : 0}%`;
}

function renderCompanyProfile(job) {
  const profile = job.company_profile || {};
  const setValue = (id, value) => {
    document.querySelector(`#${id}`).textContent = value || "未采集";
  };
  setValue("companySize", profile.company_size);
  setValue("companyIndustry", profile.industry);
  setValue("companyFinancing", profile.financing);
  const age = profile.company_age && profile.founded_date
    ? `${profile.company_age}（${profile.founded_date}）`
    : profile.company_age || profile.founded_date;
  setValue("companyAge", age);
  setValue("companyLegalName", profile.legal_name);
  setValue("companyStatus", profile.operating_status);
  setValue("companyCapital", profile.registered_capital);
  document.querySelector("#companyCollectedAt").textContent = job.collected_at
    ? `采集于 ${String(job.collected_at).slice(0, 10)}`
    : "";
}

function createTextElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function invalidateMatchResults() {
  state.matching = null;
  state.boundary = null;
}

function inferredPreferenceTracks(profile, signals) {
  const tracks = new Set(
    (profile?.target_tracks || []).filter((track) => preferenceTrackOrder.includes(track)),
  );
  (signals || [])
    .filter((signal) => signal.dimension === "track" && signal.direction === "prefer")
    .forEach((signal) => {
      if (preferenceTrackOrder.includes(signal.value)) tracks.add(signal.value);
    });
  return preferenceTrackOrder.filter((track) => tracks.has(track));
}

function formatPreferenceAlignment(alignment, confirmed) {
  const current = Number(alignment?.strong_conflicts || 0);
  if (!confirmed) return `当前强冲突 ${current} 个，确认后重新计算`;
  const previous = Number(alignment?.previous_strong_conflicts);
  if (Number.isFinite(previous)) {
    return previous === current
      ? `强冲突 ${current} 个（与上版一致）`
      : `强冲突 ${previous} → ${current}`;
  }
  return `当前强冲突 ${current} 个`;
}

function setPreferenceFormDisabled(disabled) {
  if (!elements.preferenceForm) return;
  elements.preferenceForm.querySelectorAll("input, select, button").forEach((control) => {
    control.disabled = disabled;
  });
  if (state.preferenceSaving) {
    elements.preferenceSaveState.textContent = "正在保存偏好并重算岗位...";
    elements.savePreferencesButton.textContent = "重算中";
  } else if (state.collectionRunning) {
    elements.preferenceSaveState.textContent = "岗位采集中，暂不可确认偏好。";
    elements.savePreferencesButton.textContent = "等待采集结束";
  } else if (state.calibrationRunning) {
    elements.preferenceSaveState.textContent = "简历校准中，暂不可确认偏好。";
    elements.savePreferencesButton.textContent = "等待校准结束";
  } else {
    elements.savePreferencesButton.textContent = "确认并重算";
  }
}

function syncPreferenceTrackState() {
  elements.preferenceTrackOptions
    .querySelectorAll(".preference-track-option")
    .forEach((option) => {
      const input = option.querySelector("input");
      option.classList.toggle("selected", Boolean(input?.checked));
    });
}

function renderPreferenceConfirmation(dashboard) {
  const profile = dashboard.profile;
  if (!profile) {
    elements.preferenceConfirmation.hidden = true;
    return;
  }

  const preferences = dashboard.preference_calibration || {};
  const confirmedRecord = dashboard.confirmed_preferences || null;
  const confirmed = confirmedRecord?.settings || null;
  const inferredTracks = inferredPreferenceTracks(profile, preferences.signals);
  const selectedTracks = new Set(
    (confirmed?.target_tracks?.length ? confirmed.target_tracks : inferredTracks),
  );
  const constraints = confirmed?.hard_constraints || {};

  elements.preferenceConfirmation.hidden = false;
  elements.preferenceTrackOptions.replaceChildren();
  preferenceTrackOrder.forEach((track) => {
    const option = document.createElement("label");
    option.className = "preference-track-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "target_tracks";
    input.value = track;
    input.checked = selectedTracks.has(track);
    input.dataset.inferred = String(!confirmedRecord && inferredTracks.includes(track));
    const text = document.createElement("span");
    text.textContent = labels.tracks[track] || track;
    option.append(input, text);
    elements.preferenceTrackOptions.append(option);
  });
  syncPreferenceTrackState();

  elements.maximumExperience.value = constraints.maximum_required_experience_years
    ? String(constraints.maximum_required_experience_years)
    : "";
  elements.rejectThirdParty.checked = Boolean(constraints.reject_third_party);
  elements.rejectDispatch.checked = Boolean(constraints.reject_dispatch);
  elements.rejectLongTermOverseas.checked = Boolean(constraints.reject_long_term_overseas);
  elements.preferenceVersion.textContent = confirmedRecord?.version
    ? `已确认 ${confirmedRecord.version}`
    : "尚未确认 · 默认值仅供参考";
  elements.preferenceAlignment.textContent = formatPreferenceAlignment(
    dashboard.alignment || {},
    Boolean(confirmedRecord),
  );
  elements.preferenceFormError.textContent = "";
  if (!state.preferenceSaving) elements.preferenceSaveState.textContent = "";
  setPreferenceFormDisabled(state.collectionRunning || state.calibrationRunning || state.preferenceSaving);
}

function preferencePayload() {
  const targetTracks = Array.from(
    elements.preferenceForm.querySelectorAll('input[name="target_tracks"]:checked'),
  ).map((input) => input.value);
  const maximum = elements.maximumExperience.value;
  return {
    target_tracks: targetTracks,
    hard_constraints: {
      ...(maximum ? { maximum_required_experience_years: Number(maximum) } : {}),
      reject_third_party: elements.rejectThirdParty.checked,
      reject_dispatch: elements.rejectDispatch.checked,
      reject_long_term_overseas: elements.rejectLongTermOverseas.checked,
    },
  };
}

async function savePreferences(event) {
  event.preventDefault();
  if (
    !state.calibration?.profile
    || state.collectionRunning
    || state.calibrationRunning
    || state.preferenceSaving
  ) return;
  const payload = preferencePayload();
  elements.preferenceFormError.textContent = "";
  if (!payload.target_tracks.length) {
    elements.preferenceFormError.textContent = "请至少选择一个重点方向。";
    elements.preferenceTrackOptions.querySelector("input")?.focus();
    return;
  }

  state.preferenceSaving = true;
  setPreferenceFormDisabled(true);
  try {
    const profile = state.calibration.profile;
    const dashboard = await fetchJson("/api/calibration/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        candidate_id: profile.profile_id,
        analysis_version: state.calibration.match?.analysis_version || "resume-match-v1",
      }),
    });
    invalidateMatchResults("preferences");
    clearLearningCache();
    renderCalibrationDashboard(dashboard);
    elements.calibrationStatus.textContent = `偏好已确认，${dashboard.match?.total || 0} 个岗位已按新版本重算。`;
    elements.preferenceSaveState.textContent = formatPreferenceAlignment(
      dashboard.alignment || {},
      true,
    );
  } catch (error) {
    elements.preferenceFormError.textContent = `${error.message} 当前结果未改变。`;
  } finally {
    state.preferenceSaving = false;
    setPreferenceFormDisabled(state.collectionRunning || state.calibrationRunning);
  }
}

function renderCalibrationDashboard(dashboard) {
  state.calibration = dashboard;
  const profile = dashboard.profile;
  const preferences = dashboard.preference_calibration || {};
  const collection = dashboard.collection_plan || {};
  const match = dashboard.match || {};
  const unknowns = preferences.unknowns || [];
  const signals = preferences.signals || [];
  const skills = profile?.skills || [];

  document.querySelector("#calibrationSkillCount").textContent = String(skills.length);
  document.querySelector("#calibrationLocalJobs").textContent = String(collection.local_jobs || 0);
  document.querySelector("#calibrationReviewedJobs").textContent = String(preferences.reviewed_jobs || 0);
  document.querySelector("#calibrationUnknownCount").textContent = String(unknowns.length);
  document.querySelector("#calibrationAsOf").textContent = dashboard.calibrated_at
    ? `校准于 ${String(dashboard.calibrated_at).slice(0, 16).replace("T", " ")}`
    : "";
  document.querySelector("#calibrationScope").textContent = profile
    ? `${profile.display_name || profile.profile_id} · 已校准 ${match.total || 0} 个岗位 · 原始简历未保存`
    : dashboard.empty_message || "选择本地简历开始校准。";
  document.querySelector("#resumeProfileVersion").textContent = profile?.version || "";

  const summary = document.querySelector("#profileSummary");
  summary.replaceChildren();
  if (profile) {
    const educationLabels = { unknown: "未识别", high_school: "高中", associate: "大专", bachelor: "本科", master: "硕士", doctor: "博士" };
    [
      ["候选人", profile.display_name || profile.profile_id],
      ["经验", `${profile.experience_years || 0} 年`],
      ["学历", educationLabels[profile.education?.level] || "未识别"],
      ["目标方向", (profile.target_tracks || []).map((track) => labels.tracks[track] || track).join("、") || "待岗位样本确认"],
    ].forEach(([name, value]) => {
      const wrapper = document.createElement("div");
      wrapper.append(createTextElement("dt", "", name), createTextElement("dd", "", value));
      summary.append(wrapper);
    });
  }

  elements.resumeSkillList.replaceChildren();
  if (!skills.length) {
    elements.resumeSkillList.append(createTextElement("p", "list-empty", profile ? "没有提取到足够明确的能力证据。" : "完成校准后显示能力和对应简历原文。"));
  }
  skills.forEach((skill) => {
    const article = document.createElement("article");
    article.className = "resume-skill-item";
    article.append(createTextElement("h4", "", skill.name || skill.id));
    (skill.evidence || []).forEach((evidence) => article.append(createTextElement("p", "", evidence)));
    elements.resumeSkillList.append(article);
  });

  elements.collectionQueries.replaceChildren();
  (collection.queries || []).forEach((query) => elements.collectionQueries.append(createTextElement("span", "query-chip", query)));
  if (!(collection.queries || []).length) elements.collectionQueries.append(createTextElement("p", "list-empty", "校准后生成岗位搜索范围。"));
  const collectionMessages = {
    not_run: `当前已使用 ${collection.local_jobs || 0} 个本地岗位；可在本地校准后按画像补充岗位。`,
    running: "正在按简历画像采集岗位；你可以继续查看本地结果。",
    completed: `采集完成，本轮新增 ${collection.collected_new_jobs || 0} 个岗位，现有 ${collection.local_jobs || 0} 个。`,
    sample_completed: `采集完成，本轮新增 ${collection.collected_new_jobs || 0} 个岗位。`,
    exhausted: `已检索完当前范围，本轮新增 ${collection.collected_new_jobs || 0} 个岗位。`,
    security_check: "招聘平台触发安全校验，采集已停止；本地校准结果仍可使用。",
    stopped: "采集已停止；本地校准结果仍可使用。",
  };
  const collectionStatus = collection.external_collection || "not_run";
  const collectionMessage = collectionMessages[collectionStatus] || `采集状态：${collectionStatus}`;
  const collectionError = collection.external_collection_error ? ` ${collection.external_collection_error}` : "";
  document.querySelector("#externalCollectionState").textContent = `${collectionMessage}${collectionError}`;
  elements.collectResumeJobsButton.disabled = !profile || !state.calibratedResumeText || state.collectionRunning;

  elements.preferenceSignals.replaceChildren();
  if (!signals.length) elements.preferenceSignals.append(createTextElement("p", "list-empty", "现有审阅还不足以形成稳定偏好。"));
  signals.forEach((signal) => {
    const article = document.createElement("article");
    article.className = "preference-item";
    const heading = document.createElement("div");
    heading.append(
      createTextElement("h4", "", signal.label),
      createTextElement("span", "soft-label", `软调整 ${signal.score_adjustment > 0 ? "+" : ""}${signal.score_adjustment}`),
    );
    article.append(heading);
    const evidence = (signal.evidence || []).map((item) => `${item.title} · ${item.decision}`).join("；");
    article.append(createTextElement("p", "", evidence || "暂无可展示证据"));
    elements.preferenceSignals.append(article);
  });

  elements.calibrationUnknowns.replaceChildren();
  if (!unknowns.length) elements.calibrationUnknowns.append(createTextElement("p", "list-empty", "当前没有待确认项。"));
  unknowns.forEach((item) => {
    const article = document.createElement("article");
    article.className = "unknown-item";
    article.append(createTextElement("h4", "", item.label), createTextElement("p", "", item.reason));
    elements.calibrationUnknowns.append(article);
  });
  renderPreferenceConfirmation(dashboard);
}

function clearLearningCache() {
  state.learning = null;
}

async function loadCalibrationDashboard(force = false) {
  elements.calibrationError.textContent = "";
  if (state.calibration && !force) {
    renderCalibrationDashboard(state.calibration);
    return;
  }
  try {
    renderCalibrationDashboard(await fetchJson("/api/calibration"));
  } catch (error) {
    elements.calibrationError.textContent = `${error.message} 请重新选择简历校准。`;
  }
}

async function calibrateSelectedResume() {
  const file = elements.resumeFile.files?.[0];
  if (!file) return;
  elements.calibrationError.textContent = "";
  elements.calibrationStatus.textContent = "正在提取简历证据并重算岗位匹配...";
  state.calibrationRunning = true;
  setPreferenceFormDisabled(true);
  elements.calibrateResumeButton.disabled = true;
  elements.calibrateResumeButton.textContent = "校准中";
  try {
    const resumeText = await file.text();
    const dashboard = await fetchJson("/api/calibration", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume_text: resumeText, filename: file.name }),
    });
    invalidateMatchResults();
    state.calibratedResumeText = resumeText;
    state.calibratedFilename = file.name;
    renderCalibrationDashboard(dashboard);
    elements.calibrationStatus.textContent = `已完成：${dashboard.match?.total || 0} 个岗位重新校准。`;
  } catch (error) {
    elements.calibrationError.textContent = `${error.message} 请检查简历内容后重试。`;
    elements.calibrationStatus.textContent = "";
  } finally {
    state.calibrationRunning = false;
    setPreferenceFormDisabled(state.collectionRunning || state.preferenceSaving);
    elements.calibrateResumeButton.disabled = state.collectionRunning || !file;
    elements.calibrateResumeButton.textContent = "1. 本地校准";
  }
}

function setCollectionRunning(running) {
  state.collectionRunning = running;
  elements.resumeFile.disabled = running;
  elements.resumeFileAction.classList.toggle("disabled", running);
  elements.resumeFileAction.setAttribute("aria-disabled", String(running));
  elements.calibrateResumeButton.disabled = running || !elements.resumeFile.files?.[0];
  elements.collectResumeJobsButton.disabled = running || !state.calibratedResumeText;
  elements.collectResumeJobsButton.textContent = running ? "采集中" : "2. 按画像采集";
  setPreferenceFormDisabled(running || state.calibrationRunning || state.preferenceSaving);
}

async function pollCollectionStatus() {
  const payload = await fetchJson("/api/calibration/collection-status");
  const collection = payload.collection || { status: "idle" };
  if (collection.status === "running") {
    setCollectionRunning(true);
    elements.calibrationStatus.textContent = "正在读取岗位与公司信息；可继续使用其他视图。";
    window.setTimeout(() => pollCollectionStatus().catch(showCollectionPollingError), 1500);
    return;
  }
  setCollectionRunning(false);
  if (collection.status === "idle") return;
  const collectionCompleted = ["completed", "exhausted", "sample_completed"].includes(collection.status);
  if (collectionCompleted) state.learning = null;
  invalidateMatchResults();
  const dashboard = await fetchJson("/api/calibration");
  renderCalibrationDashboard(dashboard);
  const added = Number(collection.collected_new_jobs || 0);
  elements.calibrationStatus.textContent = collectionCompleted
    ? `采集结束：新增 ${added} 个岗位，匹配已重算。`
    : "采集已停止，本地校准结果仍可使用。";
}

function showCollectionPollingError(error) {
  elements.calibrationError.textContent = `${error.message} 无法读取采集状态，请稍后刷新。`;
}

async function collectResumeJobs() {
  if (!state.calibratedResumeText || state.collectionRunning) return;
  let collectionStarted = false;
  elements.calibrationError.textContent = "";
  setCollectionRunning(true);
  elements.calibrationStatus.textContent = "正在启动按画像采集...";
  try {
    await fetchJson("/api/calibration/collect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resume_text: state.calibratedResumeText,
        filename: state.calibratedFilename || "resume.md",
      }),
    });
    collectionStarted = true;
    await pollCollectionStatus();
  } catch (error) {
    if (!collectionStarted) setCollectionRunning(false);
    elements.calibrationError.textContent = `${error.message} 本地校准结果未受影响。`;
    elements.calibrationStatus.textContent = collectionStarted
      ? "采集仍在后台运行，请稍后刷新页面查看状态。"
      : "";
  }
}

function renderLearningDashboard(dashboard) {
  state.learning = dashboard;
  const snapshot = dashboard.snapshot || {};
  const notes = dashboard.notes || {};
  const priorities = dashboard.priorities || [];
  const demands = dashboard.market_demands || [];
  document.querySelector("#learningScope").textContent = `基于本地 ${snapshot.collected_jobs || 0} 个岗位，其中 ${snapshot.target_jobs || 0} 个处于当前可投或挑战范围`;
  document.querySelector("#learningAsOf").textContent = snapshot.latest_collected_at
    ? `数据截至 ${String(snapshot.latest_collected_at).slice(0, 10)}`
    : "";
  document.querySelector("#learningTargetJobs").textContent = String(snapshot.target_jobs || 0);
  document.querySelector("#learningReviewedJobs").textContent = String(snapshot.reviewed_jobs || 0);
  document.querySelector("#learningNoteCount").textContent = String(notes.learning || 0);
  document.querySelector("#learningDemandCount").textContent = String(demands.length);

  elements.learningPriorities.replaceChildren();
  if (!priorities.length) {
    elements.learningPriorities.append(createTextElement("p", "list-empty", "还没有足够的学习信号。"));
  }
  priorities.forEach((priority, index) => {
    const row = document.createElement("article");
    row.className = "priority-item";
    const number = createTextElement("span", "priority-number", String(index + 1).padStart(2, "0"));
    const body = document.createElement("div");
    body.className = "priority-body";
    const heading = document.createElement("div");
    heading.className = "priority-heading";
    heading.append(
      createTextElement("h4", "", priority.name),
      createTextElement(
        "span",
        "priority-signal",
        priority.note_count
          ? `${priority.note_count} 条备注 · ${priority.market_job_count} 个岗位 / ${priority.share_percent}%`
          : `${priority.market_job_count} 个岗位 / ${priority.share_percent}%`,
      ),
    );
    const recommendation = createTextElement("p", "priority-recommendation", priority.recommendation);
    const deliverable = document.createElement("p");
    deliverable.className = "priority-deliverable";
    deliverable.append(
      createTextElement("span", "", "建议产出"),
      createTextElement("strong", "", priority.deliverable),
    );
    body.append(heading, recommendation, deliverable);
    row.append(number, body);
    elements.learningPriorities.append(row);
  });

  elements.marketDemands.replaceChildren();
  demands.slice(0, 10).forEach((demand) => {
    const row = document.createElement("div");
    row.className = "demand-item";
    const heading = document.createElement("div");
    heading.className = "demand-heading";
    heading.append(
      createTextElement("strong", "", demand.name),
      createTextElement("span", "", `${demand.job_count} / ${snapshot.target_jobs || 0}`),
    );
    const track = document.createElement("div");
    track.className = "demand-track";
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(0, Math.min(100, demand.share_percent || 0))}%`;
    track.append(fill);
    row.append(heading, track);
    elements.marketDemands.append(row);
  });

  const categoryLabels = { learning: "学习", risk: "风险", strength: "优势", general: "其他" };
  const summary = document.querySelector("#noteSummary");
  summary.replaceChildren();
  ["learning", "risk", "strength", "general"].forEach((category) => {
    if (!notes[category]) return;
    summary.append(createTextElement("span", category, `${categoryLabels[category]} ${notes[category]}`));
  });

  elements.noteSignals.replaceChildren();
  (notes.signals || []).forEach((signal) => {
    const row = document.createElement("article");
    row.className = "note-signal-item";
    const heading = document.createElement("div");
    heading.className = "note-signal-heading";
    const badge = createTextElement("span", `note-category ${signal.category}`, categoryLabels[signal.category] || "其他");
    const jobLabel = [signal.title, signal.company].filter(Boolean).join(" · ");
    if (signal.sample_name) {
      const jobButton = createTextElement("button", "note-job-link", jobLabel);
      jobButton.type = "button";
      jobButton.dataset.jobId = signal.job_id;
      jobButton.dataset.platform = signal.platform;
      jobButton.dataset.sampleName = signal.sample_name;
      heading.append(badge, jobButton);
    } else {
      heading.append(badge, createTextElement("span", "note-job-label", jobLabel));
    }
    row.append(heading, createTextElement("p", "", signal.excerpt));
    elements.noteSignals.append(row);
  });
  if (!notes.signals?.length) {
    elements.noteSignals.append(createTextElement("p", "list-empty", "还没有个人备注。"));
  }
}

async function loadLearningDashboard() {
  elements.learningError.textContent = "";
  if (state.learning) {
    renderLearningDashboard(state.learning);
    return;
  }
  try {
    const dashboard = await fetchJson("/api/learning");
    renderLearningDashboard(dashboard);
  } catch (error) {
    elements.learningError.textContent = `${error.message} 请确认本地数据库可读后重试。`;
  }
}

function filteredMatches() {
  const jobs = state.matching?.jobs || [];
  if (state.matchFilter === "all") return jobs;
  if (state.matchFilter === "changed") return jobs.filter((job) => job.changed);
  if (state.matchFilter === "baseline_pending" || state.matchFilter === "jd_changed") {
    return jobs.filter((job) => job.match_refresh_reason === state.matchFilter);
  }
  return jobs.filter((job) => job.recommendation === state.matchFilter);
}

function renderMatchQueue() {
  const jobs = filteredMatches().slice();
  if (state.matchFilter === "baseline_pending" || state.matchFilter === "jd_changed") {
    jobs.sort((left, right) => (
      Number(left.refresh_priority ?? 99) - Number(right.refresh_priority ?? 99)
      || Number(right.score ?? 0) - Number(left.score ?? 0)
      || String(left.job_id).localeCompare(String(right.job_id))
    ));
  }
  const filterLabels = {
    all: "全部岗位",
    priority: "优先投",
    challenge: "挑战投",
    not_fit: "当前不建议投",
    changed: "新旧判断冲突",
    baseline_pending: "待建立基线",
    jd_changed: "JD 已变化",
  };
  document.querySelector("#matchFilterTitle").textContent = filterLabels[state.matchFilter];
  elements.matchVisibleCount.textContent = String(jobs.length);
  elements.matchJobList.replaceChildren();
  if (!jobs.length) {
    elements.matchJobList.append(createTextElement("p", "list-empty", "这个筛选下没有岗位。"));
    return;
  }
  jobs.forEach((job) => {
    const button = document.createElement("button");
    button.type = "button";
    const selected = job.job_id === state.selectedMatchId
      && (!state.selectedMatchPlatform || job.platform === state.selectedMatchPlatform);
    button.className = `match-job-item${selected ? " active" : ""}`;
    button.dataset.matchJobId = job.job_id;
    button.dataset.platform = job.platform;
    const top = document.createElement("span");
    top.className = "match-job-top";
    top.append(
      createTextElement("span", `match-label ${job.recommendation}`, labels.recommendations[job.recommendation]),
      createTextElement("strong", "match-list-score", String(job.score)),
    );
    const title = createTextElement("span", "match-job-title", job.title || "未命名岗位");
    const company = createTextElement("span", "match-job-company", job.company || "公司信息缺失");
    const meta = document.createElement("span");
    meta.className = "match-job-meta";
    meta.append(
      createTextElement("span", "", labels.tracks[job.track] || job.track),
      createTextElement("span", "", job.experience_raw || "经验未提供"),
    );
    if (job.changed) meta.append(createTextElement("span", "match-change-flag", "v1→v2"));
    if (job.match_refresh_reason === "baseline_pending") {
      meta.append(createTextElement("span", "match-change-flag", "待建立基线"));
    }
    if (job.match_refresh_reason === "jd_changed") {
      meta.append(createTextElement("span", "match-change-flag", "JD 已变化"));
    }
    if (job.match_stale) {
      meta.append(createTextElement(
        "span",
        "match-refresh-priority",
        `优先处理 ${job.refresh_priority ?? "-"} · ${job.refresh_priority_reason || "待处理匹配"}`,
      ));
    }
    if (job.refresh_changed) meta.append(createTextElement("span", "match-change-flag", "重算有变化"));
    button.append(top, title, company, meta);
    button.addEventListener("click", () => selectMatch(job.job_id, job.platform));
    elements.matchJobList.append(button);
  });
}

function renderMatchingDashboard(dashboard) {
  state.matching = dashboard;
  const snapshot = dashboard.snapshot;
  if (!snapshot) {
    document.querySelector("#matchScope").textContent = dashboard.empty_message || "尚未生成匹配预览。";
    document.querySelector("#matchVersion").textContent = "";
    ["matchTotal", "matchPriority", "matchChallenge", "matchNotFit", "matchConflicts", "matchBaselinePending", "matchJdChanged"]
      .forEach((id) => { document.querySelector(`#${id}`).textContent = "0"; });
    renderMatchQueue();
    elements.matchDetailEmpty.querySelector("strong").textContent = "尚未生成 v2 匹配预览";
    elements.matchDetailEmpty.querySelector("span").textContent = dashboard.empty_message || "请先生成本地预览。";
    return;
  }
  document.querySelector("#matchScope").textContent = `${snapshot.profile_id} · ${snapshot.total} 个岗位 · ${snapshot.insufficient} 个信息不足 · ${snapshot.recently_refreshed || 0} 条有重算记录`;
  document.querySelector("#matchVersion").textContent = `${snapshot.analysis_version} / ${snapshot.profile_version}`;
  document.querySelector("#matchTotal").textContent = String(snapshot.total);
  document.querySelector("#matchPriority").textContent = String(snapshot.priority);
  document.querySelector("#matchChallenge").textContent = String(snapshot.challenge);
  document.querySelector("#matchNotFit").textContent = String(snapshot.not_fit);
  document.querySelector("#matchConflicts").textContent = String(snapshot.conflicts);
  document.querySelector("#matchBaselinePending").textContent = String(snapshot.baseline_pending || 0);
  document.querySelector("#matchJdChanged").textContent = String(snapshot.jd_changed || 0);
  renderMatchQueue();
}

function renderEvidenceCards(containerId, values, emptyText) {
  const container = document.querySelector(`#${containerId}`);
  container.replaceChildren();
  if (!values?.length) {
    container.append(createTextElement("p", "list-empty", emptyText));
    return;
  }
  values.forEach((item) => {
    const row = document.createElement("article");
    row.className = "match-evidence-item";
    row.append(createTextElement("h4", "", item.label || item.id));
    const evidence = document.createElement("dl");
    evidence.append(
      createTextElement("dt", "", "JD 要求"),
      createTextElement("dd", "", item.jd_evidence || "未提取到原文"),
      createTextElement("dt", "", "个人证据"),
      createTextElement("dd", "", item.candidate_evidence || "当前档案没有直接证据"),
    );
    row.append(evidence);
    container.append(row);
  });
}

function renderRefreshComparison(comparison) {
  const container = document.querySelector("#refreshEvidenceChanges");
  const addedColumn = document.querySelector("#refreshAddedColumn");
  const removedColumn = document.querySelector("#refreshRemovedColumn");
  const addedList = document.querySelector("#refreshAddedEvidence");
  const removedList = document.querySelector("#refreshRemovedEvidence");
  if (!container || !addedColumn || !removedColumn || !addedList || !removedList) return;
  const added = [
    ...(comparison?.hard_requirements_added || []).map((item) => `硬门槛：${item.label || item.id}`),
    ...(comparison?.gaps_added || []).map((item) => `能力缺口：${item.label || item.id}`),
    ...(comparison?.uncertainties_added || []).map((item) => `待确认：${item}`),
  ];
  const removed = [
    ...(comparison?.hard_requirements_removed || []).map((item) => `硬门槛：${item.label || item.id}`),
    ...(comparison?.gaps_removed || []).map((item) => `能力缺口：${item.label || item.id}`),
    ...(comparison?.uncertainties_removed || []).map((item) => `待确认：${item}`),
  ];
  const renderList = (list, values) => {
    list.replaceChildren();
    values.forEach((value) => list.append(createTextElement("li", "", value)));
  };
  renderList(addedList, added);
  renderList(removedList, removed);
  addedColumn.hidden = !added.length;
  removedColumn.hidden = !removed.length;
  container.hidden = !(added.length || removed.length);
}

function renderMatchDetail(match) {
  elements.matchDetailEmpty.hidden = true;
  elements.matchDetailContent.hidden = false;
  elements.matchingError.textContent = "";
  const recommendation = document.querySelector("#matchRecommendation");
  recommendation.className = `match-label ${match.recommendation}`;
  recommendation.textContent = labels.recommendations[match.recommendation] || match.recommendation;
  const information = document.querySelector("#matchInformation");
  information.className = `information-label ${match.information_status}`;
  information.textContent = labels.information[match.information_status] || match.information_status;
  document.querySelector("#matchChanged").hidden = !match.changed;
  const matchStale = document.querySelector("#matchStale");
  const refreshReason = match.match_refresh_reason || (
    match.match_stale ? (match.jd_content_hash ? "jd_changed" : "baseline_pending") : null
  );
  matchStale.hidden = !refreshReason;
  matchStale.textContent = refreshReason === "baseline_pending"
    ? "待建立匹配基线"
    : "JD 已变化，待更新匹配";
  const refreshPriority = document.querySelector("#matchRefreshPriority");
  refreshPriority.hidden = !refreshReason;
  refreshPriority.textContent = refreshReason
    ? `优先处理 ${match.refresh_priority ?? "-"} · ${match.refresh_priority_reason || "待处理匹配"}`
    : "";
  document.querySelector("#matchRefreshChanged").hidden = !match.refresh_changed;
  const refreshComparison = document.querySelector("#refreshComparison");
  const previousRefreshMatch = document.querySelector("#previousRefreshMatch");
  const currentRefreshMatch = document.querySelector("#currentRefreshMatch");
  const refreshComparisonMeta = document.querySelector("#refreshComparisonMeta");
  const refreshComparisonLabel = document.querySelector("#refreshComparisonLabel");
  const refreshComparisonHeading = document.querySelector("#refreshComparisonHeading");
  const previousRefreshLabel = document.querySelector("#previousRefreshLabel");
  const currentRefreshLabel = document.querySelector("#currentRefreshLabel");
  if (match.previous_match) {
    const previous = match.previous_match;
    const refreshKind = match.refresh_comparison?.refresh_kind;
    const isBaseline = refreshKind === "baseline_initialization";
    refreshComparison.hidden = false;
    refreshComparisonLabel.textContent = isBaseline ? "首次建立匹配基线" : "JD 更新重算";
    refreshComparisonHeading.textContent = isBaseline ? "基线建立前后" : "JD 更新前后";
    previousRefreshLabel.textContent = isBaseline ? "原始判断" : "更新前";
    currentRefreshLabel.textContent = isBaseline ? "基线判断" : "更新后";
    previousRefreshMatch.textContent = `${labels.recommendations[previous.recommendation] || previous.recommendation} · ${previous.score} 分`;
    currentRefreshMatch.textContent = `${labels.recommendations[match.recommendation] || match.recommendation} · ${match.score} 分`;
    refreshComparisonMeta.textContent = previous.captured_at
      ? `${isBaseline ? "建立于" : "重算于"} ${formatFreshnessTime(previous.captured_at)}`
      : "";
    renderRefreshComparison(match.refresh_comparison);
  } else {
    refreshComparison.hidden = true;
    previousRefreshMatch.textContent = "";
    currentRefreshMatch.textContent = "";
    refreshComparisonMeta.textContent = "";
    refreshComparisonLabel.textContent = "匹配刷新记录";
    refreshComparisonHeading.textContent = "更新前后";
    previousRefreshLabel.textContent = "更新前";
    currentRefreshLabel.textContent = "更新后";
    renderRefreshComparison(null);
  }
  document.querySelector("#matchJobTitle").textContent = match.title || "未命名岗位";
  document.querySelector("#matchCompanyLine").textContent = match.company || "公司信息缺失";
  document.querySelector("#matchScore").textContent = String(match.score ?? 0);
  document.querySelector("#matchConfidence").textContent = `置信度 ${Math.round((match.confidence || 0) * 100)}%`;

  const meta = document.querySelector("#matchMeta");
  meta.replaceChildren();
  [
    ["方向", labels.tracks[match.track] || match.track],
    ["薪资", match.salary_raw],
    ["地点", match.location],
    ["经验", match.experience_raw],
    ["学历", match.education_raw],
  ].forEach(([name, value]) => {
    const wrapper = document.createElement("div");
    wrapper.append(createTextElement("dt", "", name), createTextElement("dd", "", value || "未提供"));
    meta.append(wrapper);
  });

  document.querySelector("#previousMatch").textContent = labels.tiers[match.previous_fit_tier] || "旧版无判断";
  document.querySelector("#currentMatch").textContent = labels.recommendations[match.recommendation] || match.recommendation;
  document.querySelector("#hardRequirementCount").textContent = `${match.hard_requirements?.length || 0} 项`;
  document.querySelector("#matchStrengthCount").textContent = `${match.strengths?.length || 0} 项`;
  document.querySelector("#matchGapCount").textContent = `${match.gaps?.length || 0} 项`;
  renderEvidenceCards("hardRequirements", match.hard_requirements, "没有识别到明确不满足的硬门槛。交由能力和偏好继续判断。");
  renderEvidenceCards("matchStrengths", match.strengths, "当前档案还没有匹配到直接能力证据。");
  renderEvidenceCards("matchGaps", match.gaps, "没有识别到需要单独补齐的能力缺口。");
  const preferenceSignals = match.preference_signals || [];
  document.querySelector("#preferenceSignalsSection").hidden = !preferenceSignals.length;
  document.querySelector("#matchPreferenceCount").textContent = `${preferenceSignals.length} 项`;
  renderEvidenceCards(
    "matchPreferenceSignals",
    preferenceSignals.map((item) => ({
      ...item,
      jd_evidence: `分数${item.adjustment > 0 ? "+" : ""}${item.adjustment} · 仅作软调整`,
      candidate_evidence: (item.evidence || []).map((value) => value.title || value.note).filter(Boolean).join("；"),
    })),
    "没有行为偏好影响这条结果。",
  );

  const uncertainties = document.querySelector("#matchUncertainties");
  uncertainties.replaceChildren();
  (match.uncertainties || []).forEach((value) => uncertainties.append(createTextElement("li", "", value)));
  document.querySelector("#matchUncertaintySection").hidden = !(match.uncertainties || []).length;
  document.querySelector("#matchJdText").textContent = match.jd_text || "没有采集到 JD 正文";
  const reviewButton = document.querySelector("#openReviewJob");
  reviewButton.dataset.jobId = match.job_id;
  reviewButton.dataset.platform = match.platform;
  reviewButton.dataset.sampleName = match.sample_name || "__reviewed__";
  reviewButton.hidden = !match.decision;
}

async function selectMatch(jobId, platform = "") {
  state.selectedMatchId = jobId;
  state.selectedMatchPlatform = platform || null;
  renderMatchQueue();
  const requestId = ++state.matchDetailRequest;
  elements.matchDetailEmpty.hidden = false;
  elements.matchDetailEmpty.querySelector("strong").textContent = "正在读取匹配证据";
  elements.matchDetailEmpty.querySelector("span").textContent = "结果来自本地版本化分析。";
  elements.matchDetailContent.hidden = true;
  try {
    const query = new URLSearchParams();
    if (platform) query.set("platform", platform);
    const suffix = query.toString() ? `?${query}` : "";
    const payload = await fetchJson(`/api/matches/${encodeURIComponent(jobId)}${suffix}`);
    if (requestId !== state.matchDetailRequest) return;
    state.selectedMatchPlatform = payload.match.platform || platform || null;
    renderMatchDetail(payload.match);
  } catch (error) {
    if (requestId !== state.matchDetailRequest) return;
    elements.matchDetailEmpty.querySelector("strong").textContent = "匹配详情读取失败";
    elements.matchDetailEmpty.querySelector("span").textContent = error.message;
  }
}

async function loadMatchingDashboard() {
  elements.matchingError.textContent = "";
  try {
    const dashboard = await fetchJson("/api/matches");
    renderMatchingDashboard(dashboard);
    if (dashboard.jobs?.length && !state.selectedMatchId) {
      await selectMatch(dashboard.jobs[0].job_id, dashboard.jobs[0].platform);
    }
  } catch (error) {
    elements.matchingError.textContent = `${error.message} 请确认本地匹配预览已经生成。`;
  }
}

async function refreshStaleMatches() {
  if (!elements.refreshStaleMatchesButton) return;
  elements.refreshStaleMatchesButton.disabled = true;
  elements.refreshStaleMatchesState.textContent = "正在重算...";
  elements.matchingError.textContent = "";
  try {
    const result = await fetchJson("/api/matches/refresh-stale", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 20 }),
    });
    elements.refreshStaleMatchesState.textContent = result.refreshed
      ? `建立基线 ${result.baseline_initialized || 0} 条，因 JD 变化重算 ${result.jd_changed_refreshed || 0} 条，剩余 ${result.remaining} 条`
      : "没有可重算岗位";
    state.matching = null;
    await loadMatchingDashboard();
  } catch (error) {
    elements.refreshStaleMatchesState.textContent = "";
    elements.matchingError.textContent = `${error.message} 当前匹配结果未改变。`;
  } finally {
    elements.refreshStaleMatchesButton.disabled = false;
  }
}

function renderBoundaryDashboard(dashboard) {
  state.boundary = dashboard;
  const snapshot = dashboard.snapshot;
  if (!snapshot) {
    document.querySelector("#boundaryScope").textContent = dashboard.empty_message || "尚未生成边界样本。";
    document.querySelector("#boundaryVersion").textContent = "";
    ["boundarySelected", "boundaryStrong", "boundarySoft", "boundaryInformation", "boundaryLearning"]
      .forEach((id) => { document.querySelector(`#${id}`).textContent = "0"; });
    elements.boundaryInsights.replaceChildren(createTextElement("p", "list-empty", "暂无可分析的误判模式。"));
    elements.boundaryGroups.replaceChildren(createTextElement("p", "list-empty", dashboard.empty_message || "暂无复核样本。"));
    elements.boundaryRuleGate.textContent = "";
    return;
  }

  document.querySelector("#boundaryScope").textContent = `基于 ${snapshot.reviewed_jobs} 条已审岗位，抽取 ${snapshot.selected_jobs} 条高信息样本`;
  document.querySelector("#boundaryVersion").textContent = `${snapshot.analysis_version} / ${snapshot.profile_version}`;
  const selectedMetrics = snapshot.selected_metrics || {};
  document.querySelector("#boundarySelected").textContent = String(selectedMetrics.jobs ?? snapshot.selected_jobs ?? 0);
  document.querySelector("#boundaryStrong").textContent = String(selectedMetrics.strong_conflicts || 0);
  document.querySelector("#boundarySoft").textContent = String(selectedMetrics.soft_conflicts || 0);
  document.querySelector("#boundaryInformation").textContent = String(selectedMetrics.information_gaps || 0);
  document.querySelector("#boundaryLearning").textContent = String(selectedMetrics.learning_signals || 0);

  elements.boundaryInsights.replaceChildren();
  (dashboard.insights || []).forEach((insight) => {
    const article = document.createElement("article");
    article.className = `boundary-insight ${insight.severity || "medium"}`;
    const heading = document.createElement("div");
    heading.append(
      createTextElement("h4", "", insight.title),
      createTextElement("span", "boundary-insight-count", String(insight.count || 0)),
    );
    article.append(heading, createTextElement("p", "", insight.detail));
    elements.boundaryInsights.append(article);
  });
  const actions = dashboard.actions || {};
  elements.boundaryRuleGate.replaceChildren(
    createTextElement("strong", "", actions.threshold_change_recommended ? "已达到规则调整门槛" : "暂不自动改阈值"),
    createTextElement("p", "", actions.rule_change_gate || "先确认冲突原因，再修改规则。"),
    createTextElement("p", "boundary-next-step", actions.next_step || ""),
  );

  const signalLabels = {
    conflict: "判断冲突",
    information: "信息不全",
    learning: "学习标记",
    correction: "已有纠错",
    boundary: "临近边界",
  };
  elements.boundaryGroups.replaceChildren();
  (dashboard.groups || []).forEach((group) => {
    const section = document.createElement("section");
    section.className = `boundary-group ${group.recommendation}`;
    const heading = document.createElement("header");
    const title = document.createElement("div");
    title.append(
      createTextElement("h4", "", group.label),
      createTextElement("span", "", `已审 ${group.available} · 本轮 ${group.jobs?.length || 0}`),
    );
    heading.append(title);
    section.append(heading);

    const list = document.createElement("div");
    list.className = "boundary-job-list";
    (group.jobs || []).forEach((job) => {
      const article = document.createElement("article");
      article.className = `boundary-job ${job.conflict_level}`;
      const top = document.createElement("div");
      top.className = "boundary-job-top";
      const identity = document.createElement("div");
      identity.className = "boundary-job-identity";
      identity.append(
        createTextElement("h5", "", job.title || "未命名岗位"),
        createTextElement("p", "", [job.company, job.salary_raw, job.experience_raw].filter(Boolean).join(" · ") || "岗位信息缺失"),
      );
      const score = document.createElement("div");
      score.className = "boundary-job-score";
      score.append(
        createTextElement("strong", "", String(job.score ?? 0)),
        createTextElement("span", `conflict-label ${job.conflict_level}`, labels.conflicts[job.conflict_level] || job.conflict_level),
      );
      top.append(identity, score);

      const decision = document.createElement("div");
      decision.className = "boundary-decision-line";
      decision.append(
        createTextElement("span", `match-label ${job.recommendation}`, labels.recommendations[job.recommendation] || job.recommendation),
        createTextElement("span", "boundary-arrow", "→"),
        createTextElement("span", `interest-label ${job.interest}`, `你的决定：${labels.interests[job.interest] || job.interest}`),
      );

      const signals = document.createElement("div");
      signals.className = "boundary-signals";
      (job.signals || []).forEach((signal) => signals.append(createTextElement("span", signal, signalLabels[signal] || signal)));

      const reasons = document.createElement("ul");
      reasons.className = "boundary-reasons";
      (job.reasons || []).forEach((reason) => reasons.append(createTextElement("li", "", reason)));
      const evidence = document.createElement("div");
      evidence.className = "boundary-evidence";
      if (job.note) {
        const note = document.createElement("p");
        note.append(
          createTextElement("strong", "", "个人备注"),
          createTextElement("span", "", job.note),
        );
        evidence.append(note);
      }
      const evidenceGroups = [
        ["JD 证据 · 硬门槛", job.hard_requirements, "hard"],
        ["JD 证据 · 能力缺口", job.gaps, "gap"],
        ["JD 证据 · 匹配优势", job.strengths, "strength"],
      ];
      evidenceGroups.forEach(([label, values, className]) => {
        const jdEvidence = (values || [])
          .map((item) => item.jd_evidence)
          .filter(Boolean)
          .slice(0, 2);
        if (!jdEvidence.length) return;
        const source = document.createElement("p");
        source.className = className;
        source.append(
          createTextElement("strong", "", label),
          createTextElement("span", "", jdEvidence.join("；")),
        );
        evidence.append(source);
      });
      const open = createTextElement("button", "text-action boundary-open-review", "回到原审阅");
      open.type = "button";
      open.dataset.jobId = job.job_id;
      open.dataset.platform = job.platform;
      open.dataset.sampleName = job.sample_name || DEFAULT_SAMPLE_NAME;
      article.append(top, decision);
      if (job.signals?.length) article.append(signals);
      article.append(reasons);
      if (evidence.childElementCount) article.append(evidence);
      article.append(open);
      list.append(article);
    });
    if (!group.jobs?.length) list.append(createTextElement("p", "list-empty", "这一档没有已审样本。"));
    section.append(list);
    elements.boundaryGroups.append(section);
  });
}

async function loadBoundaryDashboard(force = false) {
  elements.boundaryError.textContent = "";
  if (state.boundary && !force) {
    renderBoundaryDashboard(state.boundary);
    return;
  }
  try {
    renderBoundaryDashboard(await fetchJson("/api/boundary-calibration"));
  } catch (error) {
    elements.boundaryError.textContent = `${error.message} 请确认本地匹配预览和审阅记录可读。`;
  }
}

function formatFreshnessTime(value) {
  if (!value) return "尚未检查";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function filteredFreshnessJobs() {
  const jobs = state.freshness?.jobs || [];
  if (state.freshnessFilter === "all") return jobs;
  if (["detail_due", "retry_due", "scan_attention", "detail_missing"].includes(state.freshnessFilter)) {
    return jobs.filter((job) => job.refresh_attention === state.freshnessFilter);
  }
  if (state.freshnessFilter === "closed") {
    return jobs.filter((job) => ["closed", "archived"].includes(job.lifecycle_state));
  }
  return jobs.filter((job) => job.lifecycle_state === state.freshnessFilter);
}

function createFreshnessStatus(job) {
  return createTextElement(
    "span",
    `freshness-status ${job.lifecycle_state}`,
    labels.freshness[job.lifecycle_state] || job.lifecycle_state,
  );
}

function freshnessAction(job) {
  return {
    detail_due: "重新获取详情",
    retry_due: "重试详情",
    scan_attention: "重新扫描列表",
    detail_missing: "补采完整 JD",
  }[job.refresh_attention] || "无需动作";
}

function freshnessActionLabel(job) {
  return {
    detail_refresh: "详情复核",
    coverage_scan: "列表复扫",
  }[job.refresh_action] || freshnessAction(job);
}

function renderRefreshPlan(plan) {
  elements.refreshPlan.replaceChildren();
  plan.slice(0, 10).forEach((job, index) => {
    const row = document.createElement("article");
    row.className = "refresh-plan-item";
    const number = createTextElement("span", "refresh-plan-number", String(index + 1).padStart(2, "0"));
    const body = document.createElement("div");
    const heading = document.createElement("div");
    heading.className = "refresh-plan-heading";
      heading.append(createTextElement("h4", "", job.title || "未命名岗位"), createFreshnessStatus(job));
    body.append(
      heading,
      createTextElement("p", "", `${job.company || "公司信息缺失"} · ${freshnessActionLabel(job)}`),
      createTextElement("small", "", `距最近有效证据 ${job.age_days} 天 · 建议周期 ${job.ttl_days} 天`),
    );
    row.append(number, body);
    elements.refreshPlan.append(row);
  });
  if (!plan.length) {
    elements.refreshPlan.append(createTextElement("p", "list-empty", "还没有可安排的岗位。"));
  }
}

function renderFreshnessJobs() {
  const jobs = filteredFreshnessJobs();
  elements.freshnessVisibleCount.textContent = `${jobs.length} 个`;
  elements.freshnessJobList.replaceChildren();
  jobs.forEach((job) => {
    const row = document.createElement("article");
    row.className = "freshness-job-item";
    const heading = document.createElement("div");
    heading.className = "freshness-job-heading";
    const title = document.createElement("div");
    title.append(
      createTextElement("h4", "", job.title || "未命名岗位"),
      createTextElement("p", "", job.company || "公司信息缺失"),
    );
    heading.append(title, createFreshnessStatus(job));
    if (job.scan_state && job.scan_state !== "listed") {
      heading.append(createTextElement("span", `freshness-scan ${job.scan_state}`,
        job.scan_state === "likely_closed" ? "连续两轮未见" : "本轮未见"));
    }

    const evidence = document.createElement("dl");
    evidence.className = "freshness-evidence";
    [
      ["最近 JD 证据", job.has_detail
        ? formatFreshnessTime(job.last_success_at || job.collected_at)
        : "尚未采集完整 JD"],
      ["刷新周期", `${job.ttl_days} 天`],
      ["当前间隔", `${job.age_days} 天`],
      ["下一步", freshnessActionLabel(job)],
    ].forEach(([name, value]) => {
      const wrapper = document.createElement("div");
      wrapper.append(createTextElement("dt", "", name), createTextElement("dd", "", value));
      evidence.append(wrapper);
    });
    row.append(heading, evidence);
    if (job.last_error) row.append(createTextElement("p", "freshness-last-error", job.last_error));
    if (job.source_url) {
      const source = document.createElement("a");
      source.className = "freshness-source-link";
      source.href = job.source_url;
      source.target = "_blank";
      source.rel = "noreferrer";
      source.textContent = "查看原职位";
      row.append(source);
    }
    elements.freshnessJobList.append(row);
  });
  if (!jobs.length) {
    elements.freshnessJobList.append(createTextElement("p", "list-empty", "这个状态下没有岗位。"));
  }
}

function renderRoleTracks(roleTracks) {
  const container = document.querySelector("#roleTrackList");
  if (!container) return;
  container.replaceChildren();
  (roleTracks?.tracks || []).forEach((track) => {
    const row = document.createElement("article");
    row.className = "role-track-item";
    const heading = document.createElement("div");
    heading.className = "role-track-heading";
    heading.append(
      createTextElement("h4", "", `${track.company_name || "未知公司"} · ${track.role_family}`),
      createTextElement("span", `role-track-status ${track.status}`,
        labels.roleTracks[track.status] || track.status),
    );
    row.append(heading, createTextElement("p", "", `${track.job_count} 个历史岗位 · ${track.closed_job_count} 个已明确关闭`));
    if (track.age_days != null) row.append(createTextElement("small", "", `最近出现 ${track.age_days} 天前`));
    container.append(row);
  });
  if (!(roleTracks?.tracks || []).length) container.append(createTextElement("p", "list-empty", "还没有公司岗位方向记录。"));
}

function renderFreshnessDashboard(dashboard) {
  state.freshness = dashboard;
  const summary = dashboard.summary || {};
  document.querySelector("#freshnessTotal").textContent = String(summary.total || 0);
  document.querySelector("#freshnessDetailDue").textContent = String(summary.detail_due || 0);
  document.querySelector("#freshnessRetryDue").textContent = String(summary.retry_due || 0);
  document.querySelector("#freshnessScanAttention").textContent = String(summary.scan_attention || 0);
  document.querySelector("#freshnessDetailMissing").textContent = String(summary.detail_missing || 0);
  document.querySelector("#freshnessClosed").textContent = String((summary.closed || 0) + (summary.archived || 0));
  document.querySelector("#freshnessScope").textContent = `${summary.live || 0} 个岗位仍在实时范围，${summary.due || 0} 个有明确下一步`;
  document.querySelector("#freshnessAsOf").textContent = `截至 ${formatFreshnessTime(summary.as_of)}`;
  renderRefreshPlan(dashboard.refresh_plan || []);
  renderFreshnessJobs();
  renderRoleTracks(dashboard.role_tracks || {});
}

async function loadFreshnessDashboard(force = false) {
  elements.freshnessError.textContent = "";
  if (state.freshness && !force) {
    renderFreshnessDashboard(state.freshness);
    return;
  }
  try {
    renderFreshnessDashboard(await fetchJson("/api/freshness"));
  } catch (error) {
    elements.freshnessError.textContent = `${error.message} 请确认本地岗位数据库可读。`;
  }
}

function jobUpdateLabel(task) {
  return {
    planned: "已生成本轮计划，尚未联网",
    running: "任务执行中",
    interrupted: "上次任务中断，等待重新启动",
    cancelled: "任务已停止",
    completed: "任务完成",
    partial: "任务部分完成",
    failed: "任务失败",
  }[task?.status] || "尚未生成更新任务";
}

function renderJobUpdate(task) {
  state.jobUpdate = task || null;
  const status = task || {};
  elements.jobUpdateStatus.textContent = jobUpdateLabel(status);
  elements.startJobUpdate.disabled = Boolean(task && ["planned", "running"].includes(status.status));
  elements.executeJobUpdate.disabled = !task || !["planned", "interrupted"].includes(status.status);
  elements.stopJobUpdate.disabled = !["planned", "running", "interrupted"].includes(status.status);
}

async function loadJobUpdate() {
  try {
    const [preview, saved] = await Promise.all([
      fetchJson("/api/job-updates/plan"), fetchJson("/api/job-updates/latest"),
    ]);
    renderJobUpdate(saved.job_update);
    renderJobUpdatePlan(saved.job_update?.plan || preview.job_update);
  } catch (error) {
    elements.jobUpdateStatus.textContent = error.message;
    elements.startJobUpdate.disabled = true;
  }
}

function renderJobUpdatePlan(plan) {
  const stages = plan?.stages || [];
  document.querySelector("#jobUpdateSummary").textContent = stages.map((stage) =>
    `${{favorites: "新增收藏", search: "搜索发现", detail_refresh: "旧 JD 复核"}[stage.source]} ${stage.budget} 条`
  ).join(" · ");
  document.querySelector("#jobUpdateCoverage").textContent =
    `本轮选中 ${plan?.coverage?.selected_detail_jobs || 0} 个待复核岗位，另有 ${plan?.coverage?.remaining_detail_jobs || 0} 个待处理。尚未访问 Boss。`;
}

async function startJobUpdate() {
  elements.startJobUpdate.disabled = true;
  try {
    const payload = await fetchJson("/api/job-updates", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    renderJobUpdate(payload.job_update);
    renderJobUpdatePlan(payload.job_update.plan);
    if (state.freshness) await loadFreshnessDashboard(true);
  } catch (error) {
    elements.jobUpdateStatus.textContent = error.message;
    elements.startJobUpdate.disabled = false;
  }
}

async function stopJobUpdate() {
  const runId = state.jobUpdate?.run_id;
  if (!runId) return;
  elements.stopJobUpdate.disabled = true;
  try {
    const payload = await fetchJson(`/api/job-updates/${encodeURIComponent(runId)}/stop`, { method: "POST" });
    renderJobUpdate(payload.job_update);
  } catch (error) {
    elements.jobUpdateStatus.textContent = error.message;
    elements.stopJobUpdate.disabled = false;
  }
}

async function executeJobUpdate() {
  const runId = state.jobUpdate?.run_id;
  if (!runId) return;
  elements.executeJobUpdate.disabled = true;
  elements.jobUpdateStatus.textContent = "正在连接 Boss 并执行本轮更新，请留在此页面观察状态…";
  try {
    const payload = await fetchJson(`/api/job-updates/${encodeURIComponent(runId)}/execute`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    renderJobUpdate(payload.job_update);
    renderJobUpdatePlan(payload.job_update.plan);
    if (state.freshness) await loadFreshnessDashboard(true);
  } catch (error) {
    elements.jobUpdateStatus.textContent = error.message;
    elements.executeJobUpdate.disabled = false;
  }
}

async function switchView(view) {
  state.view = view;
  const calibration = view === "calibration";
  const learning = view === "learning";
  const matching = view === "matching";
  const boundary = view === "boundary";
  const freshness = view === "freshness";
  const outreach = view === "outreach";
  elements.calibrationView.hidden = !calibration;
  elements.reviewView.hidden = calibration || learning || matching || boundary || freshness || outreach;
  elements.learningView.hidden = !learning;
  elements.matchingView.hidden = !matching;
  elements.boundaryView.hidden = !boundary;
  elements.freshnessView.hidden = !freshness;
  elements.outreachView.hidden = !outreach;
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    const active = button.dataset.viewTarget === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (calibration) await loadCalibrationDashboard();
  if (learning) await loadLearningDashboard();
  if (matching) await loadMatchingDashboard();
  if (boundary) await loadBoundaryDashboard();
  if (freshness) {
    await loadFreshnessDashboard();
    await loadJobUpdate();
  }
  if (outreach) await loadOutreachDashboard();
}

const outreachStatusLabels = {
  not_generated: "待生成",
  generated: "待确认",
  edited: "修改后待确认",
  partially_confirmed: "部分确认",
  ready: "可以发送",
};

const sendStatusLabels = {
  queued: "已授权 · 待发送",
  running: "发送中",
  partial: "部分送达",
  confirmed: "已送达",
  failed: "发送失败",
  paused: "已暂停 · 待人工核对",
  unknown: "结果未知 · 待人工核对",
};

const sendActionLabels = { greeting: "招呼语", image: "简历长图" };

const sendActionStatusLabels = {
  pending: "待发送",
  running: "发送中",
  confirmed: "已送达",
  failed: "失败",
  unknown: "结果未知",
};

// Only these task states may be executed (or retried) from the UI. A confirmed
// task is never re-sent, and a paused one must wait for a human.
const sendExecutableStates = ["queued", "partial", "failed", "unknown"];

function outreachApi(jobId, suffix = "", platform = state.selectedOutreachPlatform || "boss") {
  const query = new URLSearchParams({ platform });
  return `/api/outreach/${encodeURIComponent(jobId)}${suffix}?${query}`;
}

function renderOutreachQueue() {
  const jobs = state.outreach?.jobs || [];
  document.querySelector("#outreachCount").textContent = String(jobs.length);
  document.querySelector("#outreachSummary").textContent = jobs.length
    ? `${jobs.length} 个岗位可以准备，${jobs.filter((job) => job.ready).length} 个已确认`
    : "当前没有同时满足“想投、优先投、JD 有效”的岗位";
  elements.outreachJobList.replaceChildren();
  jobs.forEach((job) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `outreach-job${job.job_id === state.selectedOutreachId ? " active" : ""}`;
    const top = document.createElement("span");
    top.className = "outreach-job-top";
    top.append(
      createTextElement("strong", "", job.title || "未命名岗位"),
      createTextElement("span", `outreach-mini-status ${job.status || "not_generated"}`, outreachStatusLabels[job.status] || job.status),
    );
    button.append(
      top,
      createTextElement("span", "outreach-job-company", job.company || "公司信息缺失"),
      createTextElement("small", "", `${job.score || 0} 分${job.salary_raw ? ` · ${job.salary_raw}` : ""}`),
    );
    button.addEventListener("click", () => selectOutreach(job.job_id, job.platform));
    elements.outreachJobList.append(button);
  });
  if (!jobs.length) {
    elements.outreachJobList.append(createTextElement("p", "list-empty", "先在岗位审阅中标记“想投”，并完成匹配与 JD 更新。"));
  }
}

function applyOutreachControls(outreach) {
  const generated = Boolean(outreach.content_version);
  elements.generateOutreach.disabled = false;
  elements.generateOutreach.textContent = generated ? "重新生成投递包" : "生成投递包";
  elements.greetingEditor.disabled = !generated;
  elements.resumeEditor.disabled = !generated;
  elements.saveGreeting.disabled = !generated;
  elements.saveResume.disabled = !generated;
  elements.confirmGreeting.disabled = !generated || outreach.stale || outreach.greeting_confirmed || state.greetingDirty;
  elements.confirmResume.disabled = !generated || outreach.stale || outreach.resume_confirmed || state.resumeDirty;
  elements.confirmGreeting.textContent = state.greetingDirty
    ? "请先保存修改"
    : (outreach.greeting_confirmed ? "招呼语已确认" : "确认招呼语");
  elements.confirmResume.textContent = state.resumeDirty
    ? "请先保存并重做长图"
    : (outreach.resume_confirmed ? "长图简历已确认" : "确认长图简历");
}

function renderOutreachEvidence(container, items, emptyText) {
  container.replaceChildren();
  (items || []).forEach((item) => {
    const block = document.createElement("article");
    block.className = "outreach-evidence-item";
    block.append(createTextElement("strong", "", item.label || item.id || "匹配项"));
    if (item.candidate_evidence) {
      block.append(createTextElement("p", "candidate-evidence", item.candidate_evidence));
    }
    if (item.jd_evidence) {
      block.append(createTextElement("blockquote", "jd-evidence", item.jd_evidence));
    }
    container.append(block);
  });
  if (!container.children.length) {
    container.append(createTextElement("p", "outreach-list-empty", emptyText));
  }
}

function setCollapsibleGroup(root, open) {
  (root || document).querySelectorAll("details.outreach-collapsible").forEach((details) => {
    details.open = open;
  });
}

function resetOutreachCollapsibles() {
  document.querySelectorAll("details.outreach-collapsible").forEach((details) => {
    details.open = details.getAttribute("data-collapse-default") === "open";
  });
}

function renderOutreachChanges(summary) {
  const changes = summary?.changes || [];
  elements.outreachChanges.replaceChildren();
  changes.forEach((change) => {
    const block = document.createElement("details");
    block.className = "outreach-change-item";
    block.classList.add("outreach-collapsible");
    block.dataset.collapseGroup = "changes";
    block.dataset.collapseDefault = "closed";
    const heading = document.createElement("div");
    heading.className = "outreach-change-heading";
    heading.append(
      createTextElement("span", "change-kind", change.kind === "project_moved" ? "项目顺序" : "要点顺序"),
    );
    const summary = document.createElement("summary");
    summary.append(
      createTextElement("span", "change-summary-label", change.label || "简历条目"),
      createTextElement("strong", "change-position", `${change.from} → ${change.to}`),
    );
    const detail = document.createElement("div");
    detail.className = "outreach-change-detail";
    detail.append(
      heading,
      createTextElement("small", "change-context", change.context || ""),
      createTextElement("p", "change-reason", change.reason || "仅恢复出位置变化；旧版本未保存调整理由。"),
    );
    block.append(summary, detail);
    if (change.jd_evidence) {
      block.append(createTextElement("blockquote", "jd-evidence", change.jd_evidence));
    }
    elements.outreachChanges.append(block);
  });
  if (!changes.length) {
    elements.outreachChanges.append(createTextElement("p", "outreach-list-empty", "本次没有改变项目或要点顺序，母版原文保持不变。"));
  }
  const scope = summary?.scope || {};
  elements.outreachScope.textContent = scope.projects || scope.bullets
    ? `检查 ${scope.projects || 0} 个项目、${scope.bullets || 0} 条要点 · 改写 ${scope.rewritten || 0} 处`
    : "生成后显示简历调整范围";
  document.querySelector("#outreachChangeCount").textContent = `${changes.length} 项`;
}

function setOutreachMobileTab(tab) {
  document.querySelectorAll("[data-outreach-tab]").forEach((button) => {
    const active = button.dataset.outreachTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-outreach-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.outreachPanel === tab);
  });
}

function renderOutreachDetail(outreach) {
  state.outreachDetail = outreach;
  state.greetingDirty = false;
  state.resumeDirty = false;
  elements.outreachEmpty.hidden = true;
  elements.outreachContent.hidden = false;
  elements.outreachError.textContent = "";
  document.querySelector("#outreachCompany").textContent = outreach.company || "公司信息缺失";
  document.querySelector("#outreachEditorHeading").textContent = outreach.title || "未命名岗位";
  document.querySelector("#outreachJobMeta").textContent = `${outreach.score || 0} 分${outreach.salary_raw ? ` · ${outreach.salary_raw}` : ""}`;
  document.querySelector("#outreachMatchScore").textContent = String(outreach.score || 0);
  elements.outreachJdText.textContent = outreach.jd_text || "JD 正文缺失";
  document.querySelector("#outreachJdLength").textContent = `${(outreach.jd_text || "").length} 字`;
  document.querySelector("#outreachJdSummary").textContent = `${(outreach.jd_text || "").split(/\r?\n/).filter(Boolean).slice(0, 3).join(" · ") || "暂无 JD 摘要"}`;
  renderOutreachEvidence(elements.outreachStrengths, outreach.strengths, "当前匹配分析没有可展示的优势证据。");
  renderOutreachEvidence(
    elements.outreachGaps,
    [...(outreach.gaps || []), ...(outreach.uncertainties || [])],
    "当前没有识别到明确缺口；仍需结合完整 JD 人工判断。",
  );
  document.querySelector("#outreachStrengthCount").textContent = `${(outreach.strengths || []).length} 条`;
  document.querySelector("#outreachGapCount").textContent = `${(outreach.gaps || []).length + (outreach.uncertainties || []).length} 条`;
  renderOutreachChanges(outreach.change_summary);
  resetOutreachCollapsibles();
  setOutreachMobileTab("jd");
  const status = outreach.stale ? "内容已失效" : (outreachStatusLabels[outreach.status] || outreach.status);
  document.querySelector("#outreachStatus").textContent = status;
  document.querySelector("#outreachStatus").className = `outreach-status ${outreach.stale ? "stale" : outreach.status}`;
  document.querySelector("#outreachVersion").textContent = outreach.content_version
    ? `内容版本 v${outreach.content_version} · ${outreach.ready ? "文字和长图均已确认" : "尚未发送"}`
    : "尚未生成投递包";

  const generated = Boolean(outreach.content_version);
  elements.greetingEditor.value = outreach.greeting_text || "";
  elements.resumeEditor.value = outreach.tailored_md || "";
  const greeting = elements.greetingEditor.value.trim();
  const templatePhrases = ["贵司", "本人", "我对岗位很感兴趣", "和我的经历高度匹配", "希望进一步沟通", "期待进一步沟通", "想进一步沟通", "简历已投，请查收"];
  const greetingHasIdentity = /我目前|我现在|我是|我做|我负责|目前做/.test(greeting.slice(0, 80));
  const greetingPasses = greetingHasIdentity && !templatePhrases.some((phrase) => greeting.includes(phrase));
  const greetingQuality = document.querySelector("#greetingQuality");
  greetingQuality.textContent = !generated
    ? "生成后检查是否先介绍自己、回应具体 JD，并自然提及相关作品集。"
    : (greetingPasses
      ? "结构自然：有自我定位和岗位回应，可以继续人工润色。"
      : "这版仍有模板感：建议重新生成，或补充自我定位与具体岗位回应。");
  greetingQuality.className = `greeting-quality ${generated && greetingPasses ? "pass" : "attention"}`;
  applyOutreachControls(outreach);
  document.querySelector("#greetingCount").textContent = `${elements.greetingEditor.value.length} 字`;

  const artifactVersion = outreach.image_hash || outreach.content_version || 0;
  if (outreach.resume_assets && outreach.resume_assets.length) {
    const current = outreach.resume_assets.find(
      (asset) => asset.id === (outreach.resume_asset_id || state.pendingAssetId),
    );
    elements.outreachResumeSource.textContent = outreach.resume_asset_id
      ? `发送的简历：成品「${current ? current.label : outreach.resume_asset_id}」（你做好的 Word/长图副本，逐岗确认后发送）`
      : "发送的简历：尚未选定成品简历，生成时将按 JD 关键词自动推荐一份";
  } else {
    elements.outreachResumeSource.textContent = outreach.resume_source
      ? `发送的简历：母版 ${outreach.resume_source}（全部岗位共用 · 仅按 JD 重排既有内容，不换简历、不新增内容）`
      : "";
  }
  renderAssetPicker(outreach);
  if (generated && outreach.image_path) {
    elements.outreachResumeImage.src = `${outreachApi(outreach.job_id, "/artifact/image", outreach.platform)}&v=${encodeURIComponent(artifactVersion)}`;
    elements.outreachResumeImage.hidden = false;
    elements.outreachImageEmpty.hidden = true;
    document.querySelector("#resumeImageSize").textContent = `${outreach.image_width} × ${outreach.image_height}px`;
    elements.downloadOutreachDocx.href = outreachApi(outreach.job_id, "/artifact/docx", outreach.platform);
    elements.downloadOutreachDocx.hidden = false;
  } else {
    elements.outreachResumeImage.removeAttribute("src");
    elements.outreachResumeImage.hidden = true;
    elements.outreachImageEmpty.hidden = false;
    document.querySelector("#resumeImageSize").textContent = "";
    elements.downloadOutreachDocx.hidden = true;
  }
  renderSendGate(outreach);
}

function renderAssetPicker(outreach) {
  const assets = (outreach && outreach.resume_assets) || [];
  if (!assets.length) {
    elements.outreachAssetSection.hidden = true;
    elements.outreachAssetOptions.replaceChildren();
    elements.outreachAssetCurrent.textContent = "";
    return;
  }
  elements.outreachAssetSection.hidden = false;
  const savedId = outreach.resume_asset_id || "";
  const effectiveId = savedId || state.pendingAssetId
    || outreach.suggested_asset_id || assets[0].id;
  const current = assets.find((asset) => asset.id === effectiveId);
  elements.outreachAssetCurrent.textContent = savedId
    ? `当前：${current ? current.label : savedId}`
    : "生成时自动推荐";
  elements.outreachAssetOptions.replaceChildren();
  assets.forEach((asset) => {
    const checked = asset.id === effectiveId;
    const option = document.createElement("label");
    option.className = `asset-option${checked ? " checked" : ""}`;
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "outreachAsset";
    input.value = asset.id;
    input.checked = checked;
    input.addEventListener("change", () => chooseOutreachAsset(asset.id));
    option.append(input, createTextElement("span", "", asset.label));
    if (asset.suggested) option.append(createTextElement("span", "asset-tag", "推荐"));
    elements.outreachAssetOptions.append(option);
  });
}

async function chooseOutreachAsset(assetId) {
  if (!state.selectedOutreachId) return;
  const jobId = state.selectedOutreachId;
  const platform = state.selectedOutreachPlatform || "boss";
  const outreach = state.outreachDetail;
  if ((outreach && outreach.resume_asset_id) === assetId) {
    renderAssetPicker(outreach);
    return;
  }
  if (!outreach || !outreach.content_version) {
    // 还没生成投递包：只记住选择，点「生成投递包」时带上。
    state.pendingAssetId = assetId;
    renderAssetPicker(outreach);
    return;
  }
  if (outreach.resume_confirmed
    && !window.confirm("切换成品简历会作废当前长图确认和已发出的发送授权，确定切换？")) {
    renderAssetPicker(outreach);
    return;
  }
  setOutreachBusy("正在切换成品简历并生成本岗副本...");
  try {
    const payload = await fetchJson(outreachApi(jobId, "/asset", platform), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_id: assetId }),
    });
    state.pendingAssetId = null;
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      renderOutreachDetail(payload.outreach);
    }
    state.outreach = null;
    await loadOutreachDashboard(true);
  } catch (error) {
    elements.outreachError.textContent = error.message;
    renderAssetPicker(state.outreachDetail);
  } finally {
    setOutreachBusy("");
  }
}

async function selectOutreach(jobId, platform = "boss") {
  state.selectedOutreachId = jobId;
  state.selectedOutreachPlatform = platform;
  state.sendTask = null;
  state.sendBusy = false;
  state.sendRequest += 1;
  state.pendingAssetId = null;
  renderOutreachQueue();
  const request = ++state.outreachRequest;
  elements.outreachEmpty.hidden = false;
  elements.outreachEmpty.querySelector("strong").textContent = "正在读取投递准备";
  elements.outreachContent.hidden = true;
  try {
    const payload = await fetchJson(outreachApi(jobId, "", platform));
    if (request !== state.outreachRequest) return;
    renderOutreachDetail(payload.outreach);
    await loadSendTask(jobId, platform);
    await loadProgress(jobId, platform);
  } catch (error) {
    if (request !== state.outreachRequest) return;
    elements.outreachEmpty.querySelector("strong").textContent = "投递准备读取失败";
    elements.outreachEmpty.querySelector("span").textContent = error.message;
  }
}

async function loadOutreachDashboard(force = false) {
  if (!state.outreach || force) state.outreach = await fetchJson("/api/outreach");
  renderOutreachQueue();
  const jobs = state.outreach.jobs || [];
  if (jobs.length && !state.selectedOutreachId) {
    await selectOutreach(jobs[0].job_id, jobs[0].platform);
  }
}

function setOutreachBusy(message = "") {
  const buttons = [elements.generateOutreach, elements.saveGreeting, elements.saveResume, elements.confirmGreeting, elements.confirmResume, elements.authorizeSend, elements.executeSend];
  if (message) {
    buttons.forEach((button) => { button.disabled = true; });
    elements.outreachJobList.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    elements.outreachError.textContent = message;
    return;
  }
  elements.outreachJobList.querySelectorAll("button").forEach((button) => { button.disabled = false; });
  if (state.outreachDetail) applyOutreachControls(state.outreachDetail);
  renderSendGate(state.outreachDetail);
}

async function generateOutreachPackage() {
  if (!state.selectedOutreachId) return;
  const jobId = state.selectedOutreachId;
  const platform = state.selectedOutreachPlatform;
  setOutreachBusy("AI 正在定制招呼语和简历，并生成 Word 与长图，请稍候...");
  elements.generateOutreach.textContent = "生成中";
  try {
    const payload = await fetchJson(outreachApi(jobId, "/generate", platform), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_id: state.pendingAssetId || "" }),
    });
    state.pendingAssetId = null;
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      renderOutreachDetail(payload.outreach);
    }
    state.outreach = null;
    await loadOutreachDashboard(true);
  } catch (error) {
    elements.outreachError.textContent = `${error.message} 没有发送任何消息。`;
  } finally {
    setOutreachBusy("");
  }
}

async function saveOutreachPart(kind) {
  if (!state.selectedOutreachId) return;
  const jobId = state.selectedOutreachId;
  const platform = state.selectedOutreachPlatform;
  const isGreeting = kind === "greeting";
  setOutreachBusy(isGreeting ? "正在保存招呼语..." : "正在重做 Word 和长图...");
  try {
    const body = isGreeting
      ? { text: elements.greetingEditor.value }
      : { markdown: elements.resumeEditor.value };
    const payload = await fetchJson(outreachApi(jobId, `/${kind}`, platform), {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      renderOutreachDetail(payload.outreach);
    }
    state.outreach = null;
    await loadOutreachDashboard(true);
  } catch (error) {
    elements.outreachError.textContent = error.message;
  } finally {
    setOutreachBusy("");
  }
}

async function confirmOutreachPart(kind) {
  if (!state.selectedOutreachId) return;
  const jobId = state.selectedOutreachId;
  const platform = state.selectedOutreachPlatform;
  setOutreachBusy("正在保存确认状态...");
  try {
    const payload = await fetchJson(outreachApi(jobId, "/confirm", platform), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    });
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      renderOutreachDetail(payload.outreach);
    }
    state.outreach = null;
    await loadOutreachDashboard(true);
  } catch (error) {
    elements.outreachError.textContent = error.message;
  } finally {
    setOutreachBusy("");
  }
}

async function loadSendTask(jobId, platform) {
  const request = ++state.sendRequest;
  try {
    const payload = await fetchJson(outreachApi(jobId, "/send", platform));
    if (request !== state.sendRequest) return;
    state.sendTask = payload.send || null;
  } catch (error) {
    if (request !== state.sendRequest) return;
    state.sendTask = null;
  }
  renderSendGate(state.outreachDetail);
}

async function loadProgress(jobId, platform) {
  const request = ++state.progressRequest;
  elements.outreachProgressTimeline.replaceChildren();
  elements.outreachProgressCount.textContent = "";
  try {
    const payload = await fetchJson(outreachApi(jobId, "/progress", platform));
    if (request !== state.progressRequest) return;
    renderProgress(jobId, platform, payload.progress || {});
  } catch (error) {
    if (request !== state.progressRequest) return;
    const item = document.createElement("li");
    item.className = "progress-empty";
    item.textContent = `沟通进展读取失败：${error.message}`;
    elements.outreachProgressTimeline.append(item);
  }
}

function renderProgress(jobId, platform, data) {
  const events = data.events || [];
  elements.outreachProgressCount.textContent = events.length
    ? `${data.pending_count ? `${data.pending_count} 条待确认 · ` : ""}共 ${data.total} 条`
    : "暂无记录";
  elements.outreachProgressSection.hidden = !state.selectedOutreachId;
  const outcome = data.outcome;
  if (outcome) {
    const head = document.createElement("li");
    head.className = `progress-outcome ${outcome.event_type}`;
    head.textContent = `当前结果：${outcome.event_label}${outcome.note ? ` · ${outcome.note}` : ""}`;
    elements.outreachProgressTimeline.append(head);
  }
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "progress-empty";
    item.textContent = "还没有沟通记录。发送招呼语后在这里登记，后续进展自己更新。";
    elements.outreachProgressTimeline.append(item);
    return;
  }
  for (const event of events) {
    const item = document.createElement("li");
    item.className = `progress-event ${event.kind}${event.pending ? " pending" : ""}`;
    const meta = document.createElement("div");
    meta.className = "progress-event-head";
    const label = document.createElement("strong");
    label.textContent = event.event_label;
    const time = document.createElement("span");
    time.textContent = `${event.occurred_at.replace("T", " ").slice(0, 16)} · ${event.source === "manual" ? "人工登记" : "AI 推断"}`;
    meta.append(label, time);
    const body = document.createElement("div");
    body.className = "progress-event-body";
    body.textContent = event.note || "";
    item.append(meta);
    if (event.note) item.append(body);
    if (event.evidence) {
      const evidence = document.createElement("div");
      evidence.className = "progress-event-evidence";
      evidence.textContent = `证据：${event.evidence}`;
      item.append(evidence);
    }
    if (event.pending) {
      const actions = document.createElement("div");
      actions.className = "progress-event-actions";
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.className = "secondary-action";
      confirmButton.textContent = "确认无误";
      confirmButton.addEventListener("click", async () => {
        confirmButton.disabled = true;
        try {
          await fetchJson(outreachApi(jobId, "/progress/confirm", platform), {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ event_id: event.id }),
          });
          await loadProgress(jobId, platform);
        } catch (error) {
          elements.outreachError.textContent = error.message;
          confirmButton.disabled = false;
        }
      });
      actions.append(confirmButton);
      item.append(actions);
    }
    elements.outreachProgressTimeline.append(item);
  }
}

function renderSendGate(outreach) {
  const ready = Boolean(outreach && outreach.content_version && outreach.ready && !outreach.stale);
  const version = Number((outreach && outreach.content_version) || 0);
  const task = state.sendTask;
  const versionMatches = Boolean(task) && Number(task.content_version) === version;
  const status = versionMatches ? task.status : null;

  let badgeClass = "idle";
  let badgeText = "未授权";
  if (!outreach || !outreach.content_version) {
    badgeText = "先完成双确认";
  } else if (task && !versionMatches) {
    badgeClass = "stale";
    badgeText = "内容已更新 · 需重新授权";
  } else if (!ready) {
    badgeText = "先完成双确认";
  } else if (versionMatches) {
    badgeClass = status;
    badgeText = sendStatusLabels[status] || status;
  }
  elements.outreachSendBadge.textContent = badgeText;
  elements.outreachSendBadge.className = `send-badge ${badgeClass}`;

  const canAuthorize = ready && !versionMatches && !state.sendBusy;
  const canExecute = ready && versionMatches && sendExecutableStates.includes(status) && !state.sendBusy;
  elements.authorizeSend.disabled = !canAuthorize;
  elements.executeSend.disabled = !canExecute;
  elements.authorizeSend.textContent = task ? "重新授权本岗" : "授权本岗";
  elements.executeSend.textContent = status === "partial"
    ? "继续发送剩余内容"
    : (status === "failed" || status === "unknown" ? "重试发送" : "确认发送");

  if (versionMatches && task) {
    elements.outreachSendTask.hidden = false;
    elements.outreachSendStatus.textContent = sendStatusLabels[status] || status;
    elements.outreachSendActions.replaceChildren();
    (task.actions || []).forEach((action) => {
      const row = document.createElement("li");
      row.className = `send-action ${action.status}`;
      row.append(
        createTextElement("span", "", sendActionLabels[action.kind] || action.kind),
        createTextElement("strong", "", sendActionStatusLabels[action.status] || action.status),
      );
      if (action.evidence) row.append(createTextElement("small", "", `证据：${action.evidence}`));
      if (action.error) row.append(createTextElement("small", "send-action-error", action.error));
      elements.outreachSendActions.append(row);
    });
  } else {
    elements.outreachSendTask.hidden = true;
    elements.outreachSendActions.replaceChildren();
    elements.outreachSendStatus.textContent = "";
  }
}

async function authorizeSendAction() {
  if (!state.selectedOutreachId || state.sendBusy) return;
  const jobId = state.selectedOutreachId;
  const platform = state.selectedOutreachPlatform || "boss";
  state.sendBusy = true;
  renderSendGate(state.outreachDetail);
  elements.outreachError.textContent = "正在冻结当前版本的招呼语与长图...";
  try {
    const payload = await fetchJson(outreachApi(jobId, "/authorize", platform), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      state.sendTask = payload.send || null;
      elements.outreachError.textContent = "";
    }
  } catch (error) {
    elements.outreachError.textContent = `${error.message} 没有发送任何消息。`;
  } finally {
    state.sendBusy = false;
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      renderSendGate(state.outreachDetail);
    }
  }
}

async function executeSendAction() {
  if (!state.selectedOutreachId || state.sendBusy) return;
  const task = state.sendTask;
  if (!task || !task.task_id) return;
  const jobId = state.selectedOutreachId;
  const platform = state.selectedOutreachPlatform || "boss";
  const title = (state.outreachDetail && state.outreachDetail.title) || "该岗位";
  if (!window.confirm(`确认向「${title}」真实发送招呼语和简历长图？\n\n这一步会立即向招聘方发出消息，且不可撤回。`)) {
    return;
  }
  state.sendBusy = true;
  renderSendGate(state.outreachDetail);
  elements.outreachError.textContent = "正在发送招呼语与简历长图，请勿重复点击...";
  try {
    const payload = await fetchJson(`/api/outreach/send/${encodeURIComponent(task.task_id)}/execute`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      state.sendTask = payload.send || null;
      elements.outreachError.textContent = "";
    }
  } catch (error) {
    elements.outreachError.textContent = error.message;
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      await loadSendTask(jobId, platform);
    }
  } finally {
    state.sendBusy = false;
    if (state.selectedOutreachId === jobId && state.selectedOutreachPlatform === platform) {
      renderSendGate(state.outreachDetail);
    }
  }
}

function renderQueue() {
  const jobs = filteredJobs();
  elements.visibleCount.textContent = String(jobs.length);
  elements.list.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("p");
    empty.className = "list-empty";
    empty.textContent = "这个筛选下没有岗位。";
    elements.list.append(empty);
    return;
  }

  jobs.forEach((job) => {
    const button = document.createElement("button");
    button.type = "button";
    const selected = job.job_id === state.selectedId
      && (!state.selectedPlatform || job.platform === state.selectedPlatform);
    button.className = `job-item${job.reviewed ? " reviewed" : ""}${selected ? " active" : ""}`;
    button.dataset.jobId = job.job_id;
    button.dataset.platform = job.platform;

    const top = document.createElement("span");
    top.className = "job-item-top";
    const bucket = document.createElement("span");
    bucket.className = "mini-bucket";
    bucket.textContent = labels.buckets[job.sample_bucket] || job.sample_bucket;
    const mark = document.createElement("span");
    mark.className = `interest-mark ${job.interest || ""}`;
    mark.setAttribute("aria-label", job.reviewed ? `已决定：${job.interest}` : "未决定");
    top.append(bucket, mark);

    const title = document.createElement("span");
    title.className = "job-item-title";
    title.textContent = job.title || "未命名岗位";
    const company = document.createElement("span");
    company.className = "job-item-company";
    company.textContent = job.company || "公司信息缺失";
    const meta = document.createElement("span");
    meta.className = "job-item-meta";
    const salary = document.createElement("span");
    salary.textContent = job.salary_raw || "薪资未知";
    const track = document.createElement("span");
    track.textContent = labels.tracks[job.track] || job.track || "其他";
    const tier = document.createElement("span");
    tier.className = `queue-tier ${job.fit_tier || ""}`;
    tier.textContent = labels.tiers[job.fit_tier] || "待判断";
    meta.append(salary, track, tier);
    button.append(top, title, company, meta);
    button.addEventListener("click", () => selectJob(
      job.job_id,
      DEFAULT_SAMPLE_NAME,
      job.platform,
    ));
    elements.list.append(button);
  });
}

function addMeta(label, value) {
  const wrapper = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value || "未提供";
  wrapper.append(term, description);
  document.querySelector("#jobMeta").append(wrapper);
}

function setDecisionForm(decision) {
  elements.decisionForm.reset();
  elements.formError.textContent = "";
  elements.saveState.textContent = decision ? "已保存" : "";
  if (decision) {
    const interest = elements.decisionForm.querySelector(`[name="interest"][value="${decision.interest}"]`);
    if (interest) interest.checked = true;
    elements.decisionForm.elements.rejection_reason.value = decision.rejection_reason || "";
    elements.decisionForm.elements.note.value = decision.note || "";
    elements.learningIntent.checked = Boolean(
      decision.learning_intent ?? decision.learning_suggested,
    );
  }
  updateSaveAction();
}

function setCorrectionForm(corrections) {
  elements.correctionForm.reset();
  elements.correctionError.textContent = "";
  elements.correctionState.textContent = "";
  if (!corrections?.length) return;
  const latest = corrections[corrections.length - 1];
  elements.correctionForm.elements.category.value = latest.category;
  elements.correctionForm.elements.note.value = latest.note;
  elements.correctionState.textContent = `已记录 ${corrections.length} 条`;
}

function renderEvidence(listId, values) {
  const list = document.querySelector(`#${listId}`);
  list.replaceChildren();
  const items = values?.length ? values : ["JD 信息不足，暂不判断"];
  items.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    list.append(item);
  });
}

function renderSystemAnalysis(job) {
  const analysis = job.system_analysis || {};
  analysisFields.forEach(([key, valueId, evidenceId]) => {
    const judgment = analysis[key] || { value: "信息不足", tone: "muted", evidence: [] };
    const value = document.querySelector(`#${valueId}`);
    value.className = `analysis-value ${judgment.tone || "muted"}`;
    value.textContent = judgment.value || "信息不足";
    renderEvidence(evidenceId, judgment.evidence);
  });
  document.querySelector("#analysisVersion").textContent = job.analyzed_at
    ? `规则分析 ${String(job.analyzed_at).slice(0, 10)}`
    : "";

  const riskLine = document.querySelector("#riskLine");
  const risks = document.querySelector("#riskChips");
  risks.replaceChildren();
  (job.risks || []).forEach((risk) => {
    const chip = document.createElement("span");
    chip.textContent = labels.risks[risk] || risk;
    risks.append(chip);
  });
  riskLine.hidden = !job.risks?.length;
}

function renderDetail(job) {
  elements.detailEmpty.hidden = true;
  elements.detailContent.hidden = false;
  document.querySelector("#sampleBucket").textContent = labels.buckets[job.sample_bucket] || job.sample_bucket;
  document.querySelector("#fitTier").textContent = labels.tiers[job.fit_tier] || job.fit_tier;
  document.querySelector("#fitTier").className = `tier ${job.fit_tier || ""}`;
  document.querySelector("#jobTitle").textContent = job.title || "未命名岗位";
  document.querySelector("#companyLine").textContent = job.company || "公司信息缺失";
  const sourceLink = document.querySelector("#sourceLink");
  sourceLink.href = job.source_url || "#";
  sourceLink.hidden = !job.source_url;

  const meta = document.querySelector("#jobMeta");
  meta.replaceChildren();
  addMeta("薪资", job.salary_raw);
  addMeta("地点", job.location);
  addMeta("经验", job.experience_raw);
  addMeta("学历", job.education_raw);
  addMeta("招聘者", [job.recruiter_name, job.recruiter_role].filter(Boolean).join(" · "));
  addMeta("技能", (job.skills || []).join("、"));

  renderCompanyProfile(job);
  renderSystemAnalysis(job);
  const jd = job.jd_text || job.detail_text || "没有采集到 JD 正文";
  document.querySelector("#jdText").textContent = jd;
  document.querySelector("#jdLength").textContent = `${jd.length} 字`;
  setDecisionForm(job.decision);
  setCorrectionForm(job.corrections);
}

function jobApiUrl(jobId, sampleName = DEFAULT_SAMPLE_NAME, suffix = "", platform = "") {
  const query = new URLSearchParams({ sample_name: sampleName });
  if (platform) query.set("platform", platform);
  return `/api/jobs/${encodeURIComponent(jobId)}${suffix}?${query}`;
}

async function selectJob(jobId, sampleName = DEFAULT_SAMPLE_NAME, platform = "") {
  state.selectedId = jobId;
  state.selectedPlatform = platform || null;
  state.selectedSampleName = sampleName;
  renderQueue();
  const requestId = ++state.detailRequest;
  elements.detailEmpty.hidden = false;
  elements.detailEmpty.querySelector("strong").textContent = "正在读取完整 JD";
  elements.detailEmpty.querySelector("span").textContent = "数据来自本地数据库。";
  elements.detailContent.hidden = true;
  try {
    const payload = await fetchJson(jobApiUrl(jobId, sampleName, "", platform));
    if (requestId !== state.detailRequest) return;
    state.selectedPlatform = payload.job.platform || platform || null;
    renderDetail(payload.job);
  } catch (error) {
    if (requestId !== state.detailRequest) return;
    elements.detailEmpty.querySelector("strong").textContent = "这条岗位读取失败";
    elements.detailEmpty.querySelector("span").textContent = error.message;
  }
}

async function loadJobs(selectFirst = true) {
  const payload = await fetchJson("/api/jobs");
  state.jobs = payload.jobs;
  state.stats = payload.stats;
  renderProgress();
  renderQueue();
  if (selectFirst && !state.selectedId && state.jobs.length) {
    await selectJob(
      state.jobs[0].job_id,
      DEFAULT_SAMPLE_NAME,
      state.jobs[0].platform,
    );
  }
}

function decisionPayload() {
  const data = new FormData(elements.decisionForm);
  return {
    interest: data.get("interest"),
    rejection_reason: data.get("rejection_reason"),
    note: data.get("note"),
    learning_intent: data.get("learning_intent") === "on",
  };
}

async function saveDecision(event) {
  event.preventDefault();
  if (!state.selectedId || !elements.decisionForm.reportValidity()) return;
  elements.formError.textContent = "";
  elements.saveButton.disabled = true;
  elements.saveButton.textContent = "正在保存...";
  try {
    const selectedSampleName = state.selectedSampleName || DEFAULT_SAMPLE_NAME;
    await fetchJson(jobApiUrl(
      state.selectedId,
      selectedSampleName,
      "",
      state.selectedPlatform,
    ), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(decisionPayload()),
    });
    state.learning = null;
    state.boundary = null;
    const currentIndex = state.jobs.findIndex(
      (job) => job.job_id === state.selectedId && job.platform === state.selectedPlatform,
    );
    await loadJobs(false);
    if (selectedSampleName !== DEFAULT_SAMPLE_NAME) {
      await selectJob(state.selectedId, selectedSampleName, state.selectedPlatform);
      return;
    }
    const next = state.jobs.slice(currentIndex + 1).find((job) => !job.reviewed)
      || state.jobs.find((job) => !job.reviewed);
    if (next) await selectJob(next.job_id, DEFAULT_SAMPLE_NAME, next.platform);
    else await selectJob(state.selectedId, DEFAULT_SAMPLE_NAME, state.selectedPlatform);
  } catch (error) {
    elements.formError.textContent = error.message;
  } finally {
    elements.saveButton.disabled = false;
    updateSaveAction();
  }
}

async function saveCorrection(event) {
  event.preventDefault();
  if (!state.selectedId || !elements.correctionForm.reportValidity()) return;
  const data = new FormData(elements.correctionForm);
  elements.correctionError.textContent = "";
  elements.correctionState.textContent = "";
  elements.correctionButton.disabled = true;
  elements.correctionButton.textContent = "提交中...";
  try {
    await fetchJson(jobApiUrl(
      state.selectedId,
      state.selectedSampleName || DEFAULT_SAMPLE_NAME,
      "/correction",
      state.selectedPlatform,
    ), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: data.get("category"), note: data.get("note") }),
    });
    state.boundary = null;
    elements.correctionState.textContent = "已记录";
  } catch (error) {
    elements.correctionError.textContent = error.message;
  } finally {
    elements.correctionButton.disabled = false;
    elements.correctionButton.textContent = "提交纠错";
  }
}

function updateSaveAction() {
  const rejected = elements.decisionForm.querySelector('[name="interest"][value="reject"]')?.checked;
  elements.saveButton.textContent = rejected ? "放弃并看下一条" : "保存并看下一条";
}

document.querySelector("#filters").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  state.filter = button.dataset.filter;
  document.querySelectorAll("#filters button").forEach((item) => item.classList.toggle("active", item === button));
  renderQueue();
});

document.querySelector(".view-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view-target]");
  if (button) switchView(button.dataset.viewTarget);
});

document.querySelector(".freshness-metrics").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-freshness-filter]");
  if (!button) return;
  state.freshnessFilter = button.dataset.freshnessFilter;
  document.querySelectorAll("[data-freshness-filter]").forEach((item) => {
    item.classList.toggle("active", item === button);
  });
  renderFreshnessJobs();
});

elements.noteSignals.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-job-id]");
  if (!button) return;
  switchView("review").then(() => selectJob(
    button.dataset.jobId,
    button.dataset.sampleName || DEFAULT_SAMPLE_NAME,
    button.dataset.platform,
  ));
});

elements.boundaryGroups.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-job-id]");
  if (!button) return;
  switchView("review").then(() => selectJob(
    button.dataset.jobId,
    button.dataset.sampleName || DEFAULT_SAMPLE_NAME,
    button.dataset.platform,
  ));
});

document.querySelector(".match-metrics").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-match-filter]");
  if (!button) return;
  state.matchFilter = button.dataset.matchFilter;
  document.querySelectorAll("[data-match-filter]").forEach((item) => item.classList.toggle("active", item === button));
  renderMatchQueue();
});

document.querySelector("#openReviewJob").addEventListener("click", (event) => {
  const button = event.currentTarget;
  switchView("review").then(() => selectJob(
    button.dataset.jobId,
    button.dataset.sampleName || "__reviewed__",
    button.dataset.platform,
  ));
});

elements.decisionForm.addEventListener("submit", saveDecision);
elements.correctionForm.addEventListener("submit", saveCorrection);
elements.preferenceForm.addEventListener("submit", savePreferences);
elements.refreshStaleMatchesButton?.addEventListener("click", refreshStaleMatches);
elements.startJobUpdate?.addEventListener("click", startJobUpdate);
elements.executeJobUpdate?.addEventListener("click", executeJobUpdate);
elements.stopJobUpdate?.addEventListener("click", stopJobUpdate);
document.querySelector("#reloadJobUpdate")?.addEventListener("click", loadJobUpdate);
elements.preferenceTrackOptions.addEventListener("change", syncPreferenceTrackState);
document.querySelectorAll('[name="interest"]').forEach((input) => input.addEventListener("change", updateSaveAction));
document.querySelector("#quickReject").addEventListener("click", () => {
  const reject = elements.decisionForm.querySelector('[name="interest"][value="reject"]');
  reject.checked = true;
  updateSaveAction();
  saveDecision({ preventDefault() {} });
});

elements.resumeFile.addEventListener("change", () => {
  const file = elements.resumeFile.files?.[0];
  document.querySelector("#resumeFileName").textContent = file ? file.name : "尚未选择文件";
  elements.calibrateResumeButton.disabled = state.collectionRunning || !file;
  state.calibratedResumeText = "";
  state.calibratedFilename = "";
  elements.collectResumeJobsButton.disabled = true;
  elements.calibrationStatus.textContent = "";
});
elements.calibrateResumeButton.addEventListener("click", calibrateSelectedResume);
elements.collectResumeJobsButton.addEventListener("click", collectResumeJobs);
elements.generateOutreach.addEventListener("click", generateOutreachPackage);
elements.saveGreeting.addEventListener("click", () => saveOutreachPart("greeting"));
elements.saveResume.addEventListener("click", () => saveOutreachPart("resume"));
elements.confirmGreeting.addEventListener("click", () => confirmOutreachPart("greeting"));
elements.confirmResume.addEventListener("click", () => confirmOutreachPart("resume"));
elements.authorizeSend.addEventListener("click", authorizeSendAction);
elements.executeSend.addEventListener("click", executeSendAction);
elements.outreachProgressForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const jobId = state.selectedOutreachId;
  const platform = state.selectedOutreachPlatform || "boss";
  if (!jobId) return;
  const submitButton = elements.outreachProgressForm.querySelector("button");
  submitButton.disabled = true;
  try {
    await fetchJson(outreachApi(jobId, "/progress", platform), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        event_type: elements.progressEventType.value,
        note: elements.progressNote.value.trim(),
        evidence: elements.progressEvidence.value.trim(),
      }),
    });
    elements.progressNote.value = "";
    elements.progressEvidence.value = "";
    await loadProgress(jobId, platform);
  } catch (error) {
    elements.outreachError.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});
elements.outreachMobileTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-outreach-tab]");
  if (button) setOutreachMobileTab(button.dataset.outreachTab);
});
elements.outreachCollapseToolbar.addEventListener("click", (event) => {
  const action = event.target.closest("[data-collapse-action]")?.dataset.collapseAction;
  if (!action) return;
  setCollapsibleGroup(elements.outreachContent, action === "expand");
});
elements.greetingEditor.addEventListener("input", () => {
  state.greetingDirty = true;
  document.querySelector("#greetingCount").textContent = `${elements.greetingEditor.value.length} 字`;
  elements.confirmGreeting.disabled = true;
  elements.confirmGreeting.textContent = "请先保存修改";
});
elements.resumeEditor.addEventListener("input", () => {
  state.resumeDirty = true;
  elements.confirmResume.disabled = true;
  elements.confirmResume.textContent = "请先保存并重做长图";
});

loadCalibrationDashboard().catch(() => {});
pollCollectionStatus().catch(showCollectionPollingError);

loadJobs().catch((error) => {
  elements.list.replaceChildren();
  const message = document.createElement("p");
  message.className = "list-empty";
  message.textContent = `${error.message} 请确认本地数据库存在后刷新页面。`;
  elements.list.append(message);
});

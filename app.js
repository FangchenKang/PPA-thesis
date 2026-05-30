const app = document.querySelector("#app");
const searchPanel = document.querySelector("#searchPanel");
const searchInput = document.querySelector("#globalSearch");

const state = {
  journals: [],
  issues: [],
  articleRows: null,
  articleSchema: null,
  articleIndex: null,
  articleDictionaries: null,
  articleLoadPromise: null,
  issueMap: null,
};

const DATA_BASE = "./data/";

async function fetchJson(name) {
  const response = await fetch(`${DATA_BASE}${name}`);
  if (!response.ok) {
    throw new Error(`无法读取 ${name}`);
  }
  return response.json();
}

async function loadBaseData() {
  const [journalsPayload, issuesPayload] = await Promise.all([
    fetchJson("journals.json"),
    fetchJson("issues.json"),
  ]);
  state.journals = journalsPayload.journals || [];
  state.issues = issuesPayload.issues || [];
}

async function loadArticles() {
  if (state.articleRows) return state.articleRows;
  if (!state.articleLoadPromise) {
    state.articleLoadPromise = fetchJson("articles.json").then((payload) => {
      state.articleSchema = payload.schema || [];
      state.articleIndex = Object.fromEntries(state.articleSchema.map((key, index) => [key, index]));
      state.articleDictionaries = payload.dictionaries || {};
      state.articleRows = payload.articles || [];
      state.issueMap = new Map();
      for (const row of state.articleRows) {
        const key = `${cell(row, "journal_key")}::${cell(row, "period")}`;
        if (!state.issueMap.has(key)) state.issueMap.set(key, []);
        state.issueMap.get(key).push(row);
      }
      return state.articleRows;
    });
  }
  return state.articleLoadPromise;
}

function cell(row, key) {
  const raw = row[state.articleIndex[key]];
  const dictionary = state.articleDictionaries?.[key];
  return dictionary ? dictionary[raw] : raw;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function encodeHash(value) {
  return encodeURIComponent(value);
}

function route() {
  const raw = window.location.hash.slice(1) || "/";
  const [pathPart, queryPart = ""] = raw.split("?");
  const parts = pathPart.replace(/^\/+/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(queryPart);
  if (parts[0] === "journal" && parts[1]) return { name: "journal", journalKey: parts[1], params };
  if (parts[0] === "issue" && parts[1] && parts[2]) return { name: "issue", journalKey: parts[1], period: parts[2], params };
  return { name: "home", params };
}

function tags(...values) {
  return values
    .filter((value) => value !== undefined && value !== null && String(value).trim())
    .map((value) => `<span class="tag">${escapeHtml(value)}</span>`)
    .join("");
}

function stat(label, value) {
  return `<span class="stat">${escapeHtml(label)}：${escapeHtml(value)}</span>`;
}

function groupBy(items, keyFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item) || "未分类";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }
  return map;
}

function renderHome() {
  const totalArticles = state.journals.reduce((sum, journal) => sum + Number(journal.article_count || 0), 0);
  const disciplines = groupBy(state.journals, (journal) => journal.discipline);
  const disciplineBlocks = [...disciplines.entries()]
    .sort(([a], [b]) => a.localeCompare(b, "zh-CN"))
    .map(([discipline, journals]) => {
      const typeGroups = groupBy(journals, (journal) => journal.journal_type);
      const typeBlocks = [...typeGroups.entries()]
        .sort(([a], [b]) => a.localeCompare(b, "zh-CN"))
        .map(([type, typeJournals]) => {
          const cards = typeJournals
            .sort((a, b) => a.display_name.localeCompare(b.display_name, "zh-CN"))
            .map(renderJournalCard)
            .join("");
          return `<h3 class="subgroup-title">${escapeHtml(type)}</h3><div class="card-grid">${cards}</div>`;
        })
        .join("");
      return `
        <section class="group-block">
          <div class="group-title">
            <h2>${escapeHtml(discipline)}</h2>
            <span>${journals.length} 种期刊</span>
          </div>
          ${typeBlocks}
        </section>
      `;
    })
    .join("");

  app.innerHTML = `
    <div class="page-title">
      <h1>首页</h1>
      <p>统一展示仓库中已经整理好的期刊文章信息。页面只读取本地 JSON 索引，不下载 PDF，不展示文章全文。</p>
      <div class="stats-row">
        ${stat("期刊", state.journals.length)}
        ${stat("期次", state.issues.length)}
        ${stat("文章", totalArticles)}
      </div>
    </div>
    ${disciplineBlocks || `<div class="empty">暂无期刊数据</div>`}
  `;
}

function renderJournalCard(journal) {
  return `
    <article class="journal-card">
      <h3><a href="#/journal/${encodeHash(journal.journal_key)}">${escapeHtml(journal.display_name)}</a></h3>
      <div class="toolbar">${tags(journal.discipline, journal.journal_type, journal.quartile)}</div>
      <div class="meta-row">
        ${stat("年份", journal.year_range || "暂无")}
        ${stat("文章", journal.article_count || 0)}
        ${stat("期次", journal.issue_count || 0)}
      </div>
    </article>
  `;
}

function renderJournalPage(journalKey) {
  const journal = state.journals.find((item) => item.journal_key === journalKey);
  if (!journal) {
    app.innerHTML = `<div class="empty">未找到该期刊</div>`;
    return;
  }

  const issues = state.issues.filter((item) => item.journal_key === journalKey);
  const byYear = groupBy(issues, (item) => item.year || "未知年份");
  const yearBlocks = [...byYear.entries()]
    .sort(([a], [b]) => Number(b) - Number(a))
    .map(([year, yearIssues], index) => {
      const issueCards = yearIssues
        .sort((a, b) => (b.month || b.issue || 0) - (a.month || a.issue || 0))
        .map((issue) => `
          <a class="issue-card" href="#/issue/${encodeHash(journalKey)}/${encodeHash(issue.period)}">
            <h3>${escapeHtml(issue.period_label)}</h3>
            <div class="meta-row">${stat("文章", issue.article_count)}</div>
          </a>
        `)
        .join("");
      return `
        <details class="year-group" ${index < 2 ? "open" : ""}>
          <summary>${escapeHtml(year)} <span>${yearIssues.length} 个期次</span></summary>
          <div class="issue-list">${issueCards}</div>
        </details>
      `;
    })
    .join("");

  app.innerHTML = `
    <div class="page-title">
      <div><a href="#/">首页</a> / 期刊</div>
      <h1>${escapeHtml(journal.display_name)}</h1>
      <div class="toolbar">${tags(journal.discipline, journal.journal_type, journal.quartile, journal.language)}</div>
    </div>
    <div class="summary-grid">
      <div class="summary-box"><b>文章总数</b><span>${escapeHtml(journal.article_count || 0)}</span></div>
      <div class="summary-box"><b>年份范围</b><span>${escapeHtml(journal.year_range || "暂无")}</span></div>
      <div class="summary-box"><b>期次总数</b><span>${escapeHtml(journal.issue_count || 0)}</span></div>
    </div>
    ${yearBlocks || `<div class="empty">暂无期次数据</div>`}
  `;
}

async function renderIssuePage(journalKey, period, params) {
  const journal = state.journals.find((item) => item.journal_key === journalKey);
  const issue = state.issues.find((item) => item.journal_key === journalKey && item.period === period);
  if (!journal || !issue) {
    app.innerHTML = `<div class="empty">未找到该期次</div>`;
    return;
  }

  app.innerHTML = `<div class="loading">正在读取文章索引...</div>`;
  await loadArticles();
  const rows = state.issueMap.get(`${journalKey}::${period}`) || [];
  const articleCards = rows.map(renderArticleCard).join("");
  app.innerHTML = `
    <div class="page-title">
      <div><a href="#/">首页</a> / <a href="#/journal/${encodeHash(journalKey)}">期刊</a> / 期次</div>
      <h1>${escapeHtml(journal.display_name)} ${escapeHtml(issue.period_label)}</h1>
      <div class="toolbar">${tags(journal.discipline, journal.journal_type, journal.quartile)}</div>
      <div class="stats-row">${stat("年份", issue.year || "未知")}${stat("文章", rows.length)}</div>
    </div>
    <div class="article-stack">${articleCards || `<div class="empty">暂无文章数据</div>`}</div>
  `;

  const articleId = params.get("article");
  if (articleId) {
    window.requestAnimationFrame(() => {
      document.getElementById(`article-${articleId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
}

function renderArticleCard(row) {
  const id = cell(row, "id");
  const title = cell(row, "title") || "无题";
  const authors = formatAuthors(cell(row, "authors"));
  const abstract = cell(row, "abstract") || "暂无摘要";
  const keywords = cell(row, "keywords") || [];
  const aiSummary = cell(row, "ai_summary") || "暂无 AI 总结";
  const basis = cell(row, "basis");
  const sourceUrl = cell(row, "source_url");
  const keywordHtml = keywords.length
    ? `<div class="keyword-list">${keywords.map((keyword) => `<span>${escapeHtml(keyword)}</span>`).join("")}</div>`
    : "";
  const sourceButton = sourceUrl
    ? `<a class="button" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">打开原文</a>`
    : "";
  return `
    <article class="article-card" id="article-${escapeHtml(id)}">
      <h2>${escapeHtml(title)}</h2>
      <div class="authors">作者：${escapeHtml(authors || "暂无作者")}</div>
      ${keywordHtml}
      <div class="article-sections">
        <section class="text-panel">
          <h3>摘要</h3>
          <p>${escapeHtml(abstract)}</p>
        </section>
        <section class="text-panel">
          <h3>AI 总结</h3>
          <p>${escapeHtml(aiSummary)}</p>
          ${basis ? `<p class="muted">${escapeHtml(basis)}</p>` : ""}
        </section>
      </div>
      ${sourceButton ? `<div class="button-row">${sourceButton}</div>` : ""}
    </article>
  `;
}

function formatAuthors(authors) {
  if (Array.isArray(authors)) return authors.filter(Boolean).join("；");
  return String(authors || "");
}

function snippet(row, term) {
  const fields = ["abstract", "ai_summary", "title", "keywords"];
  const lowerTerm = term.toLocaleLowerCase("zh-CN");
  for (const field of fields) {
    const raw = field === "keywords" ? formatAuthors(cell(row, field)) : String(cell(row, field) || "");
    const lower = raw.toLocaleLowerCase("zh-CN");
    const index = lower.indexOf(lowerTerm);
    if (index >= 0) {
      const start = Math.max(0, index - 46);
      const end = Math.min(raw.length, index + term.length + 86);
      return `${start > 0 ? "..." : ""}${raw.slice(start, end)}${end < raw.length ? "..." : ""}`;
    }
  }
  return cell(row, "abstract") || "暂无摘要";
}

let searchTimer = null;
searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(runSearch, 240);
});

async function runSearch() {
  const term = searchInput.value.trim();
  if (!term) {
    searchPanel.classList.add("hidden");
    searchPanel.innerHTML = "";
    return;
  }
  searchPanel.classList.remove("hidden");
  searchPanel.innerHTML = `<div class="loading">正在搜索本地索引...</div>`;
  await loadArticles();
  const lowerTerm = term.toLocaleLowerCase("zh-CN");
  const results = [];
  for (const row of state.articleRows) {
    const haystack = [
      cell(row, "journal"),
      cell(row, "title"),
      formatAuthors(cell(row, "authors")),
      formatAuthors(cell(row, "keywords")),
      cell(row, "abstract"),
      cell(row, "ai_summary"),
    ].join(" ").toLocaleLowerCase("zh-CN");
    if (haystack.includes(lowerTerm)) {
      results.push(row);
      if (results.length >= 80) break;
    }
  }
  renderSearchResults(term, results);
}

function renderSearchResults(term, rows) {
  if (!rows.length) {
    searchPanel.innerHTML = `<div class="empty">没有找到匹配文章</div>`;
    return;
  }
  searchPanel.innerHTML = `
    <div class="group-title">
      <h2>搜索结果</h2>
      <span>显示前 ${rows.length} 条</span>
    </div>
    <div class="result-stack">
      ${rows.map((row) => `
        <article class="result-card">
          <h3>
            <a href="#/issue/${encodeHash(cell(row, "journal_key"))}/${encodeHash(cell(row, "period"))}?article=${encodeHash(cell(row, "id"))}">
              ${escapeHtml(cell(row, "title") || "无题")}
            </a>
          </h3>
          <div class="authors">${escapeHtml(cell(row, "journal"))} / ${escapeHtml(cell(row, "period_label"))} / 作者：${escapeHtml(formatAuthors(cell(row, "authors")) || "暂无作者")}</div>
          <p>${escapeHtml(snippet(row, term))}</p>
        </article>
      `).join("")}
    </div>
  `;
}

async function render() {
  const current = route();
  searchPanel.classList.add("hidden");
  if (current.name === "journal") {
    renderJournalPage(current.journalKey);
    return;
  }
  if (current.name === "issue") {
    await renderIssuePage(current.journalKey, current.period, current.params);
    return;
  }
  renderHome();
}

async function init() {
  try {
    await loadBaseData();
    await render();
  } catch (error) {
    app.innerHTML = `<div class="empty">${escapeHtml(error.message || "页面加载失败")}</div>`;
  }
}

window.addEventListener("hashchange", render);
init();

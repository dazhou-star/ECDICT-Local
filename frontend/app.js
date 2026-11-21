

(() => {

  const $ = s => document.querySelector(s);



  const state = {

    mode: "prefix",

    page: "lookup",

    timer: null,

    selected: -1,

    favorites: [],
    favoriteDates: {},

    history: read("lexicon-history", []),

    audio: null

  };



  const q = $("#query");

  const suggestions = $("#suggestions");



  function read(k, fallback) {

    try {

      return JSON.parse(localStorage.getItem(k)) ?? fallback;

    } catch {

      return fallback;

    }

  }



  function save(k, v) {

    localStorage.setItem(k, JSON.stringify(v));

  }



  function esc(v) {

    return String(v ?? "").replace(/[&<>"']/g, c => ({

      "&": "&amp;",

      "<": "&lt;",

      ">": "&gt;",

      '"': "&quot;",

      "'": "&#39;"

    }[c]));

  }



  async function api(path, options = {}) {
    const r = await fetch(path, options);
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        detail = (await r.json()).detail || detail;
      } catch {}
      throw new Error(detail);
    }
    return r.json();
  }


  function status(s) {

    $("#status").textContent = s;

  }



  function addHistory(word) {

    state.history = [

      word,

      ...state.history.filter(x => x !== word)

    ].slice(0, 50);

    save("lexicon-history", state.history);

  }



  function applyFavorites(items) {
    state.favorites = items.map(item => item.word);
    state.favoriteDates = Object.fromEntries(
      items.map(item => [item.word.toLowerCase(), item.added_at])
    );
    $("#favCount").textContent = state.favorites.length;
  }

  async function loadFavorites() {
    const data = await api("/api/favorites");
    applyFavorites(Array.isArray(data.items) ? data.items : []);
    return data.items || [];
  }

  async function initializeFavorites() {
    // Migrate the previous browser-only collection. Do not remove its local
    // copy until the server import and a subsequent read both succeed.
    const legacy = read("lexicon-favorites", []);
    if (!Array.isArray(legacy)) {
      throw new Error("旧生词本数据格式异常，未清理本地数据");
    }

    if (legacy.length) {
      await api("/api/favorites/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ words: legacy })
      });
    }

    await loadFavorites();
    localStorage.removeItem("lexicon-favorites");
  }

  function isSaved(word) {
    return state.favorites.some(
      savedWord => savedWord.toLowerCase() === String(word).toLowerCase()
    );
  }

  async function toggleSave(word) {
    const button = $("#save");
    if (button) button.disabled = true;

    try {
      if (isSaved(word)) {
        await api(`/api/favorites/${encodeURIComponent(word)}`, {
          method: "DELETE"
        });
      } else {
        await api("/api/favorites", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ word })
        });
      }

      await loadFavorites();
      await showWord(word);
    } catch (e) {
      status(`生词本操作失败：${e.message}`);
      console.error(e);
      if (button && button.isConnected) button.disabled = false;
    }
  }

  function formatFavoriteDate(value) {
    if (!value) return "日期未知";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
    return date.toLocaleDateString(undefined, {
      year: "numeric", month: "2-digit", day: "2-digit"
    });
  }


  function hideSuggestions() {

    suggestions.hidden = true;

    suggestions.innerHTML = "";

    state.selected = -1;

  }



  async function doSuggest() {

    const term = q.value.trim();



    $("#clear").style.display = term ? "block" : "none";



    if (!term || state.page !== "lookup") {

      hideSuggestions();

      return;

    }



    try {

      const data = await api(

        `/api/search?q=${encodeURIComponent(term)}&limit=12&mode=${state.mode}`

      );



      suggestions.innerHTML = "";



      data.items.forEach(item => {

        const b = document.createElement("button");

        b.className = "suggestion";

        b.innerHTML = `

          <b>${esc(item.word)}</b>

          <small>${esc(item.translation || item.definition || "查看词条")}</small>

        `;



        b.addEventListener("mousedown", e => e.preventDefault());

        b.addEventListener("click", () => showWord(item.word));

        suggestions.appendChild(b);

      });



      suggestions.hidden = !data.items.length;

      state.selected = -1;

    } catch (e) {

      status("搜索服务不可用");

      console.error(e);

    }

  }



  function section(title, value, cls = "translation") {

    if (value === null || value === undefined || value === "") return "";



    return `

      <section class="section">

        <div class="section-title">${esc(title)}</div>

        <div class="${cls}">${esc(value)}</div>

      </section>

    `;

  }



  function fieldLabel(k) {

    return ({

      word: "单词",

      phonetic: "音标",

      phonetic_us: "美式音标",

      phonetic_uk: "英式音标",

      definition: "英文释义",

      translation: "中文释义",

      pos: "词性",

      collins: "柯林斯星级",

      oxford: "牛津核心词汇",

      tag: "词汇标签",

      bnc: "BNC 词频",

      frq: "词频",

      exchange: "词形变化",

      audio: "发音音频"

    })[k] || k;

  }



  // 兼容数据库中的真实换行与字面量 \n
  function normalizeNewlines(value) {
    let text = String(value ?? "");
    // Handle CRLF/LF that were stored as literal backslash sequences.
    // Repeat twice to also cover values escaped once more during transport.
    for (let i = 0; i < 2; i++) {
      text = text
        .replace(/\\\\r\\\\n/g, "\n")
        .replace(/\\\\n/g, "\n")
        .replace(/\\r\\n/g, "\n")
        .replace(/\\n/g, "\n");
    }
    return text.replace(/\r\n?/g, "\n");
  }

  // 中文释义按词性分组
  function parseTranslation(value) {
    const lines = normalizeNewlines(value)
      .split("\n")
      .map(line => line.trim())
      .filter(Boolean);

    const groups = [];
    const pattern = /^((?:n|v|vt|vi|a|s|r|adj|adv|pron|prep|conj|int|art|aux|abbr|num|det)\.|\[[^\]]+\])\s*(.*)$/i;

    for (const line of lines) {
      const match = line.match(pattern);
      if (match) {
        groups.push({ label: match[1], text: match[2] });
      } else if (groups.length) {
        groups[groups.length - 1].text +=
          (groups[groups.length - 1].text ? " " : "") + line;
      } else {
        groups.push({ label: "释义", text: line });
      }
    }
    return groups;
  }

    // ECDICT exchange 字段解析
  function parseExchange(value) {
    if (!value) return [];
    return String(value).split("/").map(part => {
      const index = part.indexOf(":");
      if (index < 0) return null;
      const code = part.slice(0, index).trim();
      const word = part.slice(index + 1).trim();
      return word ? { code, word } : null;
    }).filter(Boolean);
  }

  function getRegularVerbForms(word) {
    const w = String(word ?? "").trim().toLowerCase();
    if (!w) return null;

    const vowelGroups = w.match(/[aeiou]+/g) || [];
    const cvcEnding = /[^aeiou][aeiou][^aeiouwxy]$/.test(w);
    const doublesFinalConsonant = cvcEnding && vowelGroups.length === 1;
    const doublesFinalZ = /[aeiou]z$/.test(w) && !w.endsWith("zz");
    const finalLVariant =
      !w.endsWith("ll") && /[^aeiou][aeiou]l$/.test(w);

    let past;
    if (w.endsWith("e")) {
      past = [w + "d"];
    } else if (/[^aeiou]y$/.test(w)) {
      past = [w.slice(0, -1) + "ied"];
    } else if (doublesFinalZ) {
      past = [w + "zed"];
    } else if (w.endsWith("c")) {
      past = [w + "ked"];
    } else {
      past = [w + "ed"];
      if (doublesFinalConsonant) {
        past = [w + w.slice(-1) + "ed"];
      } else if (cvcEnding) {
        past.push(w + w.slice(-1) + "ed");
      }
    }

    let ing;
    if (w === "be") {
      ing = ["being"];
    } else if (w.endsWith("ie")) {
      ing = [w.slice(0, -2) + "ying"];
    } else if (w.endsWith("e") && !w.endsWith("ee")) {
      ing = [w.slice(0, -1) + "ing"];
    } else if (doublesFinalZ) {
      ing = [w + "zing"];
    } else if (w.endsWith("c")) {
      ing = [w + "king"];
    } else {
      ing = [w + "ing"];
      if (doublesFinalConsonant) {
        ing = [w + w.slice(-1) + "ing"];
      } else if (cvcEnding) {
        ing.push(w + w.slice(-1) + "ing");
      }
    }

    if (finalLVariant) {
      past.push(w + "led");
      ing.push(w + "ling");
    }

    let third;
    if (doublesFinalZ) {
      third = w + "zes";
    } else if (/(s|x|z|ch|sh|o)$/.test(w)) {
      third = w + "es";
    } else if (/[^aeiou]y$/.test(w)) {
      third = w.slice(0, -1) + "ies";
    } else {
      third = w + "s";
    }

    return { p: past, d: past, i: ing, "3": [third] };
  }

  function getIrregularForms(baseWord, forms) {
    const expected = getRegularVerbForms(baseWord);
    if (!expected) return [];

    return ["p", "d", "i", "3"].filter(code => {
      const actual = forms[code];
      return actual &&
        !expected[code].includes(String(actual).trim().toLowerCase());
    });
  }

  function getDoubledVerbForms(baseWord, forms) {
    const base = String(baseWord ?? "").trim().toLowerCase();
    const expected = getRegularVerbForms(base);
    if (!expected) return [];

    const last = base.slice(-1);
    const doubleForms = {
      p: base + last + "ed",
      d: base + last + "ed",
      i: base + last + "ing"
    };
    if (/[aeiou]z$/.test(base) && !base.endsWith("zz")) {
      doubleForms["3"] = base + last + "es";
    }

    return Object.keys(doubleForms).filter(code => {
      const actual = String(forms[code] ?? "").trim().toLowerCase();
      return actual === doubleForms[code] &&
        expected[code].includes(actual);
    });
  }

  function isVerbEntry(pos) {
    return String(pos ?? "").split(/[;,/]+/).some(value =>
      ["v", "vt", "vi"].includes(
        value.trim().toLowerCase().replace(/\.$/, "")
      )
    );
  }

  const POS_NAMES = {
    n: "名词", v: "动词", vt: "及物动词", vi: "不及物动词",
    a: "形容词", s: "形容词", r: "副词",
    adj: "形容词", adv: "副词", pron: "代词", prep: "介词",
    conj: "连词", int: "感叹词", art: "冠词", aux: "助动词",
    abbr: "缩写", num: "数词", det: "限定词"
  };

  function getPosKey(value) {
    const key = String(value ?? "")
      .trim()
      .toLowerCase()
      .replace(/\.$/, "");

    if (["v", "vt", "vi"].includes(key)) return "verb";
    if (["a", "s", "adj"].includes(key)) return "adj";
    if (["r", "adv"].includes(key)) return "adv";

    const allowed = [
      "n", "pron", "prep", "conj", "int",
      "art", "aux", "abbr", "num", "det"
    ];

    return allowed.includes(key) ? key : "other";
  }

  function posClass(value) {
    return `pos-${getPosKey(value)}`;
  }

  const TAG_NAMES = {
    cet4: "大学英语四级", cet6: "大学英语六级",
    ky: "考研英语", toefl: "托福", ielts: "雅思", gre: "GRE",
    zk: "中考英语", gk: "高考英语"
  };

  function parseTags(value) {
    const raw = String(value ?? "").toLowerCase().trim();
    if (!raw) return [];

    // ECDICT may store tags as "zk gk" or as a compact concatenated string.
    const tokens = raw.split(/[\s,;|]+/).filter(Boolean);
    const known = ["cet4", "cet6", "toefl", "ielts", "gre", "ky", "zk", "gk"];
    const found = [];

    for (const token of tokens) {
      let rest = token;
      for (const tag of known) {
        if (rest.includes(tag)) {
          if (!found.includes(tag)) found.push(tag);
          rest = rest.split(tag).join("");
        }
      }
    }
    return found;
  }

  function renderTranslation(value) {
    const groups = parseTranslation(value);
    if (!groups.length) return "";
    return `
      <section class="section">
        <div class="section-title">中文释义</div>
        <div class="translation-groups">
          ${groups.map(group => `
            <div class="translation-group">
              <span class="pos-label ${posClass(group.label)}">${esc(group.label)}</span>
              <span class="meaning-text">${esc(group.text)}</span>
            </div>
          `).join("")}
        </div>
      </section>
    `;
  }

  function renderDefinitions(value) {
    const lines = normalizeNewlines(value)
      .split("\n").map(x => x.trim()).filter(Boolean);
    if (!lines.length) return "";

    const items = lines.map(line => {
      const match = line.match(/^((?:vt|vi|adj|adv|pron|prep|conj|int|art|aux|abbr|num|det|n|v|a|s|r)\.)\s*(.*)$/i);
      return match
        ? { pos: match[1], text: match[2] }
        : { pos: "", text: line };
    });

    return `
      <section class="section">
        <div class="section-title">英文释义</div>
        <ol class="definition-list">
          ${items.map(item => `
            <li class="definition-item">
              ${item.pos ? `<span class="definition-pos ${posClass(item.pos)}">${esc(item.pos)}</span>` : ""}
              <span class="definition-text">${esc(item.text)}</span>
            </li>
          `).join("")}
        </ol>
      </section>
    `;
  }

  function renderVocabulary(entry) {
    const pos = String(entry.pos ?? "").split(/[;,/]+/)
      .map(x => x.trim()).filter(Boolean);
    const tags = parseTags(entry.tag);
    if (!pos.length && !tags.length) return "";

    return `
      <section class="section">
        <div class="section-title">词性与词汇标签</div>
        ${pos.length ? `
          <div class="vocab-line">
            <span class="vocab-heading">词性</span>
            <span class="vocab-values">${pos.map(x => {
              const key = x.toLowerCase().replace(/\.$/, "");
              return `<span class="vocab-pos ${posClass(key)}">${esc(POS_NAMES[key] || x)} <small>${esc(x)}</small></span>`;
            }).join("")}</span>
          </div>
        ` : ""}
        ${tags.length ? `
          <div class="vocab-line">
            <span class="vocab-heading">适用考试 / 词汇范围</span>
            <span class="vocab-values">${tags.map(tag => `
              <span class="vocab-tag vocab-tag-${esc(tag)}" title="${esc(TAG_NAMES[tag] || "词汇标签")}">
                <b>${esc(tag.toUpperCase())}</b>
                <span class="vocab-tag-name">${esc(TAG_NAMES[tag] || "词汇标签")}</span>
              </span>
            `).join("")}</span>
          </div>
        ` : ""}
      </section>
    `;
  }

  function renderWordStats(entry) {
    const stats = [];
    if (entry.collins !== undefined && entry.collins !== "") {
      const n = Number(entry.collins);
      const valid = Number.isFinite(n);
      const count = valid ? Math.max(0, Math.min(5, n)) : 0;
      const stars = valid ? "★".repeat(count) + "☆".repeat(5 - count) : esc(entry.collins);
      stats.push(`<div class="word-stat-card stat-collins">
        <div class="word-stat-head"><span class="word-stat-label">柯林斯星级</span><span class="word-stat-symbol">✦</span></div>
        <div class="collins-rating"><span class="collins-stars">${stars}</span>${valid ? `<b class="stat-score">${count}<small>/5</small></b>` : ""}</div>
        <div class="word-stat-note">词汇熟悉度参考</div></div>`);
    }
    if (entry.oxford !== undefined && entry.oxford !== "") {
      const yes = ["1", "true", "yes"].includes(String(entry.oxford).toLowerCase());
      stats.push(`<div class="word-stat-card ${yes ? "stat-oxford-yes" : "stat-oxford-no"}">
        <div class="word-stat-head"><span class="word-stat-label">牛津核心词汇</span><span class="word-stat-symbol">◎</span></div>
        <div class="word-stat-value"><b>${yes ? "核心词汇" : "非核心词汇"}</b><span class="stat-status">${yes ? "收录" : "未标记"}</span></div>
        <div class="word-stat-note">Oxford 核心词汇标记</div></div>`);
    }
    const formatRank = value => {
      const n = Number(value);
      return Number.isFinite(n) ? n.toLocaleString("en-US") : esc(value);
    };
    if (entry.bnc !== undefined && entry.bnc !== "") {
      stats.push(`<div class="word-stat-card stat-bnc">
        <div class="word-stat-head"><span class="word-stat-label">BNC 词频排名</span><span class="word-stat-symbol">▤</span></div>
        <div class="word-stat-value"><b class="rank-number">${formatRank(entry.bnc)}</b><span class="rank-unit">名</span></div>
        <div class="word-stat-note">英国国家语料库</div></div>`);
    }
    if (entry.frq !== undefined && entry.frq !== "") {
      stats.push(`<div class="word-stat-card stat-frequency">
        <div class="word-stat-head"><span class="word-stat-label">词频排名</span><span class="word-stat-symbol">↗</span></div>
        <div class="word-stat-value"><b class="rank-number">${formatRank(entry.frq)}</b><span class="rank-unit">名</span></div>
        <div class="word-stat-note">ECDICT 综合词频</div></div>`);
    }
    if (!stats.length) return "";
    return `<section class="section"><div class="section-title">词汇等级与频率</div><div class="word-stats-panel">${stats.join("")}</div></section>`;
  }

  function renderExchange(value, baseWord, pos) {
    const forms = parseExchange(value);
    const byCode = Object.fromEntries(forms.map(x => [x.code, x.word]));
    const verbCodes = ["p", "d", "i"];
    const hasVerbForms = verbCodes.some(code => byCode[code]);
    const availableVerbCodes = ["p", "d", "i", "3"]
      .filter(code => byCode[code]);
    const showVerbNotice = isVerbEntry(pos) || availableVerbCodes.length > 0;
    const irregularCodes = getIrregularForms(baseWord, byCode);
    const doubledCodes = getDoubledVerbForms(baseWord, byCode);
    const coreCodes = ["p", "d"].filter(code => byCode[code]);
    const irregularCoreCodes = coreCodes.filter(code =>
      irregularCodes.includes(code)
    );
    const otherCodes = ["i", "3"].filter(code => byCode[code]);
    const irregularOtherCodes = otherCodes.filter(code =>
      irregularCodes.includes(code)
    );

    let changeStatus = "regular";
    if (!byCode.p || !byCode.d || !String(baseWord ?? "").trim()) {
      changeStatus = "missing";
    } else if (irregularCoreCodes.length) {
      changeStatus = coreCodes.length === 2 && irregularCoreCodes.length === 1
        ? "mixed"
        : "irregular";
    } else if (irregularOtherCodes.length) {
      changeStatus = "mixed";
    }

    const statusLabels = {
      regular: "规则变化",
      irregular: "不规则变化",
      mixed: "混合变化",
      missing: "缺少数据"
    };
    const statusDetails = {
      regular: "已提供的形式符合常见规则",
      irregular: "已提供的形式包含特殊变化",
      mixed: "部分已提供形式符合规则，部分为特殊变化",
      missing: "数据库未提供足够的动词词形，无法判断"
    };
    const labels = {
      s: "复数", "3": "第三人称单数",
      r: "比较级", t: "最高级", "0": "原形", "1": "其他词形"
    };

    const rows = [];
    if (showVerbNotice) {
      rows.push(`
        <div class="irregular-notice notice-${changeStatus}">
          <strong>${statusLabels[changeStatus]}</strong>
          <span>${statusDetails[changeStatus]}</span>
        </div>
      `);
    }

    if (hasVerbForms) {
      const columns = [
        ["", "原形", baseWord || "—"],
        ["p", "过去式", byCode.p || "—"],
        ["d", "过去分词", byCode.d || "—"],
        ["i", "现在分词", byCode.i || "—"]
      ];
      rows.push(`
        <div class="verb-forms">
          ${columns.map(([code, label, word], i) => {
            const irregular = irregularCodes.includes(code);
            const doubled = !irregular && doubledCodes.includes(code);
            return `
              ${i ? `<span class="verb-separator">→</span>` : ""}
              <div class="verb-form${irregular ? " irregular-form" : doubled ? " doubled-form" : ""}">
                <span>${label}</span>
                <b>${esc(word)}</b>
                ${irregular ? "<small>特殊变化</small>" : doubled ? "<small>双写</small>" : ""}
              </div>
            `;
          }).join("")}
        </div>
      `);
    }

    const grouped = new Map();
    for (const item of forms) {
      if (verbCodes.includes(item.code)) continue;
      const label = labels[item.code] || `其他变化 (${item.code})`;
      if (!grouped.has(label)) grouped.set(label, []);
      if (!grouped.get(label).some(x => x.word === item.word)) {
        grouped.get(label).push(item);
      }
    }

    for (const [label, items] of grouped) {
      rows.push(`
        <div class="inflection-row">
          <span class="inflection-label">${esc(label)}</span>
          <span class="inflection-words">${items.map(item => {
            const irregular = item.code === "3" &&
              irregularCodes.includes("3");
            const doubled = !irregular && item.code === "3" &&
              doubledCodes.includes("3");
            return `
              <span class="inflection-word${irregular ? " irregular-form" : doubled ? " doubled-form" : ""}">
                ${esc(item.word)}${irregular ? "<small>特殊变化</small>" : doubled ? "<small>双写</small>" : ""}
              </span>
            `;
          }).join("、")}</span>
        </div>
      `);
    }

    if (!rows.length) return "";
    return `
      <section class="section">
        <div class="section-title">词形变化</div>
        <div class="exchange-content">${rows.join("")}</div>
      </section>
    `;
  }

    // 判断音频地址是否有效

  function getAudioUrl(value) {

    if (!value || typeof value !== "string") return null;



    const source = value.trim();



    try {

      const url = new URL(source, window.location.href);



      if (url.protocol !== "http:" && url.protocol !== "https:") {

        return null;

      }



      return url.href;

    } catch {

      return null;

    }

  }



  // 使用浏览器英文语音合成



function speakWord(word) {

  if (!("speechSynthesis" in window)) {

    status("当前浏览器不支持语音合成");

    return;

  }



  const synth = window.speechSynthesis;

  synth.cancel();



  // 不强制指定 voice，让 Edge 自行选择系统语音

  const utterance = new SpeechSynthesisUtterance(word);

  utterance.lang = "en-US";

  utterance.rate = 0.85;

  utterance.volume = 1;



  let finished = false;



  utterance.onstart = () => {

    status(`正在朗读：${word}`);

  };



  utterance.onend = () => {

    finished = true;

    status("发音播放完成");

  };



  utterance.onerror = event => {

    if (finished) return;

    console.error("语音合成失败：", event);

    status(`语音合成失败：${event.error}`);

  };



  // 从用户点击事件直接启动播放

  synth.speak(utterance);

}





  // 优先播放词典音频，失败后回退到语音合成

  async function playPronunciation(entry) {

    const word = entry.word || "";

    const button = $("#speak");



    if (!word || !button) return;



    button.disabled = true;

    button.textContent = "⏳ 正在播放";



    if (state.audio) {

      state.audio.pause();

      state.audio = null;

    }



    if ("speechSynthesis" in window) {

      speechSynthesis.cancel();

    }



    const audioUrl = getAudioUrl(entry.audio);



    if (audioUrl) {

      try {

        const audio = new Audio(audioUrl);

        state.audio = audio;



        audio.addEventListener("ended", () => {

          button.disabled = false;

          button.textContent = "🔊 发音";

          status("发音播放完成");

        }, { once: true });



        audio.addEventListener("error", () => {

          state.audio = null;

        }, { once: true });



        status("正在播放词典音频…");



        await audio.play();



        button.disabled = false;

        button.textContent = "🔊 发音";

        return;

      } catch (e) {

        console.warn("词典音频播放失败，尝试语音合成：", e);

        state.audio = null;

      }

    }



    button.disabled = false;

    button.textContent = "🔊 发音";



    speakWord(word);

  }



  function renderWord(entry) {
    const word = entry.word || "";
    const raw = Object.entries(entry).filter(
      ([, v]) => v !== null && v !== undefined && v !== ""
    );
    const phonetic = entry.phonetic || entry.phonetic_us || entry.phonetic_uk || "";

    $("#welcome").hidden = true;
    $("#empty").hidden = true;

    $("#results").innerHTML = `
      <article class="entry">
        <div class="entry-head">
          <div>
            <h2 class="word">${esc(word)}</h2>
            <div class="sub">
              ${phonetic
                ? `<span>/${esc(String(phonetic).replace(/^/|/$/g, ""))}/</span>`
                : ""}
              <button class="sound" id="speak">🔊 发音</button>
            </div>
          </div>
          <button class="save ${isSaved(word) ? "saved" : ""}" id="save">
            ${isSaved(word) ? "★ 已收藏" : "☆ 加入生词本"}
          </button>
        </div>

        ${renderTranslation(entry.translation)}
        ${renderDefinitions(entry.definition)}
        ${renderVocabulary(entry)}
        ${renderWordStats(entry)}
        ${renderExchange(entry.exchange, word, entry.pos)}

        <details class="raw">
          <summary>查看完整原始词条（${raw.length} 个字段）</summary>
          <div class="raw-grid">
            ${raw.map(([k, v]) => `
              <div class="raw-field">
                <b>${esc(fieldLabel(k))}</b>
                <span>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>
              </div>
            `).join("")}
          </div>
        </details>
      </article>
    `;

    $("#save").addEventListener("click", () => toggleSave(word));
    $("#speak").addEventListener("click", () => playPronunciation(entry));
    addHistory(word);
  }

    async function showWord(word) {

    hideSuggestions();



    q.value = word;

    $("#clear").style.display = "block";

    status("正在查询词条…");



    try {

      const entry = await api(

        `/api/word/${encodeURIComponent(word)}`

      );



      renderWord(entry);

      status("本地查询完成");



      state.page = "lookup";

      setSide("lookup");

    } catch (e) {

      $("#results").innerHTML = "";

      $("#welcome").hidden = true;

      $("#empty").hidden = false;



      $("#empty h2").textContent = "没有找到词条";

      $("#empty p").textContent = e.message;



      status("查询未找到");

    }

  }



  function setSide(page) {

    document.querySelectorAll(".side-btn[data-page]")

      .forEach(b => {

        b.classList.toggle("active", b.dataset.page === page);

      });

  }



  async function showList(page) {
    state.page = page;
    setSide(page);
    hideSuggestions();

    $("#welcome").hidden = true;
    $("#empty").hidden = true;

    if (page === "favorites") {
      try {
        await loadFavorites();
      } catch (e) {
        $("#results").innerHTML = `
          <h2 style="font-size:18px;margin:4px 0 16px">我的生词本</h2>
          <div class="welcome">
            <h2>无法加载生词本</h2>
            <p>${esc(e.message)}。请确认词典服务器正常运行后重试。</p>
          </div>
        `;
        status("生词本加载失败");
        return;
      }
    }

    const list = page === "favorites"
      ? state.favorites.map(word => ({
          word,
          added_at: state.favoriteDates[word.toLowerCase()] || ""
        }))
      : state.history.map(word => ({ word, added_at: "" }));

    $("#results").innerHTML = `
      <h2 style="font-size:18px;margin:4px 0 16px">
        ${page === "favorites" ? "我的生词本" : "最近查询"}
      </h2>
    `;

    if (!list.length) {
      $("#results").innerHTML += `
        <div class="welcome">
          <h2>这里暂时是空的</h2>
          <p>查询单词后，可以在这里查看记录。</p>
        </div>
      `;
      return;
    }

    list.forEach(item => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `
        <div class="list-row-info">
          <b>${esc(item.word)}</b>
          ${page === "favorites"
            ? `<small class="list-date">添加于 ${esc(formatFavoriteDate(item.added_at))}</small>`
            : ""}
        </div>
        <button type="button">查看 →</button>
      `;

      row.querySelector("button")
        .addEventListener("click", () => showWord(item.word));

      $("#results").appendChild(row);
    });
  }

  q.addEventListener("input", () => {

    clearTimeout(state.timer);

    state.timer = setTimeout(doSuggest, 120);

  });



  q.addEventListener("keydown", e => {

    const opts = [...suggestions.querySelectorAll(".suggestion")];



    if (e.key === "ArrowDown" && opts.length) {

      e.preventDefault();

      state.selected = Math.min(

        state.selected + 1,

        opts.length - 1

      );

    } else if (e.key === "ArrowUp" && opts.length) {

      e.preventDefault();

      state.selected = Math.max(0, state.selected - 1);

    } else if (e.key === "Escape") {

      hideSuggestions();

    } else if (e.key === "Enter") {

      e.preventDefault();



      const selected = opts[state.selected];



      showWord(

        selected

          ? selected.querySelector("b").textContent

          : q.value.trim()

      );

    }



    opts.forEach((o, i) => {

      o.classList.toggle("selected", i === state.selected);

    });

  });



  $("#clear").addEventListener("click", () => {

    q.value = "";

    $("#clear").style.display = "none";



    hideSuggestions();



    $("#results").innerHTML = "";

    $("#empty").hidden = true;

    $("#welcome").hidden = false;



    q.focus();

  });



  document.querySelectorAll(".filter").forEach(b => {

    b.addEventListener("click", () => {

      state.mode = b.dataset.mode;



      document.querySelectorAll(".filter").forEach(x => {

        x.classList.toggle("active", x === b);

      });



      doSuggest();

    });

  });



  document.querySelectorAll(".side-btn[data-page]").forEach(b => {

    b.addEventListener("click", () => {

      if (b.dataset.page === "lookup") {

        state.page = "lookup";

        setSide("lookup");



        $("#results").innerHTML = "";

        $("#welcome").hidden = false;

      } else {

        showList(b.dataset.page);

      }

    });

  });



  document.querySelectorAll(".examples button").forEach(b => {

    b.addEventListener("click", () => showWord(b.dataset.word));

  });



  $("#random").addEventListener("click", async () => {

    try {

      const d = await api("/api/random");

      showWord(d.word);

    } catch (e) {

      status(e.message);

    }

  });



  $("#theme").addEventListener("click", () => {

    document.body.classList.toggle("dark");



    localStorage.setItem(

      "lexicon-dark",

      document.body.classList.contains("dark") ? "1" : "0"

    );

  });



  if (localStorage.getItem("lexicon-dark") === "1") {

    document.body.classList.add("dark");

  }



  initializeFavorites().catch(e => {
    status(`生词本同步失败：${e.message}`);
    console.error("生词本同步失败：", e);
  });

  api("/api/stats")

    .then(d => {

      $("#total").textContent =

        Number(d.total_entries).toLocaleString();



      $("#fields").textContent = d.fields.length;



      status(

        `${Number(d.total_entries).toLocaleString()} 个词条已就绪`

      );

    })

    .catch(() => status("词库未导入或后端未启动"));



})();
